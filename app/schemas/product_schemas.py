from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict

class ProductBase(BaseModel):
    name: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    platform: Optional[str] = 'web'
    notification_urls: Optional[str] = None
    channel_type: Optional[str] = 'webhook'

class ProductCreate(ProductBase):
    pass

class ProductResponse(ProductBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class ProductMobileSettingsBase(BaseModel):
    provider: Optional[str] = "appium_local"
    server_url: Optional[str] = None
    auth_user: Optional[str] = None
    auth_token: Optional[str] = None
    device_name: Optional[str] = None
    platform_version: Optional[str] = None
    app_identifier: Optional[str] = None

class ProductMobileSettingsCreate(ProductMobileSettingsBase):
    pass

class ProductMobileSettingsResponse(ProductMobileSettingsBase):
    id: int
    product_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
