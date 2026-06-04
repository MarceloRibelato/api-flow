from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import uuid

from app.database import get_db
from app.auth import get_current_user
from app.models.user_models import UserDB
from app.models.performance_models import PerformanceTestResult
from app.services.performance_service import PerformanceService

router = APIRouter(
    prefix="/performance",
    tags=["Performance Testing"]
)

class LoadTestRequest(BaseModel):
    api_data: Dict[str, Any]
    virtual_users: int = 10
    duration_seconds: int = 30
    ramp_up_seconds: int = 0
    test_name: Optional[str] = None

@router.post("/start")
def start_performance_test(
    req: LoadTestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    job_id = f"perf_{uuid.uuid4().hex}"
    
    # We must pass a new DB session to the background task because the current request session will close.
    def bg_task(job_id, api_data, vu, duration, ramp_up, comp_id, user_id, test_name):
        from app.database import SessionLocal
        bg_db = SessionLocal()
        try:
            PerformanceService.run_load_test_sync(
                bg_db, job_id, api_data, vu, duration, ramp_up, comp_id, user_id, test_name
            )
        finally:
            bg_db.close()

    background_tasks.add_task(
        bg_task,
        job_id,
        req.api_data,
        req.virtual_users,
        req.duration_seconds,
        req.ramp_up_seconds,
        current_user.company_id,
        current_user.id,
        req.test_name
    )
    
    return {"message": "Performance test started", "job_id": job_id}

@router.get("/history")
def get_performance_history(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    results = db.query(PerformanceTestResult).filter(
        PerformanceTestResult.company_id == current_user.company_id
    ).order_by(PerformanceTestResult.started_at.desc()).all()
    
    # Do not return full time_series_data to save bandwidth for list view
    return [
        {
            "id": r.id,
            "test_name": r.test_name,
            "target_url": r.target_url,
            "status": r.status,
            "virtual_users": r.virtual_users,
            "duration_seconds": r.duration_seconds,
            "total_requests": r.total_requests,
            "success_requests": r.success_requests,
            "failed_requests": r.failed_requests,
            "avg_latency": r.avg_latency,
            "started_at": r.started_at,
            "completed_at": r.completed_at
        } for r in results
    ]

@router.get("/{job_id}/stats")
def get_performance_stats(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    result = db.query(PerformanceTestResult).filter(
        PerformanceTestResult.id == job_id,
        PerformanceTestResult.company_id == current_user.company_id
    ).first()
    
    if not result:
        raise HTTPException(status_code=404, detail="Performance test not found")
        
    return {
        "id": result.id,
        "test_name": result.test_name,
        "target_url": result.target_url,
        "status": result.status,
        "virtual_users": result.virtual_users,
        "duration_seconds": result.duration_seconds,
        "ramp_up_seconds": result.ramp_up_seconds,
        "total_requests": result.total_requests,
        "success_requests": result.success_requests,
        "failed_requests": result.failed_requests,
        "avg_latency": result.avg_latency,
        "min_latency": result.min_latency,
        "max_latency": result.max_latency,
        "p90_latency": result.p90_latency,
        "p95_latency": result.p95_latency,
        "p99_latency": result.p99_latency,
        "requests_per_second": result.requests_per_second,
        "time_series_data": result.time_series_data,
        "started_at": result.started_at,
        "completed_at": result.completed_at
    }

@router.post("/{job_id}/stop")
def stop_performance_test(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    stopped = PerformanceService.stop_load_test(job_id)
    if not stopped:
        # It might have already finished or not started yet
        return {"message": "Test not active or already finished."}
    return {"message": "Stop signal sent successfully."}
