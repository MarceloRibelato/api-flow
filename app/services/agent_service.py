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

def _prune_mcp_snapshot(snapshot: str, max_chars: int = 30000) -> str:
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
        "search", "combobox", "option", "alert", "data-testid", "data-qa",
        "role=", "aria-label", "placeholder", "submit", "btn", "table", "item"
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

def _extract_snapshot_elements(snapshot_text: str) -> Dict[str, List[str]]:
    """Extracts candidate buttons, textboxes, and links from the MCP accessibility tree or text snapshot."""
    buttons = []
    textboxes = []
    links = []
    if not snapshot_text:
        return {"buttons": [], "textboxes": [], "links": []}
    for line in snapshot_text.splitlines():
        line = line.strip()
        b_match = re.search(r"button\s+['\"]([^'\"]+)['\"]", line, re.IGNORECASE)
        if b_match and b_match.group(1) not in buttons:
            buttons.append(b_match.group(1))
        t_match = re.search(r"textbox\s+['\"]([^'\"]+)['\"]", line, re.IGNORECASE)
        if t_match and t_match.group(1) not in textboxes:
            textboxes.append(t_match.group(1))
        l_match = re.search(r"link\s+['\"]([^'\"]+)['\"]", line, re.IGNORECASE)
        if l_match and l_match.group(1) not in links:
            links.append(l_match.group(1))
    return {"buttons": buttons, "textboxes": textboxes, "links": links}

