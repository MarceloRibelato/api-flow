from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.orm import Session
from datetime import datetime
import logging

from app.models.schedule_models import ScheduleModel
from app.database import SessionLocal
# Import services to execute logic - avoiding circular imports might be tricky
# ideally we move execution logic to a common place or import inside function

scheduler = BackgroundScheduler()

logger = logging.getLogger(__name__)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def execute_job(schedule_id: int):
    """
    Callback function executed by the scheduler.
    Fetches the schedule details and triggers the actual test execution.
    """
    logger.info(f"Executing scheduled job: {schedule_id}")
    db = SessionLocal()
    try:
        schedule = db.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
        if not schedule:
            logger.error(f"Schedule {schedule_id} not found during execution")
            return

        schedule.last_run = datetime.utcnow()
        if scheduler.get_job(str(schedule_id)):
             schedule.next_run = scheduler.get_job(str(schedule_id)).next_run_time
        db.commit()

        # Import services locally
        from app.services.feature_service import FeatureService
        from app.services.flow_service import FlowService
        from app.services.history_service import HistoryService
        from app.services.variable_service import VariableService
        from app.schemas.history_schemas import ExecutionHistoryCreate
        import requests
        import time
        import json

        # OPTIMIZATION: Use Session for connection pooling (Keep-Alive)
        # This significantly reduces latency by handling TCP/SSL once.
        session = requests.Session()
        # CRITICAL FIX: Disable proxy detection (can cause 1-2s delay on Windows)
        session.trust_env = False

        # Helper method for replacing variables - simplified version
        import re
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
                        return str(v.value)
                
                # 3. Not Found - Return original placeholder
                return match.group(0)

            return re.sub(r'\{\{([\w\.\-_]+)\}\}', replacer, text)

        # Helper to execute a single flow
        def execute_flow_logic(flow_meta, product_id, env_id, company_id, variables_dict, feature_name: str = None):
            try:
                # Load full flow data with cards
                flow_data = FlowService.load(db, flow_meta['project_id'], company_id, flow_meta['id'])
                cards = flow_data.get('cardData', {})
                
                logger.info(f"    ▶ Executing Flow: {flow_meta.get('name')} (ID: {flow_meta['id']}) - Cards: {len(cards)}")

                if not cards:
                    logger.warning(f"     ⚠ No cards found in flow {flow_meta['id']}")
                    return 0, 0
                
                # Sort cards by execution order (BFS/graph traversal or simple list?)
                # Current implementation iterates card items? No, flow_data['cardData'] is dict.
                # We need to traverse nodes.
                # Simplified: Iterate nodes in order?
                # The original code iterated `cards.items()` which is random order!
                # We need topological sort or just follow edges.
                # For now, let's assuming existing logic (which was navigating nodes?).
                # Wait, review original code execution logic!
                
                # ORIGINAL LOGIC WAS MISSING IN VIEW! I assumed it iterates correctly.
                # Let's check how it iterates.
                nodes = flow_data.get('nodes', [])
                edges = flow_data.get('edges', [])
                
                logger.info(f"   ► Executing Flow: {flow_meta.get('name', 'Unknown')} (ID: {flow_meta['id']}) - {len(cards)} cards")

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

                visited = set()
                
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
                            try:
                                # Prepare Request
                                method = api_call.get('method', 'GET')
                                url = replace_vars(api_call.get('url', ''), variables_dict)
                                
                                # FORCE FIX: Replace localhost with 127.0.0.1
                                if 'localhost' in url:
                                    url = url.replace('localhost', '127.0.0.1')
                                
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
                                headers = json.loads(replace_vars(headers_json_str, variables_dict))
                                
                                if isinstance(headers, list): headers = {}
                                if not isinstance(headers, dict) and headers is not None: headers = {}

                                body = replace_vars(api_call.get('body', ''), variables_dict)
                                
                                start_time = time.time()
                                try:
                                    # Use flow_session instead of new session
                                    resp = flow_session.request(method, url, headers=headers, data=body)
                                except Exception as req_ex:
                                    logger.error(f"Request failed: {req_ex}")
                                    # Don't break flow logic, but log error
                                    raise req_ex

                                duration = int((time.time() - start_time) * 1000)

                                try:
                                    resp_json = resp.json()
                                except ValueError:
                                    resp_json = None

                                # --- Assertion Evaluation Logic ---
                                assertions = api_call.get('assertions', [])
                                assertion_results = []
                                assertions_passed = True
                                
                                if assertions:
                                    logger.info(f"      Evaluating {len(assertions)} assertions...")
                                    for assertion in assertions:
                                        try:
                                            # Normalize assertion structure
                                            src = assertion.get('type') or assertion.get('source') or 'statusCode'
                                            raw_operator = assertion.get('operator', 'equals')
                                            target = assertion.get('value')
                                            if target is None: target = assertion.get('target')
                                            
                                            prop = assertion.get('property') or assertion.get('path')

                                            # Normalize Operator
                                            op = str(raw_operator).lower()
                                            # Map to standard set
                                            if op in ['equals', '==', 'eq']: op = 'equals'
                                            elif op in ['notequals', '!=', 'notesquals', 'neq', 'not_equals']: op = 'notequals'
                                            elif op in ['contains', 'in']: op = 'contains'
                                            elif op in ['notcontains', 'not_contains']: op = 'notcontains'
                                            elif op in ['greaterthan', 'gt', '>']: op = 'gt'
                                            elif op in ['lessthan', 'lt', '<']: op = 'lt'
                                            elif op in ['exists']: op = 'exists'
                                            elif op in ['notexists', 'not_exists']: op = 'notexists'

                                            actual_val = None
                                            is_success = False
                                            
                                            # --- Status Code ---
                                            if src == 'statusCode':
                                                actual_val = resp.status_code
                                                if op == 'exists':
                                                    is_success = True
                                                elif op == 'notexists':
                                                    is_success = False
                                                else:
                                                    try:
                                                        target_int = int(str(target).strip())
                                                        if op == 'equals': is_success = (actual_val == target_int)
                                                        elif op == 'notequals': is_success = (actual_val != target_int)
                                                        elif op == 'gt': is_success = (actual_val > target_int)
                                                        elif op == 'lt': is_success = (actual_val < target_int)
                                                        else: is_success = (actual_val == target_int)
                                                    except ValueError:
                                                        is_success = False
                                            
                                            # --- Response Time ---
                                            elif src == 'responseTime':
                                                actual_val = duration
                                                if op == 'exists':
                                                    is_success = True
                                                elif op == 'notexists':
                                                    is_success = False
                                                else:
                                                    try:
                                                        target_int = int(str(target).strip())
                                                        if op == 'lt': is_success = (actual_val < target_int)
                                                        elif op == 'gt': is_success = (actual_val > target_int)
                                                        elif op == 'equals': is_success = (actual_val == target_int) # Less common but possible
                                                        else: is_success = (actual_val < target_int) # Default fallback
                                                    except ValueError:
                                                        is_success = False

                                            # --- Header ---
                                            elif src == 'header':
                                                found_key = next((k for k in resp.headers.keys() if k.lower() == str(prop).lower()), None)
                                                actual_val = resp.headers[found_key] if found_key else None
                                                
                                                if op == 'exists': 
                                                    is_success = (actual_val is not None)
                                                elif op == 'notexists': 
                                                    is_success = (actual_val is None)
                                                else:
                                                    val_to_compare = str(actual_val) if actual_val is not None else ""
                                                    if op == 'equals': is_success = (val_to_compare == str(target))
                                                    elif op == 'contains': is_success = (str(target) in val_to_compare)
                                                    elif op == 'notequals': is_success = (val_to_compare != str(target))
                                                    elif op == 'notcontains': is_success = (str(target) not in val_to_compare)
                                                    else: is_success = (val_to_compare == str(target))

                                            # --- Body ---
                                            elif src == 'body':
                                                if resp_json:
                                                    parts = str(prop).split('.')
                                                    current = resp_json
                                                    for part in parts:
                                                        if isinstance(current, dict) and part in current:
                                                            current = current[part]
                                                        else:
                                                            current = None
                                                            break
                                                    actual_val = current
                                                else:
                                                    actual_val = None

                                                if op == 'exists':
                                                    is_success = (actual_val is not None)
                                                elif op == 'notexists':
                                                    is_success = (actual_val is None)
                                                else:
                                                    str_actual = str(actual_val) if actual_val is not None else ""
                                                    str_target = str(target)
                                                    
                                                    if op == 'equals': is_success = (str_actual == str_target)
                                                    elif op == 'contains': is_success = (str_target in str_actual)
                                                    elif op == 'notequals': is_success = (str_actual != str_target)
                                                    elif op == 'notcontains': is_success = (str_target not in str_actual)
                                                    else: is_success = (str_actual == str_target)

                                            assertion_results.append({
                                                "source": src,
                                                "operator": raw_operator,
                                                "target": str(target) if target is not None else "",
                                                "actual": str(actual_val) if actual_val is not None else "None",
                                                "success": is_success
                                            })
                                            
                                            if not is_success:
                                                assertions_passed = False

                                        except Exception as e:
                                            logger.error(f"Assertion Error: {e}")
                                            assertion_results.append({
                                                "source": src,
                                                "operator": raw_operator,
                                                "target": str(target),
                                                "actual": "Error",
                                                "success": False,
                                                "error_message": str(e)
                                            })
                                            assertions_passed = False

                                # Determine Final Status
                                # If assertions exist, they determine success.
                                # If NO assertions, fallback to status code check.
                                final_error_message = None
                                if assertions:
                                    if not assertions_passed:
                                        final_error_message = "Assertions Failed"
                                        flow_failed = True
                                else:
                                    if resp.status_code >= 400:
                                        final_error_message = f"HTTP Error {resp.status_code}"
                                        flow_failed = True

                                if final_error_message:
                                    flow_fail_count += 1
                                else:
                                    flow_success_count += 1

                                # -------------------------------------

                                # --- Automatic Variable Extraction ---
                                extracts = api_call.get('extracts', [])
                                if extracts:
                                    logger.info(f"      Processing {len(extracts)} extraction rules...")
                                    for rule in extracts:
                                        try:
                                            # Normalize rule structure (it might be dict or object depending on serialization)
                                            r_source = rule.get('source') if isinstance(rule, dict) else getattr(rule, 'source', None)
                                            r_property = rule.get('property') if isinstance(rule, dict) else getattr(rule, 'property', '')
                                            r_variable = rule.get('variable') if isinstance(rule, dict) else getattr(rule, 'variable', '')

                                            value = None
                                            if r_source == 'header':
                                                # Header extraction (case-insensitive)
                                                value = next((v for k, v in resp.headers.items() if k.lower() == str(r_property).lower()), None)
                                            elif r_source == 'body' and resp_json:
                                                # Body extraction (simple dot notation)
                                                parts = str(r_property).split('.')
                                                current = resp_json
                                                for part in parts:
                                                    if isinstance(current, dict) and part in current:
                                                        current = current[part]
                                                    else:
                                                        current = None
                                                        break
                                                value = current
                                            
                                            if value is not None:
                                                var_name = str(r_variable).strip().upper()
                                                if var_name:
                                                    variables_dict[var_name] = str(value)   # Update persistence dict
                                                    logger.info(f"      ✅ Extracted: {var_name} = {value}")

                                        except Exception as e:
                                            logger.error(f"      ❌ Extraction Error for {rule}: {str(e)}")
                                # -------------------------------------
                                
                                # Save History
                                hist = ExecutionHistoryCreate(
                                    api_id=api_call.get('id'),
                                    api_name=api_call.get('name'),
                                    project_id=product_id,
                                    flow_id=flow_meta['id'],
                                    schedule_id=schedule.id,  # Link to Schedule
                                    feature_name=feature_name, # Save grouped feature name
                                    node_name=card.get('name'),
                                    method=method,
                                    url=url,
                                    request_headers=headers,
                                    request_body=body,
                                    status_code=resp.status_code,
                                    status_text=resp.reason,
                                    response_headers=dict(resp.headers),
                                    response_body=resp.text,
                                    response_time=duration,
                                    environment_id=env_id,
                                    environment_name=str(env_id),
                                    variables_used={},
                                    assertions=assertion_results, # Save Assertions
                                    error_message=final_error_message # Explicit Success/Fail Status
                                )
                                user_id_to_save = schedule.user_id if schedule.user_id else 1
                                logger.info(f"💾 Saving history for Schedule {schedule.id}, User {user_id_to_save}")
                                HistoryService.save(db, hist, user_id=user_id_to_save)
                                logger.info(f"      -> {method} {url} [{resp.status_code}]")
                            
                            except Exception as ex:
                                logger.error(f"Failed to execute API call in card {card.get('name')}: {ex}")

                    # Add neighbors to queue
                    # Sort neighbors by X position for consistent flow
                    neighbors = adj.get(current_id, [])
                    neighbors.sort(key=get_x_pos)
                    
                    for neighbor in neighbors:
                         in_degree[neighbor] -= 1
                         if in_degree[neighbor] == 0:
                             queue.append(neighbor)

                return flow_success_count, flow_fail_count
            except Exception as e:
                logger.error(f"Error executing flow {flow_meta['id']}: {e}")
                db.rollback() # Ensure session is clean for next flow/feature
                return 0, 0

        # Helper to prepare variables
        def get_merged_variables(product_id, env_id):
            global_vars = VariableService.get_all(db, product_id, environment_id=None)
            env_vars = []
            if env_id:
                env_vars = VariableService.get_all(db, product_id, environment_id=env_id)
            
            variables = {v.name: v for v in global_vars}
            for v in env_vars:
                variables[v.name] = v
            return variables

        # --- EXECUTION LOGIC ---
        success_count = 0
        fail_count = 0

        if schedule.type == 'feature':
            feature_id = schedule.target_id
            env_id = schedule.environment_id
            logger.info(f"🚀 Running Scheduled Feature {feature_id} in Env {env_id}")

            feature = FeatureService.get_by_id(db, feature_id, schedule.company_id)
            if not feature:
                logger.error(f"Feature {feature_id} not found")
                return

            # Fix: list_by_project uses project_id which corresponds to Feature ID in FlowDB
            flows = FlowService.list_by_project(db, feature.id, schedule.company_id)
            
            # Prepare variables
            variables = get_merged_variables(feature.product_id, env_id)

            # Prepare variables
            variables = get_merged_variables(feature.product_id, env_id)

            for flow_meta in flows:
                s, f = execute_flow_logic(flow_meta, feature.product_id, env_id, schedule.company_id, variables, feature_name=feature.name)
                success_count += s
                fail_count += f
            
            # Determine overall status
            if fail_count > 0:
                schedule.last_run_status = 'failure'
            else:
                schedule.last_run_status = 'success'
            
            logger.info(f"Feature execution completed. Success: {success_count}, Fail: {fail_count}. Status: {schedule.last_run_status}")
            db.commit()

        elif schedule.type == 'suite':
            product_id = schedule.target_id
            env_id = schedule.environment_id
            logger.info(f"🚀 Running Scheduled Suite (Product) {product_id} in Env {env_id}")
            
            # 1. Fetch all features for the product
            # Needs to import FeatureModel here or use Service if available
            from app.models.feature_models import FeatureModel
            feature_objs = db.query(FeatureModel).filter(FeatureModel.product_id == product_id).all()
            
            # Detach data to avoid session issues during loop commits/rollbacks
            features = [{'id': f.id, 'name': f.name, 'product_id': f.product_id} for f in feature_objs]
            
            if not features:
                logger.warning(f"No features found for product {product_id}")
                return
            
            logger.info(f"Found {len(features)} features for product {product_id}")

            # 2. Prepare variables once for the product
            variables = get_merged_variables(product_id, env_id)

            # 3. Iterate features and execute their flows

            for feature in features:
                try:
                    logger.info(f"  📂 Processing Feature: {feature['name']} (ID: {feature['id']})")
                    flows = FlowService.list_by_project(db, feature['id'], schedule.company_id)
                    
                    if not flows:
                        logger.info(f"     (No flows found)")
                        continue

                    for flow_meta in flows:
                        s, f = execute_flow_logic(flow_meta, product_id, env_id, schedule.company_id, variables, feature_name=feature['name'])
                        success_count += s
                        fail_count += f
                
                except Exception as feat_ex:
                    logger.error(f"Failed to process feature {feature['id']}: {feat_ex}")
                    db.rollback() # Ensure session is clean for next feature
                    # Optionally count as failure or just log
                    fail_count += 1 # Assume failure if feature crashes
            
            # Update Schedule Status (Last Run Status)
            if fail_count > 0:
                schedule.last_run_status = 'failure'
            else:
                schedule.last_run_status = 'success'
            
            logger.info(f"Suite execution completed. Success: {success_count}, Fail: {fail_count}. Status: {schedule.last_run_status}")
            db.commit()

        # --- 4. Send Webhook Notification ---
        # Logic: Use Schedule URL > Fallback to Product URL
        
        target_urls = schedule.notification_urls
        
        if not target_urls:
            # Fallback logic based on type
            try:
                from app.models.product_models import ProductModel
                from app.models.feature_models import FeatureModel
                
                product_id_for_url = None
                
                if schedule.type == 'suite':
                    product_id_for_url = schedule.target_id
                elif schedule.type == 'feature':
                    # Need to get product_id from feature
                    feat = db.query(FeatureModel).filter(FeatureModel.id == schedule.target_id).first()
                    if feat:
                        product_id_for_url = feat.product_id
                
                if product_id_for_url:
                    prod = db.query(ProductModel).filter(ProductModel.id == product_id_for_url).first()
                    if prod and prod.notification_urls:
                        target_urls = prod.notification_urls
                        logger.info(f"Using Product-level webhook for Schedule {schedule.id}")

            except Exception as e:
                logger.error(f"Error fetching product webhook: {e}")

        if target_urls:
            # CHECK NOTIFICATION TOGGLE
            if hasattr(schedule, 'notifications_enabled') and schedule.notifications_enabled is False:
                 logger.info(f"Skipping webhook notification for Schedule {schedule.id} (notifications disabled)")
                 # We still want to log or do other things? Probably just skip.
            else:
                logger.info(f"Target URLs for webhook: {target_urls}")
                try:
                    # Helper function defined inline or use a service method if reusable
                    # Using simple requests here for immediate execution
                    urls = [u.strip() for u in target_urls.split(',') if u.strip()]
                    logger.info(f"Parsed URLs: {urls}")
                    
                    payload = {
                        "schedule_id": schedule.id,
                        "schedule_name": schedule.name,
                        "type": schedule.type,
                        "target_id": schedule.target_id,
                        "status": schedule.last_run_status,
                        "execution_time": schedule.last_run.isoformat() if schedule.last_run else datetime.utcnow().isoformat(),
                        "success_count": success_count,
                        "fail_count": fail_count
                    }
                
                    for url in urls:
                        logger.info(f"Sending webhook to: {url}")
                        
                        try:
                            final_payload = payload
                            headers = {'Content-Type': 'application/json'}

                            # --- SLACK FORMATTING ---
                            if 'hooks.slack.com' in url:
                                color = "#36a64f" if payload['status'] == 'success' else "#d72b3f"
                                final_payload = {
                                    "text": f"Execution Report: {payload['schedule_name']}",
                                    "attachments": [
                                        {
                                            "color": color,
                                            "fields": [
                                                {"title": "Status", "value": payload['status'].upper(), "short": True},
                                                {"title": "Success", "value": str(payload['success_count']), "short": True},
                                                {"title": "Failed", "value": str(payload['fail_count']), "short": True},
                                                {"title": "Target", "value": f"{payload['type'].title()} #{payload['target_id']}", "short": True}
                                            ],
                                            "footer": "API Flow Scheduler",
                                            "ts": int(time.time())
                                        }
                                    ]
                                }
                            
                            # --- TEAMS FORMATTING (Adaptive Card or MessageCard) ---
                            elif 'webhook.office.com' in url or 'outlook.office.com' in url:
                                theme_color = "00FF00" if payload['status'] == 'success' else "FF0000"
                                final_payload = {
                                    "@type": "MessageCard",
                                    "@context": "http://schema.org/extensions",
                                    "themeColor": theme_color,
                                    "summary": f"Execution: {payload['schedule_name']}",
                                    "sections": [{
                                        "activityTitle": f"📢 Execution Completed: {payload['schedule_name']}",
                                        "activitySubtitle": f"Status: {payload['status'].upper()}",
                                        "facts": [
                                        {"name": "Success", "value": str(payload['success_count'])},
                                        {"name": "Failed", "value": str(payload['fail_count'])},
                                        {"name": "Time", "value": payload['execution_time']}
                                    ],
                                    "markdown": True
                                }]
                            }

                            wh_resp = requests.post(url, json=final_payload, headers=headers, timeout=5)
                            logger.info(f"Webhook response: {wh_resp.status_code}")
                        except Exception as wh_err:
                            logger.error(f"Failed to send webhook to {url}: {wh_err}")

                except Exception as notify_err:
                    logger.error(f"Error processing webhooks: {notify_err}")

    except Exception as outer_e:
        logger.error(f"Critical error in execute_job {schedule_id}: {outer_e}")
        # Try to set status to failure if DB session is still viable
        try:
             schedule.last_run_status = 'failure'
             db.commit()
        except: pass
    finally:
        # Close the session to free resources
        if 'session' in locals():
            session.close()
        db.close()

