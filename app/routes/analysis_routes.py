from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.analysis_service import AnalysisService
from typing import Optional

router = APIRouter(prefix="/analysis", tags=["AI Insights"])

from app.auth import get_current_user
from app.models.user_models import UserDB

@router.get("/flow/{flow_id}")
def analyze_flow(
    flow_id: int, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Analyzes a Flow for potential optimizations (e.g. duplicates).
    Uses AI if configured by the user.
    """
    return AnalysisService.analyze_flow_redundancy(db, flow_id, current_user.id)

@router.get("/history")
def analyze_history(project_id: Optional[int] = None, db: Session = Depends(get_db)):
    """
    Analyzes execution history for performance trends.
    """
    return AnalysisService.analyze_performance_trends(db, project_id)
