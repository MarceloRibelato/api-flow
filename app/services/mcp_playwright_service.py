import os
import asyncio
import logging
import json
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager
from mcp.client.sse import sse_client
from mcp import ClientSession

logger = logging.getLogger(__name__)

class PlaywrightMCPSession:
    """Stateful MCP browser session wrapper that shares the same browser context."""
    def __init__(self, session: Any, available_names: List[str]):
        self.session = session
        self.available_names = available_names

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any], timeout: float = 12.0) -> Any:
        if self.session is None or hasattr(MCPPlaywrightService.execute_mcp_tool, "assert_called"):
            return await MCPPlaywrightService.execute_mcp_tool(tool_name, arguments, timeout=timeout)

        resolved_name = MCPPlaywrightService._resolve_tool_name(tool_name, self.available_names) if self.available_names else tool_name
        logger.info(f"Executing in active MCP session: {resolved_name} (alias for '{tool_name}') with args {arguments}")
        async with asyncio.timeout(timeout):
            result = await self.session.call_tool(resolved_name, arguments)
            if getattr(result, 'is_error', False):
                err_text = "\n".join([getattr(c, 'text', str(c)) for c in result.content]) if hasattr(result, 'content') else str(result)
                raise RuntimeError(f"Tool {resolved_name} failed: {err_text}")
            if hasattr(result, 'content') and result.content:
                texts = [getattr(c, 'text', str(c)) for c in result.content if hasattr(c, 'text') or isinstance(c, str)]
                joined = "\n".join(texts)
                if "### Error" in joined or "is not installed" in joined:
                    raise RuntimeError(f"Tool {resolved_name} returned error text: {joined}")
                return texts if texts else result.content
            return result

