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


def test_secret_variable_create_and_update(client):
    headers = get_headers(client, "secretuser")
    proj = client.post("/products/", headers=headers, json={"name": "Secret Proj"}).json()

    # 1. Create a secret variable
    resp = client.post(
        "/variables",
        headers=headers,
        json={
            "name": "API_SECRET_KEY",
            "value": "super-confidential-token-999",
            "type": "secret",
            "project_id": proj["id"]
        },
    )
    assert resp.status_code == 200
    created = resp.json()
    assert created["name"] == "API_SECRET_KEY"
    assert created["type"] == "secret"
    assert created["value"] == "super-confidential-token-999"

    var_id = created["id"]

    # 2. Update with empty string (simulating UI edit without changing secret value)
    update_resp = client.put(
        f"/variables/{var_id}",
        headers=headers,
        json={
            "name": "API_SECRET_KEY",
            "value": "",
            "type": "secret",
            "project_id": proj["id"]
        }
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["value"] == "super-confidential-token-999"

    # 3. Update with masked bullet string
    update_masked_resp = client.put(
        f"/variables/{var_id}",
        headers=headers,
        json={
            "name": "API_SECRET_KEY",
            "value": "••••••••",
            "type": "secret",
            "project_id": proj["id"]
        }
    )
    assert update_masked_resp.status_code == 200
    assert update_masked_resp.json()["value"] == "super-confidential-token-999"

    # 4. Update with a brand new secret value
    update_new_resp = client.put(
        f"/variables/{var_id}",
        headers=headers,
        json={
            "name": "API_SECRET_KEY",
            "value": "brand-new-secret-token-777",
            "type": "secret",
            "project_id": proj["id"]
        }
    )
    assert update_new_resp.status_code == 200
    assert update_new_resp.json()["value"] == "brand-new-secret-token-777"

