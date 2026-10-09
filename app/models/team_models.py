from datetime import datetime
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Text
from sqlalchemy.orm import relationship

from app.database import Base


class TeamDB(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    company = relationship("CompanyDB", foreign_keys=[company_id], lazy="select")
    mocks = relationship("ServiceMockDB", back_populates="team", cascade="all, delete-orphan")
