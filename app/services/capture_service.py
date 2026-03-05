import json
from datetime import datetime
from sqlalchemy.orm import Session # Added import for Session
from app.models.flow_models import FlowDB, FlowNodeDB, FlowCardDataDB, FlowEdgeDB
from app.database import SessionLocal

class CaptureService:
    @staticmethod
    def ingest_captured_requests(db: Session, requests: list, project_id: int = None): # Modified signature
        """
        Receives a list of raw requests from the Chrome Extension 
        and converts them into a new Flow, grouping API calls by Page URL (Node).
        """
        if not requests: # Changed requests_data to requests
            return {"error": "No data received"}

        # db = SessionLocal() # Removed
        try:
            # Auto-assign to first project if none provided (Visibility Fallback)
            if not project_id:
                from app.models.feature_models import FeatureModel
                first_project = db.query(FeatureModel).first()
                if first_project:
                    project_id = first_project.id
                    print(f"⚠️ No project_id provided. Auto-assigning to Project {project_id} ({first_project.name})")

            # 1. Create a new Flow
            flow_name = f"Captured Flow - {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            new_flow = FlowDB(name=flow_name, project_id=project_id, flow_type="api", created_at=datetime.utcnow())
            db.add(new_flow)
            db.commit()
            db.refresh(new_flow)

            # 2. Group requests by Page URL (Sequence Preserved)
            # Logic: If current request's pageUrl is same as previous, add to same group.
            # If different, start new group (new Node).
            
            IGNORED_ENDPOINTS = [
                "/api/auth/profile",
                "/api/notifications",
                "/favicon.ico",
                "/manifest.json"
            ]

            def should_ignore(req):
                # 1. Check Blacklist
                url = req.get('url', '').lower()
                if any(ignored in url for ignored in IGNORED_ENDPOINTS):
                    return True
                
                # 2. Ignore OPTION requests (CORS preflight)
                if req.get('method') == 'OPTIONS':
                    return True
                    
                return False

            nodes_data = [] 
            current_group = None

            last_request_signature = None # To track sequential duplicates

            for req in requests: # Changed requests_data to requests
                # FILTER: Skip ignored requests
                if should_ignore(req):
                    print(f"Skipping ignored request: {req.get('url')}")
                    continue

                # DEDUPLICATION: Skip exact duplicate of the immediately preceding request
                # Signature: Method + URL + Body
                req_body_str = json.dumps(req.get('body')) if isinstance(req.get('body'), (dict, list)) else str(req.get('body'))
                request_signature = f"{req.get('method')}:{req.get('url')}:{req_body_str}"

                if request_signature == last_request_signature:
                    print(f"Skipping duplicate request: {req.get('url')}")
                    continue
                
                last_request_signature = request_signature

                page_url = req.get('pageUrl', 'Unknown Page')
                custom_name = req.get('customNodeName')
                print(f"DEBUG CAPTURE: URL={req.get('url')} CustomName={custom_name} Page={page_url}")
                
                # Logic: Group by (custom_name) if present, ELSE by (page_url)
                if custom_name:
                    group_key = f"custom:{custom_name}"
                    display_title = custom_name
                else:
                    group_key = f"page:{page_url}"
                    display_title = req.get('pageTitle', page_url)
                    # Cleanup title
                    if page_url == 'Unknown Page' and req.get('url'):
                        display_title = "API Group"

                if not current_group or current_group['group_key'] != group_key:
                    # Start New Node Group
                    current_group = {
                        'group_key': group_key,
                        'page_url': page_url,
                        'title': display_title,
                        'requests': []
                    }
                    nodes_data.append(current_group)
                
                # Add request to current group
                current_group['requests'].append(req)

            # 3. Create Nodes and Edges from Groups
            previous_node_id = None
            
            for i, group in enumerate(nodes_data):
                node_client_id = f"node-{i}"
                
                # Frontend Position Logic (Horizontal Layout)
                pos_x = 100 + (i * 450)
                pos_y = 100

                # Create Visual Node (Represents the Page)
                node = FlowNodeDB(
                    flow_id=new_flow.id,
                    client_id=node_client_id,
                    type="custom", 
                    position_x=pos_x,
                    position_y=pos_y,
                    width=400,
                    height=300,
                    data={
                        "name": group['title'] or "Page Node",
                        "color": "#3b82f6", # Blue for Pages
                        "description": f"Page: {group['page_url']}",
                        "isPage": True
                    }
                )
                db.add(node)

                # Process API Calls for this Node
                api_calls_payload = []
                for j, req in enumerate(group['requests']):
                    # Hardened URL Resolution
                    from urllib.parse import urljoin
                    original_url = req.get('url', '')
                    page_url = req.get('pageUrl', '')
                    
                    if page_url and not original_url.startswith('http'):
                        absolute_url = urljoin(page_url, original_url)
                        print(f"      [CAPTURE FIX] Resolved relative URL: {original_url} -> {absolute_url}")
                    else:
                        absolute_url = original_url

                    # Format Headers
                    raw_headers = req.get('headers', {})
                    formatted_headers = []
                    if isinstance(raw_headers, dict):
                        formatted_headers = [{"key": k, "value": str(v)} for k, v in raw_headers.items()]
                    
                    api_calls_payload.append({
                        "id": f"api-{i}-{j}",
                        "name": f"{req.get('method')} {absolute_url.split('?')[0].split('/')[-1] or '/'}", # Short name
                        "method": req.get('method', 'GET'),
                        "url": absolute_url,
                        "headers": formatted_headers,
                        "body": json.dumps(req.get('body')) if isinstance(req.get('body'), (dict, list)) else (req.get('body') or ""), 
                        "extracts": [],
                        "assertions": []
                    })

                # Create Card Data
                card_data = FlowCardDataDB(
                    node_id=node_client_id,
                    flow_id=new_flow.id,
                    name=group['title'] or "Page Node",
                    color="#3b82f6",
                    description=f"Captured from: {group['page_url']}",
                    api_calls=api_calls_payload
                )
                db.add(card_data)

                # Create Edge from Previous Node
                if previous_node_id:
                    edge = FlowEdgeDB(
                        flow_id=new_flow.id,
                        client_id=f"edge-{i-1}-{i}",
                        source=previous_node_id,
                        target=node_client_id,
                        type="buttonedge",
                        animated=True
                    )
                    db.add(edge)
                
                previous_node_id = node_client_id
            
            db.commit()
            return {"success": True, "flow_id": new_flow.id, "flow_name": new_flow.name, "nodes_created": len(nodes_data)}
            
        except Exception as e:
            db.rollback()
            print(f"Error ingesting capture: {e}")
            import traceback
            traceback.print_exc()
            return {"error": str(e)}
        finally:
            pass # Removed db.close()

    @staticmethod
    def get_hierarchy(db: Session): # Modified signature
        """
        Returns a list of Products with their Features
        Structure: [{id, name, features: [{id, name}]}]
        """
        # db = SessionLocal() # Removed
        try:
            from app.models.product_models import ProductModel
            from app.models.feature_models import FeatureModel # Added import
            products = db.query(ProductModel).all()
            result = []
            for prod in products:
                features = []
                for feat in prod.features:
                    features.append({"id": feat.id, "name": feat.name})
                
                result.append({
                    "id": prod.id,
                    "name": prod.name,
                    "features": features
                })
            return result
        except Exception as e:
            print(f"Error fetching hierarchy: {e}")
            return []
        finally:
            pass # Session managed by caller
