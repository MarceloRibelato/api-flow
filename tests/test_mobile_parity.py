from unittest.mock import MagicMock, patch
from app.services.appium_inspector_service import AppiumInspectorService, _active_sessions
from app.services.appium_executor_service import AppiumExecutorService


def test_inspector_get_snapshot_skip_tree():
    mock_driver = MagicMock()
    mock_driver.get_screenshot_as_base64.return_value = "ZmFrZV9pbWc="
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 1920}

    mock_exec = MagicMock()
    mock_exec._driver = mock_driver
    _active_sessions["sess_skip"] = mock_exec
    try:
        snap = AppiumInspectorService.get_snapshot("sess_skip", skip_tree=True)
        assert snap["image_b64"] == "ZmFrZV9pbWc="
        assert snap["tree"] == []
        mock_driver.page_source  # not accessed
    finally:
        _active_sessions.pop("sess_skip", None)


def test_inspector_test_selector_returns_boxes():
    el1 = MagicMock()
    el1.location = {"x": 10, "y": 20}
    el1.size = {"width": 100, "height": 40}
    el2 = MagicMock()
    el2.location = {"x": 15, "y": 80}
    el2.size = {"width": 100, "height": 40}

    mock_exec = MagicMock()
    mock_exec._driver = MagicMock()
    mock_exec._find_elements.return_value = [el1, el2]
    _active_sessions["sess_boxes"] = mock_exec
    try:
        res = AppiumInspectorService.test_selector("sess_boxes", "id=btn")
        assert res["found"] is True
        assert res["count"] == 2
        assert len(res["boxes"]) == 2
        assert res["boxes"][1]["y"] == 80
    finally:
        _active_sessions.pop("sess_boxes", None)


def test_inspector_generate_selectors_validates_real_count():
    mock_driver = MagicMock()
    mock_driver.get_screenshot_as_base64.return_value = "ZmFrZQ=="
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 1920}
    mock_driver.page_source = (
        '<hierarchy><android.widget.Button resource-id="com.app:id/login" '
        'content-desc="Entrar" text="Entrar" bounds="[10,20][210,100]" /></hierarchy>'
    )

    mock_exec = MagicMock()
    mock_exec._driver = mock_driver
    # id= returns 1 match, accessibility_id= returns 2 matches
    def fake_find_elements(sel, timeout_ms=400):
        if sel.startswith("id="):
            return [MagicMock()]
        return [MagicMock(), MagicMock()]

    mock_exec._find_elements.side_effect = fake_find_elements
    _active_sessions["sess_gen"] = mock_exec
    try:
        res = AppiumInspectorService.generate_selectors_for_element("sess_gen", text="Entrar")
        assert res["success"] is True
        assert res["selectors"][0]["count"] == 1
        assert res["selectors"][0]["strategy"] == "id"
    finally:
        _active_sessions.pop("sess_gen", None)


def test_executor_retries_and_variable_interpolation_and_gettext():
    executor = AppiumExecutorService()
    executor._driver = MagicMock()
    executor._driver.get_window_size.return_value = {"width": 1080, "height": 1920}
    executor._variables = {"BTN_ID": "submit_btn", "VAR_OUT": "captured_text"}

    el = MagicMock()
    el.text = "Confirmado"
    # Fail first attempt, succeed on retry
    calls = {"n": 0}
    def fake_find(selector, timeout_ms=5000, step_data=None):
        calls["n"] += 1
        assert selector == "submit_btn"
        if calls["n"] == 1:
            return None
        return el

    executor._find_element = fake_find
    res = executor.execute_step_sync({
        "type": "getText",
        "name": "Get Status",
        "properties": {"selector": "{{BTN_ID}}", "variable": "OUT_TEXT", "retries": 1}
    })
    assert res["status"] == 200
    assert res["retry_count"] == 1
    assert res["extracted_value"] == "Confirmado"
    assert executor._variables["OUT_TEXT"] == "Confirmado"


