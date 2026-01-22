from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


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

    model_config = ConfigDict(from_attributes=True)


class ReorderItem(BaseModel):
    id: int
    position: int


class ReorderSchema(BaseModel):
    new_order: List[ReorderItem]
