import pytest
import json
import threading
from unittest.mock import MagicMock, patch
from requests.adapters import HTTPAdapter

from app.services.assertion_engine import evaluate_assertion, _resolve_json_path
from app.services.extraction_engine import resolve_json_path, extract_value
from app.services.executors.api_executor_service import ApiExecutorService
from app.services.flow_executor_service import FlowExecutorService


# ── 1. JSON PATH BRACKET NOTATION IN ASSERTIONS & EXTRACTIONS ────────

def test_json_path_bracket_notation_in_assertions():
    resp_json = {
        "users": [
            {"id": 101, "name": "Alice", "tags": ["admin", "qa"]},
            {"id": 102, "name": "Bob", "tags": ["user"]}
        ],
        "meta": {"total": 2}
    }

    # Bracket indexing users[0].name
    a1 = {"source": "body", "property": "users[0].name", "operator": "equals", "value": "Alice"}
    res1 = evaluate_assertion(a1, 200, {}, resp_json, "", 50)
    assert res1["success"] is True
    assert res1["actual"] == "Alice"

    # Nested array bracket indexing users[0].tags[1]
    a2 = {"source": "body", "property": "users[0].tags[1]", "operator": "equals", "value": "qa"}
    res2 = evaluate_assertion(a2, 200, {}, resp_json, "", 50)
    assert res2["success"] is True
    assert res2["actual"] == "qa"

    # Root bracket with $ prefix $.users[1].id
    a3 = {"source": "body", "property": "$.users[1].id", "operator": "equals", "value": 102}
    res3 = evaluate_assertion(a3, 200, {}, resp_json, "", 50)
    assert res3["success"] is True

    # Quoted key inside bracket users[0]['name']
    a4 = {"source": "body", "property": "users[0]['name']", "operator": "equals", "value": "Alice"}
    res4 = evaluate_assertion(a4, 200, {}, resp_json, "", 50)
    assert res4["success"] is True

    # Out of bounds returns failure
    a5 = {"source": "body", "property": "users[99].name", "operator": "exists"}
    res5 = evaluate_assertion(a5, 200, {}, resp_json, "", 50)
    assert res5["success"] is False


def test_json_path_root_array_traversal():
    root_array = [
        {"code": "BR", "name": "Brazil"},
        {"code": "US", "name": "United States"}
    ]
    assert _resolve_json_path(root_array, "[0].code") == "BR"
    assert _resolve_json_path(root_array, "$[1].name") == "United States"
    assert resolve_json_path(root_array, "[1].code") == "US"


def test_json_path_bracket_notation_in_extractions():
    resp_json = {
        "data": {
            "items": [
                {"token": "first-token-xyz", "expires_in": 3600},
                {"token": "second-token-abc", "expires_in": 7200}
            ]
        }
    }

    rule = {"source": "body", "property": "data.items[0].token", "variable": "AUTH_TOKEN"}
    var_name, var_val = extract_value(rule, {}, resp_json)
    assert var_name == "AUTH_TOKEN"
    assert var_val == "first-token-xyz"

    rule2 = {"source": "body", "property": "data.items[1].expires_in", "variable": "EXPIRY"}
    var_name2, var_val2 = extract_value(rule2, {}, resp_json)
    assert var_name2 == "EXPIRY"
    assert var_val2 == "7200"


# ── 2. REAL JSON SCHEMA CONTRACT ASSERTIONS ──────────────────────────

def test_contract_assertion_valid_json_schema():
    schema = {
        "type": "object",
        "properties": {
            "user_id": {"type": "integer"},
            "email": {"type": "string"}
        },
        "required": ["user_id", "email"]
    }
    resp_json = {"user_id": 42, "email": "tester@scopeflow.io"}

    assertion = {"source": "contract", "value": schema}
    res = evaluate_assertion(assertion, 200, {}, resp_json, "", 80)
    assert res["success"] is True
    assert res["actual"] == "Schema Match"


