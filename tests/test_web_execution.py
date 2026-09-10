import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.models.schedule_models import ScheduleModel
from app.models.api_test_history_models import WebExecutionHistory
from app.schemas.history_schemas import ExecutionHistoryCreate
from app.services.history_service import HistoryService
from app.models.company_models import CompanyDB
from app.services.playwright_executor_service import PlaywrightExecutorService


# =====================================================================
# 1. Testes de Banco & Histórico de Execuções Web (E2E)
# =====================================================================

def test_web_execution_history_save_and_retrieve(db_session):
    """Garante que registros de execução web (E2E) são salvos na tabela correta (WebExecutionHistory)."""
    from app.models.user_models import UserDB
    company = CompanyDB(name="Web QA Co")
    db_session.add(company)
    db_session.commit()

    user = UserDB(username="web_tester", email="web@test.com", hashed_password="pw", company_id=company.id, role="admin", status="active")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    hist_create = ExecutionHistoryCreate(
        batch_id="batch_web_123",
        api_name="Web Click Login Button",
        project_id=1,
        flow_id="15",
        node_id="node_web_1",
        method="E2E_STEP",
        url="http://app.internal/login",
        status_code=200,
        status_text="OK",
        response_time=250,
        execution_type="e2e"
    )

    saved = HistoryService.save(db_session, hist_create, user_id=user.id)
    assert saved is not None
    assert isinstance(saved, WebExecutionHistory)
    assert saved.execution_type == "e2e"
    assert saved.node_id == "node_web_1"
    assert saved.status_code == 200

    # Consulta através do HistoryService.get_all
    results = HistoryService.get_all(
        db=db_session,
        company_id=company.id,
        project_id=1,
        execution_type="e2e",
        batch_id="batch_web_123"
    )
    assert results["total"] >= 1
    assert any(item.batch_id == "batch_web_123" for item in results["items"])


def test_web_save_batch_auto_resolves_from_schedule(db_session):
    """Garante que save_batch detecta automaticamente execution_type='e2e' a partir do schedule.flow_type."""
    sched = ScheduleModel(
        name="Web E2E Regression Run",
        type="flow",
        target_id=20,
        environment_id=1,
        status="active",
        flow_type="e2e",
        company_id=1,
        user_id=1
    )
    db_session.add(sched)
    db_session.commit()
    db_session.refresh(sched)

    item = ExecutionHistoryCreate(
        batch_id=f"batch_sched_{sched.id}",
        api_name="Web Fill Form",
        project_id=1,
        schedule_id=sched.id,
        method="E2E_STEP",
        url="http://app.internal/checkout",
        status_code=200,
        execution_type=None  # Deve ser auto-resolvido para 'e2e'
    )

    HistoryService.save_batch(db_session, [item], user_id=1)

    web_records = db_session.query(WebExecutionHistory).filter(
        WebExecutionHistory.schedule_id == sched.id
    ).all()
    assert len(web_records) == 1
    assert web_records[0].execution_type == "web"
    assert isinstance(web_records[0], WebExecutionHistory)


# =====================================================================
# 2. Testes de Execução de Passos Básicos (PlaywrightExecutorService)
# =====================================================================

