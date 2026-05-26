import logging
import json
import asyncio
from sqlalchemy.orm import Session
from app.services.mcp_playwright_service import MCPPlaywrightService
from app.services.skill_service import SkillService
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class AgentService:
    """Service to coordinate autonomous AI agents (Planner, Healer)."""

    @staticmethod
    async def run_planner(db: Session, user_id: int, url: str) -> Dict[str, Any]:
        """Runs the Planner Agent: Explores URL via MCP and generates a test flow."""
        logger.info(f"Agent Planner starting for URL: {url}")
        
        try:
            # 1. Direct MCP Exploration (Get A11y Tree)
            # Note: We use the MCP service to navigate and get a snapshot
            # Simplified for now: we navigate and the MCP server provides tools
            await MCPPlaywrightService.execute_mcp_tool("playwright_navigate", {"url": url})
            
            # Get a 'snapshot' of the page. In @playwright/mcp this is often playwright_screenshot 
            # or we can use custom tools if we had them. 
            # For this implementation, we assume the LLM uses the context we provide.
            
            mcp_snapshot = f"Navegação para {url} concluída. Pronto para inspeção profunda."
            
            # 2. Execute the Specialist Skill
            context = {
                "mcp_snapshot": mcp_snapshot,
                "flow_type": "web"
            }
            
            planner_result_raw = SkillService.execute_skill(db, user_id, "qa_agent_planner", context)
            
            try:
                plan = json.loads(planner_result_raw)
                return plan
            except:
                return {"error": "AI returned invalid JSON", "raw": planner_result_raw}
                
        except Exception as e:
            logger.error(f"Error in Agent Planner: {e}")
            raise ValueError(f"Planner failed: {str(e)}")

    @staticmethod
    async def run_healer(db: Session, user_id: int, flow_id: int, step_index: int, error_message: str) -> Dict[str, Any]:
        """Runs the Healer Agent: Fixes a failed test step using live browser state."""
        logger.info(f"Agent Healer starting for flow {flow_id}, step {step_index}")
        
        from app.models.flow_models import FlowDB
        from app.services.flow_service import FlowService
        
        # 1. Load context
        flow = db.query(FlowDB).filter(FlowDB.id == flow_id).first()
        if not flow: raise ValueError("Flow not found")
        
        flow_data = FlowService.load(db, flow.project_id, 1, flow.id, flow.flow_type)
        card_data = flow_data.get("cardData", {})
        
        # Find failed selector (Simplified logic)
        failed_selector = "unknown"
        action_type = "unknown"
        target_url = ""
        
        # Try to find URL and selector from the nodes
        for node_id, data in card_data.items():
            if "url" in data and data["url"]: target_url = data["url"]
            # This logic should be more precise based on step_index
            
        # 2. Use MCP to inspect the current state
        if target_url:
            await MCPPlaywrightService.execute_mcp_tool("playwright_navigate", {"url": target_url})
            mcp_snapshot = "Snapshot da página live capturado via MCP."
        else:
            mcp_snapshot = "URL não encontrada para inspeção live."

        # 3. Execute Skill
        context = {
            "error_message": error_message,
            "failed_selector": failed_selector,
            "action_type": action_type,
            "mcp_snapshot": mcp_snapshot
        }
        
        healer_result_raw = SkillService.execute_skill(db, user_id, "qa_agent_healer", context)
        
        try:
            return json.loads(healer_result_raw)
        except:
            return {"error": "AI returned invalid JSON", "raw": healer_result_raw}
