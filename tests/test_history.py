def get_headers(client, username="histuser"):
    client.post("/auth/create", json={"username": username, "password": "password"})
    token = client.post(
        "/auth/login", data={"username": username, "password": "password"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_save_execution(client):
    headers = get_headers(client, "execuser")

    history_data = {
        "url": "http://api.test",
        "method": "GET",
        "status_code": 200,
        "status_text": "OK",
        "response_time": 150,
        "api_id": 1,
    }

    response = client.post("/history/", headers=headers, json=history_data)
    assert response.status_code == 201
    data = response.json()
    assert data["url"] == "http://api.test"
    assert "execution_id" in data


def test_get_history(client):
    headers = get_headers(client, "histviewuser")

    # Create valid history items
    client.post(
        "/history/",
        headers=headers,
        json={
            "url": "A",
            "method": "GET",
            "status_code": 200,
            "status_text": "OK",
            "response_time": 100,
        },
    )
    client.post(
        "/history/",
        headers=headers,
        json={
            "url": "B",
            "method": "POST",
            "status_code": 201,
            "status_text": "Created",
            "response_time": 200,
        },
    )

    # Get All
    resp = client.get("/history/", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2

    # Filter by Method
    resp_filter = client.get("/history/?method=POST", headers=headers)
    assert resp_filter.json()["total"] == 1
    assert resp_filter.json()["items"][0]["url"] == "B"


def test_delete_history(client):
    headers = get_headers(client, "delhistuser")

    create = client.post(
        "/history/",
        headers=headers,
        json={
            "url": "del",
            "method": "GET",
            "status_code": 200,
            "status_text": "OK",
            "response_time": 100,
        },
    )
    exec_id = create.json()["execution_id"]

    resp = client.delete(f"/history/{exec_id}", headers=headers)
    assert resp.status_code == 200

    # Verify
    resp_get = client.get(f"/history/{exec_id}", headers=headers)
    assert resp_get.status_code == 404


def test_clear_history(client):
    headers = get_headers(client, "clearhistuser")

    client.post(
        "/history/",
        headers=headers,
        json={
            "url": "1",
            "method": "GET",
            "status_code": 200,
            "status_text": "OK",
            "response_time": 100,
        },
    )
    client.post(
        "/history/",
        headers=headers,
        json={
            "url": "2",
            "method": "GET",
            "status_code": 200,
            "status_text": "OK",
            "response_time": 100,
        },
    )

    # Clear without confirm
    resp = client.delete("/history/", headers=headers)
    assert resp.status_code == 400

    # Clear with confirm
    resp = client.delete("/history/?confirm=true", headers=headers)
    assert resp.status_code == 200

    # List
    list_resp = client.get("/history/", headers=headers)
    assert list_resp.json()["total"] == 0


def test_get_unique_apis(client):
    headers = get_headers(client, "uniqueuser")

    # Create duplicate history items
    client.post(
        "/history/",
        headers=headers,
        json={
            "url": "http://api.test/1",
            "method": "GET",
            "status_code": 200,
            "status_text": "OK",
            "response_time": 100,
        },
    )
    client.post(
        "/history/",
        headers=headers,
        json={
            "url": "http://api.test/1",
            "method": "GET", # DUPLICATE
            "status_code": 200,
            "status_text": "OK",
            "response_time": 120,
        },
    )
    client.post(
        "/history/",
        headers=headers,
        json={
            "url": "http://api.test/2",
            "method": "POST",
            "status_code": 201,
            "status_text": "Created",
            "response_time": 200,
        },
    )

    # Get Unique APIs
    resp = client.get("/history/unique/apis", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2

    # Verify content
    methods_urls = set((item["method"], item["url"]) for item in data)
    assert ("GET", "http://api.test/1") in methods_urls
    assert ("POST", "http://api.test/2") in methods_urls

