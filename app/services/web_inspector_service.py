import json
import logging
import base64
import time
import sys
import traceback
import asyncio
import ipaddress
from urllib.parse import urlparse
from typing import Dict, List, Any, Optional
try:
    from playwright.async_api import async_playwright, Page, BrowserContext, Browser
except ImportError:
    async_playwright = None
    Page = Any
    BrowserContext = Any
    Browser = Any

try:
    import playwright_stealth
except ImportError:
    playwright_stealth = None

try:
    from app.services.playwright_executor_service import PlaywrightExecutorService
except ImportError:
    PlaywrightExecutorService = None

logger = logging.getLogger(__name__)

# Global session management
_session_lock = asyncio.Lock()
_active_sessions: Dict[str, Any] = {}
_session_progress: Dict[str, Dict[str, Any]] = {}
_playwright_instance = None  # Persistent playwright instance
_shared_browser = None  # Persistent shared Chromium instance
_last_cleanup_time = 0

BLOCKED_HOSTNAMES = {
    "localhost", "flow-backend", "flow-db", "flow-redis", 
    "flow-celery-worker", "flow-frontend", "flow-db-migration",
    "host.docker.internal", "127.0.0.1", "0.0.0.0"
}

def _validate_url(url: str):
    """Protects against SSRF by validating the URL hostname and resolved IP address."""
    if not url:
        return
    parsed = urlparse(url)
    if not parsed.scheme:
        parsed = urlparse(f"https://{url}")
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Protocolo não permitido: {scheme}. Use apenas http ou https.")
    
    hostname = (parsed.hostname or "").lower().strip()
    if not hostname:
        raise ValueError("URL inválida: hostname não especificado.")
    
    if hostname in BLOCKED_HOSTNAMES:
        raise ValueError(f"Navegação bloqueada: acesso a host interno proibido ({hostname}).")
    
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise ValueError(f"Navegação bloqueada: endereço IP restrito ou privado ({ip}).")
        if str(ip) == "169.254.169.254":
            raise ValueError("Navegação bloqueada: endpoint de metadados de nuvem restrito.")
    except ValueError as ve:
        if "Navegação bloqueada" in str(ve):
            raise ve
        # Hostname is a valid domain string
        pass

QA_FLOW_EXTRACTOR_SCRIPT = """window.__getQAFlowSnapshot = () => {
const results = [];
                // Function to collect all elements piercing Shadow DOM boundaries
                const collectAllElements = (root, collected = []) => {
                    if (!root) return collected;
                    const children = root.children || [];
                    for (let i = 0; i < children.length; i++) {
                        const el = children[i];
                        collected.push(el);
                        // Pierce shadow DOM if open
                        if (el.shadowRoot) {
                            collectAllElements(el.shadowRoot, collected);
                        }
                        // Traverse light DOM
                        collectAllElements(el, collected);
                    }
                    return collected;
                };

                const allNodes = collectAllElements(document.body);
                const queryStr = 'button, input, select, textarea, a, [onclick], [role="button"], [class*="btn" i], [class*="button" i], [role="link"], [role="menuitem"], [role="tab"], [role="switch"], [role="checkbox"], [role="menuitemcheckbox"], [role="menuitemradio"], [aria-haspopup], [data-icon], svg[onclick]';
                
                const interactables = allNodes.filter(el => {
                    try { 
                        // 1. Check if it's a standard interactable
                        const isNative = el.matches(queryStr);
                        // 2. Check if it's a Shadow Host / Custom Element
                        const isCustom = el.tagName.indexOf('-') !== -1;
                        
                        if (isNative) return true;
                        
                        // If it's a custom element, only capture it as 'interactable' if:
                        // - It doesn't have an internal interactable child that covers it (delegation)
                        // - OR it has an explicit click handler
                        if (isCustom) {
                            if (el.getAttribute('onclick') || el.getAttribute('role') === 'button') return true;
                            // If it's a wrapper for an input (like vsg-vlv-input), we want the input instead.
                            // But we keep it as a fallback if no children are found.
                            return true; 
                        }
                        if (el.hasAttribute('tabindex') && el.getAttribute('tabindex') !== '-1') return true;
                        return false;
                    } catch (e) { return false; }
                });
                
                // Phase 1: Imprint JIT locators without triggering layout recalculations
                for (let i = 0; i < interactables.length; i++) {
                    interactables[i].setAttribute('data-lws-id', `lws-${i}`);
                }

                // Phase 2: Batch read layout and styles (Zero Layout Thrashing)
                for (let index = 0; index < interactables.length; index++) {
                    const el = interactables[index];
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) continue;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;

                    // Extract icon metadata from SVG or icon fonts
                    let iconAttr = '';
                    try {
                        const elTitle = el.getAttribute('title') || '';
                        const elAriaLabel = el.getAttribute('aria-label') || '';
                        const svgEl = el.querySelector('svg');
                        if (svgEl) {
                            const svgLabel = svgEl.getAttribute('aria-label') || svgEl.getAttribute('data-icon') || svgEl.getAttribute('name') || '';
                            const svgTitle = svgEl.querySelector('title')?.textContent || '';
                            const svgClass = typeof svgEl.className === 'string' ? svgEl.className : (svgEl.className?.baseVal || '');
                            const classMatch = svgClass.match(/(?:lucide|fa|bi|icon|feather|heroicon)[-_]([a-z0-9-]+)/i);
                            iconAttr = svgLabel || svgTitle || (classMatch ? classMatch[1] : '') || elTitle || elAriaLabel;
                        }
                        if (!iconAttr) {
                            const iconEl = el.querySelector('i, span[class*="icon"], span[class*="fa-"], span[class*="bi-"], span[class*="lucide-"]');
                            if (iconEl) {
                                const iClass = typeof iconEl.className === 'string' ? iconEl.className : '';
                                const match = iClass.match(/(?:fa[srbld]?\\s+fa-|bi-|lucide-|icon-)([a-z0-9-]+)/i);
                                iconAttr = iconEl.getAttribute('aria-label') || iconEl.getAttribute('title') || (match ? match[1] : '') || '';
                            }
                        }
                        if (!iconAttr) {
                            iconAttr = el.getAttribute('data-icon') || el.getAttribute('data-testid') || elTitle || '';
                        }
                    } catch(e) {}

                    let extractedText = el.innerText?.substring(0, 50).trim() || el.value?.substring(0, 50).trim() || '';
                    if (!extractedText && iconAttr) {
                        extractedText = `[icon: ${iconAttr}]`;
                    }

                    results.push({
                        id: `lws-${index}`, // Store our JIT pointer mapped to React
                        tagName: el.tagName,
                        type: el.type || '',
                        text: extractedText,
                        iconAttr: (iconAttr || '').substring(0, 50),
                        placeholder: el.placeholder?.substring(0, 50) || '',
                        nameAttr: el.name || '',
                        idAttr: el.id || '',
                        pointerEvents: style.pointerEvents,
                        className: (typeof el.className === 'string' ? el.className : '').substring(0, 100),
                        labelAttr: (el.getAttribute('aria-label') || '').substring(0, 50) || (function(){
                            try {
                                if (el.id) {
                                    let lbl = document.querySelector(`label[for="${el.id}"]`);
                                    if (lbl) return lbl.innerText.trim().substring(0, 50);
                                }
                                let pLbl = el.closest('label');
                                if (pLbl) return pLbl.innerText.trim().substring(0, 50);
                            } catch(e) {}
                            return '';
                        })(),
                        bounds: {
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height
                        }
                    });
                }

                // Sort by area descending so smallest elements render last (highest Z-Index)
                results.sort((a, b) => {
                    const areaA = a.bounds.width * a.bounds.height;
                    const areaB = b.bounds.width * b.bounds.height;
                    return areaB - areaA;
                });

                // Return all lightweight nodes (No slicing to avoid deleting small critical buttons!)
                const tree = results;

                // 3. Detect Caret Position for active element
                let caret = null;
                const active = document.activeElement;
                if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) {
                    try {
                        const selStart = active.selectionStart !== undefined ? active.selectionStart : 0;
                        const style = window.getComputedStyle(active);
                        const rect = active.getBoundingClientRect();
                        
                        const div = document.createElement('div');
                        for (const prop of ['direction', 'boxSizing', 'width', 'height', 'overflowX', 'overflowY', 'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'lineHeight', 'textTransform', 'letterSpacing', 'wordSpacing']) {
                            div.style[prop] = style[prop];
                        }
                        div.style.position = 'absolute';
                        div.style.visibility = 'hidden';
                        div.style.whiteSpace = 'pre-wrap';
                        div.style.top = '-9999px';
                        
                        const textBefore = (active.value || active.innerText || '').substring(0, selStart);
                        div.textContent = textBefore;
                        
                        const span = document.createElement('span');
                        span.textContent = (active.value || active.innerText || '').substring(selStart, selStart + 1) || '|';
                        div.appendChild(span);
                        document.body.appendChild(div);
                        
                        const spanRect = span.getBoundingClientRect();
                        caret = {
                            x: rect.left + span.offsetLeft,
                            y: rect.top + span.offsetTop,
                            height: parseFloat(style.fontSize) || 16
                        };
                        document.body.removeChild(div);
                    } catch (e) {
                        console.warn("Caret calc failed", e);
                    }
                }

                return { tree, caret };
};"""