class SchedulerService:
    def start(self):
        if not scheduler.running:
            scheduler.start()
            logger.info("APScheduler started")

    def add_job(self, schedule: ScheduleModel, db: Session):
        """
        Adds a job to the scheduler based on the ScheduleModel.
        """
        job_id = str(schedule.id)
        
        # Remove existing if any (to update)
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

        if schedule.status != 'active':
            return

        trigger = None
        if schedule.cron_expression:
            # Assuming cron string like "0 9 * * *" or 5 fields
            # Simplified: Use cron trigger. Ideally parse the string.
            trigger = CronTrigger.from_crontab(schedule.cron_expression)
        elif schedule.run_at:
            trigger = DateTrigger(run_date=schedule.run_at)
        
        if trigger:
            scheduler.add_job(
                execute_job,
                trigger=trigger,
                args=[schedule.id],
                id=job_id,
                replace_existing=True
            )
            
            # Update next_run immediately
            job = scheduler.get_job(job_id)
            if job:
                schedule.next_run = job.next_run_time
                db.commit()

    def remove_job(self, schedule_id: int):
        job_id = str(schedule_id)
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
            
    def sync_jobs(self, db: Session):
        """
        Syncs in-memory scheduler with database schedules (e.g. on startup)
        """
        schedules = db.query(ScheduleModel).filter(ScheduleModel.status == 'active').all()
        for s in schedules:
            self.add_job(s, db)

scheduler_service = SchedulerService()
