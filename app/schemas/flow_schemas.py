from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class AssertionRule(BaseModel):
    source: str  # status_code, body, header, response_time
    property: Optional[str] = None  # path for body/header (e.g. "data.id")
    operator: str  # eq, neq, gt, lt, contains, etc.
    target: Any


class ApiCallSchema(BaseModel):
    id: str
    name: Optional[str] = "Nova Requisição"  # Added name field
    method: str
    url: str
    headers: List[Dict[str, str]] = []
    body: Optional[str] = None
    params: List[Dict[str, str]] = []
    description: str = ""
    timeout: int = 30000
    assertions: List[AssertionRule] = []


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


class CardDataSchema(BaseModel):
    name: str
    color: str = "#10b981"
    description: str = ""
    bddScenarios: List[Dict[str, Any]] = []
    apiCalls: List[ApiCallSchema] = []
    envData: Dict[str, EnvSpecificData] = {}



class FlowSaveSchema(BaseModel):
    projectId: int
    flowId: Optional[int] = None  # New: ID of the flow being saved
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

    class Config:
        from_attributes = True

