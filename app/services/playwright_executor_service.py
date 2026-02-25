import logging
import json
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

class PlaywrightExecutorService:
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def start(self, video_dir: str = None):
        """Starts the playwright engine and browser instance."""
        if not self._playwright:
            self._playwright = sync_playwright().start()
        
        if not self._browser:
            self._browser = self._playwright.chromium.launch(headless=True)
            logger.info("Playwright Browser launched (Sync/Headless)")
        
        if not self._context:
            context_args = {}
            if video_dir:
                context_args["record_video_dir"] = video_dir
                context_args["record_video_size"] = {"width": 1280, "height": 720}
                logger.info(f"📹 Video Recording enabled in: {video_dir}")
                
            self._context = self._browser.new_context(**context_args)
        
        if not self._page:
            self._page = self._context.new_page()

    def stop(self):
        """Shuts down the browser and playwright engine."""
        if self._page:
            self._page.close()
            self._page = None
            
        if self._context:
            self._context.close()
            self._context = None
            
        if self._browser:
            self._browser.close()
            self._browser = None
        
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
            logger.info("Playwright Browser stopped")

    def execute_step(self, step):
        """
        Executes a single E2E step using the persistent page.
        Returns a result dict.
        """
        step_type = step.get('type')
        name = step.get('name', 'Untitled Step')
        properties = step.get('properties', {})
        timeout = properties.get('timeout', 30000)
        
        logger.info(f"Executing E2E step: {name} ({step_type})")
        
        step_result = {
            "status": 200,
            "reason": "OK",
            "text": "",
            "duration": 0
        }
        
        start_time = 1000 # Placeholder for time.time() * 1000 logic in caller if needed
        # But we'll just track text/log here
        
        try:
            self.start() # Ensure started
            
            if step_type == 'browser':
                url = properties.get('value', '').strip()
                if url:
                    # Add protocol if missing
                    if not url.startswith(('http://', 'https://')):
                        url = f"http://{url}"
                        
                    self._page.goto(url, timeout=timeout)
                    step_result["text"] = f"Navigated to {url}"
                else:
                    raise ValueError("URL is missing for browser step")
            
            elif step_type == 'click':
                selector = properties.get('selector', '')
                if selector:
                    self._page.click(selector, timeout=timeout)
                    step_result["text"] = f"Clicked element: {selector}"
                else:
                    raise ValueError("Selector is missing for click step")
                    
            elif step_type == 'type':
                selector = properties.get('selector', '')
                value = properties.get('value', '')
                if selector:
                    self._page.fill(selector, value, timeout=timeout)
                    step_result["text"] = f"Typed text into {selector}"
                else:
                    raise ValueError("Selector is missing for type step")
                    
            elif step_type == 'wait':
                ms = int(properties.get('value', 1000))
                self._page.wait_for_timeout(ms)
                step_result["text"] = f"Waited for {ms}ms"
                
            elif step_type == 'hover':
                selector = properties.get('selector', '')
                if selector:
                    self._page.hover(selector, timeout=timeout)
                    step_result["text"] = f"Hovered over {selector}"
                else:
                    raise ValueError("Selector is missing for hover step")
                    
            elif step_type == 'scroll':
                selector = properties.get('selector', '')
                if selector:
                    self._page.locator(selector).scroll_into_view_if_needed(timeout=timeout)
                    step_result["text"] = f"Scrolled to {selector}"
                else:
                    value = properties.get('value', '0').lower()
                    if value == 'bottom':
                        self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        step_result["text"] = "Scrolled to bottom"
                    elif value == 'top':
                        self._page.evaluate("window.scrollTo(0, 0)")
                        step_result["text"] = "Scrolled to top"
                    else:
                        self._page.evaluate(f"window.scrollBy(0, {value})")
                        step_result["text"] = f"Scrolled by {value}px"
                        
            elif step_type == 'keypress':
                key = properties.get('value', 'Enter')
                selector = properties.get('selector', '')
                if selector:
                    self._page.press(selector, key, timeout=timeout)
                    step_result["text"] = f"Pressed {key} on {selector}"
                else:
                    self._page.keyboard.press(key)
                    step_result["text"] = f"Pressed {key} globally"
                    
            elif step_type == 'assert':
                selector = properties.get('selector', '')
                operator = properties.get('operator', 'visible').lower()
                expected_value = properties.get('value', '')
                
                if not selector and not expected_value:
                    raise ValueError("Assertion needs either a selector or a value")
                
                if operator == 'visible':
                    if selector:
                        self._page.wait_for_selector(selector, state="visible", timeout=timeout)
                        step_result["text"] = f"Assertion passed: {selector} is visible"
                    else:
                        # If no selector, assume we are checking page content
                        if expected_value in self._page.content():
                            step_result["text"] = f"Assertion passed: text '{expected_value}' found on page"
                        else:
                            raise ValueError(f"Text '{expected_value}' not found on page")
                            
                elif operator == 'hidden':
                    if selector:
                        self._page.wait_for_selector(selector, state="hidden", timeout=timeout)
                        step_result["text"] = f"Assertion passed: {selector} is hidden"
                    else:
                        raise ValueError("Selector is missing for 'hidden' assertion")
                        
                elif operator == 'equals':
                    if selector:
                        actual_value = self._page.inner_text(selector, timeout=timeout)
                        if actual_value == expected_value:
                            step_result["text"] = f"Assertion passed: text for {selector} equals '{expected_value}'"
                        else:
                            raise ValueError(f"Assertion failed: expected '{expected_value}', but found '{actual_value}'")
                    else:
                        raise ValueError("Selector is missing for 'equals' assertion")
                        
                elif operator == 'contains':
                    if selector:
                        actual_value = self._page.inner_text(selector, timeout=timeout)
                        if expected_value in actual_value:
                            step_result["text"] = f"Assertion passed: text for {selector} contains '{expected_value}'"
                        else:
                            raise ValueError(f"Assertion failed: '{expected_value}' not found in '{actual_value}'")
                    else:
                        if expected_value in self._page.content():
                            step_result["text"] = f"Assertion passed: page content contains '{expected_value}'"
                        else:
                            raise ValueError(f"Assertion failed: '{expected_value}' not found on page")
                        
            elif step_type == 'refresh':
                self._page.reload(timeout=timeout)
                step_result["text"] = "Page refreshed"
                
            else:
                step_result["status"] = 400
                step_result["reason"] = "Unsupported Action"
                step_result["text"] = f"Unsupported step type: {step_type}"
                
        except Exception as e:
            logger.error(f"Error executing E2E step {name}: {str(e)}")
            step_result["status"] = 500
            step_result["reason"] = "E2E Error"
            step_result["text"] = str(e)
            
        return step_result

    def get_video_path(self):
        """Returns the path to the recorded video if recording was enabled."""
        if self._page and self._page.video:
            return self._page.video.path()
        return None

# We will instantiate this per-flow in FlowExecutorService
