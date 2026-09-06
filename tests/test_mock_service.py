import asyncio
import pytest
from unittest.mock import MagicMock
from app.models.mock_models import MockRuleDB, ServiceMockDB
from app.services.mock_service import MockExecutionEngine


def test_interpolate_template_url_and_path():
    request_data = {
        "path": "/api/v1/checkout/pix",
        "url": "/api/v1/checkout/pix?user=42&env=test",
        "method": "POST",
        "query_params": {"user": "42", "env": "test"},
        "headers": {"Authorization": "Bearer token123"},
        "json_body": {"order_id": 999}
    }
    template = '{"status": "ok", "path": "{{req.path}}", "url": "{{req.url}}", "user": "{{req.query.user}}", "auth": "{{req.headers.Authorization}}", "order": "{{req.body.order_id}}"}'
    interpolated = MockExecutionEngine.interpolate_template(template, request_data)

    assert '"path": "/api/v1/checkout/pix"' in interpolated
    assert '"url": "/api/v1/checkout/pix?user=42&env=test"' in interpolated
    assert '"user": "42"' in interpolated
    assert '"auth": "Bearer token123"' in interpolated
    assert '"order": "999"' in interpolated


def test_match_rule_url_contains_entire_url_without_key():
    # Regra onde a URL inteira contém '400' ou 'pix' sem nenhuma chave
    rule_url = MockRuleDB(
        id=1,
        name="Regra URL Inteira Contem 400",
        method="GET",
        path_pattern="*",
        priority=1,
        is_active=True,
        match_headers=[
            {
                "target": "url",
                "field": "",  # Sem chave!
                "operator": "contains",
                "value": "400"
            }
        ],
        response_status=400,
        response_body='{"error": "Triggered by URL containing 400"}'
    )

    rule_default = MockRuleDB(
        id=2,
        name="Default 200",
        method="GET",
        path_pattern="*",
        priority=5,
        is_active=True,
        match_headers=None,
        response_status=200,
        response_body='{"status": "ok"}'
    )

    rules = [rule_url, rule_default]

    # Caso A: A URL contém 400 no path
    matched_a = MockExecutionEngine.match_rule(
        rules=rules,
        method="GET",
        path="/v1/status/400/details",
        headers={},
        query_params={},
        body_text=""
    )
    assert matched_a is not None
    assert matched_a.id == 1
    assert matched_a.response_status == 400

    # Caso B: A URL contém 400 na query string
    matched_b = MockExecutionEngine.match_rule(
        rules=rules,
        method="GET",
        path="/v1/search",
        headers={},
        query_params={"code": "400"},
        body_text=""
    )
    assert matched_b is not None
    assert matched_b.id == 1
    assert matched_b.response_status == 400

    # Caso C: A URL NÃO contém 400
    matched_c = MockExecutionEngine.match_rule(
        rules=rules,
        method="GET",
        path="/v1/search",
        headers={},
        query_params={"code": "200"},
        body_text=""
    )
    assert matched_c is not None
    assert matched_c.id == 2
    assert matched_c.response_status == 200


def test_process_mock_request_user_defined_response():
    mock_db = MagicMock(spec=ServiceMockDB)
    mock_db.id = 1
    mock_db.name = "Mock Gateway"
    mock_db.slug = "mock-gateway"

    rule = MockRuleDB(
        id=99,
        mock_id=1,
        name="Retorno Customizado",
        method="GET",
        path_pattern="*",
        priority=1,
        is_active=True,
        match_headers=[
            {
                "target": "url",
                "field": "",
                "operator": "contains",
                "value": "relatorio-financeiro"
            }
        ],
        response_status=200,
        response_headers={"Content-Type": "application/json", "X-Custom-Rule": "applied"},
        response_body='{"report": "ready", "path": "{{req.path}}", "full_url": "{{req.url}}"}',
        delay_ms=0
    )
    mock_db.rules = [rule]

    db_session = MagicMock()

    status, headers, body = asyncio.run(MockExecutionEngine.process_mock_request(
        db=db_session,
        mock=mock_db,
        method="GET",
        path="/api/relatorio-financeiro/download",
        headers={},
        query_params={"user": "123"},
        body_text="",
        raw_url="http://localhost:8000/mock/mock-gateway/api/relatorio-financeiro/download?user=123"
    ))

    assert status == 200
    assert headers.get("X-Custom-Rule") == "applied"
    assert '"report": "ready"' in body
    assert '"path": "/api/relatorio-financeiro/download"' in body
    assert "http://localhost:8000/mock/mock-gateway/api/relatorio-financeiro/download?user=123" in body


