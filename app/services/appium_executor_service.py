import logging
import json
import time

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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, video_dir: str = None, db=None, product_id: int = None):
        """Inicializa o driver mobile com as configurações do produto."""
        try:
            # Load settings from DB if available
            if db and product_id:
                from app.services.product_service import ProductService
                settings_obj = ProductService.get_mobile_settings(db, product_id)
                self._settings = {
                    "provider": settings_obj.provider or "appium_local",
                    "server_url": settings_obj.server_url or "http://localhost:4723",
                    "auth_user": settings_obj.auth_user or "",
                    "auth_token": settings_obj.auth_token or "",
                    "device_name": settings_obj.device_name or "Android Emulator",
                    "platform_version": settings_obj.platform_version or "",
                    "app_identifier": settings_obj.app_identifier or "",
                }
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
                }
                logger.warning("📱 [Appium] No DB/product_id passed — using fallback defaults.")

            # BrowserStack or SauceLabs: attach credentials to URL
            if self._provider in ("browserstack", "saucelabs"):
                self._start_cloud_driver()
            elif self._provider == "playwright_adb":
                self._start_playwright_adb()
            else:
                self._start_appium_local()

        except Exception as e:
            logger.error(f"📱 [Appium] Failed to start executor: {e}")
            raise

    def _start_appium_local(self):
        """Conecta ao servidor Appium local via webdriver.Remote."""
        try:
            from appium import webdriver as appium_webdriver
            from appium.options import AppiumOptions

            options = AppiumOptions()
            options.platform_name = "Android"
            options.set_capability("deviceName", self._settings["device_name"])
            if self._settings.get("platform_version"):
                options.set_capability("platformVersion", self._settings["platform_version"])
            if self._settings.get("app_identifier"):
                options.set_capability("app", self._settings["app_identifier"])
            options.set_capability("automationName", "UiAutomator2")
            options.set_capability("noReset", True)

            server_url = self._settings["server_url"]
            if not server_url.endswith("/wd/hub"):
                server_url = server_url.rstrip("/") + "/wd/hub"

            self._driver = appium_webdriver.Remote(
                command_executor=server_url,
                options=options
            )
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
            from appium.options import AppiumOptions

            options = AppiumOptions()
            options.platform_name = "Android"
            s = self._settings

            options.set_capability("deviceName", s["device_name"])
            if s.get("platform_version"):
                options.set_capability("platformVersion", s["platform_version"])
            if s.get("app_identifier"):
                options.set_capability("app", s["app_identifier"])
            options.set_capability("automationName", "UiAutomator2")

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
            logger.info(f"📱 [{self._provider}] Cloud session started — device: {s['device_name']}")

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

    def execute_step(self, step_data: dict, capture_screenshot: bool = False, db=None, user_id=None) -> dict:
        """Executa um passo nativo no Appium/Cloud."""
        step_type = step_data.get('type')
        props = step_data.get('properties', {})
        step_name = step_data.get('name', step_type)

        logger.info(f"📱 [{self._provider or 'appium'}] Executing step: {step_type} — {step_name}")

        try:
            if not self._driver:
                logger.warning("📱 No active Appium driver — step simulated.")
                time.sleep(0.5)
                return {
                    "status": 200,
                    "reason": "OK (simulated)",
                    "text": f"[SIMULATED] Step '{step_type}' — driver not connected."
                }

            if step_type == 'tap':
                selector = props.get('selector') or props.get('value', '')
                el = self._find_element(selector)
                if el:
                    el.click()

            elif step_type == 'long_press':
                from appium.webdriver.common.touch_action import TouchAction
                selector = props.get('selector', '')
                duration_ms = int(props.get('duration', 2000))
                el = self._find_element(selector)
                if el:
                    action = TouchAction(self._driver)
                    action.long_press(el, duration=duration_ms).release().perform()

            elif step_type == 'type':
                selector = props.get('selector', '')
                value = props.get('value', '')
                el = self._find_element(selector)
                if el:
                    el.send_keys(value)

            elif step_type == 'clear_field':
                selector = props.get('selector', '')
                el = self._find_element(selector)
                if el:
                    el.clear()

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
                selector = props.get('selector', '')
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
                src = self._find_element(src_sel)
                tgt = self._find_element(tgt_sel)
                if src and tgt:
                    action = TouchAction(self._driver)
                    action.long_press(src, duration=duration_ms).move_to(tgt).release().perform()

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
                self._driver.press_keycode(4)  # Android KEYCODE_BACK

            elif step_type == 'home_button':
                self._driver.press_keycode(3)  # Android KEYCODE_HOME

            elif step_type == 'set_location':
                lat = float(props.get('latitude', -23.5505))
                lon = float(props.get('longitude', -46.6333))
                self._driver.set_location(lat, lon, 0)
                logger.info(f"📍 Location set to ({lat}, {lon})")

            elif step_type == 'assert':
                selector = props.get('selector', '')
                operator = props.get('operator', 'visible')
                expected = props.get('value', '')
                el = self._find_element(selector)

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

            elif step_type == 'wait':
                delay = int(props.get('timeout', 1000))
                time.sleep(delay / 1000.0)

            elif step_type == 'screenshot':
                if self._driver:
                    screenshot_b64 = self._driver.get_screenshot_as_base64()
                    return {
                        "status": 200,
                        "reason": "OK",
                        "text": f"[Screenshot captured: {len(screenshot_b64)} bytes]"
                    }

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
                    self._driver.execute_script('browserstack_executor: {"action": "setNetworkConditions", "arguments": {"networkProfile": "' + profile + '"}')

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

            else:
                logger.warning(f"📱 Unsupported step type: {step_type}")

            return {
                "status": 200,
                "reason": "OK",
                "text": f"Step '{step_type}' executed successfully."
            }

        except AssertionError as ae:
            # — Screenshot on Assertion Failure (Grupo 3)
            failure_screenshot = None
            if self._driver:
                try:
                    failure_screenshot = self._driver.get_screenshot_as_base64()
                    logger.info("📸 [Appium] Screenshot on failure captured.")
                except Exception:
                    pass
            return {
                "status": 400,
                "reason": "Assertion Error",
                "text": str(ae),
                "screenshot_b64": failure_screenshot,
            }
        except Exception as e:
            logger.error(f"📱 Step Failed: {e}")
            # — Screenshot on Unexpected Error
            failure_screenshot = None
            if self._driver:
                try:
                    failure_screenshot = self._driver.get_screenshot_as_base64()
                except Exception:
                    pass
            return {
                "status": 500,
                "reason": "Appium Error",
                "text": str(e),
                "screenshot_b64": failure_screenshot,
            }

    def _find_element(self, selector: str):
        """Localiza elemento por accessibility id, xpath ou text."""
        if not self._driver or not selector:
            return None
        try:
            from appium.webdriver.common.appiumby import AppiumBy
            if selector.startswith('/'):
                return self._driver.find_element(AppiumBy.XPATH, selector)
            elif selector.startswith('//'):
                return self._driver.find_element(AppiumBy.XPATH, selector)
            else:
                return self._driver.find_element(AppiumBy.ACCESSIBILITY_ID, selector)
        except Exception as e:
            logger.warning(f"📱 Element not found: {selector} — {e}")
            return None

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def close(self):
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None
            logger.info(f"📱 [{self._provider}] Driver session closed.")
