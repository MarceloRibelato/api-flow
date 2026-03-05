import pytest
from tests.test_projects import get_auth_token_and_admin
from app.models.flow_models import FlowDB, FlowCardDataDB
from app.models.api_test_history_models import ApiExecutionHistory
from app.models.agent_models import AgentSettingsDB
import json
from unittest.mock import patch, MagicMock

def test_analyze_flow_redundancy(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "analysis_user")
    headers = {"Authorization": f"Bearer {token}"}

    # Setup a flow with duplicate calls
    flow = FlowDB(name="Test Flow", project_id=1)
    db_session.add(flow)
    db_session.commit()

    card1 = FlowCardDataDB(
        flow_id=flow.id,
        node_id="1",
        name="Node 1",
        api_calls=[{"method": "GET", "url": "https://api.example.com/data"}]
    )
    card2 = FlowCardDataDB(
        flow_id=flow.id,
        node_id="2",
        name="Node 2",
        api_calls=[{"method": "GET", "url": "https://api.example.com/data"}]
    )
    db_session.add_all([card1, card2])
    db_session.commit()

    response = client.get(f"/analysis/flow/{flow.id}", headers=headers)
    assert response.status_code == 200
    data = response.json()
    
    # Should find 1 redundancy (Node 1 and Node 2 have duplicate call)
    redundancies = [r for r in data if r["type"] == "redundancy"]
    assert len(redundancies) >= 1
    assert "Duplicate request detected: GET:https://api.example.com/data" in redundancies[0]["message"]

def test_analyze_history_trends(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "history_analysis_user")
    headers = {"Authorization": f"Bearer {token}"}

    import datetime
    base_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    
    # Add history data to simulate regression
    # 5 runs at 100ms (baseline)
    for i in range(5):
        db_session.add(ApiExecutionHistory(
            url="https://api.test/slow", method="GET", response_time=100, 
            project_id=1, user_id=1, status_code=200, execution_id=f"base-{i}",
            created_at=base_time + datetime.timedelta(minutes=i)
        ))
    db_session.commit()
    
    # 5 runs at 500ms (recent)
    for i in range(5):
        db_session.add(ApiExecutionHistory(
            url="https://api.test/slow", method="GET", response_time=500, 
            project_id=1, user_id=1, status_code=200, execution_id=f"recent-{i}",
            created_at=base_time + datetime.timedelta(hours=1, minutes=i)
        ))
    db_session.commit()

    response = client.get("/analysis/history?project_id=1", headers=headers)
    assert response.status_code == 200
    data = response.json()
    
    # Should find a regression
    regressions = [r for r in data if r["metric"] == "regression"]
    assert len(regressions) >= 1
    assert "GET https://api.test/slow" in regressions[0]["resource"]

def test_generate_assertions_simulation(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "assertion_user")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "status": 201,
        "headers": {"Content-Type": "application/json"},
        "body": {"id": 123, "success": True},
        "mode": "ai" # Will fall back to simulation because AI is not configured
    }

    response = client.post("/analysis/assertions", headers=headers, json=payload)
    assert response.status_code == 200
    data = response.json()
    
    assert any(a["source"] == "status" and a["target"] == 201 for a in data)
    assert any(a["source"] == "body" and a["property"] == "id" and a["operator"] == "exists" for a in data)

def test_generate_assertions_contract(client, db_session):
    token = get_auth_token_and_admin(client, db_session, "contract_user")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "status": 200,
        "headers": {"Content-Type": "application/json"},
        "body": {"name": "Test", "email": "test@example.com"},
        "mode": "contract"
    }

    response = client.post("/analysis/assertions", headers=headers, json=payload)
    assert response.status_code == 200
    data = response.json()
    
    assert len(data) == 1
    assert data[0]["operator"] == "json_schema"
    
    schema = json.loads(data[0]["target"])
    assert schema["type"] == "object"
    assert "name" in schema["properties"]
    assert schema["properties"]["email"]["format"] == "email"

@patch('app.services.analysis_service.requests.post')
def test_analyze_with_ai_openai(mock_post, client, db_session):
    token = get_auth_token_and_admin(client, db_session, "ai_user_openai")
    headers = {"Authorization": f"Bearer {token}"}
    
    from app.models.user_models import UserDB
    user = db_session.query(UserDB).filter(UserDB.username == "ai_user_openai").first()

    settings = AgentSettingsDB(user_id=user.id, ai_enabled=True, ai_provider="openai", ai_model="gpt-4o", ai_api_key="sk-test")
    db_session.add(settings)
    
    flow = FlowDB(name="AI Flow", project_id=1)
    db_session.add(flow)
    db_session.commit()

    card1 = FlowCardDataDB(flow_id=flow.id, node_id="1", name="Node 1", api_calls=[{"method": "GET", "url": "https://api.example.com"}])
    db_session.add(card1)
    db_session.commit()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": '[{"type": "security", "severity": "high", "message": "Test AI", "details": "Test"}]'}}]
    }
    mock_post.return_value = mock_resp

    response = client.get(f"/analysis/flow/{flow.id}", headers=headers)
    assert response.status_code == 200
    data = response.json()
    
    ai_suggestions = [r for r in data if r["type"] == "security"]
    assert len(ai_suggestions) == 1
    assert ai_suggestions[0]["message"] == "Test AI"

@patch('app.services.analysis_service.requests.post')
def test_analyze_with_ai_anthropic(mock_post, client, db_session):
    token = get_auth_token_and_admin(client, db_session, "ai_user_anthropic")
    headers = {"Authorization": f"Bearer {token}"}

    from app.models.user_models import UserDB
    user = db_session.query(UserDB).filter(UserDB.username == "ai_user_anthropic").first()

    settings = AgentSettingsDB(user_id=user.id, ai_enabled=True, ai_provider="anthropic", ai_model="claude", ai_api_key="sk-test")
    db_session.add(settings)
    
    flow = FlowDB(name="AI Flow 2", project_id=1)
    db_session.add(flow)
    db_session.commit()

    card1 = FlowCardDataDB(flow_id=flow.id, node_id="1", name="Node 1", api_calls=[{"method": "GET", "url": "https://api.example.com"}])
    db_session.add(card1)
    db_session.commit()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "content": [{"text": '[{"type": "optimization", "severity": "low", "message": "Cache It", "details": "Add caching"}]'}]
    }
    mock_post.return_value = mock_resp

    response = client.get(f"/analysis/flow/{flow.id}", headers=headers)
    assert response.status_code == 200
    data = response.json()
    
    ai_suggestions = [r for r in data if r["type"] == "optimization"]
    assert len(ai_suggestions) == 1
    assert ai_suggestions[0]["message"] == "Cache It"
