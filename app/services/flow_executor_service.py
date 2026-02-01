import logging
import time
import json
import re
import requests
from sqlalchemy.orm import Session
from app.services.flow_service import FlowService
from app.services.history_service import HistoryService
from app.schemas.history_schemas import ExecutionHistoryCreate

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
                    return str(v.value)
            
            # 3. Not Found - Return original placeholder
            return match.group(0)

        return re.sub(r'\{\{([\w\.\-_]+)\}\}', replacer, text)

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
                            url = FlowExecutorService.replace_vars(api_call.get('url', ''), variables_dict)
                            
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
                            headers = json.loads(FlowExecutorService.replace_vars(headers_json_str, variables_dict))
                            
                            if isinstance(headers, list): headers = {}
                            if not isinstance(headers, dict) and headers is not None: headers = {}

                            body = FlowExecutorService.replace_vars(api_call.get('body', ''), variables_dict)
                            
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
                                                    elif op == 'equals': is_success = (actual_val == target_int)
                                                    else: is_success = (actual_val < target_int)
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

                            # --- Automatic Variable Extraction ---
                            extracts = api_call.get('extracts', [])
                            if extracts:
                                for rule in extracts:
                                    try:
                                        r_source = rule.get('source') if isinstance(rule, dict) else getattr(rule, 'source', None)
                                        r_property = rule.get('property') if isinstance(rule, dict) else getattr(rule, 'property', '')
                                        r_variable = rule.get('variable') if isinstance(rule, dict) else getattr(rule, 'variable', '')

                                        value = None
                                        if r_source == 'header':
                                            value = next((v for k, v in resp.headers.items() if k.lower() == str(r_property).lower()), None)
                                        elif r_source == 'body' and resp_json:
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
                                                variables_dict[var_name] = str(value)

                                    except Exception as e:
                                        logger.error(f"      ❌ Extraction Error for {rule}: {str(e)}")
                            
                            # Save History
                            hist = ExecutionHistoryCreate(
                                api_id=api_call.get('id'),
                                api_name=api_call.get('name'),
                                project_id=product_id,
                                flow_id=flow_meta['id'],
                                schedule_id=schedule_id,
                                feature_name=feature_name,
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
                                assertions=assertion_results,
                                error_message=final_error_message
                            )
                            # BATCH COMMIT OPTIMIZATION (commit=False)
                            HistoryService.save(db, hist, user_id=user_id, commit=False)
                        
                        except Exception as ex:
                            logger.error(f"Failed to execute API call in card {card.get('name')}: {ex}")

                # Add neighbors to queue
                neighbors = adj.get(current_id, [])
                neighbors.sort(key=get_x_pos)
                for neighbor in neighbors:
                        in_degree[neighbor] -= 1
                        if in_degree[neighbor] == 0:
                            queue.append(neighbor)
            
            # FINAL COMMIT FOR THE FLOW
            db.commit()

            return flow_success_count, flow_fail_count
        except Exception as e:
            logger.error(f"Error executing flow {flow_meta['id']}: {e}")
            db.rollback()
            return 0, 0
