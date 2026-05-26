import os
import asyncio
import logging
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

class MCPPlaywrightService:
    """Service to interact with the Playwright MCP server running in a separate container."""
    
    # The URL of the MCP server as defined in docker-compose
    MCP_URL = os.getenv("MCP_PLAYWRIGHT_URL", "http://mcp-playwright:8931/mcp")

    @staticmethod
    async def execute_mcp_tool(tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Connects to the MCP server via SSE and executes a specific tool."""
        logger.info(f"Executing MCP tool: {tool_name} with args: {arguments}")
        
        try:
            async with sse_client(MCPPlaywrightService.MCP_URL) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    # Initialize the session
                    await session.initialize()
                    
                    # Call the tool
                    result = await session.call_tool(tool_name, arguments)
                    
                    # MCP tools return a content list (usually text)
                    if hasattr(result, 'content'):
                        return [c.text for c in result.content if hasattr(c, 'text')]
                    return result
        except Exception as e:
            logger.error(f"Error executing MCP tool {tool_name}: {e}")
            raise RuntimeError(f"Playwright MCP Error: {str(e)}")

    @staticmethod
    async def inspect_url(url: str) -> Dict[str, Any]:
        """A high-level helper that navigates to a URL and returns accessibility/structure info."""
        try:
            # 1. Navigate
            await MCPPlaywrightService.execute_mcp_tool("playwright_navigate", {"url": url})
            
            # 2. Get Accessibility Tree or Screenshot (Metadata)
            # The official playwright-mcp often has 'playwright_screenshot' or 'playwright_get_console_logs'
            # For inspection, we usually want the page data.
            # Assuming the tool names follow the @playwright/mcp convention:
            
            # Note: The exact tool names depend on the version of @playwright/mcp
            # We will use 'playwright_screenshot' for visual verification in this helper.
            screenshot_result = await MCPPlaywrightService.execute_mcp_tool("playwright_screenshot", {})
            
            return {
                "url": url,
                "status": "success",
                "screenshot": screenshot_result[0] if screenshot_result else None
            }
        except Exception as e:
            return {"url": url, "status": "error", "message": str(e)}

    @staticmethod
    def run_sync(coro):
        """Helper to run async code in sync context if needed (e.g. from FastAPI sync routes)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
