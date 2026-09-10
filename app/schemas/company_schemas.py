from pydantic import BaseModel, ConfigDict
from typing import Optional

class CompanySettingsUpdate(BaseModel):
    retention_days: Optional[int] = None
    history_retention_days: Optional[int] = None
    history_archive_retention_days: Optional[int] = None

class CompanySettingsResponse(BaseModel):
    id: int
    name: str
    retention_days: Optional[int] = 360
    history_retention_days: Optional[int] = None
    history_archive_retention_days: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)
