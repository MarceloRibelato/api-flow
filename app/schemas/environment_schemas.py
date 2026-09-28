from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class EnvironmentCreate(BaseModel):
    name: str
    project_id: int
    suite_id: Optional[str] = None
    base_url: Optional[str] = None
    clone_from_id: Optional[int] = None


class EnvironmentResponse(BaseModel):
    id: int
    name: str
    project_id: int
    suite_id: Optional[str] = None
    base_url: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class EnvironmentUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
