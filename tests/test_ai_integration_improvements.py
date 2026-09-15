import pytest
import json
from unittest.mock import patch, MagicMock
from sqlalchemy.orm import Session
from app.models.user_models import UserDB
from app.models.agent_models import AgentSettingsDB, AgentMemoryDB
from app.models.flow_models import FlowDB
from app.models.feature_models import ProjectDB
from app.services.ai_task_manager import ai_task_manager

def get_tokens(client, db_session: Session):
    # Admin User
    client.post("/auth/create", json={"username": "ai_admin", "password": "Password123!", "accepted_terms": True, "company": "AI Admin Corp"})
    admin_user = db_session.query(UserDB).filter(UserDB.username == "ai_admin").first()
    admin_user.role = "admin"
    admin_user.status = "active"
    db_session.commit()
    admin_token = client.post("/auth/login", json={"username": "ai_admin", "password": "Password123!"}).json()["access_token"]

    # Regular User 1 (Tenant 1)
    client.post("/auth/create", json={"username": "ai_member1", "password": "Password123!", "accepted_terms": True, "company": "Company Alpha"})
    user1 = db_session.query(UserDB).filter(UserDB.username == "ai_member1").first()
    user1.role = "user"
    user1.status = "active"
    db_session.commit()
    user1_token = client.post("/auth/login", json={"username": "ai_member1", "password": "Password123!"}).json()["access_token"]

    # Regular User 2 (Tenant 2)
    client.post("/auth/create", json={"username": "ai_member2", "password": "Password123!", "accepted_terms": True, "company": "Company Beta"})
    user2 = db_session.query(UserDB).filter(UserDB.username == "ai_member2").first()
    user2.role = "user"
    user2.status = "active"
    db_session.commit()
    user2_token = client.post("/auth/login", json={"username": "ai_member2", "password": "Password123!"}).json()["access_token"]

    return {
        "admin": {"user": admin_user, "token": admin_token, "headers": {"Authorization": f"Bearer {admin_token}"}},
        "user1": {"user": user1, "token": user1_token, "headers": {"Authorization": f"Bearer {user1_token}"}},
        "user2": {"user": user2, "token": user2_token, "headers": {"Authorization": f"Bearer {user2_token}"}},
    }

def test_debug_settings_security_and_masking(client, db_session: Session):
    tokens = get_tokens(client, db_session)

    # 1. Unauthenticated request MUST be rejected with 401
    resp_unauth = client.get("/analysis/skills/debug_settings")
    assert resp_unauth.status_code == 401

    # 2. Non-admin user MUST be forbidden with 403
    resp_user = client.get("/analysis/skills/debug_settings", headers=tokens["user1"]["headers"])
    assert resp_user.status_code == 403

    # 3. Setup sensitive API key for Admin
    admin_user = tokens["admin"]["user"]
    settings = AgentSettingsDB(
        user_id=admin_user.id,
        ai_enabled=True,
        ai_provider="openai",
        ai_model="gpt-4o",
        ai_api_key="sk-secret-super-sensitive-api-key-12345"
    )
    db_session.add(settings)
    db_session.commit()

    # 4. Admin request MUST NOT leak full plaintext API key
    resp_admin = client.get("/analysis/skills/debug_settings", headers=tokens["admin"]["headers"])
    assert resp_admin.status_code == 200
    data = resp_admin.json()
    assert "sk-secret-super-sensitive-api-key-12345" not in json.dumps(data)
    assert data["key_configured"] is True
    assert "..." in data["key_masked"]

def test_maintenance_routes_authorization(client, db_session: Session):
    tokens = get_tokens(client, db_session)

    for route in ["/analysis/skills/clean-ghosts", "/analysis/skills/dump-flow", "/analysis/skills/debug-db"]:
        # Unauthenticated rejected with 401
        res_unauth = client.get(route)
        assert res_unauth.status_code == 401, f"Failed for {route}"

        # Regular user rejected with 403
        res_user = client.get(route, headers=tokens["user1"]["headers"])
        assert res_user.status_code == 403, f"Failed for {route}"

        # Admin allowed with 200
        res_admin = client.get(route, headers=tokens["admin"]["headers"])
        assert res_admin.status_code == 200, f"Failed for {route}"

