import pytest
from unittest.mock import patch, MagicMock
from app.services.flow_executor_service import FlowExecutorService
from app.models.variable_model import Variable

def test_replace_vars_string_match():
    text = "Hello {{NAME}}"
    vars_dict = {"NAME": "World"}
    result = FlowExecutorService.replace_vars(text, vars_dict)
    assert result == "Hello World"

def test_replace_vars_db_object_match():
    text = "Status: {{STATUS}}"
    class MockVar:
        value = "Active"
    vars_dict = {"STATUS": MockVar()}
    result = FlowExecutorService.replace_vars(text, vars_dict)
    assert result == "Status: Active"

def test_replace_vars_fallback_match():
    text = "Welcome {{USER.NAME}}"
    class MockVar:
        name = "user.name"
        value = "Admin"
    vars_dict = {"OTHER": MockVar()}  # Key doesn't match but object name does
    result = FlowExecutorService.replace_vars(text, vars_dict)
    assert result == "Welcome Admin"

def test_replace_vars_not_found():
    text = "Missing {{VAR}}"
    result = FlowExecutorService.replace_vars(text, {})
    assert result == "Missing {{VAR}}"

def test_is_blocked_domain():
    assert FlowExecutorService.is_blocked_domain("https://google-analytics.com/test") is True
    assert FlowExecutorService.is_blocked_domain("http://doubleclick.net/ads") is True
    assert FlowExecutorService.is_blocked_domain("https://myapi.com/v1") is False

@patch("app.config.settings.TARGET_URL_REPLACEMENT", "my-gateway.test")
def test_sanitize_url_for_docker_rule_a():
    url = "http://localhost:8080/api"
    result = FlowExecutorService.sanitize_url_for_docker(url)
    assert result == "http://my-gateway.test:8080/api"

@patch("app.config.settings.TARGET_URL_REPLACEMENT", None)
@patch("app.config.settings.INTERNAL_GATEWAY_URL", "http://internal-gw")
def test_sanitize_url_for_docker_rule_b():
    url = "http://localhost/api"
    result = FlowExecutorService.sanitize_url_for_docker(url)
    assert result == "http://internal-gw/api"

    url2 = "http://localhost:8000/api"
    result2 = FlowExecutorService.sanitize_url_for_docker(url2)
    assert result2 == "http://internal-gw:8000/api"

@patch("app.services.variable_service.VariableService.get_all")
@patch("app.services.environment_service.EnvironmentService.get_by_project")
def test_get_merged_variables(mock_get_envs, mock_get_vars, db_session):
    # Setup global vars
    g_var = Variable(name="GLOBAL", value="1")
    # Setup env vars
    e_var = Variable(name="ENV", value="2")
    
    # First call is global, second is env specific
    mock_get_vars.side_effect = [[g_var], [e_var]]
    
    result = FlowExecutorService.get_merged_variables(db_session, 1, 10)
    assert "GLOBAL" in result
    assert "ENV" in result
    assert result["GLOBAL"].value == "1"

@patch("app.services.feature_service.FeatureService.get_by_id")
@patch("app.services.flow_service.FlowService.list_by_project")
@patch("app.services.flow_executor_service.FlowExecutorService.get_merged_variables")
@patch("app.services.flow_executor_service.FlowExecutorService.execute_flow_logic")
def test_execute_feature_group_api(mock_execute, mock_get_vars, mock_list_flows, mock_get_feature, db_session):
    """Verifica que apenas flows do tipo 'api' são executados quando flow_type='api'."""
    mock_get_feature.return_value = MagicMock(id=1, name="Feat", product_id=2)
    mock_list_flows.return_value = [
        {"id": 10, "name": "API Flow", "flow_type": "api"},
        {"id": 11, "name": "E2E Flow", "flow_type": "e2e"}
    ]
    mock_get_vars.return_value = {}
    mock_execute.return_value = (5, 1)

    s, f = FlowExecutorService.execute_feature_group(db_session, 1, 1, 1, flow_type='api')

    assert s == 5
    assert f == 1
    # Must execute only the API flow (id=10), not the E2E flow
    mock_execute.assert_called_once()
    called_flow = mock_execute.call_args[0][1]  # second positional arg is flow_meta
    assert called_flow['id'] == 10


@patch("app.services.feature_service.FeatureService.get_by_id")
@patch("app.services.flow_service.FlowService.list_by_project")
@patch("app.services.flow_executor_service.FlowExecutorService.get_merged_variables")
@patch("app.services.flow_executor_service.FlowExecutorService.execute_flow_logic")
def test_execute_feature_group_e2e(mock_execute, mock_get_vars, mock_list_flows, mock_get_feature, db_session):
    """Verifica que apenas flows do tipo 'e2e' são executados quando flow_type='e2e'."""
    mock_get_feature.return_value = MagicMock(id=1, name="Feat", product_id=2)
    mock_list_flows.return_value = [
        {"id": 10, "name": "API Flow", "flow_type": "api"},
        {"id": 11, "name": "E2E Flow", "flow_type": "e2e"}
    ]
    mock_get_vars.return_value = {}
    mock_execute.return_value = (3, 0)

    s, f = FlowExecutorService.execute_feature_group(db_session, 1, 1, 1, flow_type='e2e')

    assert s == 3
    assert f == 0
    mock_execute.assert_called_once()
    called_flow = mock_execute.call_args[0][1]
    assert called_flow['id'] == 11  # the E2E flow


@patch("app.services.feature_service.FeatureService.get_by_id")
@patch("app.services.flow_service.FlowService.list_by_project")
@patch("app.services.flow_executor_service.FlowExecutorService.get_merged_variables")
@patch("app.services.flow_executor_service.FlowExecutorService.execute_flow_logic")
def test_execute_feature_group_no_matching_type_fallback(mock_execute, mock_get_vars, mock_list_flows, mock_get_feature, db_session):
    """Quando não há flows do tipo solicitado, cai no fallback para o mais recente."""
    mock_get_feature.return_value = MagicMock(id=1, name="Feat", product_id=2)
    mock_list_flows.return_value = [
        {"id": 10, "name": "API Flow", "flow_type": "api"}
    ]
    mock_get_vars.return_value = {}
    mock_execute.return_value = (1, 0)

    # Pede e2e, mas só existe api - deve cair no fallback
    s, f = FlowExecutorService.execute_feature_group(db_session, 1, 1, 1, flow_type='e2e')

    # Fallback: executa o único flow disponível
    assert s == 1
    assert f == 0
    mock_execute.assert_called_once()
