from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import uuid
import json
import asyncio
from sse_starlette.sse import EventSourceResponse

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
    api_data: Optional[Dict[str, Any]] = None
    api_list: Optional[List[Dict[str, Any]]] = None
    virtual_users: int = 10
    duration_seconds: int = 30
    ramp_up_seconds: int = 0
    test_name: Optional[str] = None
    environment_id: Optional[int] = None
    dataset: Optional[List[Dict[str, Any]]] = None

@router.post("/start")
def start_performance_test(
    req: LoadTestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    job_id = f"perf_{uuid.uuid4().hex}"
    
    final_list = req.api_list if req.api_list else ([req.api_data] if req.api_data else [])
    target_url = final_list[0].get("url", "unknown_url") if len(final_list) == 1 else "Multiple APIs (Flow)"
    
    # Create DB entry synchronously so SSE stream doesn't fail on connection
    result = PerformanceTestResult(
        id=job_id,
        company_id=current_user.company_id,
        user_id=current_user.id,
        status="pending",
        virtual_users=req.virtual_users,
        duration_seconds=req.duration_seconds,
        ramp_up_seconds=req.ramp_up_seconds,
        test_name=req.test_name,
        target_url=target_url
    )
    db.add(result)
    db.commit()

    from app.tasks.execution_tasks import celery_run_load_test

    # Send to Celery worker instead of running in background tasks
    celery_run_load_test.delay(
        job_id=job_id,
        api_list=final_list,
        virtual_users=req.virtual_users,
        duration_seconds=req.duration_seconds,
        ramp_up_seconds=req.ramp_up_seconds,
        company_id=current_user.company_id,
        user_id=current_user.id,
        test_name=req.test_name,
        environment_id=req.environment_id,
        dataset=req.dataset
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
        "p50_latency": result.p50_latency,
        "p90_latency": result.p90_latency,
        "p95_latency": result.p95_latency,
        "p99_latency": result.p99_latency,
        "requests_per_second": result.requests_per_second,
        "time_series_data": result.time_series_data,
        "api_stats": result.api_stats,
        "failed_requests_detail": result.failed_requests_detail,
        "started_at": result.started_at,
        "completed_at": result.completed_at
    }

@router.get("/{job_id}/stream")
async def stream_performance_stats(
    job_id: str,
    request: Request,
    token: Optional[str] = None,
    db: Session = Depends(get_db)
):
    # Verify token manually since EventSource cannot send Auth headers
    if not token:
        raise HTTPException(status_code=401, detail="Token requerido")
        
    try:
        from app.auth import SECRET_KEY, ALGORITHM
        from jose import jwt
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Token inválido")
            
        user = db.query(UserDB).filter(UserDB.username == username).first()
        if not user:
            raise HTTPException(status_code=401, detail="Usuário não encontrado")
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")

    async def event_publisher():
        from app.database import SessionLocal
        
        while True:
            if await request.is_disconnected():
                break
                
            local_db = SessionLocal()
            try:
                result = local_db.query(PerformanceTestResult).filter(
                    PerformanceTestResult.id == job_id,
                    PerformanceTestResult.company_id == user.company_id
                ).first()
                
                if not result:
                    yield {"event": "error", "data": json.dumps({"error": "Job not found"})}
                    break
                    
                stats_dict = {
                    "id": result.id,
                    "status": result.status,
                    "total_requests": result.total_requests,
                    "success_requests": result.success_requests,
                    "failed_requests": result.failed_requests,
                    "duration_seconds": result.duration_seconds,
                    "ramp_up_seconds": result.ramp_up_seconds,
                    "avg_latency": result.avg_latency,
                    "min_latency": result.min_latency,
                    "max_latency": result.max_latency,
                    "p50_latency": result.p50_latency,
                    "p90_latency": result.p90_latency,
                    "p95_latency": result.p95_latency,
                    "p99_latency": result.p99_latency,
                    "requests_per_second": result.requests_per_second,
                    "time_series_data": result.time_series_data,
                    "api_stats": result.api_stats,
                    "failed_requests_detail": result.failed_requests_detail,
                    "started_at": result.started_at.isoformat() if result.started_at else None,
                    "completed_at": result.completed_at.isoformat() if result.completed_at else None
                }
                
                yield {"event": "message", "data": json.dumps(stats_dict)}
                
                if result.status in ["completed", "stopped", "error"]:
                    break
            except Exception as e:
                yield {"event": "error", "data": json.dumps({"error": str(e)})}
                break
            finally:
                local_db.close()
                
            await asyncio.sleep(1)

    return EventSourceResponse(event_publisher())



@router.post("/{job_id}/stop")
def stop_performance_test(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    stopped = PerformanceService.stop_load_test(db, job_id)
    if not stopped:
        # It might have already finished or not started yet
        return {"message": "Test not active or already finished."}
    return {"message": "Stop signal sent successfully."}

@router.delete("/{job_id}")
def delete_performance_test(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    deleted = PerformanceService.delete_test_result(db, job_id, current_user.company_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Performance test not found")
    return {"message": "Test result deleted successfully."}

@router.get("/{job_id}/ai-diagnostic")
def get_performance_diagnostic(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    from app.services.analysis_service import AnalysisService
    diagnostic = AnalysisService.analyze_performance_test(db, job_id, current_user.id)
    return {"diagnostic": diagnostic}

@router.get("/{job_id}/export-detailed-csv")
def export_detailed_csv(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    import os
    from fastapi.responses import FileResponse
    
    base_reports_dir = "/app/data/reports" if os.path.exists("/app") else os.path.abspath("data/reports")
    shared_csv_path = os.path.join(base_reports_dir, f"{job_id}_detailed.csv")
    tmp_csv_path = f"/tmp/{job_id}_detailed.csv"
    
    csv_path = shared_csv_path if os.path.exists(shared_csv_path) else tmp_csv_path
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail="Detailed CSV not found for this job ID. Wait for the test to finish or run a new one.")
        
    return FileResponse(
        path=csv_path, 
        media_type="text/csv", 
        filename=f"Relatorio_Detalhado_{job_id}.csv"
    )

@router.get("/compare/{job_id_1}/{job_id_2}/ai-diagnostic")
def get_performance_comparison_diagnostic(
    job_id_1: str,
    job_id_2: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    from app.services.analysis_service import AnalysisService
    diagnostic = AnalysisService.analyze_performance_comparison(db, job_id_1, job_id_2, current_user.id)
    return {"diagnostic": diagnostic}
