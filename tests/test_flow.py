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

    client.post("/flow/save", headers=headers, json=flow_data)

    response = client.get(f"/flow/load/{feature['id']}", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["nodes"]) == 1


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
        "nodes": [{"id": "1", "type": "x", "position": {"x": 0, "y": 0}, "data": {"name": "N1"}}],
        "edges": [],
        "cardData": {"1": {"name": "N1"}},
    }
    client.post("/flow/save", headers=headers, json=flow_data)
    
    response = client.get(f"/flow/stats/{feature['id']}", headers=headers)
    assert response.status_code == 200
    stats = response.json()
    assert stats["nodes"] == 1
    assert stats["cards"] == 1


def test_delete_flow(client):
    headers = get_headers(client, "delflowuser")
    prod = client.post(
        "/products/", headers=headers, json={"name": "Test Product Del"}
    ).json()
    
    feature = client.post(
        "/features/", headers=headers, json={"name": "Del Feature", "product_id": prod["id"]}
    ).json()

    flow_data = {"projectId": feature["id"], "nodes": [], "edges": []}
    client.post("/flow/save", headers=headers, json=flow_data)
    
    response = client.delete(f"/flow/{feature['id']}", headers=headers)
    assert response.status_code == 200

    # Check stats for deleted flow (should say exists=False)
    stats_resp = client.get(f"/flow/stats/{feature['id']}", headers=headers)
    assert stats_resp.json()["exists"] is False
