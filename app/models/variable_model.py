from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class Variable(Base):
    __tablename__ = "variables"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(255), nullable=False, index=True)
    value = Column(Text, nullable=True)
    type = Column(String(50), default="static")

    faker_type = Column(String(100), nullable=True)
    faker_options = Column(JSON, nullable=True)
    json_path = Column(String(255), nullable=True)
    api_id = Column(BigInteger, nullable=True)

    project_id = Column(BigInteger, nullable=False, index=True)
    flow_id = Column(BigInteger, nullable=True)
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=True, index=True)

    environment = relationship("Environment", back_populates="variables")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
