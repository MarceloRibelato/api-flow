from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from typing import Dict, Any
from app.database import get_db
from app.services.skill_service import SkillService
from app.services.analysis_service import AnalysisService
import uuid
import json
import random

router = APIRouter(prefix="/analysis/skills", tags=["Analysis Skills"])

@router.get("/debug_settings")
def debug_settings(db: Session = Depends(get_db)):
    from app.models.agent_models import AgentSettingsDB
    settings = db.query(AgentSettingsDB).first()
    if not settings:
        return {"error": "No settings found"}
    return {
        "user_id": settings.user_id,
        "provider": settings.ai_provider,
        "model": settings.ai_model,
        "key": settings.ai_api_key,
        "url": settings.ai_base_url
    }

@router.get("/dump-flow")
def dump_flow(db: Session = Depends(get_db)):
    from app.models.flow_models import FlowDB, FlowEdgeDB
    import uuid
    flow = db.query(FlowDB).order_by(FlowDB.updated_at.desc()).first()
    if not flow: return {"error": "no flow"}
    
    existing_edges = [(e.source, e.target) for e in flow.flow_edges]
    fixed_nodes = []
    new_edges = []
    
    connected_targets = {target for source, target in existing_edges}
    
    for i, n in enumerate(flow.flow_nodes):
        if n.client_id != "start" and n.client_id not in connected_targets:
            fixed_nodes.append(n.client_id)
            
            # Find previous node to connect to
            if i == 0 or flow.flow_nodes[i-1].client_id == "start":
                source_id = "start"
            else:
                source_id = flow.flow_nodes[i-1].client_id
                
            new_edges.append(FlowEdgeDB(
                flow_id=flow.id,
                client_id=f"edge_fixed_{uuid.uuid4().hex[:6]}",
                source=source_id,
                target=n.client_id,
                type="buttonedge",
                animated=True
            ))
            connected_targets.add(n.client_id)
            
    if new_edges:
        db.add_all(new_edges)
        db.commit()
        return {"status": "fixed", "nodes_fixed": fixed_nodes}
    
    return {"status": "all_connected"}

@router.get("/clean-ghosts")
def clean_ghosts(db: Session = Depends(get_db)):
    from app.models.flow_models import FlowDB, FlowNodeDB, FlowCardDataDB, FlowE2EStepDB
    from sqlalchemy import desc
    
    # 1. Encontra cards órfãos (que não tem um Node correspondente no fluxo)
    all_cards = db.query(FlowCardDataDB).all()
    ghost_cards_count = 0
    for card in all_cards:
        node = db.query(FlowNodeDB).filter(
            FlowNodeDB.flow_id == card.flow_id,
            FlowNodeDB.client_id == card.node_id
        ).first()
        
        if not node:
            db.query(FlowE2EStepDB).filter(FlowE2EStepDB.card_db_id == card.db_id).delete()
            db.delete(card)
            ghost_cards_count += 1
            
    # 2. Encontra e deleta fluxos duplicados antigos (mantendo apenas o mais recente)
    # Isso resolve o bug onde o Card Inventory soma cards de fluxos antigos que não aparecem mais no canvas
    flows = db.query(FlowDB).order_by(desc(FlowDB.updated_at)).all()
    seen_flows = set()
    deleted_flows_count = 0
    
    for f in flows:
        key = f"{f.project_id}_{f.flow_type}"
        if key in seen_flows:
            # Fluxo duplicado mais antigo! Deletar tudo dele.
            db.query(FlowE2EStepDB).filter(
                FlowE2EStepDB.card_db_id.in_(
                    db.query(FlowCardDataDB.db_id).filter(FlowCardDataDB.flow_id == f.id)
                )
            ).delete(synchronize_session=False)
            
            db.query(FlowCardDataDB).filter(FlowCardDataDB.flow_id == f.id).delete(synchronize_session=False)
            db.query(FlowNodeDB).filter(FlowNodeDB.flow_id == f.id).delete(synchronize_session=False)
            db.query(FlowEdgeDB).filter(FlowEdgeDB.flow_id == f.id).delete(synchronize_session=False)
            db.delete(f)
            deleted_flows_count += 1
        else:
            seen_flows.add(key)
            
    db.commit()
    return {
        "status": "cleanup_complete", 
        "deleted_ghost_cards": ghost_cards_count,
        "deleted_duplicate_flows": deleted_flows_count
    }
