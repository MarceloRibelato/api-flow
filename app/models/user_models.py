from sqlalchemy import Column, Integer, String, ForeignKey, Boolean, DateTime
from sqlalchemy.orm import relationship

from app.database import Base


class UserDB(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    full_name = Column(String, nullable=True)
    cpf = Column(String, unique=True, index=True, nullable=True)
    
    # Legacy fields (optional to keep)
    company = Column(String, nullable=True) # Legacy field
    cnpj = Column(String, index=True, nullable=True)
    phone = Column(String, nullable=True)

    # New Multi-Tenant Fields
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)
    role = Column(String(50), default="viewer") # admin, editor, viewer
    token_version = Column(Integer, default=1, nullable=False) # Invalidate tokens on change
    status = Column(String(20), default="pending") # active, pending, blocked
    
    # Terms of Use
    accepted_terms = Column(Boolean, default=False)
    terms_accepted_at = Column(DateTime, nullable=True)

    # Relationship
    from app.models.company_models import CompanyDB # Late import might be needed or just string ref
    company_rel = relationship("CompanyDB", back_populates="users", foreign_keys=[company_id]) # Use primaryjoin if needed, but FK is better.
    
    # Let's verify if I can add ForeignKey safely. Yes, usually.
    # Updated definition below:

