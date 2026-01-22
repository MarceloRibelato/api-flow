import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.user_models import UserDB
from app.schemas.auth_schemas import Token, UserCreate, UserUpdate
from app.services.auth_service import AuthService

# Logger acquisition (inherited config)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)
):
    """
    Endpoint para autenticação. Recebe dados via Form (padrão OAuth2).
    Retorna o token JWT (Bearer) se as credenciais forem válidas.
    """
    logger.info("=== LOGIN REQUEST ===")
    logger.info(f"Username attempt: {form_data.username}")

    user = AuthService.authenticate_user(db, form_data.username, form_data.password)

    if not user:
        # logger.warning(f"Login failed for user: {form_data.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário ou senha incorretos",
            headers={"WWW-Authenticate": "Bearer"},
        )

    logger.info(f"Login successful for user: {form_data.username}")
    logger.info(f"Access token generated for: {user.username}")

    return AuthService.create_token_response(user)


@router.post("/create")
def register(user: UserCreate, db: Session = Depends(get_db)):
    """
    Registra um novo usuário no banco de dados.
    """
    logger.info("=== REGISTER REQUEST RECEIVED ===")

    # Verifica duplicidade
    existing_user = AuthService.get_user_by_username(db, user.username)
    if existing_user:
        raise HTTPException(status_code=400, detail="Username já existe")

    # Verifica email
    if user.email:
        existing_email = db.query(UserDB).filter(UserDB.email == user.email).first()
        if existing_email:
            raise HTTPException(status_code=400, detail="Email já existe")

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
            "cnpj": AuthService.format_cnpj(new_user.cnpj),
            "phone": AuthService.format_phone(new_user.phone),
        }

    except Exception as e:
        db.rollback()
        error_msg = f"Database error: {str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


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