def test_mobile_device_pool_acquire_waits_and_filters_allowed_devices():
    import threading
    import time
    from app.services.mobile_device_pool_service import MobileDevicePoolService

    dev1 = MagicMock(id=101, label="Pixel 7", is_active=True, product_id=1)
    dev2 = MagicMock(id=102, label="Galaxy S23", is_active=True, product_id=1)

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [dev1, dev2]

    MobileDevicePoolService._locked_devices.clear()
    MobileDevicePoolService._allocated_system_ports.clear()
    MobileDevicePoolService._allocated_mjpeg_ports.clear()

    # Only allow dev2
    info2 = MobileDevicePoolService.acquire_device(db=db, product_id=1, allowed_device_ids=[102], wait_timeout_s=0.1)
    assert info2["device"].id == 102
    assert 102 in MobileDevicePoolService._locked_devices

    # Release dev2 from another thread after 0.15s and verify acquire_device waits and succeeds
    def _release_later():
        time.sleep(0.15)
        MobileDevicePoolService.release_device(info2)

    t = threading.Thread(target=_release_later)
    t.start()
    try:
        reacquired = MobileDevicePoolService.acquire_device(db=db, product_id=1, allowed_device_ids=[102], wait_timeout_s=2.0)
        assert reacquired["device"].id == 102
        MobileDevicePoolService.release_device(reacquired)
    finally:
        t.join()
        MobileDevicePoolService._locked_devices.clear()


def test_flow_executor_mobile_replicate_runs_all_target_devices():
    from unittest.mock import patch
    from app.services.flow_executor_service import FlowExecutorService

    db = MagicMock()
    flow_meta = {
        "id": 55,
        "product_id": 9,
        "project_id": 12,
        "flow_type": "mobile",
        "nodes": [{"id": "n1", "type": "start", "data": {}}],
        "edges": [],
    }
    captured_device_ids = []

    def fake_execute_flow_logic(*args, **kwargs):
        captured_device_ids.append(kwargs.get("device_id"))
        return 1, 0

    with patch("app.services.flow_service.FlowService.load", return_value=flow_meta), \
         patch.object(FlowExecutorService, "get_merged_variables", return_value={}), \
         patch.object(FlowExecutorService, "_resolve_mobile_device_targets", return_value=[101, 102]), \
         patch("app.database.SessionLocal", return_value=MagicMock()), \
         patch.object(FlowExecutorService, "execute_flow_logic", side_effect=fake_execute_flow_logic):
        s, f = FlowExecutorService.execute_flow_by_id(
            db, "55", env_id=1, company_id=1, flow_type="mobile",
            mobile_parallel_mode="replicate", mobile_device_ids=[101, 102]
        )

    assert (s, f) == (2, 0)
    assert sorted(captured_device_ids) == [101, 102]


def test_flow_executor_mobile_distribute_splits_paths_across_devices():
    from unittest.mock import patch
    from app.services.flow_executor_service import FlowExecutorService

    db = MagicMock()
    flow_meta = {
        "id": 77,
        "product_id": 9,
        "flow_type": "mobile",
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "a", "type": "mobileAction", "position": {"x": 100, "y": 0}, "data": {"name": "A"}},
            {"id": "b", "type": "mobileAction", "position": {"x": 100, "y": 100}, "data": {"name": "B"}},
        ],
        "edges": [
            {"source": "s", "target": "a"},
            {"source": "s", "target": "b"},
        ],
    }
    seen_only_paths = []
    seen_batch_ids = []
    orig_exec = FlowExecutorService.execute_flow_logic

    def wrapped_exec(*args, **kwargs):
        if kwargs.get("_only_path_numbers") is not None:
            seen_only_paths.append(kwargs["_only_path_numbers"])
            seen_batch_ids.append(kwargs.get("_base_batch_id"))
            return 1, 0
        return orig_exec(*args, **kwargs)

    with patch.object(FlowExecutorService, "_resolve_mobile_device_targets", return_value=[101, 102]), \
         patch("app.database.SessionLocal", return_value=MagicMock()), \
         patch.object(FlowExecutorService, "execute_flow_logic", side_effect=wrapped_exec):
        s, f = FlowExecutorService.execute_flow_logic(
            db, flow_meta, product_id=9, env_id=1, company_id=1,
            variables_dict={}, flow_type="mobile",
            mobile_parallel_mode="distribute", mobile_device_ids=[101, 102],
            max_concurrency=2
        )

    assert (s, f) == (2, 0)
    assert sorted([tuple(sorted(p)) for p in seen_only_paths]) == [(1,), (2,)]
    assert len(seen_batch_ids) == 2 and seen_batch_ids[0] and len(set(seen_batch_ids)) == 1


