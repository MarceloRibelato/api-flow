from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.dashboard_service import DashboardService
from datetime import datetime
from typing import Dict, List, Any

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

def _parse_dates(start: str, end: str):
    s, e = None, None
    if start:
        try:
            s = datetime.fromisoformat(start)
        except:
            pass
    if end:
        try:
            e = datetime.fromisoformat(end)
        except:
            pass
    return s, e

@router.get("/metrics")
def get_metrics(
    days: int = 7, 
    project_id: int = None,
    flow_id: int = None,
    environment_id: int = None,
    start_date: str = None, 
    end_date: str = None, 
    execution_type: str = None,
    search_term: str = None,
    status_code: str = None,
    last_execution_only: bool = False,
    db: Session = Depends(get_db)
):
    valid_start, valid_end = _parse_dates(start_date, end_date)
    return DashboardService.get_summary_stats(
        db, days, project_id, flow_id, environment_id, valid_start, valid_end, execution_type,
        search_term, status_code, last_execution_only
    )

@router.get("/recent-executions")
def get_recent_executions(
    limit: int = 5, 
    project_id: int = None,
    flow_id: int = None,
    environment_id: int = None,
    start_date: str = None,
    end_date: str = None,
    execution_type: str = None,
    search_term: str = None,
    status_code: str = None,
    last_execution_only: bool = False,
    db: Session = Depends(get_db)
):
    valid_start, valid_end = _parse_dates(start_date, end_date)
    return DashboardService.get_recent_executions(
        db, limit, project_id, flow_id, environment_id, valid_start, valid_end, execution_type,
        search_term, status_code, last_execution_only
    )

@router.get("/failures")
def get_failures(
    limit: int = 5, 
    project_id: int = None,
    flow_id: int = None,
    environment_id: int = None,
    start_date: str = None,
    end_date: str = None,
    execution_type: str = None,
    search_term: str = None,
    status_code: str = None,
    last_execution_only: bool = False,
    db: Session = Depends(get_db)
):
    valid_start, valid_end = _parse_dates(start_date, end_date)
    return DashboardService.get_recent_failures(
        db, limit, project_id, flow_id, environment_id, valid_start, valid_end, execution_type,
        search_term, status_code, last_execution_only
    )

@router.get("/slowest")
def get_slowest(
    limit: int = 5,
    project_id: int = None,
    flow_id: int = None,
    environment_id: int = None,
    start_date: str = None,
    end_date: str = None,
    execution_type: str = None,
    search_term: str = None,
    status_code: str = None,
    last_execution_only: bool = False,
    db: Session = Depends(get_db)
):
    valid_start, valid_end = _parse_dates(start_date, end_date)
    return DashboardService.get_slowest_executions(
        db, limit, project_id, flow_id, environment_id, valid_start, valid_end, execution_type,
        search_term, status_code, last_execution_only
    )

@router.get("/daily")
def get_daily_stats(
    days: int = 7,
    project_id: int = None,
    flow_id: int = None,
    environment_id: int = None,
    start_date: str = None,
    end_date: str = None,
    execution_type: str = None,
    search_term: str = None,
    status_code: str = None,
    last_execution_only: bool = False,
    db: Session = Depends(get_db)
):
    valid_start, valid_end = _parse_dates(start_date, end_date)
    return DashboardService.get_daily_stats(
        db, days, project_id, flow_id, environment_id, valid_start, valid_end, execution_type,
        search_term, status_code, last_execution_only
    )

@router.get("/top-failures")
def get_top_failures(
    limit: int = 5,
    project_id: int = None,
    flow_id: int = None,
    environment_id: int = None,
    start_date: str = None,
    end_date: str = None,
    execution_type: str = None,
    search_term: str = None,
    status_code: str = None,
    last_execution_only: bool = False,
    db: Session = Depends(get_db)
):
    valid_start, valid_end = _parse_dates(start_date, end_date)
    return DashboardService.get_top_failing_apis(
        db, limit, project_id, flow_id, environment_id, valid_start, valid_end, execution_type,
        search_term, status_code, last_execution_only
    )

@router.get("/export")
def export_dashboard(
    days: int = 7,
    project_id: int = None,
    flow_id: int = None,
    environment_id: int = None,
    start_date: str = None,
    end_date: str = None,
    execution_type: str = None,
    search_term: str = None,
    status_code: str = None,
    last_execution_only: bool = False,
    db: Session = Depends(get_db)
):
    valid_start, valid_end = _parse_dates(start_date, end_date)
    csv_data = DashboardService.export_data_csv(
        db, days, project_id, flow_id, environment_id, valid_start, valid_end, execution_type,
        search_term, status_code, last_execution_only
    )
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=dashboard_export.csv"}
    )
