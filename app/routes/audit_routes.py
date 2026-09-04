from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user
from app.models.user_models import UserDB
from app.services.audit_service import AuditService

router = APIRouter(prefix="/audit", tags=["Audit Logs"])


@router.get("/logs")
def get_audit_logs(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Retorna os registros de auditoria da empresa com suporte a paginação e filtros."""
    return AuditService.list_logs(
        db=db,
        company_id=current_user.company_id,
        page=page,
        per_page=per_page,
        action=action,
        resource_type=resource_type,
        search=search
    )


@router.get("/actions")
def get_audit_actions(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Retorna a lista de categorias de ações registradas para preencher o filtro da UI."""
    return {
        "actions": AuditService.get_action_types(db=db, company_id=current_user.company_id)
    }
