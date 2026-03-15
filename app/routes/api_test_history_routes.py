from datetime import datetime
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user_models import UserDB
from app.schemas.history_schemas import (
    ExecutionHistoryCreate,
    ExecutionHistoryResponse,
    PaginatedHistoryResponse,
    UniqueApiSummary,
)
from app.services.history_service import HistoryService


from ..auth import get_current_user

router = APIRouter(prefix="/history", tags=["Execution History"])


@router.post(
    "/", response_model=ExecutionHistoryResponse, status_code=status.HTTP_201_CREATED
)
def save_execution_history(
    history: ExecutionHistoryCreate,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Salva um novo registro de execução de API"""
    try:
        return HistoryService.save(db, history, current_user.id)
    except Exception as e:
        logging.error(f"❌ Error saving history: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao salvar: {str(e)}")


@router.get("/", response_model=PaginatedHistoryResponse)
def get_execution_history(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=1000),
    api_id: Optional[int] = None,
    project_id: Optional[int] = None,
    flow_id: Optional[int] = None,
    environment_id: Optional[int] = None,
    schedule_id: Optional[int] = None,  # Added filter
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    method: Optional[str] = None,
    status_code: Optional[int] = None,
    node_id: Optional[str] = None,
    execution_type: Optional[str] = Query(None, regex="^(api|web)$"),
    sort_by: Optional[str] = Query('created_at', regex="^(created_at|id|response_time)$"),
    order: Optional[str] = Query('desc', regex="^(asc|desc)$"),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Retorna o histórico paginado e filtrado (Unificado)"""
    try:
        return HistoryService.get_all(
            db,
            current_user.company_id,  # Updated: company_id
            page,
            limit,
            api_id,
            project_id,
            flow_id,
            environment_id,
            schedule_id,  # Passed to service
            start_date,
            end_date,
            method,
            status_code,
            node_id,
            execution_type,
            sort_by,
            order
        )
    except Exception as e:
        logging.error(f"❌ Error getting history: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao buscar: {str(e)}")


@router.get("/{execution_id}", response_model=ExecutionHistoryResponse)
def get_execution_detail(
    execution_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    history = HistoryService.get_by_id(db, execution_id, current_user.company_id) # Updated
    if not history:
        raise HTTPException(status_code=404, detail="Não encontrado")
    return history


@router.delete("/{execution_id}")
def delete_execution_history(
    execution_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    success = HistoryService.delete(db, execution_id, current_user.company_id) # Updated
    if not success:
        raise HTTPException(status_code=404, detail="Não encontrado")
    return {"message": "Removido"}


@router.delete("/")
def clear_execution_history(
    db: Session = Depends(get_db),
    confirm: bool = Query(False),
    current_user: UserDB = Depends(get_current_user),
):
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmação necessária")

    HistoryService.clear_all(db, current_user.company_id) # Updated
    return {"message": "Histórico limpo"}


@router.get("/unique/apis", response_model=List[UniqueApiSummary])
def get_unique_apis(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Retorna lista única de APIs (método/url) para filtros"""
    return HistoryService.get_unique_apis(db, current_user.company_id) # Updated

