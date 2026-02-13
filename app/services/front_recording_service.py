import logging
import traceback
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
    def save_recording(feature_id: int, name: str, requests: List[Dict[str, Any]], interactions: List[Dict[str, Any]]):
        db = SessionLocal()
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
                FrontRecordingService.generate_flow_from_recording(db, new_recording)
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
            db.close()

    @staticmethod
    def list_recordings(feature_id: int):
        db = SessionLocal()
        try:
            recordings = db.query(FrontRecordingDB).filter(FrontRecordingDB.feature_id == feature_id).all()
            return recordings
        finally:
            db.close()

    @staticmethod
    def generate_playwright_script(recording_id: int):
        db = SessionLocal()
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
            db.close()
    @staticmethod
    def generate_flow_from_recording(db: Session, recording: FrontRecordingDB):
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
            "data": {"name": feature_name, "color": "#10b981", "description": "Raiz do Fluxo E2E"}
        })
        card_data[master_id] = {
            "name": feature_name,
            "color": "#10b981",
            "description": "Raiz do Fluxo E2E",
            "apiCalls": []
        }

        for idx, step in enumerate(steps):
            node_id = f"node-e2e-{idx}"
            
            # Node
            nodes.append({
                "id": node_id,
                "type": "custom",
                "position": {"x": (idx + 1) * spacing_x, "y": 100},
                "data": {"name": step["name"], "color": "#3b82f6"}
            })
            
            # Edge from previous or Master
            source_id = f"node-e2e-{idx-1}" if idx > 0 else master_id
            edges.append({
                "id": f"edge-e2e-{source_id}-{node_id}",
                "source": source_id,
                "target": node_id,
                "type": "buttonedge",
                "animated": True
            })
            
            # Code for this specific node
            node_script = [
                "# Script gerado para esta etapa",
                ""
            ]
            for inter in step["inters"]:
                action = inter.get('action')
                selector = inter.get('selector')
                if action == 'click':
                    node_script.append(f"page.click('{selector}')")
                elif action == 'fill':
                    value = inter.get('value', '')
                    value_esc = str(value).replace("'", "\\'")
                    node_script.append(f"page.fill('{selector}', '{value_esc}')")
                node_script.append("page.wait_for_timeout(300)")

            # Process API Calls for this step
            api_calls_payload = []

            # 1. Add Playwright Script as a special "API"
            api_calls_payload.append({
                "id": f"script-{idx}",
                "name": "Script Playwright",
                "method": "PYTHON",
                "url": "playwright",
                "description": "\n".join(node_script),
                "headers": [],
                "params": [],
                "assertions": [],
                "extracts": []
            })

            # 2. Add Real API Requests captured during this step
            # We filter requests that match the current step name
            step_requests = [
                r for r in (recording.requests or []) 
                if r.get('customNodeName') == step["name"]
            ]

            for req in step_requests:
                api_calls_payload.append({
                    "id": str(uuid.uuid4()),
                    "name": f"{req.get('method')} {req.get('url')[:30]}...",
                    "method": req.get('method'),
                    "url": req.get('url'),
                    "description": "Captured via Front Recorder",
                    "headers": [{"key": k, "value": str(v)} for k, v in req.get('headers', {}).items()] if isinstance(req.get('headers'), dict) else req.get('headers', []),
                    "body": str(req.get('body')) if req.get('body') is not None else None,
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
                "apiCalls": api_calls_payload
            }

        # 3. Save via FlowService
        flow_schema = FlowSaveSchema(
            projectId=recording.feature_id,
            flowType="frontend",
            name=f"Fluxo Front: {recording.name}",
            nodes=nodes,
            edges=edges,
            cardData=card_data
        )
        
        FlowService.save(db, flow_schema)
