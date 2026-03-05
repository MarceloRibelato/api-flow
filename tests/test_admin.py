import pytest
from app.models.user_models import UserDB
from tests.test_projects import get_auth_token_and_admin

def test_list_users_as_admin(client, db_session):
    # Admin user
    admin_token = get_auth_token_and_admin(client, db_session, "admin_user", company="AdminCorp")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Create another user to list
    client.post("/auth/create", json={
        "username": "user_to_list",
        "password": "Password123!",
        "accepted_terms": True,
        "company": "AdminCorp"
    })

    response = client.get("/admin/users", headers=headers)
    assert response.status_code == 200
    users = response.json()
    assert len(users) >= 2
    assert any(u["username"] == "user_to_list" for u in users)

def test_list_users_as_non_admin(client, db_session):
    # Ensure there's an admin first so the next user isn't auto-admin
    get_auth_token_and_admin(client, db_session, "dummy_admin")

    # Standard user (viewer)
    client.post("/auth/create", json={
        "username": "std_user",
        "password": "Password123!",
        "accepted_terms": True,
        "company": "OtherCorp"
    })
    
    # Manually make active but keep viewer
    std_user = db_session.query(UserDB).filter(UserDB.username == "std_user").first()
    std_user.status = "active"
    db_session.commit()
    
    login_resp = client.post("/auth/login", json={"username": "std_user", "password": "Password123!"})
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/admin/users", headers=headers)
    assert response.status_code == 403
    assert "Apenas administradores" in response.json()["detail"]

def test_approve_user(client, db_session):
    # Admin
    admin_token = get_auth_token_and_admin(client, db_session, "admin_approver", company="Corp")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Create pending user
    client.post("/auth/create", json={
        "username": "pending_user",
        "password": "Password123!",
        "accepted_terms": True,
        "company": "Corp"
    })
    
    pending_user = db_session.query(UserDB).filter(UserDB.username == "pending_user").first()
    assert pending_user.status == "pending"
    user_id = pending_user.id

    # Approve
    payload = {"status": "active", "role": "admin"}
    response = client.put(f"/admin/users/{user_id}/approve", headers=headers, json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "active"
    assert response.json()["role"] == "admin"

    # Verify in DB
    db_session.refresh(pending_user)
    assert pending_user.status == "active"
    assert pending_user.role == "admin"

def test_delete_user(client, db_session):
    # Admin
    admin_token = get_auth_token_and_admin(client, db_session, "admin_deleter", company="Corp")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Create user to delete
    client.post("/auth/create", json={
        "username": "delete_me",
        "password": "Password123!",
        "accepted_terms": True,
        "company": "Corp"
    })
    target_user = db_session.query(UserDB).filter(UserDB.username == "delete_me").first()
    target_id = target_user.id

    # Delete
    response = client.delete(f"/admin/users/{target_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["msg"] == "Usuário excluído com sucesso"

    # Verify 404
    response = client.delete(f"/admin/users/{target_id}", headers=headers)
    assert response.status_code == 404

def test_delete_self_restricted(client, db_session):
    # Admin
    admin_token = get_auth_token_and_admin(client, db_session, "self_admin", company="Corp")
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    admin_user = db_session.query(UserDB).filter(UserDB.username == "self_admin").first()
    admin_id = admin_user.id

    # Try delete self
    response = client.delete(f"/admin/users/{admin_id}", headers=headers)
    assert response.status_code == 400
    assert "Você não pode excluir a si mesmo" in response.json()["detail"]
