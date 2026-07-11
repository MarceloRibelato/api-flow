from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.auth import get_current_user
from app.models.user_models import UserDB
from app.schemas.auth_schemas import UserResponse, UserApprove

router = APIRouter(prefix="/admin", tags=["Admin"])

def check_admin(user: UserDB):
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Acesso negado. Apenas administradores."
        )

@router.get("/users", response_model=List[UserResponse])
def list_users(
    status: Optional[str] = None,
    current_user: UserDB = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    check_admin(current_user)
    
    query = db.query(UserDB)
    if status:
        query = query.filter(UserDB.status == status)
    
    return query.all()

@router.put("/users/{user_id}/approve")
def approve_user(
    user_id: int,
    approval_data: UserApprove,
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    check_admin(current_user)
    
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
        
    user.status = approval_data.status
    user.role = approval_data.role
    db.commit()
    
    return {"msg": "Usuário atualizado com sucesso", "status": user.status, "role": user.role}

@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    check_admin(current_user)
    
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
        
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Você não pode excluir a si mesmo.")
        
    try:
        db.delete(user)
        db.commit()
    except Exception as e:
        db.rollback()
        error_msg = str(e)
        if "foreign key constraint" in error_msg.lower() or "integrityerror" in error_msg.lower():
            raise HTTPException(
                status_code=400, 
                detail="Não é possível excluir este usuário pois ele possui dados vinculados (Histórico, Projetos, etc)."
            )
        raise HTTPException(status_code=500, detail=f"Erro ao excluir usuário: {error_msg}")
    
    return {"msg": "Usuário excluído com sucesso"}