def test_bola_prevention_on_user_active_task_and_cancel(client, db_session: Session):
    tokens = get_tokens(client, db_session)
    user1 = tokens["user1"]["user"]
    user2 = tokens["user2"]["user"]

    # 1. Unauthenticated is rejected
    assert client.get("/analysis/skills/user-active-task").status_code == 401

    # 2. User 1 tries to query User 2's active task -> 403 BOLA blocked!
    resp_bola = client.get(f"/analysis/skills/user-active-task?user_id={user2.id}", headers=tokens["user1"]["headers"])
    assert resp_bola.status_code == 403

    # 3. User 1 queries own active task -> 200 OK
    resp_own = client.get(f"/analysis/skills/user-active-task?user_id={user1.id}", headers=tokens["user1"]["headers"])
    assert resp_own.status_code == 200
    assert "active_task" in resp_own.json()

    # 4. User 1 queries without user_id query param -> defaults to own user_id
    resp_default = client.get("/analysis/skills/user-active-task", headers=tokens["user1"]["headers"])
    assert resp_default.status_code == 200

    # 5. Start a task for User 2
    task = ai_task_manager.start_task(
        user2.id,
        1,
        "test_skill",
        lambda db, u: "done"
    )
    task_id = task["task_id"]

    # 6. User 1 tries to view User 2's task status -> 403 BOLA blocked!
    resp_view = client.get(f"/analysis/skills/tasks/{task_id}", headers=tokens["user1"]["headers"])
    assert resp_view.status_code == 403

    # 7. User 1 tries to cancel User 2's task -> 403 BOLA blocked!
    resp_cancel = client.post(f"/analysis/skills/tasks/{task_id}/cancel?user_id={user2.id}", headers=tokens["user1"]["headers"])
    assert resp_cancel.status_code == 403

    # 8. User 2 views own task status -> 200 OK
    resp_view_own = client.get(f"/analysis/skills/tasks/{task_id}", headers=tokens["user2"]["headers"])
    assert resp_view_own.status_code == 200

def test_feedback_persists_to_agent_memory_db(client, db_session: Session):
    tokens = get_tokens(client, db_session)
    user1 = tokens["user1"]["user"]

    # Create dummy flow
    flow = FlowDB(name="Feedback Flow", project_id=1)
    db_session.add(flow)
    db_session.commit()

    feedback_payload = {
        "suggestion_name": "Cenário de Teste Inválido",
        "reason": "Esta API não aceita campos vazios na regra de negócio."
    }

    # Post feedback
    res = client.post(
        f"/analysis/skills/flow/{flow.id}/feedback?user_id={user1.id}",
        headers=tokens["user1"]["headers"],
        json=feedback_payload
    )
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    # Verify database persistence in AgentMemoryDB
    memory_record = db_session.query(AgentMemoryDB).filter(
        AgentMemoryDB.flow_id == flow.id,
        AgentMemoryDB.role == "feedback_rejected"
    ).first()

    assert memory_record is not None
    assert memory_record.user_id == user1.id
    data = json.loads(memory_record.content)
    assert data["suggestion_name"] == feedback_payload["suggestion_name"]
    assert data["reason"] == feedback_payload["reason"]

def test_analysis_history_multi_tenant_isolation(client, db_session: Session):
    from app.models.product_models import ProductModel
    tokens = get_tokens(client, db_session)
    user1 = tokens["user1"]["user"]
    user2 = tokens["user2"]["user"]

    # 1. Unauthenticated -> 401
    assert client.get("/analysis/history").status_code == 401

    # 2. Create product belonging to Company Beta (User 2)
    prod2 = ProductModel(name="Product Beta", company_id=user2.company_id)
    db_session.add(prod2)
    db_session.commit()

    # 3. User 1 (Company Alpha) attempts to query User 2's project history -> 403 Forbidden!
    resp_forbidden = client.get(f"/analysis/history?project_id={prod2.id}", headers=tokens["user1"]["headers"])
    assert resp_forbidden.status_code == 403

    # 4. User 2 queries own project history -> 200 OK
    resp_allowed = client.get(f"/analysis/history?project_id={prod2.id}", headers=tokens["user2"]["headers"])
    assert resp_allowed.status_code == 200

def test_execute_skill_and_save_flow_bola_protection(client, db_session: Session):
    tokens = get_tokens(client, db_session)
    user1 = tokens["user1"]["user"]
    user2 = tokens["user2"]["user"]

    # 1. Execute skill unauthenticated -> 401
    assert client.post("/analysis/skills/flow/1/execute/qa_specialist_api").status_code == 401

    # 2. Execute skill impersonating user 2 -> 403 Forbidden
    resp_bola_exec = client.post(
        f"/analysis/skills/flow/1/execute/qa_specialist_api?user_id={user2.id}",
        headers=tokens["user1"]["headers"]
    )
    assert resp_bola_exec.status_code == 403

    # 3. Save flow unauthenticated -> 401
    assert client.post("/analysis/skills/save-generated-flow", json={}).status_code == 401

    # 4. Save flow impersonating user 2 -> 403 Forbidden
    resp_bola_save = client.post(
        f"/analysis/skills/save-generated-flow?user_id={user2.id}",
        headers=tokens["user1"]["headers"],
        json={"name": "Malicious Flow"}
    )
    assert resp_bola_save.status_code == 403

    # 5. Save flow impersonating different company_id -> 403 Forbidden
    resp_bola_comp = client.post(
        f"/analysis/skills/save-generated-flow?company_id=9999",
        headers=tokens["user1"]["headers"],
        json={"name": "Malicious Flow"}
    )
    assert resp_bola_comp.status_code == 403
