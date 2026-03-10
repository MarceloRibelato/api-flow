import logging
import json
import time as _time
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# Extensions / patterns to ignore when capturing network calls (static assets & noise)
_IGNORED_EXTENSIONS = (
    # Images
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico', '.bmp', '.avif',
    # Fonts
    '.woff', '.woff2', '.ttf', '.otf', '.eot',
    # Code / styles
    '.css', '.js', '.ts', '.map', '.mjs',
    # Documents / markup
    '.html', '.htm', '.xml', '.txt',
    # Media
    '.mp4', '.webm', '.ogg', '.mp3', '.wav',
)
_IGNORED_DOMAINS = (
    # Google services
    'fonts.googleapis.com', 'fonts.gstatic.com',
    'google-analytics.com', 'googletagmanager.com', 'googleapis.com/analytics',
    'accounts.google.com/gsi',
    # Icon CDNs
    'fontawesome.com', 'use.fontawesome.com', 'kit.fontawesome.com',
    'fonts.gstatic.com',
    'cdnjs.cloudflare.com', 'unpkg.com', 'jsdelivr.net',
    'materialdesignicons.com', 'iconify.io',
    # Analytics & monitoring
    'analytics', 'hotjar.com', 'clarity.ms',
    'segment.io', 'segment.com', 'mixpanel.com',
    'fullstory.com', 'logrocket.com', 'datadog',
    'newrelic.com', 'nr-data.net',
    'heap.io', 'heapanalytics.com',
    'amplitude.com', 'pendo.io',
    'intercom.io', 'intercomcdn.com',
    # Ads & tracking
    'doubleclick.net', 'adnxs.com', 'ads.', 'adservice.',
    'facebook.com/tr', 'connect.facebook.net',
    'linkedin.com/px', 'snap.licdn.com',
    # Error trackers
    'sentry.io', 'sentry-cdn.com', 'bugsnag.com', 'rollbar.com',
    # Chat / support widgets
    'tawk.to', 'crisp.chat', 'freshchat', 'zendesk.com',
    # Generic CDN noise
    'recaptcha', 'gstatic.com/recaptcha',
)

