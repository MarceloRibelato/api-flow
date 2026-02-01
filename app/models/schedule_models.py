from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, BigInteger
from sqlalchemy.orm import relationship
from app.database import Base

class ScheduleModel(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=True)  # Optional descriptive name
    type = Column(String(20), nullable=False)  # 'suite' or 'feature'
    target_id = Column(BigInteger, nullable=False)  # ID of the Product (Suite) or Feature, changed to BigInt
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=True)
    user_id = Column(Integer, nullable=True)  # Track who created the schedule for history filtering
    
    # Scheduling details
    cron_expression = Column(String(100), nullable=True)  # e.g. "0 9 * * *" for daily at 9am
    run_at = Column(DateTime, nullable=True)  # Single execution time
    notification_urls = Column(String(500), nullable=True) # Webhooks (comma separated or single)
    
    notifications_enabled = Column(Boolean, default=True)
    status = Column(String(20), default="active")  # 'active', 'paused', 'completed'
    last_run = Column(DateTime, nullable=True)
    last_run_status = Column(String(20), nullable=True)  # 'success', 'failure'
    next_run = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Multi-tenancy
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)

    # Relationship
    environment = relationship("Environment", back_populates="schedules")
    company_rel = relationship("CompanyDB", back_populates="schedules") # Need to add backref in CompanyDB or just define here

