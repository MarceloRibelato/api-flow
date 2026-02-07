from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Union

from app.database import get_db
from app.models.schedule_models import ScheduleModel
from app.services.scheduler_service import execute_job
from app.services.pdf_service import PDFService

router = APIRouter(
    prefix="/execute",
    tags=["Execution API"]
)

class ExecutionRequest(BaseModel):
    product_id: int
    feature_id: Union[int, str] # 'all' or ID
    environment_id: int
    name: str
    flow_id: Optional[int] = None

from app.auth import get_current_user
from app.models.user_models import UserDB

@router.post("/create")
def trigger_execution(
    req: ExecutionRequest, 
    background_tasks: BackgroundTasks, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Creates a new schedule (one-time) and triggers execution immediately.
    """
    # 1. Determine Type
    target_id = 0
    sched_type = 'suite'
    
    if req.flow_id:
        sched_type = 'flow'
        target_id = req.flow_id
    elif str(req.feature_id).lower() == 'all':
        sched_type = 'suite'
        target_id = req.product_id
    else:
        sched_type = 'feature'
        try:
            target_id = int(req.feature_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid feature_id. Must be integer or 'all'")

    # 2. Create Schedule
    new_schedule = ScheduleModel(
        name=req.name,
        type=sched_type,
        target_id=target_id,
        environment_id=req.environment_id,
        cron_expression=None,
        run_at=datetime.utcnow(), # One-off
        status='active',

        company_id=current_user.company_id, # Use Auth
        user_id=current_user.id # Use Auth
    )
    
    db.add(new_schedule)
    db.commit()
    db.refresh(new_schedule)

    # 3. Trigger Async Execution (Bypass APScheduler for immediate run)
    # background_tasks.add_task(execute_job, new_schedule.id)
    # DEBUG: Run synchronously to catch errors
    execute_job(new_schedule.id)

    return {
        "message": "Execution started",
        "schedule_id": new_schedule.id,
        "status": "running"
    }

@router.get("/{schedule_id}/pdf")
def get_execution_pdf(
    schedule_id: int, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Generates and returns a PDF report for the given execution (schedule).
    """
    pdf_bytes = PDFService.generate_execution_report(db, schedule_id)
    
    if not pdf_bytes:
        raise HTTPException(status_code=404, detail="Execution not found or no data")
        
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=report_{schedule_id}.pdf"}
    )
