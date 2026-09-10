import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch
from app.config import settings
settings.DATABASE_URL = "sqlite:///:memory:"

# 1. Setup test engine and SessionLocal EARLY
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# 2. Patch app.database module BEFORE importing app
import app.database as db_mod
db_mod.engine = engine
db_mod.SessionLocal = TestingSessionLocal

# 3. Now import app and other elements
from sqlalchemy import event
from app.database import Base, get_db
from app.main import app

@event.listens_for(Base.metadata, "before_create")
def fix_composite_pk_sqlite(target, connection, **kw):
    if connection.dialect.name == "sqlite":
        for table in target.tables.values():
            if len(table.primary_key.columns) > 1:
                for col in table.columns:
                    if col.autoincrement is True:
                        col.autoincrement = "auto"


@pytest.fixture(scope="function")
def db_session():
    """Create a new database session with a rollback at the end of the test."""
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db_session):
    """Create a test client that uses the override database session."""

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    
    # Mock scheduler and celery to avoid external connection issues during tests
    import httpx
    with patch("app.services.scheduler_service.scheduler_service.start"), \
         patch("app.services.scheduler_service.scheduler_service.sync_jobs"), \
         patch("app.tasks.execution_tasks.celery_execute_job.delay"):
        with TestClient(app) as test_client:
            # Ensure http_client is and remains initialized
            if not hasattr(app.state, "http_client") or app.state.http_client is None:
                app.state.http_client = httpx.AsyncClient()
            yield test_client

            
    app.dependency_overrides.clear()
