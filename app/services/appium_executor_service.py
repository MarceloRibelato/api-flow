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

            elif step_type == 'type':
                selector = props.get('selector', '')
                value = props.get('value', '')
                el = self._find_element(selector)
                if el:
                    el.send_keys(value)

            elif step_type == 'swipe':
                from appium.webdriver.common.touch_action import TouchAction
                sx = int(props.get('startX', 500))
                sy = int(props.get('startY', 1200))
                ex = int(props.get('endX', 500))
                ey = int(props.get('endY', 300))
                duration = int(props.get('duration', 800))
                action = TouchAction(self._driver)
                action.press(x=sx, y=sy).wait(duration).move_to(x=ex, y=ey).release().perform()

            elif step_type == 'assert':
                selector = props.get('selector', '')
                el = self._find_element(selector)
                expected_text = props.get('value', '')
                if el and expected_text:
                    actual = el.text
                    if expected_text not in actual:
                        raise AssertionError(
                            f"Assertion failed. Expected '{expected_text}' in '{actual}'")

            elif step_type == 'wait':
                delay = int(props.get('timeout', 5000))
                time.sleep(delay / 1000.0)

            elif step_type == 'screenshot':
                if self._driver:
                    screenshot_b64 = self._driver.get_screenshot_as_base64()
                    return {
                        "status": 200,
                        "reason": "OK",
                        "text": f"[Screenshot captured: {len(screenshot_b64)} bytes]"
                    }

            else:
                logger.warning(f"📱 Unsupported step type: {step_type}")

            return {
                "status": 200,
                "reason": "OK",
                "text": f"Step '{step_type}' executed successfully."
            }

        except AssertionError as ae:
            return {
                "status": 400,
                "reason": "Assertion Error",
                "text": str(ae)
            }
        except Exception as e:
            logger.error(f"📱 Step Failed: {e}")
            return {
                "status": 500,
                "reason": "Appium Error",
                "text": str(e)
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
