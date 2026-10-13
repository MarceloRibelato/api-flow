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


def test_register_with_existing_company_cnpj(client):
    # Register first user creating the company
    resp1 = client.post(
        "/auth/create",
        json={
            "username": "cnpj_user1",
            "email": "cnpj_user1@test.com",
            "password": "Password123!",
            "company": "Empresa Alfa",
            "cnpj": "11.222.333/0001-44",
            "accepted_terms": True,
        },
    )
    assert resp1.status_code == 200
    comp_id = resp1.json()["company_id"]
    assert comp_id is not None

    # Register second user with same company CNPJ but slightly different typed company name
    resp2 = client.post(
        "/auth/create",
        json={
            "username": "cnpj_user2",
            "email": "cnpj_user2@test.com",
            "password": "Password123!",
            "company": "Alfa Dept",
            "cnpj": "11.222.333/0001-44",
            "accepted_terms": True,
        },
    )
    assert resp2.status_code == 200
    assert resp2.json()["company_id"] == comp_id


def test_register_duplicate_cpf(client):
    resp1 = client.post(
        "/auth/create",
        json={
            "username": "cpf_user1",
            "email": "cpf_user1@test.com",
            "password": "Password123!",
            "cpf": "123.456.789-00",
            "accepted_terms": True,
        },
    )
    assert resp1.status_code == 200

    resp2 = client.post(
        "/auth/create",
        json={
            "username": "cpf_user2",
            "email": "cpf_user2@test.com",
            "password": "Password123!",
            "cpf": "12345678900",
            "accepted_terms": True,
        },
    )
    assert resp2.status_code == 409
    assert "CPF já existe" in resp2.json()["detail"]


def test_logout_revokes_token(client):
    # 1. Create and login user
    client.post(
        "/auth/create",
        json={"username": "logoutuser", "password": "Password123!", "accepted_terms": True}
    )
    login_resp = client.post(
        "/auth/login",
        json={"username": "logoutuser", "password": "Password123!"}
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Access profile successfully
    prof_resp = client.get("/auth/profile", headers=headers)
    assert prof_resp.status_code == 200

    # 3. Logout
    logout_resp = client.post("/auth/logout", headers=headers)
    assert logout_resp.status_code == 200

    # 4. Access profile with old token should now fail (401)
    prof_after = client.get("/auth/profile", headers=headers)
    assert prof_after.status_code == 401
    detail = prof_after.json()["detail"].lower()
    assert "revogada" in detail or "invalidado" in detail


def test_inactive_and_pending_user_access(client, db_session):
    from app.models.user_models import UserDB
    # First user is admin (active)
    client.post("/auth/create", json={"username": "first_admin", "password": "Password123!", "accepted_terms": True})

    # Second user is created as pending
    client.post("/auth/create", json={"username": "pending_user", "password": "Password123!", "accepted_terms": True})

    # Pending user cannot login
    login_resp = client.post("/auth/login", json={"username": "pending_user", "password": "Password123!"})
    assert login_resp.status_code == 403

    # Now make user active to get token, then block them
    u = db_session.query(UserDB).filter(UserDB.username == "pending_user").first()
    u.status = "active"
    db_session.commit()

    login_active = client.post("/auth/login", json={"username": "pending_user", "password": "Password123!"})
    assert login_active.status_code == 200
    token = login_active.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Verify active user works
    assert client.get("/auth/profile", headers=headers).status_code == 200

    # Block user
    u.status = "blocked"
    db_session.commit()

    # Accessing profile with previous token should fail with 403
    blocked_resp = client.get("/auth/profile", headers=headers)
    assert blocked_resp.status_code == 403


def test_unregistered_flw_key_rejected(client):
    response = client.get("/auth/profile", headers={"X-API-Key": "flw_fake_unregistered_token_12345"})
    assert response.status_code == 401


def test_service_token_auth(client, db_session):
    # Admin user with company
    client.post("/auth/create", json={
        "username": "token_owner",
        "password": "Password123!",
        "accepted_terms": True,
        "company": "TokenCorp"
    })
    token = client.post("/auth/login", json={"username": "token_owner", "password": "Password123!"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Generate API key via CICD route
    gen_resp = client.post("/cicd/tokens", headers=headers, json={"name": "Deploy Key"})
    assert gen_resp.status_code == 200
    api_key = gen_resp.json()["token"]
    assert api_key.startswith("flw_")

    # Access protected route using X-API-Key
    api_headers = {"X-API-Key": api_key}
    prof_resp = client.get("/auth/profile", headers=api_headers)
    assert prof_resp.status_code == 200
    assert prof_resp.json()["username"] == "token_owner"


