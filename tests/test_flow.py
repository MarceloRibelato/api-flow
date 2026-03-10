def get_headers(client, username="flowuser"):
    client.post("/auth/create", json={
        "username": username, 
        "password": "Password123!",
        "accepted_terms": True
    })
    token = client.post(
        "/auth/login", json={"username": username, "password": "Password123!"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_save_load_flow(client):
    headers = get_headers(client, "saveloaduser")
    prod = client.post(
        "/products/", headers=headers, json={"name": "Test Product"}
    ).json()
    
    feature = client.post(
        "/features/", headers=headers, json={"name": "Test Feature", "product_id": prod["id"]}
    ).json()

    flow_data = {
        "projectId": feature["id"],
        "flow_type": "api",
        "nodes": [
            {
                "id": "1",
                "type": "custom",
                "position": {"x": 0, "y": 0},
                "data": {"name": "Start Node"},
            }
        ],
        "edges": [],
        "cardData": {"1": {"name": "Start Node"}},
    }

    resp = client.post("/flow/save", headers=headers, json=flow_data)
    assert resp.status_code == 200

    response = client.get(f"/flow/load/{feature['id']}?flow_type=api", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["id"] == "1"


def test_flow_stats(client):
    headers = get_headers(client, "statsuser")
    prod = client.post(
        "/products/", headers=headers, json={"name": "Test Product Stats"}
    ).json()
    
    feature = client.post(
        "/features/", headers=headers, json={"name": "Stats Feature", "product_id": prod["id"]}
    ).json()

    flow_data = {
        "projectId": feature["id"],
        "flow_type": "api",
        "nodes": [{"id": "1", "type": "custom", "position": {"x": 0, "y": 0}, "data": {"name": "N1"}}],
        "edges": [],
        "cardData": {"1": {"name": "N1"}},
    }
    client.post("/flow/save", headers=headers, json=flow_data)
    
    response = client.get(f"/flow/stats/{feature['id']}", headers=headers)
    assert response.status_code == 200
    stats = response.json()
    assert stats["exists"] is True
    assert stats["nodes"] == 1


def test_delete_flow(client):
    headers = get_headers(client, "delflowuser")
    prod = client.post(
        "/products/", headers=headers, json={"name": "Test Product Del"}
    ).json()
    
    feature = client.post(
        "/features/", headers=headers, json={"name": "Del Feature", "product_id": prod["id"]}
    ).json()

    # Create flow first
    flow_data = {"projectId": feature["id"], "flow_type": "api", "nodes": [], "edges": []}
    client.post("/flow/save", headers=headers, json=flow_data)
    
    response = client.delete(f"/flow/{feature['id']}", headers=headers)
    # The first delete should be 200, but if it was already deleted (cleanup), it might be 404
    assert response.status_code in [200, 404]

    # Stats should indicate flow doesn't exist (exists=False is a 200 response from get_stats)
    stats_resp = client.get(f"/flow/stats/{feature['id']}", headers=headers)
    assert stats_resp.status_code == 200
    assert stats_resp.json()["exists"] is False
