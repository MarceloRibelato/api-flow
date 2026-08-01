import logging
import traceback
import uuid
from typing import List, Dict, Any
from sqlalchemy.orm import Session

from app.models.front_recording_models import FrontRecordingDB
from app.models.feature_models import FeatureModel
from app.schemas.flow_schemas import FlowSaveSchema, NodeSchema, EdgeSchema, CardDataSchema, NodeDataBasic
from app.services.flow_service import FlowService

logger = logging.getLogger(__name__)

class FrontRecordingService:
    @staticmethod
    def save_recording(db: Session, feature_id: int, name: str, requests: List[Dict[str, Any]], interactions: List[Dict[str, Any]], user_id: int):
        try:
            # Verify feature exists
            feature = db.query(FeatureModel).filter(FeatureModel.id == feature_id).first()
            if not feature:
                return {"error": "Feature não encontrada"}

            # Filter out chrome-extension internal URLs
            if interactions:
                interactions = [i for i in interactions if not str(i.get('pageUrl', '')).startswith('chrome-extension://') and not str(i.get('frameUrl', '')).startswith('chrome-extension://')]
            if requests:
                requests = [r for r in requests if not str(r.get('url', '')).startswith('chrome-extension://') and not str(r.get('pageUrl', '')).startswith('chrome-extension://')]

            new_recording = FrontRecordingDB(
                feature_id=feature_id,
                name=name,
                requests=requests,
                interactions=interactions
            )
            db.add(new_recording)
            db.commit()
            db.refresh(new_recording)
            
            # AUTOMATIC FLOW GENERATION
            try:
                FrontRecordingService.generate_flow_from_recording(db, new_recording, user_id)
            except Exception as fe:
                logger.error(f"Failed to auto-generate flow: {fe}")
                logger.error(traceback.format_exc())

            return {
                "success": True,
                "id": new_recording.id,
                "name": new_recording.name
            }
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to save front recording: {e}")
            return {"error": str(e)}
        finally:
            pass

    @staticmethod
    def list_recordings(db: Session, feature_id: int):
        try:
            recordings = db.query(FrontRecordingDB).filter(FrontRecordingDB.feature_id == feature_id).all()
            return recordings
        finally:
            pass

    @staticmethod
    def generate_playwright_script(db: Session, recording_id: int):
        try:
            recording = db.query(FrontRecordingDB).filter(FrontRecordingDB.id == recording_id).first()
            if not recording:
                return "# Gravação não encontrada"

            interactions = recording.interactions or []
            
            script_lines = [
                "from playwright.sync_api import sync_playwright",
                "",
                "def run():",
                "    with sync_playwright() as p:",
                "        browser = p.chromium.launch(headless=False)",
                "        context = browser.new_context()",
                "        page = context.new_page()",
                ""
            ]

            last_node = None
            for inter in interactions:
                node = inter.get('customNodeName')
                if node and node != last_node:
                    script_lines.append(f"        # --- Etapa: {node} ---")
                    last_node = node
                
                action = inter.get('action')
                selector = inter.get('selector')
                
                if action == 'click':
                    script_lines.append(f"        page.click('{selector}')")
                elif action == 'fill':
                    value = inter.get('value', '')
                    # Escape quotes in value
                    value_esc = str(value).replace("'", "\\'")
                    script_lines.append(f"        page.fill('{selector}', '{value_esc}')")
                
                # Add a small delay for stability
                script_lines.append("        page.wait_for_timeout(500)")

            script_lines.extend([
                "",
                "        # Finalizando",
                "        print('Automação concluída com sucesso!')",
                "        browser.close()",
                "",
                "if __name__ == '__main__':",
                "    run()"
            ])

            return "\n".join(script_lines)
        finally:
            pass
    @staticmethod
    def generate_flow_from_recording(db: Session, recording: FrontRecordingDB, user_id: int):
        interactions = recording.interactions or []
        if not interactions:
            return

        # Common telemetry and analytics domains to ignore during flow generation
        TELEMETRY_DOMAINS = [
            "backtrace.io", "sentry.io", "google-analytics.com", "googletagmanager.com",
            "datadoghq.com", "newrelic.com", "mixpanel.com", "segment.com",
            "facebook.net", "facebook.com/tr", "doubleclick.net", "clarity.ms",
            "hotjar.com", "optimizely.com", "stats.g.doubleclick.net",
            "track.hubspot.com", "analytics"
        ]

        def is_telemetry(url: str) -> bool:
            if not url: return True
            for domain in TELEMETRY_DOMAINS:
                if domain in url:
                    return True
            return False

        # 1. Group interactions by step name or Page URL
        steps = []
        current_step = {"name": "Início", "inters": []}
        
        for inter in interactions:
            step_name = inter.get('customNodeName')
            if not step_name:
                # Fallback to page title or URL
                step_name = inter.get('pageTitle') or inter.get('pageUrl', 'Sem Título')
            
            if step_name != current_step["name"]:
                if current_step["inters"]:
                    steps.append(current_step)
                current_step = {"name": step_name, "inters": []}
            current_step["inters"].append(inter)
            
        if current_step["inters"]:
            steps.append(current_step)

        # Assign requests to steps to avoid orphans
        if recording.requests and steps:
            for req in recording.requests:
                req_url = req.get('url', '')
                if is_telemetry(req_url):
                    continue

                req_page_url = req.get('pageUrl')
                req_custom_name = req.get('customNodeName')
                req_ts = req.get('timestamp', 0)
                
                assigned_idx = -1
                
                # 1. Match by custom name
                if req_custom_name:
                    for i, s in enumerate(steps):
                        if s["name"] == req_custom_name:
                            assigned_idx = i
                            break
                
                # 2. Match by pageUrl and timestamp proximity
                if assigned_idx == -1 and req_page_url:
                    for i, s in enumerate(steps):
                        step_page_url = s["inters"][0].get('pageUrl') if s["inters"] else None
                        if step_page_url == req_page_url:
                            step_start = s["inters"][0].get('timestamp', 0) if s["inters"] else 0
                            if req_ts >= step_start - 5000:
                                assigned_idx = i
                            elif assigned_idx == -1:
                                assigned_idx = i
                
                # 3. Fallback: Assign to the step that has the closest timestamp before it
                if assigned_idx == -1:
                    closest_idx = 0
                    for i, s in enumerate(steps):
                        step_start = s["inters"][0].get('timestamp', 0) if s["inters"] else 0
                        if step_start <= req_ts:
                            closest_idx = i
                    assigned_idx = closest_idx
                
                # Store in the step
                steps[assigned_idx].setdefault('matched_requests', []).append(req)

        # 2. Build React Flow Structure
        nodes = []
        edges = []
        card_data = {}
        
        spacing_x = 300
        
        # ADD START NODE
        nodes.append({
            "id": "start",
            "type": "startNode",
            "position": {"x": -300, "y": 100},
            "data": {
                "name": "Start", 
                "color": "#10b981",
                "childCount": 0,
                "isCollapsed": False
            }
        })
        card_data["start"] = {
            "name": "Start",
            "color": "#10b981",
            "description": "Início do fluxo",
            "apiCalls": [],
            "e2eSteps": [],
            "bddScenarios": [],
            "envData": {}
        }

        if steps:
            edges.append({
                "id": "edge-start-node-recorded-0",
                "source": "start",
                "target": "node-recorded-0",
                "type": "buttonedge",
                "animated": True
            })
        
        for idx, step in enumerate(steps):
            node_id = f"node-recorded-{idx}"
            
            # Node - Data must match NodeDataBasic (no description here)
            nodes.append({
                "id": node_id,
                "type": "custom",
                "position": {"x": idx * spacing_x, "y": 100},
                "data": {
                    "name": step["name"], 
                    "color": "#3b82f6",
                    "childCount": 0,
                    "isCollapsed": False
                }
            })
            
            # Edge from previous step
            if idx > 0:
                source_id = f"node-recorded-{idx-1}"
                edges.append({
                    "id": f"edge-recorded-{source_id}-{node_id}",
                    "source": source_id,
                    "target": node_id,
                    "type": "buttonedge",
                    "animated": True
                })
            
            # 1. Combine All Steps (Interactions + Requests) for Interleaving
            combined_steps = []
            
            # Add navigation if it's the first step and we have a URL
            base_url = step["inters"][0].get('pageUrl') if step["inters"] else ""
            if idx == 0 and base_url:
                 combined_steps.append({
                    "raw": {
                        "action": "browser",
                        "value": base_url
                    },
                    "type": "e2e",
                    "ts": (step["inters"][0].get('timestamp', 0) if step["inters"] else 0) - 1
                })

            for inter in step["inters"]:
                combined_steps.append({"raw": inter, "type": "e2e", "ts": inter.get('timestamp', 0)})
            
            # Add all matched requests
            for req in step.get("matched_requests", []):
                combined_steps.append({"raw": req, "type": "api", "ts": req.get('timestamp', 0)})

            # Sort by timestamp to preserve real user flow
            combined_steps.sort(key=lambda x: x['ts'])

            # 2. Process Combined Steps and Assign Order
            api_calls_payload = []
            e2e_steps_payload = []
            
            for global_order, entry in enumerate(combined_steps):
                data = entry['raw']
                if entry['type'] == 'e2e':
                    action = data.get('action')
                    selector = data.get('selector', 'body')
                    value = data.get('value', '')
                    text = data.get('text', '')
                    
                    if action == 'browser':
                        e2e_steps_payload.append({
                            "id": str(uuid.uuid4()),
                            "order": global_order,
                            "type": "browser",
                            "name": "Navegar para Página",
                            "properties": {"value": value, "timeout": 30000}
                        })
                    elif action == 'click':
                         e2e_steps_payload.append({
                            "id": str(uuid.uuid4()),
                            "order": global_order,
                            "type": "click",
                            "name": f"Clicar: {text}" if text else f"Clicar em {selector.split(' > ')[-1]}",
                            "properties": {"selector": selector, "timeout": 30000, "isIframe": data.get('isIframe', False), "frameUrl": data.get('frameUrl', '')}
                        })
                    elif action == 'fill':
                        label = data.get('label', 'Campo')
                        e2e_steps_payload.append({
                            "id": str(uuid.uuid4()),
                            "order": global_order,
                            "type": "type",
                            "name": f"Digitar em {label}" if label != 'Campo' else f"Digitar em {selector.split(' > ')[-1]}",
                            "properties": {"selector": selector, "value": value, "timeout": 30000, "isIframe": data.get('isIframe', False), "frameUrl": data.get('frameUrl', '')}
                        })
                    
                else: # API Request
                     api_calls_payload.append({
                        "id": str(uuid.uuid4()),
                        "order": global_order,
                        "name": f"{data.get('method')} {data.get('url', '')[:30]}...",
                        "method": data.get('method', 'GET'),
                        "url": data.get('url', ''),
                        "description": "Captured via Front Recorder",
                        "headers": [{"key": k, "value": str(v)} for k, v in data.get('headers', {}).items()] if isinstance(data.get('headers'), dict) else [],
                        "body": str(data.get('body')) if data.get('body') else None,
                        "params": [],
                        "timeout": 30000,
                        "assertions": [],
                        "extracts": []
                    })

            # Card Data
            card_data[node_id] = {
                "name": step["name"],
                "color": "#3b82f6",
                "description": f"Etapa automática gerada da gravação: {recording.name}",
                "apiCalls": api_calls_payload,
                "e2eSteps": e2e_steps_payload,
                "bddScenarios": [],
                "envData": {}
            }

        # 3. SAVE DOUBLE FLOWS (E2E and API)
        import copy
        company_id = None
        if recording.feature and recording.feature.product:
            company_id = recording.feature.product.company_id
        
        if not company_id:
            logger.warning(f"Could not save flows for recording {recording.id}: Company ID not found")
            return

        # A. Save E2E Flow
        flow_schema_e2e = FlowSaveSchema(
            projectId=recording.feature_id,
            flow_type="e2e",
            name=f"Fluxo Front: {recording.name}",
            nodes=nodes,
            edges=edges,
            cardData=card_data
        )
        FlowService.save(db, flow_schema_e2e, company_id, user_id)
        logger.info(f"✅ Auto-Generated E2E Flow for recording {recording.name}")

        # B. Save API Flow (Parallel)
        # We clone the card data and remove E2E steps for a clean API view
        card_data_api = copy.deepcopy(card_data)
        for nid in card_data_api:
            card_data_api[nid]["e2eSteps"] = []
            
        flow_schema_api = FlowSaveSchema(
            projectId=recording.feature_id,
            flow_type="api",
            name=f"Fluxo API: {recording.name}",
            nodes=nodes,
            edges=edges,
            cardData=card_data_api
        )
        FlowService.save(db, flow_schema_api, company_id, user_id)
        logger.info(f"✅ Auto-Generated API Flow for recording {recording.name}")
