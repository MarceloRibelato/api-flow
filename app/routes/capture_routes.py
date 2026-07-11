from fastapi import APIRouter, HTTPException, Body, Depends
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from app.database import get_db
from app.services.capture_service import CaptureService

router = APIRouter(prefix="/capture", tags=["Capture"])

@router.post("/ingest")
async def ingest_capture(
    requests: List[Dict[str, Any]] = Body(...),
    project_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """
    Ingest captured network requests from Chrome Extension
    """
    result = CaptureService.ingest_captured_requests(db, requests, project_id)
    
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
        
    return result

@router.get("/structure")
def get_structure(db: Session = Depends(get_db)):
    """
    Returns Products and Features hierarchy for Extension UI
    """
    return CaptureService.get_hierarchy(db)
