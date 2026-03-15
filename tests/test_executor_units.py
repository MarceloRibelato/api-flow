import pytest
from unittest.mock import MagicMock, patch
from app.services.variable_resolver import replace_vars
from app.services.assertion_engine import evaluate_assertion, evaluate_all_assertions
from app.services.extraction_engine import extract_value, resolve_json_path
from app.services.url_utils import sanitize_url_for_docker, is_blocked_domain, ensure_absolute_url, ensure_protocol

# ── VARIABLE RESOLVER TESTS ──────────────────────────────────────────

def test_replace_vars_basic():
    vars = {"USER": "admin", "ID": 123}
    assert replace_vars("Hello {{USER}}", vars) == "Hello admin"
    assert replace_vars("ID is {{ID}}", vars) == "ID is 123"

def test_replace_vars_case_insensitive():
    vars = {"USER": "admin"}
    assert replace_vars("Hello {{user}}", vars) == "Hello admin"
    assert replace_vars("Hello {{uSeR}}", vars) == "Hello admin"

def test_replace_vars_orm_objects():
    # Mocking an ORM object with .name and .value attributes
    mock_var = MagicMock()
    mock_var.name = "API_KEY"
    mock_var.value = "secret-123"
    
    vars = {"SOMETHING": mock_var}
    assert replace_vars("Key: {{API_KEY}}", vars) == "Key: secret-123"

def test_replace_vars_not_found():
    vars = {"A": "1"}
    assert replace_vars("Hello {{B}}", vars) == "Hello {{B}}"


# ── ASSERTION ENGINE TESTS ───────────────────────────────────────────

def test_evaluate_assertion_status():
    assertion = {"source": "statusCode", "operator": "equals", "value": 200}
    res = evaluate_assertion(assertion, 200, {}, {}, "", 100)
    assert res["success"] is True
    assert res["actual"] == "200"

def test_evaluate_assertion_body_json():
    assertion = {"source": "body", "property": "user.name", "operator": "equals", "value": "John"}
    resp_json = {"user": {"name": "John"}}
    res = evaluate_assertion(assertion, 200, {}, resp_json, "", 100)
    assert res["success"] is True

def test_evaluate_assertion_response_time():
    assertion = {"source": "responseTime", "operator": "lt", "value": 500}
    res = evaluate_assertion(assertion, 200, {}, {}, "", 300)
    assert res["success"] is True
    
    res_fail = evaluate_assertion(assertion, 200, {}, {}, "", 600)
    assert res_fail["success"] is False

def test_evaluate_all_assertions():
    assertions = [
        {"source": "statusCode", "operator": "equals", "value": 201},
        {"source": "body", "property": "id", "operator": "exists"}
    ]
    results, all_passed = evaluate_all_assertions(assertions, 201, {}, {"id": 10}, "", 50)
    assert all_passed is True
    assert len(results) == 2


# ── EXTRACTION ENGINE TESTS ──────────────────────────────────────────

def test_resolve_json_path():
    data = {"a": {"b": [{"c": 1}, {"c": 2}]}}
    assert resolve_json_path(data, "a.b.1.c") == 2
    assert resolve_json_path(data, "a.x") is None

def test_extract_value_body():
    rule = {"source": "body", "property": "token", "variable": "AUTH_TOKEN"}
    resp_json = {"token": "abc-123"}
    var_name, var_value = extract_value(rule, {}, resp_json)
    assert var_name == "AUTH_TOKEN"
    assert var_value == "abc-123"

def test_extract_value_header():
    rule = {"source": "header", "property": "Content-Type", "variable": "CTYPE"}
    headers = {"Content-Type": "application/json"}
    var_name, var_value = extract_value(rule, headers, {})
    assert var_name == "CTYPE"
    assert var_value == "application/json"


# ── URL UTILS TESTS ──────────────────────────────────────────────────

@patch('app.config.settings')
def test_sanitize_url_for_docker(mock_settings):
    mock_settings.TARGET_URL_REPLACEMENT = None
    mock_settings.INTERNAL_GATEWAY_URL = "http://gateway:80"
    
    # Test localhost rewrite
    assert sanitize_url_for_docker("http://localhost:3000/test") == "http://gateway:80/test"
    
    # Test backend bypass (8000)
    assert sanitize_url_for_docker("http://localhost:8000/api") == "http://127.0.0.1:8000/api"

@patch('app.config.settings')
def test_ensure_absolute_url(mock_settings):
    mock_settings.API_BASE_URL = "https://api.flow.com"
    
    assert ensure_absolute_url("/login") == "https://api.flow.com/login"
    assert ensure_absolute_url("https://other.com/api") == "https://other.com/api"

def test_is_blocked_domain():
    assert is_blocked_domain("https://google-analytics.com/collect") is True
    assert is_blocked_domain("https://myapi.com/data") is False

def test_ensure_protocol():
    assert ensure_protocol("google.com") == "http://google.com"
    assert ensure_protocol("https://google.com") == "https://google.com"
