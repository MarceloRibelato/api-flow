from sqlalchemy import Column, DateTime, Integer, String, BigInteger
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class Environment(Base):
    __tablename__ = "environments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    project_id = Column(BigInteger, nullable=False, index=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    variables = relationship(
        "Variable", back_populates="environment", cascade="all, delete-orphan"
    )

    schedules = relationship(
        "ScheduleModel", back_populates="environment", cascade="all, delete-orphan"
    )
