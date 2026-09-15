import os
import logging
import json
import asyncio
import re
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from app.services.mcp_playwright_service import MCPPlaywrightService
from app.services.skill_service import SkillService

logger = logging.getLogger(__name__)

def _extract_json(text: str) -> Any:
    """Extracts valid JSON from an LLM response string, auto-stripping comments and repairing structure."""
    if not text:
        return {}
    from app.services.skill_service import _robust_json_parse
    try:
        parsed = _robust_json_parse(text)
        if isinstance(parsed, (dict, list)):
            return parsed
    except Exception:
        pass
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except Exception:
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            return json.loads(cleaned[first_brace:last_brace + 1])
        first_bracket = cleaned.find("[")
        last_bracket = cleaned.rfind("]")
        if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
            return json.loads(cleaned[first_bracket:last_bracket + 1])
        raise

def _prune_mcp_snapshot(snapshot: str, max_chars: int = 6000) -> str:
    """
    Limits large accessibility trees or HTML snapshots to avoid LLM token bloat and timeouts,
    prioritizing interactive elements, landmarks, inputs, buttons, and headings.
    """
    if not snapshot or len(snapshot) <= max_chars:
        return snapshot

    lines = snapshot.splitlines()
    interactive_keywords = (
        "button", "link", "textbox", "input", "select", "checkbox", "radio",
        "menu", "heading", "form", "dialog", "navigation", "main", "tab",
        "search", "combobox", "option", "alert"
    )
    
    selected_lines = []
    other_lines = []
    
    for line in lines:
        lower = line.lower()
        if any(kw in lower for kw in interactive_keywords):
            selected_lines.append(line)
        else:
            other_lines.append(line)
            
    result = []
    current_len = 0
    
    for line in selected_lines:
        if current_len + len(line) + 1 > max_chars:
            break
        result.append(line)
        current_len += len(line) + 1
        
    if current_len < max_chars:
        for line in other_lines:
            if current_len + len(line) + 1 > max_chars:
                break
            result.append(line)
            current_len += len(line) + 1
            
    logger.info(f"Pruned MCP snapshot from {len(snapshot)} to {current_len} chars ({len(result)} lines)")
    return "\n".join(result)

