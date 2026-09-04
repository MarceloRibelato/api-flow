# app/schemas/history_schemas.py
from datetime import datetime
from typing import Any, Dict, Optional, List, Union
import json

from pydantic import BaseModel, ConfigDict, Field, field_validator, computed_field


class AssertionResult(BaseModel):
    source: str
    operator: Optional[str] = None
    property: Optional[str] = None # Added missing field
    target: Optional[Any] = None
    actual: Optional[Any] = None
    success: bool = False
    error_message: Optional[str] = None


class ExecutionHistoryBase(BaseModel):
    batch_id: Optional[str] = None  # Groups all API calls from a single run
    api_id: Optional[Union[int, str]] = None
    api_name: Optional[str] = None  # Added api_name
    project_id: Optional[int] = None
    flow_id: Optional[Union[int, str]] = None
    schedule_id: Optional[int] = None  # Added schedule_id
    feature_name: Optional[str] = None # Added feature_name
    node_id: Optional[Union[int, str]] = None # Added node_id for stable identifier (e.g. '1-1')
    node_name: Optional[str] = None  # Added node_name for functional grouping
    method: Optional[str] = "GET"
    url: Optional[str] = None
    request_headers: Optional[Union[Dict[str, Any], List[Any]]] = None
    request_body: Optional[Any] = None # Robust to non-string
    request_params: Optional[Union[Dict[str, Any], List[Any]]] = None
    status_code: int = 0
    status_text: Optional[str] = "OK"
    response_headers: Optional[Union[Dict[str, Any], List[Any]]] = None
    response_body: Optional[Any] = None # Robust to non-string
    response_time: int = 0
    error_message: Optional[str] = None
    variables_used: Optional[Union[Dict[str, Any], List[Any]]] = None
    processed_url: Optional[str] = None
    user_id: Optional[int] = None
    environment_id: Optional[int] = None
    environment_name: Optional[str] = None
    assertions: Optional[List[AssertionResult]] = None
    video_url: Optional[str] = None  # Added for E2E recordings
    healed_selector: Optional[str] = None # Added for Drift Detection
    execution_type: Optional[str] = "api"  # 'api' or 'web'
    trigger_origin: Optional[str] = "manual"  # 'manual', 'pipeline', 'schedule'
    retry_count: Optional[int] = 0


    @field_validator('request_headers', 'request_params', 'response_headers', 'variables_used', mode='before')
    @classmethod
    def convert_list_to_dict(cls, v):
        if isinstance(v, list):
            # Check if it's a list of {key, value} or {name, value}
            result = {}
            for item in v:
                if isinstance(item, dict):
                    key = item.get('key') or item.get('name')
                    value = item.get('value')
                    if key is not None:
                        result[key] = value
                    else:
                        # Maybe it's a single-entry dict like {"Header": "Value"}
                        result.update(item)
            return result
        return v

    @field_validator('request_body', 'response_body')
    @classmethod
    def stringify_bodies(cls, v):
        if v is None: return None
        if isinstance(v, (dict, list)): return json.dumps(v)
        return str(v)


class ExecutionHistoryCreate(ExecutionHistoryBase):
    pass


class ApiHistoryCreate(ExecutionHistoryBase):
    execution_type: Optional[str] = "api"


class WebHistoryCreate(ExecutionHistoryBase):
    execution_type: Optional[str] = "web"
    video_url: Optional[str] = None
    healed_selector: Optional[str] = None


class MobileHistoryCreate(ExecutionHistoryBase):
    execution_type: Optional[str] = "mobile"
    video_url: Optional[str] = None


class ExecutionHistoryResponse(ExecutionHistoryBase):
    id: int
    execution_id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    success: bool # Read directly from Model property

    model_config = ConfigDict(from_attributes=True)


class ExecutionHistorySummary(BaseModel):
    id: int
    execution_id: str
    batch_id: Optional[str] = None  # Used by frontend to group executions
    feature_name: Optional[str] = None # Added feature_name
    method: Optional[str] = "GET"
    url: Optional[str] = None
    status_code: int = 0
    status_text: Optional[str] = "OK"
    response_time: int = 0
    created_at: datetime
    processed_url: Optional[str] = None
    environment_id: Optional[int] = None  # Added environment_id
    environment_name: Optional[str] = None
    node_name: Optional[str] = None  # Added node_name
    node_id: Optional[Union[int, str]] = None  # Added node_id
    project_id: Optional[int] = None # Added project_id
    api_id: Optional[Union[int, str]] = None
    flow_id: Optional[Union[int, str]] = None
    api_name: Optional[str] = None  # RESTORED: Critical for matching in frontend
    success: bool # Read directly from Model property
    error_message: Optional[str] = None # Critical for diagnostics
    request_body: Optional[Any] = None # Added for visibility
    response_body: Optional[Any] = None # Added for visibility (used for screenshots)
    assertions: Optional[List[AssertionResult]] = None # Added assertions for report summary
    video_url: Optional[str] = None  # Added for E2E recordings
    healed_selector: Optional[str] = None # Added for Drift Detection
    execution_type: Optional[str] = "api"  # 'api' or 'web'
    trigger_origin: Optional[str] = "manual"  # 'manual', 'pipeline', 'schedule'
    retry_count: Optional[int] = 0

    
    model_config = ConfigDict(from_attributes=True)


class ExecutionHistoryListResponse(BaseModel):
    total: int
    skip: int
    limit: int
    data: list[ExecutionHistorySummary]


class PaginatedHistoryResponse(BaseModel):
    total: int
    page: int
    limit: int
    total_pages: int
    items: list[ExecutionHistorySummary]

    model_config = ConfigDict(from_attributes=True)


class UniqueApiSummary(BaseModel):
    method: str
    url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
