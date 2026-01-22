from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.project_schemas import ProjectCreate, ProjectResponse, ReorderSchema
from app.services.project_service import ProjectService

from ..auth import get_current_user
from ..models.user_models import UserDB

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post("/", response_model=ProjectResponse)
def create_project(
    project: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    return ProjectService.create(db, project)


@router.put("/reorder")
def reorder_projects(
    data: ReorderSchema,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    success = ProjectService.reorder(db, data)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to reorder projects")
    return {"status": "success"}


@router.get("/", response_model=List[ProjectResponse])
def read_projects(
    db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)
):
    return ProjectService.get_all(db)


@router.get("/{project_id}", response_model=ProjectResponse)
def read_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    project = ProjectService.get_by_id(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")
    return project


@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: int,
    project_update: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    updated_project = ProjectService.update(db, project_id, project_update)
    if not updated_project:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")
    return updated_project


@router.delete("/{project_id}")
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    success = ProjectService.delete(db, project_id)
    if not success:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")
    return {"message": f"Projeto {project_id} removido com sucesso"}
