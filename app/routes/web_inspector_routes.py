import traceback
from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import logging
import uuid

from app.services.web_inspector_service import WebInspectorService

logger = logging.getLogger(__name__)

router = APIRouter()

class SessionStartPayload(BaseModel):
    steps: Optional[List[Dict[str, Any]]] = None

@router.post("/session/start")
async def start_web_session(payload: SessionStartPayload = None, initial_url: str = None):
    """Starts a live web session."""
    session_id = str(uuid.uuid4())
    try:
        steps = payload.steps if payload else None
        snapshot = await WebInspectorService.start_session(session_id, initial_url, steps=steps)
        return {"session_id": session_id, **snapshot}
    except Exception as e:
        logger.error(f"Failed to start web session: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/session/{session_id}")
async def stop_web_session(session_id: str):
    """Stops the web session."""
    try:
        await WebInspectorService.stop_session(session_id)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error stopping web session: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/session/{session_id}/snapshot")
async def get_snapshot(session_id: str):
    """Gets current browser snapshot."""
    try:
        return await WebInspectorService.get_snapshot(session_id)
    except Exception as e:
        logger.error(f"Failed to get web snapshot: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/session/{session_id}/resize")
async def resize_session(session_id: str, payload: dict):
    """Resizes the browser viewport."""
    try:
        width = payload.get("width", 1280)
        height = payload.get("height", 720)
        return await WebInspectorService.resize_session(session_id, width, height)
    except Exception as e:
        logger.error(f"Resize session failed: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/session/{session_id}/interact")
async def interact(session_id: str, action: dict, skip_tree: bool = False):
    """Executes a web interaction."""
    try:
        return await WebInspectorService.interact(session_id, action, skip_tree=skip_tree)
    except Exception as e:
        logger.error(f"Web interaction failed: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/session/{session_id}/test-selector")
async def test_selector(session_id: str, payload: dict):
    """Tests a custom selector and returns the bounding box."""
    try:
        selector = payload.get("selector")
        if not selector:
            return {"success": False, "error": "Selector is empty."}
        return await WebInspectorService.test_selector(session_id, selector)
    except Exception as e:
        logger.error(f"Test selector failed: {e}")
        traceback.print_exc()
        # Do not raise HTTP Exception so the frontend can display the bad selector syntax elegantly
        return {"success": False, "error": str(e)}

@router.post("/session/{session_id}/generate-selectors")
async def generate_selectors(session_id: str, payload: dict):
    """Finds an element by text or lwsId and generates semantic selectors for it."""
    try:
        text = payload.get("text")
        lws_id = payload.get("lwsId")
        
        if not text and not lws_id:
            return {"success": False, "error": "Forneça o text ou lwsId do elemento."}
            
        return await WebInspectorService.generate_selectors_for_element(session_id, text, lws_id)
    except Exception as e:
        logger.error(f"Generate selectors failed: {e}")
        traceback.print_exc()
        return {"success": False, "error": str(e)}