def test_generate_best_practice_responses_includes_404_and_504():
    from app.services.mock_import_service import MockImportService

    rules = MockImportService.generate_best_practice_responses("Get Users", "GET", "/api/v1/users")
    assert len(rules) == 6

    status_codes = [r["response_status"] for r in rules]
    assert status_codes == [200, 400, 401, 404, 500, 504]

    # Validar regra 404
    rule_404 = next(r for r in rules if r["response_status"] == 404)
    assert rule_404["priority"] == 4
    assert rule_404["match_headers"] == [{"target": "query", "field": "error", "operator": "equals", "value": "404"}]
    assert "404 Not Found" in rule_404["name"]
    assert '"error": "Not Found"' in rule_404["response_body"]
    assert rule_404["delay_ms"] == 50

    # Validar regra 504
    rule_504 = next(r for r in rules if r["response_status"] == 504)
    assert rule_504["priority"] == 6
    assert rule_504["match_headers"] == [{"target": "query", "field": "error", "operator": "equals", "value": "504"}]
    assert "504 Timeout" in rule_504["name"]
    assert '"error": "Gateway Timeout"' in rule_504["response_body"]
    assert rule_504["delay_ms"] == 5000  # Simulação de latência de timeout


def test_mock_engine_matches_imported_404_and_504_rules():
    from app.services.mock_import_service import MockImportService

    imported_raw_rules = MockImportService.generate_best_practice_responses("Get Users", "GET", "/api/v1/users")
    rule_objects = [
        MockRuleDB(
            id=idx + 1,
            mock_id=10,
            name=r["name"],
            method=r["method"],
            path_pattern=r["path_pattern"],
            priority=r["priority"],
            match_headers=r.get("match_headers"),
            response_status=r["response_status"],
            response_body=r["response_body"],
            delay_ms=r["delay_ms"],
            is_active=True
        )
        for idx, r in enumerate(imported_raw_rules)
    ]

    # 1. Requisição normal -> deve retornar 200 OK (default)
    matched_default = MockExecutionEngine.match_rule(
        rules=rule_objects,
        method="GET",
        path="/api/v1/users",
        headers={},
        query_params={},
        body_text=""
    )
    assert matched_default is not None
    assert matched_default.response_status == 200

    # 2. Requisição com ?error=404 -> deve retornar 404 Not Found
    matched_404 = MockExecutionEngine.match_rule(
        rules=rule_objects,
        method="GET",
        path="/api/v1/users",
        headers={},
        query_params={"error": "404"},
        body_text=""
    )
    assert matched_404 is not None
    assert matched_404.response_status == 404
    assert "404 Not Found" in matched_404.name

    # 3. Requisição com ?error=504 -> deve retornar 504 Gateway Timeout
    matched_504 = MockExecutionEngine.match_rule(
        rules=rule_objects,
        method="GET",
        path="/api/v1/users",
        headers={},
        query_params={"error": "504"},
        body_text=""
    )
    assert matched_504 is not None
    assert matched_504.response_status == 504
    assert matched_504.delay_ms == 5000
    assert "504 Timeout" in matched_504.name


def test_echo_body_header_and_params_interpolation():
    request_data = {
        "path": "/api/checkout",
        "url": "/api/checkout?param_one=val1&param_two=100",
        "method": "POST",
        "query_params": {"param_one": "val1", "param_two": "100"},
        "headers": {"Authorization": "Bearer secret-token-xyz", "X-Request-ID": "req-12345"},
        "json_body": {
            "order_id": 9876,
            "is_paid": True,
            "customer": {
                "name": "Maria Silva",
                "email": "maria@empresa.com"
            },
            "items": [
                {"sku": "SKU-001", "qty": 2},
                {"sku": "SKU-002", "qty": 5}
            ]
        },
        "body_text": '{"order_id": 9876}'
    }

    template = """{
        "status": "success",
        "echo_param_query": "{{req.query.param_one}}",
        "echo_param_alias": "{{req.param.param_two}}",
        "echo_header_auth": "{{req.headers.Authorization}}",
        "echo_header_case_insensitive": "{{req.header.x-request-id}}",
        "echo_body_id": {{req.body.order_id}},
        "echo_body_bool": {{req.body.is_paid}},
        "echo_body_nested_name": "{{req.body.customer.name}}",
        "echo_body_nested_email": "{{req.body.customer.email}}",
        "echo_body_array_sku": "{{req.body.items.0.sku}}",
        "echo_body_array_qty": {{req.body.items.1.qty}},
        "echo_full_body": {{req.body}}
    }"""

    interpolated = MockExecutionEngine.interpolate_template(template, request_data)

    assert '"echo_param_query": "val1"' in interpolated
    assert '"echo_param_alias": "100"' in interpolated
    assert '"echo_header_auth": "Bearer secret-token-xyz"' in interpolated
    assert '"echo_header_case_insensitive": "req-12345"' in interpolated
    assert '"echo_body_id": 9876' in interpolated
    assert '"echo_body_bool": true' in interpolated
    assert '"echo_body_nested_name": "Maria Silva"' in interpolated
    assert '"echo_body_nested_email": "maria@empresa.com"' in interpolated
    assert '"echo_body_array_sku": "SKU-001"' in interpolated
    assert '"echo_body_array_qty": 5' in interpolated
    assert '"customer": {' in interpolated


