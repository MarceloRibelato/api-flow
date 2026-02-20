from datetime import datetime
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.database import Base

class ServiceTokenDB(Base):
    __tablename__ = "service_tokens"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    token_hash = Column(String(255), unique=True, index=True, nullable=False)
    
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)

    # Relationships
    # Using string references to avoid circular imports if needed
    user = relationship("UserDB", foreign_keys=[user_id])
    company = relationship("CompanyDB", foreign_keys=[company_id])
