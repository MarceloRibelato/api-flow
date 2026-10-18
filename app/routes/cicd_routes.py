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
from app.models.api_test_history_models import ApiExecutionHistory, WebExecutionHistory, MobileExecutionHistory
from app.services.scheduler_service import execute_job
from app.services.audit_service import AuditService
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
    flow_type: Optional[str] = 'api' # 'api', 'e2e', or 'performance'
    
    # Performance parameters (only used if flow_type == 'performance')
    virtual_users: Optional[int] = 10
    duration_seconds: Optional[int] = 30
    ramp_up_seconds: Optional[int] = 0
    
    # Data-driven parameter
    dataset: Optional[list] = None

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
        
        AuditService.log_action(
            db=db,
            company_id=company_id,
            user=current_user,
            action="CREATE_SERVICE_TOKEN",
            resource_type="service_token",
            resource_id=str(new_token.id),
            resource_name=new_token.name,
            details={"token_id": new_token.id, "token_name": new_token.name}
        )

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
        
    token_name = token.name
    db.delete(token)
    db.commit()

    AuditService.log_action(
        db=db,
        company_id=current_user.company_id,
        user=current_user,
        action="DELETE_SERVICE_TOKEN",
        resource_type="service_token",
        resource_id=str(token_id),
        resource_name=token_name,
        details={"token_id": token_id, "token_name": token_name}
    )

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
    clean_prod_name = (req.product_name or "").strip()
    product_query = db.query(ProductModel).filter(ProductModel.name.ilike(clean_prod_name))
    if current_user.company_id:
        product_query = product_query.filter(
            (ProductModel.company_id == current_user.company_id) | (ProductModel.company_id.is_(None))
        )
    product = product_query.first()

    # Fallback A: Resolver pelo nome do Time (TeamDB) caso o usuário esteja executando a partir de um Team
    if not product and clean_prod_name:
        from app.models.team_models import TeamDB
        team_query = db.query(TeamDB).filter(TeamDB.name.ilike(clean_prod_name))
        if current_user.company_id:
            team_query = team_query.filter(TeamDB.company_id == current_user.company_id)
        team = team_query.first()
        if team:
            product = db.query(ProductModel).filter(ProductModel.team_id == team.id).first()

    # Fallback B: Se informou feature_name específico, localizar o produto dono da feature na empresa
    if not product and req.feature_name and req.feature_name.lower() != 'all':
        feat_match = db.query(FeatureModel).join(ProductModel).filter(
            FeatureModel.name.ilike(req.feature_name.strip()),
            (ProductModel.company_id == current_user.company_id) | (ProductModel.company_id.is_(None))
        ).first()
        if feat_match:
            product = db.query(ProductModel).filter(ProductModel.id == feat_match.product_id).first()

    # Fallback C: Se o nome enviado for genérico ('Product', 'Produto') e houver produto na empresa
    if not product and clean_prod_name.lower() in ('product', 'produto', 'default'):
        fallback_q = db.query(ProductModel)
        if current_user.company_id:
            fallback_q = fallback_q.filter(
                (ProductModel.company_id == current_user.company_id) | (ProductModel.company_id.is_(None))
            )
        product = fallback_q.first()

    if not product:
        raise HTTPException(status_code=404, detail=f"Produto '{req.product_name}' não encontrado")

    # 2. Resolver Ambiente
    clean_env_name = (req.environment_name or "DEV").strip()
    candidate_project_ids = [product.id]
    if getattr(product, 'team_id', None) and product.team_id not in candidate_project_ids:
        candidate_project_ids.append(product.team_id)

    env = db.query(Environment).filter(
        Environment.name.ilike(clean_env_name),
        Environment.project_id.in_(candidate_project_ids)
    ).first()
    if not env:
        env = db.query(Environment).filter(Environment.project_id.in_(candidate_project_ids)).first()
    if not env and current_user.company_id:
        company_prod_ids = [
            p.id for p in db.query(ProductModel.id).filter(
                (ProductModel.company_id == current_user.company_id) | (ProductModel.company_id.is_(None))
            ).all()
        ]
        if company_prod_ids:
            env = db.query(Environment).filter(
                Environment.name.ilike(clean_env_name),
                Environment.project_id.in_(company_prod_ids)
            ).first()
            if not env:
                env = db.query(Environment).filter(Environment.project_id.in_(company_prod_ids)).first()
    if not env:
        env = Environment(
            name=clean_env_name or "DEV",
            project_id=product.id
        )
        db.add(env)
        db.commit()
        db.refresh(env)

    # 3. Resolver Alvo (Feature ou Suite completa)
    target_id = product.id
    target_type = 'suite'
    
    if req.feature_name and req.feature_name.lower() != 'all':
        clean_feat_name = req.feature_name.strip()
        feature = db.query(FeatureModel).filter(
            FeatureModel.name.ilike(clean_feat_name),
            FeatureModel.product_id == product.id
        ).first()
        if not feature:
            feature = db.query(FeatureModel).join(ProductModel).filter(
                FeatureModel.name.ilike(clean_feat_name),
                (ProductModel.company_id == current_user.company_id) | (ProductModel.company_id.is_(None))
            ).first()
        if not feature:
            feature = db.query(FeatureModel).filter(FeatureModel.product_id == product.id).first()
        if not feature:
            raise HTTPException(status_code=404, detail=f"Feature '{req.feature_name}' não encontrada")
        target_id = feature.id
        target_type = 'feature'

    # 4. Criar Agendamento (Execução Pontual)
    from datetime import timezone
    exec_name = req.execution_name or f"CI/CD: {req.product_name} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    if not exec_name.startswith("[PIPELINE]") and not exec_name.startswith("CI/CD"):
        exec_name = f"[PIPELINE] {exec_name}"
    
    new_schedule = ScheduleModel(
        name=exec_name,
        type=target_type,
        target_id=target_id,
        environment_id=env.id,
        cron_expression=None,
        run_at=datetime.now(timezone.utc),
        status='active',
        company_id=current_user.company_id,
        user_id=current_user.id,
        is_manual=True,
        flow_type=req.flow_type or 'api',
        virtual_users=req.virtual_users if req.flow_type == 'performance' else None,
        duration_seconds=req.duration_seconds if req.flow_type == 'performance' else None,
        ramp_up_seconds=req.ramp_up_seconds if req.flow_type == 'performance' else None,
        dataset=req.dataset
    )
    db.add(new_schedule)
    db.commit()
    db.refresh(new_schedule)

    AuditService.log_action(
        db=db,
        company_id=current_user.company_id,
        user=current_user,
        action="EXECUTE_PIPELINE",
        resource_type="execution",
        resource_id=str(new_schedule.id),
        resource_name=exec_name,
        details={
            "product_name": req.product_name,
            "feature_name": req.feature_name,
            "environment_name": req.environment_name,
            "flow_type": req.flow_type,
            "schedule_id": new_schedule.id,
            "product_id": product.id,
            "project_id": product.id
        }
    )

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
def job_status(job_id: str, db: Session = Depends(get_db), current_user: UserDB = Depends(get_current_user)):
    """Consulta o status e resultado de uma execução disparada via CI/CD."""
    if job_id.startswith("{{") and job_id.endswith("}}"):
        return {
            "job_id": job_id, 
            "status": "pending",
            "message": f"Aguardando resolução da variável {job_id}..."
        }

    try:
        numeric_job_id = int(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="job_id deve ser um número inteiro válido.")

    job_query = db.query(ScheduleModel).filter(ScheduleModel.id == numeric_job_id)
    if current_user.company_id:
        job_query = job_query.filter(
            (ScheduleModel.company_id == current_user.company_id) | (ScheduleModel.company_id.is_(None))
        )
    job = job_query.first()
    
    if not job:
        raise HTTPException(status_code=404, detail="Execução não encontrada")

    # Determine history model based on flow_type
    flow_t = getattr(job, 'flow_type', 'api') or 'api'
    if flow_t == 'e2e':
        PrimaryModel = WebExecutionHistory
    elif flow_t == 'mobile':
        PrimaryModel = MobileExecutionHistory
    else:
        PrimaryModel = ApiExecutionHistory

    results = db.query(PrimaryModel).filter(
        PrimaryModel.schedule_id == numeric_job_id
    ).order_by(PrimaryModel.created_at.desc()).all()
    
    if not results:
        # Fallback to check other tables
        for FallbackModel in (WebExecutionHistory, ApiExecutionHistory, MobileExecutionHistory):
            if FallbackModel != PrimaryModel:
                results = db.query(FallbackModel).filter(
                    FallbackModel.schedule_id == numeric_job_id
                ).order_by(FallbackModel.created_at.desc()).all()
                if results:
                    break
    
    # Se o agendamento pontual ainda está ativo ou rodando no worker, não encerrar prematuramente
    if not job.cron_expression and job.status == 'running':
        return {
            "job_id": numeric_job_id,
            "status": "running",
            "completed_requests": len(results),
            "message": "Execução em andamento..."
        }

    if not results:
        if job.status in ('completed', 'failure'):
            is_fail = (job.last_run_status == 'failure' or job.status == 'failure')
            return {
                "job_id": numeric_job_id,
                "status": "completed",
                "result": "failure" if is_fail else "success",
                "total_requests": 0,
                "failed_count": 1 if is_fail else 0,
                "started_at": job.last_run or job.run_at,
                "finished_at": job.last_run or datetime.now()
            }
        return {
            "job_id": numeric_job_id, 
            "status": "pending",
            "message": "Execução em fila ou processando..."
        }
    
    # Agrupa pelo retry_count mais recente da execução (inclui todos os paths e features da suíte)
    latest_retry = getattr(results[0], 'retry_count', 0) or 0
    latest_results = [r for r in results if (getattr(r, 'retry_count', 0) or 0) == latest_retry]
    
    # Avaliamos se houve falhas verificando o error_message (fonte da verdade p/ assertion/erro)
    failed_requests = [r for r in latest_results if bool(r.error_message)]
    any_failure = len(failed_requests) > 0 or job.last_run_status == 'failure'
    
    return {
        "job_id": numeric_job_id,
        "status": "completed",
        "result": "failure" if any_failure else "success",
        "total_requests": len(latest_results),
        "failed_count": len(failed_requests),
        "started_at": latest_results[-1].created_at if latest_results else job.run_at,
        "finished_at": latest_results[0].created_at if latest_results else None
    }
