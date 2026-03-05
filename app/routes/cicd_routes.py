from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional, Union
import secrets
import hashlib
from datetime import datetime

from app.database import get_db
from app.auth import get_current_user, hash_token
from app.models.user_models import UserDB
from app.models.service_token_models import ServiceTokenDB
from app.models.product_models import ProductModel
from app.models.feature_models import FeatureModel
from app.models.environment_model import Environment
from app.models.schedule_models import ScheduleModel
from app.models.api_test_history_models import ApiExecutionHistory
from app.services.scheduler_service import execute_job
from pydantic import BaseModel

router = APIRouter(
    prefix="/cicd",
    tags=["CI/CD Integration"]
)

# --- Schemas ---
class TokenCreate(BaseModel):
    name: str

class TokenRead(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_used_at: Optional[datetime] = None

class ExecutionRequestCICD(BaseModel):
    product_name: str
    feature_name: Optional[str] = 'all'
    environment_name: str
    execution_name: Optional[str] = None

# --- Token Management ---
@router.get("/tokens", response_model=List[TokenRead])
def list_tokens(db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    """Lista os tokens de serviço do usuário logado."""
    return db.query(ServiceTokenDB).filter(ServiceTokenDB.user_id == current_user.id).all()

@router.post("/tokens")
def create_token(req: TokenCreate, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    """Gera um novo token de serviço (API Key)."""
    print(f"DEBUG: create_token request received. Name: {req.name}, User: {current_user.id}, Company: {current_user.company_id}")
    try:
        raw_token = f"flw_{secrets.token_urlsafe(32)}"
        token_hash = hash_token(raw_token)
        
        # Ensure company_id is available
        company_id = current_user.company_id
        if company_id is None:
            # Fallback to first company found if user has no company assigned (should not happen in production)
            from app.models.company_models import CompanyDB
            first_company = db.query(CompanyDB).first()
            if first_company:
                company_id = first_company.id
                print(f"DEBUG: User had no company_id, using fallback: {company_id}")
            else:
                raise HTTPException(status_code=400, detail="User has no company and no companies found in DB")

        new_token = ServiceTokenDB(
            name=req.name,
            token_hash=token_hash,
            user_id=current_user.id,
            company_id=company_id
        )
        db.add(new_token)
        db.commit()
        db.refresh(new_token)
        
        print(f"DEBUG: Token created successfully. ID: {new_token.id}")
        return {
            "id": new_token.id,
            "name": new_token.name,
            "token": raw_token
        }
    except Exception as e:
        print(f"ERROR in create_token: {str(e)}")
        import traceback
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro interno ao gerar token: {str(e)}")

@router.delete("/tokens/{token_id}")
def delete_token(token_id: int, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    """Revoga um token de serviço."""
    token = db.query(ServiceTokenDB).filter(
        ServiceTokenDB.id == token_id, 
        ServiceTokenDB.user_id == current_user.id
    ).first()
    
    if not token:
        raise HTTPException(status_code=404, detail="Token não encontrado")
        
    db.delete(token)
    db.commit()
    return {"message": "Token revogado com sucesso"}

# --- CI/CD Execution ---
@router.post("/execute")
def execute_cicd(
    req: ExecutionRequestCICD, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Dispara a execução de um Produto ou Funcionalidade via nomes amigáveis."""
    # 1. Resolver Produto
    product = db.query(ProductModel).filter(
        ProductModel.name == req.product_name,
        ProductModel.company_id == current_user.company_id
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail=f"Produto '{req.product_name}' não encontrado")

    # 2. Resolver Ambiente
    env = db.query(Environment).filter(
        Environment.name == req.environment_name,
        Environment.project_id == product.id
    ).first()
    if not env:
        raise HTTPException(status_code=404, detail=f"Ambiente '{req.environment_name}' não encontrado para este produto")

    # 3. Resolver Alvo (Feature ou Suite completa)
    target_id = product.id
    target_type = 'suite'
    
    if req.feature_name and req.feature_name.lower() != 'all':
        feature = db.query(FeatureModel).filter(
            FeatureModel.name == req.feature_name,
            FeatureModel.product_id == product.id
        ).first()
        if not feature:
            raise HTTPException(status_code=404, detail=f"Feature '{req.feature_name}' não encontrada")
        target_id = feature.id
        target_type = 'feature'

    # 4. Criar Agendamento (Execução Pontual)
    from datetime import timezone
    exec_name = req.execution_name or f"CI/CD: {req.product_name} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    new_schedule = ScheduleModel(
        name=exec_name,
        type=target_type,
        target_id=target_id,
        environment_id=env.id,
        cron_expression=None,
        run_at=datetime.now(timezone.utc),
        status='active',
        company_id=current_user.company_id,
        user_id=current_user.id
    )
    db.add(new_schedule)
    db.commit()
    db.refresh(new_schedule)

    # 5. Disparar Execução
    # Nota: Usamos o scheduler_service para manter consistência com execuções manuais/agendadas
    try:
        execute_job(new_schedule.id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao iniciar execução: {str(e)}")

    return {
        "job_id": new_schedule.id,
        "status": "triggered",
        "target": target_type,
        "target_id": target_id
    }

@router.get("/status/{job_id}")
def job_status(job_id: int, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    """Consulta o status e resultado de uma execução disparada via CI/CD."""
    job = db.query(ScheduleModel).filter(
        ScheduleModel.id == job_id,
        ScheduleModel.company_id == current_user.company_id
    ).first()
    
    if not job:
        raise HTTPException(status_code=404, detail="Execução não encontrada")

    # Buscamos os resultados no histórico associado a este job_id
    results = db.query(ApiExecutionHistory).filter(ApiExecutionHistory.schedule_id == job_id).all()
    
    if not results:
        return {
            "job_id": job_id, 
            "status": "pending",
            "message": "Execução em fila ou processando..."
        }
    
    # Avaliamos se houve falhas (status >= 400)
    failed_requests = [r for r in results if r.status_code >= 400]
    any_failure = len(failed_requests) > 0
    
    return {
        "job_id": job_id,
        "status": "completed",
        "result": "failure" if any_failure else "success",
        "total_requests": len(results),
        "failed_count": len(failed_requests),
        "started_at": job.run_at,
        "finished_at": results[-1].created_at if results else None
    }
