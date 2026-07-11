import pytest
from tests.test_projects import get_auth_token_and_admin
from app.models.api_test_history_models import ApiExecutionHistory
from datetime import datetime, timedelta, timezone

def setup_history_data(db_session):
    # Success
    db_session.add(ApiExecutionHistory(
        url="https://api.test/ok", method="GET", response_time=100, 
        project_id=1, user_id=1, status_code=200, execution_id="ex-1",
        created_at=datetime.now(timezone.utc)
    ))
    # Failure (error message)
    db_session.add(ApiExecutionHistory(
        url="https://api.test/fail", method="POST", response_time=150, 
        project_id=1, user_id=1, status_code=500, execution_id="ex-2",
        error_message="Internal Server Error",
        api_name="Fail API",
        created_at=datetime.now(timezone.utc)
    ))
    # Slow
    db_session.add(ApiExecutionHistory(
        url="https://api.test/slow", method="GET", response_time=3000, 
        project_id=1, user_id=1, status_code=200, execution_id="ex-3",
        created_at=datetime.now(timezone.utc)
    ))
    db_session.commit()

def test_dashboard_metrics(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "dash_user")
    headers = {"Authorization": f"Bearer {token}"}
    setup_history_data(db_session)

    response = client.get("/dashboard/metrics", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total_executions"] == 3
    assert data["total_failures"] == 1
    assert data["success_rate"] == 66.67

def test_dashboard_failures(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "dash_user_fail")
    headers = {"Authorization": f"Bearer {token}"}
    setup_history_data(db_session)

    response = client.get("/dashboard/failures?limit=5", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["api_name"] == "Fail API"

def test_dashboard_slowest(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "dash_user_slow")
    headers = {"Authorization": f"Bearer {token}"}
    setup_history_data(db_session)

    response = client.get("/dashboard/slowest?limit=5", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[0]["response_time"] == 3000

def test_dashboard_daily(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "dash_user_daily")
    headers = {"Authorization": f"Bearer {token}"}
    setup_history_data(db_session)

    response = client.get("/dashboard/daily?days=7", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 7
    # Last item should be today
    assert data[-1]["total"] == 3

def test_dashboard_top_failures(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "dash_user_top")
    headers = {"Authorization": f"Bearer {token}"}
    setup_history_data(db_session)

    response = client.get("/dashboard/top-failures", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["api_name"] == "Fail API"
    assert data[0]["failure_count"] == 1
