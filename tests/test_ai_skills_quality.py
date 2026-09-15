import os
import json
from unittest.mock import patch, MagicMock
from app.services.skill_service import SkillService
from app.services.analysis_service import AnalysisService
from app.models.agent_models import AgentMemoryDB, AgentSettingsDB
from app.models.user_models import UserDB
from tests.test_flow import get_headers

def test_all_skill_definitions_validity():
    skills_dir = SkillService.SKILLS_DIR
    assert os.path.exists(skills_dir)
    skill_files = [f for f in os.listdir(skills_dir) if f.endswith(".json")]
    assert len(skill_files) >= 13

    for fname in skill_files:
        fpath = os.path.join(skills_dir, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "name" in data
        assert "prompt" in data
        assert len(data["prompt"].strip()) > 30

def test_prune_flow_for_llm_preserves_crucial_qa_details():
    raw_flow = {
        "flow_type": "mobile",
        "nodes": [
            {
                "id": "node_1",
                "type": "custom",
                "data": {
                    "name": "Tela de Login",
                    "nodeType": "mobile",
                    "isMainFlow": True,
                    "unnecessary_heavy_styling": {"color": "#fff", "border": "1px"}
                }
            }
        ],
        "edges": [{"id": "e1", "source": "start", "target": "node_1"}],
        "cardData": {
            "node_1": {
                "name": "Tela de Login",
                "nodeType": "mobile",
                "description": "Autenticação mobile com Appium",
                "e2eSteps": [
                    {
                        "id": "step_1",
                        "name": "Digitar Usuário",
                        "type": "action",
                        "action": "type",
                        "properties": {
                            "action": "type",
                            "selector": "~username_input",
                            "value": "teste@flow.com",
                            "screenshot": "base64_massive_image_data_here...",
                            "html_snippet": "<div>...</div>"
                        }
                    },
                    {
                        "id": "step_2",
                        "name": "Tocar Entrar",
                        "type": "action",
                        "properties": {
                            "action": "tap",
                            "selector": "~login_btn"
                        }
                    }
                ]
            }
        }
    }

    pruned = SkillService._prune_flow_for_llm(raw_flow)
    assert pruned["flow_type"] == "mobile"
    assert len(pruned["nodes"]) == 1
    assert pruned["nodes"][0]["data"]["nodeType"] == "mobile"
    assert pruned["nodes"][0]["data"]["isMainFlow"] is True
    assert "unnecessary_heavy_styling" not in pruned["nodes"][0]["data"]

    card = pruned["cardData"]["node_1"]
    assert card["nodeType"] == "mobile"
    assert len(card["e2eSteps"]) == 2
    step1 = card["e2eSteps"][0]
    assert step1["action"] == "type"
    assert "screenshot" not in step1["properties"]
    assert "html_snippet" not in step1["properties"]

    step2 = card["e2eSteps"][1]
    # action was detected from properties['action']
    assert step2["action"] == "tap"

def test_build_flow_blueprint_generation():
    pruned_data = {
        "nodes": [
            {"id": "n1", "data": {"name": "Auth"}},
            {"id": "n2", "data": {"name": "Get Profile"}}
        ],
        "cardData": {
            "n1": {
                "name": "Auth",
                "apiCalls": [{"method": "POST", "url": "https://api.example.com/auth"}]
            },
            "n2": {
                "name": "Get Profile",
                "apiCalls": [{"method": "GET", "url": "https://api.example.com/profile"}]
            }
        }
    }
    bp = SkillService.build_flow_blueprint("User Flow", "api", pruned_data)
    assert bp["platform"] == "api"
    assert bp["total_nodes"] == 2
    assert "POST https://api.example.com/auth" in bp["key_touchpoints"]
    assert "GET https://api.example.com/profile" in bp["key_touchpoints"]

def test_analysis_service_call_llm_memory_isolation(db_session):
    user = UserDB(username="skillqualityuser", email="skill@test.com", role="member")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    settings = AgentSettingsDB(
        user_id=user.id,
        ai_enabled=True,
        ai_provider="openai",
        ai_model="gpt-4o",
        ai_api_key="sk-fakekey"
    )
    db_session.add(settings)

    # Add memory with custom roles that MUST NOT be passed to LLM
    db_session.add(AgentMemoryDB(user_id=user.id, flow_id=999, role="feedback_rejected", content=json.dumps({"reason": "not good"})))
    db_session.add(AgentMemoryDB(user_id=user.id, flow_id=999, role="user", content="Past question 1"))
    db_session.add(AgentMemoryDB(user_id=user.id, flow_id=999, role="assistant", content="Past answer 1"))
    db_session.commit()

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Fresh response"}}]
        }
        mock_post.return_value = mock_resp

        res = AnalysisService._call_llm(db_session, user.id, "Current Prompt", flow_id=999)
        assert res == "Fresh response"

        # Inspect sent payload
        sent_messages = mock_post.call_args[1]["json"]["messages"]
        roles = [m["role"] for m in sent_messages]

        # Verify feedback_rejected was EXCLUDED
        assert "feedback_rejected" not in roles
        # Verify chronological order: past user -> past assistant -> current user
        assert roles == ["user", "assistant", "user"]
        assert sent_messages[-1]["content"] == "Current Prompt"

def test_execute_skill_endpoint_context(client, db_session):
    headers = get_headers(client, "execskilluser")
    prod = client.post("/products/", headers=headers, json={"name": "P1"}).json()
    feat = client.post("/features/", headers=headers, json={"name": "F1", "product_id": prod["id"]}).json()

    flow_payload = {
        "projectId": feat["id"],
        "flow_type": "api",
        "nodes": [
            {"id": "start", "type": "startNode", "position": {"x": 0, "y": 0}, "data": {"name": "Start"}},
            {"id": "n1", "type": "custom", "position": {"x": 100, "y": 0}, "data": {"name": "Login API"}}
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "n1", "type": "buttonedge"}
        ],
        "cardData": {
            "n1": {
                "name": "Login API",
                "nodeType": "api",
                "apiCalls": [
                    {
                        "id": "c1",
                        "name": "Auth",
                        "method": "POST",
                        "url": "https://api.test/auth",
                        "body": "{}",
                        "assertions": [{"source": "status_code", "operator": "equals", "target": 200}]
                    }
                ]
            }
        }
    }
    client.post("/flow/save", headers=headers, json=flow_payload)

    from app.models.flow_models import FlowDB
    flow_obj = db_session.query(FlowDB).filter(FlowDB.project_id == feat["id"]).first()
    assert flow_obj is not None

    with patch.object(SkillService, "execute_skill", return_value="Analyzed API") as mock_exec:
        resp = client.post(
            f"/analysis/skills/flow/{flow_obj.id}/execute/qa_specialist_api",
            headers=headers
        )
        assert resp.status_code == 200
        assert resp.json() == "Analyzed API"

        # Verify context passed to SkillService
        passed_context = mock_exec.call_args[0][3]
        assert passed_context["flow_type"] == "api"
        assert "mapping_context" in passed_context
        assert "blueprint" in passed_context
        assert len(passed_context["nodes"]) == 2

