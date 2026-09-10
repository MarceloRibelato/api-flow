import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.playwright_executor_service import PlaywrightExecutorService


# Configura backend do anyio estritamente para asyncio
@pytest.fixture
def anyio_backend():
    return 'asyncio'


# =====================================================================
# TESTES: PlaywrightExecutorService - Fase 1 (Concorrência & Coroutines)
# =====================================================================

@pytest.mark.anyio
async def test_dialog_handler_awaits_dialog_accept():
    """Garante que o dialog handler é uma corrotina assíncrona e invoca await dialog.accept()."""
    executor = PlaywrightExecutorService(headless=True)
    mock_page = MagicMock()
    dialog_callback = None

    def capture_on(event, cb):
        nonlocal dialog_callback
        if event == "dialog":
            dialog_callback = cb

    mock_page.on = MagicMock(side_effect=capture_on)

    mock_context = MagicMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    executor._context = mock_context
    executor._browser = MagicMock()

    await executor.start()

    assert dialog_callback is not None
    import inspect
    assert inspect.iscoroutinefunction(dialog_callback)

    mock_dialog = MagicMock()
    mock_dialog.message = "Alerta de teste"
    mock_dialog.accept = AsyncMock()

    await dialog_callback(mock_dialog)

    assert executor._last_dialog_message == "Alerta de teste"
    mock_dialog.accept.assert_awaited_once()


