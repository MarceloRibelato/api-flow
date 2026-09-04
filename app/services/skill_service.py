import os
import json
import logging
import re
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from app.services.analysis_service import AnalysisService
from app.models.flow_models import FlowDB
from app.models.agent_models import AgentSettingsDB
from app.services.flow_service import FlowService

logger = logging.getLogger(__name__)

def _robust_json_parse(text: str) -> Any:
    """Parses JSON safely by extracting from markdown blocks, stripping trailing commas, handling Extra Data, and auto-healing truncated JSON."""
    if not text:
        return None
    text = text.strip()
    
    # 1. Try to extract from markdown JSON blocks if present
    blocks = re.findall(r'```(?:json)?\s*([\s\S]*?)\s*```', text, re.IGNORECASE)
    candidates = []
    if blocks:
        candidates.extend([b.strip() for b in blocks if b.strip()])
    
    # Also add the raw text / sliced text as candidate
    start_idx = text.find('[')
    start_obj = text.find('{')
    first_idx = -1
    if start_idx != -1 and (start_obj == -1 or start_idx < start_obj):
        first_idx = start_idx
    elif start_obj != -1:
        first_idx = start_obj
        
    if first_idx != -1:
        candidates.append(text[first_idx:])
    else:
        candidates.append(text)

    def _clean_and_parse_candidate(cand: str) -> Any:
        # Strip trailing markdown if left over
        cand = re.sub(r'\s*```[a-zA-Z]*\s*$', '', cand).strip()
        # Remove single-line and end-of-line comments safely (preserving http:// and https://)
        cand = re.sub(r'(?<!:)\/\/.*$', '', cand, flags=re.MULTILINE)
        # Normalize smart quotes
        cand = cand.replace('“', '"').replace('”', '"').replace('‘', "'").replace('’', "'")
        # Remove trailing commas before closing brackets
        cand = re.sub(r',\s*([\]}])', r'\1', cand)
        # Fix common pythonisms
        cleaned = cand.replace(': True', ': true').replace(': False', ': false').replace(': None', ': null')
        cleaned = cleaned.replace(':True', ': true').replace(':False', ': false').replace(':None', ': null')
        cleaned = cleaned.replace('True', 'true').replace('False', 'false').replace('None', 'null')

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            if "Extra data" in str(e) and getattr(e, 'pos', 0) > 0:
                cleaned = cleaned[:e.pos].strip()
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    pass

        def heal_json(t: str) -> Any:
            t = re.sub(r',\s*$', '', t)
            t = re.sub(r',\s*([\]}])', r'\1', t)
            try:
                return json.loads(t)
            except json.JSONDecodeError as e:
                if "Extra data" in str(e) and getattr(e, 'pos', 0) > 0:
                    t = t[:e.pos].strip()
                    t = re.sub(r',\s*$', '', t)
                    t = re.sub(r',\s*([\]}])', r'\1', t)
                    try:
                        return json.loads(t)
                    except Exception:
                        pass

            stack = []
            in_string = False
            escape = False
            
            for char in t:
                if escape:
                    escape = False
                    continue
                if char == '\\':
                    escape = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                    
                if not in_string:
                    if char == '{': stack.append('}')
                    elif char == '[': stack.append(']')
                    elif char in ('}', ']'):
                        if stack and stack[-1] == char:
                            stack.pop()
                            
            if in_string:
                t += '"'
                
            while stack:
                t += stack.pop()
                
            t = re.sub(r',\s*([\]}])', r'\1', t)
            try:
                return json.loads(t)
            except json.JSONDecodeError as e:
                if "Extra data" in str(e) and getattr(e, 'pos', 0) > 0:
                    return json.loads(t[:e.pos].strip())
                raise

        # 1. Direct heal attempt
        try:
            return heal_json(cleaned)
        except Exception:
            pass

        # 2. Cutoff fallback: find last comma, }, ], {, or [ and attempt healing
        for cutoff_char in [',', '}', ']', '{', '[']:
            positions = [i for i, ltr in enumerate(cleaned) if ltr == cutoff_char]
            for last_pos in reversed(positions):
                candidate_str = cleaned[:last_pos] if cutoff_char in (',', '}', ']') else cleaned[:last_pos + 1]
                try:
                    return heal_json(candidate_str)
                except Exception:
                    continue

        raise ValueError("Candidate parsing failed")

    for candidate in candidates:
        try:
            return _clean_and_parse_candidate(candidate)
        except Exception:
            continue

    logger.error(f"JSON Parse Error. Raw Text:\n{text}")
    raise ValueError("AI returned invalid JSON for alternatives flow generation.")