@router.get("/debug-db")
def debug_db(db: Session = Depends(get_db)):
    from app.models.flow_models import FlowDB, FlowNodeDB, FlowCardDataDB
    from sqlalchemy import func
    
    # Get all flows
    flows = db.query(FlowDB).all()
    result = []
    for f in flows:
        node_count = db.query(func.count(FlowNodeDB.client_id)).filter(FlowNodeDB.flow_id == f.id).scalar()
        card_count = db.query(func.count(FlowCardDataDB.db_id)).filter(FlowCardDataDB.flow_id == f.id).scalar()
        result.append({
            "flow_id": f.id,
            "project_id": f.project_id,
            "name": f.name,
            "flow_type": f.flow_type,
            "node_count": node_count,
            "card_count": card_count
        })
    return {"flows": result}

@router.get("/list")
def list_skills(
    db: Session = Depends(get_db)
):
    """Returns the list of available specialist skills."""
    return [
        { "id": "qa_specialist_api", "name": "Especialista em API", "description": "Automação e testes de Backend/API." },
        { "id": "qa_specialist_web", "name": "Especialista em Web", "description": "Automação e testes de Frontend Web." },
        { "id": "qa_specialist_mobile", "name": "Especialista em Mobile", "description": "Automação e testes Mobile (Appium)." },
        { "id": "generate_alternatives_skill", "name": "Geração de Alternativas", "description": "Cria variações de teste." },
        { "id": "security_skill", "name": "Especialista em Segurança", "description": "Analisa vulnerabilidades." },
        { "id": "performance_skill", "name": "Especialista em Performance", "description": "Avalia gargalos e carga." }
    ]

@router.post("/flow/{flow_id}/execute/{skill_id}")
def execute_skill(
    flow_id: int,
    skill_id: str,
    user_id: int = Query(...),
    company_id: int = Query(1),
    db: Session = Depends(get_db)
):
    """Executes a generic skill (returns text/insights)."""
    try:
        from app.services.flow_service import FlowService
        flow = FlowService.load(db, None, company_id, flow_id, "api")
        if not flow or not flow.get("nodes"):
            flow = FlowService.load(db, None, company_id, flow_id, "e2e")
            
        generation_context = {
            "blueprint": "Logical Blueprint Context",
            "nodes": flow.get("nodes", []),
            "cardData": flow.get("cardData", {}),
            "project_id": flow.get("project_id")
        }
        
        result = SkillService.execute_skill(db, user_id, skill_id, generation_context)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class ChatRequestSchema(BaseModel):
    message: str
    history: Optional[List[Dict[str, str]]] = None
    live_context: Optional[Dict[str, Any]] = None

