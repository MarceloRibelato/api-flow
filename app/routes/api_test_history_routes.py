from datetime import datetime
import logging
from typing import List, Optional

import traceback
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
    execution_type: Optional[str] = Query(None, pattern="^(api|web)$"),
    batch_id: Optional[str] = None,
    sort_by: Optional[str] = Query('created_at', pattern="^(created_at|id|response_time)$"),
    order: Optional[str] = Query('desc', pattern="^(asc|desc)$"),
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
            batch_id,
            sort_by,
            order
        )
    except Exception as e:
        logging.error(f"❌ Error getting history: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao buscar: {str(e)}")



@router.get("/batches")
def get_history_batches(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    project_id: Optional[int] = None,
    flow_id: Optional[int] = None,
    schedule_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Retorna os batches agrupados e paginados"""
    try:
        return HistoryService.get_batches(
            db=db,
            company_id=current_user.company_id,
            page=page,
            limit=limit,
            project_id=project_id,
            flow_id=flow_id,
            schedule_type=schedule_type
        )
    except Exception as e:
        import logging
        logging.error(f"❌ Error getting history batches: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao buscar batches: {str(e)}")

@router.get("/averages")
def get_history_averages(
    schedule_id: Optional[int] = None,
    flow_id: Optional[int] = None,
    api_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Retorna a média de execução e taxa de sucesso dos steps com base em filtros"""
    try:
        stats = HistoryService.get_history_averages(db, schedule_id=schedule_id, flow_id=flow_id, api_id=api_id)
        return {"stats": stats}
    except Exception as e:
        logging.error(f"❌ Error getting averages: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao buscar médias: {str(e)}")


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
    """Remove um registro específico do histórico"""
    success = HistoryService.delete(db, execution_id, current_user.company_id)
    if not success:
        raise HTTPException(status_code=404, detail="Não encontrado")
    return {"status": "success", "message": "Registro removido"}


@router.delete("/batch/{batch_id}")
def delete_batch_history(
    batch_id: str,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Remove todos os registros associados a um batch_id"""
    success = HistoryService.delete_batch(db, batch_id, current_user.company_id)
    if not success:
        raise HTTPException(status_code=404, detail="Nenhum registro encontrado para este batch")
    return {"status": "success", "message": "Batch removido"}


@router.delete("/")
def clear_execution_history(
    confirm: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Remove TODO o histórico do usuário (Requer confirmação)"""
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Confirmação necessária. Use ?confirm=true para apagar tudo."
        )
    HistoryService.clear_all(db, current_user.company_id)
    return {"status": "success", "message": "Histórico limpo com sucesso"}


@router.get("/unique/apis", response_model=List[UniqueApiSummary])
def get_unique_apis(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user),
):
    """Retorna lista de APIs únicas (endpoint + método) que já foram executadas"""
    return HistoryService.get_unique_apis(db, current_user.company_id)
