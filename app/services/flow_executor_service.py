import logging
import time
import json
import re
from datetime import datetime
import requests
from jsonschema import validate, ValidationError
from sqlalchemy.orm import Session
from app.services.flow_service import FlowService
from app.services.history_service import HistoryService
from app.schemas.history_schemas import ExecutionHistoryCreate
from app.services.variable_service import VariableService
from app.schemas.variable_schemas import VariableCreate

logger = logging.getLogger(__name__)

class FlowExecutorService:
    @staticmethod
    def replace_vars(text, variables):
        if not text or not isinstance(text, str): return text
        def replacer(match):
            var_name = match.group(1).strip().upper() # Normalize to UPPER
            
            # 1. Direct Lookup (Fastest)
            if var_name in variables:
                val = variables[var_name]
                # If it's a DB Object (has .value)
                if hasattr(val, 'value'):
                    return str(val.value)
                # If it's a direct value (extracted string)
                return str(val)
            
            # 2. Fallback Search (Slower - for case mismatch handling or object scanning)
            # Only iterate objects that have 'name' attribute
            for v in variables.values():
                if hasattr(v, 'name') and v.name.upper() == var_name:
                    logger.debug(f"    🔍 Replaced '{{match.group(1)}}' with '{v.value}' (Fallback Match)")
                    return str(v.value)
            
            # 3. Not Found - Return original placeholder
            logger.warning(f"    ⚠️ Variable '{{var_name}}' NOT FOUND. Available: {list(variables.keys())}")
            return match.group(0)

        return re.sub(r'\{\{([\w\.\-_]+)\}\}', replacer, text)

    @staticmethod
    def sanitize_url_for_docker(url: str) -> str:
        """
        Rewrites localhost URLs to localhost:8000 when running inside Docker backend.
        The backend listens on port 8000, but variables often point to localhost (port 80).
        """
        if not url: return url
        
        # Check if URL targets localhost port 80 (default)
        # e.g. http://localhost/api/... -> http://localhost:8000/api/...
        # e.g. http://127.0.0.1/api/... -> http://127.0.0.1:8000/api/...
        if url.startswith("http://localhost/") or url.startswith("http://127.0.0.1/"):
             # Insert port 8000 AND remove /api prefix if present (because Nginx usually strips it)
             new_url = url.replace("http://localhost/", "http://localhost:8000/")
             new_url = new_url.replace("http://127.0.0.1/", "http://127.0.0.1:8000/")
             
             # Nginx Logic: /api/ -> /
             if "/api/" in new_url:
                 new_url = new_url.replace("/api/", "/")
                 
             logger.info(f"      🔧 Rewrote Internal URL: {url} -> {new_url}")
             return new_url
             
        # Also handle https if dev uses self-signed
        if url.startswith("https://localhost/") or url.startswith("https://127.0.0.1/"):
             # Assuming backend doesn't do SSL, we might need to downgrade to http OR use port 8000? 
             # Usually backend is http inside container.
             new_url = url.replace("https://localhost/", "http://localhost:8000/")
             new_url = new_url.replace("https://127.0.0.1/", "http://127.0.0.1:8000/")
             
             if "/api/" in new_url:
                 new_url = new_url.replace("/api/", "/")
                 
             logger.info(f"      🔧 Rewrote Internal HTTPS URL: {url} -> {new_url}")
             return new_url
             
        return url

    @staticmethod
    def is_blocked_domain(url: str) -> bool:
        BLACKLIST = [
            "smaato.net", "temu.com", "weborama.fr", "rfihub.com", 
            "doubleclick.net", "google-analytics.com", "criteo.com",
            "pubmatic.com", "adnxs.com", "rubiconproject.com", "openx.net"
        ]
        return any(domain in url for domain in BLACKLIST)

    @staticmethod
    def execute_flow_logic(db: Session, flow_meta, product_id, env_id, company_id, variables_dict, feature_name: str = None, schedule_id: int = None, user_id: int = 1):
        try:
            # Load full flow data with cards
            flow_data = FlowService.load(db, flow_meta['project_id'], company_id, flow_meta['id'])
            cards = flow_data.get('cardData', {})
            
            logger.info(f"    ▶ Executing Flow: {flow_meta.get('name')} (ID: {flow_meta['id']}) - Cards: {len(cards)}")

            if not cards:
                logger.warning(f"     ⚠ No cards found in flow {flow_meta['id']}")
                return 0, 0
            
            nodes = flow_data.get('nodes', [])
            edges = flow_data.get('edges', [])
            
            # Build Graph (Adjacency List)
            adj = {n['id']: [] for n in nodes}
            in_degree = {n['id']: 0 for n in nodes}
            
            # Filter valid edges (where both source and target exist)
            node_ids = set(n['id'] for n in nodes)
            valid_edges = [e for e in edges if e['source'] in node_ids and e['target'] in node_ids]

            for edge in valid_edges:
                src, tgt = edge['source'], edge['target']
                adj[src].append(tgt)
                in_degree[tgt] += 1

            # BFS / Kahn's Algorithm for Topological Sort/Traversal
            # Start with nodes having in_degree 0 (Roots)
            queue = [n['id'] for n in nodes if in_degree[n['id']] == 0]
            
            flow_failed = False  # Track if any step fails
            flow_success_count = 0
            flow_fail_count = 0 
            
            # Sort roots by position.x to respect visual order
            def get_x_pos(nid):
                n = next((x for x in nodes if x['id'] == nid), None)
                return n['position']['x'] if n else 0
            
            queue.sort(key=get_x_pos)
            
            # Use a shared session for the entire flow to persist Cookies (Auth)
            flow_session = requests.Session()
            flow_session.trust_env = False # Disable proxy for performance

            visited = set()
            
            # Accumulate history items for batch save
            history_buffer = []

            while queue:
                current_id = queue.pop(0)
                
                if current_id in visited:
                    continue
                visited.add(current_id)

                # Execute Card for this Node if exists
                card = cards.get(current_id)
                if card:
                    api_calls = card.get('apiCalls', [])
                    logger.info(f"      Running Node: {card.get('name')} ({len(api_calls)} calls)")
                    
                    for api_call in api_calls:
                        method = api_call.get('method', 'GET')
                        url = FlowExecutorService.replace_vars(api_call.get('url', ''), variables_dict)
                        
                        # Initialize response placeholders for history
                        resp_status = 0
                        resp_reason = "Pending"
                        resp_headers = {}
                        resp_text = ""
                        duration = 0
                        assertion_results = []
                        assertions_passed = True
                        final_error_message = None
                        headers = {}
                        body = ""

                        try:
                            if method == 'PYTHON':
                                # --- Logic/Script Step (Playwright) ---
                                logger.info(f"      [SKIP] Logic Step: {api_call.get('name')} (PYTHON)")
                                resp_status = 200
                                resp_reason = "Logic Captured"
                                resp_text = api_call.get('description', 'Playwright Script Content')
                                duration = 0
                            else:
                                if url.startswith('/'):
                                    from app.config import settings
                                    # Remove leading slash to avoid double slash if base ends with one (though join handles it usually, simple concat is safer if we control format)
                                    # Actually, standard is base without slash, path with slash.
                                    # But let's be safe.
                                    base = settings.API_BASE_URL.rstrip('/')
                                    path = url.lstrip('/')
                                    url = f"{base}/{path}"
                                    path = url.lstrip('/')
                                    url = f"{base}/{path}"
                                    logger.info(f"      [FIX] Relative URL detected. Prepended base ({settings.API_BASE_URL}): {url}")
                                
                                # SANITIZATION FOR DOCKER ENV
                                url = FlowExecutorService.sanitize_url_for_docker(url)
                            


                            # 🛑 DOMAIN FILTERING (AdBlock)
                            if FlowExecutorService.is_blocked_domain(url):
                                logger.warning(f"      ⛔ Skipping Blocked/Ad Domain: {url}")
                                continue
                            
                            headers_raw = api_call.get('headers', {})
                            if isinstance(headers_raw, list):
                                mapping = {}
                                for h in headers_raw:
                                        if isinstance(h, dict):
                                            if 'key' in h and 'value' in h:
                                                mapping[h['key']] = h['value']
                                            else:
                                                mapping.update(h)
                                headers_raw = mapping
                            
                            headers_json_str = json.dumps(headers_raw)
                            headers = json.loads(FlowExecutorService.replace_vars(headers_json_str, variables_dict))
                            
                            if isinstance(headers, list): headers = {}
                            if not isinstance(headers, dict) and headers is not None: headers = {}
                            
                            # SAFETY: Remove headers that interfere with requests auto-calculation
                            headers = {k: v for k, v in headers.items() if k.lower() not in ['content-length', 'host']} 

                            body = FlowExecutorService.replace_vars(api_call.get('body', ''), variables_dict)

                            # Identify existing Content-Type key (case-insensitive)
                            ct_key = next((k for k in headers.keys() if k.lower() == 'content-type'), None)
                            body_is_urlencoded = isinstance(body, str) and ('=' in body or '&' in body) and not body.strip().startswith('{')
                            
                            if ct_key:
                                val = headers[ct_key]
                                if 'multipart/form-data' in str(val).lower() and body_is_urlencoded:
                                    headers[ct_key] = 'application/x-www-form-urlencoded'
                                    logger.info(f"      [FIX] Forced Content-Type to x-www-form-urlencoded (was multipart)")
                            
                            if 'auth/login' in url and body_is_urlencoded:
                                if not ct_key:
                                    headers['Content-Type'] = 'application/x-www-form-urlencoded'
                                elif 'json' in headers[ct_key]:
                                     headers[ct_key] = 'application/x-www-form-urlencoded'

                            start_time = time.time()
                            try:
                                logger.info(f"      [DEBUG] Requesting: {method} {url}")
                                resp = flow_session.request(method, url, headers=headers, data=body)
                                duration = int((time.time() - start_time) * 1000)
                                resp_status = resp.status_code
                                resp_reason = resp.reason
                                resp_headers = dict(resp.headers)
                                resp_text = resp.text
                                
                                try:
                                    resp_json = resp.json()
                                except ValueError:
                                    resp_json = None

                                # --- Assertion Evaluation Logic ---
                                assertions_raw = api_call.get('assertions', [])
                                if assertions_raw:
                                    unique_assertions = []
                                    seen_assertions = set()
                                    
                                    # 1. Deduplication
                                    for assertion in assertions_raw:
                                        src = assertion.get('source') or assertion.get('type') or 'statusCode'
                                        prop = assertion.get('property') or assertion.get('path') or ''
                                        op = assertion.get('operator') or 'equals'
                                        target = assertion.get('value')
                                        if target is None: target = assertion.get('target') or ''
                                        
                                        # Strict Single Contract Check
                                        if src == 'contract':
                                            key = 'contract_unique_key'
                                        else:
                                            key = f"{src}|{prop}|{op}|{target}"
                                            
                                        if key not in seen_assertions:
                                            seen_assertions.add(key)
                                            unique_assertions.append(assertion)

                                    for assertion in unique_assertions:
                                        try:
                                            # Normalize Source
                                            src = assertion.get('source') or assertion.get('type') or 'statusCode'
                                            if src == 'status': src = 'statusCode'
                                            
                                            raw_operator = assertion.get('operator', 'equals')
                                            target = assertion.get('value')
                                            if target is None: target = assertion.get('target')
                                            prop = assertion.get('property') or assertion.get('path')
                                            op = str(raw_operator).lower()
                                            
                                            # Operator mapping...
                                            if op in ['equals', '==', 'eq']: op = 'equals'
                                            elif op in ['notequals', '!=', 'notesquals', 'neq', 'not_equals']: op = 'notequals'
                                            elif op in ['contains', 'in']: op = 'contains'
                                            elif op in ['notcontains', 'not_contains']: op = 'notcontains'
                                            elif op in ['greaterthan', 'gt', '>']: op = 'gt'
                                            elif op in ['lessthan', 'lt', '<']: op = 'lt'
                                            elif op in ['exists']: op = 'exists'
                                            elif op in ['notexists', 'not_exists']: op = 'notexists'
                                            elif op in ['json_schema', 'matches_schema']: op = 'json_schema' # Contract

                                            actual_val = None
                                            is_success = False
                                            
                                            # --- CONTRACT VALIDATION ---
                                            if src == 'contract':
                                                if op == 'json_schema':
                                                    try:
                                                        schema = json.loads(target) if isinstance(target, str) else target
                                                        data_to_validate = resp_json
                                                        validate(instance=data_to_validate, schema=schema)
                                                        is_success = True
                                                        actual_val = "Schema Match"
                                                        target = "Valid Contract" # UX
                                                    except ValidationError as ve:
                                                        is_success = False
                                                        actual_val = f"Validation Error: {ve.message}"
                                                        target = "Valid Contract"
                                                    except Exception as schema_err:
                                                        logger.error(f"      ❌ Contract Schema Error: {schema_err}")
                                                        is_success = False
                                                        actual_val = f"Schema Error: {str(schema_err)}"
                                                        target = "Valid Contract"
                                                else:
                                                     is_success = False
                                                     actual_val = "Unknown Operator"

                                            elif src == 'statusCode':
                                                actual_val = resp_status
                                                if op == 'exists': is_success = True
                                                elif op == 'notexists': is_success = False
                                                else:
                                                    try:
                                                        target_int = int(str(target).strip())
                                                        if op == 'equals': is_success = (actual_val == target_int)
                                                        elif op == 'notequals': is_success = (actual_val != target_int)
                                                        elif op == 'gt': is_success = (actual_val > target_int)
                                                        elif op == 'lt': is_success = (actual_val < target_int)
                                                        else: is_success = (actual_val == target_int)
                                                    except ValueError: is_success = False
                                            
                                            elif src == 'responseTime':
                                                actual_val = duration
                                                if op == 'exists': is_success = True
                                                elif op == 'notexists': is_success = False
                                                else:
                                                    try:
                                                        target_int = int(str(target).strip())
                                                        if op == 'lt': is_success = (actual_val < target_int)
                                                        elif op == 'gt': is_success = (actual_val > target_int)
                                                        elif op == 'equals': is_success = (actual_val == target_int)
                                                        else: is_success = (actual_val < target_int)
                                                    except ValueError: is_success = False

                                            elif src == 'header':
                                                # Case Insensitive Lookup
                                                found_key = next((k for k in resp_headers.keys() if k.lower() == str(prop).lower()), None)
                                                actual_val = resp_headers[found_key] if found_key else None
                                                if op == 'exists': is_success = (actual_val is not None)
                                                elif op == 'notexists': is_success = (actual_val is None)
                                                else:
                                                    val_to_compare = str(actual_val) if actual_val is not None else ""
                                                    if op == 'equals': is_success = (val_to_compare == str(target))
                                                    elif op == 'contains': is_success = (str(target) in val_to_compare)
                                                    elif op == 'notequals': is_success = (val_to_compare != str(target))
                                                    elif op == 'notcontains': is_success = (str(target) not in val_to_compare)
                                                    else: is_success = (val_to_compare == str(target))

                                            elif src == 'body':
                                                if resp_json:
                                                    assertion_path_parts = str(prop).split('.')
                                                    current = resp_json
                                                    for part in assertion_path_parts:
                                                        if isinstance(current, dict) and part in current: current = current[part]
                                                        elif isinstance(current, list):
                                                            try:
                                                                idx = int(part)
                                                                if 0 <= idx < len(current): current = current[idx]
                                                                else: current = None; break
                                                            except ValueError: current = None; break
                                                        else: current = None; break
                                                    actual_val = current
                                                else: actual_val = None

                                                if op == 'exists': is_success = (actual_val is not None)
                                                elif op == 'notexists': is_success = (actual_val is None)
                                                else:
                                                    str_actual = str(actual_val) if actual_val is not None else ""
                                                    str_target = str(target)
                                                    if op == 'equals': is_success = (str_actual == str_target)
                                                    elif op == 'contains': is_success = (str_target in str_actual)
                                                    elif op == 'notequals': is_success = (str_actual != str_target)
                                                    elif op == 'notcontains': is_success = (str_target not in str_actual)
                                                    else: is_success = (str_actual == str_target)

                                            assertion_results.append({
                                                "source": src, "operator": raw_operator, "target": str(target) if target is not None else "",
                                                "actual": str(actual_val) if actual_val is not None else "None", "success": is_success
                                            })
                                            if not is_success: assertions_passed = False
                                        except Exception as ass_err:
                                            logger.error(f"Assertion Error: {ass_err}")
                                            assertions_passed = False

                                # --- Automatic Variable Extraction ---
                                extracts = api_call.get('extracts', [])
                                if extracts and resp_json:
                                    for rule in extracts:
                                        try:
                                            r_source = rule.get('source')
                                            r_property = rule.get('property', '')
                                            r_variable = rule.get('variable', '')
                                            value = None
                                            if r_source == 'header':
                                                value = next((v for k, v in resp_headers.items() if k.lower() == str(r_property).lower()), None)
                                            elif r_source == 'body':
                                                extract_path_parts = str(r_property).split('.')
                                                current = resp_json
                                                for part in extract_path_parts:
                                                    if isinstance(current, dict) and part in current: current = current[part]
                                                    elif isinstance(current, list):
                                                        try:
                                                            idx = int(part)
                                                            if 0 <= idx < len(current): current = current[idx]
                                                            else: current = None; break
                                                        except ValueError: current = None; break
                                                    else: current = None; break
                                                value = current
                                            
                                            if value is not None:
                                                var_name = str(r_variable).strip().upper()
                                                if var_name:
                                                    variables_dict[var_name] = str(value)
                                                    try:
                                                        new_var = VariableCreate(name=var_name, value=str(value), project_id=product_id, environment_id=env_id, type="extracted")
                                                        VariableService.create(db, new_var)
                                                        logger.info(f"      ✅ Extracted & Saved Variable [{var_name}] = '{value}'")
                                                    except: pass
                                        except Exception as e:
                                            logger.error(f"      ❌ Extraction Error: {e}")

                            except Exception as req_ex:
                                logger.error(f"Request failed: {req_ex}")
                                resp_status = 500
                                resp_reason = "Request Exception"
                                resp_text = str(req_ex)
                                final_error_message = str(req_ex)

                        # Determine Final Status for history display
                        if not final_error_message:
                            if api_call.get('assertions'):
                                if not assertions_passed:
                                    final_error_message = "Assertions Failed"
                                    flow_failed = True
                            else:
                                if resp_status >= 400:
                                    final_error_message = f"HTTP Error {resp_status}"
                                    flow_failed = True

                        if final_error_message: flow_fail_count += 1
                        else: flow_success_count += 1

                        # Sanitize ID
                        raw_api_id = api_call.get('id')
                        api_id_clean = int(raw_api_id) if str(raw_api_id).isdigit() else None

                        # Prepare History Object (ALWAYS save)
                        hist = ExecutionHistoryCreate(
                            api_id=api_id_clean,
                            api_name=api_call.get('name') or "Step",
                            project_id=product_id,
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
                            status_text=resp_reason,
                            response_headers=resp_headers,
                            response_body=resp_text,
                            response_time=duration,
                            environment_id=env_id,
                            environment_name=str(env_id),
                            variables_used={},
                            assertions=assertion_results,
                            error_message=final_error_message
                        )
                        history_buffer.append(hist)
                    
                    except Exception as loop_ex:
                        logger.error(f"Critical error in execution loop: {loop_ex}")

                # Add neighbors to queue
                neighbors = adj.get(current_id, [])
                neighbors.sort(key=get_x_pos)
                for neighbor in neighbors:
                        in_degree[neighbor] -= 1
                        if in_degree[neighbor] == 0:
                            queue.append(neighbor)
            
            # BATCH SAVE ALL HISTORY
            if history_buffer:
                logger.info(f"💾 Bulk Saving {len(history_buffer)} execution records...")
                HistoryService.save_batch(db, history_buffer, user_id)

            return flow_success_count, flow_fail_count
        except Exception as e:
            logger.error(f"Error executing flow {flow_meta['id']}: {e}")
            db.rollback()
            return 0, 0
        except Exception as e:
            logger.error(f"Error executing flow {flow_meta['id']}: {e}")
            db.rollback()
            return 0, 0

    @staticmethod
    def get_merged_variables(db: Session, product_id: int, env_id: int):
        from app.services.variable_service import VariableService
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

        variables = {v.name: v for v in global_vars}
        for v in env_vars:
            variables[v.name] = v
            
        logger.info(f"    ✅ Total Merged Variables: {len(variables)} keys: {list(variables.keys())}")
        return variables

    @staticmethod
    def execute_feature_group(db: Session, feature_id: int, env_id: int, company_id: int, schedule_id: int = None, user_id: int = 1):
        """
        Executes all flows within a specific feature.
        """
        from app.services.feature_service import FeatureService
        from app.services.flow_service import FlowService
        
        feature = FeatureService.get_by_id(db, feature_id, company_id)
        if not feature:
            logger.error(f"Feature {feature_id} not found")
            return 0, 0

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
            latest_flow = flows[0]
            if len(flows) > 1:
                logger.info(f"ℹ️  Selecting latest flow '{latest_flow.get('name')}' (ID: {latest_flow.get('id')}) from {len(flows)} detected flows.")
            
            s, f = FlowExecutorService.execute_flow_logic(db, latest_flow, feature.product_id, env_id, company_id, variables, feature_name=feature.name, schedule_id=schedule_id, user_id=user_id)
            success_count += s
            fail_count += f
        else:
            logger.warning(f"No flows found for feature {feature.id}")
        
        return success_count, fail_count

    @staticmethod
    def execute_flow_by_id(db: Session, flow_id: int, env_id: int, company_id: int, schedule_id: int = None, user_id: int = 1):
        """
        Executes a single specific flow.
        """
        from app.services.flow_service import FlowService
        from app.models.feature_models import FeatureModel
        
        # 1. Load Flow Metadata
        # We need project_id first to call load, but FlowService.load tries to look it up if flow_id is passed?
        # Actually FlowService.load signature is (db, project_id, company_id, flow_id). 
        # It requires project_id to verify ownership.
        # So we need to fetch the flow DB object first to get the project_id.
        from app.models.flow_models import FlowDB
        flow = db.query(FlowDB).filter(FlowDB.id == flow_id).first()
        if not flow:
            logger.error(f"Flow {flow_id} not found")
            return 0, 0
            
        # 2. Load Full Flow Data
        flow_meta = FlowService.load(db, flow.project_id, company_id, flow_id)
        if not flow_meta or not flow_meta.get('id'):
             logger.error(f"Could not load flow data for {flow_id}")
             return 0, 0

        # 3. Get Project/Feature info for Variables
        # flow_meta has 'product_id' which FlowService.load populates.
        product_id = flow_meta.get('product_id')
        feature_name = "Unknown Feature"
        
        feature = db.query(FeatureModel).filter(FeatureModel.id == flow.project_id).first()
        if feature:
            feature_name = feature.name
            if not product_id: product_id = feature.product_id

        if not product_id:
            logger.warning(f"Product ID not found for flow {flow_id}, using 0 for variables lookup")
            product_id = 0

        logger.info(f"🚀 Executing Single Flow: {flow_meta['name']} (ID: {flow.id}) in Feature {feature_name}")

        # 4. Prepare Variables
        variables = FlowExecutorService.get_merged_variables(db, product_id, env_id)

        # 5. Execute
        return FlowExecutorService.execute_flow_logic(db, flow_meta, product_id, env_id, company_id, variables, feature_name=feature_name, schedule_id=schedule_id, user_id=user_id)

    @staticmethod
    def execute_suite(db: Session, product_id: int, env_id: int, company_id: int, schedule_id: int = None, user_id: int = 1, max_concurrency: int = None):
        """
        Executes all features within a product.
        """
        from app.models.feature_models import FeatureModel
        from app.services.flow_service import FlowService

        feature_objs = db.query(FeatureModel).filter(FeatureModel.product_id == product_id).all()
        features = [{'id': f.id, 'name': f.name, 'product_id': f.product_id} for f in feature_objs]
        
        if not features:
            logger.warning(f"No features found for product {product_id}")
            return 0, 0

        if not features:
            logger.warning(f"No features found for product {product_id}")
            return 0, 0

        # RAW VARIABLES (Objects)
        raw_variables = FlowExecutorService.get_merged_variables(db, product_id, env_id)
        
        # KEY FIX: Serialize to plain dict {name: value} to avoid SQLAlchemy Session threading issues
        # and ensure each thread gets a clean, independent snapshot.
        variables = {}
        for k, v in raw_variables.items():
            if hasattr(v, 'value'):
               variables[k] = str(v.value)
            else:
               variables[k] = str(v)
        
        total_success = 0
        total_fail = 0

        logger.info(f"🚀 Executing Suite for Product {product_id} - {len(features)} Features")

        import concurrent.futures

        # Worker Function for Parallel Execution
        def process_feature(feature):
            f_success = 0
            f_fail = 0
            try:
                # Create a NEW DB Session for this thread/feature to avoid sharing session across threads
                # Session is not thread-safe!
                from app.database import SessionLocal
                thread_db = SessionLocal()
                
                try:
                    logger.info(f"  📂 Processing Feature: {feature['name']} (ID: {feature['id']}) [Thread]")
                    flows = FlowService.list_by_project(thread_db, feature['id'], company_id)
                    
                    if flows:
                        # FIX: Only execute latest flow per feature
                        latest_flow = flows[0]
                         # Re-fetch merge variables inside thread or pass them? 
                         # Variables are dict, safe to read.
                        s, f = FlowExecutorService.execute_flow_logic(
                            thread_db, latest_flow, product_id, env_id, company_id, 
                            variables.copy(), # Copy vars to avoid contamination
                            feature_name=feature['name'], schedule_id=schedule_id, user_id=user_id
                        )
                        f_success = s
                        f_fail = f
                finally:
                    thread_db.close()
            except Exception as feat_ex:
                logger.error(f"Failed to process feature {feature['id']}: {feat_ex}")
                f_fail = 1
            return f_success, f_fail

        # Run Features in Parallel
        # Adjust max_workers as needed via env var MAX_CONCURRENT_FEATURES (default 5) or override
        from app.config import settings
        max_workers = max_concurrency if max_concurrency else settings.MAX_CONCURRENT_FEATURES
        
        logger.info(f"🚀 Starting parallel execution with {max_workers} workers")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            full_results = list(executor.map(process_feature, features))

        # Aggregate Results
        for s, f in full_results:
            total_success += s
            total_fail += f
        
        return total_success, total_fail
