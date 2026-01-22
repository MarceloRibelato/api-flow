from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class VariableCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    
    name: str
    value: str = ""
    type: str = "static"
    faker_type: Optional[str] = Field(default=None)
    faker_options: Optional[dict] = Field(default=None, alias="fakerOptions")
    project_id: int
    flow_id: Optional[int] = None
    environment_id: Optional[int] = None
    api_id: Optional[int] = Field(default=None, alias="apiId")
    json_path: Optional[str] = Field(default=None, alias="jsonPath")


class VariableResponse(VariableCreate):
    id: int
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
