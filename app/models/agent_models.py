from sqlalchemy import Column, Integer, String, ForeignKey, Boolean
from sqlalchemy.orm import relationship, backref
from app.database import Base
from app.models.user_models import UserDB

class AgentSettingsDB(Base):
    __tablename__ = "agent_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    
    ai_enabled = Column(Boolean, default=True)
    ai_provider = Column(String, default="flow_ia")  # flow_ia, ollama, openai, anthropic, gemini
    ai_model = Column(String, default="qwen2.5:14b-instruct-q4_K_M")
    ai_api_key = Column(String, nullable=True)
    ai_base_url = Column(String, nullable=True)
    
    # Relationship with User
    user = relationship("UserDB", backref=backref("agent_settings", cascade="all, delete-orphan", passive_deletes=True))

from datetime import datetime
from sqlalchemy import DateTime

class AgentMemoryDB(Base):
    __tablename__ = "agent_memory"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    flow_id = Column(Integer, ForeignKey("flow_data.id"), nullable=False)
    role = Column(String, nullable=False) # "user" or "assistant"
    content = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

    user = relationship("UserDB", backref=backref("agent_memories", cascade="all, delete-orphan", passive_deletes=True))
