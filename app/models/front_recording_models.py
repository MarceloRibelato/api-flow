from datetime import datetime
from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.database import Base

class FrontRecordingDB(Base):
    __tablename__ = "front_recordings"

    id = Column(Integer, primary_key=True, index=True)
    feature_id = Column(Integer, ForeignKey("features.id"), index=True)
    name = Column(String(255), nullable=False)
    
    # Store captured data as JSON
    requests = Column(JSON, default=list)
    interactions = Column(JSON, default=list)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    feature = relationship("FeatureModel")
