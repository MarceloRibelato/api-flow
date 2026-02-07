from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models.user_models import UserDB

SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=10)
security = HTTPBearer()
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES


def verify_password(plain, hashed):
    return pwd_context.verify(plain, hashed)


def get_password_hash(password):
    return pwd_context.hash(password)


import uuid
from .models.auth_models import BlacklistedToken

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    # Generate unique JTI
    to_encode.update({"exp": expire, "jti": str(uuid.uuid4())})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)
):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        jti: str = payload.get("jti")
# ... (rest of function)

        if username is None:
            raise HTTPException(status_code=401, detail="Token inválido")
            
        # Check Blacklist
        if jti:
            blacklisted = db.query(BlacklistedToken).filter(BlacklistedToken.jti == jti).first()
            if blacklisted:
                raise HTTPException(status_code=401, detail="Sessão encerrada (Token invalidado)")

        user = db.query(UserDB).filter(UserDB.username == username).first()
        if user is None:
            raise HTTPException(status_code=401, detail="Usuário não encontrado")
             
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")
