from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

from app.database import get_db
from app.models.schedule_models import ScheduleModel
from app.services.scheduler_service import scheduler_service
from app.auth import get_current_user
from app.models.user_models import UserDB

router = APIRouter()

# --- Pydantic Schemas ---
class ScheduleCreate(BaseModel):
    name: Optional[str] = None
    type: str # 'suite' or 'feature'
    target_id: int
    environment_id: Optional[int] = None
    cron_expression: Optional[str] = None
    run_at: Optional[datetime] = None

class ScheduleOut(BaseModel):
    id: int
    name: Optional[str]
    type: str
    target_id: int
    environment_id: Optional[int]
    cron_expression: Optional[str]
    run_at: Optional[datetime]
    status: str
    last_run: Optional[datetime]
    last_run_status: Optional[str] = None
    next_run: Optional[datetime]
    created_at: datetime
    
    class Config:
        orm_mode = True

# --- Routes ---

@router.post("/", response_model=ScheduleOut)
def create_schedule(schedule_in: ScheduleCreate, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    # Basic validation
    if not schedule_in.cron_expression and not schedule_in.run_at:
        raise HTTPException(status_code=400, detail="Must provide cron_expression OR run_at")
        
    db_schedule = ScheduleModel(
        name=schedule_in.name,
        type=schedule_in.type,
        target_id=schedule_in.target_id,
        environment_id=schedule_in.environment_id,
        cron_expression=schedule_in.cron_expression,
        run_at=schedule_in.run_at,
        status="active",
        user_id=current_user.id,
        company_id=current_user.company_id # Assign Company
    )
    db.add(db_schedule)
    db.commit()
    db.refresh(db_schedule)
    
    # Register in scheduler
    scheduler_service.add_job(db_schedule, db)
    
    return db_schedule

@router.get("/", response_model=List[ScheduleOut])
def list_schedules(type: Optional[str] = None, target_id: Optional[int] = None, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    query = db.query(ScheduleModel).filter(ScheduleModel.company_id == current_user.company_id) # Filter by Company
    if type:
        query = query.filter(ScheduleModel.type == type)
    if target_id:
        query = query.filter(ScheduleModel.target_id == target_id)
    return query.all()

@router.delete("/{schedule_id}")
def delete_schedule(schedule_id: int, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    schedule = db.query(ScheduleModel).filter(ScheduleModel.id == schedule_id, ScheduleModel.company_id == current_user.company_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
        
    scheduler_service.remove_job(schedule_id)
    db.delete(schedule)
    db.commit()
    return {"message": "Schedule deleted"}

@router.post("/{schedule_id}/pause")
def pause_schedule(schedule_id: int, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    schedule = db.query(ScheduleModel).filter(ScheduleModel.id == schedule_id, ScheduleModel.company_id == current_user.company_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    schedule.status = "paused"
    scheduler_service.remove_job(schedule_id) # Remove from active jobs
    db.commit()
    return {"message": "Schedule paused"}

@router.post("/{schedule_id}/resume")
def resume_schedule(schedule_id: int, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    schedule = db.query(ScheduleModel).filter(ScheduleModel.id == schedule_id, ScheduleModel.company_id == current_user.company_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
        
    schedule.status = "active"
    scheduler_service.add_job(schedule, db) # Add back to scheduler
    db.commit()
    return {"message": "Schedule resumed"}
