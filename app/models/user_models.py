from sqlalchemy import Column, Integer, String

from app.database import Base


class UserDB(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    full_name = Column(String, nullable=True)
    cpf = Column(String, unique=True, index=True, nullable=True)
    company = Column(String, nullable=True)
    cnpj = Column(String, unique=True, index=True, nullable=True)
    phone = Column(String, nullable=True)