def test_process_mock_request_echoes_in_body_and_headers():
    mock_db = MagicMock(spec=ServiceMockDB)
    mock_db.id = 55
    mock_db.name = "Echo Gateway"
    mock_db.slug = "echo-gateway"

    rule = MockRuleDB(
        id=77,
        mock_id=55,
        name="Echo Rule",
        method="POST",
        path_pattern="/api/v1/payments",
        priority=1,
        is_active=True,
        match_headers=None,
        response_status=201,
        response_headers={
            "Content-Type": "application/json",
            "X-Echo-Auth": "{{req.headers.Authorization}}",
            "X-Echo-Tracking": "{{req.param.track_id}}"
        },
        response_body='{"received_client": "{{req.body.client}}", "received_value": {{req.body.amount}}, "channel": "{{req.query.channel}}"}',
        delay_ms=0
    )
    mock_db.rules = [rule]

    db_session = MagicMock()

    status, headers, body = asyncio.run(MockExecutionEngine.process_mock_request(
        db=db_session,
        mock=mock_db,
        method="POST",
        path="/api/v1/payments",
        headers={"Authorization": "Bearer token-abc-123", "Content-Type": "application/json"},
        query_params={"track_id": "TRK-999", "channel": "mobile-app"},
        body_text='{"client": "TechCorp", "amount": 450.75}'
    ))

    assert status == 201
    assert headers.get("X-Echo-Auth") == "Bearer token-abc-123"
    assert headers.get("X-Echo-Tracking") == "TRK-999"
    assert '"received_client": "TechCorp"' in body
    assert '"received_value": 450.75' in body
    assert '"channel": "mobile-app"' in body


def test_date_formatting_multiple_formats_and_offsets():
    import re
    from datetime import datetime

    request_data = {"json_body": {}}

    template = """{
        "iso": "{{date.iso}}",
        "iso_ms": "{{date.iso_ms}}",
        "br": "{{date.br}}",
        "br_datetime": "{{date.br_datetime}}",
        "ymd": "{{date.ymd}}",
        "epoch": {{date.epoch}},
        "custom_slash": "{{date:YYYY/MM/DD}}",
        "custom_dash_time": "{{date:DD-MM-YYYY HH:mm:ss}}",
        "future_offset": "{{date:+30d:YYYY-MM-DD}}"
    }"""

    interpolated = MockExecutionEngine.interpolate_template(template, request_data)

    # 1. ISO format: 2026-09-05T...Z
    assert re.search(r'"iso": "\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"', interpolated)
    # 2. ISO with MS: 2026-09-05T...123Z
    assert re.search(r'"iso_ms": "\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"', interpolated)
    # 3. BR: 05/09/2026
    assert re.search(r'"br": "\d{2}/\d{2}/\d{4}"', interpolated)
    # 4. BR Datetime: 05/09/2026 23:42:44
    assert re.search(r'"br_datetime": "\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}"', interpolated)
    # 5. YMD: 2026-09-05
    assert re.search(r'"ymd": "\d{4}-\d{2}-\d{2}"', interpolated)
    # 6. Epoch: numbers only
    assert re.search(r'"epoch": \d{10}', interpolated)
    # 7. Custom slash: 2026/09/05
    assert re.search(r'"custom_slash": "\d{4}/\d{2}/\d{2}"', interpolated)
    # 8. Custom dash time: 05-09-2026 23:42:44
    assert re.search(r'"custom_dash_time": "\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2}"', interpolated)
    # 9. Future offset (+30 days)
    assert re.search(r'"future_offset": "\d{4}-\d{2}-\d{2}"', interpolated)


def test_merge_received_json_with_created_date():
    import json

    # Caso 1: Spread do JSON recebido + campo "created"
    request_data = {
        "json_body": {
            "transaction_id": "TX-9988",
            "amount": 1250.00,
            "currency": "BRL"
        },
        "body_text": '{"transaction_id": "TX-9988", "amount": 1250.00, "currency": "BRL"}'
    }

    template_spread = """{
        "...req.body",
        "created": "{{date.br_datetime}}",
        "status": "APPROVED"
    }"""

    interpolated_spread = MockExecutionEngine.interpolate_template(template_spread, request_data)
    data_spread = json.loads(interpolated_spread)

    assert data_spread["transaction_id"] == "TX-9988"
    assert data_spread["amount"] == 1250.00
    assert data_spread["currency"] == "BRL"
    assert data_spread["status"] == "APPROVED"
    assert "/" in data_spread["created"] and ":" in data_spread["created"]

    # Caso 2: req.body_merge com objeto adicional contendo "created"
    template_merge = """{{req.body_merge({
        "created": "{{date.iso}}",
        "processed_by": "FlowMockEngine"
    })}}"""

    interpolated_merge = MockExecutionEngine.interpolate_template(template_merge, request_data)
    data_merge = json.loads(interpolated_merge)

    assert data_merge["transaction_id"] == "TX-9988"
    assert data_merge["amount"] == 1250.00
    assert data_merge["processed_by"] == "FlowMockEngine"
    assert "T" in data_merge["created"] and data_merge["created"].endswith("Z")



