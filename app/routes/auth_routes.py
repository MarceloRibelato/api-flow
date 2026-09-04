import logging
import re

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth import get_current_user, verify_password
from app.database import get_db
from app.exceptions import (
    InvalidCredentialsError, AccountPendingError, DuplicateResourceError,
    ValidationError as DomainValidationError, FlowException
)
from app.models.user_models import UserDB
from app.schemas.auth_schemas import Token, UserCreate, UserUpdate, LoginRequest, ForgotPasswordRequest, ResetPasswordRequest
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.post("/login", response_model=Token)
def login(
    login_data: LoginRequest, db: Session = Depends(get_db)
):
    """
    Endpoint para autenticação via JSON.
    Retorna o token JWT (Bearer) se as credenciais forem válidas.
    """
    logger.info("=== LOGIN REQUEST ===")
    logger.info(f"Username attempt: {login_data.username}")

    user = AuthService.get_user_by_username(db, login_data.username)
    if not user:
        logger.warning(f"Login failed: User '{login_data.username}' not found in database.")
        raise InvalidCredentialsError()

    if not verify_password(login_data.password, user.hashed_password):
        logger.warning(f"Login failed: Incorrect password for user '{login_data.username}'.")
        raise InvalidCredentialsError()

    if user.status and user.status not in ['active', 'approved']:
        logger.warning(f"Login failed: User '{login_data.username}' is in status '{user.status}'.")
        raise AccountPendingError()

    logger.info(f"Login successful for user: {login_data.username}")
    return AuthService.create_token_response(user)


@router.post("/logout")
def logout():
    """
    Realiza o logout do usuário (Front-end deve descartar o token).
    """
    return {"msg": "Logout realizado com sucesso"}


@router.post("/create")
def register(user: UserCreate, db: Session = Depends(get_db)):
    """
    Registra um novo usuário no banco de dados.
    """
    logger.info("=== REGISTER REQUEST RECEIVED ===")

    # Verifica duplicidade
    existing_user = AuthService.get_user_by_username(db, user.username)
    if existing_user:
        raise DuplicateResourceError("Username")

    if user.email:
        existing_email = db.query(UserDB).filter(UserDB.email == user.email).first()
        if existing_email:
            raise DuplicateResourceError("Email")

    if user.cpf:
        cpf_clean = re.sub(r"[.-]", "", user.cpf)
        existing_cpf = db.query(UserDB).filter(UserDB.cpf == cpf_clean).first()
        if existing_cpf:
            raise DuplicateResourceError("CPF")

    try:
        new_user = AuthService.create_user(db, user)
        logger.info(f"User successfully saved to database. ID: {new_user.id}")

        return {
            "msg": "Usuário criado com sucesso",
            "id": new_user.id,
            "username": new_user.username,
            "email": new_user.email,
            "full_name": new_user.full_name,
            "cpf": AuthService.format_cpf(new_user.cpf),
            "company": new_user.company, 
            "company_id": new_user.company_id,
            "role": new_user.role,
            "cnpj": AuthService.format_cnpj(new_user.cnpj),
            "phone": AuthService.format_phone(new_user.phone),
        }

    except ValueError as ve:
        db.rollback()
        logger.warning(f"Validation Error: {str(ve)}")
        raise DomainValidationError(detail=str(ve))
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        error_msg = str(e)
        if "unique constraint" in error_msg.lower() or "integrityerror" in error_msg.lower():
             logger.warning(f"Integrity Error: {error_msg}")
             raise DuplicateResourceError(detail="Dados duplicados (Usuário, Email ou CPF já existem).")
        
        logger.error(f"Database error: {error_msg}")
        raise FlowException(detail="Erro interno do servidor", status_code=500)


@router.get("/profile")
def get_profile(current_user: UserDB = Depends(get_current_user)):
    """
    Retorna o perfil do usuário logado.
    """
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "cpf": current_user.cpf,
        "company": current_user.company,
        "company_id": current_user.company_id,
        "role": current_user.role,
        "cnpj": current_user.cnpj,
        "phone": current_user.phone,
    }


@router.put("/profile")
def update_user_profile(
    user_update: UserUpdate,
    current_user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Atualiza os dados do perfil do usuário logado.
    """
    logger.info("=== UPDATE PROFILE REQUEST ===")
    logger.info(f"User: {current_user.username}")

    try:
        updated_user = AuthService.update_user(db, current_user, user_update)
        logger.info(f"Profile updated successfully: {updated_user.username}")

        return {
            "msg": "Perfil atualizado com sucesso",
            "user": {
                "id": updated_user.id,
                "username": updated_user.username,
                "email": updated_user.email,
                "full_name": updated_user.full_name,
                "cpf": AuthService.format_cpf(updated_user.cpf),
                "company": updated_user.company,
                "cnpj": AuthService.format_cnpj(updated_user.cnpj),
                "phone": AuthService.format_phone(updated_user.phone),
            },
        }

    except Exception as e:
        db.rollback()
        error_msg = f"Erro ao atualizar perfil: {str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)




@router.post("/forgot-password")
def forgot_password(request: ForgotPasswordRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Solicita o link de reset de senha.
    """
    AuthService.request_password_reset(db, request.email, background_tasks)
    return {"msg": "Se o e-mail existir, um link de reset foi enviado."}


@router.post("/reset-password")
def reset_password(request: ResetPasswordRequest, db: Session = Depends(get_db)):
    """
    Redefine a senha utilizando o token recebido por e-mail.
    """
    try:
        AuthService.reset_password_with_token(db, request)
        return {"msg": "Senha redefinida com sucesso."}
    except ValueError as e:
        raise DomainValidationError(detail=str(e))
