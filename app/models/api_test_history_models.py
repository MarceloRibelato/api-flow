# app/models/api_test_history_models.py
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
from sqlalchemy.orm import deferred, declared_attr
from sqlalchemy.sql import func

from app.database import Base
from app.models.custom_types import GzippedText
from app.config import settings

is_postgres = "postgres" in settings.DATABASE_URL.lower()


class ExecutionHistoryBase:
    """Base mixin defining shared columns for partitioned execution history tables."""
    # Primary key must include partition column (created_at) in PostgreSQL partitioned tables
    id = Column(Integer, primary_key=True, autoincrement=True)

    execution_id = Column(String, index=True)
    batch_id = Column(String(100), nullable=True, index=True)

    # Related IDs
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

    # Request data
    method = Column(String(10), index=True)
    url = Column(Text)
    request_params = Column(JSON, nullable=True)

    # Response data
    status_code = Column(Integer, index=True)
    status_text = Column(String(100))
    response_time = Column(Integer)  # ms
    error_message = Column(Text, nullable=True)

    # Metadata
    processed_url = Column(Text, nullable=True)
    assertions = Column(JSON, nullable=True)
    video_url = Column(String(500), nullable=True)
    healed_selector = Column(String(500), nullable=True)
    trigger_origin = Column(String(50), default="manual", index=True)
    retry_count = Column(Integer, default=0, nullable=False)

    # Timestamps (created_at is part of composite primary key for partitioning in PostgreSQL)
    created_at = Column(DateTime(timezone=True), primary_key=is_postgres, server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    @declared_attr
    def request_headers(cls):
        return deferred(Column(JSON, nullable=True))

    @declared_attr
    def request_body(cls):
        return deferred(Column(GzippedText, nullable=True))

    @declared_attr
    def response_headers(cls):
        return deferred(Column(JSON, nullable=True))

    @declared_attr
    def response_body(cls):
        return deferred(Column(GzippedText, nullable=True))

    @declared_attr
    def variables_used(cls):
        return deferred(Column(JSON, nullable=True))

    @property
    def success(self):
        return not bool(self.error_message)


class ApiExecutionHistory(ExecutionHistoryBase, Base):
    __tablename__ = "api_execution_history"

    execution_type = Column(String(50), index=True, default="api")

    __table_args__ = (
        Index('idx_api_history_lookup', "user_id", "project_id", "created_at"),
        Index('idx_api_history_env_lookup', "user_id", "environment_id", "created_at"),
        Index('idx_api_history_schedule_time', "schedule_id", "created_at"),
        Index('idx_api_history_project_time', "project_id", "created_at"),
        Index('idx_api_history_batch_time', "batch_id", "created_at"),
        Index('idx_api_history_exec_id', "execution_id"),
        *([{"postgresql_partition_by": "RANGE (created_at)"}] if is_postgres else [])
    )


class WebExecutionHistory(ExecutionHistoryBase, Base):
    __tablename__ = "web_execution_history"

    execution_type = Column(String(50), index=True, default="web")

    __table_args__ = (
        Index('idx_web_history_lookup', "user_id", "project_id", "created_at"),
        Index('idx_web_history_env_lookup', "user_id", "environment_id", "created_at"),
        Index('idx_web_history_schedule_time', "schedule_id", "created_at"),
        Index('idx_web_history_project_time', "project_id", "created_at"),
        Index('idx_web_history_batch_time', "batch_id", "created_at"),
        Index('idx_web_history_exec_id', "execution_id"),
        *([{"postgresql_partition_by": "RANGE (created_at)"}] if is_postgres else [])
    )


class MobileExecutionHistory(ExecutionHistoryBase, Base):
    __tablename__ = "mobile_execution_history"

    execution_type = Column(String(50), index=True, default="mobile")

    __table_args__ = (
        Index('idx_mob_history_lookup', "user_id", "project_id", "created_at"),
        Index('idx_mob_history_env_lookup', "user_id", "environment_id", "created_at"),
        Index('idx_mob_history_schedule_time', "schedule_id", "created_at"),
        Index('idx_mob_history_project_time', "project_id", "created_at"),
        Index('idx_mob_history_batch_time', "batch_id", "created_at"),
        Index('idx_mob_history_exec_id', "execution_id"),
        *([{"postgresql_partition_by": "RANGE (created_at)"}] if is_postgres else [])
    )



# Compatibility aliases for polymorphic legacy imports
ApiHistoryDetails = ApiExecutionHistory
WebHistoryDetails = WebExecutionHistory
MobileHistoryDetails = MobileExecutionHistory
ApiExecutionHistoryArchive = ApiExecutionHistory
