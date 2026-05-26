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
            from appium.options.common.base import AppiumOptions

            options = AppiumOptions()
            options.platform_name = "Android"
            options.set_capability("appium:deviceName", self._settings["device_name"])
            if self._settings.get("platform_version"):
                options.set_capability("appium:platformVersion", self._settings["platform_version"])
            if self._settings.get("app_identifier"):
                app_id = self._settings["app_identifier"]
                # Detect if it's an APK file path or an installed package name
                is_apk_path = (
                    app_id.lower().endswith('.apk') or
                    '\\' in app_id or
                    ('/' in app_id and not app_id.startswith('com.') and not app_id.startswith('br.') and not app_id.startswith('io.'))
                )
                if is_apk_path:
                    # Rewrite Windows path for Docker if needed
                    if app_id.startswith('C:\\') or app_id.startswith('c:\\'):
                        # APK must be accessible from within the container — warn but still set
                        logger.warning(f"📱 [Appium] APK path '{app_id}' is a Windows local path. "
                                       "Ensure Appium server runs on the host (not inside Docker).")
                    options.set_capability("appium:app", app_id)
                    logger.info(f"📱 [Appium] Using APK install path: {app_id}")
                else:
                    # e.g. 'br.com.sicoob' or 'com.vibra.app' or 'com.pkg/com.pkg.MainActivity'
                    if '/' in app_id:
                        pkg, activity = app_id.split('/', 1)
                        options.set_capability("appium:appPackage", pkg)
                        options.set_capability("appium:appActivity", activity)
                        # Wait for any activity to avoid splash screen timeouts
                        options.set_capability("appium:appWaitActivity", "*")
                        logger.info(f"📱 [Appium] Using package: {pkg} and activity: {activity}")
                    else:
                        options.set_capability("appium:appPackage", app_id)
                        logger.info(f"📱 [Appium] Using installed package: {app_id}")
            options.set_capability("appium:automationName", "UiAutomator2")
            options.set_capability("appium:noReset", False)
            options.set_capability("appium:newCommandTimeout", 3600)
            options.set_capability("appium:ignoreUnimportantViews", False)

            server_url = self._settings["server_url"]
            
            # Automatically route localhost to host machine when running in Docker
            if "localhost" in server_url or "127.0.0.1" in server_url:
                server_url = server_url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
                logger.info("📱 [Appium] Auto-rewriting localhost to host.docker.internal for Docker networking")

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
            from appium.options.common.base import AppiumOptions

            options = AppiumOptions()
            options.platform_name = "Android"
            s = self._settings

            options.set_capability("appium:deviceName", s["device_name"])
            if s.get("platform_version"):
                options.set_capability("appium:platformVersion", s["platform_version"])
            if s.get("app_identifier"):
                app_id = s["app_identifier"]
                is_apk_path = (
                    app_id.lower().endswith('.apk') or
                    '\\' in app_id or
                    ('/' in app_id and not app_id.startswith('com.') and not app_id.startswith('br.') and not app_id.startswith('io.'))
                )
                if is_apk_path:
                    options.set_capability("appium:app", app_id)
                else:
                    if '/' in app_id:
                        pkg, activity = app_id.split('/', 1)
                        options.set_capability("appium:appPackage", pkg)
                        options.set_capability("appium:appActivity", activity)
                        options.set_capability("appium:appWaitActivity", "*")
                    else:
                        options.set_capability("appium:appPackage", app_id)
            options.set_capability("appium:automationName", "UiAutomator2")
            options.set_capability("appium:noReset", False)
            options.set_capability("appium:newCommandTimeout", 3600)
            options.set_capability("appium:ignoreUnimportantViews", False)

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
            # Log current screen state for debugging physical device mismatches
            try:
                window_size = self._driver.get_window_size()
                logger.info(f"📱 [Appium] Screen resolution: {window_size['width']}x{window_size['height']}")
            except: pass
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
                x = props.get('x')
                y = props.get('y')
                if x is not None: x = int(x)
                if y is not None: y = int(y)
                timeout = int(props.get('timeout', 5000))
                
                el = self._find_element(selector, timeout_ms=timeout)
                if el:
                    # Robust Click: Get element location and tap the center
                    # el.click() often fails silently on some Android builds
                    loc = el.location
                    size = el.size
                    cx = loc['x'] + (size['width'] // 2)
                    cy = loc['y'] + (size['height'] // 2)
                    logger.info(f"📍 [Appium] Tapping element center: ({cx}, {cy})")
                    self._driver.tap([(cx, cy)])
                elif x is not None and y is not None:
                    logger.info(f"📍 [Appium] Selector '{selector}' failed. Falling back to explicit coordinates ({x}, {y})")
                    self._driver.tap([(x, y)])
                else:
                    raise Exception(f"Element '{selector}' not found for click and no coordinates available as fallback.")
                
                # Small wait for UI response
                time.sleep(0.5)

            elif step_type == 'long_press':
                from appium.webdriver.common.touch_action import TouchAction
                selector = props.get('selector', '')
                duration_ms = int(props.get('duration', 2000))
                timeout = int(props.get('timeout', 5000))
                el = self._find_element(selector, timeout_ms=timeout)
                if el:
                    action = TouchAction(self._driver)
                    action.long_press(el, duration=duration_ms).release().perform()
                else:
                    raise Exception(f"Element '{selector}' not found for long_press after {timeout}ms")

            elif step_type == 'type':
                selector = props.get('selector', '')
                value = props.get('value', '')
                x = props.get('x')
                y = props.get('y')
                if x is not None: x = int(x)
                if y is not None: y = int(y)
                timeout = int(props.get('timeout', 5000))
                
                el = self._find_element(selector, timeout_ms=timeout)
                success = False
                
                if el:
                    try:
                        # 1. Fast Path: Try direct input (works for standard EditText)
                        el.send_keys(value)
                        success = True
                        logger.info(f"⌨️ [Appium] Direct input success")
                    except Exception:
                        # 2. Robust Fallback: Tap center then type (works for Flutter/Hybrid)
                        try:
                            loc = el.location
                            size = el.size
                            cx = loc['x'] + (size['width'] // 2)
                            cy = loc['y'] + (size['height'] // 2)
                            self._driver.tap([(cx, cy)])
                            time.sleep(0.4)
                            self._driver.execute_script('mobile: type', {'text': value})
                            success = True
                            logger.info(f"⌨️ [Appium] Enhanced type success (Tap+Type)")
                        except Exception as e:
                            logger.warning(f"⚠️ [Appium] Fast and Enhanced type failed: {e}")
                
                if not success:
                    if x is not None and y is not None:
                        logger.info(f"📍 [Appium] Falling back to coordinate type at ({x}, {y})")
                        self._driver.tap([(x, y)])
                        time.sleep(0.8) # Wait for focus/keyboard
                        try:
                            self._driver.execute_script('mobile: type', {'text': value})
                            success = True
                        except Exception as e2:
                            logger.error(f"❌ [Appium] Global type failed: {e2}")
                            raise e2
                    else:
                        raise Exception(f"Element '{selector}' cannot receive text and no coordinates available as fallback.")

            elif step_type == 'clear_field':
                selector = props.get('selector', '')
                timeout = int(props.get('timeout', 5000))
                el = self._find_element(selector, timeout_ms=timeout)
                if el:
                    el.clear()
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

            # --- Capture screenshot on success if requested ---
            screenshot_b64 = None
            if capture_screenshot:
                try:
                    # Small grace period for UI stabilization before screenshot
                    time.sleep(0.2)
                    screenshot_b64 = self._driver.get_screenshot_as_base64()
                except Exception:
                    pass

            return {
                "status": 200,
                "reason": "OK",
                "text": f"Step '{step_type}' executed successfully.",
                "screenshot_b64": screenshot_b64
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
            if selector.startswith('/') or selector.startswith('//'):
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

    def _find_element(self, selector: str, timeout_ms: int = 5000):
        """Localiza elemento por ID, accessibility id, xpath ou text usando timeout explícito e fallback."""
        if not self._driver or not selector:
            return None
        try:
            from appium.webdriver.common.appiumby import AppiumBy
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            
            # --- 0. Normalize Selector (Handle multi-line or extra spaces) ---
            # Remove newlines and trim to increase match chances on physical devices
            clean_selector = " ".join(selector.split()) 
            
            timeout_s = max(0.5, timeout_ms / 1000.0)
            wait = WebDriverWait(self._driver, timeout_s)
            
            # 1. Detect proper locator strategy
            if selector.startswith('/') or selector.startswith('//'):
                locator = (AppiumBy.XPATH, selector)
            elif ':id/' in selector or '/' in selector:
                # Common Android resource-id pattern
                locator = (AppiumBy.ID, selector)
            else:
                # Default to accessibility ID (content-desc)
                locator = (AppiumBy.ACCESSIBILITY_ID, selector)
                
            try:
                el = wait.until(EC.presence_of_element_located(locator))
                logger.info(f"🎯 [Appium] Found element with primary strategy {locator[0]}")
                return el
            except Exception:
                # Fallback: if ID/AccessibilityID fails, try the other one briefly
                fallback_timeout = 1.5
                short_wait = WebDriverWait(self._driver, fallback_timeout)
                
                # If we tried ID, try Accessibility ID now
                if locator[0] == AppiumBy.ID:
                    try: 
                        el = short_wait.until(EC.presence_of_element_located((AppiumBy.ACCESSIBILITY_ID, selector)))
                        logger.info(f"🎯 [Appium] Found with fallback: Accessibility ID")
                        return el
                    except: pass
                # If we tried Accessibility ID, try ID now
                elif locator[0] == AppiumBy.ACCESSIBILITY_ID:
                    try: 
                        el = short_wait.until(EC.presence_of_element_located((AppiumBy.ID, selector)))
                        logger.info(f"🎯 [Appium] Found with fallback: ID")
                        return el
                    except: pass
                
                # 3. Final fallback strategy: Combined attribute search (High Performance)
                # Instead of looping through strategies, we use a single broad XPath search
                # to reduce network round-trips between the backend and Appium.

                partial_text = clean_selector[:30] if len(clean_selector) > 30 else clean_selector
                
                # Combine multiple possible locations in one query
                combined_xpath = (
                    f"//*[@text='{selector}' or @content-desc='{selector}' or "
                    f"contains(@text, '{partial_text}') or contains(@content-desc, '{partial_text}') or "
                    f"@hint='{selector}' or @placeholder='{selector}']"
                )

                try:
                    el = short_wait.until(EC.presence_of_element_located((AppiumBy.XPATH, combined_xpath)))
                    logger.info(f"🎯 [Appium] Found with combined adaptive XPath")
                    return el
                except:
                    # Final attempt: UIAutomator (sometimes more reliable than XPath on Android)
                    try:
                        uia_strategy = f'new UiSelector().textContains("{partial_text}")'
                        el = short_wait.until(EC.presence_of_element_located((AppiumBy.ANDROID_UIAUTOMATOR, uia_strategy)))
                        logger.info(f"🎯 [Appium] Found with UIAutomator fallback")
                        return el
                    except:
                        pass
                    
                raise # Re-raise if all fail
                
        except Exception as e:
            logger.warning(f"📱 Element not found: {selector} after {timeout_ms}ms — {e}")
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
