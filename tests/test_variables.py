def get_headers(client, username="varuser"):
    client.post("/auth/create", json={
        "username": username, 
        "password": "Password123!",
        "accepted_terms": True
    })
    token = client.post(
        "/auth/login", json={"username": username, "password": "Password123!"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_create_and_update_variable(client):
    headers = get_headers(client, "upsertuser")
    proj = client.post("/products/", headers=headers, json={"name": "Var Proj"}).json()

    # Create
    resp = client.post(
        "/variables",
        headers=headers,
        json={"name": "TEST_VAR", "value": "123", "project_id": proj["id"]},
    )
    assert resp.status_code == 200

    # Update (Upsert logic in service)
    resp = client.post(
        "/variables",
        headers=headers,
        json={"name": "TEST_VAR", "value": "456", "project_id": proj["id"]},
    )
    assert resp.status_code == 200
    assert resp.json()["value"] == "456"


def test_delete_variable(client):
    headers = get_headers(client, "delvaruser")
    proj = client.post(
        "/products/", headers=headers, json={"name": "Del Var Proj"}
    ).json()

    var = client.post(
        "/variables", headers=headers, json={"name": "TO_DEL", "value": "1", "project_id": proj["id"]}
    ).json()

    # Delete by ID
    resp = client.delete(f"/variables/{var['id']}", headers=headers)
    assert resp.status_code == 200

    # Verify
    list_resp = client.get(f"/variables?project_id={proj['id']}", headers=headers)
    assert len(list_resp.json()) == 0


def test_delete_variable_by_name(client):
    headers = get_headers(client, "delnameuser")
    proj = client.post(
        "/products/", headers=headers, json={"name": "Del Name Proj"}
    ).json()

    client.post(
        "/variables", headers=headers, json={"name": "DEL_ME", "value": "1", "project_id": proj["id"]}
    )

    resp = client.delete(f"/variables/by-name/DEL_ME?project_id={proj['id']}", headers=headers)
    assert resp.status_code == 200

    list_resp = client.get(f"/variables?project_id={proj['id']}", headers=headers)
    assert len(list_resp.json()) == 0


def test_bulk_create_variables(client):
    headers = get_headers(client, "bulkuser")
    proj = client.post("/products/", headers=headers, json={"name": "Bulk Proj"}).json()

    vars_list = [
        {"name": "V1", "value": "1", "project_id": proj["id"]},
        {"name": "V2", "value": "2", "project_id": proj["id"]},
    ]

    resp = client.post("/variables/bulk", headers=headers, json=vars_list)
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    list_resp = client.get(f"/variables?project_id={proj['id']}", headers=headers)
    assert len(list_resp.json()) == 2
