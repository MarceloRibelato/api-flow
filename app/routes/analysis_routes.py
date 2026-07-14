from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.analysis_service import AnalysisService
from typing import Optional, Dict, Any
from pydantic import BaseModel

class AssertionRequest(BaseModel):
    status: int
    headers: Dict[str, Any]
    body: Any
    mode: Optional[str] = "ai" # 'ai' or 'contract'

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

@router.get("/flow/{flow_id}/suggest-tests")
def suggest_tests(
    flow_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Suggests new test scenarios based on the flow.
    """
    return AnalysisService.suggest_test_scenarios(db, flow_id, current_user.id)

@router.get("/project/{project_id}/suggest-tests")
def suggest_tests_by_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Finds the latest flow and suggests tests.
    """
    return AnalysisService.suggest_test_scenarios_by_project(db, project_id, current_user.id)

@router.post("/flow/{flow_id}/implement")
def implement_test(
    flow_id: int,
    scenario_title: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Generates and returns the implementation for a suggested test scenario.
    """
    try:
        result = AnalysisService.implement_test_scenario(db, flow_id, current_user.id, scenario_title, current_user.company_id)
        if not result:
            raise HTTPException(status_code=400, detail="Failed to implement test scenario: LLM returned empty response or invalid format.")
        return result
    except ValueError as e:
        import traceback
        try:
            with open("/app/error_new.log", "a") as f:
                f.write(f"ValueError in implement_test: {e}\n{traceback.format_exc()}\n")
        except: pass
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        try:
            with open("/app/error_new.log", "a") as f:
                f.write(f"Error in implement_test: {e}\n{tb}\n")
        except: pass
        raise HTTPException(status_code=500, detail=f"Erro interno: {e}\nTraceback: {tb}")

@router.post("/assertions")
def generate_assertions(
    req: AssertionRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Generates assertion suggestions based on the provided API response.
    """
    return AnalysisService.generate_assertions(db, current_user.id, req.dict())

class GenerateFlowRequest(BaseModel):
    prompt: str

@router.post("/project/{project_id}/generate-flow")
def generate_flow(
    project_id: int,
    req: GenerateFlowRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Generates a new flow from a natural language prompt.
    """
    try:
        return AnalysisService.generate_flow_from_text(db, req.prompt, project_id, current_user.company_id, current_user.id)
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))