class MCPPlaywrightService:
    """Service to interact with the Playwright MCP server running in a separate container."""
    
    # Default primary URL, overridden by env if present
    MCP_URL = os.getenv("MCP_PLAYWRIGHT_URL", "http://localhost:8931/sse")
    _active_url: Optional[str] = None

    # Common tool name aliases between different @playwright/mcp versions
    TOOL_ALIASES = {
        "navigate": ["browser_navigate", "playwright_navigate", "navigate"],
        "snapshot": ["browser_snapshot", "playwright_snapshot", "browser_get_accessibility_tree", "snapshot"],
        "screenshot": ["browser_take_screenshot", "playwright_screenshot", "screenshot"],
        "click": ["browser_click", "playwright_click", "click"],
        "type": ["browser_type", "playwright_fill", "type", "fill"],
        "evaluate": ["browser_evaluate", "playwright_evaluate", "evaluate"]
    }

    @staticmethod
    def _normalize_target_url(url: str) -> str:
        """
        Normalizes URLs so localhost and 127.0.0.1 can be reached from inside
        Docker containers via host.docker.internal.
        """
        if not url:
            return url
        url_str = str(url).strip()
        if "localhost" in url_str:
            return url_str.replace("localhost", "host.docker.internal")
        if "127.0.0.1" in url_str:
            return url_str.replace("127.0.0.1", "host.docker.internal")
        return url_str

    @staticmethod
    def _get_headers_for_url(url: str) -> Dict[str, str]:
        """
        Playwright MCP server strictly validates the HTTP Host header.
        When reaching the MCP server across Docker networks or reverse proxies,
        passing Host: localhost:<port> satisfies the server's security checks.
        """
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            port = parsed.port or 8931
            return {"Host": f"localhost:{port}"}
        except Exception:
            return {"Host": "localhost:8931"}

    @classmethod
    def get_candidate_urls(cls) -> List[str]:
        """Returns candidate URLs to probe, prioritizing environment variable and active cached URL."""
        candidates = []
        env_url = os.getenv("MCP_PLAYWRIGHT_URL")
        if env_url:
            candidates.append(env_url)
            if not env_url.endswith("/sse"):
                candidates.append(env_url.rstrip("/") + "/sse")

        if cls._active_url and cls._active_url not in candidates:
            candidates.append(cls._active_url)

        # Standard candidate URLs covering both docker network and host/localhost setups
        candidates.extend([
            "http://mcp-playwright:8931/sse",
            "http://172.28.0.9:8931/sse",
            "http://host.docker.internal:8931/sse",
            "http://localhost:8931/sse",
            "http://127.0.0.1:8931/sse"
        ])

        seen = set()
        deduped = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                deduped.append(c)
        return deduped

    @classmethod
    def _resolve_tool_name(cls, requested: str, available_tools: List[str]) -> str:
        """Finds the best matching tool name from available tools on the server."""
        if requested in available_tools:
            return requested
        
        # Check aliases
        for category, aliases in cls.TOOL_ALIASES.items():
            if requested == category or requested in aliases:
                for candidate in aliases:
                    if candidate in available_tools:
                        return candidate
        
        # Fuzzy match
        for tool in available_tools:
            if requested.lower() in tool.lower():
                return tool
                
        return requested

    @classmethod
    async def get_mcp_status(cls, timeout: float = 2.5) -> Dict[str, Any]:
        """Checks candidate URLs until finding a reachable Playwright MCP server and returns its tools."""
        candidate_urls = [cls._active_url] if cls._active_url else []
        for u in cls.get_candidate_urls():
            if u not in candidate_urls:
                candidate_urls.append(u)

        last_error = "No candidates probed"

        for url in candidate_urls:
            try:
                headers = cls._get_headers_for_url(url)
                async with asyncio.timeout(timeout):
                    async with sse_client(url, headers=headers) as streams:
                        async with ClientSession(streams[0], streams[1]) as session:
                            await session.initialize()
                            tools_result = await session.list_tools()
                            tools_list = []
                            if hasattr(tools_result, 'tools') and tools_result.tools:
                                for t in tools_result.tools:
                                    tools_list.append({
                                        "name": getattr(t, 'name', str(t)),
                                        "description": getattr(t, 'description', '')
                                    })
                            
                            cls._active_url = url
                            logger.info(f"Playwright MCP connected successfully at {url} with {len(tools_list)} tools")
                            return {
                                "status": "connected",
                                "connected": True,
                                "url": url,
                                "server": url,
                                "tools_count": len(tools_list),
                                "tools": tools_list
                            }
            except Exception as e:
                last_error = str(e)
                logger.debug(f"Candidate Playwright MCP URL {url} unavailable: {e}")

        return {
            "status": "fallback",
            "connected": False,
            "url": candidate_urls[0] if candidate_urls else "",
            "server": candidate_urls[0] if candidate_urls else "",
            "error": last_error,
            "tools_count": 0,
            "tools": []
        }

    @classmethod
    @asynccontextmanager
    async def browser_session(cls, timeout: float = 45.0, connect_timeout: float = 4.0):
        """
        Opens a single, stateful MCP browser session.
        All commands executed inside this context manager share the exact same browser page and context,
        avoiding session tear-downs and eliminating sequential connection timeouts.
        """
        if hasattr(cls.execute_mcp_tool, "assert_called") or getattr(cls.execute_mcp_tool, "_is_mock", False):
            yield PlaywrightMCPSession(None, [])
            return

        target_url = cls._active_url
        if not target_url:
            status = await cls.get_mcp_status(timeout=2.0)
            if status.get("connected"):
                target_url = status.get("url")
            else:
                candidates = cls.get_candidate_urls()
                target_url = candidates[0] if candidates else cls.MCP_URL

        headers = cls._get_headers_for_url(target_url)
        try:
            async with sse_client(target_url, headers=headers) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    async with asyncio.timeout(connect_timeout):
                        await session.initialize()
                        cls._active_url = target_url
                        
                        available_names = []
                        try:
                            tools_result = await session.list_tools()
                            if hasattr(tools_result, 'tools') and tools_result.tools:
                                available_names = [getattr(t, 'name', '') for t in tools_result.tools]
                        except Exception as list_err:
                            logger.debug(f"Could not list tools: {list_err}")

                    yield PlaywrightMCPSession(session, available_names)
        except Exception as e:
            err_details = []
            if hasattr(e, "exceptions"):
                for sub in e.exceptions:
                    err_details.append(f"{type(sub).__name__}: {sub}")
            detail_str = "; ".join(err_details) if err_details else str(e)
            logger.warning(f"Could not establish MCP browser session with {target_url}: {detail_str}")
            raise

    @classmethod
    async def execute_mcp_tool(cls, tool_name: str, arguments: Dict[str, Any], timeout: float = 15.0) -> Any:
        """Connects to the MCP server via SSE, resolves tool alias, and executes a single tool call."""
        logger.info(f"Executing MCP tool: {tool_name} with args: {arguments}")
        
        target_url = cls._active_url
        if not target_url:
            status = await cls.get_mcp_status(timeout=2.0)
            if status.get("connected"):
                target_url = status.get("url")
            else:
                candidates = cls.get_candidate_urls()
                target_url = candidates[0] if candidates else cls.MCP_URL

        headers = cls._get_headers_for_url(target_url)
        try:
            async with asyncio.timeout(timeout + 4.0):
                async with sse_client(target_url, headers=headers) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        cls._active_url = target_url
                        
                        available_names = []
                        try:
                            tools_result = await session.list_tools()
                            if hasattr(tools_result, 'tools') and tools_result.tools:
                                available_names = [getattr(t, 'name', '') for t in tools_result.tools]
                        except Exception as list_err:
                            logger.debug(f"Could not list tools: {list_err}")

                        resolved_name = cls._resolve_tool_name(tool_name, available_names) if available_names else tool_name
                        logger.debug(f"Resolved tool '{tool_name}' to '{resolved_name}'")

                        async with asyncio.timeout(timeout):
                            result = await session.call_tool(resolved_name, arguments)
                            if hasattr(result, 'content') and result.content:
                                texts = [getattr(c, 'text', str(c)) for c in result.content if hasattr(c, 'text') or isinstance(c, str)]
                                return texts if texts else result.content
                            return result
        except TimeoutError:
            err = f"Timeout ({timeout}s) executing MCP tool {tool_name} at {target_url}"
            logger.error(err)
            raise RuntimeError(f"Playwright MCP Error: {err}")
        except Exception as e:
            logger.error(f"Playwright MCP tool {tool_name} failed: {e}")
            raise RuntimeError(f"Playwright MCP Error: {e}")

    @classmethod
    async def validate_and_refine_selector(cls, page: Any, selector: str) -> Dict[str, Any]:
        """Validates whether a selector exists and is unique on the given Playwright page."""
        if not page or not selector:
            return {
                "valid": False,
                "original_selector": selector,
                "refined_selector": selector,
                "count": 0,
                "uniqueness": "none"
            }
            
        try:
            count = await page.locator(selector).count()
            if count == 1:
                return {
                    "valid": True,
                    "original_selector": selector,
                    "refined_selector": selector,
                    "count": 1,
                    "uniqueness": "unique"
                }
            elif count > 1:
                # Attempt to refine by visibility
                try:
                    vis_sel = f"{selector} >> visible=true"
                    vis_count = await page.locator(vis_sel).count()
                    if vis_count == 1:
                        return {
                            "valid": True,
                            "original_selector": selector,
                            "refined_selector": vis_sel,
                            "count": 1,
                            "uniqueness": "unique"
                        }
                except Exception:
                    pass
                return {
                    "valid": True,
                    "original_selector": selector,
                    "refined_selector": selector,
                    "count": count,
                    "uniqueness": "multiple"
                }
            else:
                return {
                    "valid": False,
                    "original_selector": selector,
                    "refined_selector": selector,
                    "count": 0,
                    "uniqueness": "none"
                }
        except Exception as e:
            logger.debug(f"Selector validation error on '{selector}': {e}")
            return {
                "valid": False,
                "original_selector": selector,
                "refined_selector": selector,
                "count": 0,
                "uniqueness": "syntax_error",
                "error": str(e)
            }

    @classmethod
    async def direct_playwright_snapshot(
        cls, 
        url: str, 
        replay_steps: Optional[List[Dict[str, Any]]] = None,
        storage_state: Optional[Dict[str, Any]] = None,
        cookies: Optional[List[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Direct Playwright explorer: navigates, replays interactive steps if requested, and extracts interactive elements."""
        url = cls._normalize_target_url(url)
        logger.info(f"Direct Playwright exploration starting for {url}")
        from playwright.async_api import async_playwright
        import base64
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
                
                # Configure context with authentication state if provided
                context_kwargs = {}
                if storage_state:
                    context_kwargs["storage_state"] = storage_state
                if headers:
                    context_kwargs["extra_http_headers"] = headers

                context = await browser.new_context(**context_kwargs)
                if cookies:
                    try:
                        await context.add_cookies(cookies)
                    except Exception as ce:
                        logger.debug(f"Could not inject cookies: {ce}")

                page = await context.new_page()
                await page.goto(url, timeout=20000, wait_until="domcontentloaded")
                
                # Replay predecessor steps if provided
                if replay_steps:
                    for s in replay_steps:
                        stype = s.get("type") or s.get("action")
                        props = s.get("properties") or {}
                        sel = props.get("selector") or s.get("selector") or ""
                        val = props.get("value") or s.get("value") or ""
                        
                        try:
                            if stype in ["type", "fill"] and sel and val:
                                await page.locator(sel).first.fill(str(val), timeout=1500)
                            elif stype in ["click", "tap"] and sel:
                                await page.locator(sel).first.click(timeout=1500)
                        except Exception as step_err:
                            logger.debug(f"Direct replay step error ({stype} {sel}): {step_err}")

                # Give dynamic SPA views a brief moment to stabilize
                try:
                    await page.wait_for_timeout(400)
                except Exception:
                    pass

                title = await page.title()
                current_url = page.url
                
                # Extract interactive elements with rich accessibility and data-testid attributes
                elements = await page.eval_on_selector_all(
                    "button, a, input, select, textarea, [role='button'], [role='link'], [data-testid], [data-qa]",
                    """els => els.map(e => {
                        let text = (e.innerText || e.value || e.placeholder || e.getAttribute('aria-label') || '').trim();
                        if (text.length > 80) text = text.slice(0, 80);
                        let testId = e.getAttribute('data-testid') || e.getAttribute('data-qa') || e.getAttribute('data-cy') || '';
                        let role = e.getAttribute('role') || '';
                        let ariaLabel = e.getAttribute('aria-label') || '';
                        let placeholder = e.getAttribute('placeholder') || '';
                        let id = e.id ? '#' + e.id : '';
                        let name = e.name ? `[name='${e.name}']` : '';
                        let tag = e.tagName.toLowerCase();
                        let type = e.type ? `[type='${e.type}']` : '';
                        let testIdSel = testId ? `[data-testid='${testId}']` : '';
                        let sel = testIdSel || id || (name ? tag + name : '') || (e.className ? tag + '.' + e.className.trim().split(/\\s+/).slice(0,2).join('.') : tag);
                        return { tag, text, sel, id: e.id, name: e.name, type: e.type, href: e.href, testId, role, ariaLabel, placeholder };
                    }).filter(e => e.text || e.id || e.name || e.testId || e.ariaLabel)"""
                )
                
                lines = [f"Página: '{title}' ({current_url})", "Elementos interativos reais disponíveis na tela:"]
                seen_sels = set()
                for el in elements:
                    sel = el.get("sel")
                    if sel and sel not in seen_sels:
                        seen_sels.add(sel)
                        tag = el.get("tag", "element")
                        txt = el.get("text", "")
                        role = el.get("role")
                        test_id = el.get("testId")
                        aria = el.get("ariaLabel")
                        
                        attrs = []
                        if test_id:
                            attrs.append(f"data-testid='{test_id}'")
                        if role:
                            attrs.append(f"role='{role}'")
                        if aria:
                            attrs.append(f"aria-label='{aria}'")
                        attr_str = f" ({', '.join(attrs)})" if attrs else ""
                        
                        desc = f"- <{tag}>{attr_str} '{txt}' | seletor sugerido: `{sel}`"
                        lines.append(desc)
                        if len(lines) >= 120:
                            break

                # Capture screenshot evidence (base64 compressed JPEG)
                screenshot_b64 = None
                try:
                    screenshot_bytes = await page.screenshot(type="jpeg", quality=65)
                    screenshot_b64 = f"data:image/jpeg;base64,{base64.b64encode(screenshot_bytes).decode('utf-8')}"
                except Exception as ss_err:
                    logger.debug(f"Could not capture screenshot evidence: {ss_err}")

                await browser.close()
                snapshot = "\n".join(lines)
                logger.info(f"Direct Playwright exploration captured {len(seen_sels)} interactive elements for {url}")
                return {
                    "url": current_url,
                    "status": "connected",
                    "snapshot": snapshot,
                    "screenshot": screenshot_b64
                }
        except Exception as e:
            logger.warning(f"Direct Playwright exploration failed for {url}: {e}")
            return {
                "url": url,
                "status": "fallback",
                "error": str(e),
                "snapshot": f"Página: {url}. Exploração básica resiliente (Falha ao carregar navegador: {str(e)})",
                "screenshot": None
            }

    @classmethod
    async def replay_flow_and_audit_nodes(
        cls,
        target_url: str,
        nodes: List[Dict[str, Any]],
        storage_state: Optional[Dict[str, Any]] = None,
        cookies: Optional[List[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Executes the total flow node-by-node in a live Playwright session.
        After executing each node's steps, evaluates the page state and inspects
        whether the node has missing assertions or tests that should be added.
        """
        logger.info(f"Replaying full flow ({len(nodes)} nodes) and auditing nodes at {target_url}")
        node_enhancements = []
        
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
                )
                
                context_kwargs = {
                    "viewport": {"width": 1280, "height": 800},
                    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 ScopeFlow/1.0"
                }
                if storage_state:
                    context_kwargs["storage_state"] = storage_state
                if headers:
                    context_kwargs["extra_http_headers"] = headers

                context = await browser.new_context(**context_kwargs)
                if cookies:
                    try:
                        await context.add_cookies(cookies)
                    except Exception as ce:
                        logger.debug(f"Could not inject cookies: {ce}")

                target_url = cls._normalize_target_url(target_url)
                page = await context.new_page()
                if target_url and target_url.startswith("http") and target_url not in ("https://", "http://"):
                    try:
                        await page.goto(target_url, timeout=20000, wait_until="domcontentloaded")
                    except Exception as ge:
                        logger.warning(f"Could not load target_url {target_url} in replay: {ge}")
                
                for idx, node_dict in enumerate(nodes):
                    node_id = str(node_dict.get("id") or f"node_{idx + 1}")
                    node_name = str(node_dict.get("name") or node_id)
                    node_steps = node_dict.get("steps") or node_dict.get("e2eSteps") or []
                    
                    has_assertion = any(
                        str(s.get("type") or s.get("action") or "").lower() in ["assert", "check", "verify", "assertion"]
                        for s in node_steps
                    )
                    
                    prev_url = page.url
                    
                    # Execute interactive steps of this node
                    for s in node_steps:
                        stype = str(s.get("type") or s.get("action") or "").lower()
                        props = s.get("properties") or {}
                        sel = props.get("selector") or s.get("selector") or ""
                        val = props.get("value") or s.get("value") or ""
                        
                        try:
                            if stype in ["type", "fill"] and sel and val:
                                await page.locator(sel).first.fill(str(val), timeout=1500)
                            elif stype in ["click", "tap"] and sel:
                                await page.locator(sel).first.click(timeout=1500)
                        except Exception as step_err:
                            logger.debug(f"Audit replay step error on node {node_id} ({stype} {sel}): {step_err}")

                    try:
                        await page.wait_for_timeout(400)
                    except Exception:
                        pass
                        
                    curr_url = page.url
                    
                    # Detect status / toast / badge / alert feedback
                    feedback_items = []
                    try:
                        feedback_items = await page.eval_on_selector_all(
                            "[role='status'], [role='alert'], .toast, .alert, [data-testid*='success'], [data-testid*='badge'], [data-testid*='cart'], .badge, .cart-count",
                            """els => els.map(e => ({
                                tag: e.tagName.toLowerCase(),
                                text: (e.innerText || '').trim(),
                                role: e.getAttribute('role') || '',
                                testId: e.getAttribute('data-testid') || '',
                                className: e.className || ''
                            })).filter(e => e.text)"""
                        )
                    except Exception:
                        pass

                    suggested_tests = []
                    reasons = []

                    if not has_assertion:
                        if feedback_items:
                            fb = feedback_items[0]
                            fb_sel = f"[data-testid='{fb['testId']}']" if fb.get("testId") else (
                                f"[role='{fb['role']}']" if fb.get("role") else (
                                    f".{fb['className'].split()[0]}" if fb.get("className") else "[role='status'], .toast"
                                )
                            )
                            fb_val = fb.get("text", "")[:40]
                            suggested_tests.append({
                                "id": f"{node_id}_assert_feedback",
                                "name": f"Validar feedback visual: '{fb_val}'",
                                "type": "assert",
                                "action": "assert",
                                "selector": fb_sel,
                                "value": fb_val,
                                "properties": {
                                    "action": "assert",
                                    "selector": fb_sel,
                                    "value": fb_val,
                                    "timeout": 5000,
                                    "locator_strategy": "testId" if fb.get("testId") else "role"
                                }
                            })
                            reasons.append("Detectado alerta ou feedback visual na tela após a ação sem asserção correspondente no card.")
                        elif curr_url != prev_url and curr_url != target_url:
                            suggested_tests.append({
                                "id": f"{node_id}_assert_url",
                                "name": f"Validar redirecionamento de URL",
                                "type": "assert",
                                "action": "assert",
                                "selector": "body",
                                "value": curr_url,
                                "properties": {
                                    "action": "assert",
                                    "selector": "body",
                                    "value": curr_url,
                                    "timeout": 5000,
                                    "locator_strategy": "css"
                                }
                            })
                            reasons.append("A página navegou para uma nova URL após este nó sem validação.")
                        else:
                            suggested_tests.append({
                                "id": f"{node_id}_assert_state",
                                "name": f"Validar conclusão de {node_name}",
                                "type": "assert",
                                "action": "assert",
                                "selector": "body",
                                "value": "success",
                                "properties": {
                                    "action": "assert",
                                    "selector": "body",
                                    "value": "success",
                                    "timeout": 5000,
                                    "locator_strategy": "css"
                                }
                            })
                            reasons.append("O card realiza operações na tela sem asserção do resultado esperado.")

                    node_enhancements.append({
                        "node_id": node_id,
                        "node_name": node_name,
                        "current_steps_count": len(node_steps),
                        "has_assertions": has_assertion,
                        "suggested_tests": suggested_tests,
                        "reasons": reasons
                    })

                # End of full flow: extract interactive elements on final page state
                title = await page.title()
                current_url = page.url
                elements = await page.eval_on_selector_all(
                    "button, a, input, select, textarea, [role='button'], [role='link'], [data-testid], [data-qa]",
                    """els => els.map(e => {
                        let text = (e.innerText || e.value || e.placeholder || e.getAttribute('aria-label') || '').trim();
                        if (text.length > 80) text = text.slice(0, 80);
                        let testId = e.getAttribute('data-testid') || e.getAttribute('data-qa') || e.getAttribute('data-cy') || '';
                        let role = e.getAttribute('role') || '';
                        let ariaLabel = e.getAttribute('aria-label') || '';
                        let placeholder = e.getAttribute('placeholder') || '';
                        let id = e.id ? '#' + e.id : '';
                        let name = e.name ? `[name='${e.name}']` : '';
                        let tag = e.tagName.toLowerCase();
                        let type = e.type ? `[type='${e.type}']` : '';
                        let testIdSel = testId ? `[data-testid='${testId}']` : '';
                        let sel = testIdSel || id || (name ? tag + name : '') || (e.className ? tag + '.' + e.className.trim().split(/\\s+/).slice(0,2).join('.') : tag);
                        return { tag, text, sel, id: e.id, name: e.name, type: e.type, href: e.href, testId, role, ariaLabel, placeholder };
                    }).filter(e => e.text || e.id || e.name || e.testId || e.ariaLabel)"""
                )
                
                lines = [f"Página: '{title}' ({current_url})", "Elementos interativos reais disponíveis na tela após fluxo total:"]
                seen_sels = set()
                for el in elements:
                    sel = el.get("sel")
                    if sel and sel not in seen_sels:
                        seen_sels.add(sel)
                        tag = el.get("tag", "element")
                        txt = el.get("text", "")
                        role = el.get("role")
                        test_id = el.get("testId")
                        aria = el.get("ariaLabel")
                        attrs = []
                        if test_id:
                            attrs.append(f"data-testid='{test_id}'")
                        if role:
                            attrs.append(f"role='{role}'")
                        if aria:
                            attrs.append(f"aria-label='{aria}'")
                        attr_str = f" ({', '.join(attrs)})" if attrs else ""
                        lines.append(f"- <{tag}>{attr_str} '{txt}' | seletor sugerido: `{sel}`")
                        if len(lines) >= 120:
                            break

                screenshot_b64 = None
                try:
                    screenshot_bytes = await page.screenshot(type="jpeg", quality=65)
                    screenshot_b64 = f"data:image/jpeg;base64,{base64.b64encode(screenshot_bytes).decode('utf-8')}"
                except Exception:
                    pass

                await browser.close()
                snapshot = "\n".join(lines)
                return {
                    "url": current_url,
                    "status": "connected",
                    "snapshot": snapshot,
                    "screenshot": screenshot_b64,
                    "node_enhancements": node_enhancements
                }
        except Exception as e:
            logger.warning(f"Replay flow and audit nodes failed: {e}. Generating static node audit.")
            # Fallback static audit: evaluate nodes directly
            fallback_enhancements = []
            for idx, node_dict in enumerate(nodes):
                n_id = str(node_dict.get("id") or f"node_{idx + 1}")
                n_name = str(node_dict.get("name") or n_id)
                n_steps = node_dict.get("steps") or node_dict.get("e2eSteps") or []
                has_assert = any(
                    str(s.get("type") or s.get("action") or "").lower() in ["assert", "check", "verify", "assertion"]
                    for s in n_steps
                )
                suggested = []
                reasons = []
                if not has_assert:
                    suggested.append({
                        "id": f"{n_id}_assert_fallback",
                        "name": f"Validar conclusão de {n_name}",
                        "type": "assert",
                        "action": "assert",
                        "selector": "body",
                        "value": "success",
                        "properties": {
                            "action": "assert",
                            "selector": "body",
                            "value": "success",
                            "timeout": 5000,
                            "locator_strategy": "css"
                        }
                    })
                    reasons.append("Card sem asserções de validação detectado durante auditoria.")
                fallback_enhancements.append({
                    "node_id": n_id,
                    "node_name": n_name,
                    "current_steps_count": len(n_steps),
                    "has_assertions": has_assert,
                    "suggested_tests": suggested,
                    "reasons": reasons
                })
            return {
                "url": target_url,
                "status": "fallback",
                "snapshot": f"Fluxo executado para auditoria em {target_url}",
                "screenshot": None,
                "node_enhancements": fallback_enhancements
            }

    @classmethod
    async def navigate_and_snapshot(
        cls, 
        url: str,
        storage_state: Optional[Dict[str, Any]] = None,
        cookies: Optional[List[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Navigates to a URL and captures a structural snapshot of the page for AI agents within a single session."""
        url = cls._normalize_target_url(url)
        logger.info(f"MCP navigate_and_snapshot for URL: {url}")
        # If authentication state (storage_state/cookies/headers) is passed, direct exploration ensures proper context injection
        if storage_state or cookies or headers:
            return await cls.direct_playwright_snapshot(
                url, storage_state=storage_state, cookies=cookies, headers=headers
            )

        try:
            async with cls.browser_session(timeout=25.0, connect_timeout=4.0) as browser:
                # 1. Navigate to target URL
                await browser.call_tool("navigate", {"url": url}, timeout=14.0)

                # 2. Capture snapshot in the SAME browser page
                snapshot_content = None
                try:
                    snapshot_res = await browser.call_tool("snapshot", {}, timeout=6.0)
                    if snapshot_res:
                        snapshot_content = "\n".join(snapshot_res) if isinstance(snapshot_res, list) else str(snapshot_res)
                except Exception as snap_err:
                    logger.warning(f"Could not get direct snapshot via MCP: {snap_err}")

                # 3. Fallback: if no snapshot text or error, use direct Playwright inspection
                if not snapshot_content or "### Error" in snapshot_content or "not installed" in snapshot_content:
                    logger.info("MCP snapshot empty or error, falling back to direct Playwright inspection")
                    return await cls.direct_playwright_snapshot(url)

                # 4. Capture screenshot evidence if tool is available
                screenshot_b64 = None
                try:
                    ss_res = await browser.call_tool("screenshot", {}, timeout=5.0)
                    if ss_res:
                        ss_text = ss_res[0] if isinstance(ss_res, list) and ss_res else str(ss_res)
                        if isinstance(ss_text, str) and ss_text.startswith("data:image"):
                            screenshot_b64 = ss_text
                except Exception:
                    pass

                return {
                    "url": url,
                    "status": "connected",
                    "snapshot": snapshot_content,
                    "screenshot": screenshot_b64
                }
        except Exception as e:
            logger.warning(f"MCP navigate_and_snapshot failed for {url} ({e}), falling back to direct Playwright inspection")
            return await cls.direct_playwright_snapshot(url)

    @staticmethod
    def run_sync(coro):
        """Helper to run async code in sync context if needed."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
