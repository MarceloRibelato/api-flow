from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import relationship

from app.database import Base
# from app.models.schedule_models import ScheduleModel # Avoid circular, use string 'ScheduleModel'


class CompanyDB(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    cnpj = Column(String(20), unique=True, index=True, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    users = relationship("UserDB", back_populates="company_rel")
    products = relationship("ProductModel", back_populates="company_rel")
    schedules = relationship("ScheduleModel", back_populates="company_rel")
