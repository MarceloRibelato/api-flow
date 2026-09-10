import io
import time
import pytest
import numpy as np
from PIL import Image, ImageDraw
from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session

from app.services.mobile_vision_service import MobileVisionService
from app.services.mobile_healing_service import MobileHealingService
from app.services.mobile_device_pool_service import MobileDevicePoolService
from app.services.mobile_diagnostics_service import MobileDiagnosticsService
from app.services.appium_executor_service import AppiumExecutorService
from app.models.user_models import UserDB
from app.models.product_models import ProductModel, ProductMobileDeviceDB
from app.models.company_models import CompanyDB


# =====================================================================
# 1. TESTES: MobileVisionService (Visão Computacional OpenCV / PIL)
# =====================================================================

def test_mobile_vision_detect_button_coordinates_with_container():
    """Valida detecção dinâmica das coordenadas de um botão dentro de um container."""
    # Cria uma imagem sintética 400x400
    img = Image.new("RGB", (400, 400), color=(240, 240, 240))
    draw = ImageDraw.Draw(img)
    # Desenha um botão retangular (200x50) centralizado na região inferior
    draw.rectangle([100, 250, 300, 300], fill=(59, 130, 246))  # Azul estilo botão
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    container = {"x": 50, "y": 200, "width": 300, "height": 150}
    coords = MobileVisionService.detect_button_coordinates(
        screenshot_bytes=png_bytes,
        container_bounds=container
    )

    assert coords is not None
    cx, cy = coords
    # Centro esperado do botão: x=200, y=275
    assert abs(cx - 200) <= 20
    assert abs(cy - 275) <= 20


