from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.auth import get_current_user
from app.models.user_models import UserDB
from app.models.agent_models import AgentSettingsDB
from app.schemas.agent_schemas import AgentSettingsUpdate, AgentSettingsResponse
import logging

router = APIRouter(prefix="/agent/settings", tags=["AI Agent Settings"])
logger = logging.getLogger(__name__)

@router.get("/", response_model=AgentSettingsResponse)
def get_agent_settings(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == current_user.id).first()
    if not settings:
        # Create default if not exists
        settings = AgentSettingsDB(user_id=current_user.id)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    
    return settings

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
    
    if settings_update.ai_provider is not None:
        settings.ai_provider = settings_update.ai_provider
    if settings_update.ai_model is not None:
        settings.ai_model = settings_update.ai_model
    if settings_update.ai_api_key is not None:
        settings.ai_api_key = settings_update.ai_api_key
        
    try:
        db.commit()
        db.refresh(settings)
        return settings
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating agent settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to update settings")