def test_stop_execution_endpoint_and_cancel_check():
    from unittest.mock import patch
    from app.routes.execution_routes import stop_execution
    from app.services.flow_executor_service import FlowExecutorService

    fake_schedule = MagicMock(id=88, name="Mobile Run", company_id=1, cron_expression=None, flow_type="mobile")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = fake_schedule
    user = MagicMock(id=1, company_id=1)

    fake_redis = MagicMock()
    fake_redis.get.return_value = "1"

    with patch("redis.from_url", return_value=fake_redis), \
         patch("app.routes.execution_routes.AuditService.log_action"):
        res = stop_execution(88, db=db, current_user=user)
        assert res["status"] == "cancelled"
        assert fake_schedule.status == "cancelled"
        assert fake_schedule.last_run_status == "cancelled"
        fake_redis.set.assert_called_once_with("exec_cancel:88", "1", ex=3600)
        assert FlowExecutorService._is_execution_cancelled(88) is True


def test_appium_connection_error_is_formatted_clearly():
    raw = Exception(
        "HTTPConnectionPool(host='host.docker.internal', port=4723): Max retries exceeded with url: /session "
        "(Caused by NewConnectionError(\"HTTPConnection(host='host.docker.internal', port=4723): "
        "Failed to establish a new connection: [Errno 111] Connection refused\"))"
    )
    msg = AppiumExecutorService._format_appium_connection_error("http://host.docker.internal:4723", raw)
    assert msg is not None
    assert "Appium falhou ou não foi iniciado" in msg
    assert "host.docker.internal:4723" in msg

    other = AppiumExecutorService._format_appium_connection_error("http://host.docker.internal:4723", Exception("Element not found"))
    assert other is None


def test_inspector_start_session_halts_on_remap_breakpoint():
    from unittest.mock import patch

    mock_exec = MagicMock()
    mock_exec.execute_step_sync.return_value = {"status": 200}

    steps = [
        {"type": "tap", "name": "Step 1", "properties": {"selector": "btn1"}},
        {"type": "tap", "name": "Step 2 Remap", "properties": {"selector": "btn2", "remapOnReach": True}},
    ]

    with patch("app.services.appium_inspector_service.AppiumExecutorService", return_value=mock_exec), \
         patch.object(AppiumInspectorService, "get_snapshot", return_value={"session_id": "s_remap", "screenshot": "", "tree": []}):
        res = AppiumInspectorService.start_session(db=MagicMock(), product_id=1, steps=steps, session_id="s_remap")

    assert res["is_paused"] is True
    assert res["paused_step_index"] == 1
    assert res["remap_requested"] is True
    # Only Step 1 was executed; Step 2 (marked remapOnReach) was NOT executed
    assert mock_exec.execute_step_sync.call_count == 1
    _active_sessions.pop("s_remap", None)


def test_type_step_switches_to_input_matching_coordinates_and_handles_password_mask():
    executor = AppiumExecutorService()
    executor._driver = MagicMock()
    executor._driver.get_window_size.return_value = {"width": 1080, "height": 1920}

    # Wrong input returned by generic selector (at y=200), right input is at y=800
    wrong_el = MagicMock(location={"x": 100, "y": 200}, size={"width": 800, "height": 100}, tag_name="android.widget.EditText")
    right_el = MagicMock(location={"x": 100, "y": 750}, size={"width": 800, "height": 100}, tag_name="android.widget.EditText")
    right_el.get_attribute.side_effect = lambda attr: "true" if attr == "password" else "•••••••••"
    right_el.text = "•••••••••"

    executor._find_element = MagicMock(return_value=wrong_el)
    executor._driver.find_elements.return_value = [wrong_el, right_el]
    executor._clear_element_text = MagicMock()

    res = executor.execute_step_sync({
        "type": "type",
        "name": "Type password",
        "properties": {"selector": "//android.widget.EditText", "value": "Teste@123", "x": 400, "y": 800}
    })
    assert res["status"] == 200
    # Must NOT have called _clear_element_text multiple times after typing password bullets
    assert executor._clear_element_text.call_count == 0
    # Must have tapped the right element's center (y=800), not wrong_el (y=250)
    executor._driver.tap.assert_called_with([(500, 800)])