def _expand_single_node_into_modular_journey(
    single_node: Dict[str, Any],
    raw_card_data: Dict[str, Any],
    snapshot_text: str,
    target_url: str,
    start_mode: str
) -> List[Dict[str, Any]]:
    """Expands or clusters a single monolithic node into 3 to 4 modular test cards."""
    ndata = single_node.get("data", {})
    if not isinstance(ndata, dict):
        ndata = {}
    c_id = str(single_node.get("id") or "node_1")
    c_data = raw_card_data.get(c_id) or raw_card_data.get("1") or raw_card_data.get("node_1") or {}
    if not isinstance(c_data, dict):
        c_data = {}

    steps = (
        ndata.get("e2eSteps")
        or c_data.get("e2eSteps")
        or ndata.get("steps")
        or c_data.get("steps")
        or single_node.get("e2eSteps")
        or single_node.get("steps")
        or []
    )
    if not isinstance(steps, list):
        steps = []

    # Case A: The single node has 3 or more steps. Split into 2 or 3 modular cards!
    if len(steps) >= 3:
        chunk_size = max(1, (len(steps) + 1) // 3)
        chunks = [steps[i:i + chunk_size] for i in range(0, len(steps), chunk_size)]
        
        stage_names = [
            "1. Navegação e Seleção",
            "2. Preenchimento de Dados",
            "3. Ação Principal e Submissão",
            "4. Validação e Asserções"
        ]
        
        modular_nodes = []
        for c_idx, chunk in enumerate(chunks):
            n_id = f"node_{c_idx + 1}"
            orig_name = ndata.get("name") if c_idx == 0 else None
            n_name = orig_name or (stage_names[c_idx] if c_idx < len(stage_names) else f"{c_idx + 1}. Continuação da Jornada")
            modular_nodes.append({
                "id": n_id,
                "type": "custom",
                "position": {"x": 100 + c_idx * 300, "y": 180},
                "data": {
                    "id": n_id,
                    "name": n_name,
                    "nodeType": "web",
                    "description": f"Etapa {c_idx + 1} da jornada de automação",
                    "e2eSteps": chunk
                }
            })
        
        has_assert = any(
            any(str(s.get("type") or s.get("action")).lower() in ["assert", "check", "verify"] for s in n["data"]["e2eSteps"] if isinstance(s, dict))
            for n in modular_nodes
        )
        if not has_assert and modular_nodes:
            modular_nodes[-1]["data"]["e2eSteps"].append({
                "id": f"{modular_nodes[-1]['id']}_step_assert",
                "name": "Validar conclusão do fluxo",
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
        return modular_nodes

    # Case B: The single node has 1 or 2 steps. Synthesize complementary cards to build a 3-card journey.
    elements = _extract_snapshot_elements(snapshot_text)
    node_1_steps = steps if steps else [{
        "id": "node_1_step_1",
        "name": ndata.get("name") or "Interagir com a página",
        "type": "click",
        "action": "click",
        "selector": f"button:has-text('{elements['buttons'][0]}')" if elements["buttons"] else "button",
        "value": "",
        "properties": {
            "action": "click",
            "selector": f"button:has-text('{elements['buttons'][0]}')" if elements["buttons"] else "button",
            "timeout": 5000
        }
    }]
    
    orig_title = ndata.get("name") or "Navegação e Seleção"
    if not orig_title.startswith("1."):
        node_1_name = f"1. {orig_title}"
    else:
        node_1_name = orig_title

    node_1 = {
        "id": "node_1",
        "type": "custom",
        "position": {"x": 100, "y": 180},
        "data": {
            "id": "node_1",
            "name": node_1_name,
            "nodeType": "web",
            "description": ndata.get("description") or "Navegação inicial e seleção do elemento de teste",
            "e2eSteps": node_1_steps
        }
    }
    
    # Node 2: Form input / Configuration / Interaction
    sample_textbox = elements["textboxes"][0] if elements["textboxes"] else None
    if sample_textbox:
        step_2_selector = f"input[placeholder*='{sample_textbox}' i], input[name*='{sample_textbox}' i], [data-testid*='{sample_textbox.lower()}']"
        step_2_name = f"Preencher campo '{sample_textbox}'"
        step_2_val = "Produto Teste" if any(w in sample_textbox.lower() for w in ["search", "buscar", "busca"]) else "teste"
        step_2_type = "type"
    else:
        sample_link = elements["links"][0] if elements["links"] else None
        if sample_link:
            step_2_selector = f"a:has-text('{sample_link}')"
            step_2_name = f"Acessar link '{sample_link}'"
            step_2_val = ""
            step_2_type = "click"
        else:
            step_2_selector = "input:not([type='hidden']), textarea"
            step_2_name = "Preencher dados da etapa"
            step_2_val = "teste"
            step_2_type = "type"

    node_2 = {
        "id": "node_2",
        "type": "custom",
        "position": {"x": 400, "y": 180},
        "data": {
            "id": "node_2",
            "name": "2. Preenchimento de Dados e Opções",
            "nodeType": "web",
            "description": "Inserção de dados operacionais e configuração da ação",
            "e2eSteps": [
                {
                    "id": "node_2_step_1",
                    "name": step_2_name,
                    "type": step_2_type,
                    "action": step_2_type,
                    "selector": step_2_selector,
                    "value": step_2_val,
                    "properties": {
                        "action": step_2_type,
                        "selector": step_2_selector,
                        "value": step_2_val,
                        "timeout": 5000,
                        "locator_strategy": "role" if "has-text" in step_2_selector else "css"
                    }
                }
            ]
        }
    }

    # Node 3: Submit action and assertion of success
    sample_button = elements["buttons"][1] if len(elements["buttons"]) > 1 else (elements["buttons"][0] if elements["buttons"] else None)
    btn_selector = f"button:has-text('{sample_button}')" if sample_button else "button[type='submit'], [role='button']:has-text('Salvar'), button"
    btn_name = f"Clicar em '{sample_button}'" if sample_button else "Submeter ação principal"
    
    node_3 = {
        "id": "node_3",
        "type": "custom",
        "position": {"x": 700, "y": 180},
        "data": {
            "id": "node_3",
            "name": "3. Submissão e Validação de Sucesso",
            "nodeType": "web",
            "description": "Conclusão da ação crítica e asserções de validação E2E",
            "e2eSteps": [
                {
                    "id": "node_3_step_1",
                    "name": btn_name,
                    "type": "click",
                    "action": "click",
                    "selector": btn_selector,
                    "value": "",
                    "properties": {
                        "action": "click",
                        "selector": btn_selector,
                        "value": "",
                        "timeout": 5000,
                        "locator_strategy": "role" if "has-text" in btn_selector else "css"
                    }
                },
                {
                    "id": "node_3_step_2",
                    "name": "Verificar status de sucesso da operação",
                    "type": "assert",
                    "action": "assert",
                    "selector": "[role='status'], .toast, [data-testid*='success'], body",
                    "value": "success",
                    "properties": {
                        "action": "assert",
                        "selector": "[role='status'], .toast, [data-testid*='success'], body",
                        "value": "success",
                        "timeout": 5000,
                        "locator_strategy": "css"
                    }
                }
            ]
        }
    }

    return [node_1, node_2, node_3]

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
        start_node_id: Optional[str] = None,
        storage_state: Optional[Dict[str, Any]] = None,
        cookies: Optional[List[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, str]] = None
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
            snapshot_info = {"status": "fallback", "snapshot": "", "screenshot": None}
            mcp_snapshot = ""
            node_enhancements = []

            existing_nodes = existing_flow_context.get("nodes") if (existing_flow_context and isinstance(existing_flow_context, dict)) else []

            if start_mode == "from_existing" and existing_nodes:
                logger.info(f"Replaying full flow ({len(existing_nodes)} nodes) and auditing each node at {target_url}")
                snapshot_info = await MCPPlaywrightService.replay_flow_and_audit_nodes(
                    target_url,
                    existing_nodes,
                    storage_state=storage_state,
                    cookies=cookies,
                    headers=headers
                )
                mcp_snapshot = snapshot_info.get("snapshot", "")
                node_enhancements = snapshot_info.get("node_enhancements", [])

            elif start_mode == "from_existing" and existing_steps:
                logger.info(f"Replaying {len(existing_steps)} existing automated steps to reach pre-condition state at {target_url}")
                try:
                    async with MCPPlaywrightService.browser_session(timeout=35.0, connect_timeout=3.0) as browser:
                        # Navigate to base URL first
                        await browser.call_tool("navigate", {"url": target_url}, timeout=8.0)
                        
                        # Replay non-navigation interactive steps up to the starting node in the SAME session
                        for step in existing_steps:
                            stype = step.get("type") or step.get("action")
                            props = step.get("properties") or {}
                            selector = props.get("selector") or step.get("selector") or ""
                            value = props.get("value") or step.get("value") or ""

                            if stype in ["type", "fill"] and selector and value:
                                try:
                                    await browser.call_tool("type", {"selector": selector, "text": str(value)}, timeout=4.0)
                                except Exception as te:
                                    logger.debug(f"Step replay type error on selector {selector}: {te}")
                            elif stype in ["click", "tap"] and selector:
                                try:
                                    await browser.call_tool("click", {"selector": selector}, timeout=4.0)
                                except Exception as ce:
                                    logger.debug(f"Step replay click error on selector {selector}: {ce}")
                        
                        # Take snapshot of the resulting advanced page state
                        snap_res = await browser.call_tool("snapshot", {}, timeout=4.0)
                        if snap_res:
                            mcp_snapshot = "\n".join(snap_res) if isinstance(snap_res, list) else str(snap_res)
                            snapshot_info = {"status": "connected", "snapshot": mcp_snapshot, "screenshot": None}
                except Exception as replay_err:
                    logger.warning(f"MCP step replay failed: {replay_err}. Falling back to direct Playwright explorer with step replay.")
                    snapshot_info = await MCPPlaywrightService.direct_playwright_snapshot(
                        target_url, replay_steps=existing_steps, storage_state=storage_state, cookies=cookies, headers=headers
                    )
                    mcp_snapshot = snapshot_info.get("snapshot", "")

                if not mcp_snapshot or "### Error" in mcp_snapshot or "not installed" in mcp_snapshot or "Browser" in mcp_snapshot:
                    snapshot_info = await MCPPlaywrightService.direct_playwright_snapshot(
                        target_url, replay_steps=existing_steps, storage_state=storage_state, cookies=cookies, headers=headers
                    )
                    mcp_snapshot = snapshot_info.get("snapshot", "")
            else:
                # Direct MCP exploration from initial URL
                snapshot_info = await MCPPlaywrightService.navigate_and_snapshot(
                    target_url, storage_state=storage_state, cookies=cookies, headers=headers
                )
                mcp_snapshot = snapshot_info.get("snapshot", "")
                if not mcp_snapshot or "### Error" in mcp_snapshot or "not installed" in mcp_snapshot or "Browser" in mcp_snapshot:
                    logger.info("MCP snapshot had error or was empty, falling back to direct Playwright exploration.")
                    snapshot_info = await MCPPlaywrightService.direct_playwright_snapshot(
                        target_url, storage_state=storage_state, cookies=cookies, headers=headers
                    )
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
            if start_mode == "from_existing":
                context_instruction = (
                    "A partir do estado atual da página após os passos precursores já automatizados, gere OBRIGATORIAMENTE os próximos 3 a 5 cards lógicos complementares e sequenciais (ex: 1. Interação/Seleção, 2. Preenchimento de dados, 3. Submissão, 4. Asserções de sucesso) para avançar e concluir esta jornada de teste E2E. "
                    + (f"Objetivo informado: {instruction}" if instruction else "")
                )
            else:
                context_instruction = (
                    "Gere OBRIGATORIAMENTE uma jornada de teste E2E completa e de alto valor de QA composta por 3 a 5 cards modulares conectados sequencialmente por edges (ex: 1. Acesso e Seleção Inicial, 2. Preenchimento de Dados/Opções, 3. Ação Principal/Submissão, 4. Validação e Asserções de Sucesso), explorando os elementos interativos reais identificados no snapshot da página. "
                    + (f"Objetivo informado: {instruction}" if instruction else "")
                )

            context = {
                "target_url": target_url,
                "mcp_snapshot": pruned_mcp_snapshot,
                "existing_steps_summary": existing_steps_summary,
                "instruction": context_instruction,
                "flow_type": "web"
            }
            
            planner_result_raw = await asyncio.to_thread(SkillService.execute_skill, db, user_id, "qa_agent_planner", context)
            
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
            screenshot_b64 = snapshot_info.get("screenshot")
            
            for idx, n in enumerate(raw_nodes):
                node_id = str(n.get("id") or f"node_{idx + 1}")
                ndata = n.get("data", {})
                if not isinstance(ndata, dict):
                    ndata = {}
                
                if screenshot_b64:
                    ndata["screenshot"] = screenshot_b64
                
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
                                selector = f"button:has-text('{kw}'), [role='button']:has-text('{kw}'), a:has-text('{kw}')"
                            else:
                                selector = "button[type='submit'], [role='button'], button"
                        elif norm_type == "type":
                            if not selector:
                                s_lower = step_name.lower()
                                if "email" in s_lower:
                                    selector = "input[type='email'], input[name*='email'], [data-testid*='email']"
                                elif "senh" in s_lower or "pass" in s_lower:
                                    selector = "input[type='password'], input[name*='password']"
                                elif "busc" in s_lower or "search" in s_lower:
                                    selector = "input[type='search'], input[name*='search'], input[placeholder*='Buscar' i]"
                                else:
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

                        locator_strategy = "testId" if "data-testid" in selector else ("role" if ("role=" in selector or ":has-text" in selector or "[role=" in selector) else "css")

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
                                "locator_strategy": locator_strategy,
                                **{k: v for k, v in props.items() if k not in ["selector", "value", "action", "timeout", "locator_strategy"]}
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
                            fallback_sel = f"[role='button']:has-text('{kw}'), button:has-text('{kw}'), a:has-text('{kw}')"
                        else:
                            fallback_sel = "button[type='submit'], [role='button'], button"
                        fallback_val = ""
                        step_desc = f"Clicar: {node_name}"

                    loc_strat = "testId" if "data-testid" in fallback_sel else ("role" if ("role" in fallback_sel or ":has-text" in fallback_sel) else "css")
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
                            "timeout": 5000,
                            "locator_strategy": loc_strat
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
                    "dbQueries": raw_card_entry.get("dbQueries", []),
                    "screenshot": screenshot_b64
                }
                
            # Normalize edges
            raw_edges = plan.get("edges", [])
            normalized_edges = []
            existing_pairs = set()
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
                        existing_pairs.add((source, target))

            # Ensure all sequential nodes are connected if not already linked
            for i in range(len(normalized_nodes) - 1):
                src = normalized_nodes[i]["id"]
                tgt = normalized_nodes[i + 1]["id"]
                if (src, tgt) not in existing_pairs:
                    normalized_edges.append({
                        "id": f"edge_{src}_{tgt}",
                        "source": src,
                        "target": tgt
                    })
                    existing_pairs.add((src, tgt))
                    
            return {
                "name": plan.get("name", f"Fluxo Playwright - {target_url}"),
                "flow_type": "web",
                "nodes": normalized_nodes,
                "edges": normalized_edges,
                "cardData": card_data,
                "start_mode": start_mode,
                "start_node_id": start_node_id,
                "mcp_status": snapshot_info.get("status", "unknown"),
                "screenshot": screenshot_b64,
                "node_enhancements": node_enhancements
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
        
        healer_result_raw = await asyncio.to_thread(SkillService.execute_skill, db, user_id, "qa_agent_healer", context)
        
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
