import logging
import json
import time
import os
import re
import uuid
import base64

logger = logging.getLogger(__name__)


class AppiumExecutorService:
    """
    Executes mobile steps via Appium (Local/BrowserStack/SauceLabs)
    or Playwright ADB (experimental).

    Settings are loaded from the `product_mobile_settings` table,
    accessed by passing a `db` session and `product_id` into `start()`.
    """

    def __init__(self):
        self._driver = None
        self._captured_requests = []
        self._provider = None
        self._settings = {}
        self._video_dir = None
        self._is_recording = False
        self._is_first_step = True
        self._acquired_device_info = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, video_dir: str = None, db=None, product_id: int = None, device_id: int = None):
        import asyncio
        return await asyncio.to_thread(self.start_sync, video_dir, db, product_id, device_id)

    async def execute_step(self, step_data: dict, capture_screenshot: bool = False, db=None, user_id=None) -> dict:
        import asyncio
        return await asyncio.to_thread(self.execute_step_sync, step_data, capture_screenshot, db, user_id)

    async def close(self):
        import asyncio
        return await asyncio.to_thread(self.close_sync)

    async def stop(self, trace_path=None):
        return await self.close()

    async def stop_screen_recording(self):
        import asyncio
        return await asyncio.to_thread(self._stop_screen_recording)

    async def start_screen_recording(self, video_dir: str = None):
        import asyncio
        if video_dir:
            self._video_dir = video_dir
        return await asyncio.to_thread(self._start_screen_recording)

    async def restart(self, package_name: str = None):
        import asyncio
        return await asyncio.to_thread(self.restart_app, package_name)

    def _get_package_name(self) -> str | None:
        app_id = self._settings.get("app_identifier", "")
        if app_id:
            if "/" in app_id:
                return app_id.split("/", 1)[0]
            if not app_id.lower().endswith(".apk") and "\\" not in app_id:
                return app_id
        if self._driver:
            try:
                pkg = self._driver.capabilities.get("appPackage") or getattr(self._driver, "current_package", None)
                if pkg:
                    return pkg
            except Exception:
                pass
        return None

    def restart_app(self, package_name: str = None):
        """Reinicia o aplicativo para a tela inicial utilizando terminate_app e activate_app."""
        if not self._driver:
            return
        pkg = package_name or self._get_package_name()
        if not pkg:
            logger.debug("📱 [Appium] No package name available to restart app.")
            return

        try:
            try:
                self._driver.hide_keyboard()
            except Exception:
                pass
            logger.info(f"📱 [Appium] Restarting app '{pkg}' to ensure clean initial state...")
            try:
                self._driver.terminate_app(pkg)
            except Exception as te:
                logger.debug(f"terminate_app: {te}")
            time.sleep(1)
            try:
                self._driver.activate_app(pkg)
            except Exception as ae:
                logger.debug(f"activate_app: {ae}")
            self._is_first_step = True
            time.sleep(2)
            logger.info(f"📱 [Appium] App '{pkg}' restarted successfully.")
        except Exception as err:
            logger.warning(f"⚠️ [Appium] Could not restart app '{pkg}': {err}")

    def start_sync(self, video_dir: str = None, db=None, product_id: int = None, device_id: int = None):
        """Inicializa o driver mobile com as configurações do produto e pool de dispositivos."""
        try:
            self._is_first_step = True
            # Load settings from DB if available
            if db and product_id:
                from app.services.product_service import ProductService
                from app.services.mobile_device_pool_service import MobileDevicePoolService

                settings_obj = ProductService.get_mobile_settings(db, product_id)
                self._settings = {
                    "provider": settings_obj.provider or "appium_local",
                    "server_url": settings_obj.server_url or "http://localhost:4723",
                    "auth_user": settings_obj.auth_user or "",
                    "auth_token": settings_obj.auth_token or "",
                    "device_name": settings_obj.device_name or "Android Emulator",
                    "platform_version": settings_obj.platform_version or "",
                    "app_identifier": settings_obj.app_identifier or "",
                    "custom_capabilities": getattr(settings_obj, "custom_capabilities", None) or "",
                }

                # Acquire device from pool and reserve dedicated ports
                try:
                    self._acquired_device_info = MobileDevicePoolService.acquire_device(
                        db=db,
                        product_id=product_id,
                        device_id=device_id
                    )
                    pool_dev = self._acquired_device_info.get("device")
                    if pool_dev:
                        if pool_dev.device_name:
                            self._settings["device_name"] = pool_dev.device_name
                        if pool_dev.platform_version:
                            self._settings["platform_version"] = pool_dev.platform_version
                        if pool_dev.provider_override:
                            self._settings["provider"] = pool_dev.provider_override
                        if pool_dev.server_url:
                            self._settings["server_url"] = pool_dev.server_url
                        if pool_dev.auth_user:
                            self._settings["auth_user"] = pool_dev.auth_user
                        if pool_dev.auth_token:
                            self._settings["auth_token"] = pool_dev.auth_token
                        if pool_dev.app_identifier:
                            self._settings["app_identifier"] = pool_dev.app_identifier
                        if pool_dev.custom_capabilities:
                            self._settings["custom_capabilities"] = pool_dev.custom_capabilities
                except Exception as pool_err:
                    logger.warning(f"⚠️ [Device Pool] acquire_device notice: {pool_err}")

                self._provider = self._settings["provider"]
                logger.info(f"📱 [Appium] Loaded settings for product {product_id}: "
                            f"provider={self._provider}, device={self._settings['device_name']}")
            else:
                # Fallback defaults
                self._provider = "appium_local"
                self._settings = {
                    "server_url": "http://localhost:4723",
                    "device_name": "Android Emulator",
                    "platform_version": "",
                    "app_identifier": "",
                    "custom_capabilities": "",
                }
                logger.warning("📱 [Appium] No DB/product_id passed — using fallback defaults.")

            self._video_dir = video_dir

            # BrowserStack or SauceLabs: attach credentials to URL
            if self._provider in ("browserstack", "saucelabs"):
                self._start_cloud_driver()
            elif self._provider == "playwright_adb":
                self._start_playwright_adb()
            else:
                self._start_appium_local()

            if self._video_dir:
                self._start_screen_recording()

        except Exception as e:
            logger.error(f"📱 [Appium] Failed to start executor: {e}")
            raise

    def _apply_custom_capabilities(self, options):
        """Mescla capabilities customizadas definidas pelo usuário na sessão Appium."""
        custom_caps = self._settings.get("custom_capabilities")
        if not custom_caps:
            return
        
        parsed = None
        if isinstance(custom_caps, dict):
            parsed = custom_caps
        elif isinstance(custom_caps, str):
            custom_caps = custom_caps.strip()
            if custom_caps:
                try:
                    parsed = json.loads(custom_caps)
                except Exception as e:
                    logger.warning(f"⚠️ [Appium] Failed to parse custom_capabilities JSON string: {e}")
        
        if isinstance(parsed, dict):
            for cap_key, cap_val in parsed.items():
                try:
                    options.set_capability(cap_key, cap_val)
                    logger.info(f"📱 [Appium] Applied custom capability '{cap_key}': {cap_val}")
                except Exception as cap_err:
                    logger.warning(f"⚠️ [Appium] Failed to set custom capability '{cap_key}': {cap_err}")

    def _start_appium_local(self):
        """Conecta ao servidor Appium local via webdriver.Remote."""
        try:
            from appium import webdriver as appium_webdriver
            from appium.options.common.base import AppiumOptions

            options = AppiumOptions()
            configured_platform = str(self._settings.get("platform_name", "")).lower()
            custom_caps_str = str(self._settings.get("custom_capabilities", "")).lower()
            is_ios = "ios" in configured_platform or "xcuitest" in custom_caps_str or "bundleid" in custom_caps_str

            if is_ios:
                options.platform_name = "iOS"
                options.set_capability("appium:automationName", "XCUITest")
            else:
                options.platform_name = "Android"
                options.set_capability("appium:automationName", "UiAutomator2")

            options.set_capability("appium:deviceName", self._settings["device_name"])
            if self._settings.get("platform_version"):
                options.set_capability("appium:platformVersion", self._settings["platform_version"])
            if self._settings.get("app_identifier"):
                app_id = self._settings["app_identifier"]
                # Detect if it's an app file path (.apk/.ipa/.app) or an installed package/bundleId
                is_app_file = (
                    app_id.lower().endswith(('.apk', '.ipa', '.app')) or
                    '\\' in app_id or
                    ('/' in app_id and not app_id.startswith('com.') and not app_id.startswith('br.') and not app_id.startswith('io.'))
                )
                if is_app_file:
                    if app_id.startswith('C:\\') or app_id.startswith('c:\\'):
                        logger.warning(f"📱 [Appium] App path '{app_id}' is a Windows local path. "
                                       "Ensure Appium server runs on the host (not inside Docker).")
                    options.set_capability("appium:app", app_id)
                    logger.info(f"📱 [Appium] Using app install path: {app_id}")
                else:
                    if is_ios:
                        options.set_capability("appium:bundleId", app_id)
                        logger.info(f"📱 [Appium] Using iOS bundleId: {app_id}")
                    else:
                        if '/' in app_id:
                            pkg, activity = app_id.split('/', 1)
                            options.set_capability("appium:appPackage", pkg)
                            options.set_capability("appium:appActivity", activity)
                            options.set_capability("appium:appWaitActivity", "*")
                            logger.info(f"📱 [Appium] Using package: {pkg} and activity: {activity}")
                        else:
                            options.set_capability("appium:appPackage", app_id)
                            options.set_capability("appium:appWaitActivity", "*")
            # Ensure physical/emulator device screen is awake, unlocked, and stays on
            if not is_ios:
                try:
                    dev_name = self._settings.get("device_name")
                    dev_arg = f"-s {dev_name}" if dev_name and dev_name != "Android Emulator" else ""
                    import subprocess
                    subprocess.run(
                        f"adb {dev_arg} shell 'svc power stayon true; input keyevent 224; wm dismiss-keyguard' 2>/dev/null",
                        shell=True, timeout=5
                    )
                    logger.info(f"📱 [Appium] Sent ADB wake, dismiss-keyguard and stayon commands to device ({dev_name or 'default'})")
                except Exception as adb_err:
                    logger.debug(f"Device wake via ADB notice: {adb_err}")

            options.set_capability("appium:noReset", True)
            options.set_capability("appium:autoGrantPermissions", True)
            options.set_capability("appium:newCommandTimeout", 3600)
            options.set_capability("appium:ignoreUnimportantViews", True)
            options.set_capability("appium:disableWindowAnimation", True)
            options.set_capability("appium:unicodeKeyboard", True)
            options.set_capability("appium:resetKeyboard", True)
            options.set_capability("appium:waitForIdleTimeout", 0)

            # Apply dynamic ports from Device Pool if allocated
            if getattr(self, '_acquired_device_info', None) and not is_ios:
                sys_port = self._acquired_device_info.get("system_port")
                if sys_port:
                    options.set_capability("appium:systemPort", sys_port)
                    logger.info(f"🔌 [Appium] Injected dynamic systemPort={sys_port}")
                mjpeg_port = self._acquired_device_info.get("mjpeg_port")
                if mjpeg_port:
                    options.set_capability("appium:mjpegServerPort", mjpeg_port)
                    logger.info(f"🔌 [Appium] Injected dynamic mjpegServerPort={mjpeg_port}")

            # Apply custom capabilities (overrides default options if specified)
            self._apply_custom_capabilities(options)

            server_url = self._settings["server_url"]
            
            # Automatically route localhost to host machine when running in Docker
            if "localhost" in server_url or "127.0.0.1" in server_url:
                server_url = server_url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
                logger.info("📱 [Appium] Auto-rewriting localhost to host.docker.internal for Docker networking")

            self._driver = appium_webdriver.Remote(
                command_executor=server_url,
                options=options
            )
            self._driver.implicitly_wait(0)
            try:
                self._driver.update_settings({"waitForIdleTimeout": 0})
            except Exception:
                pass
            logger.info(f"📱 [Appium] Connected to local server at {server_url}")

        except ImportError:
            logger.error("📱 [Appium] appium-python-client not installed. "
                         "Add 'Appium-Python-Client' to requirements.txt")
            raise RuntimeError("Appium-Python-Client not installed")
        except Exception as e:
            logger.error(f"📱 [Appium] Local connection failed: {e}")
            raise

    def _start_cloud_driver(self):
        """Conecta ao BrowserStack / SauceLabs via Remote WebDriver."""
        try:
            from appium import webdriver as appium_webdriver
            from appium.options.common.base import AppiumOptions

            options = AppiumOptions()
            configured_platform = str(self._settings.get("platform_name", "")).lower()
            custom_caps_str = str(self._settings.get("custom_capabilities", "")).lower()
            is_ios = "ios" in configured_platform or "xcuitest" in custom_caps_str or "bundleid" in custom_caps_str

            if is_ios:
                options.platform_name = "iOS"
                options.set_capability("appium:automationName", "XCUITest")
            else:
                options.platform_name = "Android"
                options.set_capability("appium:automationName", "UiAutomator2")

            s = self._settings

            options.set_capability("appium:deviceName", s["device_name"])
            if s.get("platform_version"):
                options.set_capability("appium:platformVersion", s["platform_version"])
            if s.get("app_identifier"):
                app_id = s["app_identifier"]
                is_app_file = (
                    app_id.lower().endswith(('.apk', '.ipa', '.app')) or
                    '\\' in app_id or
                    ('/' in app_id and not app_id.startswith('com.') and not app_id.startswith('br.') and not app_id.startswith('io.'))
                )
                if is_app_file:
                    options.set_capability("appium:app", app_id)
                else:
                    if is_ios:
                        options.set_capability("appium:bundleId", app_id)
                    else:
                        if '/' in app_id:
                            pkg, activity = app_id.split('/', 1)
                            options.set_capability("appium:appPackage", pkg)
                            options.set_capability("appium:appActivity", activity)
                            options.set_capability("appium:appWaitActivity", "*")
                        else:
                            options.set_capability("appium:appPackage", app_id)
            options.set_capability("appium:noReset", True)
            options.set_capability("appium:autoGrantPermissions", True)
            options.set_capability("appium:newCommandTimeout", 3600)
            options.set_capability("appium:ignoreUnimportantViews", True)
            options.set_capability("appium:disableWindowAnimation", True)
            options.set_capability("appium:unicodeKeyboard", True)
            options.set_capability("appium:resetKeyboard", True)
            options.set_capability("appium:waitForIdleTimeout", 0)

            # Apply custom capabilities (overrides default options if specified)
            self._apply_custom_capabilities(options)

            # Cloud-specific extras
            if self._provider == "browserstack":
                options.set_capability("bstack:options", {
                    "userName": s["auth_user"],
                    "accessKey": s["auth_token"],
                    "appiumVersion": "2.0.0",
                    "debug": False,
                    "networkLogs": True,
                })
                hub_url = (s.get("server_url") or "https://hub-cloud.browserstack.com/wd/hub")
            else:  # saucelabs
                options.set_capability("sauce:options", {
                    "username": s["auth_user"],
                    "accessKey": s["auth_token"],
                })
                hub_url = (s.get("server_url") or
                           f"https://{s['auth_user']}:{s['auth_token']}@ondemand.us-west-1.saucelabs.com/wd/hub")

            self._driver = appium_webdriver.Remote(
                command_executor=hub_url,
                options=options
            )
            self._driver.implicitly_wait(0)
            try:
                self._driver.update_settings({"waitForIdleTimeout": 0})
            except Exception:
                pass
            logger.info(f"📱 [Appium] Connected to cloud provider '{self._provider}' Cloud session started — device: {s['device_name']}")

        except ImportError:
            raise RuntimeError("Appium-Python-Client not installed")
        except Exception as e:
            logger.error(f"📱 [{self._provider}] Cloud connection failed: {e}")
            raise

    def _start_playwright_adb(self):
        """Experimental: Playwright + ADB Android control."""
        logger.info("🎭 [Playwright ADB] Starting experimental Android session.")
        # Currently a stub — Playwright doesn't natively support native Android apps.
        # Real implementation would require a custom bridge (e.g., py-android-viewclient + Playwright hybrid).
        self._driver = None
        logger.warning("🎭 [Playwright ADB] Not fully implemented yet. Steps will be simulated.")

    # ------------------------------------------------------------------
    # Step Execution
    # ------------------------------------------------------------------

    def pop_captured_requests(self):
        ret = self._captured_requests
        self._captured_requests = []
        return ret

    def _get_platform_selector(self, props: dict, allow_value_fallback: bool = False) -> str:
        logger.info(f"🔍 [Appium] _get_platform_selector props: {props}")
        if not self._driver:
            return props.get('selector') or (props.get('value', '') if allow_value_fallback else '')
        
        platform = str(self._driver.capabilities.get('platformName', 'android')).lower()
        logger.info(f"🔍 [Appium] detected platformName: {platform}")
        
        if platform == 'ios':
            selected = props.get('ios_selector') or props.get('selector') or (props.get('value', '') if allow_value_fallback else '')
            logger.info(f"🔍 [Appium] selected iOS selector: {selected}")
            return selected
        else:
            selected = props.get('android_selector') or props.get('selector') or (props.get('value', '') if allow_value_fallback else '')
            logger.info(f"🔍 [Appium] selected Android selector: {selected}")
            return selected

    def _save_screenshot(self, b64_data: str, prefix: str = "mobile") -> str:
        if not b64_data:
            return None
        import os
        import uuid
        import base64
        from app.main import VIDEO_DIR
        SCREENSHOT_DIR = os.path.join(os.path.dirname(VIDEO_DIR), "screenshots")
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        filename = f"{prefix}_{uuid.uuid4().hex[:8]}.png"
        path = os.path.join(SCREENSHOT_DIR, filename)
        try:
            with open(path, "wb") as f:
                f.write(base64.b64decode(b64_data))
            return f"/screenshots/{filename}"
        except Exception as e:
            logger.error(f"Failed to save Appium screenshot: {e}")
            return None

    def _clear_element_text(self, el):
        """Robustly erases text from a mobile input element."""
        if not el:
            return
        logger.info("🧹 [Appium] Clearing input element text...")
        try:
            el.clear()
            time.sleep(0.15)
        except Exception as e:
            logger.warning(f"⚠️ [Appium] el.clear() failed: {e}")

        # Verify if text was erased; if text remains, execute backspace keycode loop
        try:
            current_text = (el.text or el.get_attribute('text') or el.get_attribute('value') or '').strip()
            hint_text = (el.get_attribute('hint') or '').strip()
            if current_text and current_text != hint_text:
                logger.info(f"🧹 [Appium] Text '{current_text}' remains after clear(). Executing backspace keycode loop...")
                try:
                    el.click()
                    time.sleep(0.1)
                except Exception:
                    pass

                text_len = len(current_text)
                for _ in range(text_len + 5):
                    try:
                        self._driver.press_keycode(67)  # KEYCODE_DEL
                        time.sleep(0.02)
                    except Exception:
                        break
        except Exception as err:
            logger.warning(f"⚠️ [Appium] Backspace keycode clear fallback error: {err}")

    def execute_step_sync(self, step_data: dict, capture_screenshot: bool = False, db=None, user_id=None) -> dict:
        """Executa um passo nativo no Appium/Cloud."""
        self._last_strategy_used = 'standard'
        step_type = step_data.get('type')
        props = step_data.get('properties', {})
        step_name = step_data.get('name', step_type)

        step_warning = None

        logger.info(f"📱 [{self._provider or 'appium'}] Executing step: {step_type} — {step_name}")

        try:
            # Log current screen state for debugging physical device mismatches
            if not self._driver:
                logger.warning("📱 No active Appium driver — step cannot be executed.")
                return {
                    "status": 400,
                    "reason": "No Driver",
                    "text": f"Step '{step_type}' failed: No active Appium driver session."
                }
            try:
                window_size = self._driver.get_window_size()
                logger.info(f"📱 [Appium] Screen resolution: {window_size['width']}x{window_size['height']}")
            except: pass

            # Allow grace period for first step to accommodate app cold-boot splash screen
            if getattr(self, '_is_first_step', False):
                self._is_first_step = False
                orig_to = int(props.get('timeout', 5000))
                props['timeout'] = max(orig_to, 30000)
                logger.info(f"📱 [Appium] First step grace period applied: timeout={props['timeout']}ms to accommodate app launch/splash screen")

            if step_type == 'tap':
                selector = self._get_platform_selector(props, allow_value_fallback=False)
                x = props.get('x')
                y = props.get('y')
                if x is not None: x = int(x)
                if y is not None: y = int(y)
                timeout = int(props.get('timeout', 5000))
                target_text = props.get('target_text') or props.get('target_label')
                use_vision_ai = props.get('use_vision_ai', False)
                
                tapped = False
                if not selector and not target_text and (x is None or x <= 0):
                    raise Exception("No identifier, target_text or coordinates provided for click.")

                # Strategy 0: Explicit Vision AI requested (or plain text target without DOM selector)
                if use_vision_ai:
                    logger.info(f"🎨 [Vision AI] Explicit Vision AI requested for target '{target_text or selector}'")
                    vision_coords = self.detect_button_coordinates_from_screenshot(target_text=target_text or selector)
                    if vision_coords:
                        cx, cy = vision_coords
                        step_warning = (
                            "⚠️ Alerta de Boas Práticas: A automação utilizou Visão Computacional na captura de tela em tempo de execução para calcular o ponto de clique."
                        )
                        logger.info(f"🎨 [Vision AI] Screenshot button coordinates generated: ({cx}, {cy})")
                        try:
                            self._driver.execute_script('mobile: clickGesture', {'x': cx, 'y': cy})
                            tapped = True
                        except Exception:
                            try:
                                self._driver.tap([(cx, cy)])
                                tapped = True
                            except Exception: pass

                if not tapped:
                    try:
                        el = self._find_element(selector or target_text, timeout_ms=timeout, step_data=step_data)
                        if el:
                            loc = el.location
                            size = el.size

                            orig_loc = loc
                            orig_size = size
                            orig_cx = orig_loc['x'] + (orig_size['width'] // 2)
                            orig_cy = orig_loc['y'] + (orig_size['height'] // 2)

                            # Auto-promote non-clickable target (e.g., inner TextView inside a modal button) to its clickable container
                            try:
                                if el.get_attribute("clickable") != "true":
                                    ancestors = el.find_elements(AppiumBy.XPATH, "./ancestor::*[@clickable='true']")
                                    if ancestors:
                                        # Pick nearest clickable parent that is not a giant modal dialog or screen container (> 400px height)
                                        small_ancestors = [a for a in ancestors if a.size.get('height', 9999) <= max(350, orig_size['height'] * 3)]
                                        best_ancestor = small_ancestors[-1] if small_ancestors else ancestors[-1]
                                        
                                        # Only replace target coordinates if the ancestor is small/button wrapper
                                        anc_size = best_ancestor.size
                                        if anc_size.get('height', 9999) <= 400:
                                            logger.info(f"🎯 [Appium] Target element is non-clickable ({el.tag_name}); upgrading tap target to parent container: {best_ancestor.tag_name}")
                                            el = best_ancestor
                                            loc = el.location
                                            size = el.size
                                        else:
                                            logger.info(f"🎯 [Appium] Target element is non-clickable ({el.tag_name}); keeping target coordinates ({orig_cx}, {orig_cy}) from inner element")
                            except Exception as parent_err:
                                logger.debug(f"Parent clickable check skipped: {parent_err}")

                            # Check if the element is targeted using a strong unique identifier (resource-id, content-desc, accessibility-id, testID)
                            sel_str = str(selector or '').lower()
                            has_unique_identifier = False
                            if sel_str:
                                if '@content-desc' in sel_str or '@resource-id' in sel_str or '@name' in sel_str or '@accessibility-id' in sel_str or '@id' in sel_str or '@testid' in sel_str:
                                    has_unique_identifier = True
                                elif not sel_str.startswith('/') and not sel_str.startswith('.'):
                                    # Direct Accessibility ID or Resource ID string (e.g. welcome_button, btn_login_entrarEntrar)
                                    has_unique_identifier = True

                            is_compound_container = size.get('height', 0) > 180 and not has_unique_identifier

                            if not has_unique_identifier:
                                # Calculate dynamic runtime coordinates for elements without explicit IDs
                                vision_coords = self.detect_button_coordinates_from_screenshot(
                                    container_bounds={'x': loc['x'], 'y': loc['y'], 'width': size['width'], 'height': size['height']},
                                    target_text=target_text
                                )

                                if vision_coords:
                                    cx, cy = vision_coords
                                    step_warning = (
                                        "⚠️ Alerta de Boas Práticas: Este elemento não possui um identificador exclusivo (ex: resource-id, accessibility-id ou testID) "
                                        "configurado pelo time de desenvolvimento. A automação utilizou Visão Computacional em tempo de execução para interagir."
                                    )
                                    logger.info(f"🎨 [Vision AI] Runtime button coordinates generated for current device: ({cx}, {cy})")
                                elif is_compound_container:
                                    cx = loc['x'] + (size['width'] // 2)
                                    cy = loc['y'] + int(size['height'] * 0.88)
                                    step_warning = (
                                        "⚠️ Alerta de Boas Práticas: O botão está dentro de um container composto sem ID exclusivo. "
                                        "A automação utilizou cálculo inteligente de região de ação."
                                    )
                                    logger.info(f"💡 [Appium] Compound Modal Container detected ({size['width']}x{size['height']}px). Dynamic bottom action target: ({cx}, {cy})")
                                elif isinstance(step_data, dict) and step_data.get('_used_fallback'):
                                    cx = loc['x'] + (size['width'] // 2)
                                    cy = loc['y'] + (size['height'] // 2)
                                    step_warning = (
                                        "⚠️ Alerta de Boas Práticas: O elemento não possui um identificador exclusivo (resource-id/testID). "
                                        "A automação utilizou Fallback por Texto para localizá-lo."
                                    )
                                else:
                                    cx = loc['x'] + (size['width'] // 2)
                                    cy = loc['y'] + (size['height'] // 2)
                                    logger.info(f"🎯 [Appium] Dynamic runtime element center target: ({cx}, {cy})")
                            else:
                                cx = loc['x'] + (size['width'] // 2)
                                cy = loc['y'] + (size['height'] // 2)
                                logger.info(f"🎯 [Appium] Element with unique identifier '{selector}' targeted directly at ({cx}, {cy}). No warning needed.")

                            platform = str(self._driver.capabilities.get('platformName', 'android')).lower()

                            target_el = el

                            if platform == 'ios':
                                # iOS Strategy 1: Direct native element click (XCUITest synthesizes real touch tap)
                                try:
                                    target_el.click()
                                    tapped = True
                                    logger.info(f"🎯 [Appium iOS] Direct native target_el.click() succeeded for '{selector}'")
                                except Exception as click_err:
                                    logger.warning(f"⚠️ [Appium iOS] Direct native target_el.click() failed ({click_err}), trying coordinate fallback")

                                # iOS Strategy 2: Physical W3C coordinate tap at (cx, cy)
                                if not tapped:
                                    try:
                                        self._driver.tap([(cx, cy)])
                                        tapped = True
                                        logger.info(f"📍 [Appium iOS] Physical W3C tap at ({cx}, {cy}) succeeded for '{selector}'")
                                    except Exception as tap_err:
                                        logger.warning(f"⚠️ [Appium iOS] Physical W3C tap failed: {tap_err}")
                            else:
                                # Android Strategy 1: Direct Native Accessibility Element Click (target_el.click())
                                # Invokes native Android AccessibilityNodeInfo ACTION_CLICK directly on target element.
                                try:
                                    target_el.click()
                                    tapped = True
                                    logger.info(f"🎯 [Appium Android] Direct native target_el.click() succeeded for '{selector}'")
                                except Exception as click_err:
                                    logger.warning(f"⚠️ [Appium Android] Direct native target_el.click() failed: {click_err}")

                                # Android Strategy 2: Appium 2.x Native Touch Gesture on Element ID
                                if not tapped:
                                    try:
                                        self._driver.execute_script('mobile: clickGesture', {'elementId': target_el.id})
                                        tapped = True
                                        logger.info(f"🎯 [Appium Android] mobile: clickGesture on elementId succeeded for '{selector}'")
                                    except Exception as g_id_err:
                                        logger.warning(f"⚠️ [Appium Android] mobile: clickGesture on elementId failed: {g_id_err}")

                                # Android Strategy 3: Original element click (if target_el was upgraded from el)
                                if not tapped and target_el != el:
                                    try:
                                        el.click()
                                        tapped = True
                                        logger.info(f"🎯 [Appium Android] Original el.click() fallback succeeded for '{selector}'")
                                    except Exception:
                                        pass

                                # Android Strategy 4: Physical Hardware Touch Tap (mobile: clickGesture) at Center Coordinates (cx, cy)
                                if not tapped:
                                    try:
                                        self._driver.execute_script('mobile: clickGesture', {'x': cx, 'y': cy})
                                        tapped = True
                                        logger.info(f"📍 [Appium Android] Physical touch tap (mobile: clickGesture) at ({cx}, {cy}) succeeded for '{selector}'")
                                    except Exception as g_err:
                                        logger.warning(f"⚠️ [Appium Android] Physical clickGesture at ({cx}, {cy}) failed: {g_err}")

                                # Android Strategy 5: W3C Hardware Touch Tap at (cx, cy)
                                if not tapped:
                                    try:
                                        self._driver.tap([(cx, cy)])
                                        tapped = True
                                        logger.info(f"📍 [Appium Android] W3C hardware tap at ({cx}, {cy}) succeeded for '{selector}'")
                                    except Exception as tap_err:
                                        logger.warning(f"⚠️ [Appium Android] W3C hardware tap at ({cx}, {cy}) failed: {tap_err}")

                            # 6. Fallback: Recorded static coordinates (x, y)
                            if not tapped and x is not None and y is not None and x > 0 and y > 0:
                                try:
                                    logger.info(f"📍 [Appium] Trying static recorded coordinates ({x}, {y}) as fallback")
                                    self._driver.execute_script('mobile: clickGesture', {'x': int(x), 'y': int(y)})
                                    tapped = True
                                except Exception:
                                    try:
                                        self._driver.tap([(int(x), int(y))])
                                        tapped = True
                                    except Exception:
                                        pass

                            # 7. Fallback: Check for inner clickable child nodes inside container
                            if not tapped:
                                try:
                                    inner_buttons = el.find_elements(AppiumBy.XPATH, ".//android.widget.Button | .//*[@clickable='true']")
                                    if inner_buttons:
                                        inner_buttons[-1].click()
                                        tapped = True
                                        logger.info("🎯 [Appium] Clicked inner clickable child element inside container")
                                except Exception:
                                    pass
                    except Exception as sel_err:
                        logger.warning(f"⚠️ [Appium] Element '{selector}' not found in DOM ({sel_err}).")
                        # Vision AI Fallback on Screenshot ONLY when explicit use_vision_ai is True
                        if not tapped and use_vision_ai:
                            logger.info(f"🎨 [Vision AI] Attempting fallback for '{selector}'...")
                            query_target = target_text
                            if not query_target and selector:
                                raw_s = str(selector)
                                q_matches = re.findall(r"['\"]([^'\"]+)['\"]", raw_s)
                                query_target = " ".join(q_matches) if q_matches else raw_s
                                query_target = re.sub(r'Guia \d+ de \d+|Tab \d+ of \d+', '', query_target, flags=re.IGNORECASE).strip()
                                query_target = " ".join(query_target.split())

                            vision_coords = self.detect_button_coordinates_from_screenshot(target_text=query_target)
                            if vision_coords:
                                cx, cy = vision_coords
                                step_warning = (
                                    "⚠️ Alerta de Boas Práticas: O elemento não foi encontrado no DOM. A automação utilizou Visão Computacional na captura de tela para clicar."
                                )
                                logger.info(f"🎨 [Vision AI Fallback] Screenshot tap executed at ({cx}, {cy}) for '{query_target}'")
                                try:
                                    self._driver.execute_script('mobile: clickGesture', {'x': cx, 'y': cy})
                                    tapped = True
                                except Exception:
                                    try:
                                        self._driver.tap([(cx, cy)])
                                        tapped = True
                                    except Exception: pass

                if not tapped:
                    raise Exception(f"Element with identifier '{selector}' not found on device.")
                
                time.sleep(0.05)

            elif step_type == 'long_press':
                from appium.webdriver.common.touch_action import TouchAction
                selector = self._get_platform_selector(props, allow_value_fallback=False)
                duration_ms = int(props.get('duration', 2000))
                timeout = int(props.get('timeout', 5000))
                el = self._find_element(selector, timeout_ms=timeout, step_data=step_data)
                if el:
                    action = TouchAction(self._driver)
                    action.long_press(el, duration=duration_ms).release().perform()
                else:
                    raise Exception(f"Element '{selector}' not found for long_press.")

            elif step_type == 'type':
                selector = self._get_platform_selector(props, allow_value_fallback=False)
                raw_value = str(props.get('value', ''))
                
                # Resolve variables ({{BOMBA_ID}}, {{env.BOMBA_CODE}}, Faker, etc.)
                variables = step_data.get('variables', {}) if isinstance(step_data, dict) else {}
                try:
                    from app.services.variable_resolver import replace_vars
                    value = replace_vars(raw_value, variables)
                except Exception as var_err:
                    logger.warning(f"⚠️ [Appium] Variable resolution skipped for '{raw_value}': {var_err}")
                    value = raw_value

                timeout = int(props.get('timeout', 5000))
                
                if not selector:
                    raise Exception("No identifier or selector provided for type.")

                el = self._find_element(selector, timeout_ms=timeout, step_data=step_data)
                if not el:
                    raise Exception(f"Element with identifier '{selector}' not found for typing.")

                # If located element is a container (ViewGroup, View, etc.), search for inner editable EditText/TextField
                try:
                    from appium.webdriver.common.appiumby import AppiumBy
                    tag_or_cls = (el.tag_name or el.get_attribute('className') or '').lower()
                    if 'edittext' not in tag_or_cls and 'textfield' not in tag_or_cls:
                        inner_inputs = el.find_elements(AppiumBy.XPATH, ".//android.widget.EditText | .//XCUIElementTypeTextField | .//XCUIElementTypeSecureTextField | .//*[@class='android.widget.EditText']")
                        if inner_inputs:
                            el = inner_inputs[0]
                            logger.info(f"🎯 [Appium] Extracted inner editable input element from container '{selector}'")
                except Exception as inner_err:
                    logger.debug(f"Inner input search skipped: {inner_err}")

                # Clear text first if clear_first / clear_before_type property is requested
                if props.get('clear_first') or props.get('clear_before_type'):
                    logger.info("🧹 [Appium] clear_first flag set: clearing field before typing...")
                    self._clear_element_text(el)

                def is_typed_successfully():
                    clean_val = value.strip()
                    if not clean_val:
                        return True
                    try:
                        now_text = (el.text or el.get_attribute('text') or el.get_attribute('value') or '').strip()
                        act_text = ''
                        try:
                            active_el = self._driver.switch_to.active_element
                            if active_el:
                                act_text = (active_el.text or active_el.get_attribute('text') or active_el.get_attribute('value') or '').strip()
                        except Exception:
                            pass

                        # Strict match: clean_val must be contained in now_text or act_text (case-sensitive)
                        if clean_val in now_text or clean_val in act_text:
                            return True
                        
                        # Match digits ONLY if the input value is purely numeric (e.g. CPF / CNPJ / phone)
                        # If value contains letters (like passwords or names), strict case match is required.
                        has_letters = any(c.isalpha() for c in clean_val)
                        if not has_letters:
                            digits_val = ''.join(c for c in clean_val if c.isdigit())
                            if len(digits_val) >= 3:
                                digits_now = ''.join(c for c in now_text if c.isdigit())
                                digits_act = ''.join(c for c in act_text if c.isdigit())
                                if digits_val in digits_now or digits_val in digits_act:
                                    return True
                    except Exception:
                        pass
                    return False

                # Hardware Keycode Typing Helper (Forces React Native / Android to trigger onChangeText and render visual text)
                def type_hardware_keycodes(val_str):
                    for char in val_str:
                        kc = None
                        metastate = 0
                        if char.isdigit():
                            kc = 7 + int(char)
                        elif 'a' <= char <= 'z':
                            kc = 29 + (ord(char) - ord('a'))
                        elif 'A' <= char <= 'Z':
                            kc = 29 + (ord(char.lower()) - ord('a'))
                            metastate = 1  # META_SHIFT_ON for uppercase letters
                        elif char == ' ':
                            kc = 62
                        elif char == '@':
                            kc = 77
                        elif char == '.':
                            kc = 56
                        elif char == '-':
                            kc = 69
                        
                        if kc is not None:
                            try:
                                if metastate > 0:
                                    self._driver.press_keycode(kc, metastate)
                                else:
                                    self._driver.press_keycode(kc)
                                time.sleep(0.03)
                            except Exception:
                                pass

                # Focus the element by tapping center
                try:
                    loc = el.location
                    size = el.size
                    cx = loc['x'] + (size['width'] // 2)
                    cy = loc['y'] + (size['height'] // 2)
                    self._driver.tap([(cx, cy)])
                except Exception:
                    try:
                        el.click()
                    except Exception:
                        pass
                time.sleep(0.2)

                typed_success = False

                # Strategy 1: Hardware Keycode Events (triggers React Native / Android onChangeText visual render)
                try:
                    type_hardware_keycodes(value)
                    time.sleep(0.25)
                    if is_typed_successfully():
                        typed_success = True
                        logger.info(f"⌨️ [Appium] Hardware Keycode typing verified for '{selector}'")
                except Exception as ekc:
                    logger.warning(f"⚠️ [Appium] Hardware Keycode typing failed on '{selector}': {ekc}")

                # Strategy 2: Direct send_keys
                if not typed_success:
                    self._clear_element_text(el)
                    try:
                        el.send_keys(value)
                        time.sleep(0.25)
                        if is_typed_successfully():
                            typed_success = True
                            logger.info(f"⌨️ [Appium] Direct send_keys verified for '{selector}'")
                    except Exception as e1:
                        logger.warning(f"⚠️ [Appium] Direct send_keys failed on '{selector}': {e1}")

                # Strategy 3: set_value / set_text
                if not typed_success:
                    self._clear_element_text(el)
                    try:
                        if hasattr(el, 'set_value'):
                            el.set_value(value)
                        elif hasattr(self._driver, 'set_value'):
                            self._driver.set_value(el, value)
                        time.sleep(0.25)
                        if is_typed_successfully():
                            typed_success = True
                            logger.info(f"⌨️ [Appium] set_value verified for '{selector}'")
                    except Exception as e2:
                        logger.warning(f"⚠️ [Appium] set_value failed: {e2}")

                # Strategy 4: Selenium ActionChains
                if not typed_success:
                    self._clear_element_text(el)
                    try:
                        from selenium.webdriver.common.action_chains import ActionChains
                        actions = ActionChains(self._driver)
                        actions.click(el)
                        for char in value:
                            actions.send_keys(char)
                            actions.pause(0.02)
                        actions.perform()
                        time.sleep(0.25)
                        if is_typed_successfully():
                            typed_success = True
                            logger.info(f"⌨️ [Appium] ActionChains character send_keys verified for '{selector}'")
                    except Exception as e3:
                        logger.warning(f"⚠️ [Appium] ActionChains failed: {e3}")

                # Strategy 5: Active element send_keys
                if not typed_success:
                    self._clear_element_text(el)
                    try:
                        active_el = self._driver.switch_to.active_element
                        if active_el:
                            active_el.send_keys(value)
                            time.sleep(0.25)
                            if is_typed_successfully():
                                typed_success = True
                                logger.info(f"⌨️ [Appium] Active element send_keys verified for '{selector}'")
                    except Exception as e4:
                        logger.warning(f"⚠️ [Appium] Active element send_keys failed: {e4}")

                # Strategy 6: Mobile shell ADB input text (Android)
                if not typed_success:
                    self._clear_element_text(el)
                    try:
                        adb_value = value.replace(' ', '%s')
                        self._driver.execute_script('mobile: shell', {'command': 'input text', 'args': [adb_value]})
                        time.sleep(0.25)
                        if is_typed_successfully():
                            typed_success = True
                            logger.info(f"⌨️ [Appium] ADB shell input text verified for '{selector}'")
                    except Exception as e5:
                        logger.warning(f"⚠️ [Appium] ADB shell input text failed: {e5}")

                if not typed_success:
                    now_val = ''
                    try:
                        now_val = (el.text or el.get_attribute('text') or el.get_attribute('value') or '')
                    except Exception:
                        pass
                    raise Exception(f"Falha ao digitar '{value}' no elemento '{selector}'. O texto do campo permaneceu '{now_val}'.")

                # Force React Native / Flutter component visual refresh
                try:
                    self._driver.execute_script('mobile: performEditorAction', {'action': 'normal'})
                except Exception:
                    pass

            elif step_type == 'clear_field':
                selector = self._get_platform_selector(props)
                timeout = int(props.get('timeout', 5000))
                el = self._find_element(selector, timeout_ms=timeout, step_data=step_data)
                if el:
                    self._clear_element_text(el)
                else:
                    raise Exception(f"Element '{selector}' not found for clear_field after {timeout}ms")

            elif step_type == 'hide_keyboard':
                try:
                    self._driver.hide_keyboard()
                except Exception:
                    pass  # Already hidden

            elif step_type == 'swipe':
                direction = props.get('direction', 'up')
                distance_pct = int(props.get('distance', 50)) / 100.0
                duration_ms = int(props.get('duration', 800))
                size = self._driver.get_window_size()
                w, h = size['width'], size['height']
                cx = w // 2
                swipe_map = {
                    'up':    (cx, int(h * 0.7), cx, int(h * (0.7 - distance_pct))),
                    'down':  (cx, int(h * 0.3), cx, int(h * (0.3 + distance_pct))),
                    'left':  (int(w * 0.8), h // 2, int(w * (0.8 - distance_pct)), h // 2),
                    'right': (int(w * 0.2), h // 2, int(w * (0.2 + distance_pct)), h // 2),
                }
                sx, sy, ex, ey = swipe_map.get(direction, swipe_map['up'])
                self._driver.swipe(sx, sy, ex, ey, duration_ms)

            elif step_type == 'scroll_to':
                selector = self._get_platform_selector(props)
                platform = str(self._driver.capabilities.get('platformName', 'android')).lower()
                if platform == 'ios':
                    self._driver.execute_script("mobile: scroll", {"direction": "down"})
                else:
                    from appium.webdriver.common.appiumby import AppiumBy
                    try:
                        self._driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR,
                            f'new UiScrollable(new UiSelector().scrollable(true))'
                            f'.scrollIntoView(new UiSelector().descriptionContains("{selector}"))')
                    except Exception:
                        # Fallback: scroll until element appears
                        el = self._find_element(selector)
                        if el:
                            self._driver.execute_script("mobile: scrollGesture", {
                                "elementId": el.id, "direction": "down", "percent": 0.75
                            })

            elif step_type == 'drag_drop':
                from appium.webdriver.common.touch_action import TouchAction
                src_sel = props.get('selector', '')
                tgt_sel = props.get('target', '')
                duration_ms = int(props.get('duration', 1000))
                timeout = int(props.get('timeout', 5000))
                src = self._find_element(src_sel, timeout_ms=timeout)
                tgt = self._find_element(tgt_sel, timeout_ms=timeout)
                if src and tgt:
                    action = TouchAction(self._driver)
                    action.long_press(src, duration=duration_ms).move_to(tgt).release().perform()
                else:
                    raise Exception(f"Drag item '{src_sel}' or drop target '{tgt_sel}' not found after {timeout}ms")

            elif step_type == 'pinch':
                scale = float(props.get('scale', 0.5))
                self._driver.execute_script("mobile: pinchCloseGesture", {
                    "elementId": None, "percent": min(scale, 1.0), "speed": 2500
                })

            elif step_type == 'zoom':
                scale = float(props.get('scale', 1.5))
                self._driver.execute_script("mobile: pinchOpenGesture", {
                    "elementId": None, "percent": min(scale / 3.0, 1.0), "speed": 2500
                })

            elif step_type == 'rotate':
                orientation_map = {
                    'portrait': 'PORTRAIT',
                    'landscape': 'LANDSCAPE',
                    'portrait_reverse': 'PORTRAIT',
                    'landscape_reverse': 'LANDSCAPE',
                }
                orientation = orientation_map.get(
                    props.get('orientation', 'portrait').lower(), 'PORTRAIT')
                self._driver.orientation = orientation
                logger.info(f"📱 Rotated to {orientation}")

            elif step_type == 'shake':
                self._driver.shake()

            elif step_type == 'back_button':
                platform = str(self._driver.capabilities.get('platformName', 'android')).lower()
                if platform == 'ios':
                    self._driver.execute_script('mobile: swipe', {'direction': 'right'})
                else:
                    self._driver.press_keycode(4)  # Android KEYCODE_BACK

            elif step_type == 'home_button':
                platform = str(self._driver.capabilities.get('platformName', 'android')).lower()
                if platform == 'ios':
                    self._driver.execute_script('mobile: pressButton', {'name': 'home'})
                else:
                    self._driver.press_keycode(3)  # Android KEYCODE_HOME

            elif step_type == 'set_location':
                lat = float(props.get('latitude', -23.5505))
                lon = float(props.get('longitude', -46.6333))
                self._driver.set_location(lat, lon, 0)
                logger.info(f"📍 Location set to ({lat}, {lon})")

            elif step_type == 'assert':
                try:
                    self._driver.hide_keyboard()
                except Exception:
                    pass
                selector = self._get_platform_selector(props)
                operator = props.get('operator', 'visible')
                expected = props.get('value', '')
                el = self._find_element(selector, step_data=step_data)

                if operator == 'visible':
                    if not el or not el.is_displayed():
                        raise AssertionError(f"Element '{selector}' is not visible")
                elif operator == 'not_visible':
                    if el and el.is_displayed():
                        raise AssertionError(f"Element '{selector}' should not be visible")
                elif operator == 'contains':
                    if el:
                        actual = el.text
                        if expected not in actual:
                            raise AssertionError(f"Expected '{expected}' in '{actual}'")
                elif operator == 'equals':
                    if el:
                        actual = el.text
                        if actual != expected:
                            raise AssertionError(f"Expected '{expected}', got '{actual}'")
                elif operator == 'enabled':
                    if not el or not el.is_enabled():
                        raise AssertionError(f"Element '{selector}' is not enabled")
                elif operator == 'disabled':
                    if el and el.is_enabled():
                        raise AssertionError(f"Element '{selector}' should be disabled")

            elif step_type == 'assert_visual':
                try:
                    self._driver.hide_keyboard()
                except Exception:
                    pass
                import base64
                import numpy as np
                import cv2

                selector = self._get_platform_selector(props)
                base_image_b64 = props.get('base_image_b64', '')
                tolerance = float(props.get('tolerance', 5.0)) # Note: Lower is stricter. With OpenCV, we use matching threshold

                if not base_image_b64:
                    raise AssertionError("No base image provided for visual assertion")

                # Get element screenshot or full screenshot
                el = self._find_element(selector, step_data=step_data) if selector else None
                
                try:
                    if el:
                        # Capture element only
                        current_b64 = el.screenshot_as_base64
                    else:
                        # Full screen if no selector
                        current_b64 = self._driver.get_screenshot_as_base64()
                        
                    # Decode images
                    base_bytes = base64.b64decode(base_image_b64.split(',')[1] if ',' in base_image_b64 else base_image_b64)
                    current_bytes = base64.b64decode(current_b64)
                    
                    # Convert to numpy arrays for OpenCV
                    nparr_base = np.frombuffer(base_bytes, np.uint8)
                    nparr_curr = np.frombuffer(current_bytes, np.uint8)
                    
                    img_base = cv2.imdecode(nparr_base, cv2.IMREAD_COLOR)
                    img_curr = cv2.imdecode(nparr_curr, cv2.IMREAD_COLOR)
                    
                    # If sizes differ, OpenCV matchTemplate expects template to be smaller or equal
                    # So we use resize if necessary, or just compute similarity if same size.
                    if img_base.shape != img_curr.shape:
                        logger.warning(f"Resizing captured image {img_curr.shape} to base image {img_base.shape}")
                        img_curr = cv2.resize(img_curr, (img_base.shape[1], img_base.shape[0]))
                    
                    # Convert to grayscale for better structure matching
                    gray_base = cv2.cvtColor(img_base, cv2.COLOR_BGR2GRAY)
                    gray_curr = cv2.cvtColor(img_curr, cv2.COLOR_BGR2GRAY)
                    
                    # Template Matching
                    res = cv2.matchTemplate(gray_curr, gray_base, cv2.TM_CCOEFF_NORMED)
                    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                    
                    # max_val is the accuracy (1.0 is perfect match)
                    similarity_pct = max_val * 100.0
                    diff_pct = 100.0 - similarity_pct
                    
                    logger.info(f"Visual similarity: {similarity_pct:.2f}% (Diff: {diff_pct:.2f}%, Tolerance: {tolerance}%)")
                    
                    if diff_pct > tolerance:
                        raise AssertionError(f"Visual mismatch: Image differs by {diff_pct:.2f}% (max allowed is {tolerance}%)")
                except AssertionError as ae:
                    raise ae
                except Exception as ex:
                    logger.error(f"Error during OpenCV visual assert: {ex}")
                    raise AssertionError(f"Failed to perform OpenCV visual comparison: {ex}")

            elif step_type == 'wait':
                delay = int(props.get('timeout', 1000))
                time.sleep(delay / 1000.0)

            elif step_type == 'screenshot':
                if self._driver:
                    screenshot_b64 = self._driver.get_screenshot_as_base64()
                    s_url = self._save_screenshot(screenshot_b64, "manual")
                    if s_url:
                        return {
                            "status": 200,
                            "reason": "OK",
                            "text": f"Screenshot captured [Screenshot: {s_url}]",
                            "screenshot_b64": screenshot_b64
                        }
                    else:
                        raise Exception("Failed to save screenshot.")

            elif step_type == 'install_app':
                apk_path = props.get('apk_path', '')
                if apk_path:
                    self._driver.install_app(apk_path)
                    logger.info(f"📦 [Appium] Installed app: {apk_path}")

            elif step_type == 'reset_app':
                package_name = props.get('package_name', '') or self._settings.get('app_identifier', '')
                if package_name:
                    try:
                        self._driver.terminate_app(package_name)
                        time.sleep(1)
                        self._driver.activate_app(package_name)
                    except Exception:
                        self._driver.reset()
                else:
                    self._driver.reset()
                logger.info(f"🔄 [Appium] App reset: {package_name}")

            elif step_type == 'launch_app':
                deep_link = props.get('deep_link', '')
                if deep_link.startswith('http') or '://' in deep_link:
                    # It's a deep link URI
                    self._driver.execute_script('mobile: deepLink', {
                        'url': deep_link,
                        'package': self._settings.get('app_identifier', '')
                    })
                elif deep_link:
                    # It's a package name
                    self._driver.activate_app(deep_link)
                logger.info(f"🚀 [Appium] Launched: {deep_link}")

            elif step_type == 'grant_permission':
                permission = props.get('permission', 'android.permission.CAMERA')
                package = self._settings.get('app_identifier', '')
                if package:
                    self._driver.execute_script('mobile: changePermissions', {
                        'permissions': [permission],
                        'action': 'grant',
                        'appPackage': package
                    })
                    logger.info(f"🛡️ [Appium] Granted: {permission} to {package}")

            elif step_type == 'mock_network':
                profile = props.get('network_profile', '4g')
                network_map = {
                    '4g':      {'upload': 10240, 'download': 10240, 'latency': 20,   'offline': False},
                    '3g':      {'upload': 384,   'download': 384,   'latency': 100,  'offline': False},
                    '2g':      {'upload': 64,    'download': 64,    'latency': 300,  'offline': False},
                    'edge':    {'upload': 30,    'download': 80,    'latency': 400,  'offline': False},
                    'offline': {'upload': 0,     'download': 0,     'latency': 0,    'offline': True},
                    'reset':   {'upload': 10240, 'download': 10240, 'latency': 0,    'offline': False},
                }
                settings = network_map.get(profile, network_map['4g'])
                try:
                    self._driver.set_network_conditions(**settings)
                    logger.info(f"🌐 [Appium] Network throttled to: {profile}")
                except Exception:
                    # BrowserStack uses different API
                    bs_payload = json.dumps({"action": "setNetworkConditions", "arguments": {"networkProfile": profile}})
                    self._driver.execute_script(f'browserstack_executor: {bs_payload}')

            # ─── BIOMETRICS ───

            elif step_type == 'fingerprint_pass':
                try:
                    # Appium 2.x — UiAutomator2 biometric auth
                    self._driver.execute_script('mobile: fingerprint', {'fingerprintId': 1})
                    logger.info("🪶 [Appium] Biometric: fingerprint accepted.")
                except Exception:
                    # Fallback for emulator ADB
                    self._driver.press_keycode(66)  # KEYCODE_ENTER to confirm biometric

            elif step_type == 'fingerprint_fail':
                try:
                    self._driver.execute_script('mobile: fingerprint', {'fingerprintId': -1})
                    logger.info("🪶 [Appium] Biometric: fingerprint rejected.")
                except Exception:
                    self._driver.press_keycode(4)  # KEYCODE_BACK to cancel biometric prompt

            elif step_type == 'face_id_pass':
                try:
                    # iOS Simulator biometric match
                    self._driver.execute_script('mobile: enrollBiometric', {'isEnabled': True})
                    self._driver.execute_script('mobile: sendBiometricMatch', {'type': 'faceId', 'match': True})
                    logger.info("👀 [Appium] Face ID: accepted.")
                except Exception:
                    logger.warning("Face ID simulation not supported on this driver.")

            elif step_type == 'wait':
                ms = int(props.get('value', 1000) or 1000)
                time.sleep(ms / 1000.0)
                logger.info(f"⏳ [Appium] Paused execution for {ms}ms")
                return {
                    "status": 200,
                    "reason": "OK",
                    "text": f"Aguardou {ms}ms",
                    "duration": ms
                }

            else:
                logger.warning(f"📱 Unsupported step type: {step_type}")

            # --- Capture screenshot on success if requested ---
            screenshot_b64 = None
            if capture_screenshot:
                try:
                    # Small grace period for UI stabilization before screenshot
                    time.sleep(0.05)
                    screenshot_b64 = self._driver.get_screenshot_as_base64()
                except Exception:
                    pass

            text_res = f"Step '{step_type}' executed successfully."
            if step_warning:
                text_res += f" [{step_warning}]"
            if screenshot_b64:
                s_url = self._save_screenshot(screenshot_b64, f"live_{step_type}")
                if s_url:
                    text_res += f" [Screenshot: {s_url}]"

            return {
                "status": 200,
                "reason": "OK",
                "text": text_res,
                "warning": step_warning,
                "screenshot_b64": screenshot_b64,
                "strategy_used": getattr(self, '_last_strategy_used', 'standard'),
            }

        except AssertionError as ae:
            # — Screenshot on Assertion Failure (Grupo 3)
            failure_screenshot = None
            text_res = str(ae)
            if self._driver:
                try:
                    failure_screenshot = self._driver.get_screenshot_as_base64()
                    logger.info("📸 [Appium] Screenshot on failure captured.")
                    s_url = self._save_screenshot(failure_screenshot, "error_assert")
                    if s_url:
                        text_res += f"\n[Screenshot: {s_url}]"
                except Exception:
                    pass

                # Extract native crash diagnostics (logcat / syslog)
                try:
                    from app.services.mobile_diagnostics_service import MobileDiagnosticsService
                    diag = MobileDiagnosticsService.extract_crash_diagnostics(self._driver)
                    if diag and diag.get("summary"):
                        text_res += f"\n[Crash Diagnostics: {diag['summary']}]"
                    if diag and diag.get("log_url"):
                        text_res += f"\n[Forensic Log: {diag['log_url']}]"
                except Exception:
                    pass

            return {
                "status": 400,
                "reason": "Assertion Error",
                "text": text_res,
                "screenshot_b64": failure_screenshot,
            }
        except Exception as e:
            logger.error(f"📱 Step Failed: {e}")
            # — Screenshot on Unexpected Error
            failure_screenshot = None
            text_res = str(e)
            if self._driver:
                try:
                    failure_screenshot = self._driver.get_screenshot_as_base64()
                    s_url = self._save_screenshot(failure_screenshot, "error_fatal")
                    if s_url:
                        text_res += f"\n[Screenshot: {s_url}]"
                except Exception:
                    pass

                # Extract native crash diagnostics (logcat / syslog)
                try:
                    from app.services.mobile_diagnostics_service import MobileDiagnosticsService
                    diag = MobileDiagnosticsService.extract_crash_diagnostics(self._driver)
                    if diag and diag.get("summary"):
                        text_res += f"\n[Crash Diagnostics: {diag['summary']}]"
                    if diag and diag.get("log_url"):
                        text_res += f"\n[Forensic Log: {diag['log_url']}]"
                except Exception:
                    pass

            return {
                "status": 500,
                "reason": "Appium Error",
                "text": text_res,
                "screenshot_b64": failure_screenshot,
            }

    def _find_elements(self, selector: str, timeout_ms: int = 5000):
        """Localiza todos os elementos que combinam com o seletor usando espera explícita."""
        if not self._driver or not selector:
            return []
        try:
            from appium.webdriver.common.appiumby import AppiumBy
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC

            timeout_s = max(0.5, timeout_ms / 1000.0)
            wait = WebDriverWait(self._driver, timeout_s)
            
            # Detect proper locator strategy
            if selector.startswith('/') or selector.startswith('('):
                locator = (AppiumBy.XPATH, selector)
            elif ':id/' in selector or '/' in selector:
                locator = (AppiumBy.ID, selector)
            else:
                locator = (AppiumBy.ACCESSIBILITY_ID, selector)

            try:
                # wait.until(EC.presence_of_all_elements_located) can be slow or fail if some elements 
                # are partially off-screen. We'll use a safer approach: wait for at least one, 
                # then return all currently found.
                wait.until(EC.presence_of_element_located(locator))
                return self._driver.find_elements(*locator)
            except:
                # If timeout reached, return empty instead of raising
                return []

        except Exception as e:
            logger.warning(f"📱 Elements search failed: {selector} — {e}")
            return []

    def _attempt_self_healing(self, original_selector: str, step_data: dict) -> str:
        if not step_data or not self._driver:
            return None

        # Consider the flow settings passed via execute_step properties or a global _settings flag
        if not self._settings.get('enable_self_healing', True):
            return None

        from app.services.mobile_healing_service import MobileHealingEngine

        try:
            page_source = self._driver.page_source
        except Exception:
            return None

        return MobileHealingEngine.heal_selector(
            page_source=page_source,
            original_selector=original_selector,
            step_data=step_data
        )

    def _find_element(self, selector: str, timeout_ms: int = 5000, step_data: dict = None):
        """Dynamic multi-strategy element locator with polling until timeout_ms expires."""
        if not self._driver or not selector:
            return None
        
        try:
            from appium.webdriver.common.appiumby import AppiumBy
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            import time
            import re
            
            clean_selector = " ".join(str(selector).split()) 
            if not clean_selector:
                return None

            start_time = time.time()
            max_duration = max(1.0, timeout_ms / 1000.0)
            deadline = start_time + max_duration
            
            fast_wait = WebDriverWait(self._driver, 0.4)

            scrolled_attempt = False
            alert_check_attempted = False
            while time.time() < deadline:
                elapsed = time.time() - start_time

                # If element not found after 4s, check if an unexpected native system dialog/alert is blocking the screen
                if elapsed > 4.0 and not alert_check_attempted:
                    alert_check_attempted = True
                    try:
                        from app.services.mobile_diagnostics_service import MobileDiagnosticsService
                        if MobileDiagnosticsService.dismiss_system_alerts_if_present(self._driver):
                            time.sleep(0.5)
                    except Exception:
                        pass

                # Only perform gentle scroll down if element is not found after at least 8s and 40% duration,
                # ensuring initial screen loading / splash screen is not disturbed by inadvertent gestures
                if elapsed > 8.0 and elapsed > (max_duration * 0.4) and not scrolled_attempt:
                    scrolled_attempt = True
                    try:
                        size = self._driver.get_window_size()
                        cx_scr, h_scr = size['width'] // 2, size['height']
                        self._driver.swipe(cx_scr, int(h_scr * 0.7), cx_scr, int(h_scr * 0.35), 350)
                        time.sleep(0.25)
                    except Exception:
                        pass

                # 0. Accessibility ID Primary (Direct lookup for Accessibility IDs like 'welcome_button', 'Abastecer')
                try:
                    el = fast_wait.until(EC.presence_of_element_located((AppiumBy.ACCESSIBILITY_ID, clean_selector)))
                    logger.info(f"🎯 [Appium] Found element with ACCESSIBILITY_ID: {clean_selector}")
                    return el
                except Exception:
                    pass

                # 1. Check if selector is a Mobile Class Name (EditText, Button, android.widget.EditText, etc.)
                is_class_name = (
                    clean_selector in ('EditText', 'Button', 'TextView', 'ImageView', 'View', 'ViewGroup', 'CheckBox', 'RadioButton', 'ImageButton') or
                    clean_selector.startswith('android.widget.') or
                    clean_selector.startswith('android.view.') or
                    clean_selector.startswith('XCUIElementType')
                )
                if is_class_name:
                    class_target = clean_selector if ('.' in clean_selector or clean_selector.startswith('XCUI')) else f"android.widget.{clean_selector}"
                    try:
                        el = fast_wait.until(EC.presence_of_element_located((AppiumBy.CLASS_NAME, class_target)))
                        logger.info(f"🎯 [Appium] Found element with Class Name strategy: {class_target}")
                        return el
                    except Exception:
                        try:
                            el = fast_wait.until(EC.presence_of_element_located((AppiumBy.CLASS_NAME, clean_selector)))
                            logger.info(f"🎯 [Appium] Found element with Class Name strategy: {clean_selector}")
                            return el
                        except Exception:
                            pass

                # 2. Explicit XPath (starts with / or ()
                # IMPORTANT: if selector is an XPath expression, ONLY use XPath strategy.
                # Strategies 3-7 embed the raw selector as literal text/ID which corrupts complex
                # XPath expressions (e.g. from TextActionModal with translate/contains) and can
                # produce false-positive matches against unrelated screen elements.
                raw_selector = str(selector)
                clean_selector = " ".join(raw_selector.split())
                is_xpath_expr = clean_selector.startswith('/') or clean_selector.startswith('(')
                if is_xpath_expr:
                    # 2.0: Try exact raw XPath as passed
                    try:
                        el = fast_wait.until(EC.presence_of_element_located((AppiumBy.XPATH, raw_selector)))
                        logger.info(f"🎯 [Appium] Found element with exact raw XPath: {raw_selector}")
                        return el
                    except Exception:
                        pass

                    # 2.1: Try collapsed single-space XPath
                    try:
                        if clean_selector != raw_selector:
                            el = fast_wait.until(EC.presence_of_element_located((AppiumBy.XPATH, clean_selector)))
                            logger.info(f"🎯 [Appium] Found element with collapsed XPath: {clean_selector}")
                            return el
                    except Exception:
                        pass

                    # 2.2: Extract attribute value & try normalize-space and sub-tokens
                    try:
                        match = re.search(r"@(content-desc|text|label|name|resource-id)=['\"]([^'\"]+)['\"]", raw_selector)
                        if not match:
                            match = re.search(r"@(content-desc|text|label|name|resource-id)=['\"]([^'\"]+)['\"]", clean_selector)

                        if match:
                            attr_name, val = match.groups()
                            val_clean = " ".join(val.split())
                            val_no_tab = re.sub(r'Guia \d+ de \d+|Tab \d+ of \d+', '', val_clean, flags=re.IGNORECASE).strip()

                            # 2.2.a: normalize-space exact match (handles multiple spaces, tabs & newlines in attributes)
                            norm_xpath = f"//*[normalize-space(@{attr_name})='{val_clean}']"
                            try:
                                el = fast_wait.until(EC.presence_of_element_located((AppiumBy.XPATH, norm_xpath)))
                                logger.info(f"🎯 [Appium] Found element with normalize-space XPath: {norm_xpath}")
                                return el
                            except Exception:
                                pass

                            # 2.2.b: normalize-space without tab suffix ('Guia 2 de 2')
                            if val_no_tab and val_no_tab != val_clean:
                                norm_notab_xpath = f"//*[contains(normalize-space(@{attr_name}), '{val_no_tab}') or contains(normalize-space(@text), '{val_no_tab}')]"
                                try:
                                    el = fast_wait.until(EC.presence_of_element_located((AppiumBy.XPATH, norm_notab_xpath)))
                                    logger.info(f"🎯 [Appium] Found element with normalize-space no-tab XPath: {norm_notab_xpath}")
                                    return el
                                except Exception:
                                    pass

                            # 2.2.c: Case-insensitive normalize-space contains match
                            search_val = val_no_tab if val_no_tab else val_clean
                            search_lower = search_val.lower()
                            ci_xpath = f"//*[contains(translate(normalize-space(@{attr_name}), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{search_lower}') or contains(translate(normalize-space(@text), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{search_lower}')]"
                            try:
                                el = fast_wait.until(EC.presence_of_element_located((AppiumBy.XPATH, ci_xpath)))
                                logger.info(f"🎯 [Appium] Found element with case-insensitive normalize-space XPath: {ci_xpath}")
                                return el
                            except Exception:
                                pass

                            # 2.2.d: Sub-word token fallback if multiple words present
                            words = [w for w in re.split(r'\s+', search_val) if len(w) >= 3 and w.lower() not in ('guia', 'tab', 'de', 'of')]
                            if words:
                                first_word = words[0].lower()
                                word_xpath = f"//*[contains(translate(normalize-space(@{attr_name}), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{first_word}') or contains(translate(normalize-space(@text), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{first_word}')]"
                                try:
                                    el = fast_wait.until(EC.presence_of_element_located((AppiumBy.XPATH, word_xpath)))
                                    logger.info(f"🎯 [Appium] Found element with sub-word normalize-space XPath: {word_xpath}")
                                    return el
                                except Exception:
                                    pass
                    except Exception as e_norm:
                        logger.debug(f"Normalize space fallback error: {e_norm}")

                    # 2.3: Extract quoted string and try ACCESSIBILITY_ID or combined XPath
                    try:
                        m2 = re.search(r"['\"]([^'\"]{2,})['\"]", clean_selector)
                        if m2:
                            val2 = m2.group(1)
                            val2_no_tab = re.sub(r'Guia \d+ de \d+|Tab \d+ of \d+', '', val2, flags=re.IGNORECASE).strip()
                            try:
                                el = fast_wait.until(EC.presence_of_element_located((AppiumBy.ACCESSIBILITY_ID, val2_no_tab or val2)))
                                logger.info(f"🎯 [Appium] Found element via extracted XPath quote Accessibility ID: '{val2_no_tab or val2}'")
                                return el
                            except Exception:
                                combo = f"//*[@content-desc='{val2_no_tab}' or @text='{val2_no_tab}' or contains(normalize-space(@content-desc), '{val2_no_tab}') or contains(normalize-space(@text), '{val2_no_tab}')]"
                                el = fast_wait.until(EC.presence_of_element_located((AppiumBy.XPATH, combo)))
                                logger.info(f"🎯 [Appium] Found element via extracted XPath quote combined: {combo}")
                                return el
                    except Exception:
                        pass
                    elapsed_now = time.time() - start_time
                    time.sleep(0.1 if elapsed_now < 1.5 else (0.2 if elapsed_now < 3.0 else 0.3))
                    continue  # Skip all non-XPath strategies below; keep polling until timeout

                # 3. Package Resource ID (contains :id/ or /)
                if ':id/' in clean_selector or '/' in clean_selector:
                    try:
                        return fast_wait.until(EC.presence_of_element_located((AppiumBy.ID, clean_selector)))
                    except Exception:
                        pass

                # 4. Adaptive Combined XPath (Matches @content-desc, @text, @label, @name, @resource-id, @hint)
                combined_xpath = (
                    f"//*[@content-desc='{clean_selector}' or @text='{clean_selector}' or @label='{clean_selector}' or @name='{clean_selector}' or @resource-id='{clean_selector}' or "
                    f"contains(@content-desc, '{clean_selector}') or contains(@text, '{clean_selector}') or contains(@label, '{clean_selector}') or contains(@resource-id, '{clean_selector}') or "
                    f"@hint='{clean_selector}' or @placeholder='{clean_selector}']"
                )
                try:
                    el = fast_wait.until(EC.presence_of_element_located((AppiumBy.XPATH, combined_xpath)))
                    logger.info(f"🎯 [Appium] Found element with adaptive combined XPath: {combined_xpath}")
                    return el
                except Exception:
                    pass

                # 4.1 Sub-Token Extraction for identifiers with underscores/hyphens (e.g. wellcome_button -> wellcome)
                if '_' in clean_selector or '-' in clean_selector:
                    sub_tokens = [t for t in re.split(r'[_:\-]+', clean_selector) if len(t) >= 3 and t.lower() not in ('btn', 'button', 'txt', 'text', 'lbl', 'label')]
                    for sub in sub_tokens:
                        sub_xpath = f"//*[contains(@resource-id, '{sub}') or contains(@content-desc, '{sub}') or contains(@text, '{sub}') or contains(@name, '{sub}')]"
                        try:
                            el = fast_wait.until(EC.presence_of_element_located((AppiumBy.XPATH, sub_xpath)))
                            logger.info(f"🎯 [Appium] Found element via sub-token XPath: {sub_xpath}")
                            return el
                        except Exception:
                            pass

                # 5. Short Resource ID
                try:
                    return fast_wait.until(EC.presence_of_element_located((AppiumBy.ID, clean_selector)))
                except Exception:
                    pass

                # 6. Dynamic Multiline & Token Fallback Strategy (Strips accessibility tab suffixes like 'Guia 1 de 2')
                try:
                    raw_sel = str(selector)
                    clean_no_tab = re.sub(r'Guia \d+ de \d+|Tab \d+ of \d+', '', raw_sel, flags=re.IGNORECASE).strip()
                    lines = [l.strip() for l in clean_no_tab.replace(r'\n', '\n').split('\n') if l.strip()]
                    
                    candidates = []
                    if lines:
                        last_line = " ".join(lines[-1].split())
                        first_line = " ".join(lines[0].split())
                        first_two = " ".join(" ".join(lines[:2]).split())
                        if len(last_line) >= 2 and last_line not in candidates:
                            candidates.append(last_line)
                        if len(first_two) >= 3 and first_two not in candidates:
                            candidates.append(first_two)
                        if len(first_line) >= 3 and first_line not in candidates:
                            candidates.append(first_line)
                    
                    for candidate in candidates:
                        token_xpath = f"//*[contains(@content-desc, '{candidate}') or contains(@text, '{candidate}') or contains(@label, '{candidate}') or contains(@name, '{candidate}')]"
                        try:
                            el = fast_wait.until(EC.presence_of_element_located((AppiumBy.XPATH, token_xpath)))
                            logger.info(f"🎯 [Appium] Found element with multiline/tab fallback token XPath: {token_xpath}")
                            return el
                        except Exception:
                            pass
                except Exception:
                    pass

                # 7. UIAutomator fallback for Android (plain-text selectors only)
                # Skip if selector looks like an XPath or resource-id to avoid corrupt UiSelector queries
                if not (':id/' in clean_selector or '/' in clean_selector):
                    try:
                        partial_text = clean_selector[:30]
                        uia_strategy = f'new UiSelector().textContains("{partial_text}")'
                        return fast_wait.until(EC.presence_of_element_located((AppiumBy.ANDROID_UIAUTOMATOR, uia_strategy)))
                    except Exception:
                        pass

                # Adaptive polling: 100ms early for fast local response, back off to 300ms
                elapsed_now = time.time() - start_time
                time.sleep(0.1 if elapsed_now < 1.5 else (0.2 if elapsed_now < 3.0 else 0.3))

            # 8. Self-Healing fallback activated automatically when exact lookups fail
            healed_sel = self._attempt_self_healing(clean_selector, step_data)
            if healed_sel:
                try:
                    heal_loc = (AppiumBy.XPATH, healed_sel) if (healed_sel.startswith('/') or healed_sel.startswith('(')) else (AppiumBy.ACCESSIBILITY_ID, healed_sel)
                    el = WebDriverWait(self._driver, 1.5).until(EC.presence_of_element_located(heal_loc))
                    logger.info(f"🎉 [Self-Healing] Successfully found element with healed selector: {healed_sel}")
                    return el
                except Exception:
                    pass

            # 9. Step-based Dynamic Token Extraction (Extracted directly from step name/description)
            try:
                if step_data:
                    step_props = step_data.get('properties', {})
                    step_name = step_data.get('name', '')
                    step_desc = step_data.get('description', '')
                    
                    # Priority: use target_text from properties (set by TextActionModal) for direct match
                    target_text = step_props.get('target_text', '')
                    if target_text:
                        target_lower = target_text.lower()
                        target_xpath = (
                            f"//*[contains(translate(@text, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{target_lower}') or "
                            f"contains(translate(@content-desc, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{target_lower}') or "
                            f"contains(translate(@label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{target_lower}')]"
                        )
                        try:
                            el = WebDriverWait(self._driver, 1.5).until(EC.presence_of_element_located((AppiumBy.XPATH, target_xpath)))
                            logger.info(f"🎯 [Target Text Fallback] Found element matching target_text='{target_text}'")
                            if isinstance(step_data, dict): step_data['_used_fallback'] = True
                            return el
                        except Exception:
                            pass
                        # Also try UIAutomator for Android with exact target_text
                        try:
                            uia = f'new UiSelector().textContains("{target_text}")'
                            el = WebDriverWait(self._driver, 1.0).until(EC.presence_of_element_located((AppiumBy.ANDROID_UIAUTOMATOR, uia)))
                            logger.info(f"🎯 [Target Text Fallback] Found element via UIAutomator textContains='{target_text}'")
                            if isinstance(step_data, dict): step_data['_used_fallback'] = True
                            return el
                        except Exception:
                            pass

                    # Fallback: tokenize step name/description, strip punctuation to avoid XPath syntax errors
                    combined_text = f"{step_name} {step_desc}"
                    words = [re.sub(r'[^\w]', '', w) for w in re.split(r'[\s_\-/\"\']+', combined_text)]
                    words = [w for w in words if len(w) >= 3]
                    stopwords = {'tap', 'click', 'clique', 'toque', 'button', 'botao', 'btn', 'element', 'elemento',
                                 'the', 'em', 'no', 'na', 'para', 'type', 'digitar', 'assert', 'verificar'}
                    dynamic_keywords = [w for w in words if w.lower() not in stopwords]
                    for kw in dynamic_keywords:
                        kw_xpath = f"//*[@clickable='true' and (contains(translate(@text, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{kw.lower()}') or contains(translate(@content-desc, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{kw.lower()}'))]"
                        try:
                            el = fast_wait.until(EC.presence_of_element_located((AppiumBy.XPATH, kw_xpath)))
                            logger.info(f"🚀 [Step Keyword Fallback] Found element dynamically matching step keyword '{kw}'")
                            if isinstance(step_data, dict): step_data['_used_fallback'] = True
                            return el
                        except Exception:
                            pass
            except Exception as fb_err:
                logger.warning(f"⚠️ [Step Keyword Fallback] Failed: {fb_err}")

            # Element definitively not found — raise to propagate error correctly
            raise Exception(f"Element '{clean_selector}' not found after {timeout_ms}ms.")

        except Exception as e:
            logger.warning(f"📱 Element not found: {selector} after {timeout_ms}ms — {e}")
            raise

    # ------------------------------------------------------------------
    # Vision AI Button Detection
    # ------------------------------------------------------------------

    def detect_button_coordinates_from_screenshot(self, screenshot_bytes: bytes = None, container_bounds: dict = None, target_text: str = None) -> tuple[int, int] | None:
        """
        Analisa a captura de tela (PNG) e detecta visualmente o contorno do botão
        (retângulo preenchido de cor destacada dentro do container).
        Delega para o MobileVisionService especializado.
        """
        if not self._driver and not screenshot_bytes:
            return None

        from app.services.mobile_vision_service import MobileVisionService

        if not screenshot_bytes and self._driver:
            try:
                screenshot_bytes = self._driver.get_screenshot_as_png()
            except Exception:
                screenshot_bytes = None

        if not screenshot_bytes:
            return None

        page_source = None
        if self._driver:
            try:
                page_source = self._driver.page_source
            except Exception:
                pass

        return MobileVisionService.detect_button_coordinates(
            screenshot_bytes=screenshot_bytes,
            container_bounds=container_bounds,
            target_text=target_text,
            page_source=page_source
        )

    # ------------------------------------------------------------------
    # Video Recording Helpers
    # ------------------------------------------------------------------

    def _start_screen_recording(self):
        """Inicia a gravação da tela do dispositivo móvel via Appium."""
        if not self._driver or not self._video_dir:
            return
        try:
            self._driver.start_recording_screen()
            self._is_recording = True
            logger.info("🎥 [Appium] Gravação de vídeo de tela iniciada com sucesso.")
        except Exception as e:
            self._is_recording = False
            logger.warning(f"⚠️ [Appium] Não foi possível iniciar a gravação de tela: {e}")

    def _stop_screen_recording(self):
        """Para a gravação da tela do dispositivo móvel e salva como MP4."""
        if not self._driver or not self._is_recording or not self._video_dir:
            return
        try:
            import base64
            import os
            raw_b64 = self._driver.stop_recording_screen()
            self._is_recording = False
            if raw_b64:
                os.makedirs(self._video_dir, exist_ok=True)
                video_path = os.path.join(self._video_dir, "mobile_execution.mp4")
                with open(video_path, "wb") as f:
                    f.write(base64.b64decode(raw_b64))
                logger.info(f"🎥 [Appium] Vídeo de gravação salvo com sucesso em: {video_path}")
        except Exception as e:
            logger.warning(f"⚠️ [Appium] Erro ao parar ou salvar vídeo de gravação: {e}")

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def close_sync(self):
        if self._driver:
            try:
                self._driver.hide_keyboard()
            except Exception:
                pass
            if self._is_recording:
                self._stop_screen_recording()
            try:
                pkg = self._get_package_name()
                if pkg:
                    try:
                        self._driver.terminate_app(pkg)
                        logger.info(f"📱 [Appium] Terminated app '{pkg}' on session close.")
                    except Exception as te:
                        logger.debug(f"terminate_app on close: {te}")
            except Exception:
                pass
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None
            logger.info(f"📱 [{self._provider}] Driver session closed.")

        if getattr(self, '_acquired_device_info', None):
            try:
                from app.services.mobile_device_pool_service import MobileDevicePoolService
                MobileDevicePoolService.release_device(
                    device_id=self._acquired_device_info.get("device_id"),
                    system_port=self._acquired_device_info.get("system_port"),
                    mjpeg_port=self._acquired_device_info.get("mjpeg_port")
                )
                logger.info("📱 [Appium] Released device and ports back to Device Pool.")
            except Exception as pool_rel_err:
                logger.debug(f"Device pool release notice: {pool_rel_err}")
            self._acquired_device_info = None
