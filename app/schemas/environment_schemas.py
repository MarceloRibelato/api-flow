from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class EnvironmentCreate(BaseModel):
    name: str
    project_id: int
    clone_from_id: Optional[int] = None


class EnvironmentResponse(BaseModel):
    id: int
    name: str
    project_id: int
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
