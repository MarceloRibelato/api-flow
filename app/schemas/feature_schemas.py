from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class FeatureBase(BaseModel):
    name: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    product_id: Optional[int] = None

class FeatureCreate(FeatureBase):
    pass

class FeatureResponse(FeatureBase):
    id: int
    created_at: datetime
    updated_at: datetime
    position: Optional[int] = 0
    product_id: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class ReorderItem(BaseModel):
    id: int
    position: int


class ReorderSchema(BaseModel):
    new_order: List[ReorderItem]
