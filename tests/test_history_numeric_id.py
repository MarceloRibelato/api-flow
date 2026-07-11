
def test_get_execution_detail_by_numeric_id(client):
    headers = None
    # Assuming helper to get headers exists or we need to login
    client.post("/auth/create", json={
        "username": "numiduser",
        "password": "Password123!",
        "accepted_terms": True
    })
    login_resp = client.post("/auth/login", json={"username": "numiduser", "password": "Password123!"})
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create a history item
    history_data = {
        "url": "http://api.test/numeric",
        "method": "GET",
        "status_code": 200,
        "status_text": "OK",
        "response_time": 123,
    }
    create_resp = client.post("/history/", headers=headers, json=history_data)
    assert create_resp.status_code == 201
    
    # Get the numeric ID from the newly created item
    # Note: The create response schema returns 'id' as well
    created_id = create_resp.json().get("id")
    assert created_id is not None
    assert isinstance(created_id, int)

    # Fetch by numeric ID
    get_resp = client.get(f"/history/{created_id}", headers=headers)
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["id"] == created_id
    assert data["url"] == "http://api.test/numeric"

    # Fetch by string execution_id (should still work)
    execution_id = create_resp.json().get("execution_id")
    get_resp_uuid = client.get(f"/history/{execution_id}", headers=headers)
    assert get_resp_uuid.status_code == 200
    assert get_resp_uuid.json()["id"] == created_id
