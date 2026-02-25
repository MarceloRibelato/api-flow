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
    try:
        return FlowService.list_by_project(db, project_id, company_id=current_user.company_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao listar fluxos: {str(e)}")


@router.post("/create")
def create_flow(
    data: dict, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
): 
    """Cria um novo fluxo vazio no projeto"""
    try:
        if current_user.role == 'viewer':
            raise HTTPException(status_code=403, detail="Sem permissão")

        project_id = data.get("projectId")
        name = data.get("name")
        if not project_id or not name:
             raise HTTPException(status_code=400, detail="projectId e name são obrigatórios")
             
        res = FlowService.create(db, project_id, name, company_id=current_user.company_id)
        if not res:
             raise HTTPException(status_code=403, detail="Projeto inválido")
        return res
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao criar fluxo: {str(e)}")


@router.get("/load/{project_id}")
def load_flow(
    project_id: int, 
    flow_id: int = None, 
    flow_type: str = "api", # New param
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Carrega o fluxo completo."""
    try:
        return FlowService.load(db, project_id, company_id=current_user.company_id, flow_id=flow_id, flow_type=flow_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao carregar fluxo: {str(e)}")


@router.post("/save")
def save_flow(
    data: FlowSaveSchema, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Salva o fluxo completo"""
    try:
        if current_user.role == 'viewer':
            raise HTTPException(status_code=403, detail="Sem permissão")

        if not data.projectId or data.projectId <= 0:
            raise HTTPException(
                status_code=400, detail="projectId deve ser um número positivo"
            )

        return FlowService.save(db, data, company_id=current_user.company_id)

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Dados inválidos: {str(e)}")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Erro interno ao salvar fluxo: {str(e)}"
        )


@router.delete("/{project_id}")
def delete_flow(
    project_id: int, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Remove o fluxo de um projeto"""
    try:
        if current_user.role == 'viewer':
            raise HTTPException(status_code=403, detail="Sem permissão")

        success = FlowService.delete(db, project_id, company_id=current_user.company_id)
        if not success:
            raise HTTPException(
                status_code=404, detail="Fluxo não encontrado para este projeto"
            )

        return {
            "status": "deleted",
            "message": f"Fluxo do projeto {project_id} removido com sucesso",
            "projectId": project_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao remover fluxo: {str(e)}")
