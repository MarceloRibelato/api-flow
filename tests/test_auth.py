def test_register_user(client):
    response = client.post(
        "/auth/create",
        json={
            "username": "testuser",
            "password": "Password123!",
            "email": "test@example.com",
            "accepted_terms": True
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "testuser"
    assert "id" in data


def test_login_user(client):
    # Create user first
    client.post(
        "/auth/create", json={
            "username": "loginuser", 
            "password": "Password123!",
            "accepted_terms": True
        }
    )

    # Try login
    response = client.post(
        "/auth/login", json={"username": "loginuser", "password": "Password123!"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_invalid_password(client):
    client.post(
        "/auth/create", json={
            "username": "wrongpass", 
            "password": "Password123!",
            "accepted_terms": True
        }
    )

    response = client.post(
        "/auth/login", json={"username": "wrongpass", "password": "WrongPassword123!"}
    )
    assert response.status_code == 401


def test_register_duplicate_username(client):
    user_data = {
        "username": "duplicate",
        "password": "Password123!",
        "email": "dup@example.com",
        "accepted_terms": True
    }
    client.post("/auth/create", json=user_data)

    response = client.post("/auth/create", json=user_data)
    assert response.status_code == 409
    assert "Username já existe" in response.json()["detail"]


def test_register_duplicate_email(client):
    client.post(
        "/auth/create",
        json={"username": "u1", "password": "Password123!", "email": "shared@example.com", "accepted_terms": True},
    )

    response = client.post(
        "/auth/create",
        json={"username": "u2", "password": "Password123!", "email": "shared@example.com", "accepted_terms": True},
    )
    assert response.status_code == 409
    assert "Email já existe" in response.json()["detail"]


def test_get_user_profile(client):
    # Register and Login
    client.post(
        "/auth/create",
        json={
            "username": "profileuser",
            "password": "Password123!",
            "full_name": "Profile User",
            "accepted_terms": True
        },
    )
    token = client.post(
        "/auth/login", json={"username": "profileuser", "password": "Password123!"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/auth/profile", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "profileuser"
    assert data["full_name"] == "Profile User"


def test_update_user_profile(client):
    # Register and Login
    client.post("/auth/create", json={"username": "updateuser", "password": "Password123!", "accepted_terms": True})
    token = client.post(
        "/auth/login", json={"username": "updateuser", "password": "Password123!"}
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
