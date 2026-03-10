from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.schemas.front_recording_schemas import FrontRecordingInbound, FrontRecordingResponse
from app.services.front_recording_service import FrontRecordingService
from app.auth import get_current_user, get_current_user_optional
from app.models.user_models import UserDB

router = APIRouter(prefix="/front-recording", tags=["Front Recording"])

@router.get("/test-auth")
async def test_auth_optional(current_user: Optional[UserDB] = Depends(get_current_user_optional)):
    return {
        "authenticated": current_user is not None,
        "user_id": current_user.id if current_user else None,
        "username": current_user.username if current_user else "anonymous"
    }

@router.post("/save")
async def save_front_recording(
    payload: FrontRecordingInbound,
    project_id: int = Query(..., description="Feature ID"),
    name: str = Query("Gravação Front", description="Nome da etapa/venda"),
    db: Session = Depends(get_db),
    current_user: Optional[UserDB] = Depends(get_current_user_optional)
):
    """
    Save front-end recordings (requests + interactions) to a separate structure
    """
    user_id = current_user.id if current_user else 1  # Default to 1 (Admin/System) if not authenticated
    
    result = FrontRecordingService.save_recording(
        db=db,
        feature_id=project_id,
        name=name,
        requests=payload.requests,
        interactions=payload.interactions,
        user_id=user_id
    )
    
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
        
    return result

@router.get("/list/{feature_id}", response_model=List[FrontRecordingResponse])
async def list_front_recordings(feature_id: int, db: Session = Depends(get_db)):
    """
    List recordings for a specific feature
    """
    return FrontRecordingService.list_recordings(db, feature_id)

@router.get("/{recording_id}/script")
async def get_front_recording_script(recording_id: int, db: Session = Depends(get_db)):
    """
    Generate and return a Playwright Python script for the recording
    """
    script = FrontRecordingService.generate_playwright_script(db, recording_id)
    return {"script": script}