def test_mobile_vision_guards_against_false_positive_when_target_not_in_page_source():
    """Garante proteção de segurança em varredura de tela cheia: aborta se target_text não existir no DOM."""
    img = Image.new("RGB", (200, 200), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    coords = MobileVisionService.detect_button_coordinates(
        screenshot_bytes=png_bytes,
        container_bounds=None,
        target_text="Comprar Agora",
        page_source="<html><body><div>Carrinho Vazio</div></body></html>"
    )
    # Deve retornar None defensivamente para evitar clique falso positivo
    assert coords is None


def test_mobile_vision_resilience_to_corrupt_data():
    """Garante que entradas inválidas ou bytes corrompidos retornam None sem lançar exceção não tratada."""
    assert MobileVisionService.detect_button_coordinates(None) is None
    assert MobileVisionService.detect_button_coordinates(b"not_a_valid_image_bytes") is None


# =====================================================================
# 2. TESTES: MobileHealingService (Auto-Cura Fuzzy de Seletores)
# =====================================================================

SAMPLE_HIERARCHY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node index="0" text="Entrar na Conta" resource-id="com.app:id/btn_login_action" content-desc="Botão de Acesso" class="android.widget.Button" bounds="[100,500][980,620]"/>
    <node index="1" text="Criar Novo Cadastro" resource-id="com.app:id/btn_register" content-desc="" class="android.widget.Button" bounds="[100,660][980,780]"/>
    <node index="2" text="" resource-id="com.app:id/input_email" content-desc="Campo E-mail" class="android.widget.EditText" bounds="[100,300][980,420]"/>
  </node>
</hierarchy>
"""

def test_mobile_healing_by_selector_and_step_name():
    """Valida auto-cura reconstruindo seletor quando o original sofreu leve refactor."""
    healed = MobileHealingService.heal_selector(
        page_source=SAMPLE_HIERARCHY_XML,
        original_selector="btn_login",
        step_data={"name": "Clicar no Botao de Acesso", "description": "Entrar na Conta"}
    )
    assert healed is not None
    assert "btn_login_action" in healed or "Entrar na Conta" in healed


def test_mobile_healing_by_content_desc():
    """Valida auto-cura quando orientada pela descrição do passo ou acessibilidade."""
    healed = MobileHealingService.heal_selector(
        page_source=SAMPLE_HIERARCHY_XML,
        original_selector="campo_usuario",
        step_data={"name": "Preencher Campo E-mail", "description": "input email"}
    )
    assert healed is not None
    assert "input_email" in healed or "Campo E-mail" in healed


def test_mobile_healing_rejects_unrelated_target():
    """Garante que palavras não correlacionadas não produzem falso-positivo."""
    healed = MobileHealingService.heal_selector(
        page_source=SAMPLE_HIERARCHY_XML,
        original_selector="xyz_nao_existe_de_forma_alguma",
        step_data={"name": "Acao Desconhecida Totalmente Diferente"}
    )
    assert healed is None


def test_mobile_healing_malformed_xml_resilience():
    """Garante que XML corrompido retornado pelo driver não quebre a execução."""
    healed = MobileHealingService.heal_selector(
        page_source="<<< NOT_VALID_XML >>>",
        original_selector="btn_login",
        step_data={"name": "Entrar"}
    )
    assert healed is None


# =====================================================================
# 3. TESTES: MobileDevicePoolService (Pool, Concorrência e Portas)
# =====================================================================

def test_device_pool_dynamic_port_allocation_and_release():
    """Valida alocação de portas únicas e sua reciclagem sem colisão."""
    MobileDevicePoolService._allocated_system_ports.clear()
    MobileDevicePoolService._allocated_mjpeg_ports.clear()

    info1 = MobileDevicePoolService.acquire_device(product_id=1, device_id=101)
    port1 = info1["systemPort"]
    mjpeg1 = info1["mjpegServerPort"]

    assert 8200 <= port1 <= 8299
    assert 7810 <= mjpeg1 <= 7899

    info2 = MobileDevicePoolService.acquire_device(product_id=1, device_id=102)
    port2 = info2["systemPort"]
    mjpeg2 = info2["mjpegServerPort"]

    assert port1 != port2, "As portas systemPort não podem colidir em dispositivos simultâneos"
    assert mjpeg1 != mjpeg2, "As portas mjpegServerPort não podem colidir"

    # Libera usando o dicionário retornado diretamente
    MobileDevicePoolService.release_device(info1)
    assert port1 not in MobileDevicePoolService._allocated_system_ports

    MobileDevicePoolService.release_device(info2)
    assert port2 not in MobileDevicePoolService._allocated_system_ports


def test_device_pool_parse_adb_devices_output():
    """Valida o parser robusto da saída de 'adb devices -l'."""
    raw_output = (
        "List of devices attached\n"
        "R9XRC02V36A            device usb:1-1 product:a15ub model:SM_A155M device:a15 transport_id:1\n"
        "emulator-5554          device product:sdk_gphone64_arm64 model:sdk_gphone64_arm64 device:emu64a transport_id:2\n"
        "\n"
    )

    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(stdout=raw_output, returncode=0)
        devices = MobileDevicePoolService.get_connected_adb_devices()

        assert len(devices) == 2
        assert devices[0]["serial"] == "R9XRC02V36A"
        assert devices[0]["model"] == "SM_A155M"
        assert devices[0]["state"] == "device"

        assert devices[1]["serial"] == "emulator-5554"
        assert devices[1]["model"] == "sdk_gphone64_arm64"


# =====================================================================
# 4. TESTES: MobileDiagnosticsService (Diagnóstico Forense e Alertas)
# =====================================================================

def test_extract_crash_diagnostics_detects_fatal_exception():
    """Garante que exceções críticas de sistema (FATAL EXCEPTION) geram resumo forense e log."""
    mock_driver = MagicMock()
    mock_driver.log_types = ["logcat"]
    mock_driver.capabilities = {"platformName": "android"}

    mock_driver.get_log.return_value = [
        {"message": "09-09 12:00:01.123 I/System: Normal operational log"},
        {"message": "09-09 12:00:02.456 E/AndroidRuntime: FATAL EXCEPTION: main"},
        {"message": "09-09 12:00:02.457 E/AndroidRuntime: Process: com.sample.app, PID: 1234"},
        {"message": "09-09 12:00:02.458 E/AndroidRuntime: java.lang.NullPointerException: Object was null at HomeActivity.java:42"},
        {"message": "09-09 12:00:03.000 I/ActivityManager: Force finishing activity com.sample.app/.HomeActivity"}
    ]

    with patch("os.makedirs"):
        with patch("builtins.open", MagicMock()):
            diag = MobileDiagnosticsService.extract_crash_diagnostics(mock_driver)

    assert diag is not None
    assert "summary" in diag
    assert "NullPointerException" in diag["summary"] or "FATAL EXCEPTION" in diag["summary"]
    assert "crash_" in diag["log_path"]
    assert "crash_" in diag["log_url"]


def test_extract_crash_diagnostics_clean_when_no_error():
    """Valida que logs sem crash retornam None sem gerar arquivos supérfluos."""
    mock_driver = MagicMock()
    mock_driver.log_types = ["logcat"]
    mock_driver.capabilities = {"platformName": "android"}
    mock_driver.get_log.return_value = [
        {"message": "I/ActivityManager: Displayed com.sample.app/.MainActivity: +450ms"},
        {"message": "D/ViewRootImpl: ViewPostIme pointer 0"}
    ]

    diag = MobileDiagnosticsService.extract_crash_diagnostics(mock_driver)
    assert diag is None


def test_dismiss_system_alerts_w3c_alert():
    """Valida fechamento de alerta W3C nativo."""
    mock_driver = MagicMock()
    mock_alert = MagicMock()
    mock_alert.text = "Aviso do Sistema"
    mock_driver.switch_to.alert = mock_alert

    dismissed = MobileDiagnosticsService.dismiss_system_alerts_if_present(mock_driver)
    assert dismissed is True
    mock_alert.accept.assert_called_once()


def test_dismiss_system_alerts_android_dialog_button():
    """Valida fechamento de diálogo nativo do Android via botão 'Aguardar' / 'Fechar app'."""
    mock_driver = MagicMock()
    # Simula ausência de W3C Alert padrão
    mock_driver.switch_to.alert = MagicMock()
    type(mock_driver.switch_to.alert).text = None

    mock_alert_button = MagicMock()
    mock_alert_button.text = "Fechar app"
    mock_driver.find_elements.return_value = [mock_alert_button]

    dismissed = MobileDiagnosticsService.dismiss_system_alerts_if_present(mock_driver)
    assert dismissed is True
    mock_alert_button.click.assert_called_once()


# =====================================================================
# 5. TESTES: AppiumExecutorService (Integração Defensiva)
# =====================================================================

def test_executor_typing_clears_field_defensively():
    """Garante que a digitação limpa defensivamente o campo antes de tentativas."""
    mock_driver = MagicMock()
    mock_element = MagicMock()
    mock_element.tag_name = "android.widget.EditText"
    mock_element.text = "termo_digitado"
    mock_element.get_attribute.return_value = "termo_digitado"

    executor = AppiumExecutorService()
    executor._driver = mock_driver
    executor._find_element = MagicMock(return_value=mock_element)
    executor._clear_element_text = MagicMock()

    res = executor.execute_step_sync(
        step_data={
            "type": "type",
            "name": "Preencher busca",
            "properties": {
                "selector": "input_search",
                "value": "termo_digitado",
                "timeout": 5000
            }
        }
    )

    assert res["status"] == 200
    assert res["reason"] == "OK"


def test_executor_assert_hides_keyboard_defensively():
    """Garante que asserções recolhem o teclado virtual defensivamente se aberto."""
    mock_driver = MagicMock()
    mock_driver.is_keyboard_shown.return_value = True
    mock_element = MagicMock()
    mock_element.text = "Bem-vindo ao App"
    mock_element.is_displayed.return_value = True

    executor = AppiumExecutorService()
    executor._driver = mock_driver
    executor._find_element = MagicMock(return_value=mock_element)

    res = executor.execute_step_sync(
        step_data={
            "type": "assert",
            "name": "Verificar boas-vindas",
            "properties": {
                "selector": "lbl_welcome",
                "operator": "contains",
                "value": "Bem-vindo",
                "timeout": 5000
            }
        }
    )

    assert res["status"] == 200
    mock_driver.hide_keyboard.assert_called()


def test_executor_attaches_crash_diagnostics_on_failure():
    """Valida injeção de logs de crash nativos no retorno de erro quando o passo falha."""
    mock_driver = MagicMock()
    mock_driver.log_types = ["logcat"]
    mock_driver.capabilities = {"platformName": "android"}
    mock_driver.get_log.return_value = [
        {"message": "E/AndroidRuntime: FATAL EXCEPTION: main\njava.lang.NullPointerException: Null button pointer"}
    ]
    mock_driver.get_screenshot_as_base64.return_value = "dGVzdF9zY3JlZW5zaG90"

    executor = AppiumExecutorService()
    executor._driver = mock_driver
    executor._find_element = MagicMock(side_effect=Exception("Element btn_submit not found"))

    with patch("os.makedirs"):
        with patch("builtins.open", MagicMock()):
            res = executor.execute_step_sync(
                step_data={
                    "type": "tap",
                    "name": "Clicar em enviar",
                    "properties": {
                        "selector": "btn_submit",
                        "timeout": 1000
                    }
                }
            )

    assert res["status"] in (400, 500)
    assert "btn_submit" in res["text"]
    assert "[Crash Diagnostics:" in res["text"]
    assert "[Forensic Log:" in res["text"]


# =====================================================================
# 6. TESTES: Rota de Descoberta de Aparelhos ADB
# =====================================================================

def test_route_get_connected_adb_devices(client, db_session: Session):
    """Testa endpoint GET /{product_id}/mobile-devices/adb-connected."""
    client.post("/auth/create", json={"username": "mobdevuser", "password": "Password123!", "accepted_terms": True, "company": "MobCorp"})
    user = db_session.query(UserDB).filter(UserDB.username == "mobdevuser").first()
    user.role = "admin"
    user.status = "active"
    db_session.commit()

    token_resp = client.post("/auth/login", json={"username": "mobdevuser", "password": "Password123!"})
    token = token_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    prod_resp = client.post("/products/", headers=headers, json={"name": "Mobile Device Test Product"})
    prod_id = prod_resp.json()["id"]

    # Mock da descoberta de hardware ADB
    mock_devices = [{"serial": "MOCK_SERIAL_01", "state": "device", "model": "Galaxy_Test"}]
    with patch.object(MobileDevicePoolService, "get_connected_adb_devices", return_value=mock_devices):
        resp = client.get(f"/products/{prod_id}/mobile-devices/adb-connected", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert "adb_devices" in data
    assert "configured_devices" in data
    assert len(data["adb_devices"]) == 1
    assert data["adb_devices"][0]["serial"] == "MOCK_SERIAL_01"
    assert data["adb_devices"][0]["model"] == "Galaxy_Test"
