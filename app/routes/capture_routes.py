from fastapi import APIRouter, HTTPException, Body
from typing import List, Dict, Any, Optional
from app.services.capture_service import CaptureService

router = APIRouter(prefix="/capture", tags=["Capture"])

@router.post("/ingest")
async def ingest_capture(
    requests: List[Dict[str, Any]] = Body(...),
    project_id: Optional[int] = None
):
    """
    Ingest captured network requests from Chrome Extension
    """
    result = CaptureService.ingest_captured_requests(requests, project_id)
    
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
        
    return result

@router.get("/structure")
def get_structure():
    """
    Returns Products and Features hierarchy for Extension UI
    """
    return CaptureService.get_hierarchy()
