def test_register_user(client):
    response = client.post(
        "/auth/create",
        json={
            "username": "testuser",
            "password": "testpassword",
            "email": "test@example.com",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "testuser"
    assert "id" in data


def test_login_user(client):
    # Create user first
    client.post(
        "/auth/create", json={"username": "loginuser", "password": "loginpassword"}
    )

    # Try login
    response = client.post(
        "/auth/login", data={"username": "loginuser", "password": "loginpassword"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_invalid_password(client):
    client.post(
        "/auth/create", json={"username": "wrongpass", "password": "correctpassword"}
    )

    response = client.post(
        "/auth/login", data={"username": "wrongpass", "password": "wrongpassword"}
    )
    assert response.status_code == 401


def test_register_duplicate_username(client):
    user_data = {
        "username": "duplicate",
        "password": "password",
        "email": "dup@example.com",
    }
    client.post("/auth/create", json=user_data)

    response = client.post("/auth/create", json=user_data)
    assert response.status_code == 400
    assert "Username já existe" in response.json()["detail"]


def test_register_duplicate_email(client):
    client.post(
        "/auth/create",
        json={"username": "u1", "password": "p1", "email": "shared@example.com"},
    )

    response = client.post(
        "/auth/create",
        json={"username": "u2", "password": "p2", "email": "shared@example.com"},
    )
    assert response.status_code == 400
    assert "Email já existe" in response.json()["detail"]


def test_get_user_profile(client):
    # Register and Login
    client.post(
        "/auth/create",
        json={
            "username": "profileuser",
            "password": "password",
            "full_name": "Profile User",
        },
    )
    token = client.post(
        "/auth/login", data={"username": "profileuser", "password": "password"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/auth/profile", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "profileuser"
    assert data["full_name"] == "Profile User"


def test_update_user_profile(client):
    # Register and Login
    client.post("/auth/create", json={"username": "updateuser", "password": "password"})
    token = client.post(
        "/auth/login", data={"username": "updateuser", "password": "password"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Update
    response = client.put(
        "/auth/profile",
        headers=headers,
        json={"full_name": "Updated Name", "company": "New Company"},
    )
    assert response.status_code == 200

    # Verify
    response = client.get("/auth/profile", headers=headers)
    assert response.json()["full_name"] == "Updated Name"
    assert response.json()["company"] == "New Company"