def test_device_pool_auto_allocates_distinct_adb_devices_when_db_pool_empty():
    from unittest.mock import patch
    from app.services.mobile_device_pool_service import MobileDevicePoolService

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []

    MobileDevicePoolService._locked_devices.clear()
    MobileDevicePoolService._allocated_system_ports.clear()
    MobileDevicePoolService._allocated_mjpeg_ports.clear()

    adb_devices = [
        {"serial": "RQ8T302DE0L", "state": "device"},
        {"serial": "emulator-5554", "state": "device"},
    ]
    with patch.object(MobileDevicePoolService, "get_connected_adb_devices", return_value=adb_devices):
        d1 = MobileDevicePoolService.acquire_device(db=db, product_id=1, wait_timeout_s=0.1)
        d2 = MobileDevicePoolService.acquire_device(db=db, product_id=1, wait_timeout_s=0.1)
        try:
            assert {d1["device_name"], d2["device_name"]} == {"RQ8T302DE0L", "emulator-5554"}
            assert d1["system_port"] != d2["system_port"]
            assert d1["mjpeg_port"] != d2["mjpeg_port"]
        finally:
            MobileDevicePoolService.release_device(d1)
            MobileDevicePoolService.release_device(d2)


def test_validate_mobile_device_checks_adb_and_installed_app():
    from unittest.mock import patch
    from app.routes.product_routes import validate_mobile_device, MobileDeviceCreate

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    settings_obj = MagicMock(provider="appium_local", server_url="http://localhost:4723", app_identifier="br.com.alelo.app")
    user = MagicMock(id=1, role="admin")
    payload = MobileDeviceCreate(label="Emulador", platform="android", device_name="emulator-5554", is_active=True)

    with patch("app.services.product_service.ProductService.get_mobile_settings", return_value=settings_obj), \
         patch("requests.get", return_value=MagicMock(status_code=200)), \
         patch("app.services.mobile_device_pool_service.MobileDevicePoolService.is_adb_available", return_value=True), \
         patch("app.services.mobile_device_pool_service.MobileDevicePoolService.get_connected_adb_devices", return_value=[{"serial": "emulator-5554", "state": "device"}]), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")):
        res_missing_app = validate_mobile_device(1, payload, db=db, current_user=user)
        assert res_missing_app["valid"] is False
        assert any("não está instalado" in e for e in res_missing_app["errors"])

    with patch("app.services.product_service.ProductService.get_mobile_settings", return_value=settings_obj), \
         patch("requests.get", return_value=MagicMock(status_code=200)), \
         patch("app.services.mobile_device_pool_service.MobileDevicePoolService.is_adb_available", return_value=True), \
         patch("app.services.mobile_device_pool_service.MobileDevicePoolService.get_connected_adb_devices", return_value=[{"serial": "emulator-5554", "state": "device"}]), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="package:/data/app/base.apk\n")):
        res_ok = validate_mobile_device(1, payload, db=db, current_user=user)
        assert res_ok["valid"] is True
        assert res_ok["errors"] == []


def test_validate_mobile_device_delegates_adb_when_unavailable_in_container():
    from unittest.mock import patch
    from app.routes.product_routes import validate_mobile_device, MobileDeviceCreate

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    settings_obj = MagicMock(provider="appium_local", server_url="http://localhost:4723", app_identifier="br.com.alelo.app")
    user = MagicMock(id=1, role="admin")
    payload = MobileDeviceCreate(label="S20", platform="android", device_name="RQ8T302DE0L", is_active=True)

    with patch("app.services.product_service.ProductService.get_mobile_settings", return_value=settings_obj), \
         patch("requests.get", return_value=MagicMock(status_code=200)), \
         patch("app.services.mobile_device_pool_service.MobileDevicePoolService.is_adb_available", return_value=False):
        res = validate_mobile_device(1, payload, db=db, current_user=user)
        assert res["valid"] is True
        assert res["errors"] == []
        assert any("Appium" in w for w in res["warnings"])

    with patch("app.services.product_service.ProductService.get_mobile_settings", return_value=settings_obj), \
         patch("requests.get", return_value=MagicMock(status_code=200)), \
         patch("app.services.mobile_device_pool_service.MobileDevicePoolService.is_adb_available", return_value=True), \
         patch("app.services.mobile_device_pool_service.MobileDevicePoolService.get_connected_adb_devices", return_value=[{"serial": "USB-DETECTED", "state": "unauthorized"}]):
        res_usb = validate_mobile_device(1, payload, db=db, current_user=user)
        assert res_usb["valid"] is True
        assert res_usb["errors"] == []
        assert any("Appium" in w for w in res_usb["warnings"])