class SkillService:
    SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")

    @staticmethod
    def get_skill_definition(skill_id: str) -> Dict[str, Any]:
        """Loads a skill definition from its JSON file."""
        path = os.path.join(SkillService.SKILLS_DIR, f"{skill_id}.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"Skill '{skill_id}' not found.")
        
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            try:
                return json.loads(content)
            except Exception:
                return _robust_json_parse(content)

    @staticmethod
    def execute_skill(db: Session, user_id: int, skill_id: str, context: Dict[str, Any], flow_id: int = None, chat_history: list = None) -> str:
        # 1. Check AI Configuration
        settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == user_id).first()
        if not settings or not settings.ai_enabled:
            raise ValueError("O assistente de IA está desabilitado nas configurações.")
        
        if not settings.ai_api_key and settings.ai_provider != "ollama":
            raise ValueError(f"IA habilitada ({settings.ai_provider}), mas nenhuma Chave de API foi configurada.")

        skill_def = SkillService.get_skill_definition(skill_id)
        prompt_template = skill_def.get("prompt", "")
        
        # Populate alias context variables to support all skills
        if "nodes" in context and "cardData" in context and "mapping_context" not in context:
            context["mapping_context"] = json.dumps({"nodes": context["nodes"], "cardData": context["cardData"]})
        if "mapping_context" in context and isinstance(context["mapping_context"], str):
            try:
                parsed_mc = json.loads(context["mapping_context"])
                if isinstance(parsed_mc, dict):
                    if "nodes" not in context and "nodes" in parsed_mc:
                        context["nodes"] = parsed_mc["nodes"]
                    if "cardData" not in context and "cardData" in parsed_mc:
                        context["cardData"] = parsed_mc["cardData"]
                    if "flow_type" not in context and "flow_type" in parsed_mc:
                        context["flow_type"] = parsed_mc["flow_type"]
            except Exception:
                pass

        # Simple template filling
        prompt = prompt_template
        for key, value in context.items():
            placeholder = f"{{{{{key}}}}}"
            if placeholder in prompt:
                # Convert dict/list to JSON string for the prompt
                val_str = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
                prompt = prompt.replace(placeholder, val_str)

        # Clean remaining unreplaced mustache placeholders so LLM doesn't see raw template tags
        prompt = re.sub(r'\{\{\s*\w+\s*\}\}', 'Nenhum insumo adicional fornecido', prompt)

        logger.info(f"🚀 Executing Skill: {skill_id} for User: {user_id}")
        
        try:
            from app.models.user_models import UserDB
            from app.services.audit_service import AuditService
            user = db.query(UserDB).filter(UserDB.id == user_id).first()
            if user:
                AuditService.log_action(
                    db=db,
                    company_id=user.company_id or 1,
                    user=user,
                    action="EXECUTE_AI_SKILL",
                    resource_type="ai_skill",
                    resource_id=skill_id,
                    resource_name=skill_def.get("name") or skill_id,
                    details={"skill_id": skill_id, "flow_id": flow_id}
                )
        except Exception as audit_err:
            logger.warning(f"Erro ao registrar auditoria de skill: {audit_err}")
        
        max_retries = 2
        current_history = chat_history.copy() if chat_history else []
        current_prompt = prompt
        
        for attempt in range(max_retries + 1):
            result = AnalysisService._call_llm(db, user_id, current_prompt, temperature=0.1, flow_id=flow_id, chat_history=current_history)
            
            if not result:
                raise ValueError(f"LLM failed to return a result for skill '{skill_id}'.")
                
            result = result.strip()
            
            # Auto-healing loop: if it looks like a JSON block, validate it
            if "```json" in result.lower() or "```" in result:
                try:
                    # _robust_json_parse will raise an error if syntax is fundamentally broken (e.g., missing bracket)
                    _robust_json_parse(result)
                    return result
                except Exception as e:
                    logger.warning(f"JSON validation failed on attempt {attempt+1}: {e}")
                    if attempt < max_retries:
                        current_history.append({"role": "user", "content": current_prompt})
                        current_history.append({"role": "assistant", "content": result})
                        current_prompt = f"O JSON que você gerou possui um erro de sintaxe estrutural: {str(e)}. Por favor, corrija o JSON, garantindo que todas as chaves e colchetes estejam fechados corretamente, e sem comentários (//). Retorne APENAS o bloco de código JSON válido."
                    else:
                        return result # Return the broken one if out of retries
            else:
                # Not a JSON block response, just return it
                return result
            
        return result

    @staticmethod
    def _prune_flow_for_llm(flow_data: Dict[str, Any]) -> Dict[str, Any]:
        """Prunes unnecessary binary assets, styling, and heavy logs from flow_data before sending to LLM."""
        pruned_nodes = []
        for node in flow_data.get("nodes", []):
            if not isinstance(node, dict):
                continue
            data = node.get("data") or {}
            p_node = {
                "id": node.get("id"),
                "type": node.get("type"),
                "data": {
                    "name": data.get("name") or data.get("label") or data.get("title") or node.get("id"),
                    "isMainFlow": data.get("isMainFlow", False)
                }
            }
            pruned_nodes.append(p_node)

        pruned_card_data = {}
        for card_id, card in flow_data.get("cardData", {}).items():
            if not isinstance(card, dict):
                continue
            p_card = {
                "name": card.get("name"),
                "description": card.get("description")
            }
            
            # API Calls
            if "apiCalls" in card and isinstance(card["apiCalls"], list):
                pruned_apis = []
                for api in card["apiCalls"]:
                    if isinstance(api, dict):
                        body = api.get("body")
                        if isinstance(body, str) and len(body) > 1000:
                            body = body[:1000] + "...[truncated]"
                        pruned_apis.append({
                            "id": api.get("id"),
                            "name": api.get("name"),
                            "method": api.get("method"),
                            "url": api.get("url"),
                            "body": body,
                            "headers": api.get("headers"),
                            "params": api.get("params"),
                            "assertions": api.get("assertions"),
                            "extracts": api.get("extracts")
                        })
                p_card["apiCalls"] = pruned_apis

            # E2E Steps (Mobile / Web)
            e2e_steps = card.get("e2eSteps") or card.get("steps") or card.get("e2e_steps") or []
            if isinstance(e2e_steps, list) and len(e2e_steps) > 0:
                pruned_steps = []
                for step in e2e_steps:
                    if isinstance(step, dict):
                        props = dict(step.get("properties") or {})
                        props.pop("screenshot", None)
                        props.pop("image", None)
                        props.pop("html_snippet", None)
                        if isinstance(props.get("value"), str) and len(props["value"]) > 500:
                            props["value"] = props["value"][:500] + "...[truncated]"
                        pruned_steps.append({
                            "id": step.get("id"),
                            "name": step.get("name"),
                            "type": step.get("type"),
                            "action": step.get("action"),
                            "properties": props
                        })
                p_card["e2eSteps"] = pruned_steps

            if "dbQueries" in card:
                p_card["dbQueries"] = card["dbQueries"]

            pruned_card_data[card_id] = p_card

        return {
            "nodes": pruned_nodes,
            "edges": flow_data.get("edges", []),
            "cardData": pruned_card_data
        }

    @staticmethod
    def generate_alternatives_workflow(db: Session, user_id: int, flow_id: int, company_id: int = 1) -> List[Dict[str, Any]]:
        """A specialist workflow that generates technical variations of a test flow in a single LLM call."""
        
        # 1. Load Original Flow Data
        flow = db.query(FlowDB).filter(FlowDB.id == flow_id).first()
        if not flow:
            raise ValueError("Original flow not found.")

        # Load full structure (nodes, edges, cardData)
        flow_data = FlowService.load(db, flow.project_id, company_id, flow.id, flow.flow_type)
        pruned_data = SkillService._prune_flow_for_llm(flow_data)
        
        # 2. Build Python Blueprint (Fast & lightweight, avoiding redundant 1st LLM call)
        node_names = [n.get("data", {}).get("name", n.get("id")) for n in pruned_data.get("nodes", []) if n.get("data", {}).get("name")]
        blueprint = {
            "goal": f"Test automation scenario for flow '{flow.name}' ({flow.flow_type})",
            "platform": flow.flow_type,
            "logic_summary": f"Flow '{flow.name}' containing steps: {', '.join(node_names) if node_names else 'Standard flow nodes'}"
        }

        # 3. Load feedback memory
        feedback_context = "No previous feedback."
        memory_dir = os.path.join(SkillService.SKILLS_DIR, "memory")
        feedback_file = os.path.join(memory_dir, f"feedback_flow_{flow_id}.json")
        if os.path.exists(feedback_file):
            try:
                with open(feedback_file, "r", encoding="utf-8") as fb:
                    feedbacks = json.load(fb)
                    if feedbacks:
                        feedback_lines = []
                        for idx, fb_item in enumerate(feedbacks):
                            feedback_lines.append(f"{idx+1}. REJECTED SUGGESTION: '{fb_item.get('suggestion_name')}' -> REASON: {fb_item.get('reason')}")
                        feedback_context = "\n".join(feedback_lines)
            except Exception as e:
                logger.error(f"Failed to load feedback memory: {e}")

        # 4. Generate Alternatives (Single Fast LLM Call)
        generation_context = {
            "blueprint": json.dumps(blueprint, ensure_ascii=False),
            "nodes": pruned_data.get("nodes", []),
            "cardData": pruned_data.get("cardData", {}),
            "project_id": flow.project_id,
            "flow_type": flow.flow_type,
            "feedback_context": feedback_context
        }
        
        alternatives_raw = SkillService.execute_skill(db, user_id, "generate_alternatives_skill", generation_context)
        
        try:
            alternatives = _robust_json_parse(alternatives_raw)
            # Ensure it's a list
            if isinstance(alternatives, dict) and "alternatives" in alternatives:
                alternatives = alternatives["alternatives"]
            elif isinstance(alternatives, dict):
                alternatives = [alternatives]
                
            return alternatives
        except Exception as e:
            logger.error(f"Failed to parse alternatives from generation skill: {e}\nContent: {alternatives_raw}")
            raise ValueError("AI returned invalid JSON for alternatives flow generation.")
