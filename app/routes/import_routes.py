from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional, Dict, Any
import json

from app.services.import_service import import_service

router = APIRouter(prefix="/import", tags=["Import"])

class CurlRequest(BaseModel):
    curl: str

@router.post("/curl")
async def import_curl(request: CurlRequest):
    try:
        return import_service.parse_curl(request.curl)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse cURL: {str(e)}")

@router.post("/postman")
async def import_postman(file: UploadFile = File(...)):
    print(f"DEBUG: Receiving Postman import request. Filename: {file.filename}")
    if not file.filename.endswith('.json'):
        raise HTTPException(status_code=400, detail="Invalid file type. Please upload a JSON file.")
    
    try:
        content = await file.read()
        print(f"DEBUG: File content size: {len(content)} bytes")
        # print(f"DEBUG: Content preview: {content[:200]}") 
        
        collection_data = json.loads(content)
        parsed = import_service.parse_postman(collection_data)
        print(f"DEBUG: Parsed {len(parsed)} requests")
        return parsed
    except json.JSONDecodeError as e:
         print(f"DEBUG: JSON Parse Error: {e}")
         raise HTTPException(status_code=400, detail="Invalid JSON format.")
    except Exception as e:
        print(f"DEBUG: General Import Error: {e}")
        # traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to parse Postman collection: {str(e)}")
