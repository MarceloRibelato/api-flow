# app/models/history_models.py
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Index
)
from sqlalchemy.orm import deferred
from sqlalchemy.sql import func

from app.database import Base
from app.models.custom_types import GzippedText  # Import custom type


class ApiExecutionHistory(Base):
    __tablename__ = "api_test_execution_history"

    id = Column(Integer, primary_key=True, index=True)
    execution_id = Column(String, unique=True, index=True)
    batch_id = Column(String(100), nullable=True, index=True)  # Groups all API calls from a single run

    # IDs relacionados
    api_id = Column(String(100), nullable=True, index=True)
    api_name = Column(String(255), nullable=True)  # Added api_name
    project_id = Column(Integer, nullable=True, index=True)
    flow_id = Column(String(100), nullable=True, index=True)
    node_id = Column(String(100), nullable=True, index=True)  # Added stable node ID (e.g. '1-1')
    schedule_id = Column(Integer, nullable=True, index=True)  # Added schedule_id
    feature_name = Column(String(255), nullable=True) # Added feature_name
    node_name = Column(String(255), nullable=True)  # Added node_name for grouping
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # Adicionado
    environment_id = Column(Integer, nullable=True, index=True) # ID do ambiente usado
    environment_name = Column(String(100), nullable=True) # Nome do ambiente (snapshot)

    # Dados da requisição
    method = Column(String(10), index=True)
    url = Column(Text)
    request_headers = deferred(Column(JSON, nullable=True))
    request_body = deferred(Column(GzippedText, nullable=True))  # COMPRESSED
    request_params = Column(JSON, nullable=True)

    # Dados da resposta
    status_code = Column(Integer, index=True)
    status_text = Column(String(100))
    response_headers = deferred(Column(JSON, nullable=True))
    response_body = deferred(Column(GzippedText, nullable=True))  # COMPRESSED
    response_time = Column(Integer)  # ms
    error_message = Column(Text, nullable=True)

    # Metadados
    variables_used = deferred(Column(JSON, nullable=True))
    processed_url = Column(Text, nullable=True)
    assertions = Column(JSON, nullable=True)
    video_url = Column(String(500), nullable=True) # Added for E2E recordings
    healed_selector = Column(String(500), nullable=True) # Added for Drift Detection
    execution_type = Column(String(50), index=True, default="api")  # 'api' or 'web'
    trigger_origin = Column(String(50), default="manual", index=True) # 'manual', 'pipeline', 'schedule'


    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Composite Indexes for frequent filtering
    __table_args__ = (
        Index('idx_history_lookup', "user_id", "project_id", "created_at"),
        Index('idx_history_env_lookup', "user_id", "environment_id", "created_at"),
        Index('idx_history_schedule_time', "schedule_id", "created_at"),
        Index('idx_history_project_time', "project_id", "created_at"),
        Index('idx_history_batch_time', "batch_id", "created_at"),
    )

    __mapper_args__ = {
        'polymorphic_on': execution_type,
        'polymorphic_identity': 'base'
    }

    @property
    def success(self):
        # The scheduler service explicitly sets error_message if:
        # 1. Assertions failed
        # 2. No assertions and status_code >= 400
        # 3. Exception occurred
        # So providing error_message is the Source of Truth for failure.
        return not bool(self.error_message)


class ApiHistoryDetails(ApiExecutionHistory):
    __mapper_args__ = {
        'polymorphic_identity': 'api'
    }


class WebHistoryDetails(ApiExecutionHistory):
    __mapper_args__ = {
        'polymorphic_identity': 'web'
    }


class MobileHistoryDetails(ApiExecutionHistory):
    __mapper_args__ = {
        'polymorphic_identity': 'mobile'
    }


class ApiExecutionHistoryArchive(Base):
    __tablename__ = "api_test_execution_history_archive"

    id = Column(Integer, primary_key=True, index=True)
    execution_id = Column(String, unique=True, index=True)
    batch_id = Column(String(100), nullable=True, index=True)  # Groups all API calls from a single run

    # IDs relacionados
    api_id = Column(String(100), nullable=True, index=True)
    api_name = Column(String(255), nullable=True)
    project_id = Column(Integer, nullable=True, index=True)
    flow_id = Column(String(100), nullable=True, index=True)
    node_id = Column(String(100), nullable=True, index=True)
    schedule_id = Column(Integer, nullable=True, index=True)
    feature_name = Column(String(255), nullable=True)
    node_name = Column(String(255), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    environment_id = Column(Integer, nullable=True, index=True)
    environment_name = Column(String(100), nullable=True)

    # Dados da requisição
    method = Column(String(10), index=True)
    url = Column(Text)
    request_headers = deferred(Column(JSON, nullable=True))
    request_body = deferred(Column(GzippedText, nullable=True))
    request_params = Column(JSON, nullable=True)

    # Dados da resposta
    status_code = Column(Integer, index=True)
    status_text = Column(String(100))
    response_headers = deferred(Column(JSON, nullable=True))
    response_body = deferred(Column(GzippedText, nullable=True))
    response_time = Column(Integer)
    error_message = Column(Text, nullable=True)

    # Metadados
    variables_used = deferred(Column(JSON, nullable=True))
    processed_url = Column(Text, nullable=True)
    assertions = Column(JSON, nullable=True)
    video_url = Column(String(500), nullable=True) # Added for E2E recordings
    healed_selector = Column(String(500), nullable=True) # Added for Drift Detection
    execution_type = Column(String(50), index=True, default="api")  # 'api' or 'web'
    trigger_origin = Column(String(50), default="manual", index=True) # 'manual', 'pipeline', 'schedule'

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
