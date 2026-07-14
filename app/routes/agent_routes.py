from fastapi import APIRouter, Depends, HTTPException
import logging
from sqlalchemy.orm import Session
from app.database import get_db
from app.auth import get_current_user
from app.models.user_models import UserDB
from app.models.agent_models import AgentSettingsDB
from app.schemas.agent_schemas import AgentSettingsUpdate, AgentSettingsResponse
from app.services.agent_service import AgentService
from pydantic import BaseModel

router = APIRouter(prefix="/agent", tags=["AI Agent"])
logger = logging.getLogger(__name__)

class PlannerRequest(BaseModel):
    url: str

class HealerRequest(BaseModel):
    flow_id: int
    step_index: int
    error_message: str

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
    """Runs the AI Planner Agent via MCP."""
    try:
        plan = await AgentService.run_planner(db, current_user.id, req.url)
        return plan
    except Exception as e:
        logger.error(f"Error in Planner route: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/healer")
async def run_healer(
    req: HealerRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Runs the AI Healer Agent via MCP."""
    try:
        fix = await AgentService.run_healer(db, current_user.id, req.flow_id, req.step_index, req.error_message)
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
