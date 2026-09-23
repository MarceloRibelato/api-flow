from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.analysis_service import AnalysisService
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

class AssertionRequest(BaseModel):
    status: Optional[int] = 200
    headers: Optional[Any] = None
    body: Optional[Any] = None
    schema_: Optional[Any] = Field(default=None, alias="schema")
    mode: Optional[str] = "smart" # 'smart', 'ai' or 'contract'

    model_config = {"populate_by_name": True}

router = APIRouter(prefix="/analysis", tags=["AI Insights"])

from app.auth import get_current_user
from app.models.user_models import UserDB

import logging

logger = logging.getLogger(__name__)

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
def analyze_history(
    project_id: Optional[int] = None, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Analyzes execution history for performance trends.
    """
    if project_id:
        from app.models.product_models import ProductModel
        from app.models.feature_models import FeatureModel
        is_owner = db.query(ProductModel).filter(
            ProductModel.id == project_id,
            ProductModel.company_id == current_user.company_id
        ).first() is not None
        if not is_owner:
            is_owner = db.query(FeatureModel).join(ProductModel).filter(
                FeatureModel.id == project_id,
                ProductModel.company_id == current_user.company_id
            ).first() is not None
        if not is_owner and current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Acesso negado ao projeto solicitado.")
    return AnalysisService.analyze_performance_trends(db, project_id, company_id=current_user.company_id)

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
        logger.warning(f"ValueError in implement_test: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error in implement_test: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro interno ao implementar cenário de teste: {str(e)}")

@router.post("/assertions")
def generate_assertions(
    req: AssertionRequest,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    Generates assertion suggestions based on the provided API response.
    """
    raw_dict = req.model_dump(by_alias=True) if hasattr(req, "model_dump") else req.dict(by_alias=True)
    headers = raw_dict.get("headers")
    if isinstance(headers, list):
        normalized_headers = {}
        for h in headers:
            if isinstance(h, dict):
                k = h.get("key") or h.get("name")
                if k:
                    normalized_headers[str(k)] = h.get("value", "")
        raw_dict["headers"] = normalized_headers
    elif not isinstance(headers, dict):
        raw_dict["headers"] = {}

    try:
        raw_dict["status"] = int(raw_dict.get("status") or 200)
    except (ValueError, TypeError):
        raw_dict["status"] = 200

    return AnalysisService.generate_assertions(db, current_user.id, raw_dict)

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
