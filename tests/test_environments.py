def get_headers(client, username="envuser"):
    client.post("/auth/create", json={"username": username, "password": "password"})
    token = client.post(
        "/auth/login", data={"username": username, "password": "password"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_create_environment(client):
    headers = get_headers(client, "createenvuser")
    proj = client.post(
        "/projects/", headers=headers, json={"name": "Env Project"}
    ).json()

    response = client.post(
        "/environments", json={"name": "Dev", "project_id": proj["id"]}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Dev"


def test_delete_environment(client):
    headers = get_headers(client, "delenvuser")
    proj = client.post(
        "/projects/", headers=headers, json={"name": "Del Env Project"}
    ).json()

    env = client.post(
        "/environments", json={"name": "To Delete", "project_id": proj["id"]}
    ).json()

    response = client.delete(f"/environments/{env['id']}")
    assert response.status_code == 200

    # Try creating var in deleted env (should fail or just not working logically, usually we check get)
    # Since we don't have get_by_id for env exposed in routes easily (only get_all), we check get_all list
    list_resp = client.get(f"/environments?project_id={proj['id']}")
    assert len(list_resp.json()) == 0


def test_environment_cloning(client):
    headers = get_headers(client, "cloneuser")
    proj = client.post(
        "/projects/", headers=headers, json={"name": "Clone Project"}
    ).json()

    # Create Source Env
    source_env = client.post(
        "/environments", json={"name": "Source", "project_id": proj["id"]}
    ).json()

    # Add variable to source
    client.post(
        "/variables",
        json={
            "name": "VAR1",
            "value": "val1",
            "project_id": proj["id"],
            "environment_id": source_env["id"],
        },
    )

    # Clone Env
    clone_resp = client.post(
        "/environments",
        json={
            "name": "Cloned",
            "project_id": proj["id"],
            "clone_from_id": source_env["id"],
        },
    )
    assert clone_resp.status_code == 200
    cloned_env = clone_resp.json()

    # Verify variables cloned
    vars_resp = client.get(
        f"/variables?project_id={proj['id']}&environment_id={cloned_env['id']}"
    )
    vars_list = vars_resp.json()
    assert len(vars_list) == 1
    assert vars_list[0]["name"] == "VAR1"
    assert vars_list[0]["value"] == "val1"
