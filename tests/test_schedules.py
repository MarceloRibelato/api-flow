import pytest
from datetime import datetime, timedelta
from app.models.schedule_models import ScheduleModel
from tests.test_projects import get_auth_token_and_admin

def test_create_schedule_cron(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "sched_user_1")
    headers = {"Authorization": f"Bearer {token}"}

    # Create a product first for context
    prod_resp = client.post("/products/", headers=headers, json={"name": "Sched Product"})
    prod_id = prod_resp.json()["id"]

    # Create a feature
    feat_resp = client.post("/features/", headers=headers, json={"name": "Sched Feat", "product_id": prod_id})
    feat_id = feat_resp.json()["id"]

    payload = {
        "name": "Daily Health Check",
        "type": "feature",
        "target_id": feat_id,
        "cron_expression": "0 8 * * *",
        "notifications_enabled": True
    }

    response = client.post("/schedules/", headers=headers, json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Daily Health Check"
    assert data["cron_expression"] == "0 8 * * *"
    assert data["status"] == "active"

def test_create_schedule_run_at(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "sched_user_2")
    headers = {"Authorization": f"Bearer {token}"}

    run_at = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    
    payload = {
        "name": "One Time Run",
        "type": "feature",
        "target_id": 1,
        "run_at": run_at
    }

    response = client.post("/schedules/", headers=headers, json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "active"

def test_list_schedules(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "sched_user_3")
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/schedules/", headers=headers, json={
        "name": "List Test",
        "type": "feature",
        "target_id": 99,
        "cron_expression": "0 0 * * *"
    })

    response = client.get("/schedules/", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) >= 1

def test_pause_and_resume_schedule(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "sched_user_4")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create
    create_resp = client.post("/schedules/", headers=headers, json={
        "name": "Toggle Test",
        "type": "feature",
        "target_id": 100,
        "cron_expression": "0 0 * * *"
    })
    sched_id = create_resp.json()["id"]

    # 2. Pause
    pause_resp = client.post(f"/schedules/{sched_id}/pause", headers=headers)
    assert pause_resp.status_code == 200
    
    get_resp = client.get("/schedules/", headers=headers)
    sched = next(s for s in get_resp.json() if s["id"] == sched_id)
    assert sched["status"] == "paused"

    # 3. Resume
    resume_resp = client.post(f"/schedules/{sched_id}/resume", headers=headers)
    assert resume_resp.status_code == 200
    
    get_resp = client.get("/schedules/", headers=headers)
    sched = next(s for s in get_resp.json() if s["id"] == sched_id)
    assert sched["status"] == "active"

def test_delete_schedule(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "sched_user_5")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = client.post("/schedules/", headers=headers, json={
        "name": "Delete Test",
        "type": "feature",
        "target_id": 101,
        "cron_expression": "0 0 * * *"
    })
    sched_id = create_resp.json()["id"]

    del_resp = client.delete(f"/schedules/{sched_id}", headers=headers)
    assert del_resp.status_code == 200

    # Verify 404
    get_resp = client.get("/schedules/", headers=headers)
    assert not any(s["id"] == sched_id for s in get_resp.json())

def test_schedule_multi_tenant_isolation(client, db_session):
    # User A from Company A
    token_a = get_auth_token_and_admin(client, db_session, "user_a", company="Company A")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    
    # User B from Company B
    token_b = get_auth_token_and_admin(client, db_session, "user_b", company="Company B")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # User A creates a schedule
    client.post("/schedules/", headers=headers_a, json={
        "name": "Secret A",
        "type": "feature",
        "target_id": 1,
        "cron_expression": "0 0 * * *"
    })

    # User B should NOT see User A's schedule
    resp_b = client.get("/schedules/", headers=headers_b)
    # Filter to look for User A's name just in case there are other global/test data
    names_b = [s["name"] for s in resp_b.json()]
    assert "Secret A" not in names_b
