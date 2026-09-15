import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.playwright_executor_service import PlaywrightExecutorService


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
async def test_heuristic_healer_triggered_with_zero_retries():
    """Valida que o Heuristic Healer é acionado automaticamente mesmo com retries=0 quando um seletor falha."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()
    executor._wait_for_loading_to_finish = AsyncMock()

    mock_page = MagicMock()
    mock_page.wait_for_timeout = AsyncMock()

    # Broken locator fails, healed locator succeeds
    mock_broken_el = MagicMock()
    mock_broken_el.click = AsyncMock(side_effect=Exception("Timeout 5000ms exceeded waiting for locator('#old-btn')"))

    mock_healed_el = MagicMock()
    mock_healed_el.click = AsyncMock()

    mock_broken_locator = MagicMock()
    mock_broken_locator.first = mock_broken_el

    mock_healed_locator = MagicMock()
    mock_healed_locator.first = mock_healed_el

    def locator_side_effect(sel):
        if sel == "#healed-btn >> visible=true":
            return mock_healed_locator
        return mock_broken_locator

    mock_page.locator = MagicMock(side_effect=locator_side_effect)
    executor._page = mock_page

    step = {
        "type": "click",
        "name": "Botão Salvar",
        "properties": {
            "selector": "#old-btn",
            "timeout": 2000,
            "retries": 0  # Default 0 retries
        },
        "_original_properties": {
            "selector": "#old-btn"
        }
    }

    with patch("app.services.heuristic_healer_service.HeuristicHealerService.attempt_heal", new_callable=AsyncMock) as mock_heal:
        mock_heal.return_value = ("#healed-btn >> visible=true", [{"tag": "button", "id": "healed-btn"}])

        result = await executor.execute_step(step, capture_screenshot=False)

        assert result["status"] == 200
        mock_heal.assert_awaited_once()
        assert result["healed_selector"] == "#old-btn:::#healed-btn >> visible=true"
        assert "[HEURISTIC-HEALED -> #healed-btn >> visible=true]" in result["text"]
        assert "Clicked element: #healed-btn >> visible=true" in result["text"]


@pytest.mark.anyio
async def test_ai_fallback_triggered_when_heuristic_fails():
    """Valida que a IA é acionada como fallback caso o heurist-healer não encontre seletor único."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()
    executor._wait_for_loading_to_finish = AsyncMock()

    mock_page = MagicMock()
    mock_page.wait_for_timeout = AsyncMock()

    mock_broken_el = MagicMock()
    mock_broken_el.wait_for = AsyncMock(side_effect=Exception("Timeout 3000ms exceeded waiting for locator('input#broken')"))
    mock_broken_el.evaluate = AsyncMock(side_effect=Exception("Timeout 3000ms exceeded waiting for locator('input#broken')"))
    mock_broken_el.fill = AsyncMock(side_effect=Exception("Timeout 3000ms exceeded waiting for locator('input#broken')"))

    mock_healed_el = MagicMock()
    mock_healed_el.wait_for = AsyncMock()
    mock_healed_el.evaluate = AsyncMock(side_effect=["input", "text"])
    mock_healed_el.fill = AsyncMock()

    mock_broken_loc = MagicMock()
    mock_broken_loc.first = mock_broken_el

    mock_healed_loc = MagicMock()
    mock_healed_loc.first = mock_healed_el

    def locator_side_effect(sel):
        if sel == "input#healed-email":
            return mock_healed_loc
        return mock_broken_loc

    mock_page.locator = MagicMock(side_effect=locator_side_effect)
    executor._page = mock_page

    step = {
        "type": "type",
        "name": "Digitar Email",
        "properties": {
            "selector": "input#broken",
            "value": "teste@flow.com",
            "timeout": 2000
        },
        "_original_properties": {
            "selector": "input#broken"
        }
    }

    candidates = [{"tag": "input", "id": "healed-email", "placeholder": "Email do usuário"}]

    with patch("app.services.heuristic_healer_service.HeuristicHealerService.attempt_heal", new_callable=AsyncMock) as mock_heuristic:
        mock_heuristic.return_value = (None, candidates)

        with patch("app.services.analysis_service.AnalysisService.heal_selector") as mock_ai_heal:
            mock_ai_heal.return_value = "input#healed-email"

            result = await executor.execute_step(step, capture_screenshot=False, user_id=1)

            assert result["status"] == 200
            mock_heuristic.assert_awaited_once()
            mock_ai_heal.assert_called_once()
            assert result["healed_selector"] == "input#broken:::input#healed-email"
            assert "[AI-HEALED -> input#healed-email]" in result["text"]


