import pytest
from tests.test_projects import get_auth_token_and_admin
from app.models.flow_models import FlowDB, FlowNodeDB

def test_ingest_capture(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "capture_user")
    headers = {"Authorization": f"Bearer {token}"}

    # Setup a project first
    p_resp = client.post("/products/", headers=headers, json={"name": "Capture Prod"})
    prod_id = p_resp.json()["id"]
    f_resp = client.post("/features/", headers=headers, json={"name": "Capture Feat", "product_id": prod_id})
    feat_id = f_resp.json()["id"]

    capture_data = [
        {
            "method": "POST",
            "url": "https://api.myapp.com/v1/login",
            "pageUrl": "https://myapp.com/login",
            "pageTitle": "Login Page",
            "body": {"username": "user", "password": "pwd"}
        },
        {
            "method": "GET",
            "url": "https://api.myapp.com/v1/user/profile",
            "pageUrl": "https://myapp.com/dashboard",
            "pageTitle": "Dashboard",
            "headers": {"Authorization": "Bearer token123"}
        }
    ]

    response = client.post(f"/capture/ingest?project_id={feat_id}", headers=headers, json=capture_data)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["nodes_created"] == 2

    # Verify flow exists
    flow_id = data["flow_id"]
    flow = db_session.query(FlowDB).filter(FlowDB.id == flow_id).first()
    assert flow is not None
    assert "Captured Flow" in flow.name

def test_ingest_empty_capture(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "capture_user_empty")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post("/capture/ingest", headers=headers, json=[])
    assert response.status_code == 500
    assert "detail" in response.json()

def test_get_capture_structure(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "capture_user_struct")
    headers = {"Authorization": f"Bearer {token}"}

    # Setup Product and Feature
    client.post("/products/", headers=headers, json={"name": "Struct Prod"})
    
    response = client.get("/capture/structure", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert any(p["name"] == "Struct Prod" for p in data)
