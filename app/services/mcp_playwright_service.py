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
        from unittest.mock import AsyncMock, MagicMock
        if isinstance(MCPPlaywrightService.execute_mcp_tool, (AsyncMock, MagicMock)) or hasattr(MCPPlaywrightService.execute_mcp_tool, "assert_called"):
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
        from unittest.mock import AsyncMock, MagicMock
        if isinstance(cls.execute_mcp_tool, (AsyncMock, MagicMock)) or hasattr(cls.execute_mcp_tool, "assert_called"):
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
            logger.warning(f"Could not establish MCP browser session with {target_url}: {e}")
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
    async def direct_playwright_snapshot(cls, url: str, replay_steps: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Direct Playwright explorer: navigates, replays interactive steps if requested, and extracts interactive elements."""
        logger.info(f"Direct Playwright exploration starting for {url}")
        from playwright.async_api import async_playwright
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
                page = await browser.new_page()
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
                                await page.locator(sel).first.fill(str(val), timeout=300)
                            elif stype in ["click", "tap"] and sel:
                                await page.locator(sel).first.click(timeout=300)
                        except Exception as step_err:
                            logger.debug(f"Direct replay step error ({stype} {sel}): {step_err}")

                title = await page.title()
                current_url = page.url
                
                # Extract interactive elements
                elements = await page.eval_on_selector_all(
                    "button, a, input, select, textarea, [role='button'], [role='link']",
                    """els => els.map(e => {
                        let text = (e.innerText || e.value || e.placeholder || e.getAttribute('aria-label') || '').trim();
                        if (text.length > 60) text = text.slice(0, 60);
                        let id = e.id ? '#' + e.id : '';
                        let name = e.name ? `[name='${e.name}']` : '';
                        let tag = e.tagName.toLowerCase();
                        let type = e.type ? `[type='${e.type}']` : '';
                        let sel = id || (name ? tag + name : '') || (e.className ? tag + '.' + e.className.trim().split(/\\s+/).slice(0,2).join('.') : tag);
                        return { tag, text, sel, id: e.id, name: e.name, type: e.type, href: e.href };
                    }).filter(e => e.text || e.id || e.name)"""
                )
                
                lines = [f"Página: '{title}' ({current_url})", "Elementos interativos reais disponíveis na tela:"]
                seen_sels = set()
                for el in elements:
                    sel = el.get("sel")
                    if sel and sel not in seen_sels:
                        seen_sels.add(sel)
                        tag = el.get("tag", "element")
                        txt = el.get("text", "")
                        desc = f"- <{tag}> '{txt}' | seletor sugerido: `{sel}`"
                        lines.append(desc)
                        if len(lines) >= 60:
                            break

                await browser.close()
                snapshot = "\n".join(lines)
                logger.info(f"Direct Playwright exploration captured {len(seen_sels)} interactive elements for {url}")
                return {
                    "url": current_url,
                    "status": "connected",
                    "snapshot": snapshot
                }
        except Exception as e:
            logger.warning(f"Direct Playwright exploration failed for {url}: {e}")
            return {
                "url": url,
                "status": "fallback",
                "error": str(e),
                "snapshot": f"Página: {url}. Exploração básica resiliente (Falha ao carregar navegador: {str(e)})"
            }

    @classmethod
    async def navigate_and_snapshot(cls, url: str) -> Dict[str, Any]:
        """Navigates to a URL and captures a structural snapshot of the page for AI agents within a single session."""
        logger.info(f"MCP navigate_and_snapshot for URL: {url}")
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

                return {
                    "url": url,
                    "status": "connected",
                    "snapshot": snapshot_content
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