class AgentService:
    """Service to coordinate autonomous AI agents (Planner, Healer)."""

    @staticmethod
    async def run_planner(
        db: Session, 
        user_id: int, 
        url: str = "",
        start_mode: str = "scratch",
        existing_flow_context: Optional[Dict[str, Any]] = None,
        instruction: Optional[str] = None,
        start_node_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Runs the Planner Agent: Explores URL via MCP or continues from existing automated steps and generates a test flow."""
        logger.info(f"Agent Planner starting (mode={start_mode}) for URL: '{url}' (start_node_id={start_node_id})")
        
        try:
            # 1. Resolve Target URL and Existing Steps if continuing from existing automation
            existing_steps = []
            target_url = url
            
            if existing_flow_context and isinstance(existing_flow_context, dict):
                ctx_steps = existing_flow_context.get("steps") or []
                if isinstance(ctx_steps, list):
                    existing_steps = ctx_steps
                
                # If target_url wasn't provided directly, search previous steps for a browser navigation URL
                if not target_url:
                    for s in existing_steps:
                        props = s.get("properties") or {}
                        val = props.get("value") or s.get("value") or ""
                        if (s.get("type") == "browser" or s.get("action") == "navigate") and isinstance(val, str) and val.startswith("http"):
                            target_url = val
                            break
                    if not target_url:
                        target_url = existing_flow_context.get("target_url") or ""

            if not target_url:
                target_url = "https://"

            # 2. Explore or Replay via MCP Playwright
            snapshot_info = {"status": "fallback", "snapshot": ""}
            mcp_snapshot = ""

            if start_mode == "from_existing" and existing_steps:
                logger.info(f"Replaying {len(existing_steps)} existing automated steps to reach pre-condition state at {target_url}")
                try:
                    async with MCPPlaywrightService.browser_session(timeout=10.0, connect_timeout=1.5) as browser:
                        # Navigate to base URL first
                        await browser.call_tool("navigate", {"url": target_url}, timeout=3.0)
                        
                        # Replay non-navigation interactive steps up to the starting node in the SAME session
                        for step in existing_steps:
                            stype = step.get("type") or step.get("action")
                            props = step.get("properties") or {}
                            selector = props.get("selector") or step.get("selector") or ""
                            value = props.get("value") or step.get("value") or ""

                            if stype in ["type", "fill"] and selector and value:
                                try:
                                    await browser.call_tool("type", {"selector": selector, "text": str(value)}, timeout=1.0)
                                except Exception as te:
                                    logger.debug(f"Step replay type error on selector {selector}: {te}")
                            elif stype in ["click", "tap"] and selector:
                                try:
                                    await browser.call_tool("click", {"selector": selector}, timeout=1.0)
                                except Exception as ce:
                                    logger.debug(f"Step replay click error on selector {selector}: {ce}")
                        
                        # Take snapshot of the resulting advanced page state
                        snap_res = await browser.call_tool("snapshot", {}, timeout=2.0)
                        if snap_res:
                            mcp_snapshot = "\n".join(snap_res) if isinstance(snap_res, list) else str(snap_res)
                            snapshot_info = {"status": "connected", "snapshot": mcp_snapshot}
                except Exception as replay_err:
                    logger.warning(f"MCP step replay failed: {replay_err}. Falling back to direct Playwright explorer with step replay.")
                    snapshot_info = await MCPPlaywrightService.direct_playwright_snapshot(target_url, replay_steps=existing_steps)
                    mcp_snapshot = snapshot_info.get("snapshot", "")

                if not mcp_snapshot or "### Error" in mcp_snapshot or "not installed" in mcp_snapshot or "Browser" in mcp_snapshot:
                    snapshot_info = await MCPPlaywrightService.direct_playwright_snapshot(target_url, replay_steps=existing_steps)
                    mcp_snapshot = snapshot_info.get("snapshot", "")
            else:
                # Direct MCP exploration from initial URL
                snapshot_info = await MCPPlaywrightService.navigate_and_snapshot(target_url)
                mcp_snapshot = snapshot_info.get("snapshot", "")
                if not mcp_snapshot or "### Error" in mcp_snapshot or "not installed" in mcp_snapshot or "Browser" in mcp_snapshot:
                    logger.info("MCP snapshot had error or was empty, falling back to direct Playwright exploration.")
                    snapshot_info = await MCPPlaywrightService.direct_playwright_snapshot(target_url)
                    mcp_snapshot = snapshot_info.get("snapshot", "")

            # Prune snapshot to eliminate token bloat and avoid LLM timeouts
            pruned_mcp_snapshot = _prune_mcp_snapshot(mcp_snapshot)

            # 3. Build summary of existing automated steps for the LLM
            step_summaries = []
            for idx, s in enumerate(existing_steps):
                stype = s.get("type") or s.get("action") or "action"
                sname = s.get("name") or f"Passo {idx + 1}"
                props = s.get("properties") or {}
                sel = props.get("selector") or s.get("selector") or ""
                val = props.get("value") or s.get("value") or ""
                details = f"seletor='{sel}'" if sel else ""
                if val:
                    details += f", valor='{val}'"
                step_summaries.append(f"- Passo {idx + 1} ({stype}) '{sname}': {details}")
            
            existing_steps_summary = "\n".join(step_summaries) if step_summaries else "Nenhum passo anterior (iniciando fluxo do zero)."

            # 4. Execute Specialist Skill (qa_agent_planner)
            context = {
                "target_url": target_url,
                "mcp_snapshot": pruned_mcp_snapshot,
                "existing_steps_summary": existing_steps_summary,
                "instruction": instruction or "Continue a jornada do usuário explorando e mapeando os próximos elementos interativos da tela.",
                "flow_type": "web"
            }
            
            if not os.getenv("PYTEST_CURRENT_TEST"):
                import sys
                import importlib
                for m in ['app.services.analysis_service', 'app.services.skill_service']:
                    if m in sys.modules:
                        try:
                            importlib.reload(sys.modules[m])
                        except Exception:
                            pass
            import app.services.skill_service as skill_module
            planner_result_raw = await asyncio.to_thread(skill_module.SkillService.execute_skill, db, user_id, "qa_agent_planner", context)
            
            try:
                plan = _extract_json(planner_result_raw)
            except Exception as pe:
                logger.warning(f"Failed to parse Planner LLM response as JSON: {pe}. Raw: {planner_result_raw}")
                return {
                    "error": "AI returned invalid JSON",
                    "raw": planner_result_raw,
                    "mcp_status": snapshot_info.get("status", "unknown")
                }
            
            # 5. Normalize into ScopeFlow Canvas format (nodes, edges, cardData)
            raw_nodes = plan.get("nodes", [])
            raw_card_data = plan.get("cardData", {}) or {}
            
            # If nodes array is empty, recover from cardData or steps
            if not raw_nodes and raw_card_data:
                for k, v in raw_card_data.items():
                    raw_nodes.append({"id": k, "data": v, "position": {"x": 100 + len(raw_nodes) * 280, "y": 180}})
            elif not raw_nodes and (plan.get("steps") or plan.get("e2eSteps")):
                raw_nodes = [{
                    "id": "node_1",
                    "data": {
                        "name": "Exploração dos Elementos",
                        "nodeType": "web",
                        "e2eSteps": plan.get("steps") or plan.get("e2eSteps")
                    }
                }]
            elif not raw_nodes:
                raw_nodes = [{
                    "id": "node_1",
                    "data": {
                        "name": "Próxima Ação Mapeada",
                        "nodeType": "web",
                        "e2eSteps": []
                    }
                }]

            normalized_nodes = []
            card_data = {}
            
            for idx, n in enumerate(raw_nodes):
                node_id = str(n.get("id") or f"node_{idx + 1}")
                ndata = n.get("data", {})
                if not isinstance(ndata, dict):
                    ndata = {}
                
                raw_card_entry = (
                    raw_card_data.get(node_id)
                    or raw_card_data.get(str(idx + 1))
                    or raw_card_data.get(f"node_{idx + 1}")
                    or {}
                )
                if not isinstance(raw_card_entry, dict):
                    raw_card_entry = {}
                
                # Ensure nodeType is web
                ndata["id"] = node_id
                ndata["nodeType"] = ndata.get("nodeType") or raw_card_entry.get("nodeType") or "web"
                node_name = ndata.get("name") or raw_card_entry.get("name") or f"Passo {idx + 1}"
                ndata["name"] = node_name
                node_desc = ndata.get("description") or raw_card_entry.get("description") or ""
                ndata["description"] = node_desc

                # Extract steps from all candidate locations
                candidate_steps = (
                    ndata.get("e2eSteps")
                    or raw_card_entry.get("e2eSteps")
                    or ndata.get("steps")
                    or raw_card_entry.get("steps")
                    or ndata.get("e2e_steps")
                    or raw_card_entry.get("e2e_steps")
                    or n.get("e2eSteps")
                    or n.get("steps")
                    or []
                )

                normalized_steps = []
                if isinstance(candidate_steps, list):
                    for s_idx, s in enumerate(candidate_steps):
                        if not isinstance(s, dict):
                            continue
                        
                        s_id = str(s.get("id") or f"{node_id}_step_{s_idx + 1}")
                        raw_type = str(s.get("type") or s.get("action") or "click").lower().strip()
                        
                        # Normalize type/action
                        if raw_type in ["fill", "input", "write"]:
                            norm_type = "type"
                        elif raw_type in ["navigate", "open", "url", "goto"]:
                            norm_type = "browser"
                        elif raw_type in ["check", "assertion", "verify", "validate"]:
                            norm_type = "assert"
                        elif raw_type in ["tap", "press"]:
                            norm_type = "click"
                        elif raw_type in ["click", "type", "browser", "assert", "select", "hover", "scroll"]:
                            norm_type = raw_type
                        else:
                            norm_type = "click"

                        props = s.get("properties") if isinstance(s.get("properties"), dict) else {}
                        selector = str(props.get("selector") or s.get("selector") or "").strip()
                        value = str(props.get("value") or s.get("value") or "").strip()
                        step_name = s.get("name") or s.get("description") or f"{norm_type.capitalize()} {selector or value or node_name}"

                        # Sanitize missing selectors / values based on action type
                        if norm_type == "click" and not selector:
                            words = [w for w in re.sub(r"[^a-zA-Z0-9\s]", "", step_name).split() if len(w) > 3 and w.lower() not in ["clicar", "botao", "passo", "fazer", "elemento"]]
                            if words:
                                kw = words[-1]
                                selector = f"button:has-text('{kw}'), a:has-text('{kw}')"
                            else:
                                selector = "button, a"
                        elif norm_type == "type":
                            if not selector:
                                selector = "input:not([type='hidden']), textarea"
                            if not value:
                                combined_desc = (step_name + " " + selector).lower()
                                if "email" in combined_desc:
                                    value = "teste@exemplo.com"
                                elif "senh" in combined_desc or "pass" in combined_desc:
                                    value = "Senha@123"
                                elif "busc" in combined_desc or "search" in combined_desc:
                                    value = "produto"
                                else:
                                    value = "teste"
                        elif norm_type == "assert":
                            if not selector:
                                selector = "body"
                            if not value:
                                value = step_name or "success"

                        normalized_step = {
                            "id": s_id,
                            "name": step_name,
                            "type": norm_type,
                            "action": norm_type,
                            "selector": selector,
                            "value": value,
                            "properties": {
                                "selector": selector,
                                "value": value,
                                "action": norm_type,
                                "timeout": props.get("timeout", 5000),
                                **{k: v for k, v in props.items() if k not in ["selector", "value", "action", "timeout"]}
                            }
                        }
                        normalized_steps.append(normalized_step)

                # In from_existing mode, remove redundant initial browser navigation to avoid resetting predecessor state
                if start_mode == "from_existing" and idx == 0 and normalized_steps:
                    first_stype = normalized_steps[0].get("type")
                    if first_stype == "browser":
                        if len(normalized_steps) > 1:
                            logger.info(f"Removing redundant initial browser step in from_existing mode: {normalized_steps[0]['name']}")
                            normalized_steps.pop(0)
                        else:
                            logger.info(f"Converting redundant browser step to assertion in from_existing mode: {normalized_steps[0]['name']}")
                            normalized_steps[0]["type"] = "assert"
                            normalized_steps[0]["action"] = "assert"
                            normalized_steps[0]["name"] = "Verificar estado atual da página"
                            normalized_steps[0]["selector"] = "body"
                            normalized_steps[0]["value"] = "success"
                            normalized_steps[0]["properties"]["action"] = "assert"
                            normalized_steps[0]["properties"]["selector"] = "body"
                            normalized_steps[0]["properties"]["value"] = "success"

                # Fallback: A node must NEVER have 0 steps!
                if not normalized_steps:
                    logger.info(f"Node '{node_name}' ({node_id}) has 0 steps. Generating fallback contextual step.")
                    norm_name_lower = node_name.lower()
                    if any(k in norm_name_lower for k in ["validar", "verificar", "assert", "sucesso", "bem-sucedid"]):
                        fallback_type = "assert"
                        fallback_sel = "body"
                        fallback_val = "success"
                        step_desc = f"Verificar: {node_name}"
                    elif any(k in norm_name_lower for k in ["digitar", "preencher", "informar", "input", "escrever"]):
                        fallback_type = "type"
                        fallback_sel = "input:not([type='hidden']), textarea"
                        fallback_val = "teste"
                        step_desc = f"Preencher: {node_name}"
                    elif any(k in norm_name_lower for k in ["abrir", "navegar", "acessar", "url"]):
                        fallback_type = "browser"
                        fallback_sel = ""
                        fallback_val = target_url
                        step_desc = f"Acessar URL: {target_url}"
                    else:
                        fallback_type = "click"
                        words = [w for w in re.sub(r"[^a-zA-Z0-9\s]", "", node_name).split() if len(w) > 3 and w.lower() not in ["clicar", "botao", "passo", "fazer"]]
                        if words:
                            kw = words[-1]
                            fallback_sel = f"button:has-text('{kw}'), a:has-text('{kw}')"
                        else:
                            fallback_sel = "button, a"
                        fallback_val = ""
                        step_desc = f"Clicar: {node_name}"

                    normalized_steps.append({
                        "id": f"{node_id}_step_1",
                        "name": step_desc,
                        "type": fallback_type,
                        "action": fallback_type,
                        "selector": fallback_sel,
                        "value": fallback_val,
                        "properties": {
                            "selector": fallback_sel,
                            "value": fallback_val,
                            "action": fallback_type,
                            "timeout": 5000
                        }
                    })

                # Put normalized steps into both ndata and card_data
                ndata["e2eSteps"] = normalized_steps
                
                # Position layout
                pos = n.get("position")
                if not pos or not isinstance(pos, dict) or "x" not in pos or "y" not in pos:
                    pos = {"x": 100 + (idx * 280), "y": 180}
                    
                normalized_node = {
                    "id": node_id,
                    "type": n.get("type", "custom"),
                    "position": pos,
                    "data": ndata
                }
                normalized_nodes.append(normalized_node)
                card_data[node_id] = {
                    "id": node_id,
                    "name": node_name,
                    "nodeType": ndata["nodeType"],
                    "description": node_desc,
                    "e2eSteps": normalized_steps,
                    "bddScenarios": raw_card_entry.get("bddScenarios", []),
                    "apiCalls": raw_card_entry.get("apiCalls", []),
                    "dbQueries": raw_card_entry.get("dbQueries", [])
                }
                
            # Normalize edges
            raw_edges = plan.get("edges", [])
            normalized_edges = []
            if raw_edges:
                for idx, e in enumerate(raw_edges):
                    edge_id = str(e.get("id") or f"edge_{idx + 1}")
                    source = str(e.get("source", ""))
                    target = str(e.get("target", ""))
                    if source and target:
                        normalized_edges.append({
                            "id": edge_id,
                            "source": source,
                            "target": target
                        })
            else:
                # Auto-generate sequential edges if LLM did not provide them
                for i in range(len(normalized_nodes) - 1):
                    src = normalized_nodes[i]["id"]
                    tgt = normalized_nodes[i + 1]["id"]
                    normalized_edges.append({
                        "id": f"edge_{src}_{tgt}",
                        "source": src,
                        "target": tgt
                    })
                    
            return {
                "name": plan.get("name", f"Fluxo Playwright - {target_url}"),
                "flow_type": "web",
                "nodes": normalized_nodes,
                "edges": normalized_edges,
                "cardData": card_data,
                "start_mode": start_mode,
                "start_node_id": start_node_id,
                "mcp_status": snapshot_info.get("status", "unknown")
            }
                
        except Exception as e:
            logger.error(f"Error in Agent Planner: {e}")
            raise ValueError(f"Planner failed: {str(e)}")

    @staticmethod
    async def run_healer(
        db: Session,
        user_id: int,
        flow_id: int,
        step_index: int = 0,
        error_message: str = "",
        node_id: Optional[str] = None,
        failed_selector: Optional[str] = None,
        action_type: Optional[str] = None,
        target_url: Optional[str] = None,
        apply_fix: bool = False
    ) -> Dict[str, Any]:
        """Runs the Healer Agent: Fixes a failed test step using live browser state via MCP."""
        logger.info(f"Agent Healer starting for flow {flow_id}, step {step_index}")
        
        from app.models.flow_models import FlowDB
        from app.services.flow_service import FlowService
        
        # 1. Load context
        flow = db.query(FlowDB).filter(FlowDB.id == flow_id).first()
        if not flow:
            raise ValueError(f"Fluxo {flow_id} não encontrado")
        
        company_id = flow.company_id or 1
        flow_data = FlowService.load(db, flow.project_id, company_id, flow.id, flow.flow_type)
        card_data = flow_data.get("cardData", {})
        
        # Resolve target node & step details if not explicitly passed
        target_node = None
        if node_id and node_id in card_data:
            target_node = card_data[node_id]
        
        if not target_node:
            for nid, ndata in card_data.items():
                steps = ndata.get("e2eSteps", [])
                if 0 <= step_index < len(steps):
                    target_node = ndata
                    node_id = nid
                    break
        
        if target_node:
            steps = target_node.get("e2eSteps", [])
            if 0 <= step_index < len(steps):
                step = steps[step_index]
                props = step.get("properties", {})
                if not failed_selector:
                    failed_selector = props.get("selector", "")
                if not action_type:
                    action_type = props.get("action") or step.get("action") or step.get("type") or "click"
        
        # Search for target URL if not provided
        if not target_url:
            for nid, ndata in card_data.items():
                if ndata.get("url"):
                    target_url = ndata["url"]
                    break
                for st in ndata.get("e2eSteps", []):
                    st_props = st.get("properties", {})
                    if st.get("action") == "browser" and st_props.get("url"):
                        target_url = st_props["url"]
                        break
                if target_url:
                    break

        # 2. Use MCP to inspect the current state
        if target_url:
            snapshot_info = await MCPPlaywrightService.navigate_and_snapshot(target_url)
            mcp_snapshot = snapshot_info.get("snapshot", "")
            mcp_status = snapshot_info.get("status", "unknown")
        else:
            mcp_snapshot = "URL não fornecida para inspeção live."
            mcp_status = "no_url"

        # 3. Execute Skill
        context = {
            "error_message": error_message or "Element not found or timed out",
            "failed_selector": failed_selector or "unknown",
            "action_type": action_type or "click",
            "mcp_snapshot": _prune_mcp_snapshot(mcp_snapshot, max_chars=12000)
        }
        
        if not os.getenv("PYTEST_CURRENT_TEST"):
            import sys
            import importlib
            for m in ['app.services.analysis_service', 'app.services.skill_service']:
                if m in sys.modules:
                    try:
                        importlib.reload(sys.modules[m])
                    except Exception:
                        pass
        import app.services.skill_service as skill_module
        healer_result_raw = await asyncio.to_thread(skill_module.SkillService.execute_skill, db, user_id, "qa_agent_healer", context)
        
        try:
            result = _extract_json(healer_result_raw)
        except Exception as pe:
            logger.warning(f"Failed to parse Healer LLM response as JSON: {pe}. Raw: {healer_result_raw}")
            return {
                "error": "AI returned invalid JSON",
                "raw": healer_result_raw,
                "mcp_status": mcp_status
            }

        # Normalize result fields
        if not result.get("node_id") and node_id:
            result["node_id"] = str(node_id)
        if not result.get("original_selector") and failed_selector:
            result["original_selector"] = failed_selector
        result["mcp_status"] = mcp_status

        # 4. Auto-apply healing fix if requested
        if apply_fix and result.get("status") == "healed" and result.get("recommended_selector") and failed_selector and node_id:
            try:
                FlowService.apply_healing(
                    db=db,
                    project_id=flow.project_id,
                    company_id=company_id,
                    flow_id=flow.id,
                    node_id=str(node_id),
                    old_selector=failed_selector,
                    new_selector=result["recommended_selector"]
                )
                result["applied"] = True
                result["message"] = "Fix successfully applied to flow"
            except Exception as e:
                logger.error(f"Failed to auto-apply healing fix: {e}")
                result["applied"] = False
                result["apply_error"] = str(e)
                
        return result
