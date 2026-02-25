from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.environment_schemas import EnvironmentCreate, EnvironmentResponse
from app.services.environment_service import EnvironmentService

router = APIRouter(tags=["Environments"])

# --- Environment Endpoints ---


from app.auth import get_current_user
from app.models.user_models import UserDB
    
from typing import List, Optional
    
@router.get("/environments", response_model=List[EnvironmentResponse])
def get_environments(
    project_id: Optional[int] = None, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    if project_id is None:
        return []
    return EnvironmentService.get_by_project(db, project_id)


@router.post("/environments", response_model=EnvironmentResponse)
def create_environment(
    env: EnvironmentCreate, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    return EnvironmentService.create(db, env)


@router.delete("/environments/{env_id}")
def delete_environment(
    env_id: int, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    success = EnvironmentService.delete(db, env_id)
    if not success:
        raise HTTPException(status_code=404, detail="Environment not found")

    return {"message": "Deleted successfully"}
