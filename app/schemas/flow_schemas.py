from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Any, Dict, List, Optional, Union
import json

class AssertionRule(BaseModel):
    source: str = Field(..., description="Fonte da validação (status_code, body, header, response_time)", json_schema_extra={"example": "status_code"})
    property: Optional[str] = Field(None, description="Caminho do campo no corpo ou cabeçalho", json_schema_extra={"example": "data.id"})
    operator: str = Field(..., description="Operador de comparação (equals, contains, etc)", json_schema_extra={"example": "equals"})
    target: Any = Field(..., description="Valor esperado para a validação", json_schema_extra={"example": 200})

class ExtractionRule(BaseModel):
    source: str = Field(..., description="Origem do dado a extrair (body, header, status)", json_schema_extra={"example": "body"})
    property: Optional[str] = Field(None, description="Caminho do campo para extração", json_schema_extra={"example": "data.token"})
    variable: str = Field(..., description="Nome da variável onde o valor será salvo", json_schema_extra={"example": "AUTH_TOKEN"})

class ApiCallSchema(BaseModel):
    id: str = Field(..., description="ID único da requisição no card", json_schema_extra={"example": "step-1"})
    name: Optional[str] = Field("Nova Requisição", description="Nome amigável da requisição")
    method: str = Field(..., description="Método HTTP", json_schema_extra={"example": "POST"})
    url: str = Field(..., description="URL completa (suporta {{VAR}})", json_schema_extra={"example": "https://api.example.com/login"})
    headers: List[Dict[str, str]] = Field([], description="Cabeçalhos da requisição")
    body: Optional[Union[str, Dict[str, Any], List[Any]]] = Field(None, description="Corpo da requisição")
    params: List[Dict[str, str]] = Field([], description="Parâmetros de query string")
    description: str = Field("", description="Descrição opcional do passo")
    timeout: int = Field(30000, description="Timeout em milissegundos")
    delay: int = Field(0, description="Atraso antes da execução em ms")
    cacheTTL: int = Field(0, description="Tempo de vida do cache em minutos")
    parallel: bool = Field(False, description="Executar em paralelo com outras requisições do mesmo nível")
    assertions: List[AssertionRule] = Field([], description="Lista de validações esperadas")
    extracts: List[ExtractionRule] = Field([], description="Lista de extrações de variáveis")

    @field_validator('body')
    @classmethod
    def stringify_body(cls, v):
        if v is None:
            return None
        if isinstance(v, (dict, list)):
            return json.dumps(v)
        return str(v)

class NodeDataBasic(BaseModel):
    name: str = Field(..., description="Nome exibido no nó")
    color: str = Field("#10b981", description="Cor de destaque do nó")
    childCount: int = Field(0, description="Número de filhos diretos")
    isCollapsed: bool = Field(False, description="Estado de colapso visual")
    layoutDirection: Optional[str] = Field(None, description="Direção do layout (LR ou TB)")

class NodeDataFull(BaseModel):
    name: str = Field(..., description="Nome do nó")
    color: str = Field("#10b981", description="Cor do nó")
    description: str = Field("", description="Descrição do objetivo do nó")
    childCount: int = Field(0, description="Número de filhos")
    isCollapsed: bool = Field(False, description="Estado de colapso")
    bddScenarios: List[Dict[str, Any]] = Field([], description="Cenários BDD associados")
    apiCalls: List[ApiCallSchema] = Field([], description="Passos de API do card")

class NodeSchema(BaseModel):
    id: str = Field(..., description="ID único do nó (React Flow)")
    type: str = Field("custom", description="Tipo do componente do nó")
    position: Dict[str, float] = Field(..., description="Coordenadas X e Y")
    width: Optional[float] = None
    height: Optional[float] = None
    hidden: bool = Field(False, description="Indica se o nó está oculto")
    parentNode: Optional[str] = None
    data: NodeDataBasic

class EdgeSchema(BaseModel):
    id: str = Field(..., description="ID da aresta")
    source: str = Field(..., description="ID do nó de origem")
    target: str = Field(..., description="ID do nó de destino")
    type: str = Field("buttonedge", description="Tipo visual da aresta")
    animated: bool = Field(True, description="Animação de fluxo")

class EnvSpecificData(BaseModel):
    description: str = ""
    bddScenarios: List[Dict[str, Any]] = []
    apiCalls: List[ApiCallSchema] = []
    e2eSteps: List[Dict[str, Any]] = []

class CardDataSchema(BaseModel):
    name: str = Field(..., description="Nome do card")
    color: str = Field("#10b981", description="Cor do card")
    description: str = Field("", description="Descrição detalhada")
    bddScenarios: List[Dict[str, Any]] = []
    apiCalls: List[ApiCallSchema] = []
    e2eSteps: List[Dict[str, Any]] = []
    envData: Dict[str, EnvSpecificData] = {}

class FlowSaveSchema(BaseModel):
    projectId: int = Field(..., description="ID do projeto (Feature)")
    flowId: Optional[int] = Field(None, description="ID do fluxo no banco de dados")
    name: Optional[str] = Field(None, description="Nome do fluxo")
    flow_type: str = Field("api", description="Tipo de fluxo: api ou e2e")
    nodes: List[NodeSchema]
    edges: List[EdgeSchema]
    cardData: Dict[str, CardDataSchema] = {}

class FlowCreateSchema(BaseModel):
    projectId: int = Field(..., description="ID do projeto")
    name: str = Field(..., description="Nome do fluxo")

class FlowResponse(BaseModel):
    id: int
    project_id: int
    name: str
    created_at: Any
    updated_at: Any
    nodes_count: int
    edges_count: int

    model_config = ConfigDict(from_attributes=True)
