from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional, List
from app.database import get_db
from app.services.skill_service import SkillService
from app.services.ai_task_manager import ai_task_manager
from app.services.analysis_service import AnalysisService
from app.auth import get_current_user
from app.models.user_models import UserDB
import uuid
import json
import random
import re
import copy
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis/skills", tags=["Analysis Skills"])

def _resolve_user_and_company(
    current_user: UserDB,
    user_id: Optional[int] = None,
    company_id: Optional[int] = None
) -> tuple[int, int]:
    """
    Validates that current_user is authorized for the requested user_id and company_id,
    preventing BOLA (Broken Object Level Authorization).
    Non-admin users can only operate on their own behalf and within their own organization.
    """
    eff_user_id = current_user.id
    if user_id is not None and user_id != current_user.id:
        if current_user.role != "admin":
            raise HTTPException(
                status_code=403, 
                detail="Acesso não autorizado: você não tem permissão para operar em nome de outro usuário."
            )
        eff_user_id = user_id

    eff_company_id = current_user.company_id
    if company_id is not None and company_id != current_user.company_id:
        if current_user.role != "admin":
            raise HTTPException(
                status_code=403, 
                detail="Acesso não autorizado: você não tem permissão para operar em outra organização."
            )
        eff_company_id = company_id

    return eff_user_id, eff_company_id

@router.get("/debug_settings")
def debug_settings(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")
    from app.models.agent_models import AgentSettingsDB
    settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == current_user.id).first()
    if not settings:
        return {"configured": False}
    masked_key = f"{settings.ai_api_key[:3]}...{settings.ai_api_key[-3:]}" if settings.ai_api_key and len(settings.ai_api_key) > 6 else ("***" if settings.ai_api_key else None)
    return {
        "user_id": settings.user_id,
        "provider": settings.ai_provider,
        "model": settings.ai_model,
        "key_configured": bool(settings.ai_api_key),
        "key_masked": masked_key,
        "url": settings.ai_base_url,
        "ai_enabled": settings.ai_enabled
    }

