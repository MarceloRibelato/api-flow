from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import uuid
import logging
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

from app.database import get_db
from app.services.appium_inspector_service import AppiumInspectorService

logger = logging.getLogger(__name__)

router = APIRouter()

class SessionStartPayload(BaseModel):
    session_id: Optional[str] = None
    steps: Optional[List[Dict[str, Any]]] = None

@router.post("/session/start")
def start_inspector_session(product_id: int, payload: SessionStartPayload = None, session_id: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Start an interactive Appium Inspector session.
    Returns: { "session_id": "...", "image_b64": "...", "tree": [...] }
    """
    sid = session_id or (payload.session_id if payload else None) or str(uuid.uuid4())
    try:
        steps = payload.steps if payload else None
        snapshot = AppiumInspectorService.start_session(sid, db, product_id, steps=steps)
        return {"session_id": sid, **snapshot}
    except Exception as e:
        logger.error(f"Failed to start inspector session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/session/{session_id}/progress")
def get_inspector_progress(session_id: str):
    """Returns current step replay progress for an active Appium session."""
    return AppiumInspectorService.get_progress(session_id)

@router.delete("/session/{session_id}")
def stop_inspector_session(session_id: str):
    """Stops and cleans up the active Appium session."""
    try:
        AppiumInspectorService.stop_session(session_id)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error stopping inspector session: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/session/{session_id}/interact")
def execute_interaction(session_id: str, action: dict, db: Session = Depends(get_db)):
    """
    Execute an interaction on the interactive device.
    action ex: {"type": "tap", "properties": {"selector": "login_btn"}}
    """
    try:
        snapshot = AppiumInspectorService.interact(session_id, action, db)
        return {"session_id": session_id, **snapshot}
    except Exception as e:
        logger.error(f"Interaction failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/session/{session_id}/test-selector")
def test_selector(session_id: str, body: dict):
    """
    Test a selector and return its bounds if found.
    body: {"selector": "..."}
    """
    try:
        result = AppiumInspectorService.test_selector(session_id, body.get("selector"))
        return result
    except Exception as e:
        logger.error(f"Selector test failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/session/{session_id}/refresh")
def refresh_snapshot(session_id: str):
    """
    Fetches the current screen snapshot without executing an action.
    """
    try:
        snapshot = AppiumInspectorService.get_snapshot(session_id)
        return {"session_id": session_id, **snapshot}
    except Exception as e:
        logger.error(f"Refresh failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/session/{session_id}/generate-selectors")
def generate_selectors(session_id: str, payload: dict):
    """
    Finds an element by text or lwsId and generates selectors for it (AutoCorrection).
    """
    try:
        text = payload.get("text")
        lws_id = payload.get("lwsId")
        
        if not text and not lws_id:
            raise HTTPException(status_code=400, detail="Forneça o text ou lwsId do elemento.")
            
        result = AppiumInspectorService.generate_selectors_for_element(session_id, lws_id=lws_id, text=text)
        return result
    except Exception as e:
        logger.error(f"Generate selectors failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

@router.post("/session/{session_id}/ai-autocorrect")
def ai_autocorrect_step(session_id: str, payload: dict, db: Session = Depends(get_db)):
    """
    Analyzes the full mobile DOM tree for a failed step using AI and returns the optimal component selector.
    """
    try:
        failed_step = payload.get("failed_step", {})
        result = AppiumInspectorService.ai_analyze_full_tree_and_correct(session_id, failed_step, db=db)
        return result
    except Exception as e:
        logger.error(f"AI AutoCorrect endpoint failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
