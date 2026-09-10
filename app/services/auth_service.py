import re

from sqlalchemy.orm import Session

from app.auth import (
    create_access_token, 
    get_password_hash, 
    verify_password, 
    create_password_reset_token, 
    verify_password_reset_token
)
from app.models.user_models import UserDB
from app.schemas.auth_schemas import UserCreate, UserUpdate, ResetPasswordRequest


class AuthService:
    @staticmethod
    def get_user_by_username(db: Session, username: str):
        username_clean = username.strip().lower()
        # Case-insensitive lookup for both fields
        user = db.query(UserDB).filter(UserDB.username.ilike(username_clean)).first()
        if user:
            return user
        return db.query(UserDB).filter(UserDB.email.ilike(username_clean)).first()

    @staticmethod
    def authenticate_user(db: Session, username: str, password: str):
        user = AuthService.get_user_by_username(db, username)
        if not user:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        return user

    @staticmethod
    def create_user(db: Session, user: UserCreate):
        # Validation Logic
        if len(user.password) < 8:
            raise ValueError("A senha deve ter pelo menos 8 caracteres.")
        if not any(char.isupper() for char in user.password):
            raise ValueError("A senha deve conter pelo menos uma letra maiúscula.")
        if not any(char.isdigit() for char in user.password):
            raise ValueError("A senha deve conter pelo menos um número.")
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", user.password):
            raise ValueError("A senha deve conter pelo menos um caractere especial.")

        # Limpar máscaras dos campos
        cpf_clean = re.sub(r"[.-]", "", user.cpf) if user.cpf else None
        cnpj_clean = re.sub(r"[./-]", "", user.cnpj) if user.cnpj else None
        phone_clean = re.sub(r"[() -]", "", user.phone) if user.phone else None

        hashed_pwd = get_password_hash(user.password)

        # Count existing users to determine if this is the first user (System Admin)
        user_count = db.query(UserDB).count()
        is_first_user = user_count == 0

        # Multi-Tenant Logic (Company)
        company_id = None
        
        # Default Role/Status
        if is_first_user:
            role = "admin"
            status = "active"
        else:
            role = "viewer"
            status = "pending"

        if user.company:
            from app.models.company_models import CompanyDB
            existing_company = db.query(CompanyDB).filter(CompanyDB.name == user.company).first()
            
            if existing_company:
                company_id = existing_company.id
            else:
                # Create new company
                new_company = CompanyDB(
                    name=user.company,
                    cnpj=cnpj_clean # Assign CNPJ to company
                )
                db.add(new_company)
                db.flush() # Get ID
                company_id = new_company.id
        
        # Enforce Terms Acceptance
        if not user.accepted_terms:
            raise ValueError("Você deve aceitar os Termos de Uso para se cadastrar.")
            
        from datetime import datetime, timezone
        terms_accepted_at = datetime.now(timezone.utc)

        db_user = UserDB(
            username=user.username.strip().lower(),
            email=user.email.strip().lower() if user.email else None,
            hashed_password=hashed_pwd,
            full_name=user.full_name,
            cpf=cpf_clean,
            company=user.company, # Legacy text field
            company_id=company_id,
            role=role,
            status=status,
            cnpj=cnpj_clean,
            phone=phone_clean,
            accepted_terms=user.accepted_terms,
            terms_accepted_at=terms_accepted_at
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return db_user

    @staticmethod
    def update_user(db: Session, current_user: UserDB, user_update: UserUpdate):
        if user_update.full_name is not None:
            current_user.full_name = user_update.full_name
        if user_update.email is not None:
            current_user.email = user_update.email
        if user_update.company is not None:
            current_user.company = user_update.company

        if user_update.cnpj is not None:
            cnpj_clean = (
                re.sub(r"[./-]", "", user_update.cnpj) if user_update.cnpj else None
            )
            current_user.cnpj = cnpj_clean

        if user_update.phone is not None:
            phone_clean = (
                re.sub(r"[() -]", "", user_update.phone) if user_update.phone else None
            )
            current_user.phone = phone_clean

        if user_update.new_password:
            current_user.hashed_password = get_password_hash(user_update.new_password)

        db.commit()
        db.refresh(current_user)
        return current_user

    @staticmethod
    def request_password_reset(db: Session, email: str, background_tasks):
        user = db.query(UserDB).filter(UserDB.email == email).first()
        if not user:
            # For security reasons, don't reveal if user exists
            return True
        
        token = create_password_reset_token(email)
        
        from app.config import settings
        reset_link = f"{settings.FRONTEND_BASE_URL}/reset-password?token={token}"
        
        # Log for development fallback
        print("\n" + "="*50)
        print(f"PASSWORD RESET REQUEST FOR: {email}")
        print(f"LINK: {reset_link}")
        print("="*50 + "\n")
        
        # Send Real Email in background
        from app.services.email_service import EmailService
        import asyncio
        background_tasks.add_task(EmailService.send_reset_password_email, email, reset_link)
        
        return True

    @staticmethod
    def reset_password_with_token(db: Session, reset_data: ResetPasswordRequest):
        email = verify_password_reset_token(reset_data.token)
        if not email:
            raise ValueError("Token inválido ou expirado.")
            
        user = db.query(UserDB).filter(UserDB.email == email).first()
        if not user:
            raise ValueError("Usuário não encontrado.")
            
        user.hashed_password = get_password_hash(reset_data.new_password)
        db.commit()
        return True

    @staticmethod
    def create_token_response(user: UserDB):
        access_token = create_access_token(data={"sub": user.username, "version": user.token_version})
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "role": user.role
        }

    # Format helpers moved to static methods or utility, but kept here for simplicity if needed by response
    @staticmethod
    def format_cpf(cpf: str) -> str:
        if not cpf or len(cpf) != 11:
            return cpf
        return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"

    @staticmethod
    def format_cnpj(cnpj: str) -> str:
        if not cnpj or len(cnpj) != 14:
            return cnpj
        return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"

    @staticmethod
    def format_phone(phone: str) -> str:
        if not phone:
            return phone
        phone_clean = re.sub(r"\D", "", phone)
        if len(phone_clean) == 11:
            return f"({phone_clean[:2]}) {phone_clean[2:7]}-{phone_clean[7:]}"
        elif len(phone_clean) == 10:
            return f"({phone_clean[:2]}) {phone_clean[2:6]}-{phone_clean[6:]}"
        else:
            return phone
