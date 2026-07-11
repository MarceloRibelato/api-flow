from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.variable_schemas import VariableCreate, VariableResponse
from app.services.variable_service import VariableService

router = APIRouter(tags=["Variables"])

# --- Variable Endpoints ---


from app.auth import get_current_user
from app.models.user_models import UserDB    

@router.get("/variables", response_model=List[VariableResponse])
def get_variables(
    project_id: int,
    flow_id: Optional[int] = None,
    environment_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    vars = VariableService.get_all(db, project_id, environment_id)
    return vars


@router.post("/variables", response_model=VariableResponse)
def create_variable(
    var: VariableCreate, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    return VariableService.create(db, var)


@router.post("/variables/bulk", response_model=List[VariableResponse])
def bulk_create_variables(
    vars: List[VariableCreate], 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    return VariableService.bulk_create(db, vars)


@router.put("/variables/{var_id}", response_model=VariableResponse)
def update_variable(
    var_id: int, 
    var: VariableCreate, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    try:
        updated_var = VariableService.update(db, var_id, var)
        if not updated_var:
            raise HTTPException(status_code=404, detail="Variable not found")
        return updated_var
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal Error: {str(e)}")


@router.delete("/variables/{var_id}")
def delete_variable(
    var_id: int, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    success = VariableService.delete(db, var_id)
    if not success:
        raise HTTPException(status_code=404, detail="Variable not found")

    return {"message": "Deleted successfully"}


@router.delete("/variables/by-name/{name}")
def delete_variable_by_name(
    name: str,
    project_id: int,
    environment_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    success = VariableService.delete_by_name(db, name, project_id, environment_id)
    if not success:
        return {"message": "Variable not found (idempotent)"}

    return {"message": "Deleted successfully"}
