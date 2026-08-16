import traceback
from fastapi import APIRouter, HTTPException, Body, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import logging
import uuid
import json

from app.services.web_inspector_service import WebInspectorService

logger = logging.getLogger(__name__)

router = APIRouter()

class SessionStartPayload(BaseModel):
    session_id: Optional[str] = None
    steps: Optional[List[Dict[str, Any]]] = None

@router.websocket("/session/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    logger.info(f"🟢 [WebSocket] Client connected to session {session_id}")
    
    import asyncio
    
    frame_queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    
    await WebInspectorService.register_frame_queue(session_id, frame_queue, loop)
    
    async def frame_sender():
        try:
            while True:
                frame_bytes = await frame_queue.get()
                await websocket.send_bytes(frame_bytes)
        except Exception:
            pass
            
    sender_task = asyncio.create_task(frame_sender())
    
    try:
        while True:
            data = await websocket.receive_json()
            message_id = data.get("messageId")
            action_type = data.get("type")
            payload = data.get("payload", {})
            skip_tree = data.get("skip_tree", False)
            
            try:
                if action_type == "interact":
                    result, image_bytes = await WebInspectorService.interact(session_id, payload, skip_tree=skip_tree)
                    await websocket.send_json({"messageId": message_id, "success": True, "data": result})
                    if result.get("has_binary_image") and image_bytes:
                        await websocket.send_bytes(image_bytes)
                    
                elif action_type == "snapshot":
                    result, image_bytes = await WebInspectorService.get_snapshot(session_id, skip_tree=skip_tree)
                    await websocket.send_json({"messageId": message_id, "success": True, "data": result})
                    if result.get("has_binary_image") and image_bytes:
                        await websocket.send_bytes(image_bytes)
                    
                elif action_type == "resize":
                    width = payload.get("width", 1280)
                    height = payload.get("height", 720)
                    result, image_bytes = await WebInspectorService.resize_session(session_id, width, height)
                    await websocket.send_json({"messageId": message_id, "success": True, "data": result})
                    if result.get("has_binary_image") and image_bytes:
                        await websocket.send_bytes(image_bytes)
                
                elif action_type == "test-selector":
                    selector = payload.get("selector")
                    if not selector:
                        await websocket.send_json({"messageId": message_id, "success": False, "error": "Selector is empty."})
                    else:
                        result = await WebInspectorService.test_selector(session_id, selector)
                        await websocket.send_json({"messageId": message_id, "success": True, "data": result})
                        
                elif action_type == "generate-selectors":
                    text = payload.get("text")
                    lws_id = payload.get("lwsId")
                    if not text and not lws_id:
                        await websocket.send_json({"messageId": message_id, "success": False, "error": "Forneça o text ou lwsId do elemento."})
                    else:
                        result = await WebInspectorService.generate_selectors_for_element(session_id, text, lws_id)
                        await websocket.send_json({"messageId": message_id, "success": True, "data": result})
                
                else:
                    await websocket.send_json({"messageId": message_id, "success": False, "error": f"Unknown action type: {action_type}"})
                    
            except Exception as e:
                logger.error(f"🔴 [WebSocket] action {action_type} failed: {e}")
                traceback.print_exc()
                # Send error back so frontend Promise can reject
                await websocket.send_json({"messageId": message_id, "success": False, "error": str(e)})
                
    except WebSocketDisconnect:
        logger.info(f"⚪ [WebSocket] disconnected for session {session_id}")
    except Exception as e:
        logger.error(f"🔴 [WebSocket] connection error for session {session_id}: {e}")

@router.post("/session/start")
async def start_web_session(payload: SessionStartPayload = None, initial_url: str = None, session_id: str = None):
    """Starts a live web session."""
    final_session_id = session_id or (payload.session_id if payload else None) or str(uuid.uuid4())
    try:
        import base64
        steps = payload.steps if payload else None
        snapshot, image_bytes = await WebInspectorService.start_session(final_session_id, initial_url, steps=steps)
        if image_bytes:
            snapshot["image_b64"] = base64.b64encode(image_bytes).decode("utf-8")
        return {"session_id": final_session_id, **snapshot}
    except Exception as e:
        logger.error(f"Failed to start web session: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/session/{session_id}/progress")
async def get_web_session_progress(session_id: str):
    """Returns the real-time execution progress of step replay for a web session."""
    return WebInspectorService.get_progress(session_id)

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
