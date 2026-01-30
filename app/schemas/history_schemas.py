# app/schemas/history_schemas.py
from datetime import datetime
from typing import Any, Dict, Optional, List, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, computed_field


class AssertionResult(BaseModel):
    source: str
    operator: str
    target: Optional[Any] = None
    actual: Optional[Any] = None
    success: bool
    error_message: Optional[str] = None


class ExecutionHistoryBase(BaseModel):
    api_id: Optional[int] = None
    api_name: Optional[str] = None  # Added api_name
    project_id: Optional[int] = None
    flow_id: Optional[int] = None
    schedule_id: Optional[int] = None  # Added schedule_id
    node_name: Optional[str] = None  # Added node_name for functional grouping
    method: str
    url: str
    request_headers: Optional[Dict[str, Any]] = None
    request_body: Optional[str] = None
    request_params: Optional[Dict[str, Any]] = None
    status_code: int
    status_text: str
    response_headers: Optional[Dict[str, Any]] = None
    response_body: Optional[str] = None
    response_time: int
    error_message: Optional[str] = None
    variables_used: Optional[Dict[str, Any]] = None
    processed_url: Optional[str] = None
    user_id: Optional[int] = None
    environment_id: Optional[int] = None
    environment_name: Optional[str] = None
    assertions: Optional[List[AssertionResult]] = None


class ExecutionHistoryCreate(ExecutionHistoryBase):
    pass


class ExecutionHistoryResponse(ExecutionHistoryBase):
    id: int
    execution_id: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ExecutionHistorySummary(BaseModel):
    id: int
    execution_id: str
    method: str
    url: str
    status_code: int
    status_text: Optional[str] = None
    response_time: int
    created_at: datetime
    processed_url: Optional[str] = None
    environment_id: Optional[int] = None  # Added environment_id
    environment_name: Optional[str] = None
    node_name: Optional[str] = None  # Added node_name
    api_name: Optional[str] = None  # RESTORED: Critical for matching in frontend
    success: bool # Read directly from Model property
    assertions: Optional[List[AssertionResult]] = None # Added assertions for report summary
    
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
    url: str

    model_config = ConfigDict(from_attributes=True)
