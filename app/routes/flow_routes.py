from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.schemas.flow_schemas import FlowSaveSchema
from app.services.flow_service import FlowService

from ..database import get_db

router = APIRouter(prefix="/flow", tags=["Flow"])


@router.get("/list/{project_id}")
def list_flows(project_id: int, db: Session = Depends(get_db)):
    """Lista todos os fluxos de um projeto"""
    try:
        return FlowService.list_by_project(db, project_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao listar fluxos: {str(e)}")


@router.post("/create")
def create_flow(data: dict, db: Session = Depends(get_db)): # Use schema properly if created
    """Cria um novo fluxo vazio no projeto"""
    try:
        project_id = data.get("projectId")
        name = data.get("name")
        if not project_id or not name:
             raise HTTPException(status_code=400, detail="projectId e name são obrigatórios")
             
        return FlowService.create(db, project_id, name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao criar fluxo: {str(e)}")


@router.get("/load/{project_id}")
def load_flow(project_id: int, flow_id: int = None, db: Session = Depends(get_db)):
    """Carrega o fluxo completo. Se flow_id não for passado, carrega o mais recente."""
    try:
        return FlowService.load(db, project_id, flow_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao carregar fluxo: {str(e)}")


@router.post("/save")
def save_flow(data: FlowSaveSchema, db: Session = Depends(get_db)):
    """Salva o fluxo completo"""
    try:
        # Validation
        if not data.projectId or data.projectId <= 0:
            raise HTTPException(
                status_code=400, detail="projectId deve ser um número positivo"
            )

        return FlowService.save(db, data)

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Dados inválidos: {str(e)}")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Erro interno ao salvar fluxo: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Erro ao obter estatísticas: {str(e)}"
        )


@router.delete("/{project_id}")
def delete_flow(project_id: int, db: Session = Depends(get_db)):
    """Remove o fluxo de um projeto"""
    try:
        success = FlowService.delete(db, project_id)
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
