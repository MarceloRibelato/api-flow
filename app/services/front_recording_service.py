import logging
import traceback
import uuid
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from app.database import SessionLocal
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

        # 2. Build React Flow Structure
        nodes = []
        edges = []
        card_data = {}
        
        spacing_x = 300
        
        # Add Master Node (Consistent with API Flow)
        feature = db.query(FeatureModel).filter(FeatureModel.id == recording.feature_id).first()
        feature_name = feature.name if feature else "Funcionalidade"
        master_id = f"master-{recording.feature_id}"
        
        nodes.append({
            "id": master_id,
            "type": "custom",
            "position": {"x": 0, "y": 100},
            "data": {"name": feature_name, "color": "#10b981", "description": "Raiz do Fluxo Gerado"}
        })
        card_data[master_id] = {
            "name": feature_name,
            "color": "#10b981",
            "description": "Raiz do Fluxo Gerado",
            "apiCalls": []
        }

        for idx, step in enumerate(steps):
            node_id = f"node-recorded-{idx}"
            
            # Node - Data must match NodeDataBasic (no description here)
            nodes.append({
                "id": node_id,
                "type": "custom",
                "position": {"x": (idx + 1) * spacing_x, "y": 100},
                "data": {
                    "name": step["name"], 
                    "color": "#3b82f6",
                    "childCount": 0,
                    "isCollapsed": False
                }
            })
            
            # Edge from previous or Master
            source_id = f"node-recorded-{idx-1}" if idx > 0 else master_id
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
            
            # Filter requests matching this step
            # Association Strategy: match by customNodeName OR pageUrl fallback
            step_requests = []
            for r in (recording.requests or []):
                # 1. Direct match by custom name (best)
                if r.get('customNodeName') and r.get('customNodeName') == step["name"]:
                    step_requests.append(r)
                # 2. Fallback: Match by pageUrl if both have it (and customNodeName is absent in request)
                elif not r.get('customNodeName'):
                    if req_page_url and step_page_url and req_page_url == step_page_url:
                        step_requests.append(r)
                # 3. New Fallback: Match by timestamp (requests occurring shortly after the step interactions)
                elif not r.get('customNodeName'):
                    req_ts = r.get('timestamp', 0)
                    if step["inters"]:
                        step_start = step["inters"][0].get('timestamp', 0)
                        step_end = step["inters"][-1].get('timestamp', 0)
                        # If request happened during or up to 2s after the step
                        if step_start <= req_ts <= (step_end + 2000):
                            step_requests.append(r)
            
            for req in step_requests:
                combined_steps.append({"raw": req, "type": "api", "ts": req.get('timestamp', 0)})

            # Sort by timestamp to preserve real user flow
            combined_steps.sort(key=lambda x: x['ts'])

            # Handle Orphaned Requests (not matched to any step)
            # Find all requests that were not added to any node
            all_req_ids_in_steps = set()
            for s in steps:
                for r in (s.get('matched_requests') or []): # We need to track this
                    all_req_ids_in_steps.add(id(r))
            
            # This logic needs a structural rethink for simplicity:
            # Let's just gather all requests into a "Global Requests" node if they didn't match.
            # But the current generator is one-pass. 

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
        if feature and feature.product:
            company_id = feature.product.company_id
        
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