def test_resolve_mobile_device_targets_imports_product_models_and_returns_ids():
    from app.services.flow_executor_service import FlowExecutorService

    db = MagicMock()
    dev1 = MagicMock(id=10, is_active=True)
    dev2 = MagicMock(id=20, is_active=True)
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [dev1, dev2]

    ids = FlowExecutorService._resolve_mobile_device_targets(db, product_id=3, max_concurrency=2, mobile_parallel_mode="distribute")
    assert ids == [10, 20]

    filtered = FlowExecutorService._resolve_mobile_device_targets(db, product_id=3, max_concurrency=2, mobile_parallel_mode="distribute", mobile_device_ids=[20])
    assert filtered == [20]


def test_start_appium_local_activates_app_if_on_launcher():
    from unittest.mock import patch
    from app.services.appium_executor_service import AppiumExecutorService

    svc = AppiumExecutorService()
    svc._provider = "appium_local"
    svc._settings = {
        "server_url": "http://localhost:4723",
        "device_name": "R9XRC02V36A",
        "platform_version": "13",
        "app_identifier": "br.com.alelo.auto.my_fueling_mobile_app",
        "custom_capabilities": "",
    }
    mock_driver = MagicMock()
    mock_driver.current_package = "com.sec.android.app.launcher"

    with patch("appium.webdriver.Remote", return_value=mock_driver), \
         patch.object(AppiumExecutorService, "_resolve_android_launch_activity", return_value=None):
        svc._start_appium_local()

    mock_driver.activate_app.assert_called_once_with("br.com.alelo.auto.my_fueling_mobile_app")


def test_mobile_healing_prefers_edittext_with_hint_over_eye_icon_for_type_step():
    from app.services.mobile_healing_service import MobileHealingService

    xml = """<hierarchy>
        <android.widget.EditText hint="Senha" text="" content-desc="" />
        <android.widget.ImageView content-desc="icon_escondeu_senha" text="" />
    </hierarchy>"""
    healed = MobileHealingService.heal_selector(
        xml,
        "//*[@hint='Senha']",
        {"name": 'Type "Teste@123" in Senha', "action": "type"}
    )
    assert healed == "//*[@hint='Senha']"


def test_type_step_does_not_false_positive_on_empty_password_or_hijack_semantic_selector():
    from app.services.appium_executor_service import AppiumExecutorService

    executor = AppiumExecutorService()
    executor._driver = MagicMock()
    executor._driver.get_window_size.return_value = {"width": 720, "height": 1600}

    # Semantic selector on 720x1600 must NOT be hijacked by 1080x2400 coordinates
    cpf_el = MagicMock(location={"x": 40, "y": 350}, size={"width": 640, "height": 80}, tag_name="android.widget.EditText")
    cpf_el.text = "31584909862"
    cpf_el.get_attribute.side_effect = lambda attr: "31584909862" if attr == "text" else ""
    other_el = MagicMock(location={"x": 40, "y": 520}, size={"width": 640, "height": 80}, tag_name="android.widget.EditText")

    executor._find_element = MagicMock(return_value=cpf_el)
    executor._driver.find_elements.return_value = [cpf_el, other_el]
    executor._clear_element_text = MagicMock()

    res = executor.execute_step_sync({
        "type": "type",
        "name": "Type CPF",
        "properties": {"selector": "//*[@hint='CPF']", "value": "31584909862", "x": 540, "y": 571}
    })
    assert res["status"] == 200
    # Must tap cpf_el center (360, 390), NOT other_el
    executor._driver.tap.assert_called_with([(360, 390)])

    # Empty password field that ignores all typing strategies must fail (no false positive)
    empty_pw = MagicMock(location={"x": 40, "y": 520}, size={"width": 640, "height": 80}, tag_name="android.widget.EditText")
    empty_pw.text = ""
    empty_pw.get_attribute.side_effect = lambda attr: "true" if attr == "password" else ""
    executor._find_element = MagicMock(return_value=empty_pw)
    executor._driver.switch_to.active_element = empty_pw

    res_fail = executor.execute_step_sync({
        "type": "type",
        "name": "Type Senha",
        "properties": {"selector": "//*[@hint='Senha']", "value": "Teste@123"}
    })
    assert res_fail["status"] != 200


