from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import Optional, Union, List, Dict, Any

from app.database import get_db
from app.models.schedule_models import ScheduleModel
from app.services.scheduler_service import execute_job
from app.services.pdf_service import PDFService
from app.services.audit_service import AuditService

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
    dataset: Optional[List[Dict[str, Any]]] = None

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
        run_at=datetime.now(timezone.utc), # One-off
        status='active',
        max_concurrency=req.max_concurrency, # Save max_concurrency
        flow_type=req.flow_type or 'api', # Track if this is an E2E or API run
        capture_video=req.capture_video,
        capture_screenshot=req.capture_screenshot,
        visible_execution=req.visible_execution,
        duration_seconds=req.timeout_ms, # Repurposing duration_seconds for step timeout in E2E
        virtual_users=req.virtual_users, # Repurposing for retryActions
        ramp_up_seconds=req.ramp_up_seconds, # Repurposing for retryTest
        dataset=req.dataset,

        company_id=current_user.company_id, # Use Auth
        user_id=current_user.id # Use Auth
    )
    
    db.add(new_schedule)
    db.commit()
    db.refresh(new_schedule)

    from app.tasks.execution_tasks import celery_execute_job
    from app.services.audit_service import AuditService

    # 3. Trigger Async Execution via Celery
    celery_execute_job.delay(new_schedule.id)

    AuditService.log_action(
        db=db,
        company_id=current_user.company_id,
        user=current_user,
        action="RUN_EXECUTION",
        resource_type="execution",
        resource_id=str(new_schedule.id),
        resource_name=new_schedule.name,
        details={
            "flow_type": req.flow_type,
            "environment_id": req.environment_id,
            "product_id": req.product_id,
            "feature_id": req.feature_id,
            "visible_execution": req.visible_execution
        }
    )

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


from app.models.api_test_history_models import ApiExecutionHistory, WebExecutionHistory, MobileExecutionHistory

class RetryExecutionRequest(BaseModel):
    schedule_id: Optional[int] = None
    batch_id: Optional[str] = None
    product_id: Optional[int] = None
    feature_id: Optional[Union[int, str]] = None
    environment_id: Optional[int] = None
    failed_item_ids: Optional[List[Union[int, str]]] = None
    flow_type: Optional[str] = 'api'