@router.post("/flow/{flow_id}/chat/{skill_id}")
def chat_with_skill(
    flow_id: int,
    skill_id: str,
    req: ChatRequestSchema,
    user_id: int = Query(...),
    company_id: int = Query(1),
    db: Session = Depends(get_db)
):
    """Executes a chat message within a skill context, with memory."""
    try:
        from app.services.flow_service import FlowService
        
        # Use live context from frontend if available, else load from DB
        if req.live_context and "nodes" in req.live_context:
            nodes = req.live_context.get("nodes", [])
            cardData = req.live_context.get("cardData", {})
        else:
            flow = FlowService.load(db, None, company_id, flow_id, "api")
            if not flow or not flow.get("nodes"):
                flow = FlowService.load(db, None, company_id, flow_id, "e2e")
            nodes = flow.get("nodes", [])
            cardData = flow.get("cardData", {})
            
        # Limit the size of context to prevent context window overflow
        if len(nodes) > 10:
            nodes = nodes[:10]
            
        mapping_payload = {"nodes": nodes, "cardData": cardData}
        if req.live_context and "execution_results" in req.live_context:
            mapping_payload["execution_results"] = req.live_context["execution_results"]

        generation_context = {
            "mapping_context": json.dumps(mapping_payload),
            "user_message": req.message
        }
        
        # We pass flow_id=None to disable the automatic database memory injection,
        # because we are passing the explicit `history` from the frontend session.
        result = SkillService.execute_skill(
            db, 
            user_id, 
            skill_id, 
            generation_context, 
            flow_id=None, 
            chat_history=req.history
        )
        return {"response": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/flow/{flow_id}/generate-alternatives")
def generate_alternatives(
    flow_id: int,
    user_id: int = Query(...),
    company_id: int = Query(1),
    db: Session = Depends(get_db)
):
    """Executes the specialist mapping -> generation workflow for a specific flow."""
    try:
        alternatives = SkillService.generate_alternatives_workflow(db, user_id, flow_id, company_id)
        return alternatives
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _normalize_card_data(card_data: dict):
    """Normalizes API calls to match Pydantic schema."""
    for card_id, cd in card_data.items():
        all_api_calls_lists = []
        if "apiCalls" in cd:
            all_api_calls_lists.append(cd.get("apiCalls", []))
            
        env_data = cd.get("envData", {})
        for env_name, env_details in env_data.items():
            if isinstance(env_details, dict) and "apiCalls" in env_details:
                all_api_calls_lists.append(env_details.get("apiCalls", []))
        
        for api_calls_list in all_api_calls_lists:
            for idx, api_call in enumerate(api_calls_list):
                if not isinstance(api_call, dict):
                    continue
                    
                if "id" not in api_call: api_call["id"] = f"ai-step-{idx}"
                if "method" not in api_call: api_call["method"] = "GET"
                if "url" not in api_call: api_call["url"] = "http://localhost"
                
                name_val = api_call.get("name")
                if "name" not in api_call or not name_val or str(name_val).strip() in ("", "None", "Nova Requisição", "Card Name"):
                    url_path = api_call["url"].split("?")[0]
                    endpoint = url_path.strip("/").split("/")[-1]
                    if not endpoint: endpoint = "API"
                    api_call["name"] = f"{api_call['method']} /{endpoint}"
                
                for field in ["headers", "params"]:
                    if field in api_call:
                        val = api_call[field]
                        new_val = []
                        if isinstance(val, dict):
                            new_val = [{"key": str(k), "value": str(v)} for k, v in val.items()]
                        elif isinstance(val, list):
                            for item in val:
                                if isinstance(item, dict):
                                    if "key" in item and "value" in item:
                                        new_val.append({"key": str(item["key"]), "value": str(item["value"])})
                                    else:
                                        for k, v in item.items():
                                            new_val.append({"key": str(k), "value": str(v)})
                        elif isinstance(val, str):
                            try:
                                parsed = json.loads(val)
                                if isinstance(parsed, dict):
                                    new_val = [{"key": str(k), "value": str(v)} for k, v in parsed.items()]
                                elif isinstance(parsed, list):
                                    for item in parsed:
                                        if isinstance(item, dict):
                                            if "key" in item and "value" in item:
                                                new_val.append({"key": str(item["key"]), "value": str(item["value"])})
                                            else:
                                                for k, v in item.items():
                                                    new_val.append({"key": str(k), "value": str(v)})
                            except:
                                pass
                        api_call[field] = new_val

                if "assertions" in api_call and isinstance(api_call["assertions"], list):
                    for ast in api_call["assertions"]:
                        if isinstance(ast, dict):
                            if "source" not in ast: ast["source"] = "statusCode"
                            if "operator" not in ast: ast["operator"] = "equals"
                            if "target" not in ast: ast["target"] = "200"

                if "extracts" in api_call and isinstance(api_call["extracts"], list):
                    for ext in api_call["extracts"]:
                        if isinstance(ext, dict):
                            if "variableName" in ext and "variable" not in ext:
                                ext["variable"] = ext.pop("variableName")
                            if "pathSelector" in ext and "property" not in ext:
                                ext["property"] = ext.pop("pathSelector")
                            if "source" not in ext: ext["source"] = "body"
                            if "variable" not in ext: ext["variable"] = "VAR"

@router.post("/save-generated-flow")
def save_generated_flow(
    payload: Dict[str, Any],
    user_id: int = Query(...),
    company_id: int = Query(1),
    flow_id: int = Query(None),
    db: Session = Depends(get_db)
):
    """Saves a flow that was generated by the AI skill."""
    from app.services.flow_service import FlowService
    from app.schemas.flow_schemas import FlowSaveSchema
    from app.models.flow_models import FlowDB
    
    try:
        if "projectId" not in payload and flow_id:
            flow = db.query(FlowDB).filter(FlowDB.id == flow_id).first()
            if flow:
                payload["projectId"] = flow.project_id
        
        if "projectId" not in payload:
             payload["projectId"] = 1 # Fallback safe project_id
             
        if "flow_type" not in payload:
             payload["flow_type"] = "api"
             
        # Merge with original flow to prevent data loss and ADD AS NEW BRANCHES
        if flow_id:
            original_flow = FlowService.load(db, payload["projectId"], company_id, flow_id, payload["flow_type"])
            if original_flow:
                orig_nodes_list = original_flow.get("nodes", [])
                orig_edges_list = original_flow.get("edges", [])
                orig_card_data = original_flow.get("cardData", {})
                
                orig_node_ids = {str(n.get("id")) for n in orig_nodes_list}
                
                id_mapping = {}
                suffix = f"_alt_{uuid.uuid4().hex[:6]}"
                orig_node_pos = {str(n.get("id")): n.get("position", {}) for n in orig_nodes_list}
                
                new_nodes = []
                for gen_n in payload.get("nodes", []):
                    old_id = str(gen_n.get("id"))
                    if old_id == "start":
                        id_mapping["start"] = "start"
                        continue # Skip adding a duplicate start node
                        
                    if old_id in orig_node_ids:
                        new_id = f"{old_id}{suffix}"
                        id_mapping[old_id] = new_id
                        gen_n["id"] = new_id
                        
                        # Spread nodes apart using the original node's position and a random Y offset
                        base_pos = orig_node_pos.get(old_id, {})
                        if isinstance(base_pos, dict) and "x" in base_pos and "y" in base_pos:
                            y_shift = random.randint(150, 350)
                            gen_n["position"] = {"x": base_pos.get("x", 0), "y": base_pos.get("y", 0) + y_shift}
                        else:
                            gen_n["position"] = {"x": 50, "y": 100 + random.randint(50, 200)}
                    else:
                        id_mapping[old_id] = old_id
                        if "position" not in gen_n:
                            gen_n["position"] = {"x": 50, "y": 100 + random.randint(50, 200)}
                            
                    if "data" not in gen_n:
                        gen_n["data"] = {"name": gen_n.get("id", "Node"), "color": "#10b981", "childCount": 0, "isCollapsed": False}
                    
                    new_nodes.append(gen_n)
                
                new_edges = []
                for gen_e in payload.get("edges", []):
                    # AI might use from_node and to_node instead of source and target
                    if "source" not in gen_e and "from_node" in gen_e:
                        gen_e["source"] = gen_e.pop("from_node")
                    if "target" not in gen_e and "to_node" in gen_e:
                        gen_e["target"] = gen_e.pop("to_node")
                        
                    old_source = str(gen_e.get("source"))
                    old_target = str(gen_e.get("target"))
                    
                    # Remap source and target
                    gen_e["source"] = id_mapping.get(old_source, old_source)
                    gen_e["target"] = id_mapping.get(old_target, old_target)
                    gen_e["id"] = f"{gen_e.get('id', f'edge_{uuid.uuid4().hex[:4]}')}{suffix}"
                    gen_e["type"] = "buttonedge"
                    gen_e["animated"] = True
                    gen_e["markerEnd"] = {"type": "arrowclosed"}
                    new_edges.append(gen_e)
                    
                ai_custom_nodes = [n for n in new_nodes if n.get("type", "custom") != "startNode" and n.get("id") != "start"]
                
                # Combine edges first
                payload["edges"] = orig_edges_list + new_edges
                
                # Fallback para IAs preguiçosas que omitem as arestas (edges):
                # Se um nó não tiver aresta de entrada, conecte-o ao nó anterior da lista.
                # Se for o primeiro nó, conecte-o ao START.
                connected_targets = {e.get("target") for e in payload["edges"]}
                for i, n in enumerate(new_nodes):
                    nid = n.get("id")
                    if n.get("type", "custom") != "startNode" and nid != "start":
                        if nid not in connected_targets:
                            # Se for o primeiro nó, ou o nó anterior for startNode, liga no start
                            if i == 0 or new_nodes[i-1].get("id") == "start":
                                source_id = "start"
                            else:
                                source_id = new_nodes[i-1]["id"]
                                
                            payload["edges"].append({
                                "id": f"edge_auto_{uuid.uuid4().hex[:6]}",
                                "source": source_id,
                                "target": nid,
                                "type": "buttonedge",
                                "animated": True,
                                "markerEnd": {"type": "arrowclosed"}
                            })
                            connected_targets.add(nid)

                new_card_data = {}
                ai_card_keys = list(payload.get("cardData", {}).keys())
                
                # Check if AI used placeholder keys like 'node_id' and 'node_id_2'
                hallucinated_keys = [k for k in ai_card_keys if k not in orig_node_ids and k not in id_mapping]
                
                # 1. Prioritize hallucinated keys (which usually contain the actual AI mutations)
                for idx, k in enumerate(hallucinated_keys):
                    if idx < len(ai_custom_nodes):
                        new_key = ai_custom_nodes[idx]["id"]
                        new_card_data[new_key] = payload["cardData"][k]
                
                # 2. Map standard keys for any nodes that weren't covered by hallucinated keys
                for k in ai_card_keys:
                    if k not in hallucinated_keys:
                        new_key = id_mapping.get(str(k), str(k))
                        if new_key not in new_card_data:
                            new_card_data[new_key] = payload["cardData"][k]
                        
                # Force the first generated custom node to adopt the alternative scenario's name
                # This fixes the issue where the AI forgets to rename the node inside cardData
                scenario_name = payload.get("name")
                if scenario_name and ai_custom_nodes:
                    first_node_id = ai_custom_nodes[0]["id"]
                    if first_node_id in new_card_data:
                        new_card_data[first_node_id]["name"] = scenario_name
                        
                # Update node names from cardData so UI shows them properly instead of just IDs
                for n in new_nodes:
                    if n["id"] in new_card_data and "name" in new_card_data[n["id"]]:
                        n["data"]["name"] = new_card_data[n["id"]]["name"]
                
                # Combine nodes
                payload["nodes"] = orig_nodes_list + new_nodes
                orig_card_data.update(new_card_data)
                payload["cardData"] = orig_card_data
             
        # Normalize headers/params to match ApiCallSchema
        if "cardData" in payload:
            _normalize_card_data(payload["cardData"])
             
        # The AI returns a structure compatible with FlowSaveSchema
        save_schema = FlowSaveSchema(**payload)
        result = FlowService.save(db, save_schema, company_id, user_id)
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to save generated flow: {str(e)}")

from pydantic import BaseModel
class FeedbackSchema(BaseModel):
    suggestion_name: str
    reason: str

@router.post("/flow/{flow_id}/feedback")
def submit_ai_feedback(flow_id: int, payload: FeedbackSchema, user_id: int = Query(...)):
    import os
    import json
    from app.services.skill_service import SkillService
    
    memory_dir = os.path.join(SkillService.SKILLS_DIR, "memory")
    os.makedirs(memory_dir, exist_ok=True)
    
    feedback_file = os.path.join(memory_dir, f"feedback_flow_{flow_id}.json")
    
    feedbacks = []
    if os.path.exists(feedback_file):
        try:
            with open(feedback_file, "r", encoding="utf-8") as f:
                feedbacks = json.load(f)
        except:
            feedbacks = []
            
    feedbacks.append({
        "suggestion_name": payload.suggestion_name,
        "reason": payload.reason,
        "timestamp": __import__("datetime").datetime.utcnow().isoformat()
    })
    
    # Keep only the last 20 feedbacks to avoid prompt overflow
    if len(feedbacks) > 20:
        feedbacks = feedbacks[-20:]
        
    with open(feedback_file, "w", encoding="utf-8") as f:
        json.dump(feedbacks, f, indent=4)
        
    return {"status": "success", "message": "Feedback saved."}
