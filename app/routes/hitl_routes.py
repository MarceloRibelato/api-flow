from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.database import get_db
from app.models.hitl_models import HitlSessionDB
from app.models.schedule_models import ScheduleModel

router = APIRouter(
    prefix="/hitl",
    tags=["HITL API"]
)

class HitlResolveRequest(BaseModel):
    selected_index: Optional[int] = None
    custom_selector: Optional[str] = None

@router.get("/pending")
def get_pending_hitl_sessions(
    execution_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    query = db.query(HitlSessionDB).filter(HitlSessionDB.status == "pending")
    if execution_id:
        query = query.filter(HitlSessionDB.execution_id == execution_id)
    
    sessions = query.all()
    
    valid_sessions = []
    now = datetime.utcnow()
    for s in sessions:
        # Check if expired (older than 5 minutes)
        if s.created_at and (now - s.created_at).total_seconds() > 300:
            s.status = "timeout"
            db.commit()
        else:
            valid_sessions.append(s)
            
    return [{
        "id": s.id,
        "execution_id": s.execution_id,
        "project_id": s.project_id,
        "node_id": s.node_id,
        "step_id": s.step_id,
        "original_selector": s.original_selector,
        "candidates": s.candidates,
        "created_at": s.created_at
    } for s in valid_sessions]

@router.post("/{session_id}/resolve")
def resolve_hitl_session(
    session_id: int,
    req: HitlResolveRequest,
    db: Session = Depends(get_db)
):
    session = db.query(HitlSessionDB).filter(HitlSessionDB.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    if session.status != "pending":
        raise HTTPException(status_code=400, detail="Session already resolved")
        
    session.selected_index = req.selected_index
    session.custom_selector = req.custom_selector
    session.status = "resolved"
    session.resolved_at = datetime.utcnow()
    
    db.commit()
    
    return {"message": "Session resolved successfully", "status": "resolved"}
