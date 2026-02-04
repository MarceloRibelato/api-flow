import logging
import time
import json
import re
from datetime import datetime
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

                            # FIX: If Content-Type is multipart (captured from browser) but body is URL-encoded (converted by us), force proper header
                            # Otherwise server expects boundary and fails with 400
                            # FIX: Content-Type handling
                            # 1. Identify existing Content-Type key (case-insensitive)
                            ct_key = next((k for k in headers.keys() if k.lower() == 'content-type'), None)
                            
                            # 2. Check body signature
                            body_is_urlencoded = isinstance(body, str) and ('=' in body or '&' in body) and not body.strip().startswith('{')
                            
                            if ct_key:
                                val = headers[ct_key]
                                # If it's multipart (from browser capture) but we have a string body, it's a mismatch -> Fix it
                                if 'multipart/form-data' in str(val).lower():
                                    if body_is_urlencoded:
                                        headers[ct_key] = 'application/x-www-form-urlencoded'
                                        logger.info(f"      [FIX] Forced Content-Type to x-www-form-urlencoded (was multipart)")
                                    else:
                                        # If it's not obviously urlencoded, maybe it is raw data? 
                                        # But browser capture of multipart usually implies we couldn't capture the boundary, so better to default to json or urlencoded?
                                        # For now, trust the mismatch fix only if body looks like k=v
                                        pass
                            
                            # 3. Special Case: Auth/Login usually needs x-www-form-urlencoded
                            if 'auth/login' in url and body_is_urlencoded:
                                if not ct_key:
                                    headers['Content-Type'] = 'application/x-www-form-urlencoded'
                                elif 'json' in headers[ct_key]: # If accidentally captured as JSON header
                                     headers[ct_key] = 'application/x-www-form-urlencoded'


                            
                            start_time = time.time()
                            try:
                                # Use flow_session instead of new session
                                logger.info(f"      [DEBUG] Requesting: {method} {url}")
                                logger.info(f"      [DEBUG] Headers: {headers}")
                                logger.info(f"      [DEBUG] Body: {body}")
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
                            
                            # Sanitize ID (Frontend uses strings like 'api-0-1', DB expects Int or None)
                            raw_api_id = api_call.get('id')
                            api_id_clean = int(raw_api_id) if str(raw_api_id).isdigit() else None

                            # Save History
                            hist = ExecutionHistoryCreate(
                                api_id=api_id_clean,
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

    @staticmethod
    def get_merged_variables(db: Session, product_id: int, env_id: int):
        from app.services.variable_service import VariableService
        global_vars = VariableService.get_all(db, product_id, environment_id=None)
        env_vars = []
        if env_id:
            env_vars = VariableService.get_all(db, product_id, environment_id=env_id)
        
        variables = {v.name: v for v in global_vars}
        for v in env_vars:
            variables[v.name] = v
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
    def execute_suite(db: Session, product_id: int, env_id: int, company_id: int, schedule_id: int = None, user_id: int = 1):
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

        variables = FlowExecutorService.get_merged_variables(db, product_id, env_id)
        
        total_success = 0
        total_fail = 0

        logger.info(f"🚀 Executing Suite for Product {product_id} - {len(features)} Features")

        for feature in features:
            try:
                logger.info(f"  📂 Processing Feature: {feature['name']} (ID: {feature['id']})")
                flows = FlowService.list_by_project(db, feature['id'], company_id)
                
                if not flows:
                    continue

                if not flows:
                    continue

                # FIX: Only execute latest flow per feature
                latest_flow = flows[0]
                s, f = FlowExecutorService.execute_flow_logic(db, latest_flow, product_id, env_id, company_id, variables, feature_name=feature['name'], schedule_id=schedule_id, user_id=user_id)
                total_success += s
                total_fail += f
            except Exception as feat_ex:
                logger.error(f"Failed to process feature {feature['id']}: {feat_ex}")
                db.rollback()
                total_fail += 1
        
        return total_success, total_fail
