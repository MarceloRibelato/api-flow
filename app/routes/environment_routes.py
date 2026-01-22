from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.environment_schemas import EnvironmentCreate, EnvironmentResponse
from app.services.environment_service import EnvironmentService

router = APIRouter()

# --- Environment Endpoints ---


@router.get("/environments", response_model=List[EnvironmentResponse])
def get_environments(project_id: int, db: Session = Depends(get_db)):
    return EnvironmentService.get_by_project(db, project_id)


@router.post("/environments", response_model=EnvironmentResponse)
def create_environment(env: EnvironmentCreate, db: Session = Depends(get_db)):
    return EnvironmentService.create(db, env)


@router.delete("/environments/{env_id}")
def delete_environment(env_id: int, db: Session = Depends(get_db)):
    success = EnvironmentService.delete(db, env_id)
    if not success:
        raise HTTPException(status_code=404, detail="Environment not found")

    return {"message": "Deleted successfully"}
