import pytest
from tests.test_projects import get_auth_token_and_admin
from app.models.service_token_models import ServiceTokenDB
from app.models.product_models import ProductModel
from app.models.environment_model import Environment
from app.models.api_test_history_models import ApiExecutionHistory
from unittest.mock import patch

def test_token_management(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "token_user")
    headers = {"Authorization": f"Bearer {token}"}

    # Create Token
    response = client.post("/cicd/tokens", headers=headers, json={"name": "CI Token 1"})
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert data["name"] == "CI Token 1"
    token_id = data["id"]

    # List Tokens
    list_resp = client.get("/cicd/tokens", headers=headers)
    assert len(list_resp.json()) == 1

    # Delete Token
    client.delete(f"/cicd/tokens/{token_id}", headers=headers)
    assert len(client.get("/cicd/tokens", headers=headers).json()) == 0

def test_execute_cicd(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "cicd_exec_user")
    headers = {"Authorization": f"Bearer {token}"}
    
    # Setup Product and Environment
    p_resp = client.post("/products/", headers=headers, json={"name": "Prod1"})
    prod_id = p_resp.json()["id"]
    client.post("/environments/", headers=headers, json={"name": "Env1", "base_url": "http://test", "project_id": prod_id})
    
    payload = {
        "product_name": "Prod1",
        "environment_name": "Env1"
    }
    
    with patch("app.routes.cicd_routes.execute_job") as mock_exec:
        response = client.post("/cicd/execute", headers=headers, json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "triggered"
        mock_exec.assert_called_once()

def test_job_status(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "cicd_status_user")
    headers = {"Authorization": f"Bearer {token}"}
    
    # Setup Product and Environment
    p_resp = client.post("/products/", headers=headers, json={"name": "StatusProd"})
    prod_id = p_resp.json()["id"]
    client.post("/environments/", headers=headers, json={"name": "StatusEnv", "base_url": "http://test", "project_id": prod_id})
    
    # Trigger one
    with patch("app.routes.cicd_routes.execute_job"):
        exec_resp = client.post("/cicd/execute", headers=headers, json={
            "product_name": "StatusProd",
            "environment_name": "StatusEnv"
        })
    job_id = exec_resp.json()["job_id"]
    
    # Check Pending
    response = client.get(f"/cicd/status/{job_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "pending"

    # Mock Success History
    from app.models.user_models import UserDB
    user = db_session.query(UserDB).filter(UserDB.username == "cicd_status_user").first()
    
    db_session.add(ApiExecutionHistory(
        schedule_id=job_id, status_code=200, execution_id="c-1", 
        url="http://test", method="GET", user_id=user.id
    ))
    db_session.commit()
    
    response = client.get(f"/cicd/status/{job_id}", headers=headers)
    assert response.json()["status"] == "completed"
    assert response.json()["result"] == "success"