def test_pdf_service_generate_e2e_report_supports_mobile_and_web_tables():
    from app.services.pdf_service import PDFService
    from app.models.api_test_history_models import MobileExecutionHistory

    db = MagicMock()
    sched = MagicMock(id=55, name="Fluxo Abastecimento", flow_type="mobile", environment=MagicMock(name="DEV"))
    step1 = MagicMock(
        id=1, schedule_id=55, batch_id="sched_abc_path_1", node_name="Login",
        api_name="Tap em wellcome_button", method="MOBILE", url="", processed_url="",
        status_code=200, response_time=450, error_message=None, response_body="",
        video_url="/videos/sched_abc_path_1/mobile_execution.mp4", feature_name="Abastecimento"
    )
    step2 = MagicMock(
        id=2, schedule_id=55, batch_id="sched_abc_path_2", node_name="Login",
        api_name="Tap em Entrar", method="MOBILE", url="", processed_url="",
        status_code=500, response_time=1200, error_message="Element not found", response_body="",
        video_url=None, feature_name="Abastecimento"
    )

    def query_side_effect(model):
        q = MagicMock()
        if model.__name__ == "ScheduleModel":
            q.filter.return_value.first.return_value = sched
        elif model is MobileExecutionHistory:
            q.filter.return_value.order_by.return_value.all.return_value = [step1, step2]
        else:
            q.filter.return_value.order_by.return_value.all.return_value = []
        return q

    db.query.side_effect = query_side_effect

    pdf_bytes = PDFService.generate_e2e_report(db, 55, batch_id="sched_abc_path_1", execution_type="mobile")
    assert pdf_bytes is not None
    assert bytes(pdf_bytes).startswith(b"%PDF")


def test_flow_executor_conditional_if_else_runs_only_matching_branch():
    import asyncio
    from unittest.mock import AsyncMock, patch
    from app.services.flow_executor_service import FlowExecutorService

    flow_data = {
        "id": 1,
        "nodes": [
            {"id": "start", "type": "startNode", "position": {"x": 0, "y": 0}, "data": {}},
            {
                "id": "cond_1",
                "type": "custom",
                "position": {"x": 100, "y": 0},
                "data": {
                    "name": "Check Message A",
                    "nodeType": "condition",
                    "conditionConfig": {"conditionType": "text_present", "target": "Mensagem A", "timeoutMs": 1000},
                },
            },
            {"id": "node_true", "type": "custom", "position": {"x": 200, "y": 0}, "data": {"name": "Fluxo True", "nodeType": "e2e"}},
            {"id": "node_false", "type": "custom", "position": {"x": 200, "y": 100}, "data": {"name": "Fluxo False", "nodeType": "e2e"}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "cond_1"},
            {"id": "e2", "source": "cond_1", "target": "node_true", "sourceHandle": "true", "label": "TRUE"},
            {"id": "e3", "source": "cond_1", "target": "node_false", "sourceHandle": "false", "label": "FALSE"},
        ],
        "cardData": {
            "cond_1": {"name": "Check Message A", "nodeType": "condition"},
            "node_true": {
                "name": "Fluxo True",
                "nodeType": "e2e",
                "e2eSteps": [{"id": "s_true", "type": "click", "name": "Click True", "properties": {"selector": "#true"}}],
            },
            "node_false": {
                "name": "Fluxo False",
                "nodeType": "e2e",
                "e2eSteps": [{"id": "s_false", "type": "click", "name": "Click False", "properties": {"selector": "#false"}}],
            },
        },
    }

    def run_with_condition(cond_return: bool):
        executed_steps = []

        class FakePlaywright:
            def __init__(self, headless=True):
                self.schedule_id = None
                self.project_id = None

            async def start(self, video_dir=None):
                return None

            async def evaluate_condition(self, cond_cfg):
                assert cond_cfg["target"] == "Mensagem A"
                return cond_return

            async def execute_step(self, step, **kwargs):
                executed_steps.append(step["id"])
                return {"status": 200, "duration": 10, "screenshot": None, "error": None}

            async def stop(self):
                return None

        db = MagicMock()
        with patch("app.services.history_service.HistoryService.save_batch", return_value=[]), \
             patch("app.services.playwright_executor_service.PlaywrightExecutorService", FakePlaywright), \
             patch.object(FlowExecutorService, "_is_execution_cancelled", return_value=False):
            FlowExecutorService.execute_flow_logic(
                db=db,
                flow_meta=flow_data,
                product_id=1,
                env_id=1,
                company_id=1,
                variables_dict={},
                feature_name="Test Feature",
                schedule_id=None,
                flow_type="e2e",
                capture_video=False,
            )
        return executed_steps

    assert run_with_condition(True) == ["s_true"]
    assert run_with_condition(False) == ["s_false"]









