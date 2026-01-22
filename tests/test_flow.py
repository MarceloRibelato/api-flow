def get_headers(client, username="flowuser"):
    client.post("/auth/create", json={"username": username, "password": "password"})
    token = client.post(
        "/auth/login", data={"username": username, "password": "password"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_save_load_flow(client):
    headers = get_headers(client, "saveloaduser")
    proj = client.post(
        "/projects/", headers=headers, json={"name": "Flow Project"}
    ).json()

    flow_data = {
        "projectId": proj["id"],
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

    response = client.get(f"/flow/load/{proj['id']}", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["nodes"]) == 1


def test_flow_stats(client):
    headers = get_headers(client, "statsuser")
    proj = client.post(
        "/projects/", headers=headers, json={"name": "Stats Project"}
    ).json()

    flow_data = {
        "projectId": proj["id"],
        "nodes": [{"id": "1", "type": "x", "position": {}, "data": {"name": "N1"}}],
        "edges": [],
        "cardData": {"1": {"name": "N1"}},
    }
    client.post("/flow/save", headers=headers, json=flow_data)

    response = client.get(f"/flow/stats/{proj['id']}", headers=headers)
    assert response.status_code == 200
    stats = response.json()
    assert stats["nodes"] == 1
    assert stats["cards"] == 1


def test_delete_flow(client):
    headers = get_headers(client, "delflowuser")
    proj = client.post(
        "/projects/", headers=headers, json={"name": "Del Flow Project"}
    ).json()

    flow_data = {"projectId": proj["id"], "nodes": [], "edges": []}
    client.post("/flow/save", headers=headers, json=flow_data)

    response = client.delete(f"/flow/{proj['id']}", headers=headers)
    assert response.status_code == 200

    # Check stats for deleted flow (should say exists=False)
    stats_resp = client.get(f"/flow/stats/{proj['id']}", headers=headers)
    assert stats_resp.json()["exists"] is False
