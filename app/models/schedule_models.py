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
    
    status = Column(String(20), default="active")  # 'active', 'paused', 'completed'
    last_run = Column(DateTime, nullable=True)
    next_run = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    environment = relationship("Environment", back_populates="schedules") # Changed to match actual class name
