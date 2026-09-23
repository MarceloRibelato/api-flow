from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from app.database import get_db
from app.auth import get_current_user
from app.models.user_models import UserDB
from app.services.dashboard_service import DashboardService
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

router = APIRouter(prefix="/app-data", tags=["Dashboard"])

def _parse_dates(start: str, end: str):
    s, e = None, None
    if start:
        try:
            cleaned = str(start).strip().replace("Z", "+00:00")
            if len(cleaned) == 10:  # YYYY-MM-DD
                s = datetime.fromisoformat(cleaned).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
            else:
                s = datetime.fromisoformat(cleaned)
        except:
            pass
    if end:
        try:
            cleaned = str(end).strip().replace("Z", "+00:00")
            if len(cleaned) == 10:  # YYYY-MM-DD
                e = datetime.fromisoformat(cleaned).replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc)
            else:
                e = datetime.fromisoformat(cleaned)
        except:
            pass
    return s, e

def _resolve_timeframe(days: Any, start: str, end: str):
    s, e = _parse_dates(start, end)
    num_days = 7
    if str(days).lower() == "today":
        num_days = 1
        if not s:
            now_utc = datetime.now(timezone.utc)
            s = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        if not e:
            now_utc = datetime.now(timezone.utc)
            e = now_utc.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif days is not None:
        try:
            num_days = int(days)
        except (ValueError, TypeError):
            num_days = 7
    return num_days, s, e

@router.get("/summary")
def get_metrics(
    days: Any = 7, 
    project_id: int = None,
    flow_id: int = None,
    environment_id: int = None,
    start_date: str = None, 
    end_date: str = None, 
    execution_type: str = None,
    search_term: str = None,
    status_code: str = None,
    last_execution_only: bool = False,
    trigger_origin: str = None,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    num_days, valid_start, valid_end = _resolve_timeframe(days, start_date, end_date)
    return DashboardService.get_summary_stats(
        db, num_days, project_id, flow_id, environment_id, valid_start, valid_end, execution_type,
        search_term, status_code, last_execution_only, trigger_origin,
        company_id=current_user.company_id if current_user else None
    )

@router.get("/recent-runs")
def get_recent_executions(
    limit: int = 5, 
    days: Any = None,
    project_id: int = None,
    flow_id: int = None,
    environment_id: int = None,
    start_date: str = None,
    end_date: str = None,
    execution_type: str = None,
    search_term: str = None,
    status_code: str = None,
    last_execution_only: bool = False,
    trigger_origin: str = None,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    num_days, valid_start, valid_end = _resolve_timeframe(days, start_date, end_date)
    if days is None and not start_date and not end_date:
        valid_start = None
    return DashboardService.get_recent_executions(
        db, limit, project_id, flow_id, environment_id, valid_start, valid_end, execution_type,
        search_term, status_code, last_execution_only, trigger_origin,
        company_id=current_user.company_id if current_user else None
    )

@router.get("/errors")
def get_failures(
    limit: int = 5, 
    days: Any = None,
    project_id: int = None,
    flow_id: int = None,
    environment_id: int = None,
    start_date: str = None,
    end_date: str = None,
    execution_type: str = None,
    search_term: str = None,
    status_code: str = None,
    last_execution_only: bool = False,
    trigger_origin: str = None,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    num_days, valid_start, valid_end = _resolve_timeframe(days, start_date, end_date)
    if days is None and not start_date and not end_date:
        valid_start = None
    return DashboardService.get_recent_failures(
        db, limit, project_id, flow_id, environment_id, valid_start, valid_end, execution_type,
        search_term, status_code, last_execution_only, trigger_origin,
        company_id=current_user.company_id if current_user else None
    )

@router.get("/delayed")
def get_slowest(
    limit: int = 5,
    days: Any = None,
    project_id: int = None,
    flow_id: int = None,
    environment_id: int = None,
    start_date: str = None,
    end_date: str = None,
    execution_type: str = None,
    search_term: str = None,
    status_code: str = None,
    last_execution_only: bool = False,
    trigger_origin: str = None,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    num_days, valid_start, valid_end = _resolve_timeframe(days, start_date, end_date)
    if days is None and not start_date and not end_date:
        valid_start = None
    return DashboardService.get_slowest_executions(
        db, limit, project_id, flow_id, environment_id, valid_start, valid_end, execution_type,
        search_term, status_code, last_execution_only, trigger_origin,
        company_id=current_user.company_id if current_user else None
    )

@router.get("/day-stats")
def get_daily_stats(
    days: Any = 7,
    project_id: int = None,
    flow_id: int = None,
    environment_id: int = None,
    start_date: str = None,
    end_date: str = None,
    execution_type: str = None,
    search_term: str = None,
    status_code: str = None,
    last_execution_only: bool = False,
    trigger_origin: str = None,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    num_days, valid_start, valid_end = _resolve_timeframe(days, start_date, end_date)
    return DashboardService.get_daily_stats(
        db, num_days, project_id, flow_id, environment_id, valid_start, valid_end, execution_type,
        search_term, status_code, last_execution_only, trigger_origin,
        company_id=current_user.company_id if current_user else None
    )

@router.get("/top-errors")
def get_top_failures(
    limit: int = 5,
    days: Any = None,
    project_id: int = None,
    flow_id: int = None,
    environment_id: int = None,
    start_date: str = None,
    end_date: str = None,
    execution_type: str = None,
    search_term: str = None,
    status_code: str = None,
    last_execution_only: bool = False,
    trigger_origin: str = None,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    num_days, valid_start, valid_end = _resolve_timeframe(days, start_date, end_date)
    if days is None and not start_date and not end_date:
        valid_start = None
    return DashboardService.get_top_failing_apis(
        db, limit, project_id, flow_id, environment_id, valid_start, valid_end, execution_type,
        search_term, status_code, last_execution_only, trigger_origin,
        company_id=current_user.company_id if current_user else None
    )

@router.get("/download")
def export_dashboard(
    days: Any = 7,
    project_id: int = None,
    flow_id: int = None,
    environment_id: int = None,
    start_date: str = None,
    end_date: str = None,
    execution_type: str = None,
    search_term: str = None,
    status_code: str = None,
    last_execution_only: bool = False,
    trigger_origin: str = None,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    num_days, valid_start, valid_end = _resolve_timeframe(days, start_date, end_date)
    csv_data = DashboardService.export_data_csv(
        db, num_days, project_id, flow_id, environment_id, valid_start, valid_end, execution_type,
        search_term, status_code, last_execution_only, trigger_origin,
        company_id=current_user.company_id if current_user else None
    )
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=dashboard_export.csv"}
    )
