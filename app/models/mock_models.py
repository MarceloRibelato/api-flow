from datetime import datetime
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, JSON, Boolean, Text
from sqlalchemy.orm import relationship

from app.database import Base


class ServiceMockDB(Base):
    __tablename__ = "service_mocks"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, index=True, nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    target_url = Column(String(500), nullable=True)
    enable_proxy = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    rules = relationship("MockRuleDB", back_populates="mock", cascade="all, delete-orphan", order_by="MockRuleDB.priority.desc()")
    logs = relationship("MockLogDB", back_populates="mock", cascade="all, delete-orphan", order_by="MockLogDB.executed_at.desc()")


class MockRuleDB(Base):
    __tablename__ = "mock_rules"

    id = Column(Integer, primary_key=True, index=True)
    mock_id = Column(Integer, ForeignKey("service_mocks.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    method = Column(String(10), default="GET", nullable=False)  # GET, POST, PUT, DELETE, PATCH, ANY
    path_pattern = Column(String(255), nullable=False, default="/")
    priority = Column(Integer, default=1, nullable=False)

    # Matching Criteria
    match_query_params = Column(JSON, nullable=True)  # e.g. {"status": "active"}
    match_headers = Column(JSON, nullable=True)       # e.g. {"Content-Type": "application/json"}
    match_body_pattern = Column(Text, nullable=True)  # Regex or JSON string

    # Response Config
    response_status = Column(Integer, default=200, nullable=False)
    response_headers = Column(JSON, nullable=True)
    response_body = Column(Text, nullable=True)
    delay_ms = Column(Integer, default=0, nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    mock = relationship("ServiceMockDB", back_populates="rules")


class MockLogDB(Base):
    __tablename__ = "mock_logs"

    id = Column(Integer, primary_key=True, index=True)
    mock_id = Column(Integer, ForeignKey("service_mocks.id", ondelete="CASCADE"), nullable=False, index=True)
    rule_id = Column(Integer, ForeignKey("mock_rules.id", ondelete="SET NULL"), nullable=True)
    client_ip = Column(String(45), nullable=True)
    method = Column(String(10), nullable=False)
    path = Column(String(255), nullable=False)
    request_headers = Column(JSON, nullable=True)
    request_body = Column(Text, nullable=True)
    response_status = Column(Integer, nullable=False)
    response_body = Column(Text, nullable=True)
    is_proxied = Column(Boolean, default=False, nullable=False)
    executed_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    mock = relationship("ServiceMockDB", back_populates="logs")
