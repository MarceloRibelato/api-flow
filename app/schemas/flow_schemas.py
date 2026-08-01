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
    timeout: int = Field(10000, description="Timeout em milissegundos")
    delay: int = Field(0, description="Atraso antes da execução em ms")
    retries: int = Field(0, description="Número de retentativas se a requisição falhar")
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

class MessageQueueSchema(BaseModel):
    id: str = Field(..., description="ID único do passo")
    name: Optional[str] = Field("Nova Mensageria", description="Nome amigável")
    broker: str = Field("rabbitmq", description="rabbitmq, sqs, service_bus")
    action: str = Field("publish", description="publish ou consume")
    connectionString: str = Field(..., description="Connection string ou credenciais")
    queueName: str = Field(..., description="Nome da fila ou tópico")
    payload: Optional[Union[str, Dict[str, Any]]] = Field(None, description="Mensagem a publicar")
    headers: List[Dict[str, str]] = Field([], description="Propriedades/Headers da mensagem")
    timeout: int = Field(10000, description="Tempo limite em ms")
    assertions: List[AssertionRule] = Field([], description="Validações (para consume)")
    extracts: List[ExtractionRule] = Field([], description="Extrações (para consume)")

    @field_validator('payload')
    @classmethod
    def stringify_payload(cls, v):
        if v is None:
            return None
        if isinstance(v, (dict, list)):
            return json.dumps(v)
        return str(v)

class NodeDataBasic(BaseModel):
    name: str = Field(..., description="Nome exibido no nó")
    color: str = Field("#10b981", description="Cor de destaque do nó")
    nodeType: Optional[str] = Field("api", description="Tipo do nó: api, database, queue, etc")
    childCount: int = Field(0, description="Número de filhos diretos")
    isCollapsed: bool = Field(False, description="Estado de colapso visual")
    layoutDirection: Optional[str] = Field(None, description="Direção do layout (LR ou TB)")
    isMainFlow: bool = Field(False, description="Indica se é o caminho principal (happy path)")
    dbQueries: Optional[List[Dict[str, Any]]] = []
    messageQueues: Optional[List[MessageQueueSchema]] = []

class NodeDataFull(BaseModel):
    name: str = Field(..., description="Nome do nó")
    color: str = Field("#10b981", description="Cor do nó")
    nodeType: Optional[str] = Field("api", description="Tipo do nó: api, database, queue, etc")
    description: str = Field("", description="Descrição do objetivo do nó")
    childCount: int = Field(0, description="Número de filhos")
    isCollapsed: bool = Field(False, description="Estado de colapso")
    isMainFlow: bool = Field(False, description="Indica se é o caminho principal (happy path)")
    bddScenarios: List[Dict[str, Any]] = Field([], description="Cenários BDD associados")
    apiCalls: List[ApiCallSchema] = Field([], description="Passos de API do card")
    dbQueries: List[Dict[str, Any]] = Field([], description="Consultas de banco de dados do card")
    messageQueues: List[MessageQueueSchema] = Field([], description="Ações de mensageria")

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
    nodeType: Optional[str] = Field("api", description="Tipo do nó: api, database, queue, etc")
    description: str = Field("", description="Descrição detalhada")
    isMainFlow: bool = Field(False, description="Indica se é o caminho principal (happy path)")
    bddScenarios: List[Dict[str, Any]] = []
    apiCalls: List[ApiCallSchema] = []
    e2eSteps: List[Dict[str, Any]] = []
    dbQueries: List[Dict[str, Any]] = []
    messageQueues: List[MessageQueueSchema] = []
    envData: Dict[str, EnvSpecificData] = {}

class FlowSaveSchema(BaseModel):
    projectId: int = Field(..., description="ID do projeto (Feature)")
    flowId: Optional[int] = Field(None, description="ID do fluxo no banco de dados")
    name: Optional[str] = Field(None, description="Nome do fluxo")
    flow_type: str = Field("api", description="Tipo de fluxo: api, e2e ou mobile")
    nodes: List[NodeSchema]
    edges: List[EdgeSchema]
    cardData: Dict[str, CardDataSchema] = {}
    
    @field_validator('flow_type', mode='before')
    @classmethod
    def validate_flow_type(cls, v):
        allowed_types = {"api", "e2e", "mobile"}
        if not v or str(v).lower() not in allowed_types:
            return "api" # Fallback robusto para previnir lixo no DB
        return str(v).lower()

class FlowCreateSchema(BaseModel):
    projectId: int = Field(..., description="ID do projeto")
    name: str = Field(..., description="Nome do fluxo")

class TestDbSchema(BaseModel):
    connectionString: str = Field(..., description="Connection string")
    query: str = Field(..., description="SQL Query")
    environmentId: Optional[int] = Field(None, description="ID do ambiente para variáveis")
    assertions: Optional[List[Dict[str, Any]]] = []
    extracts: Optional[List[Dict[str, Any]]] = []

class HealStepSchema(BaseModel):
    project_id: int = Field(..., description="ID do Projeto (Feature)")
    flow_id: int = Field(..., description="ID do Fluxo")
    node_id: str = Field(..., description="ID do Nó E2E")
    old_selector: str = Field(..., description="Seletor antigo que falhou")
    new_selector: str = Field(..., description="Novo seletor curado pela IA")

class FlowResponse(BaseModel):
    id: int
    project_id: int
    name: str
    created_at: Any
    updated_at: Any
    nodes_count: int
    edges_count: int

    model_config = ConfigDict(from_attributes=True)