@router.get("/dump-flow")
def dump_flow(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")
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
def clean_ghosts(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")
    from app.models.flow_models import FlowDB, FlowNodeDB, FlowCardDataDB, FlowEdgeDB, FlowE2EStepDB
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
def debug_db(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")
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
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Returns the list of available specialist skills dynamically from skills directory."""
    import os
    import json
    skills = []
    skills_dir = SkillService.SKILLS_DIR
    if os.path.exists(skills_dir):
        for fname in sorted(os.listdir(skills_dir)):
            if fname.endswith(".json") and not fname.startswith("."):
                fpath = os.path.join(skills_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if "id" in data and "name" in data:
                            skills.append({
                                "id": data["id"],
                                "name": data["name"],
                                "description": data.get("description", "")
                            })
                except Exception as e:
                    pass
    if not skills:
        skills = [
            { "id": "qa_specialist_api", "name": "Especialista em API", "description": "Automação e testes de Backend/API." },
            { "id": "qa_specialist_web", "name": "Especialista em Web E2E", "description": "Automação e testes de Frontend Web." },
            { "id": "qa_specialist_mobile", "name": "Especialista em Mobile", "description": "Automação e testes Mobile (Appium)." },
            { "id": "generate_alternatives_skill", "name": "Geração de Alternativas", "description": "Cria variações de teste." }
        ]
    return skills

@router.post("/flow/{flow_id}/execute/{skill_id}")
def execute_skill(
    flow_id: int,
    skill_id: str,
    user_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Executes a generic skill (returns text/insights) with proper flow pruning and context."""
    eff_user_id, eff_company_id = _resolve_user_and_company(current_user, user_id, company_id)
    try:
        from app.models.flow_models import FlowDB
        from app.services.flow_service import FlowService
        
        flow_db = db.query(FlowDB).filter(FlowDB.id == flow_id).first()
        if not flow_db:
            raise HTTPException(status_code=404, detail="Fluxo não encontrado.")
            
        flow_type = flow_db.flow_type or "api"
        flow_data = FlowService.load(db, flow_db.project_id, eff_company_id, flow_id, flow_type)
        pruned_data = SkillService._prune_flow_for_llm(flow_data)
        blueprint = SkillService.build_flow_blueprint(flow_db.name, flow_type, pruned_data)

        nodes = pruned_data.get("nodes", [])
        cardData = pruned_data.get("cardData", {})
        mapping_payload = {"nodes": nodes, "cardData": cardData, "flow_type": flow_type}

        generation_context = {
            "blueprint": json.dumps(blueprint, ensure_ascii=False),
            "nodes": nodes,
            "cardData": cardData,
            "mapping_context": json.dumps(mapping_payload, ensure_ascii=False),
            "flow_type": flow_type,
            "project_id": flow_db.project_id
        }
        
        # Analytical skills run in isolated one-shot mode (flow_id=None) to prevent conversation memory pollution
        result = SkillService.execute_skill(db, eff_user_id, skill_id, generation_context, flow_id=None)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error executing skill {skill_id} on flow {flow_id}: {e}", exc_info=True)
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
    user_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Executes a chat message within a skill context, with explicit session memory and clean pruning."""
    eff_user_id, eff_company_id = _resolve_user_and_company(current_user, user_id, company_id)
    try:
        from app.models.flow_models import FlowDB
        from app.services.flow_service import FlowService
        
        flow_db = db.query(FlowDB).filter(FlowDB.id == flow_id).first()
        flow_type = flow_db.flow_type if flow_db else "e2e"
        
        # Use live context from frontend if available, else load from DB
        if req.live_context and "nodes" in req.live_context:
            raw_flow = {
                "nodes": req.live_context.get("nodes", []),
                "edges": req.live_context.get("edges", []),
                "cardData": req.live_context.get("cardData", {}),
                "flow_type": req.live_context.get("flow_type") or flow_type
            }
            flow_type = raw_flow["flow_type"]
            pruned_data = SkillService._prune_flow_for_llm(raw_flow)
        else:
            flow_data = FlowService.load(db, flow_db.project_id if flow_db else None, eff_company_id, flow_id, flow_type)
            pruned_data = SkillService._prune_flow_for_llm(flow_data)

        nodes = pruned_data.get("nodes", [])
        cardData = pruned_data.get("cardData", {})
        flow_name = flow_db.name if flow_db else "Flow"
        blueprint = SkillService.build_flow_blueprint(flow_name, flow_type, pruned_data)

        mapping_payload = {"nodes": nodes, "cardData": cardData, "flow_type": flow_type}
        if req.live_context and "execution_results" in req.live_context:
            mapping_payload["execution_results"] = req.live_context["execution_results"]

        generation_context = {
            "blueprint": json.dumps(blueprint, ensure_ascii=False),
            "mapping_context": json.dumps(mapping_payload, ensure_ascii=False),
            "nodes": nodes,
            "cardData": cardData,
            "flow_type": flow_type,
            "user_message": req.message
        }
        
        # We pass flow_id=None to disable automatic database memory injection,
        # because we are passing the explicit `history` from the frontend session.
        result = SkillService.execute_skill(
            db, 
            eff_user_id, 
            skill_id, 
            generation_context, 
            flow_id=None, 
            chat_history=req.history
        )
        return {"response": result}
    except Exception as e:
        logger.error(f"Error in chat_with_skill: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/flow/{flow_id}/generate-alternatives")
def generate_alternatives(
    flow_id: int,
    user_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Executes the specialist mapping -> generation workflow for a specific flow."""
    eff_user_id, eff_company_id = _resolve_user_and_company(current_user, user_id, company_id)
    try:
        alternatives = SkillService.generate_alternatives_workflow(db, eff_user_id, flow_id, eff_company_id)
        return alternatives
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Error generating alternatives for flow {flow_id}: {e}", exc_info=True)
        err_msg = str(e)
        if "Read timed out" in err_msg or "timeout" in err_msg.lower():
            raise HTTPException(
                status_code=504,
                detail="O servidor de IA demorou muito para responder (Timeout). Tente novamente ou verifique se o serviço de IA está ativo."
            )
        raise HTTPException(status_code=500, detail=err_msg)

@router.get("/user-active-task")
def get_user_active_task(
    user_id: Optional[int] = Query(None),
    current_user: UserDB = Depends(get_current_user)
):
    """Returns any active (PENDING or PROCESSING) AI task for the user."""
    eff_user_id, _ = _resolve_user_and_company(current_user, user_id, None)
    active_task = ai_task_manager.get_active_task_for_user(eff_user_id)
    return {"active_task": active_task}

@router.get("/tasks/{task_id}")
def get_ai_task_status(
    task_id: str,
    current_user: UserDB = Depends(get_current_user)
):
    """Returns status and result of a specific AI task."""
    task = ai_task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task de IA não encontrada.")
    if task.get("user_id") != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Acesso não autorizado a esta tarefa de IA.")
    return {"task": task}

@router.post("/tasks/{task_id}/cancel")
def cancel_ai_task(
    task_id: str,
    user_id: Optional[int] = Query(None),
    current_user: UserDB = Depends(get_current_user)
):
    """Cancels a pending or processing AI task for a user."""
    eff_user_id, _ = _resolve_user_and_company(current_user, user_id, None)
    task = ai_task_manager.get_task(task_id)
    if task and task.get("user_id") != eff_user_id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Acesso não autorizado para cancelar esta tarefa.")
    success = ai_task_manager.cancel_task(task_id, eff_user_id)
    if not success:
        raise HTTPException(status_code=400, detail="Não foi possível cancelar a task (não encontrada ou já finalizada).")
    return {"status": "cancelled", "message": "Solicitação cancelada pelo usuário."}

@router.post("/flow/{flow_id}/generate-alternatives-async")
def generate_alternatives_async(
    flow_id: int,
    user_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    current_user: UserDB = Depends(get_current_user)
):
    """Initiates asynchronous alternatives generation in a background worker."""
    eff_user_id, eff_company_id = _resolve_user_and_company(current_user, user_id, company_id)

    def _worker(db, u_id, f_id, c_id):
        return SkillService.generate_alternatives_workflow(db, u_id, f_id, c_id)

    task = ai_task_manager.start_task(
        eff_user_id,
        flow_id,
        "generate_alternatives_skill",
        _worker,
        eff_user_id,
        flow_id,
        eff_company_id
    )
    return task

@router.post("/flow/{flow_id}/execute-async/{skill_id}")
def execute_skill_async(
    flow_id: int,
    skill_id: str,
    user_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Initiates asynchronous generic skill execution in a background worker with proper flow pruning and context."""
    eff_user_id, eff_company_id = _resolve_user_and_company(current_user, user_id, company_id)
    from app.models.flow_models import FlowDB
    from app.services.flow_service import FlowService
    
    flow_db = db.query(FlowDB).filter(FlowDB.id == flow_id).first()
    if not flow_db:
        raise HTTPException(status_code=404, detail="Fluxo não encontrado.")

    flow_type = flow_db.flow_type or "api"
    flow_data = FlowService.load(db, flow_db.project_id, eff_company_id, flow_id, flow_type)
    pruned_data = SkillService._prune_flow_for_llm(flow_data)
    blueprint = SkillService.build_flow_blueprint(flow_db.name, flow_type, pruned_data)

    nodes = pruned_data.get("nodes", [])
    cardData = pruned_data.get("cardData", {})
    mapping_payload = {"nodes": nodes, "cardData": cardData, "flow_type": flow_type}

    generation_context = {
        "blueprint": json.dumps(blueprint, ensure_ascii=False),
        "nodes": nodes,
        "cardData": cardData,
        "mapping_context": json.dumps(mapping_payload, ensure_ascii=False),
        "flow_type": flow_type,
        "project_id": flow_db.project_id
    }

    def _worker(db_session, u_id, sk_id, ctx, f_id):
        # Isolate analytical one-shot background skills from conversational history (flow_id=None)
        raw_res = SkillService.execute_skill(db_session, u_id, sk_id, ctx, flow_id=None)
        parsed_res = raw_res
        if isinstance(raw_res, str) and ('{' in raw_res or '[' in raw_res):
            try:
                from app.services.skill_service import _robust_json_parse
                parsed_res = _robust_json_parse(raw_res)
            except Exception:
                try:
                    parsed_res = json.loads(raw_res)
                except Exception:
                    pass
        return parsed_res

    task = ai_task_manager.start_task(
        eff_user_id,
        flow_id,
        skill_id,
        _worker,
        eff_user_id,
        skill_id,
        generation_context,
        flow_id
    )
    return task

def _normalize_card_data(card_data: dict):
    """Normalizes API calls and E2E steps to match Pydantic schema."""
    if not isinstance(card_data, dict):
        return

    for card_id, cd in list(card_data.items()):
        if not isinstance(cd, dict):
            cd = {}
            card_data[card_id] = cd
        
        # 1. Normalize E2E Steps
        if "steps" in cd and "e2eSteps" not in cd:
            cd["e2eSteps"] = cd.pop("steps")
        if "e2e_steps" in cd and "e2eSteps" not in cd:
            cd["e2eSteps"] = cd.pop("e2e_steps")
            
        e2e_steps = cd.get("e2eSteps")
        if isinstance(e2e_steps, list):
            import uuid
            node_type = cd.get("nodeType") or ""
            for idx, step in enumerate(e2e_steps):
                if not isinstance(step, dict): continue
                if "id" not in step:
                    step["id"] = f"ai-step-{uuid.uuid4().hex[:6]}"
                
                # Resolve true action type
                step_type = step.get("type")
                step_action = step.get("action")
                if not step_action and isinstance(step.get("properties"), dict):
                    step_action = step.get("properties", {}).get("action")
                
                if not step_type or step_type in ["action", "step", "custom", "interaction"]:
                    if step_action and step_action not in ["action", "step", "custom"]:
                        step_type = step_action
                    elif node_type == "mobile":
                        step_type = "tap"
                    else:
                        step_type = "click"
                step["type"] = step_type

                if "properties" not in step or not isinstance(step.get("properties"), dict):
                    step["properties"] = {}
                props = step["properties"]
                
                # Move hallucinated root properties into the 'properties' dictionary
                for k in ["selector", "android_selector", "ios_selector", "value", "operator", "direction", "distance", "timeout", "url", "clear_first", "hide_keyboard"]:
                    if k in step and k not in props:
                        props[k] = step.pop(k)
                if "text" in step and "value" not in props:
                    props["value"] = step.pop("text")

                # For mobile, keep selector and android_selector synchronized
                if node_type == "mobile" or "mobile" in card_id.lower() or "mobile" in step_type.lower():
                    if props.get("selector") and not props.get("android_selector"):
                        props["android_selector"] = props["selector"]
                    elif props.get("android_selector") and not props.get("selector"):
                        props["selector"] = props["android_selector"]

                # Ensure a human-readable, non-empty semantic name
                current_name = (step.get("name") or "").strip()
                if not current_name or current_name.lower() in [f"passo {idx+1}", "passo 1", "passo", "action", "step"]:
                    target_sel = props.get("android_selector") or props.get("selector") or ""
                    val = props.get("value") or ""
                    if step_type == "type" and val:
                        step["name"] = f"Digitar '{val}'" + (f" em {target_sel}" if target_sel else "")
                    elif step_type == "tap" and target_sel:
                        step["name"] = f"Tocar em {target_sel}"
                    elif step_type == "click" and target_sel:
                        step["name"] = f"Clicar em {target_sel}"
                    elif step_type == "assert":
                        step["name"] = f"Validar {val or target_sel or 'Elemento'}"
                    elif step_type == "swipe":
                        step["name"] = f"Deslizar tela ({props.get('direction', 'up')})"
                    elif step_type == "browser" and props.get("url"):
                        step["name"] = f"Navegar para {props.get('url')}"
                    elif not current_name:
                        step["name"] = f"Passo {idx+1} ({step_type.upper()})"
                
        # 2. Normalize API Calls
        all_api_calls_lists = []
        if "apiCalls" in cd:
            api_calls = cd.get("apiCalls")
            if isinstance(api_calls, list):
                all_api_calls_lists.append(api_calls)
            
        env_data = cd.get("envData")
        if isinstance(env_data, dict):
            for env_name, env_details in env_data.items():
                if isinstance(env_details, dict) and "apiCalls" in env_details:
                    env_calls = env_details.get("apiCalls")
                    if isinstance(env_calls, list):
                        all_api_calls_lists.append(env_calls)
        
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

                if "assertions" in api_call and isinstance(api_call.get("assertions"), list):
                    for ast in api_call["assertions"]:
                        if isinstance(ast, dict):
                            if "source" not in ast: ast["source"] = "statusCode"
                            if "operator" not in ast: ast["operator"] = "equals"
                            if "target" not in ast: ast["target"] = "200"

                if "extracts" in api_call and isinstance(api_call.get("extracts"), list):
                    for ext in api_call["extracts"]:
                        if isinstance(ext, dict):
                            if "variableName" in ext and "variable" not in ext:
                                ext["variable"] = ext.pop("variableName")
                            if "pathSelector" in ext and "property" not in ext:
                                ext["property"] = ext.pop("pathSelector")
                            if "source" not in ext: ext["source"] = "body"
                            if "variable" not in ext: ext["variable"] = "VAR"

def _find_best_matching_orig_node(gen_n: dict, gen_card_data: dict, orig_custom_nodes: list, scenario_name: str = "") -> dict:
    """Matches an AI-generated node to the most relevant original custom node."""
    if not orig_custom_nodes:
        return None
    gen_id = str(gen_n.get("id"))
    for o_node in orig_custom_nodes:
        if str(o_node.get("id")) == gen_id:
            return o_node
            
    gen_data = gen_n.get("data") if isinstance(gen_n.get("data"), dict) else {}
    node_name = gen_data.get("name") or ""
    card_name = ""
    if isinstance(gen_card_data, dict) and gen_id in gen_card_data and isinstance(gen_card_data[gen_id], dict):
        card_name = gen_card_data[gen_id].get("name") or ""
        
    combined_name = f"{scenario_name} {node_name} {card_name}".lower()
    
    best_score = 0
    best_match = None
    
    for o_node in orig_custom_nodes:
        o_data = o_node.get("data") if isinstance(o_node.get("data"), dict) else {}
        o_name = (o_data.get("name") or str(o_node.get("id"))).lower()
        
        o_tokens = [t for t in re.split(r'\W+', o_name) if len(t) > 2]
        score = 0
        for token in o_tokens:
            if token in combined_name:
                score += len(token) * 2
            elif len(token) >= 4 and token[:5] in combined_name:
                score += len(token)
                
        if score > best_score:
            best_score = score
            best_match = o_node
            
    if best_match:
        return best_match
    return orig_custom_nodes[0]


@router.post("/save-generated-flow")
def save_generated_flow(
    payload: Dict[str, Any],
    user_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    flow_id: int = Query(None),
    parent_node_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Saves a flow that was generated by the AI skill."""
    eff_user_id, eff_company_id = _resolve_user_and_company(current_user, user_id, company_id)
    from app.services.flow_service import FlowService
    from app.schemas.flow_schemas import FlowSaveSchema
    from app.models.flow_models import FlowDB
    
    try:
        if not isinstance(payload, dict):
            payload = {}
            
        explicit_parent_id = payload.get("parent_node_id") or parent_node_id
        if explicit_parent_id:
            explicit_parent_id = str(explicit_parent_id)

        gen_nodes = payload.get("nodes")
        if not isinstance(gen_nodes, list):
            gen_nodes = []
        payload["nodes"] = gen_nodes
        
        gen_edges = payload.get("edges")
        if not isinstance(gen_edges, list):
            gen_edges = []
        payload["edges"] = gen_edges
        
        gen_card_data = payload.get("cardData")
        if not isinstance(gen_card_data, dict):
            gen_card_data = {}
        payload["cardData"] = gen_card_data

        if flow_id:
            flow = db.query(FlowDB).filter(FlowDB.id == flow_id).first()
            if flow:
                payload["projectId"] = flow.project_id
                payload["flow_type"] = flow.flow_type
        
        if "projectId" not in payload or not payload["projectId"]:
             payload["projectId"] = 1 # Fallback safe project_id
             
        if "flow_type" not in payload or not payload["flow_type"]:
             payload["flow_type"] = "api"
             
        # Normalize flow_type web to e2e
        if payload["flow_type"] == "web":
             payload["flow_type"] = "e2e"
             
        # Merge with original flow to prevent data loss and ADD AS NEW BRANCHES
        if flow_id:
            original_flow = FlowService.load(db, payload["projectId"], company_id, flow_id, payload["flow_type"])
            if original_flow and isinstance(original_flow, dict):
                orig_nodes_list = copy.deepcopy(original_flow.get("nodes") or [])
                if not isinstance(orig_nodes_list, list): orig_nodes_list = []
                
                orig_edges_list = copy.deepcopy(original_flow.get("edges") or [])
                if not isinstance(orig_edges_list, list): orig_edges_list = []
                
                orig_card_data = copy.deepcopy(original_flow.get("cardData") or {})
                if not isinstance(orig_card_data, dict): orig_card_data = {}
                
                orig_node_ids = {str(n.get("id")) for n in orig_nodes_list if isinstance(n, dict) and n.get("id")}
                orig_custom_nodes = [n for n in orig_nodes_list if isinstance(n, dict) and n.get("type", "custom") != "startNode" and n.get("id") != "start"]
                
                # Map original node IDs to their incoming source parents in the original flow
                orig_incoming_sources = {}
                for oe in orig_edges_list:
                    if isinstance(oe, dict) and oe.get("source") and oe.get("target"):
                        orig_incoming_sources.setdefault(str(oe["target"]), []).append(str(oe["source"]))

                id_mapping = {}
                suffix = f"_alt_{uuid.uuid4().hex[:6]}"
                orig_node_pos = {str(n.get("id")): n.get("position", {}) for n in orig_nodes_list if isinstance(n, dict) and n.get("id")}
                has_valid_explicit_parent = explicit_parent_id and (explicit_parent_id in orig_node_ids or explicit_parent_id == "start")

                new_nodes = []
                node_to_matched_orig = {}
                for i, gen_n in enumerate(payload.get("nodes", [])):
                    if not isinstance(gen_n, dict):
                        continue
                    old_id = str(gen_n.get("id"))
                    if old_id == "start":
                        id_mapping["start"] = "start"
                        continue  # Skip adding a duplicate start node

                    if has_valid_explicit_parent:
                        matched_orig_id = explicit_parent_id
                    else:
                        matched_orig = _find_best_matching_orig_node(gen_n, gen_card_data, orig_custom_nodes, scenario_name=payload.get("name", ""))
                        matched_orig_id = str(matched_orig.get("id")) if matched_orig else old_id

                    # Assign globally unique ID for each generated node to prevent collisions
                    new_id = f"node_{uuid.uuid4().hex[:8]}"
                    id_mapping[old_id] = new_id
                    gen_n["id"] = new_id
                    node_to_matched_orig[new_id] = matched_orig_id

                    # Compute relative position:
                    # First node placed relative to matched_orig_id (below existing siblings), subsequent nodes horizontally
                    if i == 0 or len(new_nodes) == 0:
                        base_pos = orig_node_pos.get(matched_orig_id, {"x": 100, "y": 100})
                        siblings_count = sum(1 for oe in orig_edges_list if isinstance(oe, dict) and str(oe.get("source")) == matched_orig_id)
                        y_offset = (siblings_count + 1) * 140
                        gen_n["position"] = {
                            "x": base_pos.get("x", 100) + 280,
                            "y": base_pos.get("y", 100) + y_offset
                        }
                    else:
                        prev_pos = new_nodes[-1].get("position", {"x": 100, "y": 100})
                        gen_n["position"] = {
                            "x": prev_pos.get("x", 100) + 280,
                            "y": prev_pos.get("y", 100)
                        }

                    gen_n["type"] = "startNode" if (gen_n.get("id") == "start" or gen_n.get("type") == "startNode") else "custom"
                    if "data" not in gen_n or not isinstance(gen_n.get("data"), dict):
                        gen_n["data"] = {"name": gen_n.get("id", "Node"), "color": "#10b981", "childCount": 0, "isCollapsed": False, "nodeType": payload["flow_type"]}
                    else:
                        gen_n["data"]["nodeType"] = gen_n["data"].get("nodeType") or payload["flow_type"]

                    new_nodes.append(gen_n)

                ai_custom_nodes = [n for n in new_nodes if n.get("type", "custom") != "startNode" and n.get("id") != "start"]
                all_valid_node_ids = orig_node_ids | {str(n.get("id")) for n in new_nodes} | {"start"}

                new_edges = []
                connected_new_targets = set()

                for gen_e in payload.get("edges", []):
                    if not isinstance(gen_e, dict):
                        continue
                    if "source" not in gen_e and "from_node" in gen_e:
                        gen_e["source"] = gen_e.pop("from_node")
                    if "target" not in gen_e and "to_node" in gen_e:
                        gen_e["target"] = gen_e.pop("to_node")

                    old_source = str(gen_e.get("source"))
                    old_target = str(gen_e.get("target"))

                    target_id = id_mapping.get(old_target, old_target)
                    if target_id not in all_valid_node_ids:
                        continue

                    if old_source in id_mapping and id_mapping[old_source] != target_id:
                        source_id = id_mapping[old_source]
                    elif has_valid_explicit_parent:
                        source_id = explicit_parent_id
                    elif old_source in orig_node_ids or old_source == "start":
                        source_id = old_source
                    else:
                        source_id = "start"

                    if source_id in all_valid_node_ids and source_id != target_id:
                        gen_e["source"] = source_id
                        gen_e["target"] = target_id
                        gen_e["id"] = f"edge_{uuid.uuid4().hex[:8]}"
                        gen_e["type"] = "buttonedge"
                        gen_e["animated"] = True
                        gen_e["markerEnd"] = {"type": "arrowclosed"}
                        new_edges.append(gen_e)
                        connected_new_targets.add(target_id)

                # Ensure every custom generated node has a valid incoming edge
                for i, n in enumerate(ai_custom_nodes):
                    nid = n.get("id")
                    if nid not in connected_new_targets:
                        if i == 0:
                            source_id = explicit_parent_id if has_valid_explicit_parent else "start"
                        else:
                            source_id = ai_custom_nodes[i - 1]["id"]

                        if source_id in all_valid_node_ids and source_id != nid:
                            new_edges.append({
                                "id": f"edge_auto_{uuid.uuid4().hex[:6]}",
                                "source": source_id,
                                "target": nid,
                                "type": "buttonedge",
                                "animated": True,
                                "markerEnd": {"type": "arrowclosed"}
                            })
                            connected_new_targets.add(nid)

                # Combine edges and deduplicate by (source, target), filtering out invalid/ghost references
                seen_edge_pairs = set()
                combined_edges = []
                for e in (orig_edges_list + new_edges):
                    if not isinstance(e, dict):
                        continue
                    s = str(e.get("source"))
                    t = str(e.get("target"))
                    if s in all_valid_node_ids and t in all_valid_node_ids and s != t:
                        pair = (s, t)
                        if pair not in seen_edge_pairs:
                            seen_edge_pairs.add(pair)
                            combined_edges.append(e)

                payload["edges"] = combined_edges

                new_card_data = {}
                ai_card_data = payload.get("cardData") or {}
                if not isinstance(ai_card_data, dict):
                    ai_card_data = {}
                payload["cardData"] = ai_card_data
                ai_card_keys = list(ai_card_data.keys())
                
                # Map card data for each generated node, inheriting from matched original card
                for n in new_nodes:
                    if n.get("type", "custom") != "startNode" and n.get("id") != "start":
                        n_id = n.get("id")
                        old_ai_id = next((k for k, v in id_mapping.items() if v == n_id), None)
                        matched_orig_id = (
                            node_to_matched_orig.get(n_id)
                            or next((k for k, v in id_mapping.items() if v == n_id and k in orig_card_data), None)
                        )
                        if not matched_orig_id and suffix in n_id:
                            matched_orig_id = n_id.split(suffix)[0]
                        if not matched_orig_id and has_valid_explicit_parent:
                            matched_orig_id = explicit_parent_id

                        # AI provided card data for this node (by AI key, node name, or scenario name)
                        node_name = n.get("data", {}).get("name") or ""
                        ai_c = ai_card_data.get(n_id) or (ai_card_data.get(old_ai_id) if old_ai_id else None) or ai_card_data.get(node_name)
                        if not ai_c:
                            for k, v in ai_card_data.items():
                                if isinstance(v, dict) and v.get("name") == node_name:
                                    ai_c = v
                                    break
                        if not isinstance(ai_c, dict):
                            ai_c = {}

                        # Original card data to inherit from
                        orig_c = copy.deepcopy(orig_card_data.get(matched_orig_id, {})) if (matched_orig_id and isinstance(orig_card_data.get(matched_orig_id), dict)) else {}

                        # Combine AI mutations over original card data
                        combined_card = copy.deepcopy(orig_c)
                        combined_card.update(ai_c)
                        
                        # Preserve/Inherit name
                        combined_card["name"] = ai_c.get("name") or node_name or combined_card.get("name") or payload.get("name", "Node")
                        
                        new_card_data[n_id] = combined_card

                # 3. Recover missing E2E/API steps and properties, set nodeType
                for new_key, card in list(new_card_data.items()):
                    if not isinstance(card, dict):
                        card = {}
                        new_card_data[new_key] = card

                    card["nodeType"] = card.get("nodeType") or payload["flow_type"]
                    
                    matched_orig_id = (
                        node_to_matched_orig.get(new_key)
                        or next((k for k, v in id_mapping.items() if v == new_key and k in orig_card_data), None)
                    )
                    if not matched_orig_id and suffix in new_key:
                        matched_orig_id = new_key.split(suffix)[0]
                    if not matched_orig_id and has_valid_explicit_parent:
                        matched_orig_id = explicit_parent_id
                        
                    orig_c = orig_card_data.get(matched_orig_id, {}) if (matched_orig_id and isinstance(orig_card_data.get(matched_orig_id), dict)) else {}

                    # E2E / Mobile / Web Flow Steps
                    if payload["flow_type"] in ["mobile", "e2e", "web"]:
                        if "e2eSteps" not in card:
                            if "steps" in card:
                                card["e2eSteps"] = card.pop("steps")
                            elif "e2e_steps" in card:
                                card["e2eSteps"] = card.pop("e2e_steps")
                            elif "apiCalls" in card:
                                card["e2eSteps"] = card.pop("apiCalls")

                        orig_steps = orig_c.get("e2eSteps") or orig_c.get("steps") or orig_c.get("e2e_steps") or []
                        new_steps = card.get("e2eSteps") or card.get("steps") or card.get("e2e_steps") or []
                        
                        if not new_steps and orig_steps:
                            card["e2eSteps"] = copy.deepcopy(orig_steps)
                            new_steps = card["e2eSteps"]
                        
                        if isinstance(new_steps, list) and isinstance(orig_steps, list):
                            for i, n_step in enumerate(new_steps):
                                if not isinstance(n_step, dict): continue
                                n_props = n_step.get("properties")
                                if not isinstance(n_props, dict):
                                    n_step["properties"] = {}
                                    n_props = n_step["properties"]

                                if i < len(orig_steps) and isinstance(orig_steps[i], dict):
                                    o_step = orig_steps[i]
                                    o_props = o_step.get("properties") or {}

                                    if not n_step.get("type") or n_step.get("type") in ["action", "step", "custom"]:
                                        n_step["type"] = o_step.get("type") or o_step.get("action") or ("tap" if payload["flow_type"] == "mobile" else "click")

                                    o_selector = o_props.get("selector") if isinstance(o_props, dict) else None
                                    o_android = o_props.get("android_selector") if isinstance(o_props, dict) else None
                                    o_ios = o_props.get("ios_selector") if isinstance(o_props, dict) else None

                                    if o_selector and "selector" not in n_step and "selector" not in n_props:
                                        n_props["selector"] = o_selector
                                    if o_android and "android_selector" not in n_step and "android_selector" not in n_props:
                                        n_props["android_selector"] = o_android
                                    if o_ios and "ios_selector" not in n_step and "ios_selector" not in n_props:
                                        n_props["ios_selector"] = o_ios

                    # API Flow Calls
                    elif payload["flow_type"] == "api":
                        if "apiCalls" not in card:
                            if "steps" in card:
                                card["apiCalls"] = card.pop("steps")
                            elif "e2eSteps" in card:
                                card["apiCalls"] = card.pop("e2eSteps")
                                
                        orig_calls = orig_c.get("apiCalls") or []
                        new_calls = card.get("apiCalls") or []
                        
                        if not new_calls and orig_calls:
                            card["apiCalls"] = copy.deepcopy(orig_calls)

                    if orig_c:
                        if "dbQueries" in orig_c and "dbQueries" not in card:
                            card["dbQueries"] = copy.deepcopy(orig_c["dbQueries"])
                        if "envData" in orig_c and "envData" not in card:
                            card["envData"] = copy.deepcopy(orig_c["envData"])

                # Force the first generated custom node to adopt the alternative scenario's name
                scenario_name = payload.get("name")
                if scenario_name and ai_custom_nodes:
                    first_node_id = ai_custom_nodes[0]["id"]
                    if first_node_id in new_card_data and isinstance(new_card_data[first_node_id], dict):
                        new_card_data[first_node_id]["name"] = scenario_name
                        
                # Update node names from cardData so UI shows them properly instead of just IDs
                for n in new_nodes:
                    if isinstance(n, dict) and n.get("id") in new_card_data:
                        c_dict = new_card_data[n["id"]]
                        if isinstance(c_dict, dict) and "name" in c_dict:
                            if "data" not in n or not isinstance(n.get("data"), dict):
                                n["data"] = {}
                            n["data"]["name"] = c_dict["name"]
                
                # Combine nodes
                payload["nodes"] = orig_nodes_list + new_nodes
                orig_card_data.update(new_card_data)
                payload["cardData"] = orig_card_data
             
        # Normalize headers/params to match ApiCallSchema
        if "cardData" in payload and isinstance(payload["cardData"], dict):
            _normalize_card_data(payload["cardData"])
             
        # Ensure every node in payload["nodes"] has a valid position dict
        if "nodes" in payload and isinstance(payload["nodes"], list):
            for n in payload["nodes"]:
                if isinstance(n, dict):
                    if "position" not in n or not isinstance(n.get("position"), dict) or "x" not in n["position"] or "y" not in n["position"]:
                        n["position"] = {"x": 100, "y": 100}

        # The AI returns a structure compatible with FlowSaveSchema
        save_schema = FlowSaveSchema(**payload)
        result = FlowService.save(db, save_schema, eff_company_id, eff_user_id)

        try:
            from app.models.user_models import UserDB
            from app.services.audit_service import AuditService
            user = db.query(UserDB).filter(UserDB.id == eff_user_id).first()
            if user:
                AuditService.log_action(
                    db=db,
                    company_id=eff_company_id,
                    user=user,
                    action="SAVE_AI_GENERATED_FLOW",
                    resource_type="flow",
                    resource_id=str(flow_id) if flow_id else None,
                    resource_name=payload.get("name") or f"Fluxo Gerado por IA",
                    details={"flow_id": flow_id, "flow_type": payload.get("flow_type")}
                )
        except Exception as audit_err:
            pass

        if isinstance(result, dict):
            first_target_id = new_nodes[0]["id"] if ('new_nodes' in locals() and new_nodes) else None
            result["target_node_id"] = first_target_id
            result["new_node_ids"] = [n["id"] for n in new_nodes] if ('new_nodes' in locals() and new_nodes) else []

        return result
    except Exception as e:
        logger.error(f"Failed to save generated flow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to save generated flow: {str(e)}")

from pydantic import BaseModel
class FeedbackSchema(BaseModel):
    suggestion_name: str
    reason: str

@router.post("/flow/{flow_id}/feedback")
def submit_ai_feedback(
    flow_id: int, 
    payload: FeedbackSchema, 
    user_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    eff_user_id, _ = _resolve_user_and_company(current_user, user_id, None)

    # 1. Persist in database AgentMemoryDB for multi-tenant and multi-instance durability
    try:
        from app.models.agent_models import AgentMemoryDB
        feedback_memory = AgentMemoryDB(
            user_id=eff_user_id,
            flow_id=flow_id,
            role="feedback_rejected",
            content=json.dumps({
                "suggestion_name": payload.suggestion_name,
                "reason": payload.reason
            })
        )
        db.add(feedback_memory)
        db.commit()
    except Exception as db_err:
        logger.error(f"Failed to persist feedback in DB: {db_err}")

    # 2. Persist in local file for backward compatibility
    try:
        import os
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
        if len(feedbacks) > 20:
            feedbacks = feedbacks[-20:]
            
        with open(feedback_file, "w", encoding="utf-8") as f:
            json.dump(feedbacks, f, indent=4)
    except Exception as file_err:
        logger.warning(f"Failed to write local feedback fallback: {file_err}")

    return {"status": "success", "message": "Feedback saved."}