class _WebInspectorServiceImpl:

    @classmethod
    async def register_frame_queue(cls, session_id: str, queue, loop):
        session = _active_sessions.get(session_id)
        if not session:
            return False
            
        session["fastapi_queue"] = queue
        session["fastapi_loop"] = loop
        
        if "cdp_client" not in session:
            try:
                page = session["page"]
                client = await page.context.new_cdp_session(page)
                session["cdp_client"] = client
                
                def on_frame(event):
                    import base64
                    import asyncio
                    img_bytes = base64.b64decode(event["data"])
                    # Send ACK so we keep getting frames
                    try:
                        asyncio.create_task(client.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]}))
                    except: pass
                    
                    # Push to FastAPI queue
                    fastapi_q = session.get("fastapi_queue")
                    fastapi_l = session.get("fastapi_loop")
                    if fastapi_q and fastapi_l and not fastapi_l.is_closed():
                        asyncio.run_coroutine_threadsafe(fastapi_q.put(img_bytes), fastapi_l)

                client.on("Page.screencastFrame", on_frame)
                await client.send("Page.startScreencast", {"format": "jpeg", "quality": 60})
                logger.info(f"🟢 [WebInspector] CDP Screencast started for {session_id}")
                return True
            except Exception as e:
                logger.error(f"🔴 [WebInspector] Failed to start screencast: {e}")
                return False
        return True

    @classmethod
    async def unregister_frame_queue(cls, session_id: str):
        """Unregisters the WebSocket frame queue to stop routing frames after disconnect."""
        session = _active_sessions.get(session_id)
        if not session:
            return False
        session.pop("fastapi_queue", None)
        session.pop("fastapi_loop", None)
        logger.info(f"⚪ [WebInspector] Frame queue unregistered for session {session_id}")
        return True
    """
    A persistent session manager that uses Playwright for an 
    interactive web inspection UI (recording and live view).
    """

    @classmethod
    async def _get_shared_browser(cls) -> Browser:
        global _playwright_instance, _shared_browser
        if not _playwright_instance:
            _playwright_instance = await async_playwright().start()

        if not _shared_browser or not _shared_browser.is_connected():
            logger.info("🌐 [WebInspector] Launching shared Chromium instance...")
            _shared_browser = await _playwright_instance.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox", 
                    "--disable-setuid-sandbox", 
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-zygote",
                    "--disable-ipv6",
                    "--disable-features=IsolateOrigins,site-per-process,SubresourceIntegrity",
                    "--disable-site-isolation-trials",
                    "--window-position=0,0",
                    "--ignore-certificate-errors",
                    "--disable-subresource-integrity"
                ]
            )
        return _shared_browser

    @classmethod
    async def start_session(cls, session_id: str, initial_url: str = None, steps: list = None, user_id: Optional[int] = None) -> dict:
        """Starts a persistent playwright browser session using an isolated context from the shared browser."""
        # SSRF Protection: Validate initial URL and any pre-existing navigation steps
        if initial_url:
            _validate_url(initial_url)
        for s in (steps or []):
            if s.get("type") in ("navigate", "browser"):
                step_url = s.get("properties", {}).get("url") or s.get("properties", {}).get("value")
                if step_url:
                    _validate_url(step_url)

        async with _session_lock:
            if session_id in _active_sessions:
                logger.info(f"🌐 [WebInspector] Session {session_id} exists. Checking health...")
                try:
                    logger.info(f"🌐 [WebInspector] Session {session_id} healthy, returning snapshot.")
                    return await cls.get_snapshot(session_id)
                except Exception as e:
                    logger.warning(f"🌐 [WebInspector] Session {session_id} unhealthy, recreating. Error: {e}")
                    await cls.stop_session(session_id)

            logger.info(f"🌐 [WebInspector] Starting fresh isolated session {session_id}...")
            
            try:
                browser = await cls._get_shared_browser()
                
                user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent=user_agent,
                    extra_http_headers={
                        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                    },
                    ignore_https_errors=True
                )
                await context.add_init_script(QA_FLOW_EXTRACTOR_SCRIPT)
                
                page = await context.new_page()
                
                # Failsafe stealth: Handle various library versions and module vs function ambiguity
                try:
                    # For async, we use stealth_async
                    if hasattr(playwright_stealth, 'stealth_async'):
                        await playwright_stealth.stealth_async(page)
                    elif hasattr(playwright_stealth, 'stealth'):
                        s = playwright_stealth.stealth
                        if callable(s):
                            # Try it, some versions auto-detect
                            if asyncio.iscoroutinefunction(s): await s(page)
                            else: s(page)
                except Exception as stealth_e:
                    logger.warning(f"🌐 [WebInspector] Stealth application warning (non-fatal): {stealth_e}")

                # If we have pre-existing steps to replay, we normally skip the bare initial_url navigation
                # because the steps list itself will contain a 'browser' step to do it.
                # HOWEVER, if steps are provided but NONE of them is a navigate step, we MUST navigate first.
                has_nav_step = False
                if steps:
                    has_nav_step = any(s.get("type") in ("navigate", "browser") for s in steps)
                
                if initial_url and (not steps or not has_nav_step):
                    if not initial_url.startswith('http'):
                        initial_url = f"https://{initial_url}"
                    logger.info(f"🌐 [WebInspector] Navigating to {initial_url}...")
                    try:
                        await page.goto(initial_url, wait_until="commit", timeout=60000)
                    except Exception as e:
                        logger.warning(f"🌐 [WebInspector] Initial goto(commit) failed: {e}, retrying...")
                        await asyncio.sleep(1)
                        try:
                            await page.goto(initial_url, wait_until="domcontentloaded", timeout=30000)
                        except Exception:
                            pass
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    except Exception:
                        pass
                    await asyncio.sleep(2.5) # Reduced buffer for async

                _active_sessions[session_id] = {
                    "context": context,
                    "page": page,
                    "requests": [],
                    "created_at": time.time(),
                    "last_accessed": time.time(),
                    "user_id": user_id
                }

                # Attach request listener
                page.on("request", lambda request: asyncio.create_task(cls._handle_request(session_id, request)))
                
                # Run lazy cleanup on new session creation
                asyncio.create_task(cls._cleanup_zombie_sessions())

                if steps:
                    logger.info(f"🌐 [WebInspector] Executing {len(steps)} pre-existing steps...")
                    failed_step_index = None
                    failed_step_error = None
                    for i, step in enumerate(steps):
                        step_name = step.get("name") or step.get("type") or f"Passo {i + 1}"
                        node_name = step.get("_sourceNodeName") or step.get("nodeName") or "Nó Atual"
                        _session_progress[session_id] = {
                            "current_index": i,
                            "total_steps": len(steps),
                            "step_name": step_name,
                            "node_name": node_name,
                            "status": "running"
                        }
                        try:
                            # Use skip_tree=True and return_snapshot=False for intermediate steps to speed up execution
                            is_last = (i == len(steps) - 1)
                            # Intermediate steps use 0.5s settle time, last one uses 1.5s for full hydration
                            await cls.interact(session_id, step, skip_tree=not is_last, settle_time=0.5 if not is_last else 1.5, return_snapshot=is_last)
                        except Exception as step_err:
                            failed_step_index = i
                            failed_step_error = str(step_err).split('\n')[0]  # first line only
                            logger.error(f"🌐 [WebInspector] Pre-existing step {i} ({step.get('type')}) failed: {step_err}")
                            if session_id in _session_progress:
                                _session_progress[session_id]["status"] = "failed"
                            # Stop execution but do not crash the session so user can debug
                            break
                    
                    if failed_step_index is None and session_id in _session_progress:
                        _session_progress[session_id]["status"] = "completed"
                else:
                    failed_step_index = None
                    failed_step_error = None
                
                logger.info(f"🌐 [WebInspector] Session {session_id} oriented to return snapshot.")
                result, image_bytes = await cls.get_snapshot(session_id)
                # Inject replay metadata so the frontend can highlight the failing step
                if failed_step_index is not None:
                    result["failed_step_index"] = failed_step_index
                    result["failed_step_error"] = failed_step_error
                logger.info(f"🌐 [WebInspector] Snapshot captured for {session_id}, type: {type(result)}")
                return result, image_bytes

            except Exception as e:
                logger.error(f"🌐 [WebInspector] FATAL failure in start_session: {e}")
                traceback.print_exc(file=sys.stdout)
                # Ensure cleanup of failed artifacts
                if 'page' in locals() and page:
                    try: await page.close()
                    except: pass
                if 'context' in locals() and context:
                    try: await context.close()
                    except: pass
                raise e

    @classmethod
    async def stop_session(cls, session_id: str):
        """Cleans up the playwright session context."""
        async with _session_lock:
            _session_progress.pop(session_id, None)
            session = _active_sessions.pop(session_id, None)
            if session:
                logger.info(f"🌐 [WebInspector] Stopping session {session_id}.")
                # 1. Detach CDP client if active
                cdp = session.get("cdp_client")
                if cdp:
                    try:
                        await cdp.send("Page.stopScreencast")
                    except Exception:
                        pass
                    try:
                        await cdp.detach()
                    except Exception as cdp_err:
                        logger.warning(f"🌐 [WebInspector] Error detaching CDP client: {cdp_err}")

                # 2. Close Page
                page = session.get("page")
                if page:
                    try:
                        await page.close()
                    except Exception as pe:
                        logger.warning(f"🌐 [WebInspector] Error closing page: {pe}")

                # 3. Close Context (Releases all cookies, cache, and memory)
                ctx = session.get("context")
                if ctx:
                    try:
                        await ctx.close()
                    except Exception as ce:
                        logger.warning(f"🌐 [WebInspector] Error closing context: {ce}")

    @classmethod
    def get_progress(cls, session_id: str) -> dict:
        """Returns current step-replay execution progress for a session."""
        progress = _session_progress.get(session_id)
        if not progress:
            return {"status": "idle"}
        return progress

    @classmethod
    async def _cleanup_zombie_sessions(cls):
        """Kills sessions that have been idle for more than 15 minutes."""
        global _last_cleanup_time
        now = time.time()
        # Only run cleanup at most once per minute
        if now - _last_cleanup_time < 60:
            return
            
        _last_cleanup_time = now
        zombies = []
        
        async with _session_lock:
            for sid, session in _active_sessions.items():
                if now - session.get("last_accessed", now) > 900: # 15 minutes TTL
                    zombies.append(sid)
                    
        for sid in zombies:
            logger.info(f"🌐 [WebInspector] Session {sid} has been idle for >15m. Running zombie cleanup...")
            await cls.stop_session(sid)

    @classmethod
    async def _handle_request(cls, session_id: str, request):
        try:
            if request.resource_type in ["fetch", "xhr"]:
                session = _active_sessions.get(session_id)
                if session:
                    headers = request.headers
                    post_data = request.post_data
                    
                    req_data = {
                        "method": request.method,
                        "url": request.url,
                        "headers": headers,
                        "body": post_data,
                        "timestamp": int(time.time() * 1000)
                    }
                    session["requests"].append(req_data)
        except Exception as e:
            logger.warning(f"Error intercepting request: {e}")

    @classmethod
    async def get_snapshot(cls, session_id: str, skip_tree: bool = False) -> dict:
        """Takes a screenshot and extracts the interactive DOM tree."""
        session = _active_sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found.")

        session["last_accessed"] = time.time()
        page: Page = session["page"]
        try:
            logger.info(f"🌐 [WebInspector] Taking snapshot for {session_id} (skip_tree={skip_tree})...")
            # 1. Screenshot
            try:
                # Bypass manual screenshot if CDP Screencast is active
                if "cdp_client" in session:
                    screenshot_bytes = b""
                else:
                    screenshot_bytes = await page.screenshot(timeout=20000, full_page=False, type="jpeg", quality=60)
            except Exception as ss_err:
                logger.warning(f"🌐 [WebInspector] Screenshot failed for {session_id}: {ss_err}")
                screenshot_bytes = b""

            if skip_tree:
                return {
                    "has_binary_image": bool(screenshot_bytes),
                    "url": page.url,
                    "title": await page.title()
                }, screenshot_bytes

            # 2. Extract Interactive Elements
            logger.info(f"🌐 [WebInspector] Extracting DOM tree...")
            eval_res = await page.evaluate("window.__getQAFlowSnapshot()")

            # Extract and clear captured requests
            captured_requests = session.get("requests", []).copy()
            session["requests"] = []

            # Return unified result
            return {
                "has_binary_image": bool(screenshot_bytes),
                "tree": eval_res.get("tree", []),
                "caret": eval_res.get("caret"),
                "url": page.url,
                "title": await page.title(),
                "captured_requests": captured_requests
            }, screenshot_bytes
        except Exception as e:
            logger.error(f"🌐 [WebInspector] Snapshot failed: {e}")
            raise e

    @classmethod
    async def resize_session(cls, session_id: str, width: int, height: int) -> dict:
        """Resizes the browser viewport and returns a fresh snapshot."""
        session = _active_sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found.")

        page: Page = session["page"]
        try:
            logger.info(f"🌐 [WebInspector] Resizing session {session_id} to {width}x{height}...")
            await page.set_viewport_size({"width": width, "height": height})
            # Give the browser a moment to reflow the layout
            await asyncio.sleep(0.3)
            return await cls.get_snapshot(session_id)
        except Exception as e:
            logger.error(f"🌐 [WebInspector] Resize failed: {e}")
            raise e

    @classmethod
    async def interact(cls, session_id: str, action: dict, skip_tree: bool = False, settle_time: float = 1.5, return_snapshot: bool = True) -> dict:
        """Executes a web action and returns new state."""
        session = _active_sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found.")

        session["last_accessed"] = time.time()
        page: Page = session["page"]
        action_type = action.get("type")
        props = action.get("properties", {})
        selector = props.get("selector")

        try:
            if action_type in ("navigate", "browser"):
                url = props.get("url") or props.get("value")
                if url:
                    _validate_url(url)
                    if not url.startswith('http'):
                        url = f"https://{url}"
                    # "commit" is the most lenient wait — just waits for navigation to start
                    # (first bytes received). Avoids "Could not connect to navigation engine"
                    # which happens in Docker when the browser can't sustain the "load" wait.
                    try:
                        await page.goto(url, wait_until="commit", timeout=60000)
                    except Exception as goto_err:
                        logger.warning(f"🌐 [WebInspector] goto(commit) failed, retrying with domcontentloaded: {goto_err}")
                        # Last-resort retry — sometimes commit fails on the very first request
                        await asyncio.sleep(1)
                        try:
                            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        except Exception:
                            pass  # If we still fail, proceed with whatever is loaded
                    # Give SPAs time to hydrate after navigation commits
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    except Exception:
                        pass
                    await asyncio.sleep(settle_time)


            elif action_type == "tap" or action_type == "click":
                button = props.get("button", "left")
                if selector:
                    # Ensure element is in view
                    locator = page.locator(selector).first
                    try:
                        await locator.scroll_into_view_if_needed(timeout=5000)
                        # Move to element first so hover/focus CSS effects fire (real-user behaviour)
                        await locator.hover(timeout=1000)
                    except Exception:
                        pass
                    
                    try:
                        # Standard click handles scrolling automatically
                        await locator.click(button=button, timeout=15000)
                    except Exception as click_err:
                        # Fallback for obscured elements
                        logger.info(f"🌐 [WebInspector] Click failed for {selector}, retrying with force=True: {click_err}")
                        await locator.click(button=button, force=True, timeout=8000)
                else:
                    # Coordinate-based click: move first to trigger hover, then click
                    x, y = props.get("x"), props.get("y")
                    await page.mouse.move(x, y)
                    await asyncio.sleep(0.05)   # brief hover dwell
                    await page.mouse.click(x, y, button=button)

                # Wait for any navigation the click may have triggered (links, form submits)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=3000)
                except Exception:
                    pass  # no navigation occurred — proceed
                await asyncio.sleep(0.2)
                
                # 🧠 AUTO-SYNC: Generate stable selectors for the element we just clicked
                # This ensures the frontend gets them IMMEDIATELY in the same request.
                lws_id = props.get("lwsId")
                if lws_id and lws_id != "coord_click":
                    try:
                        sel_data = await cls.generate_selectors(session_id, lws_id)
                        # We'll merge this into the final response
                        session["last_selectors"] = sel_data
                    except: pass
            elif action_type == "scroll":
                delta_x = props.get("deltaX", 0)
                delta_y = props.get("deltaY", 0)
                x = props.get("x")
                y = props.get("y")
                # Move mouse to cursor position first so wheel dispatches on the
                # correct element (inner scrollable containers, not just the page).
                if x is not None and y is not None:
                    await page.mouse.move(x, y)
                await page.mouse.wheel(delta_x, delta_y)
            elif action_type == "type" or action_type == "fill":
                val = props.get("value")
                locator = page.locator(selector).first
                try:
                    # Standard fill() is usually the most reliable for state updates
                    try:
                        await locator.fill(str(val), timeout=15000)
                    except Exception as fill_err:
                        logger.warning(f"🌐 [WebInspector] Standard fill failed for {selector}, trying press_sequentially: {fill_err}")
                        await locator.focus()
                        await locator.press_sequentially(str(val), delay=30)
                    
                    # Dispatch events to be extra sure
                    try:
                        await locator.evaluate("el => { el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); el.dispatchEvent(new Event('blur', {bubbles: true})); }")
                    except Exception:
                        pass
                except Exception as type_err:
                    logger.warning(f"🌐 [WebInspector] Robust type failed for {selector}, falling back to native fill: {type_err}")
                    try:
                        await locator.fill(str(val), timeout=8000)
                    except Exception:
                        # Final attempt: just try to type at the current focus
                        await page.keyboard.type(str(val))
            elif action_type in ("keypress", "keyboard"):
                key = props.get("key") or props.get("value") or "Enter"
                text = props.get("text")
                if selector:
                    try:
                        locator = page.locator(selector).first
                        await locator.press(key, timeout=8000)
                    except Exception as kp_err:
                        logger.warning(f"🌐 [WebInspector] Keypress on {selector} failed ({kp_err}), trying page.keyboard.press")
                        await page.keyboard.press(key)
                else:
                    if text:
                        await page.keyboard.type(text, delay=10)
                    if key:
                        await page.keyboard.press(key)
                await asyncio.sleep(0.1)

            elif action_type == "hover":
                if selector:
                    locator = page.locator(selector).first
                    try:
                        await locator.scroll_into_view_if_needed(timeout=4000)
                        await locator.hover(timeout=8000)
                    except Exception as hover_err:
                        logger.warning(f"🌐 [WebInspector] Hover failed for {selector}: {hover_err}")
                else:
                    x, y = props.get("x"), props.get("y")
                    if x is not None and y is not None:
                        await page.mouse.move(float(x), float(y))
                await asyncio.sleep(0.1)

            elif action_type in ("wait", "pause"):
                ms = int(props.get("value") or 1000)
                await asyncio.sleep(min(ms / 1000.0, 10.0))

            elif action_type == "wait_selector":
                sel = selector or props.get("selector")
                if sel:
                    timeout_val = int(props.get("timeout") or 10000)
                    await page.locator(sel).first.wait_for(state="visible", timeout=timeout_val)
                else:
                    raise ValueError("Seletor não informado para o passo de espera.")

            elif action_type == "refresh":
                await page.reload(wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(settle_time)

            elif action_type == "screenshot":
                # Snapshot capture handles screenshot implicitly
                await asyncio.sleep(0.1)

            elif action_type == "assert":
                operator = str(props.get("operator") or "visible").lower()
                expected_val = str(props.get("value") or "").strip()
                timeout_val = int(props.get("timeout") or 8000)
                if timeout_val < 1000:
                    timeout_val = 1000

                if not selector and not expected_val:
                    raise ValueError("Validação (assert) requer um seletor ou um texto esperado.")

                if operator in ("visible", "is_visible"):
                    if selector:
                        await page.locator(selector).first.wait_for(state="visible", timeout=timeout_val)
                    else:
                        start_t = time.time()
                        found = False
                        poll_max = min(timeout_val / 1000.0, 6.0)
                        while (time.time() - start_t) < poll_max:
                            body_text = await page.evaluate("() => document.body ? (document.body.innerText || document.body.textContent || '') : ''")
                            if expected_val.lower() in body_text.lower():
                                found = True
                                break
                            await asyncio.sleep(0.2)
                        if not found:
                            raise ValueError(f"Texto '{expected_val}' não foi encontrado na página.")

                elif operator in ("hidden", "not_visible"):
                    if selector:
                        await page.locator(selector).first.wait_for(state="hidden", timeout=timeout_val)
                    else:
                        raise ValueError("Seletor obrigatório para validação 'hidden'.")

                elif operator == "equals":
                    if selector:
                        locator = page.locator(selector).first
                        await locator.wait_for(state="attached", timeout=timeout_val)
                        actual_text = await locator.inner_text()
                        if actual_text.strip() != expected_val:
                            raise ValueError(f"Esperado '{expected_val}', mas encontrado '{actual_text.strip()}'.")
                    else:
                        raise ValueError("Seletor obrigatório para validação 'equals'.")

                elif operator in ("contains", "includes"):
                    if selector:
                        locator = page.locator(selector).first
                        await locator.wait_for(state="attached", timeout=timeout_val)
                        actual_text = await locator.inner_text()
                        if expected_val.lower() not in actual_text.lower():
                            raise ValueError(f"Texto '{expected_val}' não contido no elemento (encontrado: '{actual_text.strip()}').")
                    else:
                        body_text = await page.evaluate("() => document.body ? (document.body.innerText || document.body.textContent || '') : ''")
                        if expected_val.lower() not in body_text.lower():
                            raise ValueError(f"Texto '{expected_val}' não encontrado na página.")

            # Return fresh state
            if not return_snapshot:
                return {"success": True}, b""
                
            # Wait for SPA animations/routing to settle before capturing the new DOM tree
            await asyncio.sleep(0.5)
            res, image_bytes = await cls.get_snapshot(session_id, skip_tree=skip_tree)
            # Inject auto-synced selectors if available
            last_selectors = session.pop("last_selectors", None)
            if last_selectors:
                res["auto_selectors"] = last_selectors
            return res, image_bytes
        except Exception as e:
            logger.error(f"🌐 [WebInspector] Interaction failed: {e}")
            raise e

    @classmethod
    async def test_selector(cls, session_id: str, selector: str) -> dict:
        """Tests if a locator is valid and returns its count & bounding box."""
        session = _active_sessions.get(session_id)
        if not session:
            return {"success": False, "error": f"Session {session_id} not found."}

        page: Page = session["page"]
        try:
            # Playwright handles both CSS and text expressions beautifully
            locators = page.locator(selector)
            
            # Use a fast timeout to avoid hanging for 30 seconds on bad selectors
            try:
                await locators.first.wait_for(state="attached", timeout=1500)
            except Exception:
                pass # Proceed to count even if timeout drops

            count = await locators.count()
            if count == 0:
                return {"success": False, "error": "0 elementos encontrados para este seletor."}
            
            # Fetch bounding boxes for all matched elements up to 10
            boxes = []
            for i in range(min(count, 10)):
                try:
                    bx = await locators.nth(i).bounding_box()
                    if bx: boxes.append(bx)
                except Exception:
                    pass
            
            return {
                "success": True, 
                "count": count, 
                "boxes": boxes  # array of {x, y, width, height}
            }
        except Exception as e:
            return {"success": False, "error": f"Seletor inválido: {str(e).split('===========================')[0].strip()}"}

    @classmethod
    async def generate_selectors_for_element(cls, session_id: str, text: str = None, lws_id: str = None) -> dict:
        """Finds an element strictly by text or JIT lwsId and generates options for the Dropdown."""
        session = _active_sessions.get(session_id)
        if not session:
            return {"success": False, "error": f"Session {session_id} not found."}

        page: Page = session["page"]
        try:
            if lws_id:
                locators = page.locator(f'[data-lws-id="{lws_id}"]')
                error_msg = "Elemento base JIT não encontrado no DOM."
            else:
                escaped_text = text.replace('"', '\\"')
                locators = page.locator(f"text=\"{escaped_text}\"")
                error_msg = f"Nenhum botão/texto '{text}' encontrado na tela."
            
            try:
                await locators.first.wait_for(state="attached", timeout=2000)
            except Exception:
                pass
                
            count = await locators.count()
            if count == 0:
                return {"success": False, "error": error_msg}
            
            first_element = locators.first
            
            # JS injected directly onto the found node to analyze its own properties
            js_script = """(e) => {
                let opts = [];
                
                // Helper to get a human-readable name for the element
                const getSemanticName = (el) => {
                    // 1. Explicit text content (for buttons/links)
                    let text = (el.innerText || el.textContent || "").trim();
                    if (text && text.length < 40) return text;
                    
                    // 2. ARIA label or Title
                    let aria = el.getAttribute("aria-label") || el.getAttribute("title");
                    if (aria) return aria;
                    
                    // 3. Form Specifics (Placeholder or associated Label)
                    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') {
                        let placeholder = el.getAttribute("placeholder");
                        if (placeholder) return placeholder;
                        
                        // Try finding a label
                        if (el.id) {
                            let lbl = document.querySelector(`label[for="${el.id}"]`);
                            if (lbl) return lbl.innerText.trim();
                        }
                        let parentLbl = el.closest('label');
                        if (parentLbl) return parentLbl.innerText.trim();
                    }
                    
                    // 4. Name attribute
                    if (el.getAttribute("name")) return el.getAttribute("name");
                    
                    // 5. Fallback to Tag + ID (humanized)
                    if (el.id && el.id.length < 30) return `${el.tagName.toLowerCase()}#${el.id}`;
                    
                    return el.tagName.toLowerCase();
                };

                const semanticName = getSemanticName(e);

                const isRandom = (str) => {
                    const patterns = [
                        /[0-9]{4,}/,                      // Pure numbers (indices/ids)
                        /^[0-9a-f]{8}-[0-9a-f]{4}/i,      // UUIDs
                        /:r[0-9a-zA-Z]+:/,               // React 18 internal IDs
                        /^css-[a-zA-Z0-9]+/,             // Emotion/MUI
                        /-sc-[a-zA-Z0-9]{5,}/,           // Styled Components hashes
                        /_[a-zA-Z0-9]{5,}/,              // CSS Modules hashes
                        /^[a-zA-Z]{1,2}-[0-9]+$/         // Short hyphenated ID
                    ];
                    return patterns.some(p => p.test(str));
                };

                const getCleanClass = (cls) => {
                    if (!cls || typeof cls !== 'string') return '';
                    // Handle Styled Components (Button-sc-...) -> Button
                    if (cls.includes('-sc-')) return cls.split('-sc-')[0];
                    // Handle CSS Modules (btn_x12y3) -> btn
                    if (cls.includes('_')) {
                        let parts = cls.split('_');
                        if (parts[1] && parts[1].length >= 5) return parts[0];
                    }
                    return isRandom(cls) ? '' : cls;
                };
                
                // 1. Framework Data Bindings and Automation Testhooks (Gold Standard)
                const getAttr = (name) => e.getAttribute(name);
                if (getAttr('data-test')) opts.push(`[data-test="${getAttr('data-test')}"]`);
                if (getAttr('data-testid')) opts.push(`[data-testid="${getAttr('data-testid')}"]`);
                if (getAttr('data-test-id')) opts.push(`[data-test-id="${getAttr('data-test-id')}"]`);
                if (getAttr('data-automation')) opts.push(`[data-automation="${getAttr('data-automation')}"]`);
                if (getAttr('data-name')) opts.push(`[data-name="${getAttr('data-name')}"]`);
                if (getAttr('data-label')) opts.push(`[data-label="${getAttr('data-label')}"]`);
                
                // 2. Form Specific - Names and Placeholders (High Reliability)
                if (e.name) opts.push(`[name="${e.name}"]`);
                if (getAttr('placeholder')) opts.push(`[placeholder="${getAttr('placeholder')}"]`);
                if (getAttr('aria-label')) opts.push(`[aria-label="${getAttr('aria-label')}"]`);

                // 3. IDs (Only if stable)
                if (e.id && !isRandom(e.id)) {
                    if (e.id.includes('.')) opts.push(`[id="${e.id}"]`);
                    else opts.push(`#${e.id}`);
                }
                
                // 4. Associated Label Recognition (Playwright Native Pattern)
                const findLabelText = (el) => {
                    const clean = (t) => t ? t.replace(/[*:]/g, '').trim() : '';
                    if (el.id) {
                        let label = document.querySelector(`label[for="${el.id}"]`);
                        if (label) return clean(label.innerText);
                    }
                    let parentLabel = el.closest('label');
                    if (parentLabel) return clean(parentLabel.innerText);
                    
                    let prev = el.previousElementSibling;
                    if (prev && prev.tagName === 'LABEL') return clean(prev.innerText);
                    let next = el.nextElementSibling;
                    if (next && next.tagName === 'LABEL') return clean(next.innerText);
                    
                    return null;
                };

                let semanticLabel = findLabelText(e);
                if (semanticLabel && semanticLabel.length > 1 && semanticLabel.length < 50) {
                    let cleanLabel = semanticLabel.replace(/"/g, '\\\\\"');
                    opts.push(`internal:label="${cleanLabel}"`);
                    opts.push(`label:has-text("${cleanLabel}") >> ${e.tagName.toLowerCase()}`);
                }

                // 5. Shadow Host Detection
                let host = e.getRootNode()?.host;
                if (host) {
                    if (host.id && !isRandom(host.id)) {
                        opts.push(`#${host.id} >> internal:role=${e.tagName.toLowerCase()}`);
                    }
                }

                // 6. Text-Based locators
                let rawText = (e.innerText || e.textContent || e.value || '').trim();
                let cleanText = rawText.replace(/"/g, '\\\\\"');
                if (cleanText && cleanText.length > 0 && cleanText.length < 60) {
                    opts.push(`text="${cleanText}"`);
                }

                // 7. Standard Attributes
                if (getAttr('title')) opts.push(`[title="${getAttr('title')}"]`);
                if (getAttr('type')) opts.push(`${e.tagName.toLowerCase()}[type="${e.type}"]`);
                if (getAttr('role')) opts.push(`[role="${getAttr('role')}"]`);
                
                // 8. Relative XPath / Contextual (Dynamic)
                if (cleanText && cleanText.length > 0 && cleanText.length < 30) {
                    opts.push(`xpath=//${e.tagName.toLowerCase()}[normalize-space()="${cleanText}"]`);
                    opts.push(`xpath=//${e.tagName.toLowerCase()}[contains(text(), "${cleanText}")]`);
                }
                
                // If it's an input/button, try to find unique attributes for a relative XPath
                const attributes = ['name', 'value', 'type', 'role', 'title'];
                for (let attr of attributes) {
                    let val = getAttr(attr);
                    if (val) opts.push(`xpath=//${e.tagName.toLowerCase()}[@${attr}="${val}"]`);
                }

                // 9. Scoped CSS path
                let ancestor = e.parentElement;
                let anchor = null;
                while(ancestor && ancestor !== document.body) {
                    if (ancestor.id && !isRandom(ancestor.id)) { anchor = ancestor; break; }
                    ancestor = ancestor.parentElement;
                }
                let localPath = e.tagName.toLowerCase();
                if (e.className && typeof e.className === 'string') {
                    let clsList = e.className.split(/\\s+/).map(getCleanClass).filter(Boolean);
                    if (clsList.length > 0) localPath += `.${clsList.join('.')}`;
                }
                if (anchor) opts.push(`#${anchor.id} ${localPath}`);

                opts.push(localPath);
                
                return {
                    selectors: [...new Set(opts)].filter(Boolean),
                    semanticName: semanticName
                };
            }"""
            
            jit_res = await first_element.evaluate(js_script)
            selectors_raw = jit_res.get("selectors", [])
            semantic_name = jit_res.get("semanticName", "Passo")
            
            # Uniqueness check: Validate how many elements match each suggested selector concurrently
            async def validate_selector(sel):
                try:
                    locator = page.locator(sel)
                    c = await locator.count()
                    if c == 1:
                        return [{"value": sel, "count": 1}]
                    elif c > 1:
                        all_matches = await locator.all()
                        res = []
                        for i, match in enumerate(all_matches):
                            try:
                                if await match.get_attribute("data-lws-id") == lws_id:
                                    res.append({"value": f"{sel} >> nth={i}", "count": 1})
                                    break
                            except: pass
                        res.append({"value": sel, "count": c})
                        return res
                    return [{"value": sel, "count": 0}]
                except Exception:
                    return [{"value": sel, "count": 0}]

            selectors_with_counts = []
            tasks = [validate_selector(sel) for sel in selectors_raw]
            results = await asyncio.gather(*tasks)
            for res_list in results:
                selectors_with_counts.extend(res_list)

            # Fetch up to 10 bounding boxes concurrently for multi-target visual pagination
            async def get_box(i):
                try:
                    return await locators.nth(i).bounding_box()
                except Exception:
                    return None
            
            box_tasks = [get_box(i) for i in range(min(count, 10))]
            box_results = await asyncio.gather(*box_tasks)
            boxes = [b for b in box_results if b]
            
            # Final Filtering: Only show selectors that are uniquely identifying (exactly 1 match)
            # This eliminates automation errors and ambiguous locators
            unique_selectors = [s for s in selectors_with_counts if s["count"] == 1]
            
            # Fallback: if no unique selector was found, return everything as a best effort
            # (The positional XPath should almost always be unique anyway)
            if not unique_selectors and selectors_with_counts:
                unique_selectors = selectors_with_counts

            return {
                "success": True,
                "count": count,
                "selectors": unique_selectors,
                "semanticName": semantic_name,
                "boxes": boxes
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    async def ai_analyze_full_tree_and_correct(cls, session_id: str, failed_step: dict, user_id: Optional[int] = None) -> dict:
        """
        Analyzes the full web DOM tree for a failed step using smart attribute/text scoring,
        SVG icon semantics, and LLM fallback for optimal selector replacement.
        """
        session = _active_sessions.get(session_id)
        if not session:
            return {"success": False, "error": f"Session {session_id} not found."}

        try:
            snapshot, _ = await cls.get_snapshot(session_id, skip_tree=False)
            tree = snapshot.get("tree", [])
            if not tree:
                return {"success": False, "error": "DOM tree is empty."}

            import re
            step_type = (failed_step.get("type") or "click").lower()
            props = failed_step.get("properties", {})
            step_name = (failed_step.get("name") or "").strip()
            old_selector = (props.get("selector") or "").strip()
            expected_val = str(props.get("value") or "").strip()

            search_tokens = [t.lower() for t in [step_name, expected_val, old_selector] if t]
            
            # Extract quoted strings like 'My Cart' or "My Cart" as high-priority primary tokens
            quoted_matches = re.findall(r"['\"]([^'\"]+)['\"]", step_name)
            primary_tokens = [q.lower().strip() for q in quoted_matches if len(q.strip()) > 1]

            # Also strip command prefixes like "clicar em", "click", "assert", etc. to extract raw intent
            cleaned_step_name = re.sub(r'^(clicar\s+em|clique\s+em|click\s+on|click|type|digitar|assert|validar|verificar)\s+', '', step_name, flags=re.IGNORECASE).strip(' "\'')
            if cleaned_step_name and len(cleaned_step_name) > 1 and cleaned_step_name.lower() not in search_tokens:
                primary_tokens.append(cleaned_step_name.lower())

            # Individual meaningful words (length >= 3)
            word_tokens = [w.lower() for w in re.findall(r'\b\w{3,}\b', f"{step_name} {expected_val}") if w.lower() not in ('click', 'clique', 'clicar', 'type', 'assert', 'validar', 'verificar', 'step', 'passo')]

            # Common semantic icon & action synonyms (Portuguese and English)
            icon_synonyms = {
                "carrinho": ["cart", "shopping-cart", "basket", "bag", "checkout"],
                "cart": ["carrinho", "shopping-cart", "basket", "bag"],
                "lixeira": ["trash", "delete", "remove", "bin", "excluir", "remover", "apagar"],
                "excluir": ["trash", "delete", "remove", "bin", "lixeira", "apagar"],
                "deletar": ["trash", "delete", "remove", "bin", "lixeira"],
                "delete": ["trash", "remove", "lixeira", "excluir"],
                "busca": ["search", "find", "lupa", "pesquisar", "pesquisa"],
                "buscar": ["search", "find", "lupa", "pesquisar", "pesquisa"],
                "pesquisar": ["search", "find", "lupa", "busca"],
                "search": ["busca", "pesquisar", "lupa", "find"],
                "fechar": ["close", "x", "cancel", "times"],
                "close": ["fechar", "cancel", "times"],
                "configuracao": ["settings", "gear", "config", "cog", "preferences"],
                "configurações": ["settings", "gear", "config", "cog", "preferences"],
                "config": ["settings", "gear", "cog", "preferences"],
                "settings": ["config", "configuracao", "gear", "cog"],
                "editar": ["edit", "pencil", "pen"],
                "edit": ["editar", "pencil", "pen"],
                "adicionar": ["add", "plus", "new", "incluir"],
                "add": ["adicionar", "plus", "new", "incluir"],
                "salvar": ["save", "check", "disk", "floppy"],
                "save": ["salvar", "check", "disk"],
                "usuario": ["user", "profile", "account", "avatar"],
                "usuário": ["user", "profile", "account", "avatar"],
                "user": ["usuario", "profile", "account", "avatar"],
                "menu": ["menu", "hamburger", "bars", "nav"],
                "voltar": ["back", "arrow-left", "chevron-left", "prev"],
                "avancar": ["next", "arrow-right", "chevron-right", "forward"],
                "avançar": ["next", "arrow-right", "chevron-right", "forward"],
                "filtro": ["filter", "funnel", "filtrar"],
                "filtrar": ["filter", "funnel"],
                "filter": ["filtro", "funnel"],
            }

            candidates = []
            for node in tree:
                score = 0
                n_text = (node.get("text") or "").strip()
                n_selector = (node.get("selector") or "").strip()
                n_tag = (node.get("tagName") or "").strip().upper()
                n_placeholder = (node.get("placeholder") or "").strip()
                n_name = (node.get("nameAttr") or "").strip()
                n_id = (node.get("idAttr") or "").strip()
                n_label = (node.get("labelAttr") or "").strip()
                n_icon = (node.get("iconAttr") or "").lower().strip()

                # Tag match
                if step_type in ("type", "fill") and n_tag in ("INPUT", "TEXTAREA", "SELECT"):
                    score += 4
                elif step_type in ("click", "tap") and n_tag in ("BUTTON", "A", "INPUT"):
                    score += 4
                elif step_type == "assert":
                    score += 3
                    if expected_val and (expected_val.lower() in n_text.lower() or n_text.lower() in expected_val.lower()):
                        score += 20

                # Primary / Quoted Token Match (Very high confidence)
                for ptok in primary_tokens:
                    if ptok and (ptok in n_text.lower() or ptok in n_placeholder.lower() or ptok in n_name.lower() or ptok in n_id.lower() or ptok in n_label.lower()):
                        score += 15
                    elif n_text and ptok and (ptok in n_text.lower() or n_text.lower() in ptok):
                        score += 10

                # Full search tokens match
                for tok in search_tokens:
                    if tok and (tok in n_text.lower() or tok in n_placeholder.lower() or tok in n_name.lower() or tok in n_id.lower() or tok in n_label.lower()):
                        score += 8
                    elif n_text and tok and (tok in n_text.lower() or n_text.lower() in tok):
                        score += 5

                # Individual word tokens match
                for wtok in word_tokens:
                    if wtok in n_text.lower() or wtok in n_placeholder.lower() or wtok in n_name.lower() or wtok in n_id.lower() or wtok in n_label.lower():
                        score += 3

                # Icon Semantics Match
                if n_icon:
                    for ptok in primary_tokens:
                        if ptok and (ptok in n_icon or n_icon in ptok):
                            score += 16
                        syns = icon_synonyms.get(ptok, [])
                        if any(s in n_icon for s in syns):
                            score += 14
                    for tok in search_tokens:
                        if tok and (tok in n_icon or n_icon in tok):
                            score += 10
                        syns = icon_synonyms.get(tok, [])
                        if any(s in n_icon for s in syns):
                            score += 12
                    for wtok in word_tokens:
                        if wtok in n_icon:
                            score += 6
                        syns = icon_synonyms.get(wtok, [])
                        if any(s in n_icon for s in syns):
                            score += 8

                if old_selector and n_selector and old_selector == n_selector:
                    score += 6

                if score > 0:
                    candidates.append({"node": node, "score": score})

            candidates.sort(key=lambda c: c["score"], reverse=True)
            top_score = candidates[0]["score"] if candidates else 0
            is_ambiguous = len(candidates) > 1 and (candidates[0]["score"] - candidates[1]["score"] <= 2)

            best_node = None
            used_llm = False

            resolved_user_id = user_id or session.get("user_id")
            # If confidence is low (< 15) or candidates are ambiguous, attempt semantic LLM fallback
            if (not candidates or top_score < 15 or is_ambiguous) and resolved_user_id:
                try:
                    from app.database import SessionLocal
                    from app.services.analysis_service import AnalysisService
                    from app.services.skill_service import _robust_json_parse
                    from app.models.agent_models import AgentSettingsDB

                    db = SessionLocal()
                    try:
                        settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == resolved_user_id).first()
                        if settings and settings.ai_enabled and (settings.ai_api_key or settings.ai_provider in ("ollama", "flow_ia")):
                            logger.info(f"🤖 [WebInspector] Invoking LLM fallback for auto-correction (score={top_score}, ambiguous={is_ambiguous})...")

                            sample_nodes = []
                            seen_ids = set()

                            for c in candidates[:10]:
                                n = c["node"]
                                seen_ids.add(n.get("id"))
                                sample_nodes.append({
                                    "id": n.get("id"),
                                    "tagName": n.get("tagName"),
                                    "text": n.get("text"),
                                    "icon": n.get("iconAttr"),
                                    "label": n.get("labelAttr"),
                                    "placeholder": n.get("placeholder"),
                                    "name": n.get("nameAttr"),
                                    "idAttr": n.get("idAttr"),
                                    "className": n.get("className")
                                })

                            for n in tree:
                                if len(sample_nodes) >= 15:
                                    break
                                if n.get("id") not in seen_ids:
                                    seen_ids.add(n.get("id"))
                                    sample_nodes.append({
                                        "id": n.get("id"),
                                        "tagName": n.get("tagName"),
                                        "text": n.get("text"),
                                        "icon": n.get("iconAttr"),
                                        "label": n.get("labelAttr"),
                                        "placeholder": n.get("placeholder"),
                                        "name": n.get("nameAttr"),
                                        "idAttr": n.get("idAttr"),
                                        "className": n.get("className")
                                    })

                            prompt = (
                                "Você é um agente especialista em automação e auto-correção de testes Playwright.\n"
                                "Um passo de teste falhou ao tentar localizar um elemento no DOM.\n"
                                "Analise o passo que falhou e a lista de elementos interativos candidatos disponíveis na página para determinar qual elemento corresponde à intenção do usuário.\n\n"
                                f"PASSO QUE FALHOU:\n"
                                f"- Nome do Passo: {step_name}\n"
                                f"- Ação/Tipo: {step_type}\n"
                                f"- Seletor anterior que quebrou: {old_selector}\n"
                                f"- Valor esperado/digitado: {expected_val}\n\n"
                                f"ELEMENTOS INTERATIVOS NO DOM ATUAL:\n"
                                f"{json.dumps(sample_nodes, ensure_ascii=False, indent=2)}\n\n"
                                "INSTRUÇÕES:\n"
                                "1. Analise o objetivo da ação (ex: clicar em botão, ícone, preencher campo).\n"
                                "2. Identifique qual candidato (pelo campo 'id', ex: 'lws-0') representa o elemento pretendido.\n"
                                "3. Retorne APENAS um JSON válido no formato:\n"
                                "{\n"
                                '  "chosen_id": "lws-X",\n'
                                '  "confidence": 0.95,\n'
                                '  "reasoning": "Breve justificativa da escolha"\n'
                                "}\n"
                                "Se nenhum elemento for compatível, retorne {\"chosen_id\": null, \"confidence\": 0.0, \"reasoning\": \"Nenhum elemento compatível\"}."
                            )

                            llm_raw = await asyncio.to_thread(
                                AnalysisService._call_llm,
                                db, resolved_user_id, prompt, temperature=0.1, timeout=60
                            )
                            if llm_raw:
                                parsed = _robust_json_parse(llm_raw)
                                if isinstance(parsed, dict) and parsed.get("chosen_id"):
                                    chosen_id = parsed["chosen_id"]
                                    matched = next((n for n in tree if n.get("id") == chosen_id), None)
                                    if matched:
                                        best_node = matched
                                        used_llm = True
                                        logger.info(f"🤖 [WebInspector] LLM matched element {chosen_id}: {parsed.get('reasoning')}")
                    finally:
                        db.close()
                except Exception as llm_err:
                    logger.warning(f"🤖 [WebInspector] LLM fallback error: {llm_err}. Using heuristic.")

            if not best_node:
                best_node = candidates[0]["node"] if candidates else None

            if not best_node:
                return {"success": False, "error": "No matching DOM element found for step correction."}

            lws_id = best_node.get("id")
            sel_res = await cls.generate_selectors_for_element(session_id, lws_id=lws_id)

            best_selector = None
            if sel_res.get("success") and sel_res.get("selectors"):
                selectors = sel_res.get("selectors")
                unique = [s for s in selectors if s.get("count") == 1]
                best_selector = unique[0]["value"] if unique else selectors[0]["value"]

            if not best_selector:
                best_selector = best_node.get("selector") or f"//{best_node.get('tagName', 'div')}"

            return {
                "success": True,
                "suggested_selector": best_selector,
                "matched_node": best_node,
                "used_llm": used_llm
            }
        except Exception as e:
            logger.error(f"AI AutoCorrect web analysis failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

import threading
_playwright_loop = asyncio.new_event_loop()
def _run_playwright_loop():
    asyncio.set_event_loop(_playwright_loop)
    _playwright_loop.run_forever()
threading.Thread(target=_run_playwright_loop, daemon=True).start()

class WebInspectorService:

    @classmethod
    async def register_frame_queue(cls, *args, **kwargs):
        return await cls._dispatch(_WebInspectorServiceImpl.register_frame_queue(*args, **kwargs))

    @classmethod
    async def unregister_frame_queue(cls, *args, **kwargs):
        return await cls._dispatch(_WebInspectorServiceImpl.unregister_frame_queue(*args, **kwargs))
    @classmethod
    async def _dispatch(cls, coro):
        future = asyncio.run_coroutine_threadsafe(coro, _playwright_loop)
        return await asyncio.wrap_future(future)

    @classmethod
    async def start_session(cls, *args, **kwargs):
        return await cls._dispatch(_WebInspectorServiceImpl.start_session(*args, **kwargs))

    @classmethod
    async def stop_session(cls, *args, **kwargs):
        return await cls._dispatch(_WebInspectorServiceImpl.stop_session(*args, **kwargs))

    @classmethod
    async def get_snapshot(cls, *args, **kwargs):
        return await cls._dispatch(_WebInspectorServiceImpl.get_snapshot(*args, **kwargs))

    @classmethod
    async def resize_session(cls, *args, **kwargs):
        return await cls._dispatch(_WebInspectorServiceImpl.resize_session(*args, **kwargs))

    @classmethod
    async def interact(cls, *args, **kwargs):
        return await cls._dispatch(_WebInspectorServiceImpl.interact(*args, **kwargs))

    @classmethod
    async def test_selector(cls, *args, **kwargs):
        return await cls._dispatch(_WebInspectorServiceImpl.test_selector(*args, **kwargs))

    @classmethod
    async def generate_selectors_for_element(cls, *args, **kwargs):
        return await cls._dispatch(_WebInspectorServiceImpl.generate_selectors_for_element(*args, **kwargs))

    @classmethod
    async def ai_analyze_full_tree_and_correct(cls, *args, **kwargs):
        return await cls._dispatch(_WebInspectorServiceImpl.ai_analyze_full_tree_and_correct(*args, **kwargs))

    @classmethod
    def get_progress(cls, session_id: str) -> dict:
        return _WebInspectorServiceImpl.get_progress(session_id)
