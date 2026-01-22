def get_auth_token(client, username="projuser"):
    client.post("/auth/create", json={"username": username, "password": "password"})
    response = client.post(
        "/auth/login", data={"username": username, "password": "password"}
    )
    return response.json()["access_token"]


def test_create_project(client):
    token = get_auth_token(client, "createuser")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/projects/",
        headers=headers,
        json={"name": "Test Project", "description": "A new project"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Project"
    assert data["id"] is not None


def test_get_projects(client):
    token = get_auth_token(client, "listuser")
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/projects/", headers=headers, json={"name": "Project 1"})

    response = client.get("/projects/", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_get_project_by_id(client):
    token = get_auth_token(client, "getoneuser")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = client.post(
        "/projects/", headers=headers, json={"name": "Specific Project"}
    )
    proj_id = create_resp.json()["id"]

    response = client.get(f"/projects/{proj_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["name"] == "Specific Project"


def test_update_project(client):
    token = get_auth_token(client, "updateuser")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = client.post("/projects/", headers=headers, json={"name": "Old Name"})
    proj_id = create_resp.json()["id"]

    response = client.put(
        f"/projects/{proj_id}",
        headers=headers,
        json={"name": "New Name", "description": "Updated"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"


def test_delete_project(client):
    token = get_auth_token(client, "deleteuser")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = client.post("/projects/", headers=headers, json={"name": "To Delete"})
    proj_id = create_resp.json()["id"]

    response = client.delete(f"/projects/{proj_id}", headers=headers)
    assert response.status_code == 200

    # Verify gone
    get_resp = client.get(f"/projects/{proj_id}", headers=headers)
    assert get_resp.status_code == 404


def test_reorder_projects(client):
    token = get_auth_token(client, "reorderuser")
    headers = {"Authorization": f"Bearer {token}"}

    p1 = client.post("/projects/", headers=headers, json={"name": "P1"}).json()
    p2 = client.post("/projects/", headers=headers, json={"name": "P2"}).json()

    reorder_data = {
        "new_order": [{"id": p1["id"], "position": 2}, {"id": p2["id"], "position": 1}]
    }

    response = client.put("/projects/reorder", headers=headers, json=reorder_data)
    assert response.status_code == 200

    # Verify order
    list_resp = client.get("/projects/", headers=headers)
    ordered = list_resp.json()
    # P2 should assume position 1 (first), P1 position 2 (second)
    # The default get_all sorts by position ascending
    assert ordered[0]["name"] == "P2"
    assert ordered[1]["name"] == "P1"
