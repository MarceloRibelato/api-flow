import pytest
from unittest.mock import patch
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

    with patch('app.routes.execution_routes.execute_job') as mock_exec:
        response = client.post("/execute/create", headers=headers, json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"
        assert "schedule_id" in data
        mock_exec.assert_called_once()
        
        # Verify db entry
        sched = db_session.query(ScheduleModel).filter(ScheduleModel.id == data["schedule_id"]).first()
        assert sched is not None
        assert sched.type == "suite"
        assert sched.target_id == 1

def test_trigger_execution_feature(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "exec_user_2")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "product_id": 1,
        "feature_id": 10,
        "environment_id": 1,
        "name": "Feature Execution Test"
    }

    with patch('app.routes.execution_routes.execute_job'):
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

    with patch('app.routes.execution_routes.execute_job'):
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
