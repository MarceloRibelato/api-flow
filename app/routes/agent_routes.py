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

@router.put("/", response_model=AgentSettingsResponse)
def update_agent_settings(
    settings_update: AgentSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == current_user.id).first()
    if not settings:
        settings = AgentSettingsDB(user_id=current_user.id)
        db.add(settings)
    
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
