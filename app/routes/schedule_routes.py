from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, ConfigDict
from datetime import datetime

from app.database import get_db
from app.models.schedule_models import ScheduleModel
from app.services.scheduler_service import scheduler_service
from app.auth import get_current_user
from app.models.user_models import UserDB

router = APIRouter(tags=["Schedules"])

# --- Pydantic Schemas ---
class ScheduleCreate(BaseModel):
    name: Optional[str] = None
    type: str # 'suite' or 'feature'
    flow_type: Optional[str] = 'api' # 'api', 'e2e', or 'performance'
    target_id: int
    environment_id: Optional[int] = None
    cron_expression: Optional[str] = None
    run_at: Optional[datetime] = None
    notification_urls: Optional[str] = None
    notifications_enabled: bool = True
    
    # Performance testing parameters
    virtual_users: Optional[int] = None
    duration_seconds: Optional[int] = None
    ramp_up_seconds: Optional[int] = None

class ScheduleOut(BaseModel):
    id: int
    name: Optional[str]
    type: str
    flow_type: Optional[str] = 'api'
    target_id: int
    environment_id: Optional[int]
    environment_name: Optional[str] = None
    cron_expression: Optional[str]
    run_at: Optional[datetime]
    notification_urls: Optional[str]
    notifications_enabled: bool
    status: str
    last_run: Optional[datetime]
    last_run_status: Optional[str] = None
    next_run: Optional[datetime]
    created_at: datetime
    virtual_users: Optional[int] = None
    duration_seconds: Optional[int] = None
    ramp_up_seconds: Optional[int] = None
    
    model_config = ConfigDict(from_attributes=True)

# --- Routes ---

@router.post("/", response_model=ScheduleOut)
def create_schedule(schedule_in: ScheduleCreate, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    # Basic validation
    if not schedule_in.cron_expression and not schedule_in.run_at:
        raise HTTPException(status_code=400, detail="Must provide cron_expression OR run_at")
        
    db_schedule = ScheduleModel(
        name=schedule_in.name,
        type=schedule_in.type,
        flow_type=schedule_in.flow_type or 'api',
        target_id=schedule_in.target_id,
        environment_id=schedule_in.environment_id,
        cron_expression=schedule_in.cron_expression,
        run_at=schedule_in.run_at,
        notification_urls=schedule_in.notification_urls,
        notifications_enabled=schedule_in.notifications_enabled,
        status="active",
        user_id=current_user.id,
        company_id=current_user.company_id, # Assign Company
        virtual_users=schedule_in.virtual_users,
        duration_seconds=schedule_in.duration_seconds,
        ramp_up_seconds=schedule_in.ramp_up_seconds
    )
    db.add(db_schedule)
    db.commit()
    db.refresh(db_schedule)
    
    # Register in scheduler
    scheduler_service.add_job(db_schedule, db)
    
    return db_schedule

@router.get("/", response_model=List[ScheduleOut])
def list_schedules(type: Optional[str] = None, target_id: Optional[int] = None, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    from sqlalchemy.orm import joinedload
    from app.models.environment_model import Environment
    
    query = db.query(ScheduleModel).options(joinedload(ScheduleModel.environment)).filter(ScheduleModel.company_id == current_user.company_id) # Filter by Company
    if type:
        query = query.filter(ScheduleModel.type == type)
    if target_id:
        query = query.filter(ScheduleModel.target_id == target_id)
        
    results = query.all()
    
    # Manually map environment_name for Pydantic
    for schedule in results:
        if schedule.environment:
            schedule.environment_name = schedule.environment.name
            
    return results

@router.get("/{schedule_id}", response_model=ScheduleOut)
def get_schedule(schedule_id: int, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    schedule = db.query(ScheduleModel).filter(ScheduleModel.id == schedule_id, ScheduleModel.company_id == current_user.company_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    # Manually map environment_name
    if schedule.environment:
        schedule.environment_name = schedule.environment.name
    return schedule

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