class PlaywrightExecutorService:
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._captured_requests = []   # list of captured API calls during execution
        self._pending_requests = {}    # url -> {method, start_time}

    def start(self, video_dir: str = None):
        """Starts the playwright engine and browser instance."""
        if not self._playwright:
            self._playwright = sync_playwright().start()
        
        if not self._browser:
            self._browser = self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage", 
                    "--no-sandbox", 
                    "--disable-gpu", 
                    "--disable-infobars",
                    "--disable-setuid-sandbox",
                    "--disable-web-security",
                    "--allow-running-insecure-content"
                ]
            )
            logger.info("Playwright Browser launched (Sync/Headless/Optimized)")
        
        if not self._context:
            context_args = {}
            if video_dir:
                context_args["record_video_dir"] = video_dir
                context_args["record_video_size"] = {"width": 1280, "height": 720}
                logger.info(f"📹 Video Recording enabled in: {video_dir}")
                
            self._context = self._browser.new_context(**context_args)
            self._context.set_default_timeout(30000)
            self._context.set_default_navigation_timeout(30000)
        
        if not self._page:
            self._page = self._context.new_page()
            self._install_network_listeners()

    def _should_capture(self, url: str) -> bool:
        """Return True if this URL is an API call worth capturing."""
        if not url:
            return False
        # Skip browser-internal and inline resources
        if url.startswith(('data:', 'blob:', 'chrome-extension://', 'about:')):
            return False
        lower = url.lower()
        # Strip query string before checking extension (e.g. styles.css?v=123)
        path = lower.split('?')[0].split('#')[0]
        if any(path.endswith(ext) for ext in _IGNORED_EXTENSIONS):
            return False
        # Check domain / path fragments
        if any(domain in lower for domain in _IGNORED_DOMAINS):
            return False
        return True


    def _install_network_listeners(self):
        """Attach request/response listeners to capture API calls made by the tested app."""
        def on_request(request):
            if not self._should_capture(request.url):
                return
            self._pending_requests[request.url] = {
                'method': request.method,
                'start_ms': _time.time() * 1000
            }

        def on_response(response):
            if not self._should_capture(response.url):
                return
            pending = self._pending_requests.pop(response.url, None)
            elapsed = 0
            method = response.request.method if response.request else 'GET'
            if pending:
                elapsed = int(_time.time() * 1000 - pending['start_ms'])
                method = pending['method']
            self._captured_requests.append({
                'url': response.url,
                'method': method,
                'status': response.status,
                'duration_ms': elapsed,
            })

        self._page.on('request', on_request)
        self._page.on('response', on_response)
        logger.info("🕸️  Network capture listeners installed")

    def pop_captured_requests(self):
        """Return and clear the accumulated network captures since last call."""
        captured = list(self._captured_requests)
        self._captured_requests.clear()
        self._pending_requests.clear()
        return captured

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

    def execute_step(self, step, capture_screenshot: bool = False, db=None, user_id=None):
        """
        Executes a single E2E step using the persistent page.
        Returns a result dict.
        """
        step_type = step.get('type')
        name = step.get('name', 'Untitled Step')
        properties = step.get('properties', {})
        # Get timeout from properties or default to 45s
        timeout = properties.get('timeout')
        if timeout is None:
            timeout = 45000
        else:
            try:
                timeout = int(timeout)
            except:
                timeout = 45000
        
        # Ensure a minimum timeout for Docker environments
        if timeout < 30000:
            timeout = 30000
        
        logger.info(f"Executing E2E step: {name} ({step_type}) [Timeout: {timeout}ms]")
        
        step_result = {
            "status": 200,
            "reason": "OK",
            "text": "",
            "duration": 0
        }
        
        start_time = 1000 # Placeholder for time.time() * 1000 logic in caller if needed
        # But we'll just track text/log here
        
        MAX_RETRIES = 2
        for attempt in range(MAX_RETRIES):
            try:
                self.start() # Ensure started
                
                # Resolve target (Page or Iframe)
                target = self._page
                is_iframe = properties.get('isIframe', False)
                frame_url = properties.get('frameUrl', '')
                if is_iframe and frame_url:
                    base_url = frame_url.split('?')[0]  # strip query params for robust matching
                    target = self._page.frame_locator(f"iframe[src*='{base_url}']").first

                if step_type == 'browser':
                    url = properties.get('value', '').strip()
                    if url:
                        if not url.startswith(('http://', 'https://')):
                            url = f"http://{url}"
                        
                        # ✅ Sanitize URL for Docker (e.g. localhost -> flow-frontend)
                        try:
                            from app.services.flow_executor_service import FlowExecutorService
                            sanitized_url = FlowExecutorService.sanitize_url_for_docker(url)
                            if sanitized_url != url:
                                logger.info(f"      🔧 Playwright Rewrote URL: {url} -> {sanitized_url}")
                                url = sanitized_url
                        except Exception as e:
                            logger.warning(f"      ⚠️ Could not sanitize URL for Docker: {e}")

                        # Use 'load' for better reliability in Docker when assets might be slow
                        self._page.goto(url, timeout=timeout, wait_until='load')
                        step_result["text"] = f"Navigated to {url}"
                    else:
                        raise ValueError("URL is missing for browser step")

                elif step_type == 'click':
                    selector = properties.get('selector', '')
                    if selector:
                        el = target.locator(selector).first
                        el.click(timeout=timeout)
                        # Mandatory wait for React/Dynamic UI to react
                        self._page.wait_for_timeout(100)
                        step_result["text"] = f"Clicked element: {selector}"
                    else:
                        raise ValueError("Selector is missing for click step")
                        
                elif step_type == 'type':
                    selector = properties.get('selector', '')
                    value = properties.get('value', '')
                    if selector:
                        el = target.locator(selector).first
                        el.fill(value, timeout=timeout)
                        # Mandatory wait for state update
                        self._page.wait_for_timeout(100)
                        # Mask sensitive fields
                        _sensitive = ('password', 'passwd', 'secret', 'token', 'pin', 'cvv')
                        display_value = '••••••' if any(s in selector.lower() for s in _sensitive) else (value[:60] + ('…' if len(value) > 60 else ''))
                        step_result["text"] = f"Typed '{display_value}' into {selector}"
                    else:
                        raise ValueError("Selector is missing for type step")
                        
                elif step_type == 'wait_selector':
                    selector = properties.get('selector', '')
                    if selector:
                        target.locator(selector).first.wait_for(timeout=timeout)
                        step_result["text"] = f"Element {selector} is now present"
                    else:
                        raise ValueError("Selector is missing for wait_selector step")
                    
                elif step_type == 'wait':
                    ms = int(properties.get('value', 1000))
                    self._page.wait_for_timeout(ms)
                    step_result["text"] = f"Waited for {ms}ms"
                    
                elif step_type == 'hover':
                    selector = properties.get('selector', '')
                    if selector:
                        el = target.locator(selector).first
                        el.hover(timeout=timeout)
                        step_result["text"] = f"Hovered over {selector}"
                    else:
                        raise ValueError("Selector is missing for hover step")
                        
                elif step_type == 'scroll':
                    selector = properties.get('selector', '')
                    if selector:
                        target.locator(selector).first.scroll_into_view_if_needed(timeout=timeout)
                        step_result["text"] = f"Scrolled to {selector}"
                    else:
                        value = properties.get('value', '0').lower()
                        # Use body locator for evaluation to support both Page and FrameLocator
                        eval_target = target if hasattr(target, 'evaluate') else target.locator("body")
                        if value == 'bottom':
                            eval_target.evaluate("el => (el.scrollTo ? el.scrollTo(0, el.scrollHeight) : window.scrollTo(0, document.body.scrollHeight))")
                            step_result["text"] = "Scrolled to bottom"
                        elif value == 'top':
                            eval_target.evaluate("el => (el.scrollTo ? el.scrollTo(0, 0) : window.scrollTo(0, 0))")
                            step_result["text"] = "Scrolled to top"
                        else:
                            eval_target.evaluate(f"el => (el.scrollBy ? el.scrollBy(0, {value}) : window.scrollBy(0, {value}))")
                            step_result["text"] = f"Scrolled by {value}px"
                            
                elif step_type == 'keypress':
                    key = properties.get('value', 'Enter')
                    selector = properties.get('selector', '')
                    if selector:
                        target.locator(selector).first.press(key, timeout=timeout)
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
                            target.locator(selector).first.wait_for(state="visible", timeout=timeout)
                            step_result["text"] = f"Assertion passed: {selector} is visible"
                        else:
                            # If no selector, evaluate innerText of the frame/body
                            eval_target = target if hasattr(target, 'evaluate') else target.locator("body")
                            content = eval_target.evaluate("el => el.innerText || document.body.innerText")
                            if expected_value in content:
                                step_result["text"] = f"Assertion passed: text '{expected_value}' found on page"
                            else:
                                raise ValueError(f"Text '{expected_value}' not found on page")
                                
                    elif operator == 'hidden':
                        if selector:
                            target.locator(selector).first.wait_for(state="hidden", timeout=timeout)
                            step_result["text"] = f"Assertion passed: {selector} is hidden"
                        else:
                            raise ValueError("Selector is missing for 'hidden' assertion")
                            
                    elif operator == 'equals':
                        if selector:
                            actual_value = target.locator(selector).first.inner_text(timeout=timeout)
                            if actual_value == expected_value:
                                step_result["text"] = f"Assertion passed: text for {selector} equals '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: expected '{expected_value}', but found '{actual_value}'")
                        else:
                            raise ValueError("Selector is missing for 'equals' assertion")
                            
                    elif operator == 'contains':
                        if selector:
                            actual_value = target.locator(selector).first.inner_text(timeout=timeout)
                            if expected_value in actual_value:
                                step_result["text"] = f"Assertion passed: text for {selector} contains '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: '{expected_value}' not found in '{actual_value}'")
                        else:
                            # Use Frame-safe innerText
                            actual_value = target.locator("body").inner_text()
                            if expected_value in actual_value:
                                step_result["text"] = f"Assertion passed: page content contains '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: '{expected_value}' not found on page")
                            
                elif step_type == 'getText':
                    selector = properties.get('selector', '')
                    if selector:
                        val = target.locator(selector).first.inner_text(timeout=timeout)
                        step_result["text"] = val
                    else:
                        raise ValueError("Selector is missing for getText step")

                elif step_type == 'getAttribute':
                    selector = properties.get('selector', '')
                    attr = properties.get('attribute', 'value')
                    if selector:
                        val = target.locator(selector).first.get_attribute(attr, timeout=timeout)
                        step_result["text"] = val if val is not None else ""
                    else:
                        raise ValueError("Selector is missing for getAttribute step")

                elif step_type == 'refresh':
                    self._page.reload(timeout=timeout)
                    step_result["text"] = "Page refreshed"

                elif step_type == 'screenshot':
                    try:
                        import os
                        from app.main import VIDEO_DIR
                        SCREENSHOT_DIR = os.path.join(os.path.dirname(VIDEO_DIR), "screenshots")
                        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
                        custom_label = properties.get('value', '').strip() or name
                        safe_name = "".join(c if c.isalnum() else "_" for c in f"manual_{custom_label}")[:50]
                        screenshot_name = f"{safe_name}.png"
                        path = os.path.join(SCREENSHOT_DIR, screenshot_name)
                        full_page = str(properties.get('selector', 'false')).lower() != 'false'
                        self._page.screenshot(path=path, full_page=full_page)
                        step_result["text"] = f"Screenshot captured [Screenshot: /screenshots/{screenshot_name}]"
                        logger.info(f"      📸 Manual screenshot saved: {screenshot_name}")
                    except Exception as se:
                        raise ValueError(f"Screenshot failed: {se}")

                elif step_type == 'switch_tab':
                    value = str(properties.get('value', '0')).strip()
                    self._page.wait_for_timeout(1000) # Wait for page to open
                    pages = self._context.pages
                    if value.isdigit():
                        idx = int(value)
                        if idx < len(pages):
                            self._page = pages[idx]
                            self._page.bring_to_front()
                            step_result["text"] = f"Switched to tab index {idx}"
                        else:
                            raise ValueError(f"Tab index {idx} out of bounds")
                    else:
                        found = False
                        for p in pages:
                            if value.lower() in p.url.lower():
                                self._page = p
                                self._page.bring_to_front()
                                step_result["text"] = f"Switched to tab URL containing '{value}'"
                                found = True
                                break
                        if not found:
                            raise ValueError(f"Tab matching URL '{value}' not found")

                elif step_type == 'switch_frame':
                    value = str(properties.get('value', '')).strip()
                    if value:
                        step_result["text"] = f"Frame focus set to '{value}'"
                    else:
                        step_result["text"] = "Reset to Top Frame"

                else:
                    # Check if it's a known step but didn't match above logic
                    known_types = ['browser', 'click', 'type', 'wait_selector', 'wait', 'hover', 'scroll', 'keypress', 'assert', 'getText', 'getAttribute', 'refresh', 'screenshot', 'switch_tab', 'switch_frame']
                    if step_type not in known_types:
                        step_result["status"] = 400
                        step_result["reason"] = "Unsupported Action"
                        step_result["text"] = f"Unsupported step type: {step_type}"
                    
                # Success - break retry loop
                try:
                    current_url = self._page.url
                    logger.info(f"      📍 Current Page: {current_url}")
                except:
                    pass
                
                # 📸 Capture a screenshot after successful step — only if enabled by user
                if capture_screenshot:
                    try:
                        import os
                        from app.main import VIDEO_DIR
                        SCREENSHOT_DIR = os.path.join(os.path.dirname(VIDEO_DIR), "screenshots")
                        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
                        safe_name = "".join(c if c.isalnum() else "_" for c in f"{step_type}_{name}")[:40]
                        screenshot_name = f"live_{safe_name}.png"
                        path = os.path.join(SCREENSHOT_DIR, screenshot_name)
                        self._page.screenshot(path=path, full_page=False)
                        step_result["text"] += f" [Screenshot: /screenshots/{screenshot_name}]"
                        logger.info(f"      📸 Screenshot saved: {screenshot_name}")
                    except Exception as se:
                        logger.warning(f"      ⚠️ Could not capture step screenshot: {se}")
                
                break

            except Exception as e:
                # Only retry on certain types of errors (Timeout, etc)
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"⚠️ Step '{name}' failed (Attempt {attempt+1}/{MAX_RETRIES}). Retrying... Error: {str(e)}")
                    
                    # 🤖 AI Auto-Healing Check
                    logger.info(f"DEBUG Auto-Heal check: type={step_type}, timeout_match={'Timeout' in str(e) or 'Locator' in str(e)}, db={bool(db)}, user_id={user_id}, broken_selector={properties.get('selector')}")
                    if ("Timeout" in str(e) or "Locator" in str(e) or "Waiting for" in str(e)) and step_type in ['click', 'type', 'hover', 'scroll', 'assert', 'getText', 'getAttribute']:
                        broken_selector = properties.get('selector', '')
                        if db and user_id and broken_selector:
                            logger.info(f"🤖 [Auto-Heal] Triggering AI to fix broken selector: {broken_selector}")
                            try:
                                import traceback
                                logger.info(f"🤖 [Auto-Heal] Step 1: Importing AnalysisService")
                                from app.services.analysis_service import AnalysisService
                                logger.info(f"🤖 [Auto-Heal] Step 2: Extracting clean HTML")
                                # Extract stripped DOM for AI context
                                clean_html = self._page.evaluate('''() => {
                                    const clone = document.body.cloneNode(true);
                                    clone.querySelectorAll("script, style, svg, path, link, meta").forEach(e => e.remove());
                                    return clone.innerHTML;
                                }''')
                                
                                logger.info(f"🤖 [Auto-Heal] Step 3: Triggering AI model")
                                action_val = properties.get('value', '')
                                new_selector = AnalysisService.heal_selector(db, user_id, broken_selector, action_val, step_type, clean_html)
                                logger.info(f"🤖 [Auto-Heal] Step 4: AI Returned -> {new_selector}")
                                
                                if new_selector and new_selector != broken_selector and "```" not in new_selector:
                                    logger.info(f"✨ [Auto-Heal] Success! Replacing '{broken_selector}' with '{new_selector}'")
                                    properties['selector'] = new_selector
                                    step['properties'] = properties
                                    step_result["text"] = f"[AI-HEALED -> {new_selector}] "
                            except Exception as heal_err:
                                import traceback
                                logger.error(f"🤖 [Auto-Heal] Fatal Exception: {heal_err}")
                                logger.error(traceback.format_exc())

                    # Wait a bit before retry
                    self._page.wait_for_timeout(1000)
                    continue
                
                # If last attempt, log and return error result
                logger.error(f"❌ E2E Execution Permanent Failure: {str(e)}")
                step_result["status"] = 500
                step_result["reason"] = "E2E Error"
                step_result["text"] = str(e)
                
                if self._page:
                    try:
                        import uuid
                        import os
                        screenshot_name = f"error_{uuid.uuid4().hex[:8]}.png"
                        from app.main import VIDEO_DIR
                        SCREENSHOT_DIR = os.path.join(os.path.dirname(VIDEO_DIR), "screenshots")
                        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
                        path = os.path.join(SCREENSHOT_DIR, screenshot_name)
                        self._page.screenshot(path=path, full_page=True)
                        step_result["text"] += f"\n[Screenshot: /screenshots/{screenshot_name}]"
                    except Exception as se:
                        logger.error(f"Failed to take error screenshot: {se}")
                
                return step_result
        
        return step_result

    def get_video_path(self):
        """Returns the path to the recorded video if recording was enabled."""
        if self._page and self._page.video:
            return self._page.video.path()
        return None

# We will instantiate this per-flow in FlowExecutorService
