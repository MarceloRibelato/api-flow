import logging
import json
import time as _time
from playwright.async_api import async_playwright
import playwright_stealth
import asyncio

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
    'sentry.io', 'sentry-cdn.com', 'bugsnag.com', 'rollbar.com', 'backtrace.io',
    # Chat / support widgets
    'tawk.to', 'crisp.chat', 'freshchat', 'zendesk.com',
    # Generic CDN noise
    'recaptcha', 'gstatic.com/recaptcha',
)

class PlaywrightExecutorService:
    def __init__(self, headless: bool = True):
        import os
        import sys
        
        # Auto-fallback to headless if running in a container/linux without display
        if not headless and sys.platform.startswith('linux') and not os.environ.get('DISPLAY'):
            logger.warning("🚨 'Execução Visível' solicitada, mas nenhum X11 DISPLAY foi encontrado. Forçando headless=True para evitar crash (provavelmente rodando em Docker).")
            headless = True
            
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._captured_requests = []   # list of captured API calls during execution
        self._pending_requests = {}    # url -> {method, start_time}
        self._headless = headless
        self._last_dialog_message = None

    async def start(self, video_dir: str = None, storage_state: dict = None):
        """Starts the playwright engine and browser instance."""
        if not self._playwright:
            self._playwright = await async_playwright().start()
        
        if not self._browser:
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless,
                args=[
                    "--disable-dev-shm-usage", 
                    "--no-sandbox", 
                    "--disable-gpu", 
                    "--disable-infobars",
                    "--disable-setuid-sandbox",
                    "--disable-web-security",
                    "--allow-running-insecure-content",
                    "--disable-features=site-per-process,IsolateOrigins",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                    "--disable-ipc-flooding-protection"
                ]
            )
            logger.info("Playwright Browser launched (Sync/Headless/Optimized)")
        
        if not self._context:
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            context_args = {
                "viewport": {"width": 1280, "height": 720},
                "user_agent": user_agent,
                "extra_http_headers": {
                    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                }
            }
            
            if video_dir:
                context_args["record_video_dir"] = video_dir
                context_args["record_video_size"] = {"width": 1280, "height": 720}
                logger.info(f"📹 Video Recording enabled in: {video_dir} (1280x720 resolution)")
                
            if storage_state:
                context_args["storage_state"] = storage_state
                
            self._context = await self._browser.new_context(**context_args)
            
            # Enable Tracing (Time Machine) for this context
            try:
                await self._context.tracing.start(screenshots=True, snapshots=True, sources=True)
                logger.info("🕒 Playwright Tracing enabled.")
            except Exception as e:
                logger.warning(f"Failed to start tracing: {e}")
        
        if not self._page:
            self._page = await self._context.new_page()
            
            # Apply stealth to bypass bot detection
            try:
                await playwright_stealth.stealth_async(self._page)
            except Exception as e:
                logger.warning(f"Stealth application failed: {e}")
            
            # Auto-handle dialogs to prevent deadlocks and capture message
            def handle_dialog(dialog):
                self._last_dialog_message = dialog.message
                dialog.accept()
                
            self._page.on("dialog", handle_dialog)
            
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
            self._pending_requests[request] = {
                'method': request.method,
                'start_ms': _time.time() * 1000
            }

        def on_response(response):
            if not self._should_capture(response.url):
                return
            pending = self._pending_requests.pop(response.request, None)
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

    async def stop(self, trace_path: str = None):
        """Shuts down the browser and playwright engine, optionally saving a trace."""
        try:
            if self._context and trace_path:
                try:
                    await self._context.tracing.stop(path=trace_path)
                    logger.info(f"💾 Playwright Trace saved to {trace_path}")
                except Exception as trace_e:
                    logger.warning(f"Failed to save trace: {trace_e}")
                    
            if self._context:
                await self._context.close()
            self._page = None
            self._context = None
            
            if self._browser:
                await self._browser.close()
                self._browser = None
        
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
                
            logger.info("Playwright Browser stopped")
        except Exception as e:
            logger.error(f"Error stopping Playwright gracefully: {e}")
            self._browser = None
            self._playwright = None

    async def get_state(self):
        """Returns the current browser storage state (cookies/localstorage) and URL."""
        state = None
        url = None
        if self._context:
            try:
                state = await self._context.storage_state()
            except:
                pass
        if self._page:
            try:
                url = self._page.url
            except:
                pass
        return {"storage_state": state, "url": url}

    async def _wait_for_loading_to_finish(self, timeout_ms=3000):
        """Intelligently wait for network idle and common loaders to disappear."""
        if not self._page:
            return
            
        logger.info("⏳ [Smart Wait] Checking for loaders and network idle...")
        try:
            # Wait for common loaders to vanish
            await self._page.wait_for_function('''() => {
                const loaders = document.querySelectorAll('.spinner, .loader, mat-spinner, [role="progressbar"], .loading-overlay, #loader, #spinner, app-loader');
                for (let i = 0; i < loaders.length; i++) {
                    const el = loaders[i];
                    const style = window.getComputedStyle(el);
                    if (style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' && el.offsetWidth > 0 && el.offsetHeight > 0) {
                        return false; // Still visible
                    }
                }
                return true; // All hidden
            }''', timeout=timeout_ms)
        except Exception:
            pass

        try:
            # Wait for network idle
            await self._page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass

    async def _handle_hitl_pause(self, target, selector, step, db, user_id):
        import time
        import os
        import redis.asyncio as aioredis
        import asyncio
        from app.models.hitl_models import HitlSessionDB
        
        loc_visible = target.locator(f"{selector} >> visible=true")
        
        # We must wait for at least one element to appear before counting,
        # otherwise count() returns 0 immediately on dynamic pages.
        try:
            await loc_visible.first.wait_for(state="attached", timeout=5000)
        except:
            pass # Let the rest of the logic handle 0 elements
            
        count = 0
        try:
            count = await loc_visible.count()
        except:
            return None

        if count <= 1:
            return None

        candidates = []
        for i in range(min(count, 10)):
            try:
                el = loc_visible.nth(i)
                html = await el.evaluate("el => el.outerHTML")
                candidates.append({
                    "index": i,
                    "html": html,
                    "text": await el.text_content().strip() if await el.text_content() else ""
                })
            except Exception as e:
                logger.warning(f"Failed to get candidate {i}: {e}")

        if not candidates:
            return None

        hitl_session = HitlSessionDB(
            execution_id=getattr(self, 'schedule_id', 0),
            project_id=getattr(self, 'project_id', 0),
            node_id=step.get('node_id', 'unknown'),
            step_id=step.get('id', 'unknown'),
            original_selector=selector,
            candidates=candidates,
            status="pending"
        )
        db.add(hitl_session)
        db.commit()
        db.refresh(hitl_session)

        logger.info(f"⏸️ [HITL] Pausing execution for ambiguity on '{selector}'. Session ID: {hitl_session.id}")

        timeout = 300
        resolved_selector = None
        
        try:
            redis_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
            r = aioredis.from_url(redis_url)
            pubsub = r.pubsub()
            await pubsub.subscribe(f"hitl_resolve_{hitl_session.id}")
            
            async def wait_for_message():
                async for message in pubsub.listen():
                    if message['type'] == 'message':
                        return message['data']
            
            try:
                await asyncio.wait_for(wait_for_message(), timeout=timeout)
                # Message received, meaning the session was resolved by the API
                db.refresh(hitl_session)
                if hitl_session.status == "resolved":
                    if hitl_session.custom_selector:
                        resolved_selector = hitl_session.custom_selector
                    elif hitl_session.selected_index is not None:
                        resolved_selector = f"{selector} >> nth={hitl_session.selected_index}"
            except asyncio.TimeoutError:
                # 300 seconds passed without any pub/sub message
                pass
            finally:
                await pubsub.unsubscribe(f"hitl_resolve_{hitl_session.id}")
                await r.aclose()
        except Exception as redis_ex:
            logger.error(f"Redis PubSub failed during HITL, falling back to basic sleep: {redis_ex}")
            # Fallback in case Redis connection fails
            for _ in range(timeout):
                db.refresh(hitl_session)
                if hitl_session.status == "resolved":
                    if hitl_session.custom_selector:
                        resolved_selector = hitl_session.custom_selector
                    elif hitl_session.selected_index is not None:
                        resolved_selector = f"{selector} >> nth={hitl_session.selected_index}"
                    break
                await asyncio.sleep(1)

        if not resolved_selector:
            logger.warning(f"⏰ [HITL] Timeout reached. Falling back to AI for '{selector}'.")
            hitl_session.status = "timeout"
            db.commit()
            from app.services.analysis_service import AnalysisService
            clean_html = "\\n".join([f"[{c['index']}] {c['html']}" for c in candidates])
            try:
                ai_index_str = AnalysisService.resolve_ambiguity(db, user_id, selector, step.get('name', ''), step.get('type', ''), clean_html)
                if ai_index_str is not None:
                    import re
                    match = re.search(r'\d+', str(ai_index_str))
                    if match:
                        ai_index = int(match.group())
                        resolved_selector = f"{selector} >> nth={ai_index}"
            except Exception as e:
                logger.error(f"Error during AI resolve_ambiguity: {e}")
                
            if not resolved_selector:
                resolved_selector = f"{selector} >> nth=0"

        logger.info(f"✅ [HITL] Resumed with selector: {resolved_selector}")
        try:
            from app.services.flow_service import FlowService
            FlowService.apply_healing(
                db=db,
                project_id=getattr(self, 'project_id', 0),
                company_id=getattr(self, 'company_id', 0),
                flow_id=getattr(self, 'flow_id', 0),
                node_id=step.get('node_id', 'unknown'),
                old_selector=selector,
                new_selector=resolved_selector
            )
        except Exception as e:
            logger.error(f"Failed to permanently save HITL resolution: {e}")

        return resolved_selector

    async def execute_step(self, step, capture_screenshot: bool = False, db=None, user_id=None):
        """
        Executes a single E2E step using the persistent page.
        Returns a result dict.
        """
        step_type = step.get('type')
        name = step.get('name', 'Untitled Step')
        properties = step.get('properties', {})
        # Get timeout from properties or default to 15s
        timeout = properties.get('timeout')
        if timeout is None:
            timeout = 15000
        else:
            try:
                timeout = int(timeout)
            except:
                timeout = 15000
        
        # Ensure a minimum timeout for Docker environments
        if timeout < 5000:
            timeout = 5000
        
        logger.info(f"Executing E2E step: {name} ({step_type}) [Timeout: {timeout}ms]")
        
        step_result = {
            "status": 200,
            "reason": "OK",
            "text": "",
            "duration": 0
        }
        
        start_time = 1000 # Placeholder for time.time() * 1000 logic in caller if needed
        # But we'll just track text/log here
        
        retries_prop = properties.get('retries')
        try:
            retries_val = int(retries_prop) if retries_prop is not None else 1
        except:
            retries_val = 1
            
        MAX_ATTEMPTS = max(2, retries_val + 1)
        for attempt in range(MAX_ATTEMPTS):
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

                        # Wait until load so subsequent steps don't fail immediately because the page hasn't loaded
                        await self._page.goto(url, timeout=timeout, wait_until='load')
                        
                        # Capture a screenshot if requested, to ensure UI shows the page was loaded
                        if capture_screenshot:
                            try:
                                screenshot_bytes = await self._page.screenshot()
                                s_url = await self._save_screenshot(screenshot_bytes)
                                step_result["text"] = f"Navigated to {url} [Screenshot: {s_url}]"
                            except Exception as e:
                                logger.warning(f"Failed to capture screenshot after navigation: {e}")
                                step_result["text"] = f"Navigated to {url}"
                        else:
                            step_result["text"] = f"Navigated to {url}"
                    else:
                        raise ValueError("URL is missing for browser step")

                elif step_type == 'click':
                    await self._wait_for_loading_to_finish()
                    selector = properties.get('selector', '')
                    x_coord = properties.get('x')
                    y_coord = properties.get('y')
                    if selector:
                        if db and user_id:
                            hitl_sel = await self._handle_hitl_pause(target, selector, step, db, user_id)
                            if hitl_sel:
                                selector = hitl_sel
                                orig_sel = step.get('_original_properties', {}).get('selector', properties.get('selector'))
                                step_result["healed_selector"] = f"{orig_sel}:::{selector}"
                        
                        el = target.locator(selector).first
                        try:
                            import time
                            t1 = time.time()
                            # Perform forced click to bypass actionability wait times, return immediately
                            await el.click(timeout=timeout, force=True, no_wait_after=True)
                            t2 = time.time()
                            logger.info(f"⏱️ Click timing: click_exec={round((t2-t1)*1000)}ms")
                        except Exception as click_err:
                            logger.warning(f"Forced click failed, error: {click_err}")
                            raise click_err
                            
                        # Brief wait for UI to handle event
                        await self._page.wait_for_timeout(50)
                        if "[HEURISTIC" in step_result["text"] or "[AI" in step_result["text"]:
                            step_result["text"] += f" | Clicked element: {selector}"
                        else:
                            step_result["text"] = f"Clicked element: {selector}"
                    elif x_coord is not None and y_coord is not None:
                        # Fallback: click by coordinates recorded during Web Studio capture
                        import time
                        t1 = time.time()
                        self._page.mouse.click(float(x_coord), float(y_coord))
                        t2 = time.time()
                        logger.info(f"⏱️ Coordinate click at ({x_coord},{y_coord}): {round((t2-t1)*1000)}ms")
                        await self._page.wait_for_timeout(50)
                        step_result["text"] = f"Clicked at coordinates ({x_coord}, {y_coord})"
                    else:
                        raise ValueError("Selector is missing for click step")
                        
                elif step_type == 'type':
                    await self._wait_for_loading_to_finish()
                    selector = properties.get('selector', '')
                    value = str(properties.get('value', ''))
                    if selector:
                        if db and user_id:
                            hitl_sel = await self._handle_hitl_pause(target, selector, step, db, user_id)
                            if hitl_sel:
                                selector = hitl_sel
                                orig_sel = step.get('_original_properties', {}).get('selector', properties.get('selector'))
                                step_result["healed_selector"] = f"{orig_sel}:::{selector}"
                                
                        el = target.locator(selector).first
                        try:
                            import time
                            t1 = time.time()
                            try:
                                await el.wait_for(state="attached", timeout=timeout)
                                tag = await el.evaluate("e => e.tagName.toLowerCase()")
                            except:
                                tag = ""
                                
                            if tag == 'select':
                                await el.select_option(value=value, timeout=timeout)
                            else:
                                type_attr = await el.evaluate("e => e.type ? e.type.toLowerCase() : ''")
                                if type_attr in ['radio', 'checkbox']:
                                    await el.check(timeout=timeout, force=True)
                                else:
                                    await el.fill(value, timeout=timeout, force=True, no_wait_after=True)
                            t2 = time.time()
                            logger.info(f"⏱️ Type/Select timing: exec={round((t2-t1)*1000)}ms")
                        except Exception as type_err:
                            logger.warning(f"Forced fill/select failed, error: {type_err}")
                            raise type_err
                        # Minimal wait for state
                        await self._page.wait_for_timeout(50)
                        # Mask sensitive fields
                        _sensitive = ('password', 'passwd', 'secret', 'token', 'pin', 'cvv')
                        display_value = '••••••' if any(s in selector.lower() for s in _sensitive) else (value[:60] + ('…' if len(value) > 60 else ''))
                        if "[HEURISTIC" in step_result["text"] or "[AI" in step_result["text"]:
                            step_result["text"] += f" | Typed '{display_value}' into {selector}"
                        else:
                            step_result["text"] = f"Typed '{display_value}' into {selector}"
                    else:
                        # 🧠 Smart Fallback: Try to use the currently focused element
                        # (Very useful if Step A clicked the input and Step B is the typing)
                        try:
                            # Wait a brief moment for focus to settle after the previous click
                            for _ in range(10):
                                is_input = await self._page.evaluate("""() => {
                                    const el = document.activeElement;
                                    if (!el) return false;
                                    const tag = el.tagName;
                                    const role = el.getAttribute('role');
                                    return (['INPUT', 'TEXTAREA'].includes(tag) || role === 'textbox' || el.contentEditable === 'true');
                                }""")
                                original_selector = properties.get('selector')
                                if is_input:
                                    logger.info(f"🧠 [Smart Fallback] No selector for type step, but an input is focused. Typing directly.")
                                    self._page.keyboard.type(value)
                                    step_result["text"] = f"Typed into focused element (no selector provided)"
                                    return step_result # Success
                                await self._page.wait_for_timeout(200)
                        except Exception as fe:
                            logger.warning(f"Smart fallback check failed: {fe}")
                        
                        raise ValueError("Selector is missing for type step")
                        
                elif step_type == 'wait_selector':
                    selector = properties.get('selector', '')
                    if selector:
                        await target.locator(selector).first.wait_for(timeout=timeout)
                        step_result["text"] = f"Element {selector} is now present"
                    else:
                        raise ValueError("Selector is missing for wait_selector step")
                    
                elif step_type == 'wait':
                    ms = int(properties.get('value', 1000))
                    await self._page.wait_for_timeout(ms)
                    step_result["text"] = f"Waited for {ms}ms"
                    
                elif step_type == 'hover':
                    await self._wait_for_loading_to_finish()
                    selector = properties.get('selector', '')
                    x_coord = properties.get('x')
                    y_coord = properties.get('y')
                    if selector:
                        el = target.locator(selector).first
                        await el.hover(timeout=timeout)
                        step_result["text"] = f"Hovered over {selector}"
                    elif x_coord is not None and y_coord is not None:
                        self._page.mouse.move(float(x_coord), float(y_coord))
                        step_result["text"] = f"Hovered over coordinates ({x_coord}, {y_coord})"
                    else:
                        raise ValueError("Selector is missing for hover step")
                        
                elif step_type == 'scroll':
                    selector = properties.get('selector', '')
                    if selector:
                        await target.locator(selector).first.scroll_into_view_if_needed(timeout=timeout)
                        step_result["text"] = f"Scrolled to {selector}"
                    else:
                        dx = properties.get('deltaX', 0)
                        dy = properties.get('deltaY', properties.get('value', 0))
                        x = properties.get('x')
                        y = properties.get('y')
                        
                        logger.info(f"📜 [Executor] Scroll action: deltaX={dx}, deltaY={dy}, x={x}, y={y}")
                        
                        try:
                            # 🧠 Granular Scroll: use mouse.wheel to respect inner scrollable containers
                            dx_val = int(float(dx)) if dx is not None else 0
                            dy_val = int(float(dy)) if dy is not None else 0
                            
                            if dy == 'bottom':
                                await self._page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                                step_result["text"] = "Scrolled to bottom"
                            elif dy == 'top':
                                await self._page.evaluate("() => window.scrollTo(0, 0)")
                                step_result["text"] = "Scrolled to top"
                            else:
                                if x is not None and y is not None:
                                    self._page.mouse.move(float(x), float(y))
                                self._page.mouse.wheel(dx_val, dy_val)
                                step_result["text"] = f"Scrolled by X:{dx_val} Y:{dy_val}"
                        except Exception as e:
                            logger.warning(f"Scroll via mouse.wheel failed: {e}")
                            # Final fallback to window.scrollBy
                            await self._page.evaluate(f"window.scrollBy({dx_val if 'dx_val' in locals() else 0}, {dy_val if 'dy_val' in locals() else 0})")
                            step_result["text"] = f"Scrolled by X:{dx_val if 'dx_val' in locals() else 0} Y:{dy_val if 'dy_val' in locals() else 0} (fallback)"
                            
                elif step_type == 'keypress':
                    key = properties.get('value', 'Enter')
                    selector = properties.get('selector', '')
                    if selector:
                        await target.locator(selector).first.press(key, timeout=timeout)
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
                            await target.locator(selector).first.wait_for(state="visible", timeout=timeout)
                            step_result["text"] = f"Assertion passed: {selector} is visible"
                        else:
                            # If no selector, evaluate innerText of the frame/body
                            eval_target = target if hasattr(target, 'evaluate') else target.locator("body")
                            content = await eval_target.evaluate("el => el.innerText || document.body.innerText")
                            if expected_value in content:
                                step_result["text"] = f"Assertion passed: text '{expected_value}' found on page"
                            else:
                                raise ValueError(f"Text '{expected_value}' not found on page")
                                
                    elif operator == 'hidden':
                        if selector:
                            await target.locator(selector).first.wait_for(state="hidden", timeout=timeout)
                            step_result["text"] = f"Assertion passed: {selector} is hidden"
                        else:
                            raise ValueError("Selector is missing for 'hidden' assertion")
                            
                    elif operator == 'equals':
                        if selector == 'dialog.message':
                            if self._last_dialog_message == expected_value:
                                step_result["text"] = f"Assertion passed: modal message equals '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: expected modal '{expected_value}', but found '{self._last_dialog_message}'")
                        elif selector == 'document.title':
                            actual_value = await target.evaluate("document.title", timeout=timeout)
                            if actual_value == expected_value:
                                step_result["text"] = f"Assertion passed: title equals '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: expected title '{expected_value}', but found '{actual_value}'")
                        elif selector:
                            actual_value = await target.locator(selector).first.inner_text(timeout=timeout)
                            if actual_value == expected_value:
                                step_result["text"] = f"Assertion passed: text for {selector} equals '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: expected '{expected_value}', but found '{actual_value}'")
                        else:
                            raise ValueError("Selector is missing for 'equals' assertion")
                            
                    elif operator == 'contains':
                        if selector == 'dialog.message':
                            actual = self._last_dialog_message or ""
                            if expected_value in actual:
                                step_result["text"] = f"Assertion passed: modal message contains '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: '{expected_value}' not found in modal message '{actual}'")
                        elif selector == 'document.title':
                            actual_value = await target.evaluate("document.title", timeout=timeout)
                            if expected_value in actual_value:
                                step_result["text"] = f"Assertion passed: title contains '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: '{expected_value}' not found in title '{actual_value}'")
                        elif selector:
                            actual_value = await target.locator(selector).first.inner_text(timeout=timeout)
                            if expected_value in actual_value:
                                step_result["text"] = f"Assertion passed: text for {selector} contains '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: '{expected_value}' not found in '{actual_value}'")
                        else:
                            # Use Frame-safe innerText
                            actual_value = await target.locator("body").inner_text()
                            if expected_value in actual_value:
                                step_result["text"] = f"Assertion passed: page content contains '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: '{expected_value}' not found on page")
                            
                elif step_type == 'getText':
                    selector = properties.get('selector', '')
                    if selector:
                        val = await target.locator(selector).first.inner_text(timeout=timeout)
                        step_result["text"] = val
                    else:
                        raise ValueError("Selector is missing for getText step")

                elif step_type == 'getAttribute':
                    selector = properties.get('selector', '')
                    attr = properties.get('attribute', 'value')
                    if selector:
                        val = await target.locator(selector).first.get_attribute(attr, timeout=timeout)
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
                        await self._page.screenshot(path=path, full_page=full_page)
                        step_result["text"] = f"Screenshot captured [Screenshot: /screenshots/{screenshot_name}]"
                        logger.info(f"      📸 Manual screenshot saved: {screenshot_name}")
                    except Exception as se:
                        raise ValueError(f"Screenshot failed: {se}")

                elif step_type == 'switch_tab':
                    value = str(properties.get('value', '0')).strip()
                    await self._page.wait_for_timeout(1000) # Wait for page to open
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

                elif step_type == 'a11y':
                    try:
                        # Fetch Axe-core configuration from properties (e.g., 'critical', 'serious')
                        thresholds = properties.get('impacts', ['critical', 'serious'])
                        if isinstance(thresholds, str):
                            thresholds = [t.strip().lower() for t in thresholds.split(',')]
                        
                        logger.info(f"      ♿ Running Axe-core Accessibility scan (thresholds: {thresholds})...")
                        
                        # Inject Axe-core script via CDN directly into the page
                        self._page.add_script_tag(url="https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js")
                        
                        # Run evaluation
                        axe_results = await self._page.evaluate("async () => await axe.run()")
                        violations = axe_results.get('violations', [])
                        
                        # Filter by impact threshold
                        failed_rules = [v for v in violations if v.get('impact') in thresholds]
                        
                        if failed_rules:
                            error_msg = f"Acessibilidade Falhou: {len(failed_rules)} violações encontradas (Impacto: {', '.join(thresholds)}).\n\n"
                            for v in failed_rules:
                                error_msg += f"- [{v.get('impact', 'unknown').upper()}] {v.get('id')}: {v.get('description', '')}\n"
                                error_msg += f"  Ajuda: {v.get('help', '')}\n"
                            
                            raise ValueError(error_msg)
                        else:
                            step_result["text"] = f"Acessibilidade (a11y): {len(violations)} violações totais (Nenhuma no nível crítico configurado: {', '.join(thresholds)})."
                    except Exception as a11y_err:
                        raise ValueError(f"Falha na execução do teste de acessibilidade: {a11y_err}")

                else:
                    # Check if it's a known step but didn't match above logic
                    known_types = ['browser', 'click', 'type', 'wait_selector', 'wait', 'hover', 'scroll', 'keypress', 'assert', 'getText', 'getAttribute', 'refresh', 'screenshot', 'switch_tab', 'switch_frame', 'a11y']
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
                        await self._page.screenshot(path=path, full_page=False)
                        step_result["text"] += f" [Screenshot: /screenshots/{screenshot_name}]"
                        logger.info(f"      📸 Screenshot saved: {screenshot_name}")
                    except Exception as se:
                        logger.warning(f"      ⚠️ Could not capture step screenshot: {se}")
                
                break

            except Exception as e:
                # Only retry on certain types of errors (Timeout, etc)
                if attempt < MAX_ATTEMPTS - 1:
                    logger.warning(f"⚠️ Step '{name}' failed (Attempt {attempt+1}/{MAX_ATTEMPTS}). Retrying... Error: {str(e)}")
                    
                    # 🤖 AI Auto-Healing Check
                    err_str = str(e).lower()
                    is_healable_error = ("timeout" in err_str or "locator" in err_str or "waiting for" in err_str or "strict mode" in err_str or "not attached" in err_str or "hidden" in err_str)
                    is_healable_step = step_type in ['click', 'type', 'fill', 'wait_selector', 'hover', 'scroll', 'assert', 'getText', 'getAttribute']
                    
                    logger.info(f"DEBUG Auto-Heal check: type={step_type} (valid={is_healable_step}), err_match={is_healable_error}, db={bool(db)}, user_id={user_id}, broken_selector={properties.get('selector')}")
                    if is_healable_error and is_healable_step:
                        broken_selector = properties.get('selector', '')
                        if db and user_id and broken_selector:
                            action_val = properties.get('value', '')
                            
                            logger.info(f"🤖 [Auto-Heal] Triggering Expert Heuristic to fix broken selector: {broken_selector}")
                            try:
                                from app.services.heuristic_healer_service import HeuristicHealerService
                                new_selector, candidates = await HeuristicHealerService.attempt_heal(self._page, broken_selector, action_val, step_type)
                            except Exception as h_err:
                                logger.error(f"Heuristic failed: {h_err}")
                                new_selector, candidates = None, []
                                
                            if new_selector:
                                logger.info(f"✨ [Auto-Heal] Fast Heuristic Success! Replacing '{broken_selector}' with '{new_selector}'")
                                properties['selector'] = new_selector
                                step['properties'] = properties
                                step_result["text"] = f"[HEURISTIC-HEALED -> {new_selector}] "
                                orig_sel = step.get('_original_properties', {}).get('selector', broken_selector)
                                step_result["healed_selector"] = f"{orig_sel}:::{new_selector}"
                            else:
                                logger.info(f"🤖 [Auto-Heal] Heuristic failed or was ambiguous. Falling back to AI model.")
                                try:
                                    import traceback
                                    import json
                                    from app.services.analysis_service import AnalysisService
                                    
                                    # Fallback: Instead of sending the full DOM, we send the highly-filtered JSON array of candidates!
                                    # This is insanely faster, uses 95% less tokens, and reduces hallucinations.
                                    clean_html = json.dumps(candidates[:50], indent=2) if candidates else "[]"
                                    
                                    logger.info(f"🤖 [Auto-Heal] Triggering AI model fallback with {min(len(candidates or []), 50)} candidates")
                                    new_selector = AnalysisService.heal_selector(db, user_id, broken_selector, action_val, step_type, clean_html)
                                    
                                    if new_selector and new_selector != broken_selector and "```" not in new_selector:
                                        logger.info(f"✨ [Auto-Heal] AI Success! Replacing '{broken_selector}' with '{new_selector}'")
                                        properties['selector'] = new_selector
                                        step['properties'] = properties
                                        orig_sel = step.get('_original_properties') or {}
                                        orig_sel_str = orig_sel.get('selector', broken_selector) if isinstance(orig_sel, dict) else broken_selector
                                        step_result["text"] = f"[AI-HEALED -> {new_selector}] "
                                        step_result["healed_selector"] = f"{orig_sel_str}:::{new_selector}"
                                    else:
                                        logger.warning(f"❌ [Auto-Heal] AI could not find a valid alternative selector.")
                                        step_result["text"] = "[AI-HEAL FAILED] "
                                except Exception as heal_err:
                                    import traceback
                                    logger.error(f"🤖 [Auto-Heal] Fatal AI Exception: {heal_err}")
                                    logger.error(traceback.format_exc())
                                    step_result["text"] = f"[AI-HEAL ERROR: {str(heal_err)}] "

                    # Wait a bit before retry
                    await self._page.wait_for_timeout(1000)
                    continue
                
                # If last attempt, log and return error result
                logger.error(f"❌ E2E Execution Permanent Failure: {str(e)}")
                step_result["status"] = 500
                step_result["reason"] = "E2E Error"
                
                existing_text = step_result.get("text", "")
                step_result["text"] = f"{existing_text}\n{str(e)}".strip()
                
                if self._page:
                    try:
                        import uuid
                        import os
                        screenshot_name = f"error_{uuid.uuid4().hex[:8]}.png"
                        from app.main import VIDEO_DIR
                        SCREENSHOT_DIR = os.path.join(os.path.dirname(VIDEO_DIR), "screenshots")
                        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
                        path = os.path.join(SCREENSHOT_DIR, screenshot_name)
                        await self._page.screenshot(path=path, full_page=True)
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