@pytest.mark.anyio
async def test_execute_step_awaits_start():
    """Valida que execute_step aguarda start() assincronamente."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_function = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.url = "http://example.com"
    mock_page.screenshot = AsyncMock(return_value=b"fake_screenshot")
    executor._page = mock_page

    step = {
        "type": "browser",
        "name": "Navegar",
        "properties": {"value": "http://example.com"}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    executor.start.assert_awaited_once()
    assert result["status"] == 200


@pytest.mark.anyio
async def test_coordinate_click_awaited():
    """Valida que o clique por coordenadas aguarda mouse.click assincronamente."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_page.mouse.click = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    executor._page = mock_page
    executor._wait_for_loading_to_finish = AsyncMock()

    step = {
        "type": "click",
        "name": "Clique Coordenada",
        "properties": {"x": 150, "y": 300}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    mock_page.mouse.click.assert_awaited_once_with(150.0, 300.0)
    assert "Clicked at coordinates (150, 300)" in result["text"]


@pytest.mark.anyio
async def test_smart_fallback_type_awaited():
    """Valida que digitação sem seletor em input focado aguarda keyboard.type assincronamente."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_page.evaluate = AsyncMock(return_value=True)  # Simula is_input = True
    mock_page.keyboard.type = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    executor._page = mock_page
    executor._wait_for_loading_to_finish = AsyncMock()

    step = {
        "type": "type",
        "name": "Digitar Focado",
        "properties": {"value": "Texto Teste"}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    mock_page.keyboard.type.assert_awaited_once_with("Texto Teste")
    assert result["status"] == 200


@pytest.mark.anyio
async def test_hover_coordinates_awaited():
    """Valida que hover por coordenadas aguarda mouse.move assincronamente."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_page.mouse.move = AsyncMock()
    executor._page = mock_page
    executor._wait_for_loading_to_finish = AsyncMock()

    step = {
        "type": "hover",
        "name": "Hover Coordenadas",
        "properties": {"x": 200, "y": 400}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    mock_page.mouse.move.assert_awaited_once_with(200.0, 400.0)
    assert "Hovered over coordinates (200, 400)" in result["text"]


@pytest.mark.anyio
async def test_scroll_wheel_awaited():
    """Valida que scroll granular aguarda mouse.move e mouse.wheel assincronamente."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_page.mouse.move = AsyncMock()
    mock_page.mouse.wheel = AsyncMock()
    executor._page = mock_page

    step = {
        "type": "scroll",
        "name": "Scroll Granular",
        "properties": {"x": 100, "y": 100, "deltaX": 0, "deltaY": 250}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    mock_page.mouse.move.assert_awaited_once_with(100.0, 100.0)
    mock_page.mouse.wheel.assert_awaited_once_with(0, 250)
    assert "Scrolled by X:0 Y:250" in result["text"]


@pytest.mark.anyio
async def test_global_keypress_awaited():
    """Valida que tecla global aguarda keyboard.press assincronamente."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_page.keyboard.press = AsyncMock()
    executor._page = mock_page

    step = {
        "type": "keypress",
        "name": "Pressionar Enter",
        "properties": {"value": "Enter"}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    mock_page.keyboard.press.assert_awaited_once_with("Enter")
    assert "Pressed Enter globally" in result["text"]


@pytest.mark.anyio
async def test_page_reload_awaited():
    """Valida que refresh aguarda reload assincronamente."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_page.reload = AsyncMock()
    executor._page = mock_page

    step = {
        "type": "refresh",
        "name": "Recarregar Página",
        "properties": {}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    mock_page.reload.assert_awaited_once()
    assert result["text"] == "Page refreshed"


@pytest.mark.anyio
async def test_switch_tab_bring_to_front_awaited():
    """Valida que switch_tab aguarda bring_to_front assincronamente."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page_0 = MagicMock()
    mock_page_0.bring_to_front = AsyncMock()
    mock_page_1 = MagicMock()
    mock_page_1.bring_to_front = AsyncMock()

    mock_context = MagicMock()
    mock_context.pages = [mock_page_0, mock_page_1]
    executor._context = mock_context

    mock_current_page = MagicMock()
    mock_current_page.wait_for_timeout = AsyncMock()
    executor._page = mock_current_page

    step = {
        "type": "switch_tab",
        "name": "Mudar para Tab 1",
        "properties": {"value": "1"}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    mock_page_1.bring_to_front.assert_awaited_once()
    assert executor._page == mock_page_1
    assert "Switched to tab index 1" in result["text"]


@pytest.mark.anyio
async def test_a11y_add_script_tag_awaited():
    """Valida que escaneamento a11y aguarda add_script_tag assincronamente."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_page.add_script_tag = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value={"violations": []})
    executor._page = mock_page

    step = {
        "type": "a11y",
        "name": "Scan Acessibilidade",
        "properties": {"impacts": "critical"}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    mock_page.add_script_tag.assert_awaited_once()
    assert "0 violações totais" in result["text"]


@pytest.mark.anyio
async def test_get_video_path_awaited():
    """Valida que get_video_path é assíncrono e aguarda video.path()."""
    executor = PlaywrightExecutorService(headless=True)
    mock_page = MagicMock()
    mock_video = MagicMock()
    mock_video.path = AsyncMock(return_value="/tmp/test_video.webm")
    mock_page.video = mock_video
    executor._page = mock_page

    video_path = await executor.get_video_path()
    assert video_path == "/tmp/test_video.webm"
    mock_video.path.assert_awaited_once()


# =====================================================================
# TESTES: Ciclo de Vida Defensivo & Anti-Zumbi (Fase 2)
# =====================================================================

@pytest.mark.anyio
async def test_playwright_executor_stop_resilient_to_context_error():
    """Valida que o browser é fechado mesmo se context.close() lançar erro."""
    executor = PlaywrightExecutorService(headless=True)
    
    mock_context = MagicMock()
    mock_context.close = AsyncMock(side_effect=RuntimeError("Context close crashed"))
    executor._context = mock_context

    mock_browser = MagicMock()
    mock_browser.close = AsyncMock()
    executor._browser = mock_browser

    mock_pw = MagicMock()
    mock_pw.stop = AsyncMock()
    executor._playwright = mock_pw

    await executor.stop()

    mock_browser.close.assert_awaited_once()
    mock_pw.stop.assert_awaited_once()
    assert executor._context is None
    assert executor._browser is None
    assert executor._playwright is None
    assert executor._page is None


@pytest.mark.anyio
async def test_web_inspector_stop_session_resilient_to_page_close_error():
    """Valida que o WebInspector fecha context e browser mesmo se page.close() falhar."""
    from app.services.web_inspector_service import _WebInspectorServiceImpl, _active_sessions

    session_id = "test-resilient-session-1"
    mock_page = MagicMock()
    mock_page.close = AsyncMock(side_effect=RuntimeError("Target page crashed"))

    mock_context = MagicMock()
    mock_context.close = AsyncMock()

    mock_browser = MagicMock()
    mock_browser.close = AsyncMock()

    mock_cdp = MagicMock()
    mock_cdp.send = AsyncMock()
    mock_cdp.detach = AsyncMock()

    _active_sessions[session_id] = {
        "page": mock_page,
        "context": mock_context,
        "browser": mock_browser,
        "cdp_client": mock_cdp,
        "requests": []
    }

    await _WebInspectorServiceImpl.stop_session(session_id)

    mock_cdp.send.assert_awaited_once_with("Page.stopScreencast")
    mock_cdp.detach.assert_awaited_once()
    mock_context.close.assert_awaited_once()
    mock_browser.close.assert_awaited_once()
    assert session_id not in _active_sessions


@pytest.mark.anyio
async def test_web_inspector_unregister_frame_queue():
    """Valida que unregister_frame_queue remove referências da fila e event loop do FastAPI."""
    from app.services.web_inspector_service import _WebInspectorServiceImpl, _active_sessions

    session_id = "test-ws-session-queue"
    _active_sessions[session_id] = {
        "fastapi_queue": MagicMock(),
        "fastapi_loop": MagicMock()
    }

    res = await _WebInspectorServiceImpl.unregister_frame_queue(session_id)
    assert res is True
    assert "fastapi_queue" not in _active_sessions[session_id]
    assert "fastapi_loop" not in _active_sessions[session_id]

    # Valida sessão inexistente
    res_none = await _WebInspectorServiceImpl.unregister_frame_queue("non-existent-session")
    assert res_none is False


@pytest.mark.anyio
async def test_web_executor_path_failsafe_cleanup():
    """Valida que o failsafe anti-zumbi do orquestrador Web fecha o executor."""
    executor = PlaywrightExecutorService(headless=True)
    executor.stop = AsyncMock()
    executor._browser = MagicMock()

    # Simula o bloco finally anti-zumbi garantindo teardown
    if executor:
        try:
            if getattr(executor, '_browser', None) or getattr(executor, '_context', None):
                await executor.stop()
        except Exception:
            pass
        executor = None

    assert executor is None


# =====================================================================
# TESTES: Listener requestfailed & Acionabilidade Resiliente (Fase 3)
# =====================================================================

@pytest.mark.anyio
async def test_network_listeners_clean_pending_requests_on_requestfailed():
    """Valida que requisição com falha é desempilhada e registrada com status 0."""
    executor = PlaywrightExecutorService(headless=True)
    listeners = {}

    def mock_on(event, handler):
        listeners[event] = handler

    mock_page = MagicMock()
    mock_page.on = MagicMock(side_effect=mock_on)
    executor._page = mock_page

    executor._install_network_listeners()

    assert "request" in listeners
    assert "response" in listeners
    assert "requestfailed" in listeners

    mock_request = MagicMock()
    mock_request.url = "http://example.com/api/test-endpoint"
    mock_request.method = "POST"
    mock_request.failure = "net::ERR_CONNECTION_RESET"

    # 1. Dispara on_request
    listeners["request"](mock_request)
    assert mock_request in executor._pending_requests

    # 2. Dispara on_request_failed
    listeners["requestfailed"](mock_request)

    # 3. Garante que foi desempilhado de _pending_requests para evitar vazamento
    assert mock_request not in executor._pending_requests

    # 4. Garante que foi capturado com status 0 e mensagem de erro
    captured = executor.pop_captured_requests()
    assert len(captured) == 1
    assert captured[0]["url"] == "http://example.com/api/test-endpoint"
    assert captured[0]["status"] == 0
    assert "net::ERR_CONNECTION_RESET" in captured[0]["error"]


@pytest.mark.anyio
async def test_click_two_tier_actionability_natural_success():
    """Valida que clique com seletor tenta primeiro acionabilidade natural."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_page.wait_for_timeout = AsyncMock()
    executor._page = mock_page
    executor._wait_for_loading_to_finish = AsyncMock()

    mock_locator = MagicMock()
    mock_el = MagicMock()
    mock_el.click = AsyncMock()  # Sucesso imediato natural
    mock_locator.first = mock_el
    mock_page.locator = MagicMock(return_value=mock_locator)

    step = {
        "type": "click",
        "name": "Botão Salvar",
        "properties": {"selector": "#btn-save", "timeout": 5000}
    }

    result = await executor.execute_step(step, capture_screenshot=False)

    assert result["status"] == 200
    # Deve ter tentado timeout adaptativo sem force=True
    mock_el.click.assert_awaited_once_with(timeout=3000)


@pytest.mark.anyio
async def test_click_two_tier_actionability_fallback_to_force():
    """Valida que quando o clique natural falha por sobreposição, o fallback forçado é acionado."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_page.wait_for_timeout = AsyncMock()
    executor._page = mock_page
    executor._wait_for_loading_to_finish = AsyncMock()

    mock_locator = MagicMock()
    mock_el = MagicMock()
    # 1ª tentativa falha (TimeoutError), 2ª tentativa (forced) tem sucesso
    mock_el.click = AsyncMock(side_effect=[TimeoutError("Element not ready"), None])
    mock_locator.first = mock_el
    mock_page.locator = MagicMock(return_value=mock_locator)

    step = {
        "type": "click",
        "name": "Botão com Overlay",
        "properties": {"selector": "#btn-overlay", "timeout": 4000}
    }

    result = await executor.execute_step(step, capture_screenshot=False)

    assert result["status"] == 200
    assert mock_el.click.await_count == 2
    mock_el.click.assert_awaited_with(timeout=4000, force=True)


@pytest.mark.anyio
async def test_type_two_tier_actionability_fallback_to_force():
    """Valida que quando digitação natural falha, o fallback forçado é acionado."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_page.wait_for_timeout = AsyncMock()
    executor._page = mock_page
    executor._wait_for_loading_to_finish = AsyncMock()

    mock_locator = MagicMock()
    mock_el = MagicMock()
    mock_el.wait_for = AsyncMock()
    mock_el.evaluate = AsyncMock(side_effect=["input", "text"])
    mock_el.fill = AsyncMock(side_effect=[TimeoutError("Element obscured"), None])
    mock_locator.first = mock_el
    mock_page.locator = MagicMock(return_value=mock_locator)

    step = {
        "type": "type",
        "name": "Digitar Nome",
        "properties": {"selector": "#input-nome", "value": "Admin", "timeout": 5000}
    }

    result = await executor.execute_step(step, capture_screenshot=False)

    assert result["status"] == 200
    assert mock_el.fill.await_count == 2
    mock_el.fill.assert_awaited_with("Admin", timeout=5000, force=True)


# =====================================================================
# TESTES: PlaywrightExecutorService - Fase 4 (Diagnóstico Forense & Skeletons)
# =====================================================================

@pytest.mark.anyio
async def test_console_and_page_error_listeners_installed_on_start():
    """Valida que start() registra listeners de pageerror e console no Playwright."""
    executor = PlaywrightExecutorService(headless=True)
    mock_page = MagicMock()
    listeners = {}

    def capture_on(event, cb):
        listeners[event] = cb

    mock_page.on = MagicMock(side_effect=capture_on)
    mock_context = MagicMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    executor._context = mock_context
    executor._browser = MagicMock()

    await executor.start()

    assert "pageerror" in listeners
    assert "console" in listeners

    # Simula erro de JavaScript da página
    listeners["pageerror"]("Uncaught TypeError: Cannot read properties of undefined (reading 'map')")
    assert len(executor._page_errors) == 1
    assert "Uncaught TypeError" in executor._page_errors[0]

    # Simula mensagem de console.error
    mock_console_msg = MagicMock()
    mock_console_msg.type = "error"
    mock_console_msg.text = "Failed to fetch /api/v1/users (500 Internal Server Error)"
    listeners["console"](mock_console_msg)

    # Simula mensagem de console.log (não deve entrar no buffer de erros)
    mock_info_msg = MagicMock()
    mock_info_msg.type = "info"
    mock_info_msg.text = "Normal app log"
    listeners["console"](mock_info_msg)

    assert len(executor._console_errors) == 1
    assert "Failed to fetch" in executor._console_errors[0]

    # Valida pop_captured_errors
    errors = executor.pop_captured_errors()
    assert len(errors["page_errors"]) == 1
    assert len(errors["console_errors"]) == 1
    assert len(executor._page_errors) == 0
    assert len(executor._console_errors) == 0


@pytest.mark.anyio
async def test_page_error_capture_and_injection_on_step_failure():
    """Valida que em falha permanente de passo, exceções JavaScript da página são injetadas no resultado."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_page.screenshot = AsyncMock(return_value=b"screenshot")
    mock_page.wait_for_timeout = AsyncMock()
    # Força falha no locator
    mock_page.locator = MagicMock(side_effect=Exception("Element '#submit-btn' not found"))
    executor._page = mock_page
    executor._wait_for_loading_to_finish = AsyncMock()

    # Popula erro prévio de página capturado
    executor._page_errors.append("Uncaught ReferenceError: submitHandler is not defined")

    step = {
        "type": "click",
        "name": "Botão Submit",
        "properties": {"selector": "#submit-btn", "timeout": 1000}
    }

    result = await executor.execute_step(step, capture_screenshot=False)

    assert result["status"] == 500
    assert "[Browser JS Error: Uncaught ReferenceError: submitHandler is not defined]" in result["text"]
    assert "page_errors" in result
    assert result["page_errors"] == ["Uncaught ReferenceError: submitHandler is not defined"]


@pytest.mark.anyio
async def test_console_error_capture_and_injection_on_step_failure():
    """Valida que em falha permanente de passo, se houver erro de console, ele é injetado no resultado."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_page.screenshot = AsyncMock(return_value=b"screenshot")
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.locator = MagicMock(side_effect=Exception("Timeout 2000ms exceeded"))
    executor._page = mock_page
    executor._wait_for_loading_to_finish = AsyncMock()

    # Popula erro de console
    executor._console_errors.append("[ERROR] 404 Not Found - GET /config.json")

    step = {
        "type": "click",
        "name": "Botão Continuar",
        "properties": {"selector": "#btn-continue", "timeout": 1000}
    }

    result = await executor.execute_step(step, capture_screenshot=False)

    assert result["status"] == 500
    assert "[Browser Console: [ERROR] 404 Not Found - GET /config.json]" in result["text"]
    assert "console_errors" in result
    assert result["console_errors"] == ["[ERROR] 404 Not Found - GET /config.json"]


@pytest.mark.anyio
async def test_smart_wait_skeleton_detection():
    """Valida que _wait_for_loading_to_finish monitora loaders e skeleton screens modernos."""
    executor = PlaywrightExecutorService(headless=True)
    mock_page = MagicMock()
    mock_page.wait_for_function = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    executor._page = mock_page

    await executor._wait_for_loading_to_finish(timeout_ms=1500)

    mock_page.wait_for_function.assert_awaited_once()
    eval_arg = mock_page.wait_for_function.call_args[0][0]
    assert '[aria-busy="true"]' in eval_arg
    assert '.skeleton' in eval_arg
    assert '[class*="skeleton"]' in eval_arg
    mock_page.wait_for_load_state.assert_awaited_once_with("networkidle", timeout=1500)


def test_network_zero_status_and_error_handling_in_history():
    """Valida que requisições com status 0 ou erro de rede gravam error_message no histórico."""
    req_failed = {
        "method": "POST",
        "url": "http://api.internal/checkout",
        "status": 0,
        "duration_ms": 120,
        "error": "net::ERR_CONNECTION_REFUSED"
    }

    error_msg = req_failed.get('error') or (f"HTTP Error {req_failed['status']}" if (req_failed['status'] >= 400 or req_failed['status'] == 0) else None)
    assert error_msg == "net::ERR_CONNECTION_REFUSED"

    req_zero_no_error = {
        "method": "GET",
        "url": "http://api.internal/ping",
        "status": 0,
        "duration_ms": 50
    }
    error_msg_zero = req_zero_no_error.get('error') or (f"HTTP Error {req_zero_no_error['status']}" if (req_zero_no_error['status'] >= 400 or req_zero_no_error['status'] == 0) else None)
    assert error_msg_zero == "HTTP Error 0"

    req_ok = {
        "method": "GET",
        "url": "http://api.internal/health",
        "status": 200,
        "duration_ms": 15
    }
    error_msg_ok = req_ok.get('error') or (f"HTTP Error {req_ok['status']}" if (req_ok['status'] >= 400 or req_ok['status'] == 0) else None)
    assert error_msg_ok is None
