import pytest
from app.models.agent_models import AgentSettingsDB
from tests.test_projects import get_auth_token_and_admin

def test_get_settings_creates_default(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "agent_user")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/agent/settings/", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["ai_enabled"] is True
    assert data["ai_provider"] == "openai"
    assert data["ai_api_key"] is None

def test_update_settings(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "agent_user_update")
    headers = {"Authorization": f"Bearer {token}"}

    update_payload = {
        "ai_enabled": False,
        "ai_provider": "gemini",
        "ai_model": "gemini-pro",
        "ai_api_key": "test-key-123",
        "ai_base_url": "https://test.google.com"
    }

    response = client.put("/agent/settings/", headers=headers, json=update_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["ai_enabled"] is False
    assert data["ai_provider"] == "gemini"
    assert data["ai_model"] == "gemini-pro"
    assert data["ai_api_key"] == "test-key-123"
    assert data["ai_base_url"] == "https://test.google.com"

    # Verify persistence
    get_resp = client.get("/agent/settings/", headers=headers)
    assert get_resp.json()["ai_provider"] == "gemini"

def test_partial_update_settings(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "agent_user_partial")
    headers = {"Authorization": f"Bearer {token}"}

    # First update some
    client.put("/agent/settings/", headers=headers, json={"ai_provider": "claude", "ai_model": "sonnet"})
    
    # Then partial update
    response = client.put("/agent/settings/", headers=headers, json={"ai_enabled": False})
    assert response.status_code == 200
    data = response.json()
    assert data["ai_enabled"] is False
    assert data["ai_provider"] == "claude" # Stays the same
    assert data["ai_model"] == "sonnet"    # Stays the same
