import pytest
from app.services.pdf_service import PDFService
from app.models.api_test_history_models import ApiExecutionHistory
from app.models.schedule_models import ScheduleModel
from app.models.environment_model import Environment
from datetime import datetime, timezone

def test_generate_pdf_no_schedule(db_session):
    pdf_bytes = PDFService.generate_execution_report(db_session, 9999)
    assert pdf_bytes is None

def test_generate_pdf_success(db_session):
    # Setup
    env = Environment(name="Test Env", project_id=1)
    db_session.add(env)
    db_session.commit()
    
    schedule = ScheduleModel(name="Test Schedule", environment_id=env.id, status="active", type="suite", target_id=1, company_id=1)
    db_session.add(schedule)
    db_session.commit()
    
    # Add history
    db_session.add(ApiExecutionHistory(
        schedule_id=schedule.id,
        status_code=200,
        response_time=500,
        api_name="Test API 1",
        feature_name="Feature A",
        method="GET",
        url="http://test.com/1",
        request_body="{\"req\": 1}",
        response_body="{\"res\": 1}",
        created_at=datetime.now(timezone.utc),
        execution_id="exec-1"
    ))
    db_session.add(ApiExecutionHistory(
        schedule_id=schedule.id,
        status_code=500,
        response_time=1200,
        api_name="Test API 2",
        feature_name="Feature A",
        method="POST",
        url="http://test.com/2",
        request_body="A" * 2500, # Test truncation
        error_message="Internal Server Error",
        created_at=datetime.now(timezone.utc),
        execution_id="exec-2"
    ))
    db_session.commit()
    
    pdf_bytes = PDFService.generate_execution_report(db_session, schedule.id)
    assert pdf_bytes is not None
    assert isinstance(pdf_bytes, (bytes, bytearray))
    assert len(pdf_bytes) > 0
