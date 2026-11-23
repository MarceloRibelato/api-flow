import xml.etree.ElementTree as ET
from types import SimpleNamespace

from app.services.url_utils import is_blocked_domain, is_internal_protected_url
from app.services.database_executor_service import DatabaseExecutorService, _is_internal_flow_database
from app.routes.execution_routes import _is_record_failed


def test_ssrf_protection_blocks_internal_hosts_and_cloud_metadata():
    assert is_internal_protected_url("http://license-manager:8001/api/v1/licenses") is True
    assert is_internal_protected_url("http://flow-redis:6379") is True
    assert is_internal_protected_url("http://db:5432") is True
    assert is_internal_protected_url("http://169.254.169.254/latest/meta-data/") is True
    assert is_blocked_domain("http://license-manager:8001/health") is True

    # Normal external and customer application URLs must remain allowed
    assert is_internal_protected_url("https://api.stripe.com/v1/charges?db=1") is False
    assert is_blocked_domain("https://api.stripe.com/v1/charges?db=1") is False


def test_database_executor_blocks_internal_flow_database():
    assert _is_internal_flow_database("postgresql://flow:flow@db:5432/flow_db") is True
    assert _is_internal_flow_database("postgresql://flow:flow@flow-pgbouncer:5432/flow_db") is True

    res = DatabaseExecutorService.execute_db_step(
        {
            "connection_string": "postgresql://flow:flow@db:5432/flow_db",
            "query": "SELECT * FROM users",
        },
        {},
    )
    assert res["status"] == 403
    assert res["reason"] == "Forbidden Target Database"


def test_is_record_failed_detection():
    passed_rec = SimpleNamespace(status_code=200, error_message=None)
    failed_http_rec = SimpleNamespace(status_code=500, error_message=None)
    failed_err_rec = SimpleNamespace(status_code=200, error_message="AssertionError: expected 200")

    assert _is_record_failed(passed_rec) is False
    assert _is_record_failed(failed_http_rec) is True
    assert _is_record_failed(failed_err_rec) is True
