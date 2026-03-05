from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Any, Dict, List, Optional, Union
import json

class AssertionRule(BaseModel):
    source: str  # status_code, body, header, response_time
    property: Optional[str] = None  # path for body/header (e.g. "data.id")
    operator: str  # eq, neq, gt, lt, contains, etc.
    target: Any
    
    # Allow target to be anything but coerce it if needed? (optional for now)

class ExtractionRule(BaseModel):
    source: str  # body, header
    property: Optional[str] = None  # path (e.g. "data.token") or header key
    variable: str  # Variable name to save (e.g. "AUTH_TOKEN")


class ApiCallSchema(BaseModel):
    id: str
    name: Optional[str] = "Nova Requisição"
    method: str
    url: str
    headers: List[Dict[str, str]] = []
    body: Optional[Union[str, Dict[str, Any], List[Any]]] = None
    params: List[Dict[str, str]] = []
    description: str = ""
    timeout: int = 30000
    delay: int = 0
    parallel: bool = False
    assertions: List[AssertionRule] = []
    extracts: List[ExtractionRule] = []

    @field_validator('body')
    @classmethod
    def stringify_body(cls, v):
        if v is None:
            return None
        if isinstance(v, (dict, list)):
            return json.dumps(v)
        return str(v)



class NodeDataBasic(BaseModel):
    name: str
    color: str = "#10b981"
    childCount: int = 0
    isCollapsed: bool = False


class NodeDataFull(BaseModel):
    name: str
    color: str = "#10b981"
    description: str = ""
    childCount: int = 0
    isCollapsed: bool = False
    bddScenarios: List[Dict[str, Any]] = []
    apiCalls: List[ApiCallSchema] = []



class NodeSchema(BaseModel):
    id: str
    type: str
    position: Dict[str, float]
    width: Optional[float] = None
    height: Optional[float] = None
    hidden: bool = False
    parentNode: Optional[str] = None
    data: NodeDataBasic


class EdgeSchema(BaseModel):
    id: str
    source: str
    target: str
    type: str = "buttonedge"
    animated: bool = True


class EnvSpecificData(BaseModel):
    description: str = ""
    bddScenarios: List[Dict[str, Any]] = []
    apiCalls: List[ApiCallSchema] = []
    e2eSteps: List[Dict[str, Any]] = []


class CardDataSchema(BaseModel):
    name: str
    color: str = "#10b981"
    description: str = ""
    bddScenarios: List[Dict[str, Any]] = []
    apiCalls: List[ApiCallSchema] = []
    e2eSteps: List[Dict[str, Any]] = []
    envData: Dict[str, EnvSpecificData] = {}



class FlowSaveSchema(BaseModel):
    projectId: int
    flowId: Optional[int] = None  # New: ID of the flow being saved
    flow_type: str = "api"        # Distinguish between 'api' and 'e2e'
    name: Optional[str] = None    # New: Name update
    nodes: List[NodeSchema]
    edges: List[EdgeSchema]
    cardData: Dict[str, CardDataSchema] = {}


class FlowCreateSchema(BaseModel):
    projectId: int
    name: str


class FlowResponse(BaseModel):
    id: int
    project_id: int
    name: str
    created_at: Any
    updated_at: Any
    nodes_count: int
    edges_count: int

    model_config = ConfigDict(from_attributes=True)

