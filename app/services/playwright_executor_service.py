import logging
import json
import re
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
        self._page_errors = []         # uncaught JavaScript exceptions on page
        self._console_errors = []      # console.error messages
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
            async def handle_dialog(dialog):
                self._last_dialog_message = dialog.message
                try:
                    await dialog.accept()
                except Exception as de:
                    logger.warning(f"Auto-accept dialog error: {de}")
                
            self._page.on("dialog", handle_dialog)
            
            self._install_network_listeners()
            self._install_console_and_error_listeners()

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
        """Attach request/response/requestfailed listeners to capture API calls made by the tested app."""
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

        def on_request_failed(request):
            if not self._should_capture(request.url):
                return
            pending = self._pending_requests.pop(request, None)
            elapsed = 0
            method = request.method if request else 'GET'
            if pending:
                elapsed = int(_time.time() * 1000 - pending['start_ms'])
                method = pending['method']
            failure_text = request.failure or "Network request failed or aborted"
            self._captured_requests.append({
                'url': request.url,
                'method': method,
                'status': 0,
                'duration_ms': elapsed,
                'error': str(failure_text),
            })

        self._page.on('request', on_request)
        self._page.on('response', on_response)
        self._page.on('requestfailed', on_request_failed)
        logger.info("🕸️  Network capture listeners installed (request/response/requestfailed)")

    def pop_captured_requests(self):
        """Return and clear the accumulated network captures since last call."""
        captured = list(self._captured_requests)
        self._captured_requests.clear()
        self._pending_requests.clear()
        return captured

    def _install_console_and_error_listeners(self):
        """Attach pageerror and console listeners to capture frontend runtime exceptions."""
        def on_page_error(exc):
            err_msg = str(exc)
            logger.warning(f"🌐 [Browser Page Error] {err_msg}")
            self._page_errors.append(err_msg)
            if len(self._page_errors) > 50:
                self._page_errors.pop(0)

        def on_console(msg):
            if msg.type == "error":
                text = f"[{msg.type.upper()}] {msg.text}"
                self._console_errors.append(text)
                if len(self._console_errors) > 50:
                    self._console_errors.pop(0)

        self._page.on("pageerror", on_page_error)
        self._page.on("console", on_console)
        logger.info("🚨 Frontend console & pageerror listeners installed")

    def pop_captured_errors(self):
        """Return and clear accumulated page and console errors."""
        page_errs = list(self._page_errors)
        console_errs = list(self._console_errors)
        self._page_errors.clear()
        self._console_errors.clear()
        return {"page_errors": page_errs, "console_errors": console_errs}

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
                try:
                    await self._context.close()
                except Exception as ce:
                    logger.warning(f"Error closing Playwright context: {ce}")
                finally:
                    self._page = None
                    self._context = None
            
            if self._browser:
                try:
                    await self._browser.close()
                except Exception as be:
                    logger.warning(f"Error closing Playwright browser: {be}")
                finally:
                    self._browser = None
        
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception as pe:
                    logger.warning(f"Error stopping Playwright engine: {pe}")
                finally:
                    self._playwright = None
                
            logger.info("Playwright Browser stopped")
        except Exception as e:
            logger.error(f"Error stopping Playwright gracefully: {e}")
        finally:
            self._page = None
            self._context = None
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

    async def _wait_for_loading_to_finish(self, timeout_ms=1000):
        """Intelligently wait for network idle and common loaders to disappear."""
        if not self._page:
            return
            
        logger.info("⏳ [Smart Wait] Checking for loaders and network idle...")
        try:
            # Wait for common loaders to vanish
            await self._page.wait_for_function('''() => {
                const loaders = document.querySelectorAll('.spinner, .loader, mat-spinner, [role="progressbar"], .loading-overlay, #loader, #spinner, app-loader, [aria-busy="true"], .skeleton, [class*="skeleton"]');
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

    @staticmethod
    def _normalize_text(s: str) -> str:
        if not s:
            return ""
        s = s.replace('\u00a0', ' ')
        s = s.replace('‘', "'").replace('’', "'").replace('“', '"').replace('”', '"')
        s = re.sub(r'\s+', ' ', s)
        return s.strip()

    def _text_matches(self, expected: str, actual: str) -> bool:
        if not expected:
            return True
        if not actual:
            return False
        if expected in actual:
            return True
        norm_expected = self._normalize_text(expected)
        norm_actual = self._normalize_text(actual)
        if norm_expected in norm_actual:
            return True
        if norm_expected.lower() in norm_actual.lower():
            return True
        return False

    async def _extract_element_candidates(self, locator, timeout=5000) -> list:
        try:
            candidates = await locator.first.evaluate("""el => {
                const arr = [];
                if (el.innerText) arr.push(el.innerText);
                if (el.textContent && el.textContent !== el.innerText) arr.push(el.textContent);
                if (el.value !== undefined && el.value !== null && el.value !== '') arr.push(String(el.value));
                if (el.validationMessage) arr.push(el.validationMessage);
                if (el.placeholder) arr.push(el.placeholder);
                if (el.title) arr.push(el.title);
                return arr;
            }""", timeout=timeout)
            if isinstance(candidates, list) and candidates:
                return candidates
        except Exception:
            pass
        try:
            txt = await locator.first.inner_text(timeout=timeout)
            return [txt] if txt else []
        except Exception:
            return []

    async def _extract_element_text(self, locator, timeout=5000) -> str:
        candidates = await self._extract_element_candidates(locator, timeout=timeout)
        return " | ".join(candidates)

    async def _collect_all_page_text(self, target) -> str:
        texts = []
        if self._last_dialog_message:
            texts.append(self._last_dialog_message)

        js_script = """() => {
            const collected = [];
            if (document.body) {
                if (document.body.innerText) collected.push(document.body.innerText);
                if (document.documentElement && document.documentElement.innerText && document.documentElement.innerText !== document.body.innerText) {
                    collected.push(document.documentElement.innerText);
                }
            }
            try {
                const controls = document.querySelectorAll('input, select, textarea, button, form, fieldset, [name]');
                for (const el of controls) {
                    if (el.validationMessage) {
                        collected.push(el.validationMessage);
                    }
                    if (el.value && el.type !== 'password') {
                        collected.push(String(el.value));
                    }
                    if (el.placeholder) {
                        collected.push(el.placeholder);
                    }
                    if (el.title) {
                        collected.push(el.title);
                    }
                }
            } catch (e) {}
            try {
                const labelled = document.querySelectorAll('[aria-label], [data-tooltip], [role="alert"], [role="tooltip"], [aria-live], .tooltip, .toast, .alert, .popover');
                for (const el of labelled) {
                    const aria = el.getAttribute('aria-label');
                    if (aria) collected.push(aria);
                    const dt = el.getAttribute('data-tooltip');
                    if (dt) collected.push(dt);
                    if (el.textContent) collected.push(el.textContent);
                }
            } catch (e) {}
            try {
                function scanShadow(node) {
                    if (!node) return;
                    if (node.shadowRoot) {
                        if (node.shadowRoot.innerText) collected.push(node.shadowRoot.innerText);
                        scanShadow(node.shadowRoot);
                    }
                    const children = node.children || [];
                    for (let i = 0; i < children.length; i++) {
                        scanShadow(children[i]);
                    }
                }
                if (document.body) scanShadow(document.body);
            } catch (e) {}
            return collected.join('\\n');
        }"""

        try:
            if hasattr(target, 'evaluate'):
                main_text = await target.evaluate(js_script)
                if main_text:
                    texts.append(main_text)
            elif hasattr(self, '_page') and self._page:
                main_text = await self._page.evaluate(js_script)
                if main_text:
                    texts.append(main_text)
        except Exception:
            try:
                if hasattr(target, 'locator'):
                    body_text = await target.locator("body").inner_text()
                elif self._page:
                    body_text = await self._page.locator("body").inner_text()
                if body_text:
                    texts.append(body_text)
            except Exception:
                pass

        if hasattr(self, '_page') and self._page:
            try:
                for frame in self._page.frames:
                    if frame != self._page.main_frame:
                        try:
                            f_text = await frame.evaluate(js_script)
                            if f_text:
                                texts.append(f_text)
                        except Exception:
                            pass
            except Exception:
                pass

        return "\n".join(texts)

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
        
        # Ensure a minimal safety timeout to avoid immediate race conditions, 
        # but respect user's lower configurations if explicitly set.
        if timeout < 1000:
            timeout = 1000
        
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
            retries_val = int(retries_prop) if retries_prop is not None else 0
        except:
            retries_val = 0
            
        MAX_ATTEMPTS = max(1, retries_val + 1)
        has_healed = False
        attempt = 0
        while attempt < MAX_ATTEMPTS:
            try:
                await self.start() # Ensure started
                
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
                            # Two-tier action: 1) Try natural actionability check with adaptive timeout
                            #                  2) Fall back to forced click if obscured by transient overlay/animation
                            natural_timeout = min(timeout, 3000)
                            try:
                                await el.click(timeout=natural_timeout)
                            except Exception as nat_err:
                                logger.info(f"Natural click unfulfilled ({nat_err}), resorting to forced click fallback")
                                await el.click(timeout=timeout, force=True)
                            t2 = time.time()
                            logger.info(f"⏱️ Click timing: click_exec={round((t2-t1)*1000)}ms")
                        except Exception as click_err:
                            logger.warning(f"Click failed, error: {click_err}")
                            raise click_err
                            
                        # Brief wait for UI to handle event and micro-animations settle
                        await self._page.wait_for_timeout(100)
                        if "[HEURISTIC" in step_result["text"] or "[AI" in step_result["text"]:
                            step_result["text"] += f" | Clicked element: {selector}"
                        else:
                            step_result["text"] = f"Clicked element: {selector}"
                    elif x_coord is not None and y_coord is not None:
                        # Fallback: click by coordinates recorded during Web Studio capture
                        import time
                        t1 = time.time()
                        await self._page.mouse.click(float(x_coord), float(y_coord))
                        t2 = time.time()
                        logger.info(f"⏱️ Coordinate click at ({x_coord},{y_coord}): {round((t2-t1)*1000)}ms")
                        await self._page.wait_for_timeout(100)
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
                                    try:
                                        await el.check(timeout=min(timeout, 3000))
                                    except Exception:
                                        await el.check(timeout=timeout, force=True)
                                else:
                                    try:
                                        await el.fill(value, timeout=min(timeout, 3000))
                                    except Exception:
                                        await el.fill(value, timeout=timeout, force=True)
                            t2 = time.time()
                            logger.info(f"⏱️ Type/Select timing: exec={round((t2-t1)*1000)}ms")
                        except Exception as type_err:
                            logger.warning(f"Fill/select failed, error: {type_err}")
                            raise type_err
                        # Settle wait for DOM state and micro-animations
                        await self._page.wait_for_timeout(100)
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
                                    await self._page.keyboard.type(value)
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
                        heal_prefix = f"{step_result['text']}| " if ("[HEURISTIC" in step_result["text"] or "[AI" in step_result["text"]) else ""
                        step_result["text"] = f"{heal_prefix}Element {selector} is now present"
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
                        heal_prefix = f"{step_result['text']}| " if ("[HEURISTIC" in step_result["text"] or "[AI" in step_result["text"]) else ""
                        step_result["text"] = f"{heal_prefix}Hovered over {selector}"
                    elif x_coord is not None and y_coord is not None:
                        await self._page.mouse.move(float(x_coord), float(y_coord))
                        step_result["text"] = f"Hovered over coordinates ({x_coord}, {y_coord})"
                    else:
                        raise ValueError("Selector is missing for hover step")
                        
                elif step_type == 'scroll':
                    selector = properties.get('selector', '')
                    if selector:
                        await target.locator(selector).first.scroll_into_view_if_needed(timeout=timeout)
                        heal_prefix = f"{step_result['text']}| " if ("[HEURISTIC" in step_result["text"] or "[AI" in step_result["text"]) else ""
                        step_result["text"] = f"{heal_prefix}Scrolled to {selector}"
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
                                    await self._page.mouse.move(float(x), float(y))
                                await self._page.mouse.wheel(dx_val, dy_val)
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
                        await self._page.keyboard.press(key)
                        step_result["text"] = f"Pressed {key} globally"
                        
                elif step_type == 'assert':
                    selector = properties.get('selector', '')
                    operator = properties.get('operator', 'visible').lower()
                    expected_value = properties.get('value', '')
                    
                    if not selector and not expected_value:
                        raise ValueError("Assertion needs either a selector or a value")
                    
                    poll_max = min(timeout / 1000.0, 5.0) if timeout else 5.0
                    import time

                    if operator == 'visible':
                        if selector:
                            await target.locator(selector).first.wait_for(state="visible", timeout=timeout)
                            step_result["text"] = f"Assertion passed: {selector} is visible"
                        else:
                            start_t = time.time()
                            found = False
                            content = ""
                            while True:
                                content = await self._collect_all_page_text(target)
                                if self._text_matches(expected_value, content):
                                    found = True
                                    break
                                if (time.time() - start_t) >= poll_max:
                                    break
                                await asyncio.sleep(0.1)

                            if found:
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
                            if self._normalize_text(self._last_dialog_message or "") == self._normalize_text(expected_value):
                                step_result["text"] = f"Assertion passed: modal message equals '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: expected modal '{expected_value}', but found '{self._last_dialog_message}'")
                        elif selector == 'document.title':
                            actual_value = await target.evaluate("document.title", timeout=timeout)
                            if self._normalize_text(actual_value) == self._normalize_text(expected_value):
                                step_result["text"] = f"Assertion passed: title equals '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: expected title '{expected_value}', but found '{actual_value}'")
                        elif selector:
                            candidates = await self._extract_element_candidates(target.locator(selector), timeout=timeout)
                            norm_expected = self._normalize_text(expected_value)
                            matched = any(self._normalize_text(c) == norm_expected for c in candidates)
                            if matched:
                                step_result["text"] = f"Assertion passed: text for {selector} equals '{expected_value}'"
                            else:
                                actual_val = candidates[0] if candidates else ""
                                raise ValueError(f"Assertion failed: expected '{expected_value}', but found '{actual_val}'")
                        else:
                            raise ValueError("Selector is missing for 'equals' assertion")
                            
                    elif operator in ['contains', 'includes']:
                        if selector == 'dialog.message':
                            actual = self._last_dialog_message or ""
                            if self._text_matches(expected_value, actual):
                                step_result["text"] = f"Assertion passed: modal message contains '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: '{expected_value}' not found in modal message '{actual}'")
                        elif selector == 'document.title':
                            actual_value = await target.evaluate("document.title", timeout=timeout)
                            if self._text_matches(expected_value, actual_value):
                                step_result["text"] = f"Assertion passed: title contains '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: '{expected_value}' not found in title '{actual_value}'")
                        elif selector:
                            start_t = time.time()
                            found = False
                            actual_value = ""
                            while True:
                                actual_value = await self._extract_element_text(target.locator(selector), timeout=timeout)
                                if self._text_matches(expected_value, actual_value):
                                    found = True
                                    break
                                if (time.time() - start_t) >= poll_max:
                                    break
                                await asyncio.sleep(0.1)

                            if found:
                                step_result["text"] = f"Assertion passed: text for {selector} contains '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: '{expected_value}' not found in '{actual_value}'")
                        else:
                            start_t = time.time()
                            found = False
                            actual_value = ""
                            while True:
                                actual_value = await self._collect_all_page_text(target)
                                if self._text_matches(expected_value, actual_value):
                                    found = True
                                    break
                                if (time.time() - start_t) >= poll_max:
                                    break
                                await asyncio.sleep(0.1)

                            if found:
                                step_result["text"] = f"Assertion passed: page content contains '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: '{expected_value}' not found on page")

                    elif operator in ['not_contains', 'notcontains']:
                        if selector:
                            actual_value = await self._extract_element_text(target.locator(selector), timeout=timeout)
                            if not self._text_matches(expected_value, actual_value):
                                step_result["text"] = f"Assertion passed: text for {selector} does not contain '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: '{expected_value}' found in '{actual_value}'")
                        else:
                            actual_value = await self._collect_all_page_text(target)
                            if not self._text_matches(expected_value, actual_value):
                                step_result["text"] = f"Assertion passed: page content does not contain '{expected_value}'"
                            else:
                                raise ValueError(f"Assertion failed: '{expected_value}' was found on page")
                            
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
                    await self._page.reload(timeout=timeout)
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
                            await self._page.bring_to_front()
                            step_result["text"] = f"Switched to tab index {idx}"
                        else:
                            raise ValueError(f"Tab index {idx} out of bounds")
                    else:
                        found = False
                        for p in pages:
                            if value.lower() in p.url.lower():
                                self._page = p
                                await self._page.bring_to_front()
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
                        await self._page.add_script_tag(url="https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js")
                        
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
                err_str = str(e).lower()
                is_healable_error = (
                    "timeout" in err_str or 
                    "locator" in err_str or 
                    "waiting for" in err_str or 
                    "strict mode" in err_str or 
                    "not attached" in err_str or 
                    "hidden" in err_str or
                    "not visible" in err_str or
                    "could not be found" in err_str or
                    "element is not" in err_str
                )
                is_healable_step = step_type in ['click', 'type', 'fill', 'wait_selector', 'hover', 'scroll', 'assert', 'getText', 'getAttribute']
                broken_selector = properties.get('selector', '')
                
                logger.info(f"🔍 [Auto-Heal Check] step='{name}' ({step_type}) attempt={attempt+1}/{MAX_ATTEMPTS} has_healed={has_healed} healable_step={is_healable_step} healable_err={is_healable_error} selector='{broken_selector}'")

                if not has_healed and is_healable_error and is_healable_step and broken_selector and self._page:
                    logger.info(f"🤖 [Auto-Heal] Triggering self-healing for broken selector: {broken_selector}")
                    new_selector = None
                    candidates = []
                    action_val = properties.get('value', '')
                    
                    # 1. Fast Local Heuristic Healing
                    try:
                        from app.services.heuristic_healer_service import HeuristicHealerService
                        new_selector, candidates = await HeuristicHealerService.attempt_heal(self._page, broken_selector, action_val, step_type)
                    except Exception as h_err:
                        logger.error(f"❌ [Auto-Heal] Heuristic healing failed: {h_err}")
                        new_selector, candidates = None, []
                        
                    if new_selector:
                        logger.info(f"✨ [Auto-Heal] Fast Heuristic Success! Replacing '{broken_selector}' with '{new_selector}'")
                        orig_sel_data = step.get('_original_properties')
                        orig_sel = orig_sel_data.get('selector', broken_selector) if isinstance(orig_sel_data, dict) else broken_selector
                        properties['selector'] = new_selector
                        step['properties'] = properties
                        step_result["healed_selector"] = f"{orig_sel}:::{new_selector}"
                        step_result["text"] = f"[HEURISTIC-HEALED -> {new_selector}] "
                        has_healed = True
                        if self._page:
                            await self._page.wait_for_timeout(300)
                        continue
                    else:
                        logger.info(f"🤖 [Auto-Heal] Heuristic healing could not resolve a unique selector. Falling back to AI model...")
                        # 2. AI Model Healing Fallback
                        local_db = None
                        try:
                            import json
                            from app.services.analysis_service import AnalysisService
                            
                            active_db = db
                            if not active_db:
                                from app.database import SessionLocal
                                local_db = SessionLocal()
                                active_db = local_db
                                
                            active_user_id = user_id or 1
                            clean_html = json.dumps(candidates[:50], indent=2) if candidates else "[]"
                            
                            logger.info(f"🤖 [Auto-Heal] Triggering AI model fallback with {min(len(candidates or []), 50)} candidates for user_id={active_user_id}")
                            ai_selector = AnalysisService.heal_selector(active_db, active_user_id, broken_selector, action_val, step_type, clean_html)
                            
                            if ai_selector and ai_selector != broken_selector and "```" not in ai_selector:
                                logger.info(f"✨ [Auto-Heal] AI Success! Replacing '{broken_selector}' with '{ai_selector}'")
                                orig_sel_data = step.get('_original_properties')
                                orig_sel = orig_sel_data.get('selector', broken_selector) if isinstance(orig_sel_data, dict) else broken_selector
                                properties['selector'] = ai_selector
                                step['properties'] = properties
                                step_result["healed_selector"] = f"{orig_sel}:::{ai_selector}"
                                step_result["text"] = f"[AI-HEALED -> {ai_selector}] "
                                has_healed = True
                                if self._page:
                                    await self._page.wait_for_timeout(300)
                                continue
                            else:
                                logger.warning(f"❌ [Auto-Heal] AI could not find a valid alternative selector.")
                                step_result["text"] = "[AI-HEAL FAILED] "
                        except Exception as ai_err:
                            logger.error(f"❌ [Auto-Heal] Fatal AI Exception: {ai_err}")
                            step_result["text"] = f"[AI-HEAL ERROR: {str(ai_err)}] "
                        finally:
                            if local_db:
                                local_db.close()

                # If healing was not triggered, or healing failed, respect user-configured retry attempts
                if attempt < MAX_ATTEMPTS - 1:
                    attempt += 1
                    logger.warning(f"⚠️ Step '{name}' failed (Attempt {attempt+1}/{MAX_ATTEMPTS}). Retrying... Error: {str(e)}")
                    if self._page:
                        await self._page.wait_for_timeout(1000)
                    continue
                
                # If all attempts and healing exhausted, log and return error result
                logger.error(f"❌ E2E Execution Permanent Failure: {str(e)}")
                step_result["status"] = 500
                step_result["reason"] = "E2E Error"
                
                existing_text = step_result.get("text", "")
                step_result["text"] = f"{existing_text}\n{str(e)}".strip()
                
                if self._page_errors:
                    recent_js_err = self._page_errors[-1]
                    step_result["text"] += f"\n[Browser JS Error: {recent_js_err}]"
                    step_result["page_errors"] = list(self._page_errors)
                if self._console_errors:
                    recent_console = self._console_errors[-1]
                    step_result["text"] += f"\n[Browser Console: {recent_console}]"
                    step_result["console_errors"] = list(self._console_errors)
                
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

    async def get_video_path(self):
        """Returns the path to the recorded video if recording was enabled."""
        if self._page and self._page.video:
            try:
                return await self._page.video.path()
            except Exception as e:
                logger.warning(f"Error retrieving video path: {e}")
        return None

# We will instantiate this per-flow in FlowExecutorService