@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
async def test_execute_step_wait_selector():
    """Valida execução do passo 'wait_selector'."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_locator = MagicMock()
    mock_el = MagicMock()
    mock_el.wait_for = AsyncMock()
    mock_locator.first = mock_el
    mock_page.locator = MagicMock(return_value=mock_locator)
    executor._page = mock_page

    step = {
        "type": "wait_selector",
        "name": "Aguardar Tabela",
        "properties": {"selector": "#data-grid", "timeout": 4000}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    assert result["status"] == 200
    mock_el.wait_for.assert_awaited_once_with(timeout=4000)
    assert "Element #data-grid is now present" in result["text"]


@pytest.mark.anyio
async def test_execute_step_fixed_wait():
    """Valida execução do passo 'wait' com delay milissegundos."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_page.wait_for_timeout = AsyncMock()
    executor._page = mock_page

    step = {
        "type": "wait",
        "name": "Espera 2s",
        "properties": {"value": 2000}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    assert result["status"] == 200
    mock_page.wait_for_timeout.assert_awaited_once_with(2000)
    assert "Waited for 2000ms" in result["text"]


@pytest.mark.anyio
async def test_execute_step_get_text():
    """Valida execução do passo 'getText' capturando texto do seletor."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_locator = MagicMock()
    mock_el = MagicMock()
    mock_el.inner_text = AsyncMock(return_value="R$ 1.540,00")
    mock_locator.first = mock_el
    mock_page.locator = MagicMock(return_value=mock_locator)
    executor._page = mock_page

    step = {
        "type": "getText",
        "name": "Capturar Saldo",
        "properties": {"selector": ".balance-value", "timeout": 3000}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    assert result["status"] == 200
    assert result["text"] == "R$ 1.540,00"
    mock_el.inner_text.assert_awaited_once_with(timeout=3000)


@pytest.mark.anyio
async def test_execute_step_get_attribute():
    """Valida execução do passo 'getAttribute'."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_locator = MagicMock()
    mock_el = MagicMock()
    mock_el.get_attribute = AsyncMock(return_value="https://cdn.example.com/file.pdf")
    mock_locator.first = mock_el
    mock_page.locator = MagicMock(return_value=mock_locator)
    executor._page = mock_page

    step = {
        "type": "getAttribute",
        "name": "Link do PDF",
        "properties": {"selector": "a#download-btn", "attribute": "href", "timeout": 3000}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    assert result["status"] == 200
    assert result["text"] == "https://cdn.example.com/file.pdf"
    mock_el.get_attribute.assert_awaited_once_with("href", timeout=3000)


# =====================================================================
# 3. Testes de Asserções Web (Asserts)
# =====================================================================

@pytest.mark.anyio
async def test_execute_step_assert_visible():
    """Valida asserção de elemento visível."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_locator = MagicMock()
    mock_el = MagicMock()
    mock_el.wait_for = AsyncMock()
    mock_locator.first = mock_el
    mock_page.locator = MagicMock(return_value=mock_locator)
    executor._page = mock_page

    step = {
        "type": "assert",
        "name": "Verificar Sucesso",
        "properties": {"selector": ".alert-success", "operator": "visible", "timeout": 2000}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    assert result["status"] == 200
    mock_el.wait_for.assert_awaited_once_with(state="visible", timeout=2000)
    assert "Assertion passed: .alert-success is visible" in result["text"]


@pytest.mark.anyio
async def test_execute_step_assert_equals_success_and_failure():
    """Valida asserção de igualdade de texto: sucesso e falha."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_locator = MagicMock()
    mock_el = MagicMock()
    mock_el.inner_text = AsyncMock(return_value="Pedido Confirmado")
    mock_locator.first = mock_el
    mock_page.locator = MagicMock(return_value=mock_locator)
    executor._page = mock_page

    # 1. Caso de Sucesso
    step_ok = {
        "type": "assert",
        "name": "Verificar Título Pedido",
        "properties": {"selector": "h1.order-title", "operator": "equals", "value": "Pedido Confirmado", "timeout": 2000}
    }
    result_ok = await executor.execute_step(step_ok, capture_screenshot=False)
    assert result_ok["status"] == 200
    assert "Assertion passed" in result_ok["text"]

    # 2. Caso de Divergência (Falha)
    step_fail = {
        "type": "assert",
        "name": "Verificar Título Incorreto",
        "properties": {"selector": "h1.order-title", "operator": "equals", "value": "Pedido Cancelado", "timeout": 2000}
    }
    result_fail = await executor.execute_step(step_fail, capture_screenshot=False)
    assert result_fail["status"] == 500
    assert "expected 'Pedido Cancelado', but found 'Pedido Confirmado'" in result_fail["text"]


@pytest.mark.anyio
async def test_execute_step_assert_dialog_message():
    """Valida asserção especial sobre mensagem de diálogo/alerta nativo do navegador."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()
    executor._page = MagicMock()
    executor._last_dialog_message = "Deseja realmente excluir este item?"

    step = {
        "type": "assert",
        "name": "Verificar Mensagem Alerta",
        "properties": {"selector": "dialog.message", "operator": "equals", "value": "Deseja realmente excluir este item?"}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    assert result["status"] == 200
    assert "Assertion passed: modal message equals 'Deseja realmente excluir este item?'" in result["text"]


@pytest.mark.anyio
async def test_execute_step_assert_document_title():
    """Valida asserção sobre o título da aba/documento."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_page.evaluate = AsyncMock(return_value="Painel de Controle - ScopeFlow")
    executor._page = mock_page

    step = {
        "type": "assert",
        "name": "Verificar Título da Página",
        "properties": {"selector": "document.title", "operator": "contains", "value": "ScopeFlow", "timeout": 2000}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    assert result["status"] == 200
    assert "Assertion passed: title contains 'ScopeFlow'" in result["text"]


# =====================================================================
# 4. Testes de Segurança e Recursos Avançados
# =====================================================================

@pytest.mark.anyio
async def test_execute_step_password_masking():
    """Valida que valores digitados em campos de senha têm seu log mascarado com marcadores de proteção."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_locator = MagicMock()
    mock_el = MagicMock()
    mock_el.wait_for = AsyncMock()
    mock_el.evaluate = AsyncMock(side_effect=["input", "password"])
    mock_el.fill = AsyncMock()
    mock_locator.first = mock_el
    mock_page.locator = MagicMock(return_value=mock_locator)
    executor._page = mock_page
    executor._wait_for_loading_to_finish = AsyncMock()

    step = {
        "type": "type",
        "name": "Digitar Senha",
        "properties": {"selector": "input#user_password", "value": "SuperSecret123!", "timeout": 3000}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    assert result["status"] == 200
    mock_el.fill.assert_awaited_once_with("SuperSecret123!", timeout=3000)
    # Garante que o texto de log mascarou a senha
    assert "Typed '••••••' into input#user_password" in result["text"]
    assert "SuperSecret123!" not in result["text"]


@pytest.mark.anyio
async def test_execute_step_iframe_support():
    """Valida direcionamento automático para iframe quando isIframe=True."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    mock_page = MagicMock()
    mock_frame_locator = MagicMock()
    mock_target_locator = MagicMock()
    mock_target_el = MagicMock()
    mock_target_el.click = AsyncMock()
    mock_target_locator.locator = MagicMock(return_value=mock_target_locator)
    mock_target_locator.first = mock_target_el
    mock_frame_locator.locator = MagicMock(return_value=mock_target_locator)
    mock_frame_locator.first = mock_target_locator

    mock_page.frame_locator = MagicMock(return_value=mock_frame_locator)
    mock_page.wait_for_timeout = AsyncMock()
    executor._page = mock_page
    executor._wait_for_loading_to_finish = AsyncMock()

    step = {
        "type": "click",
        "name": "Pagar com Stripe no iframe",
        "properties": {
            "selector": "button#btn-pay",
            "isIframe": True,
            "frameUrl": "https://js.stripe.com/v3/elements-inner-card-xyz.html?param=1",
            "timeout": 4000
        }
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    assert result["status"] == 200
    mock_page.frame_locator.assert_called_once_with("iframe[src*='https://js.stripe.com/v3/elements-inner-card-xyz.html']")
    mock_target_el.click.assert_awaited_once_with(timeout=3000)


@pytest.mark.anyio
async def test_execute_step_switch_tab():
    """Valida troca de aba no navegador via índice numérico ou URL."""
    executor = PlaywrightExecutorService(headless=True)
    executor.start = AsyncMock()

    tab0 = MagicMock()
    tab0.url = "http://app.com/home"
    tab0.bring_to_front = AsyncMock()
    tab0.wait_for_timeout = AsyncMock()
    tab1 = MagicMock()
    tab1.url = "http://app.com/reports"
    tab1.bring_to_front = AsyncMock()
    tab1.wait_for_timeout = AsyncMock()

    mock_context = MagicMock()
    mock_context.pages = [tab0, tab1]
    executor._context = mock_context
    executor._page = tab0

    step = {
        "type": "switch_tab",
        "name": "Alternar para Aba 1",
        "properties": {"value": "1"}
    }

    result = await executor.execute_step(step, capture_screenshot=False)
    assert result["status"] == 200
    assert executor._page == tab1
    tab1.bring_to_front.assert_awaited_once()
    assert "Switched to tab index 1" in result["text"]
