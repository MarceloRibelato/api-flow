from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime, timezone
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
    flow_type: Optional[str] = 'api' # 'api' or 'e2e'
    max_concurrency: Optional[int] = None # Added max_concurrency
    capture_video: Optional[bool] = False # Optional flag to record video
    capture_screenshot: Optional[bool] = False # Optional flag to capture per-step screenshots
    visible_execution: Optional[bool] = False # Run with visible browser window (headless=False)
    timeout_ms: Optional[int] = 5000 # Step timeout limit
    virtual_users: Optional[int] = 2 # Reused for step retries
    ramp_up_seconds: Optional[int] = 0 # Reused for flow retries

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
        max_concurrency=req.max_concurrency, # Save max_concurrency
        flow_type=req.flow_type or 'api', # Track if this is an E2E or API run
        capture_video=req.capture_video,
        capture_screenshot=req.capture_screenshot,
        visible_execution=req.visible_execution,
        duration_seconds=req.timeout_ms, # Repurposing duration_seconds for step timeout in E2E
        virtual_users=req.virtual_users, # Repurposing for retryActions
        ramp_up_seconds=req.ramp_up_seconds, # Repurposing for retryTest

        company_id=current_user.company_id, # Use Auth
        user_id=current_user.id # Use Auth
    )
    
    db.add(new_schedule)
    db.commit()
    db.refresh(new_schedule)

    from app.tasks.execution_tasks import celery_execute_job

    # 3. Trigger Async Execution via Celery
    celery_execute_job.delay(new_schedule.id)

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
        content=bytes(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=report_{schedule_id}.pdf"}
    )

@router.get("/{schedule_id}/e2e-pdf")
def get_e2e_execution_pdf(
    schedule_id: int, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Generates and returns an E2E-specific PDF report for the given execution (schedule).
    """
    pdf_bytes = PDFService.generate_e2e_report(db, schedule_id)
    
    if not pdf_bytes:
        raise HTTPException(status_code=404, detail="E2E Execution not found or no data")
        
    return Response(
        content=bytes(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=e2e_report_{schedule_id}.pdf"}
    )
