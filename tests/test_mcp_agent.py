import json
import pytest
from unittest.mock import patch, AsyncMock
from tests.test_projects import get_auth_token_and_admin
from app.models.flow_models import FlowDB

def test_mcp_status_endpoint(client, db_session):
    """Verifies GET /agent/mcp-status returns a structured status object."""
    token = get_auth_token_and_admin(client, db_session, "mcp_status_user")
    headers = {"Authorization": f"Bearer {token}"}

    with patch("app.services.mcp_playwright_service.MCPPlaywrightService.get_mcp_status", new_callable=AsyncMock) as mock_status:
        mock_status.return_value = {
            "status": "connected",
            "server": "mcp-playwright:8931",
            "tools_count": 8,
            "tools": ["browser_navigate", "browser_snapshot", "browser_click"]
        }
        response = client.get("/agent/mcp-status", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "connected"
        assert data["server"] == "mcp-playwright:8931"
        assert len(data["tools"]) == 3

def test_planner_agent_generates_valid_flow(client, db_session):
    """Verifies POST /agent/planner explores page via MCP and returns normalized ScopeFlow canvas."""
    token = get_auth_token_and_admin(client, db_session, "planner_user")
    headers = {"Authorization": f"Bearer {token}"}

    mock_llm_json = {
        "name": "E2E Login Flow",
        "flow_type": "web",
        "nodes": [
            {
                "id": "node_1",
                "type": "custom",
                "position": {"x": 100, "y": 100},
                "data": {
                    "name": "Navegar para Login",
                    "nodeType": "web",
                    "e2eSteps": [
                        {
                            "id": "step_1",
                            "action": "browser",
                            "type": "browser",
                            "properties": {
                                "action": "navigate",
                                "url": "https://example.com/login"
                            }
                        }
                    ]
                }
            },
            {
                "id": "node_2",
                "type": "custom",
                "position": {"x": 400, "y": 100},
                "data": {
                    "name": "Preencher Credenciais",
                    "nodeType": "web",
                    "e2eSteps": [
                        {
                            "id": "step_2",
                            "action": "type",
                            "type": "type",
                            "properties": {
                                "selector": "input[type='email']",
                                "value": "test@example.com"
                            }
                        }
                    ]
                }
            }
        ],
        "edges": [
            {
                "id": "edge_1_2",
                "source": "node_1",
                "target": "node_2"
            }
        ]
    }

    with patch("app.services.mcp_playwright_service.MCPPlaywrightService.navigate_and_snapshot", new_callable=AsyncMock) as mock_nav, \
         patch("app.services.skill_service.SkillService.execute_skill") as mock_skill:
        
        mock_nav.return_value = {
            "url": "https://example.com/login",
            "status": "success",
            "snapshot": "- button 'Entrar' [ref=1]\n- textbox 'E-mail' [ref=2]"
        }
        mock_skill.return_value = f"```json\n{json.dumps(mock_llm_json)}\n```"

        payload = {"url": "https://example.com/login"}
        response = client.post("/agent/planner", headers=headers, json=payload)
        
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "E2E Login Flow"
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1
        assert "cardData" in data
        assert "node_1" in data["cardData"]
        assert "node_2" in data["cardData"]
        assert data["cardData"]["node_1"]["name"] == "Navegar para Login"
        assert data["mcp_status"] == "success"

def test_healer_agent_suggests_and_applies_fix(client, db_session):
    """Verifies POST /agent/healer uses MCP context and applies fix to flow."""
    token = get_auth_token_and_admin(client, db_session, "healer_user")
    headers = {"Authorization": f"Bearer {token}"}

    # Setup a flow in DB
    flow = FlowDB(name="Test Healer Flow", flow_type="web", company_id=1)
    db_session.add(flow)
    db_session.commit()
    db_session.refresh(flow)

    mock_llm_healer_result = {
        "status": "healed",
        "node_id": "node_login",
        "node_title": "Login Step",
        "step": "Clicar Entrar",
        "original_selector": "button#old-login-btn",
        "recommended_selector": "button[data-testid='btn-login']",
        "reason": "O botão agora possui data-testid estável.",
        "reliability_score": "0.95"
    }

    with patch("app.services.mcp_playwright_service.MCPPlaywrightService.navigate_and_snapshot", new_callable=AsyncMock) as mock_nav, \
         patch("app.services.skill_service.SkillService.execute_skill") as mock_skill, \
         patch("app.services.flow_service.FlowService.load") as mock_flow_load, \
         patch("app.services.flow_service.FlowService.apply_healing") as mock_apply:
        
        mock_nav.return_value = {
            "url": "https://example.com",
            "status": "success",
            "snapshot": "button 'Entrar' [data-testid='btn-login']"
        }
        mock_flow_load.return_value = {
            "id": flow.id,
            "name": flow.name,
            "flow_type": "web",
            "cardData": {
                "node_login": {
                    "id": "node_login",
                    "name": "Login Step",
                    "url": "https://example.com",
                    "e2eSteps": [
                        {
                            "id": "s1",
                            "action": "click",
                            "properties": {"selector": "button#old-login-btn"}
                        }
                    ]
                }
            }
        }
        mock_skill.return_value = json.dumps(mock_llm_healer_result)
        mock_apply.return_value = {"success": True}

        payload = {
            "flow_id": flow.id,
            "step_index": 0,
            "error_message": "Timed out waiting for button#old-login-btn",
            "node_id": "node_login",
            "failed_selector": "button#old-login-btn",
            "action_type": "click",
            "target_url": "https://example.com",
            "apply_fix": True
        }

        response = client.post("/agent/healer", headers=headers, json=payload)
        assert response.status_code == 200
        res_data = response.json()
        assert res_data["status"] == "healed"
        assert res_data["recommended_selector"] == "button[data-testid='btn-login']"
        assert res_data["applied"] is True
        mock_apply.assert_called_once()

def test_planner_agent_continues_from_existing_flow(client, db_session):
    """Verifies POST /agent/planner supports start_mode='from_existing' with replay and contextual continuation."""
    token = get_auth_token_and_admin(client, db_session, "planner_continuation_user")
    headers = {"Authorization": f"Bearer {token}"}

    mock_llm_json = {
        "name": "Continuação do Fluxo de Compras",
        "flow_type": "web",
        "nodes": [
            {
                "id": "node_next_1",
                "type": "custom",
                "position": {"x": 700, "y": 100},
                "data": {
                    "name": "Adicionar ao Carrinho",
                    "nodeType": "web",
                    "e2eSteps": [
                        {
                            "id": "step_c1",
                            "action": "click",
                            "type": "click",
                            "properties": {"selector": "button.btn-add-cart"}
                        }
                    ]
                }
            }
        ],
        "edges": [],
        "cardData": {
            "node_next_1": {
                "name": "Adicionar ao Carrinho",
                "nodeType": "web",
                "e2eSteps": [
                    {
                        "id": "step_c1",
                        "action": "click",
                        "type": "click",
                        "properties": {"selector": "button.btn-add-cart"}
                    }
                ]
            }
        }
    }

    with patch("app.services.mcp_playwright_service.MCPPlaywrightService.execute_mcp_tool", new_callable=AsyncMock) as mock_tool, \
         patch("app.services.skill_service.SkillService.execute_skill") as mock_skill:
        
        mock_tool.side_effect = [
            # 1. navigate
            ["Navigated"],
            # 2. type email
            ["Typed"],
            # 3. click login
            ["Clicked"],
            # 4. snapshot
            ["button 'Adicionar ao Carrinho' [class='btn-add-cart']"]
        ]
        mock_skill.return_value = json.dumps(mock_llm_json)

        payload = {
            "url": "https://example.com/login",
            "start_mode": "from_existing",
            "start_node_id": "node_prev_2",
            "instruction": "Adicionar o primeiro produto exibido no catálogo ao carrinho",
            "existing_flow_context": {
                "start_node_id": "node_prev_2",
                "target_url": "https://example.com/login",
                "steps": [
                    {
                        "id": "s1",
                        "type": "type",
                        "name": "Digitar Usuário",
                        "properties": {"selector": "input#email", "value": "test@example.com"}
                    },
                    {
                        "id": "s2",
                        "type": "click",
                        "name": "Clicar em Entrar",
                        "properties": {"selector": "button#submit"}
                    }
                ]
            }
        }

        response = client.post("/agent/planner", headers=headers, json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["start_mode"] == "from_existing"
        assert data["start_node_id"] == "node_prev_2"
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["data"]["name"] == "Adicionar ao Carrinho"
        assert mock_tool.call_count >= 2

def test_planner_agent_never_returns_zero_steps(client, db_session):
    """Verifies that nodes with 0 steps automatically get contextual fallback steps so nodes never have 0 steps."""
    token = get_auth_token_and_admin(client, db_session, "zero_steps_user")
    headers = {"Authorization": f"Bearer {token}"}

    mock_llm_json = {
        "name": "Checkout Flow",
        "flow_type": "web",
        "nodes": [
            {
                "id": "node_click",
                "type": "custom",
                "position": {"x": 100, "y": 100},
                "data": {
                    "name": "Clicar no botão de Finalizar",
                    "nodeType": "web",
                    "e2eSteps": []  # Empty from LLM
                }
            },
            {
                "id": "node_assert",
                "type": "custom",
                "position": {"x": 400, "y": 100},
                "data": {
                    "name": "Verificar se a finalização foi bem-sucedida",
                    "nodeType": "web"  # No e2eSteps key at all
                }
            }
        ],
        "edges": []
    }

    with patch("app.services.mcp_playwright_service.MCPPlaywrightService.navigate_and_snapshot", new_callable=AsyncMock) as mock_nav, \
         patch("app.services.skill_service.SkillService.execute_skill") as mock_skill:
        
        mock_nav.return_value = {"url": "https://sauce-demo.myshopify.com", "status": "connected", "snapshot": "Catalog"}
        mock_skill.return_value = json.dumps(mock_llm_json)

        payload = {"url": "https://sauce-demo.myshopify.com"}
        response = client.post("/agent/planner", headers=headers, json=payload)
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["nodes"]) == 2
        
        # Node 1: Clicar no botão de Finalizar
        n1 = data["nodes"][0]
        assert len(n1["data"]["e2eSteps"]) >= 1
        assert n1["data"]["e2eSteps"][0]["type"] == "click"
        assert "finalizar" in n1["data"]["e2eSteps"][0]["selector"].lower()
        assert len(data["cardData"]["node_click"]["e2eSteps"]) >= 1

        # Node 2: Verificar se a finalização foi bem-sucedida
        n2 = data["nodes"][1]
        assert len(n2["data"]["e2eSteps"]) >= 1
        assert n2["data"]["e2eSteps"][0]["type"] == "assert"
        assert len(data["cardData"]["node_assert"]["e2eSteps"]) >= 1

def test_planner_agent_extracts_steps_from_card_data(client, db_session):
    """Verifies that if LLM returns e2eSteps in cardData, they are normalized into both node.data and cardData."""
    token = get_auth_token_and_admin(client, db_session, "card_data_user")
    headers = {"Authorization": f"Bearer {token}"}

    mock_llm_json = {
        "name": "Search Flow",
        "flow_type": "web",
        "nodes": [
            {
                "id": "node_search",
                "type": "custom",
                "position": {"x": 100, "y": 100},
                "data": {
                    "name": "Buscar Produto",
                    "nodeType": "web"
                }
            }
        ],
        "edges": [],
        "cardData": {
            "node_search": {
                "name": "Buscar Produto",
                "e2eSteps": [
                    {
                        "id": "s1",
                        "type": "fill",
                        "selector": "#search-field",
                        "value": "shirt"
                    }
                ]
            }
        }
    }

    with patch("app.services.mcp_playwright_service.MCPPlaywrightService.navigate_and_snapshot", new_callable=AsyncMock) as mock_nav, \
         patch("app.services.skill_service.SkillService.execute_skill") as mock_skill:
        
        mock_nav.return_value = {"url": "https://sauce-demo.myshopify.com", "status": "connected", "snapshot": "Search"}
        mock_skill.return_value = json.dumps(mock_llm_json)

        payload = {"url": "https://sauce-demo.myshopify.com"}
        response = client.post("/agent/planner", headers=headers, json=payload)
        
        assert response.status_code == 200
        data = response.json()
        n = data["nodes"][0]
        assert len(n["data"]["e2eSteps"]) == 1
        step = n["data"]["e2eSteps"][0]
        assert step["type"] == "type"
        assert step["selector"] == "#search-field"
        assert step["value"] == "shirt"
        assert data["cardData"]["node_search"]["e2eSteps"][0]["selector"] == "#search-field"

def test_planner_agent_fallback_to_direct_playwright_on_mcp_error(client, db_session):
    """Verifies that if MCP snapshot returns an error string (e.g. browser not installed), it falls back to direct Playwright exploration."""
    token = get_auth_token_and_admin(client, db_session, "direct_pw_user")
    headers = {"Authorization": f"Bearer {token}"}

    with patch("app.services.mcp_playwright_service.MCPPlaywrightService.navigate_and_snapshot", new_callable=AsyncMock) as mock_nav, \
         patch("app.services.mcp_playwright_service.MCPPlaywrightService.direct_playwright_snapshot", new_callable=AsyncMock) as mock_direct, \
         patch("app.services.skill_service.SkillService.execute_skill") as mock_skill:
        
        # Simulate MCP returning the error string that was previously causing the problem
        mock_nav.return_value = {
            "status": "connected",
            "snapshot": "### Error\nError: Browser \"chrome-for-testing\" is not installed"
        }
        mock_direct.return_value = {
            "status": "connected",
            "snapshot": "Página: 'Sauce Demo'\n- <a id='customer_login_link'> 'Log in' | seletor sugerido: `#customer_login_link`"
        }
        mock_skill.return_value = json.dumps({
            "name": "Sauce Demo Login",
            "flow_type": "web",
            "nodes": [
                {
                    "id": "n1",
                    "data": {
                        "name": "Clicar em Login",
                        "e2eSteps": [{"type": "click", "selector": "#customer_login_link"}]
                    }
                }
            ]
        })

        payload = {"url": "https://sauce-demo.myshopify.com"}
        response = client.post("/agent/planner", headers=headers, json=payload)
        assert response.status_code == 200
        data = response.json()
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["data"]["e2eSteps"][0]["selector"] == "#customer_login_link"
        mock_direct.assert_called_once()

def test_planner_removes_initial_browser_step_in_from_existing_mode(client, db_session):
    """Verifies that in from_existing mode, if LLM generates a browser navigation as first step, it is removed or converted so flow progress isn't reset."""
    token = get_auth_token_and_admin(client, db_session, "cont_user")
    headers = {"Authorization": f"Bearer {token}"}

    with patch("app.services.mcp_playwright_service.MCPPlaywrightService.direct_playwright_snapshot", new_callable=AsyncMock) as mock_direct, \
         patch("app.services.skill_service.SkillService.execute_skill") as mock_skill:

        mock_direct.return_value = {
            "status": "connected",
            "snapshot": "Página: 'Cart'\n- <button id='checkout-btn'> 'Checkout' | seletor sugerido: `#checkout-btn`"
        }
        mock_skill.return_value = json.dumps({
            "name": "Checkout Flow",
            "flow_type": "web",
            "nodes": [
                {
                    "id": "n_cart",
                    "data": {
                        "name": "Continuar no Carrinho",
                        "e2eSteps": [
                            {"type": "browser", "value": "https://sauce-demo.myshopify.com/"},
                            {"type": "click", "name": "Clicar em Checkout", "properties": {"selector": "#checkout-btn"}}
                        ]
                    }
                }
            ]
        })

        payload = {
            "url": "https://sauce-demo.myshopify.com/",
            "start_mode": "from_existing",
            "existing_flow_context": {
                "steps": [{"type": "click", "selector": "#add-to-cart"}]
            }
        }
        response = client.post("/agent/planner", headers=headers, json=payload)
        assert response.status_code == 200
        data = response.json()
        steps = data["nodes"][0]["data"]["e2eSteps"]
        # The browser step should have been removed, leaving only the checkout click!
        assert len(steps) == 1
        assert steps[0]["type"] == "click"
        assert steps[0]["selector"] == "#checkout-btn"

def test_planner_infers_selectors_and_values_for_incomplete_steps(client, db_session):
    """Verifies that if steps have missing selectors or values, planner infers sensible defaults."""
    token = get_auth_token_and_admin(client, db_session, "infer_user")
    headers = {"Authorization": f"Bearer {token}"}

    with patch("app.services.mcp_playwright_service.MCPPlaywrightService.navigate_and_snapshot", new_callable=AsyncMock) as mock_nav, \
         patch("app.services.skill_service.SkillService.execute_skill") as mock_skill:

        mock_nav.return_value = {
            "status": "connected",
            "snapshot": "Página: 'Login'\n- <input id='email'> 'Email' | seletor sugerido: `#email`"
        }
        mock_skill.return_value = json.dumps({
            "name": "Incomplete Step Flow",
            "flow_type": "web",
            "nodes": [
                {
                    "id": "n_incomplete",
                    "data": {
                        "name": "Formulário",
                        "e2eSteps": [
                            {"type": "click", "name": "Clicar no botão Salvar", "properties": {}},
                            {"type": "type", "name": "Preencher email", "properties": {}},
                            {"type": "assert", "name": "Sucesso", "properties": {}}
                        ]
                    }
                }
            ]
        })

        payload = {"url": "https://example.com/login"}
        response = client.post("/agent/planner", headers=headers, json=payload)
        assert response.status_code == 200
        data = response.json()
        steps = data["nodes"][0]["data"]["e2eSteps"]
        assert len(steps) == 3
        # Click should have inferred selector with text Salvar
        assert "Salvar" in steps[0]["selector"]
        # Type should have default input selector and email value
        assert "input" in steps[1]["selector"]
        assert "teste@exemplo.com" in steps[1]["value"]
        # Assert should have body selector
        assert steps[2]["selector"] == "body"




