import re

from sqlalchemy.orm import Session

from app.auth import create_access_token, get_password_hash, verify_password
from app.models.user_models import UserDB
from app.schemas.auth_schemas import UserCreate, UserUpdate


class AuthService:
    @staticmethod
    def get_user_by_username(db: Session, username: str):
        user = db.query(UserDB).filter(UserDB.username == username).first()
        if user:
            return user
        return db.query(UserDB).filter(UserDB.email == username).first()

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
        # Limpar máscaras dos campos
        cpf_clean = re.sub(r"[.-]", "", user.cpf) if user.cpf else None
        cnpj_clean = re.sub(r"[./-]", "", user.cnpj) if user.cnpj else None
        phone_clean = re.sub(r"[() -]", "", user.phone) if user.phone else None

        hashed_pwd = get_password_hash(user.password)

        db_user = UserDB(
            username=user.username,
            email=user.email,
            hashed_password=hashed_pwd,
            full_name=user.full_name,
            cpf=cpf_clean,
            company=user.company,
            cnpj=cnpj_clean,
            phone=phone_clean,
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
    def create_token_response(user: UserDB):
        access_token = create_access_token(data={"sub": user.username})
        return {
            "access_token": access_token,
            "token_type": "bearer",
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
