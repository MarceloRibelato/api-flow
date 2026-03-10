import pytest
from unittest.mock import patch, MagicMock
from app.services.scheduler_service import execute_job, SchedulerService, purge_history_job
from app.models.schedule_models import ScheduleModel
from app.models.product_models import ProductModel
from app.models.feature_models import FeatureModel
from app.models.flow_models import FlowDB
from datetime import datetime, timezone

@pytest.fixture(autouse=True)
def cleanup_scheduler():
    """Ensure scheduler is clean before and after each test."""
    from app.services.scheduler_service import scheduler
    if scheduler.running:
        scheduler.shutdown(wait=False)
    # Clear all jobs
    for job in scheduler.get_jobs():
        job.remove()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)
    for job in scheduler.get_jobs():
        job.remove()

def test_scheduler_add_remove_sync_jobs(db_session):
    service = SchedulerService()
    
    # Setup test schedules
    s1 = ScheduleModel(name="Active Cron", type="feature", target_id=1, status="active", cron_expression="0 0 * * *")
    db_session.add(s1)
    db_session.flush()
    
    s2 = ScheduleModel(name="One Time", type="feature", target_id=1, status="active", run_at=datetime.now(timezone.utc))
    db_session.add(s2)
    db_session.flush()
    
    # Test sync_jobs (calls add_job internally)
    service.sync_jobs(db_session)
    
    assert str(s1.id) in [j.id for j in service.scheduler.get_jobs()]
    
    # Test remove_job
    service.remove_job(s1.id)
    assert str(s1.id) not in [j.id for j in service.scheduler.get_jobs()]

def test_scheduler_start():
    service = SchedulerService()
    service.start()
    assert service.scheduler.running is True
    assert service.scheduler.get_job("maintenance_purge_history") is not None

@patch("app.services.scheduler_service.SessionLocal")
@patch("app.services.flow_executor_service.FlowExecutorService.execute_feature_group")
def test_execute_job_feature(mock_execute_feature, mock_session_local, db_session):
    fake_db = MagicMock(wraps=db_session)
    fake_db.close = MagicMock()
    mock_session_local.return_value = fake_db
    mock_execute_feature.return_value = (5, 0) # 5 success, 0 fails
    
    schedule = ScheduleModel(name="Exec Feat", type="feature", target_id=1, environment_id=1, status="active")
    db_session.add(schedule)
    db_session.commit()
    
    execute_job(schedule.id)
    
    # Refresh schedule
    db_session.refresh(schedule)
    assert schedule.last_run_status == "success"
    # Verify flow_type was passed correctly
    call_kwargs = mock_execute_feature.call_args
    assert call_kwargs.kwargs.get('flow_type', 'api') == 'api'
    mock_execute_feature.assert_called_once()


@patch("app.services.scheduler_service.SessionLocal")
@patch("app.services.flow_executor_service.FlowExecutorService.execute_flow_by_id")
def test_execute_job_flow(mock_execute_flow, mock_session_local, db_session):
    fake_db = MagicMock(wraps=db_session)
    fake_db.close = MagicMock()
    mock_session_local.return_value = fake_db
    mock_execute_flow.return_value = (2, 1) # failures present
    
    schedule = ScheduleModel(name="Exec Flow", type="flow", target_id=1, environment_id=1, status="active")
    db_session.add(schedule)
    db_session.commit()
    
    execute_job(schedule.id)
    
    db_session.refresh(schedule)
    assert schedule.last_run_status == "failure"
    mock_execute_flow.assert_called_once()


@patch("app.services.scheduler_service.SessionLocal")
@patch("app.services.flow_executor_service.FlowExecutorService.execute_feature_group")
def test_execute_job_feature_e2e(mock_execute_feature, mock_session_local, db_session):
    """Verifica que flow_type='e2e' é passado corretamente ao executor."""
    fake_db = MagicMock(wraps=db_session)
    fake_db.close = MagicMock()
    mock_session_local.return_value = fake_db
    mock_execute_feature.return_value = (3, 0)

    schedule = ScheduleModel(name="E2E Feat", type="feature", target_id=1, environment_id=1, status="active", flow_type="e2e")
    db_session.add(schedule)
    db_session.commit()

    execute_job(schedule.id)

    db_session.refresh(schedule)
    assert schedule.last_run_status == "success"
    call_kwargs = mock_execute_feature.call_args
    assert call_kwargs.kwargs.get('flow_type', 'api') == 'e2e'

@patch("app.services.scheduler_service.SessionLocal")
@patch("app.services.flow_executor_service.FlowExecutorService.execute_suite")
@patch("requests.Session")
@patch("requests.post")
def test_execute_job_suite_and_webhook(mock_post, mock_requests_session, mock_execute_suite, mock_session_local, db_session):
    fake_db = MagicMock(wraps=db_session)
    fake_db.close = MagicMock()
    mock_session_local.return_value = fake_db
    mock_execute_suite.return_value = (10, 0)
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    # Test Webhook Fallback and Slack Format logic
    product = ProductModel(name="Prod WH", notification_urls="https://hooks.slack.com/test")
    db_session.add(product)
    db_session.flush()

    schedule = ScheduleModel(name="Exec Suite", type="suite", target_id=product.id, environment_id=1, status="active")
    db_session.add(schedule)
    db_session.commit()
    
    execute_job(schedule.id)
    
    db_session.refresh(schedule)
    assert schedule.last_run_status == "success"
    mock_execute_suite.assert_called_once()
    mock_post.assert_called()


@patch("app.services.history_service.HistoryService")
@patch("app.services.scheduler_service.SessionLocal")
def test_purge_history_job(mock_session_local, mock_history_service, db_session):
    mock_session_local.return_value = db_session
    mock_history_service.archive_old_records.return_value = 100
    mock_history_service.purge_archived_records.return_value = 50
    
    purge_history_job()
    
    mock_history_service.archive_old_records.assert_called_once()
    mock_history_service.purge_archived_records.assert_called_once()