@pytest.mark.anyio
async def test_self_healing_fails_permanently_if_both_fail():
    """Valida falha permanente caso nem o heurístico nem a IA consigam resolver o seletor."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()
    executor._wait_for_loading_to_finish = AsyncMock()

    mock_page = MagicMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.screenshot = AsyncMock()

    mock_broken_el = MagicMock()
    mock_broken_el.click = AsyncMock(side_effect=Exception("Timeout exceeded waiting for locator('#non-existent')"))
    mock_locator = MagicMock()
    mock_locator.first = mock_broken_el
    mock_page.locator = MagicMock(return_value=mock_locator)
    executor._page = mock_page

    step = {
        "type": "click",
        "name": "Clique Inexistente",
        "properties": {
            "selector": "#non-existent",
            "timeout": 1000
        }
    }

    with patch("app.services.heuristic_healer_service.HeuristicHealerService.attempt_heal", new_callable=AsyncMock) as mock_heuristic:
        mock_heuristic.return_value = (None, [])

        with patch("app.services.analysis_service.AnalysisService.heal_selector") as mock_ai:
            mock_ai.return_value = None

            result = await executor.execute_step(step, capture_screenshot=False)

            assert result["status"] == 500
            mock_heuristic.assert_awaited_once()
            mock_ai.assert_called_once()
            assert result.get("healed_selector") is None
            assert "Timeout exceeded" in result["text"]


@pytest.mark.anyio
async def test_heuristic_healer_works_with_fill_step():
    """Valida que o passo 'fill' é tratado como campo de texto com cura por placeholder."""
    from app.services.heuristic_healer_service import HeuristicHealerService

    mock_page = MagicMock()
    candidates = [
        {
            "tag": "input",
            "id": "new_email_input",
            "className": "form-control",
            "name": "email",
            "placeholder": "Digite seu e-mail",
            "text": ""
        }
    ]
    mock_page.evaluate = AsyncMock(return_value=candidates)

    mock_locator = MagicMock()
    mock_locator.count = AsyncMock(return_value=1)
    mock_page.locator = MagicMock(return_value=mock_locator)

    broken_selector = "input[placeholder='Digite seu e-mail']"
    healed, cand_list = await HeuristicHealerService.attempt_heal(mock_page, broken_selector, "user@test.com", "fill")

    assert healed is not None
    assert "Digite seu e-mail" in healed or "new_email_input" in healed
    assert len(cand_list) == 1


@pytest.mark.anyio
async def test_heuristic_healer_xpath_attribute_parsing():
    """Valida extração de IDs, nomes e texto a partir de seletores XPath quebrados."""
    from app.services.heuristic_healer_service import HeuristicHealerService

    mock_page = MagicMock()
    candidates = [
        {
            "tag": "button",
            "id": "btn-submit-form",
            "className": "btn-primary",
            "name": "submit_btn",
            "placeholder": "",
            "text": "Salvar Dados"
        }
    ]
    mock_page.evaluate = AsyncMock(return_value=candidates)

    mock_locator = MagicMock()
    mock_locator.count = AsyncMock(return_value=1)
    mock_page.locator = MagicMock(return_value=mock_locator)

    xpath_selector = "//button[@id='btn-submit-old' and text()='Salvar Dados']"
    healed, _ = await HeuristicHealerService.attempt_heal(mock_page, xpath_selector, "Salvar Dados", "click")

    assert healed is not None
    assert "btn-submit-form" in healed or "Salvar Dados" in healed