@router.post("/retry")
def retry_execution(
    req: RetryExecutionRequest, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Triggers re-execution of failed tests for a given schedule, batch, or list of failed item IDs.
    """
    schedule_id = req.schedule_id
    if not schedule_id and req.batch_id:
        try:
            schedule_id = int(req.batch_id.split('_')[0])
        except (ValueError, IndexError):
            pass

    existing_schedule = None
    if schedule_id:
        existing_schedule = db.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()

    hist_record = None
    if not existing_schedule:
        model_candidates = []
        if req.flow_type == 'mobile':
            model_candidates = [MobileExecutionHistory, WebExecutionHistory, ApiExecutionHistory]
        elif req.flow_type in ['e2e', 'web']:
            model_candidates = [WebExecutionHistory, MobileExecutionHistory, ApiExecutionHistory]
        else:
            model_candidates = [ApiExecutionHistory, WebExecutionHistory, MobileExecutionHistory]

        for Model in model_candidates:
            if req.batch_id and not hist_record:
                hist_record = db.query(Model).filter(Model.batch_id == req.batch_id).first()
            if not hist_record and req.failed_item_ids:
                clean_ids = [int(x) for x in req.failed_item_ids if str(x).isdigit()]
                if clean_ids:
                    hist_record = db.query(Model).filter(Model.id.in_(clean_ids)).first()
            if not hist_record and schedule_id:
                hist_record = db.query(Model).filter(Model.schedule_id == schedule_id).first()
            if hist_record:
                break

        if hist_record and hist_record.schedule_id:
            existing_schedule = db.query(ScheduleModel).filter(ScheduleModel.id == hist_record.schedule_id).first()

    target_schedule = None
    if existing_schedule:
        resolved_flow_type = req.flow_type or existing_schedule.flow_type or 'api'
        existing_schedule.status = 'active'
        existing_schedule.last_run_status = 'running'
        if req.flow_type:
            existing_schedule.flow_type = resolved_flow_type
        if req.environment_id:
            existing_schedule.environment_id = req.environment_id
        db.commit()
        db.refresh(existing_schedule)
        target_schedule = existing_schedule
    elif hist_record:
        if hist_record.flow_id:
            try:
                target_id = int(hist_record.flow_id)
            except (ValueError, TypeError):
                target_id = 0
            sched_type = 'flow'
        elif hist_record.project_id:
            try:
                target_id = int(hist_record.project_id)
            except (ValueError, TypeError):
                target_id = 0
            sched_type = 'feature'
        else:
            target_id = req.product_id or 0
            sched_type = 'suite'

        resolved_flow_type = req.flow_type or hist_record.execution_type or 'api'
        target_schedule = ScheduleModel(
            name=f"Retry - {hist_record.feature_name or 'Execution'}",
            type=sched_type,
            target_id=target_id,
            environment_id=req.environment_id or hist_record.environment_id or 1,
            user_id=current_user.id,
            status='active',
            last_run_status='running',
            flow_type=resolved_flow_type,
            capture_video=True if resolved_flow_type in ['e2e', 'mobile'] else False,
            capture_screenshot=True if resolved_flow_type in ['e2e', 'mobile'] else False,
            company_id=current_user.company_id
        )
        db.add(target_schedule)
        db.commit()
        db.refresh(target_schedule)
    elif req.product_id:
        target_id = req.product_id
        sched_type = 'suite'
        if req.feature_id and str(req.feature_id).lower() != 'all':
            sched_type = 'feature'
            try:
                target_id = int(req.feature_id)
            except ValueError:
                pass
        
        resolved_flow_type = req.flow_type or 'api'
        target_schedule = ScheduleModel(
            name="Retry Execution",
            type=sched_type,
            target_id=target_id,
            environment_id=req.environment_id or 1,
            user_id=current_user.id,
            status='active',
            last_run_status='running',
            flow_type=resolved_flow_type,
            capture_video=True if resolved_flow_type in ['e2e', 'mobile'] else False,
            capture_screenshot=True if resolved_flow_type in ['e2e', 'mobile'] else False,
            company_id=current_user.company_id
        )
        db.add(target_schedule)
        db.commit()
        db.refresh(target_schedule)
    else:
        raise HTTPException(status_code=400, detail="Must provide schedule_id, batch_id, or failed_item_ids for retry.")

    # Auto-resolve failed_item_ids if not explicitly provided
    if not req.failed_item_ids:
        eff_ft = (target_schedule.flow_type if target_schedule else None) or req.flow_type or 'api'
        if eff_ft == 'mobile':
            TargetHistoryModel = MobileExecutionHistory
        elif eff_ft in ['e2e', 'web']:
            TargetHistoryModel = WebExecutionHistory
        else:
            TargetHistoryModel = ApiExecutionHistory

        query = db.query(TargetHistoryModel.id)
        if existing_schedule:
            query = query.filter(TargetHistoryModel.schedule_id == existing_schedule.id)
        if req.batch_id:
            query = query.filter(TargetHistoryModel.batch_id == req.batch_id)
        elif not existing_schedule and hist_record and hist_record.schedule_id:
            query = query.filter(TargetHistoryModel.schedule_id == hist_record.schedule_id)
        elif not existing_schedule and hist_record and hist_record.batch_id:
            query = query.filter(TargetHistoryModel.batch_id == hist_record.batch_id)

        failed_rows = query.filter(
            (TargetHistoryModel.status_code >= 400) | 
            (TargetHistoryModel.status_code == 0) |
            (TargetHistoryModel.status_code.is_(None)) |
            ((TargetHistoryModel.error_message.isnot(None)) & (TargetHistoryModel.error_message != ''))
        ).all()
        if failed_rows:
            req.failed_item_ids = [r[0] for r in failed_rows]

    from app.tasks.execution_tasks import celery_execute_job
    celery_execute_job.delay(target_schedule.id, failed_item_ids=req.failed_item_ids)

    AuditService.log_action(
        db=db,
        company_id=current_user.company_id,
        user=current_user,
        action="RETRY_EXECUTION",
        resource_type="execution",
        resource_id=str(target_schedule.id),
        resource_name=target_schedule.name or "Retry Execution",
        details={
            "schedule_id": target_schedule.id,
            "batch_id": req.batch_id,
            "failed_item_count": len(req.failed_item_ids) if req.failed_item_ids else 0,
            "flow_type": req.flow_type
        }
    )

    return {
        "message": "Retry execution started",
        "schedule_id": target_schedule.id,
        "status": "running"
    }


