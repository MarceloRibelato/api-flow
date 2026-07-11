import json
import logging
import base64
import time
import sys
import traceback
import asyncio
from typing import Dict, List, Any, Optional
from playwright.async_api import async_playwright, Page, BrowserContext, Browser
import playwright_stealth

from app.services.playwright_executor_service import PlaywrightExecutorService

logger = logging.getLogger(__name__)

# Global session management
_session_lock = asyncio.Lock()
_active_sessions: Dict[str, Any] = {}
_playwright_instance = None  # Persistent playwright instance
_last_cleanup_time = 0

class WebInspectorService:
    """
    A persistent session manager that uses Playwright for an 
    interactive web inspection UI (recording and live view).
    """

    @classmethod
    async def start_session(cls, session_id: str, initial_url: str = None, steps: list = None) -> dict:
        """Starts a persistent playwright browser session."""
        global _playwright_instance
        async with _session_lock:
            if session_id in _active_sessions:
                logger.info(f"🌐 [WebInspector] Session {session_id} exists. Checking health...")
                try:
                    logger.info(f"🌐 [WebInspector] Session {session_id} healthy, returning snapshot.")
                    return await cls.get_snapshot(session_id)
                except Exception as e:
                    logger.warning(f"🌐 [WebInspector] Session {session_id} unhealthy, recreating. Error: {e}")
                    await cls.stop_session(session_id)

            logger.info(f"🌐 [WebInspector] Starting fresh session {session_id}...")
            
            try:
                if not _playwright_instance:
                    _playwright_instance = await async_playwright().start()
                
                # Launch browser with stability, direct network, and zero-isolation flags
                browser = await _playwright_instance.chromium.launch(
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
                
                user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent=user_agent,
                    extra_http_headers={
                        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                    },
                    ignore_https_errors=True
                )
                
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
                    "browser": browser,
                    "context": context,
                    "page": page,
                    "requests": [],
                    "created_at": time.time(),
                    "last_accessed": time.time()
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
                        try:
                            # Use skip_tree=True and return_snapshot=False for intermediate steps to speed up execution
                            is_last = (i == len(steps) - 1)
                            # Intermediate steps use 0.5s settle time, last one uses 1.5s for full hydration
                            await cls.interact(session_id, step, skip_tree=not is_last, settle_time=0.5 if not is_last else 1.5, return_snapshot=is_last)
                        except Exception as step_err:
                            failed_step_index = i
                            failed_step_error = str(step_err).split('\n')[0]  # first line only
                            logger.error(f"🌐 [WebInspector] Pre-existing step {i} ({step.get('type')}) failed: {step_err}")
                            # Stop execution but do not crash the session so user can debug
                            break
                else:
                    failed_step_index = None
                    failed_step_error = None
                
                logger.info(f"🌐 [WebInspector] Session {session_id} oriented to return snapshot.")
                result = await cls.get_snapshot(session_id)
                # Inject replay metadata so the frontend can highlight the failing step
                if failed_step_index is not None:
                    result["failed_step_index"] = failed_step_index
                    result["failed_step_error"] = failed_step_error
                logger.info(f"🌐 [WebInspector] Snapshot captured for {session_id}, type: {type(result)}")
                return result

            except Exception as e:
                logger.error(f"🌐 [WebInspector] FATAL failure in start_session: {e}")
                traceback.print_exc(file=sys.stdout)
                # Ensure cleanup of failed artifacts
                try:
                    if 'page' in locals(): await page.close()
                    if 'context' in locals(): await context.close()
                    if 'browser' in locals(): await browser.close()
                except: pass
                raise e

    @classmethod
    async def stop_session(cls, session_id: str):
        """Cleans up the playwright session."""
        async with _session_lock:
            session = _active_sessions.pop(session_id, None)
            if session:
                logger.info(f"🌐 [WebInspector] Stopping session {session_id}.")
                try:
                    await session["page"].close()
                    await session["context"].close()
                    await session["browser"].close()
                except Exception as e:
                    logger.warning(f"🌐 [WebInspector] Error during cleanup: {e}")

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
                # Optimized image payload using JPEG instead of PNG for faster network transfer
                screenshot_bytes = await page.screenshot(timeout=20000, full_page=False, type="jpeg", quality=60)
                b64_image = base64.b64encode(screenshot_bytes).decode('utf-8')
            except Exception as ss_err:
                logger.warning(f"🌐 [WebInspector] Screenshot failed for {session_id}: {ss_err}")
                b64_image = "" # Return empty image instead of crashing

            if skip_tree:
                return {
                    "image_b64": b64_image,
                    "url": page.url,
                    "title": await page.title()
                }

            # 2. Extract Interactive Elements
            logger.info(f"🌐 [WebInspector] Extracting DOM tree...")
            eval_res = await page.evaluate("""() => {
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
                const queryStr = 'button, input, select, textarea, a, [onclick], [role="button"], [class*="btn" i], [class*="button" i], [role="link"], [role="menuitem"], [role="tab"], [role="switch"], [role="checkbox"]';
                
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
                        return false;
                    } catch (e) { return false; }
                });
                
                interactables.forEach((el, index) => {
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0 || window.getComputedStyle(el).display === 'none') return;

                    // Imprint JIT locator for deferred Playwright targeting
                    el.setAttribute('data-lws-id', `lws-${index}`);

                    results.push({
                        id: `lws-${index}`, // Store our JIT pointer mapped to React
                        tagName: el.tagName,
                        type: el.type || '',
                        text: el.innerText?.substring(0, 50).trim() || el.value?.substring(0, 50).trim() || '',
                        placeholder: el.placeholder?.substring(0, 50) || '',
                        nameAttr: el.name || '',
                        idAttr: el.id || '',
                        bounds: {
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height
                        }
                    });
                });

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
            }""")

            # Extract and clear captured requests
            captured_requests = session.get("requests", []).copy()
            session["requests"] = []

            # Return unified result
            return {
                "image_b64": b64_image,
                "tree": eval_res.get("tree", []),
                "caret": eval_res.get("caret"),
                "url": page.url,
                "title": await page.title(),
                "captured_requests": captured_requests
            }
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
                        await locator.scroll_into_view_if_needed(timeout=2000)
                        # Move to element first so hover/focus CSS effects fire (real-user behaviour)
                        await locator.hover(timeout=1000)
                    except Exception:
                        pass
                    
                    try:
                        # Standard click handles scrolling automatically
                        await locator.click(button=button, timeout=30000)
                    except Exception as click_err:
                        # Fallback for obscured elements
                        logger.info(f"🌐 [WebInspector] Click failed for {selector}, retrying with force=True: {click_err}")
                        await locator.click(button=button, force=True, timeout=10000)
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
                        await locator.fill(str(val), timeout=10000)
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
                        await locator.fill(str(val), timeout=3000)
                    except Exception:
                        # Final attempt: just try to type at the current focus
                        await page.keyboard.type(str(val))
            elif action_type == "keyboard":
                key = props.get("key")
                text = props.get("text")
                
                if text:
                    # Type character by character to simulate human behaviour
                    await page.keyboard.type(text, delay=10)
                if key:
                    await page.keyboard.press(key)
                
                # Settle SPA state after typing
                await asyncio.sleep(0.1)

            # Return fresh state
            if not return_snapshot:
                return {"success": True}
                
            res = await cls.get_snapshot(session_id, skip_tree=skip_tree)
            # Inject auto-synced selectors if available
            last_selectors = session.pop("last_selectors", None)
            if last_selectors:
                res["auto_selectors"] = last_selectors
            return res
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
            
            # Uniqueness check: Validate how many elements match each suggested selector
            selectors_with_counts = []
            for sel in selectors_raw:
                try:
                    # We use a brief timeout to avoid hanging on complex selectors
                    locator = page.locator(sel)
                    c = await locator.count()
                    
                    if c == 1:
                        selectors_with_counts.append({"value": sel, "count": 1})
                    elif c > 1:
                        # Hardening: Find the exact index of the element we clicked among the multiple matches
                        # This ensures that even for repeated elements, we provide a unique locator
                        all_matches = await locator.all()
                        found_unique = False
                        for i, match in enumerate(all_matches):
                            try:
                                # We check both our injected data-lws-id and other identity attributes
                                if await match.get_attribute("data-lws-id") == lws_id:
                                    hardened_sel = f"{sel} >> nth={i}"
                                    selectors_with_counts.append({"value": hardened_sel, "count": 1})
                                    found_unique = True
                                    break
                            except: 
                                continue
                        
                        # Keep the non-unique one as an alternative
                        selectors_with_counts.append({"value": sel, "count": c})
                    else:
                        selectors_with_counts.append({"value": sel, "count": 0})
                except Exception:
                    selectors_with_counts.append({"value": sel, "count": 0})

            # Fetch up to 10 bounding boxes for multi-target visual pagination
            boxes = []
            for i in range(min(count, 10)):
                try:
                    bx = await locators.nth(i).bounding_box()
                    if bx: boxes.append(bx)
                except Exception:
                    pass
            
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
