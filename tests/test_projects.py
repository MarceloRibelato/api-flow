from sqlalchemy.orm import Session
from app.models.user_models import UserDB

def get_auth_token_and_admin(client, db_session: Session, username="projuser", company="Test Comp"):
    client.post("/auth/create", json={"username": username, "password": "Password123!", "accepted_terms": True, "company": company})
    
    # Make user admin to bypass 403
    user = db_session.query(UserDB).filter(UserDB.username == username).first()
    if user:
        user.role = "admin"
        user.status = "active"
        db_session.commit()
    
    response = client.post(
        "/auth/login", json={"username": username, "password": "Password123!"}
    )
    return response.json()["access_token"]


def test_create_feature(client, db_session: Session):
    token = get_auth_token_and_admin(client, db_session, "createfeatureuser")
    headers = {"Authorization": f"Bearer {token}"}

    # Must create a Product first
    p_resp = client.post("/products/", headers=headers, json={"name": "Test Product"})
    prod_id = p_resp.json()["id"]

    response = client.post(
        "/features/",
        headers=headers,
        json={"name": "Test Feature", "description": "A new Feature", "product_id": prod_id},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["name"] == "Test Feature"
    assert data["id"] is not None

def test_get_features(client, db_session: Session):
    token = get_auth_token_and_admin(client, db_session, "listfeatureuser")
    headers = {"Authorization": f"Bearer {token}"}

    p_resp = client.post("/products/", headers=headers, json={"name": "Test Product 2"})
    prod_id = p_resp.json()["id"]

    client.post("/features/", headers=headers, json={"name": "Feature 1", "product_id": prod_id})

    response = client.get("/features/", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) >= 1

def test_get_feature_by_id(client, db_session: Session):
    token = get_auth_token_and_admin(client, db_session, "getonefeatureuser")
    headers = {"Authorization": f"Bearer {token}"}

    p_resp = client.post("/products/", headers=headers, json={"name": "Test Product 3"})
    prod_id = p_resp.json()["id"]

    create_resp = client.post(
        "/features/", headers=headers, json={"name": "Specific Feature", "product_id": prod_id}
    )
    feat_id = create_resp.json()["id"]

    response = client.get(f"/features/{feat_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["name"] == "Specific Feature"

def test_update_feature(client, db_session: Session):
    token = get_auth_token_and_admin(client, db_session, "updatefeatureuser")
    headers = {"Authorization": f"Bearer {token}"}

    p_resp = client.post("/products/", headers=headers, json={"name": "Test Product 4"})
    prod_id = p_resp.json()["id"]

    create_resp = client.post("/features/", headers=headers, json={"name": "Old Name", "product_id": prod_id})
    feat_id = create_resp.json()["id"]

    response = client.put(
        f"/features/{feat_id}",
        headers=headers,
        json={"name": "New Name", "description": "Updated", "product_id": prod_id},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"

def test_delete_feature(client, db_session: Session):
    token = get_auth_token_and_admin(client, db_session, "deletefeatureuser")
    headers = {"Authorization": f"Bearer {token}"}

    p_resp = client.post("/products/", headers=headers, json={"name": "Test Product 5"})
    prod_id = p_resp.json()["id"]

    create_resp = client.post("/features/", headers=headers, json={"name": "To Delete", "product_id": prod_id})
    feat_id = create_resp.json()["id"]

    response = client.delete(f"/features/{feat_id}", headers=headers)
    assert response.status_code == 200

    # Verify gone
    get_resp = client.get(f"/features/{feat_id}", headers=headers)
    assert get_resp.status_code == 404

def test_reorder_features(client, db_session: Session):
    token = get_auth_token_and_admin(client, db_session, "reorderfeatureuser")
    headers = {"Authorization": f"Bearer {token}"}

    p_resp = client.post("/products/", headers=headers, json={"name": "Test Product 6"})
    prod_id = p_resp.json()["id"]

    p1 = client.post("/features/", headers=headers, json={"name": "F1", "product_id": prod_id}).json()
    p2 = client.post("/features/", headers=headers, json={"name": "F2", "product_id": prod_id}).json()

    reorder_data = {
        "new_order": [{"id": p1["id"], "position": 2}, {"id": p2["id"], "position": 1}]
    }

    response = client.put("/features/reorder", headers=headers, json=reorder_data)
    assert response.status_code == 200

    # Verify order
    list_resp = client.get("/features/", headers=headers)
    ordered = [f for f in list_resp.json() if f["product_id"] == prod_id]
    
    # Sort them securely as we get them
    ordered.sort(key=lambda x: x["position"])
    
    # P2 should assume position 1 (first), P1 position 2 (second)
    assert ordered[0]["name"] == "F2"
    assert ordered[1]["name"] == "F1"
