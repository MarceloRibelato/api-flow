import pytest
from tests.test_projects import get_auth_token_and_admin
from app.models.front_recording_models import FrontRecordingDB

def test_save_front_recording(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "recording_user")
    headers = {"Authorization": f"Bearer {token}"}

    # Setup Feature
    p_resp = client.post("/products/", headers=headers, json={"name": "Recording Prod"})
    prod_id = p_resp.json()["id"]
    f_resp = client.post("/features/", headers=headers, json={"name": "Recording Feat", "product_id": prod_id})
    feat_id = f_resp.json()["id"]

    payload = {
        "requests": [
            {"method": "GET", "url": "https://api.test/data"}
        ],
        "interactions": [
            {"action": "click", "selector": "button.submit", "customNodeName": "Step 1"}
        ]
    }

    response = client.post(f"/front-recording/save?project_id={feat_id}&name=Test Recording", headers=headers, json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["name"] == "Test Recording"

    # Verify script generation
    recording_id = data["id"]
    script_resp = client.get(f"/front-recording/{recording_id}/script", headers=headers)
    assert script_resp.status_code == 200
    assert "page.click('button.submit')" in script_resp.json()["script"]

def test_list_front_recordings(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "recording_list_user")
    headers = {"Authorization": f"Bearer {token}"}

    # Setup Feature
    p_resp = client.post("/products/", headers=headers, json={"name": "List Prod"})
    prod_id = p_resp.json()["id"]
    f_resp = client.post("/features/", headers=headers, json={"name": "List Feat", "product_id": prod_id})
    feat_id = f_resp.json()["id"]

    # Save one
    client.post(f"/front-recording/save?project_id={feat_id}&name=Rec1", headers=headers, json={"requests":[], "interactions":[]})

    response = client.get(f"/front-recording/list/{feat_id}", headers=headers)
    print("DEBUG LIST RESP:", response.json())
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["name"] == "Rec1"
