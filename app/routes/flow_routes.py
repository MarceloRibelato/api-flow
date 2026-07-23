from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.schemas.flow_schemas import FlowSaveSchema
from app.services.flow_service import FlowService

from ..database import get_db

from ..auth import get_current_user
from ..models.user_models import UserDB

router = APIRouter(prefix="/flow", tags=["Flow"])


@router.get("/list/{project_id}")
def list_flows(
    project_id: int, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Lista todos os fluxos de um projeto"""
    return FlowService.list_by_project(db, project_id, company_id=current_user.company_id)


@router.post("/create")
def create_flow(
    data: dict, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
): 
    """Cria um novo fluxo vazio no projeto"""
    from app.exceptions import InsufficientPermissionsError, ValidationError, NotFoundError
    
    if current_user.role == 'viewer':
        raise InsufficientPermissionsError()

    project_id = data.get("projectId")
    name = data.get("name")
    if not project_id or not name:
         raise ValidationError(detail="projectId e name são obrigatórios")
         
    res = FlowService.create(db, project_id, name, company_id=current_user.company_id)
    if not res:
         raise NotFoundError(resource="Projeto")
    return res


@router.get("/load/{project_id}")
def load_flow(
    project_id: int, 
    flow_id: int = None, 
    flow_type: str = "api", # New param
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Carrega o fluxo completo."""
    return FlowService.load(db, project_id, company_id=current_user.company_id, flow_id=flow_id, flow_type=flow_type)


@router.post("/save")
def save_flow(
    data: FlowSaveSchema, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Salva o fluxo completo"""
    from app.exceptions import InsufficientPermissionsError, ValidationError
    
    if current_user.role == 'viewer':
        raise InsufficientPermissionsError()

    if not data.projectId or data.projectId <= 0:
        raise ValidationError(detail="projectId deve ser um número positivo")

    try:
        return FlowService.save(db, data, company_id=current_user.company_id, user_id=current_user.id)
    except ValueError as e:
        raise ValidationError(detail=str(e))


@router.delete("/{project_id}")
def delete_flow(
    project_id: int, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Remove o fluxo de um projeto"""
    from app.exceptions import InsufficientPermissionsError, NotFoundError
    
    if current_user.role == 'viewer':
        raise InsufficientPermissionsError()

    success = FlowService.delete(db, project_id, company_id=current_user.company_id)
    if not success:
        raise NotFoundError(resource="Fluxo do projeto")

    return {
        "status": "deleted",
        "message": f"Fluxo do projeto {project_id} removido com sucesso",
        "projectId": project_id,
    }
@router.get("/stats/{project_id}")
def get_flow_stats(
    project_id: int, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Retorna estatísticas do fluxo (nós, arestas, cards, bdd, api)"""
    return FlowService.get_stats(db, project_id)


@router.get("/cards/inventory")
def get_cards_inventory(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Retorna o inventário de cards da empresa"""
    return FlowService.get_cards_inventory(db, company_id=current_user.company_id)

from app.schemas.flow_schemas import TestDbSchema
from app.services.database_executor_service import DatabaseExecutorService
from app.services.variable_service import VariableService

@router.post("/test-db")
def test_db_connection(
    data: TestDbSchema,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Testa uma consulta de banco de dados e retorna o resultado"""
    variables_dict = {}
    
    if data.environmentId:
        from app.models.variable_model import Variable
        vars_db = db.query(Variable).filter(Variable.environment_id == data.environmentId).all()
        variables_dict = {v.name: v.value for v in vars_db}

    step_data = {
        "connection_string": data.connectionString,
        "query": data.query,
        "timeout": 10,
        "assertions": data.assertions or [],
        "extracts": data.extracts or []
    }
    
    return DatabaseExecutorService.execute_db_step(step_data, variables_dict)

from app.schemas.flow_schemas import HealStepSchema
@router.put("/heal-step")
def heal_flow_step(
    data: HealStepSchema,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Aplica uma correção de auto-healing ao fluxo persistido"""
    try:
        updated_flow = FlowService.apply_healing(db, data.project_id, current_user.company_id, data.flow_id, data.node_id, data.old_selector, data.new_selector)
        return {"status": "success", "message": "Healing applied successfully", "flow": updated_flow}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
