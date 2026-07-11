from pydantic import BaseModel
from typing import Dict, Any, Optional

class ProxyRequest(BaseModel):
    method: str
    url: str
    headers: Optional[Dict[str, str]] = {}
    body: Optional[Any] = None
    params: Optional[Dict[str, Any]] = {}
