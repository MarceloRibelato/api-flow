from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query, status
from sqlalchemy.orm import Session
import uuid
import logging
import asyncio
import json
import base64
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from jose import jwt, JWTError

from app.database import get_db
from app.auth import get_current_user, SECRET_KEY, ALGORITHM
from app.models.user_models import UserDB
from app.services.appium_inspector_service import AppiumInspectorService

logger = logging.getLogger(__name__)

router = APIRouter()

class SessionStartPayload(BaseModel):
    session_id: Optional[str] = None
    steps: Optional[List[Dict[str, Any]]] = None

@router.post("/session/start")
def start_inspector_session(product_id: int, payload: SessionStartPayload = None, session_id: Optional[str] = None, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
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
def get_inspector_progress(session_id: str, current_user: UserDB = Depends(get_current_user)):
    """Returns current step replay progress for an active Appium session."""
    return AppiumInspectorService.get_progress(session_id)

@router.delete("/session/{session_id}")
def stop_inspector_session(session_id: str, current_user: UserDB = Depends(get_current_user)):
    """Stops and cleans up the active Appium session."""
    try:
        AppiumInspectorService.stop_session(session_id)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error stopping inspector session: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/session/{session_id}/interact")
def execute_interaction(session_id: str, action: dict, skip_tree: bool = False, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    """
    Execute an interaction on the interactive device.
    action ex: {"type": "tap", "properties": {"selector": "login_btn"}}
    """
    try:
        snapshot = AppiumInspectorService.interact(session_id, action, db, skip_tree=skip_tree)
        return {"session_id": session_id, **snapshot}
    except Exception as e:
        logger.error(f"Interaction failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/session/{session_id}/test-selector")
def test_selector(session_id: str, body: dict, current_user: UserDB = Depends(get_current_user)):
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
def refresh_snapshot(session_id: str, skip_tree: bool = False, current_user: UserDB = Depends(get_current_user)):
    """
    Fetches the current screen snapshot without executing an action.
    """
    try:
        snapshot = AppiumInspectorService.get_snapshot(session_id, skip_tree=skip_tree)
        return {"session_id": session_id, **snapshot}
    except Exception as e:
        logger.error(f"Refresh failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/session/{session_id}/snapshot")
def get_snapshot_post(session_id: str, body: dict = None, current_user: UserDB = Depends(get_current_user)):
    """POST snapshot endpoint matching web_inspector_routes."""
    try:
        skip_tree = bool((body or {}).get("skip_tree", False))
        snapshot = AppiumInspectorService.get_snapshot(session_id, skip_tree=skip_tree)
        return {"session_id": session_id, **snapshot}
    except Exception as e:
        logger.error(f"Snapshot failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/session/{session_id}/generate-selectors")
def generate_selectors(session_id: str, payload: dict, current_user: UserDB = Depends(get_current_user)):
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
def ai_autocorrect_step(session_id: str, payload: dict, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
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

@router.websocket("/session/ws/{session_id}")
async def mobile_websocket_endpoint(websocket: WebSocket, session_id: str, token: Optional[str] = Query(None)):
    """WebSocket live frame stream & JSON-RPC endpoint matching web_inspector_routes."""
    if token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            if not payload.get("sub"):
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
        except JWTError:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    await websocket.accept()
    stop_event = asyncio.Event()

    async def frame_streamer():
        while not stop_event.is_set():
            try:
                frame_bytes = await asyncio.to_thread(AppiumInspectorService.get_screenshot_bytes, session_id)
                if frame_bytes:
                    await websocket.send_bytes(frame_bytes)
            except Exception:
                break
            await asyncio.sleep(0.25)

    stream_task = asyncio.create_task(frame_streamer())
    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_id = msg.get("messageId")
            action_type = msg.get("type")
            data = msg.get("payload") or {}
            try:
                if action_type == "snapshot":
                    res = await asyncio.to_thread(AppiumInspectorService.get_snapshot, session_id, bool(data.get("skip_tree", False)))
                elif action_type == "test-selector":
                    res = await asyncio.to_thread(AppiumInspectorService.test_selector, session_id, data.get("selector"))
                elif action_type == "generate-selectors":
                    res = await asyncio.to_thread(AppiumInspectorService.generate_selectors_for_element, session_id, data.get("lwsId"), data.get("text"))
                else:
                    res = {"error": f"Unsupported WS action: {action_type}"}
                await websocket.send_text(json.dumps({"messageId": msg_id, "status": "ok", "data": res}))
            except Exception as e:
                await websocket.send_text(json.dumps({"messageId": msg_id, "status": "error", "error": str(e)}))
    except WebSocketDisconnect:
        pass
    finally:
        stop_event.set()
        stream_task.cancel()
