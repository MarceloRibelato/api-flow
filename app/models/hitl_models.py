from datetime import datetime
from sqlalchemy import Column, Integer, String, JSON, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base

class HitlSessionDB(Base):
    __tablename__ = "hitl_sessions"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    execution_id = Column(Integer, index=True) # corresponds to schedule_id
    project_id = Column(Integer, index=True)
    node_id = Column(String(255), index=True)
    step_id = Column(String(255), index=True)
    
    # Store the ambiguous selector
    original_selector = Column(String(1024))
    
    # JSON containing candidates: [{"index": 0, "html": "...", "text": "...", "screenshot_base64": "..."}]
    candidates = Column(JSON, default=list)
    
    # status: pending, resolved, timeout
    status = Column(String(50), default="pending", index=True)
    
    # Which candidate was chosen (index)
    selected_index = Column(Integer, nullable=True)
    
    # Optional custom selector provided by user
    custom_selector = Column(String(1024), nullable=True)
    
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
