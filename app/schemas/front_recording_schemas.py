from datetime import datetime
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, ConfigDict

class FrontRecordingBase(BaseModel):
    name: str
    requests: List[Dict[str, Any]] = []
    interactions: List[Dict[str, Any]] = []

class FrontRecordingCreate(FrontRecordingBase):
    feature_id: int

class FrontRecordingResponse(FrontRecordingBase):
    id: int
    feature_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class FrontRecordingInbound(BaseModel):
    # This matches the structure sent by the standalone recorder
    requests: List[Dict[str, Any]] = []
    interactions: List[Dict[str, Any]] = []