def test_contract_assertion_invalid_json_schema():
    schema = {
        "type": "object",
        "properties": {
            "user_id": {"type": "integer"},
            "email": {"type": "string"}
        },
        "required": ["user_id", "email"]
    }
    # Violates schema: email is an integer instead of string
    resp_json = {"user_id": 42, "email": 99999}

    assertion = {"source": "contract", "value": schema}
    res = evaluate_assertion(assertion, 200, {}, resp_json, "", 80)
    assert res["success"] is False
    assert "Schema Mismatch" in res["actual"]
    assert "not of type 'string'" in res["actual"]


def test_contract_assertion_stringified_json_schema():
    schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string"}
        },
        "required": ["status"]
    }
    resp_json = {"status": "ACTIVE"}

    assertion = {"source": "contract", "value": json.dumps(schema)}
    res = evaluate_assertion(assertion, 200, {}, resp_json, "", 80)
    assert res["success"] is True
    assert res["actual"] == "Schema Match"


def test_contract_assertion_fallback_without_schema():
    # Legacy assertions without explicit schema fallback to HTTP < 400
    assertion = {"source": "contract"}
    res_ok = evaluate_assertion(assertion, 200, {}, {}, "", 80)
    assert res_ok["success"] is True
    assert res_ok["actual"] == "Schema Match"

    res_err = evaluate_assertion(assertion, 500, {}, {}, "", 80)
    assert res_err["success"] is False
    assert res_err["actual"] == "Invalid"


# ── 3. CONNECTION POOL & HTTPADAPTER ─────────────────────────────────

def test_session_mounts_http_adapter_pool():
    import requests
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50)
    session.mount('http://', adapter)
    session.mount('https://', adapter)

    mounted_http = session.get_adapter('http://localhost:8000')
    mounted_https = session.get_adapter('https://api.scopeflow.io')

    assert isinstance(mounted_http, HTTPAdapter)
    assert mounted_http._pool_connections == 50
    assert mounted_http._pool_maxsize == 50
    assert mounted_https._pool_connections == 50
    assert mounted_https._pool_maxsize == 50


# ── 4. THREAD-SAFETY OF BUFFER UNDER PARALLEL EXECUTION ──────────────

def test_history_buffer_thread_safety_with_lock():
    buffer = []
    lock = threading.Lock()
    num_threads = 20
    items_per_thread = 50

    def worker(worker_id):
        for i in range(items_per_thread):
            with lock:
                buffer.append({"worker": worker_id, "item": i})

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Total items must match exactly with zero dropped entries
    assert len(buffer) == num_threads * items_per_thread


# ── 5. API EXECUTOR SERVICE COMPATIBILITY & PARITY ───────────────────

def test_api_executor_service_execute_flow_logic_alias():
    assert hasattr(ApiExecutorService, "execute_flow_logic")
    assert callable(getattr(ApiExecutorService, "execute_flow_logic"))
    assert ApiExecutorService.execute_flow_logic == ApiExecutorService.execute


@patch("app.services.feature_service.FeatureService.get_by_id")
@patch("app.services.flow_service.FlowService.list_by_project")
@patch("app.services.executors.api_executor_service.ApiExecutorService.get_merged_variables")
@patch("app.services.executors.api_executor_service.ApiExecutorService.execute_flow_logic")
def test_api_executor_service_execute_feature_group_no_attribute_error(
    mock_execute_logic, mock_get_vars, mock_list_flows, mock_get_feature
):
    mock_feature = MagicMock(id=1, name="Auth Feature", product_id=10)
    mock_get_feature.return_value = mock_feature
    mock_list_flows.return_value = [{"id": 1, "flow_type": "api", "name": "Login API"}]
    mock_get_vars.return_value = {}
    mock_execute_logic.return_value = (1, 0)

    db = MagicMock()
    s, f = ApiExecutorService.execute_feature_group(db, feature_id=1, env_id=1, company_id=1)

    assert s == 1
    assert f == 0
    mock_execute_logic.assert_called_once()

