from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ProjectBase(BaseModel):
    name: str
    description: Optional[str] = None
    image_url: Optional[str] = None


class ProjectCreate(ProjectBase):
    pass


class ProjectResponse(ProjectBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
