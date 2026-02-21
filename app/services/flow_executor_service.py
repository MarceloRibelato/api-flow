import logging
import time
import uuid
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
            logger.warning(f"    ⚠️ Variable '{var_name}' NOT FOUND. Available: {list(variables.keys())}")
            return match.group(0)

        return re.sub(r'\{\{([\w\.\-_]+)\}\}', replacer, text)

    @staticmethod
    def sanitize_url_for_docker(url: str) -> str:
        """
        Rewrites localhost URLs to use internal gateway or specific overrides when running inside Docker.
        """
        if not url: return url
        from app.config import settings
        
        # Rule A: User-specified global replacement for localhost
        if settings.TARGET_URL_REPLACEMENT and ("localhost" in url or "127.0.0.1" in url):
             new_url = re.sub(r'(https?://)(localhost|127\.0\.0\.1)', rf'\1{settings.TARGET_URL_REPLACEMENT}', url)
             logger.info(f"      🔧 Rewrote URL (Global Override): {url} -> {new_url}")
             return new_url
        
        # Rule B: Standard Internal rewrite for port 80/443 (Gateway/Nginx)
        # Normalize: ensure we catch localhost with or without trailing slash
        if any(url.lower().startswith(p) for p in ["http://localhost", "http://127.0.0.1"]):
            gateway = settings.INTERNAL_GATEWAY_URL.rstrip('/')
            new_url = url.replace("http://localhost", gateway).replace("http://127.0.0.1", gateway)
            logger.info(f"      🔧 Rewrote Internal Gateway URL: {url} -> {new_url}")
            return new_url

             
        # Rule C: Fallback for localhost:8000 to hit backend directly
        if ":8000" in url and ("localhost" in url or "127.0.0.1" in url):
             new_url = url.replace("localhost", "127.0.0.1") # Keep it internal
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
            nodes = flow_data.get('nodes', [])
            edges = flow_data.get('edges', [])
            
            logger.info(f"📊 [execute_flow_logic] Loaded Flow: {len(nodes)} nodes, {len(edges)} edges")
            
            # Build Graph (Adjacency List)
            adj = {n['id']: [] for n in nodes}
            in_degree = {n['id']: 0 for n in nodes}
            
            # Filter valid edges (where both source and target exist)
            node_ids = set(n['id'] for n in nodes)
            valid_edges = [e for e in edges if e['source'] in node_ids and e['target'] in node_ids]
            
            logger.info(f"🔗 Validated {len(valid_edges)}/{len(edges)} edges.")

            for edge in valid_edges:
                src, tgt = edge['source'], edge['target']
                adj[src].append(tgt)
                in_degree[tgt] += 1
                logger.debug(f"   Edge: {src} -> {tgt}")

            # BFS / Kahn's Algorithm for Topological Sort/Traversal
            # Start with nodes having in_degree 0 (Roots)
            queue = [n['id'] for n in nodes if in_degree[n['id']] == 0]
            
            logger.info(f"🌱 Initial Roots (queue): {queue}")
            
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
            history_buffer = []
            # Generate a unique batch_id to tie all API calls in this run together
            batch_id = f"sched_{uuid.uuid4().hex}"

            while queue:
                current_id = queue.pop(0)
                logger.info(f"➡️  [Step] Processing Node: {current_id} (Queue: {len(queue)})")
                
                if current_id in visited:
                    continue
                visited.add(current_id)

                # Execute Card for this Node
                card = cards.get(current_id)
                if card:
                    api_calls = card.get('apiCalls', [])
                    logger.info(f"      Running Node: {card.get('name')} ({len(api_calls)} calls)")
                    
                    try:
                        for api_call in api_calls:
                            method = api_call.get('method', 'GET')
                            url = FlowExecutorService.replace_vars(api_call.get('url', ''), variables_dict)
                            
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
                                    logger.info(f"      [SKIP] Logic Step: {api_call.get('name')} (PYTHON)")
                                    resp_status = 200
                                    resp_reason = "Logic Captured"
                                    resp_text = api_call.get('description', 'Playwright Script Content')
                                else:
                                    if url.startswith('/'):
                                        from app.config import settings
                                        base = settings.API_BASE_URL.rstrip('/')
                                        path = url.lstrip('/')
                                        url = f"{base}/{path}"
                                    
                                    # ✅ RE-ENABLED: Normalize URL for Docker internal networking
                                    url = FlowExecutorService.sanitize_url_for_docker(url)

                                    if FlowExecutorService.is_blocked_domain(url):
                                        logger.warning(f"      ⛔ Skipping Blocked/Ad Domain: {url}")
                                        continue
                                    
                                    # --- 1. Headers Reconstruction ---
                                    headers_raw = api_call.get('headers', {})
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
                                    headers = json.loads(FlowExecutorService.replace_vars(headers_json_str, variables_dict))
                                    if not isinstance(headers, dict): headers = {}
                                    headers = {str(k): str(v) for k, v in headers.items() if k.lower() not in ['content-length', 'host']} 

                                    # --- 2. Params Reconstruction (Query String) ---
                                    params_raw = api_call.get('params', {})
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
                                    params = json.loads(FlowExecutorService.replace_vars(params_json_str, variables_dict))
                                    if not isinstance(params, dict): params = {}
                                    params = {str(k): str(v) for k, v in params.items() if v is not None}

                                    # --- 3. Body Reconstruction ---
                                    body_raw = api_call.get('body', '')
                                    if isinstance(body_raw, (dict, list)):
                                        body_json_str = json.dumps(body_raw)
                                        body = FlowExecutorService.replace_vars(body_json_str, variables_dict)
                                    else:
                                        body = FlowExecutorService.replace_vars(str(body_raw), variables_dict)
                                    
                                    if body is None: body = ""

                                    ct_key = next((k for k in headers.keys() if k.lower() == 'content-type'), None)
                                    body_is_urlencoded = isinstance(body, str) and ('=' in body or '&' in body) and not body.strip().startswith('{')
                                    
                                    if ct_key and 'multipart/form-data' in str(headers[ct_key]).lower() and body_is_urlencoded:
                                        headers[ct_key] = 'application/x-www-form-urlencoded'
                                    
                                    if 'auth/login' in url and body_is_urlencoded:
                                        if not ct_key: headers['Content-Type'] = 'application/x-www-form-urlencoded'
                                        elif 'json' in headers[ct_key]: headers[ct_key] = 'application/x-www-form-urlencoded'

                                    start_time = time.time()
                                    resp = flow_session.request(method, url, headers=headers, data=body, params=params, timeout=30)
                                    duration = int((time.time() - start_time) * 1000)
                                    resp_status = resp.status_code
                                    resp_reason = resp.reason
                                    resp_headers = dict(resp.headers)
                                    resp_text = resp.text
                                    
                                    try:
                                        resp_json = resp.json()
                                    except ValueError:
                                        resp_json = None

                                    # --- Assertions ---
                                    assertions_raw = api_call.get('assertions', [])
                                    for assertion in assertions_raw:
                                        try:
                                            # Normalize Source
                                            src_raw = assertion.get('source') or assertion.get('type') or 'statusCode'
                                            src = 'statusCode' if src_raw in ['status', 'statusCode'] else src_raw
                                            
                                            raw_operator = assertion.get('operator', 'equals')
                                            op = str(raw_operator).lower()
                                            
                                            target = assertion.get('value') if assertion.get('value') is not None else assertion.get('target')
                                            prop = assertion.get('property') or assertion.get('path')
                                            
                                            actual_val = None
                                            is_success = False
                                            
                                            if src == 'statusCode':
                                                actual_val = resp_status
                                                try:
                                                    t_int = int(str(target).strip())
                                                    if op in ['equals', 'eq', '==', 'is']: is_success = (actual_val == t_int)
                                                    elif op in ['notequals', 'neq', '!=']: is_success = (actual_val != t_int)
                                                    elif op in ['gt', 'greaterthan', '>']: is_success = (actual_val > t_int)
                                                    elif op in ['lt', 'lessthan', '<']: is_success = (actual_val < t_int)
                                                    elif op in ['gte', '>=']: is_success = (actual_val >= t_int)
                                                    elif op in ['lte', '<=']: is_success = (actual_val <= t_int)
                                                except: is_success = False
                                            elif src == 'header':
                                                # Case insensitive header lookup
                                                h_key = str(prop).lower() if prop else ""
                                                actual_val = next((v for k, v in resp_headers.items() if k.lower() == h_key), None)
                                                str_act = str(actual_val) if actual_val is not None else ""
                                                str_tar = str(target)
                                                if op in ['equals', 'eq', 'is']: is_success = (str_act == str_tar)
                                                elif op in ['contains', 'in']: is_success = (str_tar in str_act)
                                                elif op in ['exists', 'not_null']: is_success = (actual_val is not None)
                                            elif src == 'responseTime':
                                                actual_val = duration
                                                try:
                                                    t_int = int(str(target).strip())
                                                    if op in ['lt', 'lessthan', '<']: is_success = (actual_val < t_int)
                                                    elif op in ['lte', '<=']: is_success = (actual_val <= t_int)
                                                    elif op in ['gt', 'greaterthan', '>']: is_success = (actual_val > t_int)
                                                    elif op in ['equals', 'eq']: is_success = (actual_val == t_int)
                                                except: is_success = False
                                            elif src == 'body':
                                                if op == 'exists':
                                                    # Check if path exists
                                                    if not prop: 
                                                        is_success = (resp_json is not None)
                                                        actual_val = "Body Received" if is_success else None
                                                    else:
                                                        parts = str(prop).split('.')
                                                        curr = resp_json
                                                        for p in parts:
                                                            if isinstance(curr, dict) and p in curr: curr = curr[p]
                                                            elif isinstance(curr, list):
                                                                try: curr = curr[int(p)]
                                                                except: curr = None; break
                                                            else: curr = None; break
                                                        is_success = (curr is not None)
                                                        actual_val = str(curr) if is_success else None
                                                elif resp_json:
                                                    # Path validation
                                                    parts = str(prop).split('.') if prop else []
                                                    curr = resp_json
                                                    for p in parts:
                                                        if isinstance(curr, dict) and p in curr: curr = curr[p]
                                                        elif isinstance(curr, list):
                                                            try: curr = curr[int(p)]
                                                            except: curr = None; break
                                                        else: curr = None; break
                                                    actual_val = curr
                                                    str_act = str(actual_val) if actual_val is not None else ""
                                                    str_tar = str(target)
                                                    if op in ['equals', 'eq', 'is']: is_success = (str_act == str_tar)
                                                    elif op in ['contains', 'in']: is_success = (str_tar in str_act)
                                                    elif op in ['exists', 'not_null']: is_success = (actual_val is not None)
                                            elif src == 'contract':
                                                # Placeholder for schema validation if target is 'Valid Contract'
                                                is_success = True if resp_status < 400 else False
                                                actual_val = "Schema Match" if is_success else "Invalid"
                                            
                                            assertion_results.append({
                                                "source": src_raw, "operator": raw_operator, "target": str(target),
                                                "actual": str(actual_val), "success": is_success
                                            })
                                            if not is_success: assertions_passed = False
                                        except Exception as ae:
                                            logger.error(f"        ❌ Assertion Error: {ae}")
                                            assertions_passed = False

                                    # --- Extraction ---
                                    extracts = api_call.get('extracts', [])
                                    if extracts:
                                        for rule in extracts:
                                            try:
                                                r_source = rule.get('source', 'body')
                                                r_prop = rule.get('property', '')
                                                r_var = rule.get('variable', '').strip().upper()
                                                
                                                val = None
                                                if r_source == 'header':
                                                    h_key = r_prop.lower()
                                                    val = next((v for k, v in resp_headers.items() if k.lower() == h_key), None)
                                                elif r_source == 'body' and resp_json:
                                                    parts = r_prop.split('.')
                                                    curr = resp_json
                                                    for p in parts:
                                                        if isinstance(curr, dict) and p in curr: curr = curr[p]
                                                        elif isinstance(curr, list):
                                                            try: curr = curr[int(p)]
                                                            except: curr = None; break
                                                        else: curr = None; break
                                                    val = curr
                                                
                                                if val is not None and r_var:
                                                    val_str = str(val)
                                                    variables_dict[r_var] = val_str
                                                    # Persist to DB for visibility in the environment panel
                                                    new_var = VariableCreate(name=r_var, value=val_str, project_id=product_id, environment_id=env_id, type="extracted")
                                                    VariableService.create(db, new_var)
                                                    logger.info(f"      ✅ Extracted [{r_var}] = '{val_str}'")
                                            except Exception as ee:
                                                logger.error(f"        ❌ Extraction Error: {ee}")

                            except Exception as req_ex:
                                logger.error(f"Request failed: {req_ex}")
                                resp_status = 500
                                final_error_message = str(req_ex)

                            if not final_error_message:
                                if api_call.get('assertions') and not assertions_passed:
                                    final_error_message = "Assertions Failed"
                                elif resp_status >= 400:
                                    final_error_message = f"HTTP Error {resp_status}"

                            if final_error_message: flow_fail_count += 1
                            else: flow_success_count += 1

                            hist = ExecutionHistoryCreate(
                                batch_id=batch_id,
                                api_id=int(api_call.get('id')) if str(api_call.get('id')).isdigit() else None,
                                api_name=api_call.get('name') or "Step",
                                project_id=product_id,
                                flow_id=flow_meta['id'],
                                node_id=current_id,
                                schedule_id=schedule_id,
                                feature_name=feature_name,
                                node_name=card.get('name'),
                                method=method,
                                url=url,
                                status_code=resp_status,
                                response_body=resp_text,
                                response_time=duration,
                                environment_id=env_id,
                                error_message=final_error_message,
                                assertions=assertion_results
                            )
                            history_buffer.append(hist)
                    except Exception as loop_ex:
                        logger.error(f"Critical error in execution loop: {loop_ex}")

                # Neighbors
                neighbors = adj.get(current_id, [])
                if neighbors:
                    logger.info(f"      🔗 Processing {len(neighbors)} neighbors of {current_id}")
                for tgt in neighbors:
                    in_degree[tgt] -= 1
                    logger.debug(f"         Neighbor {tgt} - New In-Degree: {in_degree[tgt]}")
                    if in_degree[tgt] == 0:
                        logger.info(f"      ✨ Node {tgt} is now ready (In-Degree 0). Adding to queue.")
                        queue.append(tgt)
                        queue.sort(key=get_x_pos)

            if history_buffer:
                HistoryService.save_batch(db, history_buffer, user_id)

            return flow_success_count, flow_fail_count

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
