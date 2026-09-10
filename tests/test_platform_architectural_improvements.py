import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from sqlalchemy.orm import Session

from app.models.company_models import CompanyDB
from app.models.user_models import UserDB
from app.models.product_models import ProductModel
from app.models.feature_models import FeatureModel
from app.models.flow_models import FlowDB
from app.models.schedule_models import ScheduleModel
from app.models.mock_models import ServiceMockDB
from app.models.api_test_history_models import ApiExecutionHistory
from app.models.audit_models import AuditLogDB

from app.services.auth_service import AuthService
from app.services.flow_service import FlowService
from app.services.dashboard_service import DashboardService
from app.services.audit_service import AuditService
from app.services.pdf_service import PDFService
from app.services.queue_executor_service import QueueExecutorService
from app.services.scheduler_service import _send_schedule_webhook_notifications, execute_job_logic
from app.auth import get_password_hash


# ─────────────────────────────────────────────────────────────────────────────
# 1. FLOW PERSISTENCE & EXPLICIT COMMIT
# ─────────────────────────────────────────────────────────────────────────────

def test_flow_service_save_persists_nodes_and_commits(db_session: Session):
    """Verify that FlowService.save explicitly commits node changes and updates timestamps."""
    company = CompanyDB(name="Flow Corp")
    db_session.add(company)
    db_session.commit()

    product = ProductModel(name="Flow Prod", company_id=company.id)
    db_session.add(product)
    db_session.commit()

    feature = FeatureModel(name="Flow Feat", product_id=product.id)
    db_session.add(feature)
    db_session.commit()

    flow = FlowDB(name="Checkout Flow", project_id=feature.id, company_id=company.id)
    db_session.add(flow)
    db_session.commit()

    # Initial flow has empty nodes
    loaded_initial = FlowService.load(db_session, feature.id, company.id, flow.id)
    assert loaded_initial["nodes"] == []

    from app.schemas.flow_schemas import FlowSaveSchema, NodeSchema, NodeDataBasic, EdgeSchema
    new_flow_data = FlowSaveSchema(
        projectId=feature.id,
        flowId=flow.id,
        name="Checkout Flow",
        flow_type="api",
        nodes=[
            NodeSchema(
                id="node-1",
                type="custom",
                position={"x": 100.0, "y": 100.0},
                data=NodeDataBasic(name="Login")
            ),
            NodeSchema(
                id="node-2",
                type="custom",
                position={"x": 300.0, "y": 100.0},
                data=NodeDataBasic(name="Get Profile")
            )
        ],
        edges=[
            EdgeSchema(id="edge-1", source="node-1", target="node-2")
        ],
        cardData={}
    )

    res = FlowService.save(db_session, new_flow_data, company_id=company.id, user_id=1)
    assert res["status"] == "saved"

    # Verify directly from DB query without in-memory cache
    db_session.expire_all()
    reloaded_flow = db_session.query(FlowDB).filter(FlowDB.id == flow.id).first()
    assert len(reloaded_flow.flow_nodes) == 2
    assert len(reloaded_flow.flow_edges) == 1
    assert reloaded_flow.updated_at is not None

    loaded_after = FlowService.load(db_session, feature.id, company.id, flow.id)
    assert len(loaded_after["nodes"]) == 2
    assert len(loaded_after["edges"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 2. RBAC & ONBOARDING BOOTSTRAP
# ─────────────────────────────────────────────────────────────────────────────

def test_auth_onboarding_first_user_admin_subsequent_viewer(client, db_session: Session):
    """Verify first user becomes active admin while second user becomes pending viewer."""
    # First user
    u1_resp = client.post("/auth/create", json={
        "username": "first_admin_user",
        "email": "admin1@flow.com",
        "password": "Password123!",
        "accepted_terms": True,
        "company": "Boot Corp"
    })
    assert u1_resp.status_code == 200

    u1 = db_session.query(UserDB).filter(UserDB.username == "first_admin_user").first()
    assert u1.role == "admin"
    assert u1.status == "active"

    # Second user in the system
    u2_resp = client.post("/auth/create", json={
        "username": "second_viewer_user",
        "email": "viewer2@flow.com",
        "password": "Password123!",
        "accepted_terms": True,
        "company": "Boot Corp"
    })
    assert u2_resp.status_code == 200

    u2 = db_session.query(UserDB).filter(UserDB.username == "second_viewer_user").first()
    assert u2.role == "viewer"
    assert u2.status == "pending"


# ─────────────────────────────────────────────────────────────────────────────
# 3. MULTI-TENANT DASHBOARD AGGREGATION & BOLA ISOLATION
# ─────────────────────────────────────────────────────────────────────────────

def test_dashboard_service_multi_tenant_isolation(db_session: Session):
    """Verify that DashboardService strictly isolates metrics, stats, and failures per company_id."""
    comp_a = CompanyDB(name="Company Alpha")
    comp_b = CompanyDB(name="Company Beta")
    db_session.add_all([comp_a, comp_b])
    db_session.commit()

    user_a = UserDB(username="user_alpha", email="alpha@test.com", company_id=comp_a.id, role="admin", status="active")
    user_b = UserDB(username="user_beta", email="beta@test.com", company_id=comp_b.id, role="admin", status="active")
    db_session.add_all([user_a, user_b])
    db_session.commit()

    # Company Alpha executions: 1 pass (200ms), 1 fail (400ms)
    db_session.add(ApiExecutionHistory(
        user_id=user_a.id, status_code=200, response_time=200, api_name="Alpha OK",
        created_at=datetime.now(timezone.utc), execution_id="ex-a1"
    ))
    db_session.add(ApiExecutionHistory(
        user_id=user_a.id, status_code=500, response_time=400, api_name="Alpha Fail",
        error_message="DB connection error", created_at=datetime.now(timezone.utc), execution_id="ex-a2"
    ))

    # Company Beta executions: 3 passes (100ms each)
    for i in range(3):
        db_session.add(ApiExecutionHistory(
            user_id=user_b.id, status_code=200, response_time=100, api_name=f"Beta OK {i}",
            created_at=datetime.now(timezone.utc), execution_id=f"ex-b{i}"
        ))
    db_session.commit()

    # Test Summary Stats for Alpha
    stats_a = DashboardService.get_summary_stats(db_session, company_id=comp_a.id)
    assert stats_a["total_executions"] == 2
    assert stats_a["total_failures"] == 1
    assert stats_a["success_rate"] == 50.0
    assert stats_a["avg_response_time"] == 300.0

    # Test Summary Stats for Beta
    stats_b = DashboardService.get_summary_stats(db_session, company_id=comp_b.id)
    assert stats_b["total_executions"] == 3
    assert stats_b["total_failures"] == 0
    assert stats_b["success_rate"] == 100.0
    assert stats_b["avg_response_time"] == 100.0

    # Test Failures Isolation
    failures_a = DashboardService.get_recent_failures(db_session, company_id=comp_a.id)
    assert len(failures_a) == 1
    assert failures_a[0]["api_name"] == "Alpha Fail"

    failures_b = DashboardService.get_recent_failures(db_session, company_id=comp_b.id)
    assert len(failures_b) == 0


def test_dashboard_routes_bola_prevention(client, db_session: Session):
    """Verify dashboard API endpoints return data scoped to authenticated user's company."""
    # Setup Tenant 1
    client.post("/auth/create", json={"username": "user1_t1", "password": "Password123!", "accepted_terms": True, "company": "Tenant 1"})
    u1 = db_session.query(UserDB).filter(UserDB.username == "user1_t1").first()
    u1.status = "active"
    u1.role = "admin"
    db_session.commit()
    t1_token = client.post("/auth/login", json={"username": "user1_t1", "password": "Password123!"}).json()["access_token"]

    # Setup Tenant 2
    client.post("/auth/create", json={"username": "user2_t2", "password": "Password123!", "accepted_terms": True, "company": "Tenant 2"})
    u2 = db_session.query(UserDB).filter(UserDB.username == "user2_t2").first()
    u2.status = "active"
    u2.role = "admin"
    db_session.commit()
    t2_token = client.post("/auth/login", json={"username": "user2_t2", "password": "Password123!"}).json()["access_token"]

    # Insert executions for Tenant 1
    db_session.add(ApiExecutionHistory(
        user_id=u1.id, status_code=200, response_time=150, api_name="T1 API",
        created_at=datetime.now(timezone.utc), execution_id="t1-ex1"
    ))
    db_session.commit()

    # Tenant 1 queries metrics
    r1 = client.get("/dashboard/metrics", headers={"Authorization": f"Bearer {t1_token}"})
    assert r1.status_code == 200
    assert r1.json()["total_executions"] == 1

    # Tenant 2 queries metrics -> must be 0
    r2 = client.get("/dashboard/metrics", headers={"Authorization": f"Bearer {t2_token}"})
    assert r2.status_code == 200
    assert r2.json()["total_executions"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. MOCK SERVICE MULTI-TENANT ISOLATION (NO GLOBAL FALLBACK)
# ─────────────────────────────────────────────────────────────────────────────

def test_mock_service_no_cross_tenant_leakage(client, db_session: Session):
    """Verify that project mock queries never leak mocks from other tenants."""
    comp_a = CompanyDB(name="Comp A")
    comp_b = CompanyDB(name="Comp B")
    db_session.add_all([comp_a, comp_b])
    db_session.commit()

    user_a = UserDB(username="mock_user_a", company_id=comp_a.id, role="admin", status="active",
                    hashed_password=get_password_hash("Password123!"))
    user_b = UserDB(username="mock_user_b", company_id=comp_b.id, role="admin", status="active",
                    hashed_password=get_password_hash("Password123!"))
    db_session.add_all([user_a, user_b])
    db_session.commit()

    token_b = client.post("/auth/login", json={"username": "mock_user_b", "password": "Password123!"}).json()["access_token"]

    prod_a = ProductModel(name="Prod A", company_id=comp_a.id)
    prod_b = ProductModel(name="Prod B", company_id=comp_b.id)
    db_session.add_all([prod_a, prod_b])
    db_session.commit()

    # Mock in Tenant A
    mock_a = ServiceMockDB(
        product_id=prod_a.id,
        name="Tenant A Secret Mock",
        slug="tenant-a-secret-mock",
        description="Private mock",
        is_active=True
    )
    db_session.add(mock_a)
    db_session.commit()

    # Query mocks for Tenant B's product as Tenant B user
    resp = client.get(f"/projects/{prod_b.id}/mocks", headers={"Authorization": f"Bearer {token_b}"})
    assert resp.status_code == 200
    # Must NOT return mock_a from Tenant A (preventing old global fallback)
    assert resp.json() == []


# ─────────────────────────────────────────────────────────────────────────────
# 5. AUDIT SERVICE UNIT-OF-WORK PROTECTION (SAVEPOINT SAFETY)
# ─────────────────────────────────────────────────────────────────────────────

def test_audit_service_log_action_protects_caller_transaction(db_session: Session):
    """Verify that AuditService.log_action uses begin_nested so audit failures don't roll back caller work."""
    comp = CompanyDB(name="Audit Corp")
    db_session.add(comp)
    db_session.commit()

    user = UserDB(username="audit_actor", company_id=comp.id, role="admin", status="active")
    db_session.add(user)
    db_session.commit()

    # 1. Normal audit logging
    log_entry = AuditService.log_action(
        db=db_session,
        company_id=comp.id,
        user=user,
        action="TEST_ACTION",
        resource_type="flow",
        resource_id="123",
        resource_name="Test Flow"
    )
    assert log_entry is not None
    assert log_entry.action == "TEST_ACTION"
    assert log_entry.company_id == comp.id

    # 2. Audit logging without company_id returns None safely
    assert AuditService.log_action(db=db_session, company_id=None, user=user, action="BAD", resource_type="flow") is None

    # 3. Caller has uncommitted changes, audit logging encounters DB error
    caller_prod = ProductModel(name="Uncommitted Product", company_id=comp.id)
    db_session.add(caller_prod)

    # Simulate error inside AuditService.log_action
    with patch.object(db_session, 'begin_nested', side_effect=Exception("Database lock error")):
        audit_result = AuditService.log_action(
            db=db_session,
            company_id=comp.id,
            user=user,
            action="FAILED_AUDIT",
            resource_type="product"
        )
        assert audit_result is None

    # Verify caller's session is STILL healthy and can commit its own changes
    db_session.commit()
    persisted = db_session.query(ProductModel).filter(ProductModel.name == "Uncommitted Product").first()
    assert persisted is not None


# ─────────────────────────────────────────────────────────────────────────────
# 6. SCHEDULER WEBHOOK NOTIFICATIONS & RESOURCE CLEANUP
# ─────────────────────────────────────────────────────────────────────────────

def test_scheduler_webhook_formatting_and_error_handling(db_session: Session):
    """Verify Slack, Teams, and generic webhook dispatching and resilience."""
    schedule = ScheduleModel(
        name="Nightly Run",
        type="flow",
        target_id=1,
        company_id=1,
        notification_urls="https://hooks.slack.com/services/test,https://webhook.office.com/webhookb2/test",
        last_run_status="success",
        last_run=datetime.now(timezone.utc),
        notifications_enabled=True
    )

    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200

        _send_schedule_webhook_notifications(db_session, schedule, success_count=10, fail_count=0)

        assert mock_post.call_count == 2

        # Check Slack payload
        slack_call = mock_post.call_args_list[0]
        assert "hooks.slack.com" in slack_call[0][0]
        slack_payload = slack_call[1]["json"]
        assert "attachments" in slack_payload
        assert slack_payload["attachments"][0]["color"] == "#36a64f"

        # Check Teams payload
        teams_call = mock_post.call_args_list[1]
        assert "webhook.office.com" in teams_call[0][0]
        teams_payload = teams_call[1]["json"]
        assert teams_payload["@type"] == "MessageCard"
        assert teams_payload["themeColor"] == "00FF00"


def test_scheduler_webhook_disabled_toggle(db_session: Session):
    """Verify that webhook dispatch is bypassed when notifications_enabled is False."""
    schedule = ScheduleModel(
        name="Silent Run",
        type="flow",
        target_id=1,
        company_id=1,
        notification_urls="https://hooks.slack.com/services/silent",
        last_run_status="success",
        notifications_enabled=False
    )

    with patch("requests.post") as mock_post:
        _send_schedule_webhook_notifications(db_session, schedule, success_count=5, fail_count=0)
        mock_post.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 7. QUEUE EXECUTOR RABBITMQ CONNECTION LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────

def test_queue_executor_rabbitmq_connection_closed_on_success_and_error():
    """Verify that RabbitMQ BlockingConnection is reliably closed in finally block."""
    mock_channel = MagicMock()
    mock_conn = MagicMock()
    mock_conn.channel.return_value = mock_channel
    mock_conn.is_closed = False

    # 1. Success case
    with patch("pika.BlockingConnection", return_value=mock_conn):
        with patch("pika.URLParameters"):
            res = QueueExecutorService._execute_rabbitmq(
                action="publish",
                conn_str="amqp://localhost",
                queue_name="tasks",
                payload="hello",
                timeout_ms=5000
            )
            assert res["status"] == 200
            mock_conn.close.assert_called_once()

    # 2. Exception case during channel operation
    mock_conn.reset_mock()
    mock_conn.is_closed = False
    mock_channel.basic_publish.side_effect = RuntimeError("RabbitMQ Socket Timeout")

    with patch("pika.BlockingConnection", return_value=mock_conn):
        with patch("pika.URLParameters"):
            res = QueueExecutorService._execute_rabbitmq(
                action="publish",
                conn_str="amqp://localhost",
                queue_name="tasks",
                payload="hello",
                timeout_ms=5000
            )
            assert res["status"] == 500
            assert "RabbitMQ Socket Timeout" in res["error"]
            # Connection MUST still be closed in finally:
            mock_conn.close.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 8. PDF MEDIA PATH TRAVERSAL PREVENTIONS
# ─────────────────────────────────────────────────────────────────────────────

def test_pdf_media_path_traversal_variations():
    """Verify comprehensive path traversal attacks against PDF media path resolver."""
    malicious_inputs = [
        "/../../../etc/passwd",
        "/screenshots/../../etc/shadow",
        "/videos/../../../../root/.bashrc",
        "../../media/secret.key",
        "/media/../../app/main.py",
        "../..\\..\\windows\\win.ini"
    ]
    for bad_path in malicious_inputs:
        resolved = PDFService._resolve_media_path(bad_path)
        assert resolved is None, f"Expected None for malicious path '{bad_path}', got '{resolved}'"


# ─────────────────────────────────────────────────────────────────────────────
# 9. CI/CD MULTI-TENANT PIPELINE ISOLATION
# ─────────────────────────────────────────────────────────────────────────────

def test_cicd_pipeline_tenant_isolation(client, db_session: Session):
    """Verify that CI/CD execution endpoints reject cross-tenant requests."""
    comp_a = CompanyDB(name="CI/CD Company A")
    comp_b = CompanyDB(name="CI/CD Company B")
    db_session.add_all([comp_a, comp_b])
    db_session.commit()

    user_b = UserDB(
        username="cicd_user_b",
        company_id=comp_b.id,
        role="admin",
        status="active",
        hashed_password=get_password_hash("Password123!")
    )
    db_session.add(user_b)
    db_session.commit()

    token_b = client.post("/auth/login", json={"username": "cicd_user_b", "password": "Password123!"}).json()["access_token"]

    # Product belonging exclusively to Company A
    prod_a = ProductModel(name="Exclusive Product A", company_id=comp_a.id)
    db_session.add(prod_a)
    db_session.commit()

    # User B (Company B) tries to trigger CI/CD for Product A
    resp = client.post(
        "/cicd/execute",
        headers={"Authorization": f"Bearer {token_b}"},
        json={
            "product_name": "Exclusive Product A",
            "environment_name": "Production"
        }
    )
    assert resp.status_code == 404
    assert "não encontrado" in resp.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# 10. SCHEDULER EXECUTION LOGIC RESOURCE CLEANUP (DB & HTTP SESSIONS)
# ─────────────────────────────────────────────────────────────────────────────

def test_scheduler_execute_job_logic_closes_sessions_on_exception(db_session: Session):
    """Verify that execute_job_logic reliably closes the DB session on unexpected exceptions."""
    comp = CompanyDB(name="Scheduler Resource Corp")
    db_session.add(comp)
    db_session.commit()

    schedule = ScheduleModel(
        name="Cleanup Test Schedule",
        type="flow",
        target_id=999,
        company_id=comp.id,
        status="active"
    )
    db_session.add(schedule)
    db_session.commit()

    mock_db_session = MagicMock(spec=Session)
    mock_db_session.query.side_effect = RuntimeError("Simulated internal DB failure")

    with patch("app.services.scheduler_service.SessionLocal", return_value=mock_db_session):
        execute_job_logic(schedule_id=schedule.id)
        # Verify db.close() was executed in finally:
        mock_db_session.close.assert_called_once()
