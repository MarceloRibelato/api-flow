from sqlalchemy import Column, Integer, String, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.user_models import UserDB

class AgentSettingsDB(Base):
    __tablename__ = "agent_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    
    ai_enabled = Column(Boolean, default=True)
    ai_provider = Column(String, default="openai")  # openai, anthropic, gemini
    ai_model = Column(String, default="gpt-4o")
    ai_api_key = Column(String, nullable=True)
    ai_base_url = Column(String, nullable=True)
    
    # Relationship with User
    user = relationship("UserDB", backref="agent_settings")
