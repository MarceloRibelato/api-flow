import copy
import concurrent.futures
import asyncio
import json
import logging
import os
import re
import time
import threading
import uuid
from datetime import datetime

import requests
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.feature_models import FeatureModel
from app.models.flow_models import FlowDB
from app.schemas.history_schemas import ExecutionHistoryCreate
from app.schemas.variable_schemas import VariableCreate
from app.services.flow_service import FlowService
from app.services.history_service import HistoryService
from app.services.variable_service import VariableService
from app.services.variable_resolver import replace_vars as _replace_vars
from app.services.assertion_engine import evaluate_all_assertions
from app.services.extraction_engine import process_extractions
from app.services.url_utils import (
    sanitize_url_for_docker as _sanitize_url,
    is_blocked_domain as _is_blocked_domain,
    ensure_absolute_url,
    ensure_protocol,
)

logger = logging.getLogger(__name__)

class FlowExecutorService:
    # ── Delegating wrappers for backward compatibility ──
    @staticmethod
    def replace_vars(text, variables):
        return _replace_vars(text, variables)

    @staticmethod
    def sanitize_url_for_docker(url: str) -> str:
        return _sanitize_url(url)

    @staticmethod
    def is_blocked_domain(url: str) -> bool:
        return _is_blocked_domain(url)

    @staticmethod
    def execute_flow_logic(db: Session, flow_meta: dict, product_id: int, env_id: int, company_id: int, variables_dict: dict, feature_name: str = "Unknown Feature", schedule_id: int = None, user_id: int = 1, capture_video: bool = False, capture_screenshot: bool = False, flow_type: str = 'api', visible_execution: bool = False, dataset_row: dict = None, failed_item_ids: list = None):
        # (imports now at top of file)

        # Resolve fallback env if none provided
        if not env_id and product_id:
            from app.services.environment_service import EnvironmentService
            envs = EnvironmentService.get_by_project(db, product_id)
            if envs:
                fallback_env = envs[0]
                logger.info(f"      ℹ️ Resolved null env_id in execute_flow_logic to fallback environment ID: {env_id}")
        
        global_timeout_ms = None
        global_retry_actions = None
        global_retry_test = 0
        eff_ft = flow_type or 'api'
        schedule = None
        if schedule_id:
            from app.models.schedule_models import ScheduleModel
            schedule = db.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
            if schedule:
                if schedule.duration_seconds:
                    global_timeout_ms = schedule.duration_seconds
                if schedule.virtual_users is not None:
                    global_retry_actions = schedule.virtual_users
                if schedule.ramp_up_seconds is not None:
                    global_retry_test = schedule.ramp_up_seconds
                eff_ft = getattr(schedule, 'flow_type', None) or eff_ft

        from app.models.api_test_history_models import ApiExecutionHistory, WebExecutionHistory, MobileExecutionHistory
        if eff_ft == 'mobile':
            HistoryModel = MobileExecutionHistory
        elif eff_ft in ['e2e', 'web']:
            HistoryModel = WebExecutionHistory
        else:
            HistoryModel = ApiExecutionHistory

        current_session_retry_count = 0
        if schedule_id:
            from sqlalchemy import func
            max_retry = db.query(func.max(HistoryModel.retry_count)).filter(HistoryModel.schedule_id == schedule_id).scalar()
            hist_count = db.query(func.count(HistoryModel.id)).filter(HistoryModel.schedule_id == schedule_id).scalar()
            if hist_count and hist_count > 0:
                current_session_retry_count = (max_retry or 0) + 1
        if current_session_retry_count == 0 and failed_item_ids:
            clean_ids = [int(x) for x in failed_item_ids if str(x).isdigit()]
            if clean_ids:
                from sqlalchemy import func
                for HM in (MobileExecutionHistory, WebExecutionHistory, ApiExecutionHistory):
                    max_r = db.query(func.max(HM.retry_count)).filter(HM.id.in_(clean_ids)).scalar()
                    if max_r is not None:
                        current_session_retry_count = (max_r or 0) + 1
                        break

        existing_batch_id = None
        if schedule_id:
            first_hist = db.query(HistoryModel.batch_id).filter(HistoryModel.schedule_id == schedule_id).order_by(HistoryModel.id.desc()).first()
            if first_hist and first_hist[0]:
                existing_batch_id = first_hist[0]
        elif failed_item_ids:
            clean_ids = []
            for item in failed_item_ids:
                try:
                    clean_ids.append(int(item))
                except (ValueError, TypeError):
                    pass
            if clean_ids:
                first_hist = None
                for HM in (MobileExecutionHistory, WebExecutionHistory, ApiExecutionHistory):
                    first_hist = db.query(HM.batch_id).filter(HM.id.in_(clean_ids)).first()
                    if first_hist and first_hist[0]:
                        existing_batch_id = first_hist[0]
                        break

        if existing_batch_id:
            base_prefix = re.sub(r'(_path|_retry).*$', '', existing_batch_id)
            base_batch_id = base_prefix
        else:
            base_batch_id = f"sched_{uuid.uuid4().hex}"

        history_buffer = []
        flow_success_count = 0
        flow_fail_count = 0
        e2e_executor = None

        try:
            # 1. Load full flow data with cards (Robust field access)
            p_id = flow_meta.get('project_id') or flow_meta.get('projectId')
            f_id = flow_meta.get('id')
            
            # If flow_meta already contains the nodes/edges, we can skip redundant loading
            # but usually flow_meta only contains summary info from list_by_project.
            if 'nodes' in flow_meta and flow_meta['nodes']:
                flow_data = flow_meta
            else:
                flow_data = FlowService.load(db, p_id, company_id, f_id)
            
            cards = flow_data.get('cardData', {})
            nodes = flow_data.get('nodes', [])
            edges = flow_data.get('edges', [])

            # Resolve effective flow type robustly
            effective_flow_type = flow_type if flow_type in ['mobile', 'e2e', 'web'] else (flow_data.get('flow_type') or flow_meta.get('flow_type') or 'api')
            
            logger.info(f"📊 [execute_flow_logic] Loaded Flow ID: {f_id} ({effective_flow_type}) - {len(nodes)} nodes, {len(edges)} edges")
            if not nodes:
                logger.warning(f"⚠️ No nodes found in flow data (Flow ID: {f_id})!")
            
            # Build Graph (Adjacency List)
            adj = {n['id']: [] for n in nodes}
            in_degree = {n['id']: 0 for n in nodes}
            
            # Filter valid edges (where both source and target exist)
            node_ids = set(n['id'] for n in nodes)
            valid_edges = [e for e in edges if e['source'] in node_ids and e['target'] in node_ids]
            
            logger.info(f"🔗 Validated {len(valid_edges)}/{len(edges)} edges.")

            for edge in valid_edges:
                src, tgt = edge['source'], edge['target']
                if tgt not in adj[src]:
                    adj[src].append(tgt)
                in_degree[tgt] += 1
                logger.debug(f"   Edge: {src} -> {tgt}")

            # FIND ALL PATHS (Scenarios) to run from START to END independently
            roots = [n['id'] for n in nodes if in_degree[n['id']] == 0]
            
            def get_x_pos(nid):
                n = next((x for x in nodes if x['id'] == nid), None)
                return n['position']['x'] if n else 0
            roots.sort(key=get_x_pos)

            all_paths = []
            def dfs_paths(current_id, current_path):
                neighbors = adj.get(current_id, [])
                if not neighbors:
                    all_paths.append(current_path)
                else:
                    for nxt in neighbors:
                        if nxt not in current_path: # Detect cycle
                            dfs_paths(nxt, current_path + [nxt])
                        else:
                            all_paths.append(current_path)

            for r in roots:
                dfs_paths(r, [r])
                
            logger.info(f"🛣️ Found {len(all_paths)} possible execution paths (scenarios).")

            flow_failed = False
            flow_success_count = 0
            flow_fail_count = 0 

            base_variables_dict = copy.deepcopy(variables_dict)
            if dataset_row and isinstance(dataset_row, dict):
                logger.info("Injecting dataset_row variables into execution context")
                base_variables_dict.update(dataset_row)

            final_variables = copy.deepcopy(base_variables_dict)

            # Deduplicate paths (prevent redundant execution if graph has multiple identical paths)
            all_paths = list(dict.fromkeys(tuple(p) for p in all_paths))
            all_paths = [list(p) for p in all_paths]
            path_entries = [(idx + 1, path) for idx, path in enumerate(all_paths)]
            
            # Retry Filtering: Filter scenario paths containing failed nodes/batches if this is a retry execution
            failed_node_ids = set()
            failed_path_numbers = set()
            if failed_item_ids:
                clean_failed_item_ids = []
                for item in failed_item_ids:
                    item_str = str(item).strip()
                    try:
                        clean_failed_item_ids.append(int(item))
                    except (ValueError, TypeError):
                        if item_str:
                            failed_node_ids.add(item_str)

                if clean_failed_item_ids:
                    failed_recs = db.query(HistoryModel).filter(HistoryModel.id.in_(clean_failed_item_ids)).all()
                    if not failed_recs:
                        for HM in (MobileExecutionHistory, WebExecutionHistory, ApiExecutionHistory):
                            if HM != HistoryModel:
                                failed_recs = db.query(HM).filter(HM.id.in_(clean_failed_item_ids)).all()
                                if failed_recs:
                                    break
                    
                    target_recs = []
                    f_id = flow_data.get('id') or flow_data.get('flow_id') or flow_meta.get('id') or flow_meta.get('flow_id')
                    p_id = flow_data.get('project_id') or flow_meta.get('project_id') or flow_meta.get('projectId')
                    for fr in failed_recs:
                        if fr.flow_id and f_id and str(fr.flow_id) == str(f_id):
                            target_recs.append(fr)
                        elif fr.project_id and p_id and str(fr.project_id) == str(p_id):
                            target_recs.append(fr)
                        elif not fr.flow_id and not fr.project_id:
                            target_recs.append(fr)

                    # If failed_recs exist in DB but none belong to this flow, skip this flow during retry
                    if failed_recs and not target_recs:
                        logger.info(f"⏭️ Skipping Flow {f_id} (Project {p_id}) during retry: none of the failed items belong to this flow.")
                        return 0, 0

                    for fr in target_recs:
                        if fr.node_id:
                            failed_node_ids.add(str(fr.node_id))
                        if fr.node_name:
                            failed_node_ids.add(str(fr.node_name))
                        if fr.batch_id:
                            match = re.search(r'_path_(\d+)', str(fr.batch_id))
                            if match:
                                failed_path_numbers.add(int(match.group(1)))
                        if fr.api_id and cards:
                            for n_id, card_data in cards.items():
                                e2e_steps = card_data.get('e2eSteps', []) or []
                                api_calls = card_data.get('apiCalls', []) or []
                                all_step_ids = [str(s.get('id')) for s in (e2e_steps + api_calls) if isinstance(s, dict) and s.get('id')]
                                if str(fr.api_id) in all_step_ids:
                                    failed_node_ids.add(str(n_id))

            selected_path_entries = path_entries
            if failed_path_numbers:
                # Ground-truth path filtering: isolate only the scenario paths where failures actually occurred
                filtered_entries = [pe for pe in path_entries if pe[0] in failed_path_numbers]
                if filtered_entries:
                    selected_path_entries = filtered_entries
                    logger.info(f"🎯 [RETRY FILTER] Isolating scenario paths by failed path numbers: {len(selected_path_entries)} of {len(path_entries)} path(s) selected (paths: {[pe[0] for pe in selected_path_entries]}).")
                else:
                    logger.info(f"⚠️ [RETRY FILTER] Path numbers {failed_path_numbers} not found in path entries; executing all paths.")
            elif failed_node_ids:
                # Fallback path filtering by node IDs when path numbers are unavailable (e.g. canvas retry)
                filtered_entries = [
                    pe for pe in path_entries 
                    if any(
                        str(nid) in failed_node_ids 
                        or (cards and cards.get(str(nid), {}).get('name') in failed_node_ids) 
                        for nid in pe[1]
                    )
                ]
                if filtered_entries:
                    selected_path_entries = filtered_entries
                    logger.info(f"🎯 [RETRY FILTER] Isolating scenario paths by failed node IDs: {len(selected_path_entries)} of {len(path_entries)} path(s) selected (paths: {[pe[0] for pe in selected_path_entries]}).")
                else:
                    logger.info(f"⚠️ [RETRY FILTER] No nodes in Flow matched failed node IDs {failed_node_ids}; skipping execution.")
                    return 0, 0

            logger.info(f"   Found {len(selected_path_entries)} unique path(s) to execute out of {len(path_entries)} total.")
            async def run_paths():
                nonlocal flow_success_count, flow_fail_count, e2e_executor, final_variables, history_buffer
                path_cache = {}
                for path_number, path_nodes in selected_path_entries:
                    logger.info(f"🛣️ === Executing Scenario Path {path_number} === 🛣️")
                    
                    # Each path is a separate logical execution batch, preserving original path_number
                    batch_id = f"{base_batch_id}_path_{path_number}"
                    base_video_dir = "/app/data/videos" if os.path.exists("/app") else os.path.abspath("data/videos")
                    video_dir = os.path.join(base_video_dir, batch_id)
                    os.makedirs(video_dir, exist_ok=True)
                    
                    # Reset states fully for each scenario path
                    current_path_vars = copy.deepcopy(base_variables_dict)
                    flow_session = requests.Session()
                    flow_session.trust_env = False
                    try:
                        from requests.adapters import HTTPAdapter
                        http_adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50)
                        flow_session.mount('http://', http_adapter)
                        flow_session.mount('https://', http_adapter)
                    except Exception as pool_err:
                        logger.warning(f"Could not configure HTTPAdapter pool on flow_session: {pool_err}")
                    
                    # For mobile execution, keep Appium driver session alive across scenario paths
                    # and cleanly restart the app to initial state. For web/API, reset executor.
                    if effective_flow_type == 'mobile':
                        if e2e_executor:
                            logger.info(f"📱 [Mobile] Reusing Appium session for Scenario Path {path_number}. Restarting app...")
                            await e2e_executor.restart()
                            if capture_video:
                                await e2e_executor.start_screen_recording(video_dir)
                    else:
                        e2e_executor = None

                    path_success = True

                    longest_prefix_len = 0
                    cached_state = None
                    # Never skip prefix nodes during retry runs or in mobile/e2e/web executions.
                    # Each scenario path must execute completely from the beginning (node 0) to the end.
                    is_retry_run = bool(failed_item_ids) or current_session_retry_count > 0
                    if not is_retry_run and effective_flow_type not in ['mobile', 'e2e', 'web']:
                        for i in range(len(path_nodes), 0, -1):
                            prefix = tuple(path_nodes[:i])
                            if prefix in path_cache:
                                longest_prefix_len = i
                                cached_state = path_cache[prefix]
                                break
                    
                    if cached_state:
                        logger.info(f"⏭️ [CACHE HIT] Puling {longest_prefix_len} nodes! Injecting saved state from prefix {prefix}")
                        current_path_vars = copy.deepcopy(cached_state["vars"])
                        is_e2e_flow = effective_flow_type in ['e2e', 'mobile', 'web']
                        if is_e2e_flow:
                            if effective_flow_type == 'mobile':
                                from app.services.appium_executor_service import AppiumExecutorService
                                e2e_executor = AppiumExecutorService()
                                await e2e_executor.start(video_dir=video_dir if capture_video else None, db=db, product_id=product_id)
                            else:
                                from app.services.playwright_executor_service import PlaywrightExecutorService
                                e2e_executor = PlaywrightExecutorService(headless=not visible_execution)
                                e2e_executor.schedule_id = schedule_id
                                e2e_executor.project_id = product_id
                                await e2e_executor.start(video_dir=video_dir if capture_video else None, storage_state=cached_state.get("storage_state"))
                                if cached_state.get("url"):
                                    try:
                                        await e2e_executor._page.goto(cached_state["url"])
                                        await e2e_executor._wait_for_loading_to_finish()
                                    except Exception as e:
                                        logger.warning(f"Failed to navigate to cached URL: {e}")

                    for node_idx, current_id in enumerate(path_nodes[longest_prefix_len:], start=longest_prefix_len):
                        if not path_success:
                            logger.warning(f"  🛑 Skipping node {current_id} because path previously failed.")
                            break

                        is_target_failed = False
                        if failed_node_ids:
                            card_obj = cards.get(str(current_id)) if cards else None
                            c_name = card_obj.get('name') if card_obj else None
                            is_target_failed = (
                                str(current_id) in failed_node_ids
                                or (c_name and str(c_name) in failed_node_ids)
                            )
                            if not is_target_failed and card_obj:
                                e2e_s = card_obj.get('e2eSteps', []) or []
                                api_c = card_obj.get('apiCalls', []) or []
                                step_ids = [str(s.get('id')) for s in (e2e_s + api_c) if isinstance(s, dict) and s.get('id')]
                                if any(sid in failed_node_ids for sid in step_ids):
                                    is_target_failed = True

                        logger.info(f"➡️  [Step] Processing Node: {current_id} (Target Failed: {is_target_failed})")
                        
                            # Execute Card for this Node
                        card = cards.get(current_id)
                        if card:
                            api_calls = card.get('apiCalls', [])
                            e2e_steps = card.get('e2eSteps', [])
                            
                            # Combine steps into a unified execution list
                            all_steps = []
                            is_e2e_flow = effective_flow_type in ['e2e', 'mobile', 'web']
                            actual_exec_type = "mobile" if effective_flow_type == 'mobile' else ("web" if effective_flow_type in ['e2e', 'web'] else "api")
                            
                            # Do NOT execute api_calls if this is an E2E node,
                            # because they are mapped APIs from the browser extension.
                            # Manual APIs inside E2E nodes are now added to e2e_steps.
                            node_metadata = next((n for n in nodes if n['id'] == current_id), {})
                            is_e2e_node = node_metadata.get('type') == 'e2e' or len(e2e_steps) > 0
                            
                            if not is_e2e_node:
                                for a in api_calls: all_steps.append({'data': a, 'type': 'api'})

                            for e in e2e_steps:
                                if e.get('type') == 'api_request':
                                    props = e.get('properties', {})
                                    headers_val = props.get('headers', [])
                                    if isinstance(headers_val, str):
                                        try: headers_val = json.loads(headers_val)
                                        except: headers_val = []
                                    assertions_val = props.get('assertions', [])
                                    if isinstance(assertions_val, str):
                                        try: assertions_val = json.loads(assertions_val)
                                        except: assertions_val = []
                                    extracts_val = props.get('extracts', [])
                                    if isinstance(extracts_val, str):
                                        try: extracts_val = json.loads(extracts_val)
                                        except: extracts_val = []
                                    api_data = {
                                        'id': e.get('id'),
                                        'name': e.get('name', 'API Request (E2E)'),
                                        'method': props.get('method', 'GET'),
                                        'url': props.get('url', ''),
                                        'headers': headers_val,
                                        'body': props.get('body', ''),
                                        'assertions': assertions_val,
                                        'parallel': False,
                                        'extracts': extracts_val,
                                        'delay': props.get('delay', 0)
                                    }
                                    all_steps.append({'data': api_data, 'type': 'api'})
                                elif e.get('type') in ['db_query', 'database', 'sql_query']:
                                    all_steps.append({'data': e, 'type': 'db_query'})
                                else:
                                    all_steps.append({'data': e, 'type': 'e2e'})

                            db_queries = card.get('dbQueries', []) or card.get('db_queries', []) or card.get('dbSteps', [])
                            for db_q in db_queries:
                                all_steps.append({'data': db_q, 'type': 'db_query'})
                                
                            message_queues = card.get('messageQueues', []) or card.get('message_queues', [])
                            for mq in message_queues:
                                all_steps.append({'data': mq, 'type': 'message_queue'})
                            
                            execution_lock = threading.Lock()

                            # --- Execution Helpers ---
                            async def execute_step_internal(step_entry):
                                nonlocal flow_success_count, flow_fail_count, e2e_executor, path_success
                                step_data = step_entry['data']
                                step_type = step_entry['type']
                                
                                resp_status = 0
                                resp_reason = "Pending"
                                resp_headers = {}
                                resp_text = ""
                                duration = 0
                                assertion_results = []
                                assertions_passed = True
                                final_error_message = None
                                healed_selector = None
                                headers = {}
                                body = ""
                                url = ""
                                method = ""

                                try:
                                    if step_type in ['db_query', 'database']:
                                        try:
                                            from app.services.database_executor_service import DatabaseExecutorService
                                            method = "SQL"
                                            props = step_data.get('properties', {}) if isinstance(step_data.get('properties'), dict) else {}
                                            raw_conn = step_data.get('connection_string') or step_data.get('connectionString') or props.get('connection_string') or props.get('connectionString') or ""
                                            url_val = FlowExecutorService.replace_vars(raw_conn, current_path_vars) or ""
                                            url = f"db://{url_val}" if url_val else "db://sql-database"
                                            
                                            loop = asyncio.get_event_loop()
                                            import functools
                                            db_func = functools.partial(DatabaseExecutorService.execute_db_step, step_data, current_path_vars)
                                            db_res = await loop.run_in_executor(None, db_func)
                                            
                                            resp_status = db_res['status']
                                            resp_reason = db_res['reason']
                                            resp_text = db_res['text']
                                            duration = db_res['duration']
                                            final_error_message = db_res['error']
                                            assertion_results = db_res['assertions']
                                            assertions_passed = db_res['assertions_passed']
                                            if db_res.get('extracted'):
                                                final_variables.update(db_res['extracted'])
                                                current_path_vars.update(db_res['extracted'])
                                        except Exception as db_err:
                                            logger.error(f"FATAL: DB Execution Error: {str(db_err)}")
                                            resp_status = 500
                                            resp_reason = "DB Error"
                                            resp_text = str(db_err)
                                            final_error_message = str(db_err)
                                    elif step_type == 'message_queue':
                                        try:
                                            from app.services.queue_executor_service import QueueExecutorService
                                            method = "MQ_" + str(step_data.get('broker', 'mq')).upper()
                                            url = step_data.get('queueName', 'topic/queue')
                                            
                                            loop = asyncio.get_event_loop()
                                            import functools
                                            mq_func = functools.partial(QueueExecutorService.execute_queue_step, step_data, current_path_vars)
                                            mq_res = await loop.run_in_executor(None, mq_func)
                                            
                                            resp_status = mq_res['status']
                                            resp_reason = "MQ Success" if resp_status == 200 else "MQ Error"
                                            resp_text = str(mq_res.get('message') or mq_res.get('error') or '')
                                            duration = mq_res['response_time']
                                            final_error_message = mq_res.get('error')
                                            assertion_results = mq_res.get('assertion_results', [])
                                            assertions_passed = mq_res.get('assertions_passed', True)
                                            if mq_res.get('extracted'):
                                                final_variables.update(mq_res['extracted'])
                                                current_path_vars.update(mq_res['extracted'])
                                        except Exception as mq_err:
                                            logger.error(f"FATAL: MQ Execution Error: {str(mq_err)}")
                                            resp_status = 500
                                            resp_reason = "MQ Error"
                                            resp_text = str(mq_err)
                                            final_error_message = str(mq_err)
                                            
                                    attempt = 0 # Ensure attempt is defined for all flow types before logging history
                                    
                                    if step_type == 'e2e':
                                        try:
                                            if not e2e_executor:
                                                if effective_flow_type == 'mobile':
                                                    from app.services.appium_executor_service import AppiumExecutorService
                                                    e2e_executor = AppiumExecutorService()
                                                    await e2e_executor.start(
                                                        video_dir=video_dir if capture_video else None,
                                                        db=db,
                                                        product_id=product_id
                                                    )
                                                else:
                                                    from app.services.playwright_executor_service import PlaywrightExecutorService
                                                    e2e_executor = PlaywrightExecutorService(headless=not visible_execution)
                                                    await e2e_executor.start(video_dir=video_dir if capture_video else None)
                                            # Resolve variables in E2E step data
                                            step_data_str = json.dumps(step_data)
                                            resolved_step_data = json.loads(FlowExecutorService.replace_vars(step_data_str, current_path_vars))
                                            
                                            # Preserve original unresolved properties for healing
                                            resolved_step_data['_original_properties'] = step_data.get('properties', {})
                                            
                                            step_data = resolved_step_data
                                            
                                            # Inject timeout dynamically from Schedule Config (if present)
                                            if global_timeout_ms is not None:
                                                step_data.setdefault('properties', {})['timeout'] = global_timeout_ms
                                            # No step-level retries: steps execute once and fail directly without looping
                                            step_data.setdefault('properties', {})['retries'] = 0
                                            if global_retry_test is not None:
                                                step_data.setdefault('properties', {})['ramp_up'] = global_retry_test

                                            # Sanitize E2E URL for Docker (e.g. localhost -> flow-frontend)
                                            if step_data.get('type') == 'browser' and step_data.get('properties', {}).get('value'):
                                                val = step_data['properties']['value'].strip()
                                                if val and not val.startswith(('http://', 'https://')):
                                                    val = f"http://{val}"
                                                step_data['properties']['value'] = FlowExecutorService.sanitize_url_for_docker(val)
                                                logger.info(f"      🔗 Sanitize E2E URL: {val} -> {step_data['properties']['value']}")

                                            # Execute real Playwright step
                                            start_time = time.time()
                                            resp = await e2e_executor.execute_step(step_data, capture_screenshot=capture_screenshot, db=db, user_id=user_id)
                                            duration = int((time.time() - start_time) * 1000)
                                            
                                            resp_status = resp['status']
                                            resp_reason = resp['reason']
                                            resp_text = resp['text']
                                            healed_selector = resp.get('healed_selector')
                                            
                                            method = "BROWSER"
                                            url = step_data.get('properties', {}).get('value', '') or step_data.get('properties', {}).get('selector', '')
                                        except Exception as e:
                                            logger.error(f"FATAL: E2E Execution Error: {str(e)}")
                                            resp_status = 500
                                            resp_reason = "E2E Error"
                                            resp_text = str(e)
                                    else:
                                        method = step_data.get('method', 'GET')
                                        url = FlowExecutorService.replace_vars(step_data.get('url', ''), current_path_vars)
                                        
                                        if method == 'PYTHON':
                                            try:
                                                if not e2e_executor:
                                                    from app.services.playwright_executor_service import PlaywrightExecutorService
                                                    e2e_executor = PlaywrightExecutorService(headless=not visible_execution)
                                                    await e2e_executor.start()
                                                resp = await e2e_executor.execute_step(step_data, capture_screenshot=capture_screenshot, db=db, user_id=user_id)
                                                resp_status = resp['status']
                                                resp_reason = resp['reason']
                                                resp_text = resp['text']
                                                healed_selector = resp.get('healed_selector')
                                            except Exception as e:
                                                logger.error(f"FATAL: Playwright Init Error: {str(e)}")
                                                resp_status = 500
                                                resp_reason = "E2E Driver Error"
                                                resp_text = str(e)
                                        else:
                                            # Inject global config (timeout) dynamically from Schedule Config for API steps
                                            # No step-level retries: steps execute once and fail directly without looping
                                            step_data['retries'] = 0
                                            if global_timeout_ms is not None:
                                                step_data['timeout'] = global_timeout_ms

                                            url = ensure_absolute_url(url)
                                            
                                            # ✅ RE-ENABLED: Normalize URL for Docker internal networking
                                            url = FlowExecutorService.sanitize_url_for_docker(url)

                                            if FlowExecutorService.is_blocked_domain(url):
                                                logger.warning(f"      ⛔ Skipping Blocked/Ad Domain: {url}")
                                                return
                                            
                                            # --- 1. Headers Reconstruction ---
                                            headers_raw = step_data.get('headers', {})
                                            if isinstance(headers_raw, list):
                                                mapping = {}
                                                for h in headers_raw:
                                                    if isinstance(h, dict):
                                                        k = h.get('key')
                                                        v = h.get('value')
                                                        if k is not None: mapping[k] = v
                                                        else: mapping.update(h)
                                                headers_raw = mapping
                                            
                                            headers_json_str = json.dumps(headers_raw)
                                            headers = json.loads(FlowExecutorService.replace_vars(headers_json_str, current_path_vars))
                                            headers = {str(k): str(v) for k, v in headers.items() if k.lower() not in ['content-length', 'host']} 
                                            
                                            headers_lower = {k.lower(): v for k, v in headers.items()}
                                            if "accept" not in headers_lower:
                                                headers["Accept"] = "*/*"
                                            if "user-agent" not in headers_lower:
                                                headers["User-Agent"] = "Flow-QA-Runner/1.0"
                                            # --- 2. Params Reconstruction (Query String) ---
                                            params_raw = step_data.get('params', {})
                                            if isinstance(params_raw, list):
                                                mapping = {}
                                                for p in params_raw:
                                                    if isinstance(p, dict):
                                                        k = p.get('key')
                                                        v = p.get('value')
                                                        if k is not None: mapping[k] = v
                                                        else: mapping.update(p)
                                                params_raw = mapping
                                            
                                            params_json_str = json.dumps(params_raw)
                                            params = json.loads(FlowExecutorService.replace_vars(params_json_str, current_path_vars))
                                            if not isinstance(params, dict): params = {}
                                            params = {str(k): str(v) for k, v in params.items() if v is not None}

                                            # --- 3. Body Reconstruction ---
                                            body_raw = step_data.get('body', '')
                                            if isinstance(body_raw, (dict, list)):
                                                body_json_str = json.dumps(body_raw)
                                                body = FlowExecutorService.replace_vars(body_json_str, current_path_vars)
                                            else:
                                                body = FlowExecutorService.replace_vars(str(body_raw), current_path_vars)
                                            
                                            if body is None: body = ""

                                            ct_key = next((k for k in headers.keys() if k.lower() == 'content-type'), None)
                                            body_is_urlencoded = isinstance(body, str) and ('=' in body or '&' in body) and not body.strip().startswith('{')
                                            
                                            if ct_key and 'multipart/form-data' in str(headers[ct_key]).lower() and body_is_urlencoded:
                                                headers[ct_key] = 'application/x-www-form-urlencoded'
                                            
                                            if 'auth/login' in url and body_is_urlencoded:
                                                if not ct_key: headers['Content-Type'] = 'application/x-www-form-urlencoded'
                                                elif 'json' in headers[ct_key]: headers[ct_key] = 'application/x-www-form-urlencoded'

                                            # --- 3. Delay Handling ---
                                            delay_val = step_data.get('delay')
                                            if delay_val:
                                                try:
                                                    # Handle millisecond strings or ints
                                                    if isinstance(delay_val, str):
                                                        # Remove 'ms' if exists, then cast
                                                        clean_delay = re.sub(r'[^0-9.]', '', str(delay_val))
                                                        delay_ms = float(clean_delay) if clean_delay else 0
                                                    else:
                                                        delay_ms = float(delay_val)
                                                    
                                                    if delay_ms > 0:
                                                        logger.info(f"      ⏳ Delay specified: waiting {delay_ms}ms")
                                                        await asyncio.sleep(delay_ms / 1000.0)
                                                except Exception as de:
                                                    logger.warning(f"      ⚠️ Invalid delay value '{delay_val}': {de}")

                                            try:
                                                cache_ttl_minutes = int(step_data.get('cacheTTL', 0) or 0)
                                            except ValueError:
                                                cache_ttl_minutes = 0

                                            cached_run = None
                                            
                                            if cache_ttl_minutes > 0:
                                                from app.models.api_test_history_models import ApiExecutionHistory
                                                from sqlalchemy import desc, func
                                                from datetime import timedelta
                                                import datetime
                                                
                                                # Depending on timezone configuration, func.now() or datetime.now(datetime.timezone.utc) could be used
                                                time_threshold = func.now() - timedelta(minutes=cache_ttl_minutes)
                                                cached_run = db.query(ApiExecutionHistory).filter(
                                                    ApiExecutionHistory.api_id == str(step_data.get('id')),
                                                    ApiExecutionHistory.environment_id == env_id,
                                                    ApiExecutionHistory.error_message == None,
                                                    ApiExecutionHistory.status_text != 'OK (Cached)',
                                                    ApiExecutionHistory.created_at >= time_threshold
                                                ).order_by(desc(ApiExecutionHistory.created_at)).first()
                                                
                                            max_retries = int(step_data.get('retries', 0))
                                            for attempt in range(max_retries + 1):
                                                final_error_message = None
                                                try:
                                                    if cached_run:
                                                        logger.info(f"      ♻️ CACHE HIT: Skipping HTTP request for API {step_data.get('name', step_data.get('id'))} (Valid for {cache_ttl_minutes}m)")
                                                        resp_status = cached_run.status_code
                                                        resp_reason = "OK (Cached)"
                                                        resp_headers = cached_run.response_headers or {}
                                                        resp_text = cached_run.response_body or ""
                                                        duration = cached_run.response_time or 0
                                                        try:
                                                            import json as _json
                                                            resp_json = _json.loads(resp_text) if resp_text else None
                                                        except Exception:
                                                            resp_json = None
                                                        step_data['_was_cached'] = True
                                                    else:
                                                        start_time = time.time()
                                                        api_timeout = float(step_data.get('timeout', 30000)) / 1000.0
                                                        
                                                        verify_ssl = step_data.get('verifySSL', step_data.get('verify_ssl', True))
                                                        if isinstance(verify_ssl, str):
                                                            verify_ssl = verify_ssl.lower() not in ('false', '0', 'no')

                                                        loop = asyncio.get_event_loop()
                                                        import functools
                                                        request_func = functools.partial(
                                                            flow_session.request, method, url,
                                                            headers=headers, data=body, params=params,
                                                            timeout=api_timeout, verify=verify_ssl
                                                        )
                                                        resp = await loop.run_in_executor(None, request_func)
                                                        
                                                        duration = int((time.time() - start_time) * 1000)
                                                        resp_status = resp.status_code
                                                        resp_reason = resp.reason
                                                        resp_headers = dict(resp.headers)
                                                        resp_text = resp.text
                                                        
                                                        try:
                                                            resp_json = resp.json()
                                                        except ValueError:
                                                            resp_json = None

                                                    # --- Assertions (delegated to assertion_engine) ---
                                                    assertions_raw = step_data.get('assertions', [])
                                                    if assertions_raw:
                                                        assertion_results, assertions_passed = evaluate_all_assertions(
                                                            assertions_raw, resp_status, resp_headers,
                                                            resp_json, resp_text, duration
                                                        )
                                                    else:
                                                        assertions_passed = True

                                                    if step_data.get('assertions') and not assertions_passed:
                                                        final_error_message = "Assertions Failed"
                                                    elif resp_status >= 400:
                                                        final_error_message = f"HTTP Error {resp_status}"
                                                    elif resp_status == 0:
                                                        final_error_message = "Step Timeout/Incomplete"

                                                    if not final_error_message:
                                                        break # Success
                                                    else:
                                                        if attempt < max_retries:
                                                            logger.info(f"      🔄 Retrying API {step_data.get('name')} (Attempt {attempt+1}/{max_retries})")
                                                            await asyncio.sleep(1) # simple backoff
                                                except Exception as req_ex:
                                                    logger.error(f"Request failed: {req_ex}")
                                                    resp_status = 500
                                                    final_error_message = str(req_ex)
                                                    if attempt < max_retries:
                                                        logger.info(f"      🔄 Retrying API {step_data.get('name')} due to exception (Attempt {attempt+1}/{max_retries})")
                                                        await asyncio.sleep(1)
                                                        
                                            # --- Extraction (delegated to extraction_engine) ---
                                            extracts = step_data.get('extracts', [])
                                            if extracts and not final_error_message:
                                                extracted = process_extractions(
                                                    extracts, resp_headers, resp_json,
                                                    current_path_vars, lock=execution_lock,
                                                    db=db, product_id=product_id, env_id=env_id
                                                )
                                                final_variables.update(extracted)
                                except Exception as outer_ex:
                                    logger.error(f"Unhandled execution error: {outer_ex}")
                                    if not final_error_message:
                                        final_error_message = str(outer_ex)

                                # --- Global Error Check ---
                                # Guarantee that ANY step returning an error status is correctly flagged as a failure
                                if not final_error_message and resp_status >= 400:
                                    final_error_message = resp_text if resp_text else f"Execution Failed with status {resp_status}: {resp_reason}"

                                with execution_lock:
                                    if final_error_message: 
                                        # Fail-Fast: Interrupt path on ANY primary step failure
                                        path_success = False
                                        flow_fail_count += 1
                                        logger.error(f"        ❌ Step failed: {final_error_message}. "
                                                     f"Type: {flow_type}. Node: {current_id}. Interrupting execution.")
                                    else: 
                                        flow_success_count += 1

                                base_api_name = step_data.get('name') or "Step"
                                if step_data.get('_was_cached'):
                                    base_api_name += " (CACHED)"
                                if attempt > 0:
                                    base_api_name += f":::RETRIES:::{attempt}"

                                try:
                                    resolved_retry = current_session_retry_count
                                    hist = ExecutionHistoryCreate(
                                        batch_id=batch_id,
                                        api_id=str(step_data.get('id')),
                                        api_name=base_api_name,
                                        project_id=p_id or product_id, # Use Feature ID, fallback to Product ID
                                        flow_id=flow_meta['id'],
                                        node_id=current_id,
                                        schedule_id=schedule_id,
                                        feature_name=feature_name,
                                        node_name=card.get('name'),
                                        method=method,
                                        url=url,
                                        request_headers=headers,
                                        request_body=body,
                                        status_code=resp_status,
                                        response_body=resp_text,
                                        response_time=duration,
                                        environment_id=env_id,
                                        error_message=final_error_message,
                                        assertions=assertion_results,
                                        healed_selector=healed_selector,
                                        execution_type=actual_exec_type,
                                        retry_count=resolved_retry
                                    )
                                    
                                    # The main action step (e.g., Click, Type) is appended FIRST
                                    with execution_lock:
                                        history_buffer.append(hist)
                                except Exception as hist_create_err:
                                    logger.error(f"Error creating ExecutionHistoryCreate for step: {hist_create_err}")
                                
                                # Fetch any background HTTP requests intercepted by the browser during this step
                                # We append them AFTER the main step so the sequence makes sense (Action -> Resulting Requests)
                                if step_type == 'e2e' and e2e_executor:
                                    intercepted = e2e_executor.pop_captured_requests()
                                    seen_bg = set()
                                    for req in intercepted:
                                        bg_key = (req['method'], req['url'])
                                        if bg_key in seen_bg:
                                            continue
                                        seen_bg.add(bg_key)

                                        bg_hist = ExecutionHistoryCreate(
                                            batch_id=batch_id,
                                            api_id=None,
                                            api_name=f"{req['method']} {(req['url'][:40] + '...') if len(req['url']) > 40 else req['url']}",
                                            project_id=p_id or product_id,
                                            flow_id=flow_meta['id'],
                                            node_id=current_id,
                                            schedule_id=schedule_id,
                                            feature_name=feature_name,
                                            node_name=card.get('name'),
                                            method=req['method'],
                                            url=req['url'],
                                            status_code=req['status'],
                                            response_body="", 
                                            response_time=req['duration_ms'],
                                            environment_id=env_id,
                                            error_message=f"HTTP Error {req['status']}" if req['status'] >= 400 else None,
                                            assertions=None,
                                            execution_type=actual_exec_type,
                                            retry_count=current_session_retry_count
                                        )
                                        with execution_lock:
                                            history_buffer.append(bg_hist)
                                        
                                        # Visually count intercepted requests for suite metrics (optional, avoids '0 steps' visual bug)
                                        with execution_lock:
                                            if req['status'] >= 400:
                                                flow_fail_count += 1
                                                # We no longer set path_success = False here for background requests
                                                # This prevents silent aborts when analytics/favicons fail in E2E tests
                                            else:
                                                flow_success_count += 1

                            # --- Execution Loop ---
                            try:
                                # --- HIBRID EXECUTION: Group contiguous parallel blocks ---
                                i = 0
                                while i < len(all_steps):
                                    if not path_success: break
                                    
                                    step_entry = all_steps[i]
                                    is_parallel_step = step_entry['data'].get('parallel', False)
                                    
                                    if is_parallel_step:
                                        # Gather contiguous parallel steps
                                        parallel_batch = []
                                        while i < len(all_steps) and all_steps[i]['data'].get('parallel', False):
                                            parallel_batch.append(all_steps[i])
                                            i += 1
                                        
                                        logger.info(f"      ⚡ PARALLEL BATCH: Running {len(parallel_batch)} steps for Node {current_id}")
                                        await asyncio.gather(*(execute_step_internal(step) for step in parallel_batch))
                                        
                                        # Save history after batch
                                        if history_buffer:
                                            HistoryService.save_batch(db, history_buffer, user_id)
                                            history_buffer.clear()
                                    else:
                                        # Sequential Step
                                        logger.info(f"      ▶️ SEQUENTIAL STEP: Running 1 step for Node {current_id}")
                                        await execute_step_internal(step_entry)
                                        i += 1
                                        
                                        # Save history after each step for real-time monitor
                                        if history_buffer:
                                            HistoryService.save_batch(db, history_buffer, user_id)
                                            history_buffer.clear()
                                            
                                        if not path_success: break
                            except Exception as loop_ex:
                                logger.error(f"Critical error in execution loop: {loop_ex}")


                    # Finalize Video Recording PER PATH
                    if e2e_executor:
                        try:
                            logger.info(f"⏳ Waiting 1s for video to capture final state (Scenario {path_number})...")
                            await asyncio.sleep(1)
                            
                            # Catch lingering API calls right before we tear down the browser
                            final_intercepted = e2e_executor.pop_captured_requests()
                            if final_intercepted:
                                final_buffer = []
                                for req in final_intercepted:
                                    bg_hist = ExecutionHistoryCreate(
                                        batch_id=batch_id,
                                        api_id=None,
                                        api_name=f"{req['method']} {(req['url'][:40] + '...') if len(req['url']) > 40 else req['url']}",
                                        project_id=p_id or product_id,
                                        flow_id=flow_meta['id'],
                                        node_id=None,
                                        schedule_id=schedule_id,
                                        feature_name=feature_name,
                                        node_name="Finalização (Background)",
                                        method=req['method'],
                                        url=req['url'],
                                        status_code=req['status'],
                                        response_body="", 
                                        response_time=req['duration_ms'],
                                        environment_id=env_id,
                                        error_message=f"HTTP Error {req['status']}" if req['status'] >= 400 else None,
                                        assertions=None,
                                        execution_type=actual_exec_type,
                                        retry_count=current_session_retry_count
                                    )
                                    final_buffer.append(bg_hist)
                                HistoryService.save_batch(db, final_buffer, user_id)
                                
                            trace_path = f"{video_dir}/trace_{path_number}.zip" if capture_video else None
                            if effective_flow_type == 'mobile':
                                if capture_video:
                                    await e2e_executor.stop_screen_recording()
                            else:
                                await e2e_executor.stop(trace_path=trace_path)
                            
                            # Pequeno delay adicional para garantir que o SO finalizou a gravação do arquivo (Playwright rename/flush)
                            await asyncio.sleep(0.5)
                            
                            if os.path.exists(video_dir):
                                import glob
                                videos_files = glob.glob(os.path.join(video_dir, '*.[wm][pe][b4]*'))
                                if videos_files:
                                    latest_file = max(videos_files, key=os.path.getctime)
                                    video_name = os.path.basename(latest_file)
                                    video_url = f"/videos/{batch_id}/{video_name}"
                                    logger.info(f"✅ Video Path {path_number} recorded successfully: {video_url}")
                                    HistoryService.update_video_url_by_batch(db, batch_id, video_url)
                        except Exception as ve:
                            logger.warning(f"Video finalize failed for path: {ve}")

            # asyncio.run() is thread-safe in Python 3.7+ and creates its own
            # isolated event loop per call — safe to call from multiple threads.
            async def run_paths_wrapper():
                try:
                    await run_paths()
                finally:
                    if effective_flow_type == 'mobile' and e2e_executor:
                        try:
                            logger.info("📱 [Mobile] Stopping Appium session after all paths completed.")
                            await e2e_executor.stop()
                        except Exception as stop_err:
                            logger.warning(f"Error stopping Appium executor: {stop_err}")

            asyncio.run(run_paths_wrapper())

            # Update the original variables_dict with the extracted results
            variables_dict.update(final_variables)

            # Any remaining history (usually none as it is saved in loop)
            if history_buffer:
                HistoryService.save_batch(db, history_buffer, user_id)
                history_buffer.clear()

            return flow_success_count, flow_fail_count

        except Exception as e:
            logger.error(f"Error executing flow {flow_meta['id']}: {e}")
            import traceback
            traceback.print_exc()
            if history_buffer:
                try:
                    HistoryService.save_batch(db, history_buffer, user_id)
                    history_buffer.clear()
                except Exception:
                    pass
            db.rollback()
            return 0, 0

    @staticmethod
    def get_merged_variables(db: Session, product_id: int, env_id: int):
        from app.services.environment_service import EnvironmentService
        
        logger.info(f"🔍 [get_merged_variables] Fetching vars for Product: {product_id}, Env: {env_id}")
        
        global_vars = VariableService.get_all(db, product_id, environment_id=None)
        logger.info(f"    found {len(global_vars)} global vars")
        
        env_vars = []
        if env_id:
            env_vars = VariableService.get_all(db, product_id, environment_id=env_id)
            logger.info(f"    found {len(env_vars)} env vars for env_id {env_id}")
        else:
            # FIX: If no environment selected (Global), we SHOULD attempts to find a default environment
            # because most flows rely on environment-specific variables (like BASE_URL).
            envs = EnvironmentService.get_by_project(db, product_id)
            if envs:
                fallback_env = envs[0]
                logger.warning(f"    ⚠️ No Environment selected (Scheduled?). Fallback to First Env: {fallback_env.name} (ID: {fallback_env.id})")
                extra_vars = VariableService.get_all(db, product_id, environment_id=fallback_env.id)
                env_vars.extend(extra_vars)

        variables = {}
        for v in global_vars:
            variables[v.name] = {
                "name": v.name,
                "value": v.value,
                "type": getattr(v, "type", "static"),
                "faker_type": getattr(v, "faker_type", None),
                "faker_options": getattr(v, "faker_options", {})
            }
            
        for v in env_vars:
            variables[v.name] = {
                "name": v.name,
                "value": v.value,
                "type": getattr(v, "type", "static"),
                "faker_type": getattr(v, "faker_type", None),
                "faker_options": getattr(v, "faker_options", {})
            }
            
        logger.info(f"    ✅ Total Merged Variables: {len(variables)} keys: {list(variables.keys())}")
        return variables

    @staticmethod
    def execute_feature_group(db: Session, feature_id: int, env_id: int, company_id: int, schedule_id: int = None, user_id: int = 1, max_concurrency: int = None, flow_type: str = 'api', capture_video: bool = False, capture_screenshot: bool = False, visible_execution: bool = False, dataset_row: dict = None, failed_item_ids: list = None):
        """
        Executes all flows within a specific feature.
        """
        from app.services.feature_service import FeatureService
        
        feature = FeatureService.get_by_id(db, feature_id, company_id)
        if not feature:
            logger.error(f"Feature {feature_id} not found")
            return 0, 0

        # Resolve fallback env if none provided
        if not env_id:
            from app.services.environment_service import EnvironmentService
            envs = EnvironmentService.get_by_project(db, feature.product_id)
            if envs:
                fallback_env = envs[0]
                env_id = fallback_env.id
                logger.info(f"    ⚠️ No Environment selected for Feature. Fallback to First Env: {fallback_env.name} (ID: {fallback_env.id})")

        # list_by_project actually returns flows for a "feature" (project in old naming)
        flows = FlowService.list_by_project(db, feature.id, company_id)
        variables = FlowExecutorService.get_merged_variables(db, feature.product_id, env_id)

        success_count = 0
        fail_count = 0

        logger.info(f"🚀 Executing Feature Group: {feature.name} (ID: {feature.id})")


        logger.info(f"🚀 Executing Feature Group: {feature.name} (ID: {feature.id})")

        # FIX: Only execute the latest flow to match UI behavior (1 Feature = 1 Active Flow)
        # list_by_project returns flows ordered by updated_at desc
        if flows:
            # Filter flows by type safely
            def matches_flow_type(f_item, target_type):
                ft = f_item.get('flow_type')
                if not ft:
                    return True
                if target_type == 'mobile':
                    return ft in ['mobile', 'e2e']
                if target_type in ['e2e', 'web']:
                    return ft in ['e2e', 'web']
                return ft == target_type

            typed_flows = [f for f in flows if matches_flow_type(f, flow_type)]
            
            if not typed_flows:
                logger.warning(f"No flows of type '{flow_type}' found for feature {feature.id}. Falling back to latest regardless of type.")
                latest_flow = flows[0]
            else:
                latest_flow = typed_flows[0]

            if len(flows) > 1:
                logger.info(f"ℹ️  Selecting flow '{latest_flow.get('name')}' (ID: {latest_flow.get('id')}, Type: {latest_flow.get('flow_type')}) for execution.")
            
            # Worker function for parallel execution of the same feature
            def process_feature_run(idx):
                f_success = 0
                f_fail = 0
                thread_db = SessionLocal()
                start_t = time.time()
                try:
                    logger.info(f"  📂 [PARALLEL] Feature Run {idx+1}/{max_workers} STARTED: {feature.name} (ID: {feature.id}) at t={start_t:.2f}")
                    s, f = FlowExecutorService.execute_flow_logic(
                        thread_db, latest_flow, feature.product_id, env_id, company_id, 
                        variables.copy(), feature_name=feature.name, schedule_id=schedule_id, 
                        user_id=user_id, capture_video=capture_video, capture_screenshot=capture_screenshot, 
                        flow_type=flow_type, visible_execution=visible_execution, dataset_row=dataset_row,
                        failed_item_ids=failed_item_ids
                    )
                    f_success = s
                    f_fail = f
                except Exception as feat_ex:
                    logger.error(f"Failed to process feature {feature.id} run {idx+1}: {feat_ex}")
                    import traceback; traceback.print_exc()
                    f_fail = 1
                finally:
                    thread_db.close()
                    elapsed = time.time() - start_t
                    logger.info(f"  ✅ [PARALLEL] Feature Run {idx+1}/{max_workers} DONE in {elapsed:.1f}s")
                return f_success, f_fail

            max_workers = 1 if flow_type == 'mobile' else (max_concurrency if max_concurrency else 1)
            if max_workers > 1:
                logger.info(f"🚀 [PARALLEL] Starting {max_workers} parallel runs for feature {feature.name}")
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    full_results = list(executor.map(process_feature_run, range(max_workers)))
                for s, f in full_results:
                    success_count += s
                    fail_count += f
            else:
                s, f = FlowExecutorService.execute_flow_logic(db, latest_flow, feature.product_id, env_id, company_id, variables, feature_name=feature.name, schedule_id=schedule_id, user_id=user_id, capture_video=capture_video, capture_screenshot=capture_screenshot, flow_type=flow_type, visible_execution=visible_execution, dataset_row=dataset_row, failed_item_ids=failed_item_ids)
                success_count += s
                fail_count += f
        else:
            logger.warning(f"No flows found for feature {feature.id}")
        
        return success_count, fail_count

    @staticmethod
    def execute_flow(db: Session, flow_id: str, env_id: int, company_id: int, schedule_id: int = None, user_id: int = 1, flow_type: str = 'api', capture_video: bool = False, capture_screenshot: bool = False, visible_execution: bool = False, dataset_row: dict = None, failed_item_ids: list = None):
        # We route directly to execute_flow_by_id
        return FlowExecutorService.execute_flow_by_id(db, flow_id, env_id, company_id, schedule_id, user_id, 1, flow_type, capture_video, capture_screenshot, visible_execution, dataset_row, failed_item_ids=failed_item_ids)

    @staticmethod
    def execute_flow_by_id(db: Session, flow_id: str, env_id: int, company_id: int, schedule_id: int = None, user_id: int = 1, max_concurrency: int = None, flow_type: str = 'api', capture_video: bool = False, capture_screenshot: bool = False, visible_execution: bool = False, dataset_row: dict = None, failed_item_ids: list = None):
        flow_meta = None
        # 1. Try loading by FlowDB.id directly if numeric
        try:
            numeric_id = int(flow_id)
            flow_meta = FlowService.load(db, flow_id=numeric_id, company_id=company_id)
        except (ValueError, TypeError):
            pass

        # 2. If no nodes found, try loading by project_id (feature_id) with flow_type
        if not flow_meta or not flow_meta.get('nodes'):
            try:
                numeric_proj_id = int(flow_id)
                flow_meta = FlowService.load(db, project_id=numeric_proj_id, company_id=company_id, flow_type=flow_type)
            except (ValueError, TypeError):
                pass

        if not flow_meta or not flow_meta.get('nodes'):
            logger.warning(f"Flow {flow_id} (flow_type={flow_type}) not found or has no nodes for company {company_id}")
            return 0, 1

        feature_name = "Unknown Feature"
        product_id = flow_meta.get('product_id')
        if flow_meta.get('project_id'):
            feature = db.query(FeatureModel).filter(FeatureModel.id == flow_meta.get('project_id')).first()
            if feature:
                feature_name = feature.name
                if not product_id: product_id = feature.product_id

        if not product_id:
            product_id = 0

        if not env_id and product_id:
            from app.services.environment_service import EnvironmentService
            envs = EnvironmentService.get_by_project(db, product_id)
            if envs:
                env_id = envs[0].id

        variables = FlowExecutorService.get_merged_variables(db, product_id, env_id)

        max_workers = 1 if flow_type == 'mobile' else (max_concurrency if max_concurrency else 1)
        if max_workers > 1:
            success_count = 0
            fail_count = 0
            def process_flow_run(idx):
                f_s, f_f = 0, 0
                thread_db = SessionLocal()
                try:
                    s, f = FlowExecutorService.execute_flow_logic(
                        thread_db, flow_meta, product_id, env_id, company_id, 
                        variables.copy(), feature_name=feature_name, schedule_id=schedule_id, 
                        user_id=user_id, capture_video=capture_video, capture_screenshot=capture_screenshot, 
                        flow_type=flow_type, visible_execution=visible_execution, dataset_row=dataset_row,
                        failed_item_ids=failed_item_ids
                    )
                    f_s = s
                    f_f = f
                except Exception as ex:
                    logger.error(f"Failed execution run {idx+1} for flow {flow_id}: {ex}")
                    f_f = 1
                finally:
                    thread_db.close()
                return f_s, f_f

            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                full_results = list(executor.map(process_flow_run, range(max_workers)))
            for s, f in full_results:
                success_count += s
                fail_count += f
            return success_count, fail_count
        else:
            return FlowExecutorService.execute_flow_logic(db, flow_meta, product_id, env_id, company_id, variables, feature_name=feature_name, schedule_id=schedule_id, user_id=user_id, capture_video=capture_video, capture_screenshot=capture_screenshot, flow_type=flow_type, visible_execution=visible_execution, dataset_row=dataset_row, failed_item_ids=failed_item_ids)

    @staticmethod
    def execute_suite(db: Session, product_id: int, env_id: int, company_id: int, schedule_id: int = None, user_id: int = 1, max_concurrency: int = None, flow_type: str = 'api', capture_video: bool = False, capture_screenshot: bool = False, visible_execution: bool = False, dataset_row: dict = None, failed_item_ids: list = None):
        """
        Executes all features within a product.
        """
        feature_objs = db.query(FeatureModel).filter(FeatureModel.product_id == product_id).all()
        features = [{'id': f.id, 'name': f.name, 'product_id': f.product_id} for f in feature_objs]
        
        if not features:
            logger.warning(f"No features found for product {product_id}")
            return 0, 0

        # Resolve fallback env if none provided
        if not env_id:
            from app.services.environment_service import EnvironmentService
            envs = EnvironmentService.get_by_project(db, product_id)
            if envs:
                fallback_env = envs[0]
                env_id = fallback_env.id
                logger.info(f"    ⚠️ No Environment selected for Suite. Fallback to First Env: {fallback_env.name} (ID: {fallback_env.id})")

        # RAW VARIABLES (Objects converted to safe dicts)
        variables = FlowExecutorService.get_merged_variables(db, product_id, env_id)
        
        total_success = 0
        total_fail = 0

        logger.info(f"🚀 Executing Suite for Product {product_id} - {len(features)} Features")

        # Worker Function for Execution
        def process_feature(feature):
            f_success = 0
            f_fail = 0
            thread_db = SessionLocal()
            start_t = time.time()
            try:
                logger.info(f"  📂 Feature '{feature['name']}' (ID: {feature['id']}) STARTED at t={start_t:.2f}")
                flows = FlowService.list_by_project(thread_db, feature['id'], company_id)
                
                if flows:
                    def matches_flow_type(f_item, target_type):
                        ft = f_item.get('flow_type')
                        if not ft:
                            return True
                        if target_type == 'mobile':
                            return ft in ['mobile', 'e2e']
                        if target_type in ['e2e', 'web']:
                            return ft in ['e2e', 'web']
                        return ft == target_type

                    typed_flows = [f for f in flows if matches_flow_type(f, flow_type)]
                    if not typed_flows:
                        logger.warning(f"No flows matching type '{flow_type}' found for feature {feature['id']}. Skipping.")
                        return f_success, f_fail
                    latest_flow = typed_flows[0]
                    s, f = FlowExecutorService.execute_flow_logic(
                        thread_db, latest_flow, product_id, env_id, company_id, 
                        variables.copy(),
                        feature_name=feature['name'], schedule_id=schedule_id, user_id=user_id,
                        capture_video=capture_video, capture_screenshot=capture_screenshot,
                        flow_type=flow_type, visible_execution=visible_execution, dataset_row=dataset_row,
                        failed_item_ids=failed_item_ids
                    )
                    f_success = s
                    f_fail = f
            except Exception as feat_ex:
                logger.error(f"Failed to process feature {feature['id']}: {feat_ex}")
                import traceback; traceback.print_exc()
                f_fail = 1
            finally:
                thread_db.close()
                elapsed = time.time() - start_t
                logger.info(f"  ✅ Feature '{feature['name']}' DONE in {elapsed:.1f}s")
            return f_success, f_fail

        # Mobile executions run sequentially (max_workers=1) to prevent Appium driver port collisions
        max_workers = 1 if flow_type == 'mobile' else (max_concurrency if max_concurrency else settings.MAX_CONCURRENT_FEATURES)
        
        logger.info(f"🚀 Starting suite execution with {max_workers} worker(s)")
        
        if max_workers > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                full_results = list(executor.map(process_feature, features))
        else:
            full_results = [process_feature(f) for f in features]

        # Aggregate Results
        for s, f in full_results:
            total_success += s
            total_fail += f
        
        return total_success, total_fail
