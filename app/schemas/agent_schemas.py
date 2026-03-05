from pydantic import BaseModel, ConfigDict
from typing import Optional

class AgentSettingsBase(BaseModel):
    ai_enabled: Optional[bool] = True
    ai_provider: Optional[str] = "openai"
    ai_model: Optional[str] = "gpt-4o"
    ai_api_key: Optional[str] = None
    ai_base_url: Optional[str] = None

class AgentSettingsUpdate(BaseModel):
    ai_enabled: Optional[bool] = None
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    ai_api_key: Optional[str] = None
    ai_base_url: Optional[str] = None
class AgentSettingsResponse(AgentSettingsBase):
    id: int
    user_id: int

    model_config = ConfigDict(from_attributes=True)
