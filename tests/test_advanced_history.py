import pytest
import datetime
from app.models.api_test_history_models import ApiExecutionHistory

def get_headers(client, username="advhistuser"):
    client.post("/auth/create", json={"username": username, "password": "password"})
    token = client.post(
        "/auth/login", data={"username": username, "password": "password"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

def test_large_body_compression(client):
    headers = get_headers(client, "compuser")
    
    # Generate a large string (approx 100KB)
    large_body = "x" * 100000 
    
    history_data = {
        "url": "http://api.test/large",
        "method": "POST",
        "status_code": 200,
        "status_text": "OK",
        "response_time": 500,
        "request_body": large_body, # Should be compressed
        "response_body": large_body # Should be compressed
    }

    resp = client.post("/history/", headers=headers, json=history_data)
    assert resp.status_code == 201
    data = resp.json()
    exec_id = data["execution_id"]

    # Verify retrieval (transparent decompression)
    get_resp = client.get(f"/history/{exec_id}", headers=headers)
    assert get_resp.status_code == 200
    item = get_resp.json()
    
    # The API should return the decompressed string, matching original
    assert item["request_body"] == large_body
    assert item["response_body"] == large_body

def test_complex_filtering(client):
    headers = get_headers(client, "filteruser")

    # Setup Context
    proj = client.post("/projects/", headers=headers, json={"name": "Filter Project"}).json()
    env = client.post("/environments/", headers=headers, json={"name": "Prod", "project_id": proj["id"]}).json()

    # Create History Items with different timestamps and contexts
    # Item 1: Correct Project/Env, Yesterday
    client.post("/history/", headers=headers, json={
        "url": "http://api.test/1",
        "method": "GET",
        "status_code": 200, 
        "response_time": 100,
        "project_id": proj["id"],
        "environment_id": env["id"],
        "created_at": (datetime.datetime.now() - datetime.timedelta(days=1)).isoformat() # Note: API might overwrite created_at depending on implementation, usually it's auto-generated. 
        # If API doesn't accept created_at, this test relies on instant creation. 
        # Assuming we can't easily spoof created_at via API without backdoor. 
        # We will test filtering by Project + Environment at least.
    })

    # Item 2: Correct Project, Different Env
    client.post("/history/", headers=headers, json={
        "url": "http://api.test/2",
        "method": "GET",
        "status_code": 200, 
        "response_time": 100,
        "project_id": proj["id"],
        "environment_id": 99999
    })

    # Item 3: Different Project
    client.post("/history/", headers=headers, json={
        "url": "http://api.test/3",
        "method": "GET",
        "status_code": 200, 
        "response_time": 100,
        "project_id": 99999,
        "environment_id": env["id"]
    })

    # Test Filter: Project + Environment
    filter_url = f"/history/?project_id={proj['id']}&environment_id={env['id']}"
    resp = client.get(filter_url, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    
    # Should only find Item 1
    assert data["total"] == 1
    assert data["items"][0]["url"] == "http://api.test/1"

    # Test Filter: Project Only
    resp_proj = client.get(f"/history/?project_id={proj['id']}", headers=headers)
    assert resp_proj.json()["total"] == 2 # Item 1 and 2
