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
