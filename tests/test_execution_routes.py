import pytest
from unittest.mock import patch, MagicMock
from app.models.schedule_models import ScheduleModel

from tests.test_projects import get_auth_token_and_admin


def test_trigger_execution_suite(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "exec_user_1")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "product_id": 1,
        "feature_id": "all",
        "environment_id": 1,
        "name": "Suite Execution Test",
        "max_concurrency": 5
    }

    # Patch execute_job and the scheduler attribute of the scheduler_service instance
    with patch('app.services.scheduler_service.execute_job'), \
         patch('app.services.scheduler_service.scheduler_service.scheduler') as mock_sched:
        mock_sched.add_job = MagicMock()

        response = client.post("/execute/create", headers=headers, json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"
        assert "schedule_id" in data

        sched = db_session.query(ScheduleModel).filter(ScheduleModel.id == data["schedule_id"]).first()
        assert sched is not None
        assert sched.type == "suite"
        assert sched.target_id == 1
        assert sched.flow_type == "api"  # default


def test_trigger_execution_feature(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "exec_user_2")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "product_id": 1,
        "feature_id": 10,
        "environment_id": 1,
        "name": "Feature Execution Test"
    }

    with patch('app.services.scheduler_service.execute_job'):
        response = client.post("/execute/create", headers=headers, json=payload)
        assert response.status_code == 200
        data = response.json()

        sched = db_session.query(ScheduleModel).filter(ScheduleModel.id == data["schedule_id"]).first()
        assert sched.type == "feature"
        assert sched.target_id == 10


def test_trigger_execution_flow(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "exec_user_3")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "product_id": 1,
        "feature_id": 10,
        "environment_id": 1,
        "name": "Flow Execution Test",
        "flow_id": 55
    }

    with patch('app.services.scheduler_service.execute_job'):
        response = client.post("/execute/create", headers=headers, json=payload)
        assert response.status_code == 200
        data = response.json()

        sched = db_session.query(ScheduleModel).filter(ScheduleModel.id == data["schedule_id"]).first()
        assert sched.type == "flow"
        assert sched.target_id == 55


def test_trigger_execution_invalid_feature(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "exec_user_4")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "product_id": 1,
        "feature_id": "invalid_string",
        "environment_id": 1,
        "name": "Invalid Test"
    }

    response = client.post("/execute/create", headers=headers, json=payload)
    assert response.status_code == 400


@patch('app.routes.execution_routes.PDFService.generate_execution_report')
def test_get_execution_pdf(mock_pdf_service, client, db_session):
    token = get_auth_token_and_admin(client, db_session, "exec_user_5")
    headers = {"Authorization": f"Bearer {token}"}

    mock_pdf_service.return_value = b"%PDF-1.4 Mock PDF Content"

    response = client.get("/execute/1/pdf", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == b"%PDF-1.4 Mock PDF Content"
    mock_pdf_service.assert_called_once()


@patch('app.routes.execution_routes.PDFService.generate_execution_report')
def test_get_execution_pdf_not_found(mock_pdf_service, client, db_session):
    token = get_auth_token_and_admin(client, db_session, "exec_user_6")
    headers = {"Authorization": f"Bearer {token}"}

    mock_pdf_service.return_value = None

    response = client.get("/execute/99/pdf", headers=headers)
    assert response.status_code == 404


def test_trigger_execution_e2e(client, db_session):
    """Verifica que flow_type='e2e' é persistido corretamente no schedule."""
    token = get_auth_token_and_admin(client, db_session, "exec_user_e2e")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "product_id": 1,
        "feature_id": 10,
        "environment_id": 1,
        "name": "E2E Execution Test",
        "flow_type": "e2e"
    }

    with patch('app.services.scheduler_service.execute_job'):
        response = client.post("/execute/create", headers=headers, json=payload)
        assert response.status_code == 200
        data = response.json()

        sched = db_session.query(ScheduleModel).filter(ScheduleModel.id == data["schedule_id"]).first()
        assert sched.type == "feature"
        assert sched.flow_type == "e2e"


def test_trigger_execution_api_explicit(client, db_session):
    """Verifica que flow_type='api' explícito é salvo corretamente."""
    token = get_auth_token_and_admin(client, db_session, "exec_user_api_ex")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "product_id": 1,
        "feature_id": 20,
        "environment_id": 1,
        "name": "API Explicit Test",
        "flow_type": "api"
    }

    with patch('app.services.scheduler_service.execute_job'):
        response = client.post("/execute/create", headers=headers, json=payload)
        assert response.status_code == 200
        data = response.json()

        sched = db_session.query(ScheduleModel).filter(ScheduleModel.id == data["schedule_id"]).first()
        assert sched.flow_type == "api"
