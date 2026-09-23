from fastapi import APIRouter, Depends, HTTPException
import logging
from sqlalchemy.orm import Session
from app.database import get_db
from app.auth import get_current_user
from app.models.user_models import UserDB
from app.models.agent_models import AgentSettingsDB
from app.schemas.agent_schemas import AgentSettingsUpdate, AgentSettingsResponse
from app.services.agent_service import AgentService
from typing import Optional, Dict, Any, List
from pydantic import BaseModel
from app.services.mcp_playwright_service import MCPPlaywrightService
import os

router = APIRouter(prefix="/agent", tags=["AI Agent"])
logger = logging.getLogger(__name__)

class PlannerRequest(BaseModel):
    url: Optional[str] = ""
    start_mode: Optional[str] = "scratch"
    existing_flow_context: Optional[Dict[str, Any]] = None
    instruction: Optional[str] = None
    start_node_id: Optional[str] = None
    storage_state: Optional[Dict[str, Any]] = None
    cookies: Optional[List[Dict[str, Any]]] = None
    headers: Optional[Dict[str, str]] = None

class HealerRequest(BaseModel):
    flow_id: int
    step_index: int = 0
    error_message: str = ""
    node_id: Optional[str] = None
    failed_selector: Optional[str] = None
    action_type: Optional[str] = None
    target_url: Optional[str] = None
    apply_fix: bool = False

@router.get("/mcp-status")
async def get_mcp_status(
    current_user: UserDB = Depends(get_current_user)
):
    """Returns the current connection status to the Playwright MCP server."""
    return await MCPPlaywrightService.get_mcp_status()

@router.get("/settings", response_model=AgentSettingsResponse)
@router.get("/settings/", response_model=AgentSettingsResponse)
def get_agent_settings(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == current_user.id).first()
    if not settings:
        settings = AgentSettingsDB(user_id=current_user.id)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings

@router.post("/planner")
async def run_planner(
    req: PlannerRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    logger.info(f"POST /agent/planner invoked by user {current_user.id} (mode={req.start_mode}, url='{req.url}')")
    try:
        plan = await AgentService.run_planner(
            db=db, 
            user_id=current_user.id, 
            url=req.url or "",
            start_mode=req.start_mode or "scratch",
            existing_flow_context=req.existing_flow_context,
            instruction=req.instruction,
            start_node_id=req.start_node_id,
            storage_state=req.storage_state,
            cookies=req.cookies,
            headers=req.headers
        )
        node_count = len(plan.get("nodes", [])) if isinstance(plan, dict) else 0
        logger.info(f"POST /agent/planner completed successfully for user {current_user.id}: {node_count} nodes returned")
        return plan
    except Exception as e:
        logger.error(f"Error in Planner route: {e}")
        return {
            "error": f"Planner failed: {str(e)}",
            "nodes": [],
            "edges": [],
            "cardData": {},
            "node_enhancements": []
        }

@router.post("/healer")
async def run_healer(
    req: HealerRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Runs the AI Healer Agent via MCP."""
    try:
        fix = await AgentService.run_healer(
            db=db,
            user_id=current_user.id,
            flow_id=req.flow_id,
            step_index=req.step_index,
            error_message=req.error_message,
            node_id=req.node_id,
            failed_selector=req.failed_selector,
            action_type=req.action_type,
            target_url=req.target_url,
            apply_fix=req.apply_fix
        )
        return fix
    except Exception as e:
        logger.error(f"Error in Healer route: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/settings", response_model=AgentSettingsResponse)
@router.put("/settings/", response_model=AgentSettingsResponse)
def update_agent_settings(
    settings_update: AgentSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == current_user.id).first()
    if not settings:
        settings = AgentSettingsDB(user_id=current_user.id)
        db.add(settings)
    
    # Prevent deleting or modifying the license key if it exists
    target_provider = settings_update.ai_provider if settings_update.ai_provider is not None else settings.ai_provider
    if target_provider == "ollama" and settings.ai_api_key and settings.ai_api_key.startswith("QAFLOW-"):
        if settings_update.ai_api_key is not None and settings_update.ai_api_key != settings.ai_api_key:
            raise HTTPException(
                status_code=400,
                detail="Não é permitido alterar ou remover uma chave de licença ativa."
            )

    if settings_update.ai_enabled is not None:
        settings.ai_enabled = settings_update.ai_enabled
    if settings_update.ai_provider is not None:
        settings.ai_provider = settings_update.ai_provider
    if settings_update.ai_model is not None:
        settings.ai_model = settings_update.ai_model
    if settings_update.ai_api_key is not None:
        settings.ai_api_key = settings_update.ai_api_key
    if settings_update.ai_base_url is not None:
        settings.ai_base_url = settings_update.ai_base_url
        
    try:
        db.commit()
        db.refresh(settings)
        return settings
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating agent settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to update settings")

@router.post("/settings/generate-key", response_model=AgentSettingsResponse)
def generate_agent_license_key(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    import requests
    from datetime import date, timedelta
    
    settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == current_user.id).first()
    if not settings:
        settings = AgentSettingsDB(user_id=current_user.id)
        db.add(settings)
        db.commit()
        db.refresh(settings)

    # Check if a license key is already set
    if settings.ai_api_key and settings.ai_api_key.startswith("QAFLOW-"):
        raise HTTPException(
            status_code=400,
            detail="Você já possui uma chave de licença ativa. Não é permitido gerar outra ou deletar a atual."
        )

    from app.config import settings as app_settings
    # Resolve target manager URL
    manager_url = settings.ai_base_url or app_settings.LICENSE_MANAGER_URL
    if not manager_url:
        raise HTTPException(
            status_code=400,
            detail="Endereço do Gerenciador de Licenças não configurado. Por favor, preencha o campo de Endereço/Base URL antes de gerar a chave."
        )

    # Request key generation from License Manager
    url = f"{manager_url.rstrip('/')}/api/licenses"
    payload = {
        "client_name": current_user.email or current_user.username or f"User_{current_user.id}",
        "expires_at": str(date.today() + timedelta(days=365)),
        "max_users": 10,
        "ai_allowed": True,
        "is_active": True
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.error(f"Failed to generate license key from manager: {resp.status_code} - {resp.text}")
            raise HTTPException(status_code=400, detail=f"Erro no Gerenciador de Licenças: {resp.text}")
        
        data = resp.json()
        generated_key = data.get("key")
        if not generated_key:
            raise HTTPException(status_code=500, detail="O Gerenciador de Licenças não retornou uma chave válida.")
        
        settings.ai_api_key = generated_key
        db.commit()
        db.refresh(settings)
        return settings
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error contacting license manager: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Não foi possível conectar ao Gerenciador de Licenças em {manager_url}. Verifique se o endereço está correto e online."
        )
