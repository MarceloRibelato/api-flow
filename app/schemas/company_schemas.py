from pydantic import BaseModel
from typing import Optional

class CompanySettingsUpdate(BaseModel):
    history_retention_days: Optional[int] = None
    history_archive_retention_days: Optional[int] = None

class CompanySettingsResponse(BaseModel):
    id: int
    name: str
    history_retention_days: Optional[int] = None
    history_archive_retention_days: Optional[int] = None

    class Config:
        from_attributes = True
