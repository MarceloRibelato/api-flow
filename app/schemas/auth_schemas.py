from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr


class UserBase(BaseModel):
    username: str
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    cpf: Optional[str] = None
    company: Optional[str] = None
    cnpj: Optional[str] = None
    phone: Optional[str] = None


class UserCreate(UserBase):
    password: str
    accepted_terms: bool = False


class LoginRequest(BaseModel):
    username: str
    password: str


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    company: Optional[str] = None
    cnpj: Optional[str] = None
    phone: Optional[str] = None
    new_password: Optional[str] = None


class UserResponse(UserBase):
    id: int
    company_id: Optional[int] = None
    role: Optional[str] = None
    status: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    access_token: str
    token_type: str
    role: Optional[str] = None

class UserApprove(BaseModel):
    role: str
    status: str = "active"

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str
