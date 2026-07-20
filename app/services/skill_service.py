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
    """Parses JSON safely by extracting from markdown blocks and stripping trailing commas."""
    text = text.strip()
    
    # Try to extract from markdown JSON block
    match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if match:
        text = match.group(1).strip()
    else:
        # Fallback: try to find the outermost array or object
        start_idx = text.find('[')
        start_obj = text.find('{')
        if start_idx != -1 and (start_obj == -1 or start_idx < start_obj):
            end_idx = text.rfind(']')
            if end_idx != -1 and end_idx >= start_idx:
                text = text[start_idx:end_idx+1]
        elif start_obj != -1:
            end_idx = text.rfind('}')
            if end_idx != -1 and end_idx >= start_obj:
                text = text[start_obj:end_idx+1]
                
    text = re.sub(r',\s*([\]}])', r'\1', text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"JSON Parse Error: {e}\nRaw Text:\n{text}")
        raise

class SkillService:
    SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")

    @staticmethod
    def get_skill_definition(skill_id: str) -> Dict[str, Any]:
        """Loads a skill definition from its JSON file."""
        path = os.path.join(SkillService.SKILLS_DIR, f"{skill_id}.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"Skill '{skill_id}' not found.")
        
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

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
        
        # Simple template filling
        prompt = prompt_template
        for key, value in context.items():
            placeholder = f"{{{{{key}}}}}"
            if placeholder in prompt:
                # Convert dict/list to JSON string for the prompt
                val_str = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
                prompt = prompt.replace(placeholder, val_str)

        logger.info(f"🚀 Executing Skill: {skill_id} for User: {user_id}")
        
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
    def generate_alternatives_workflow(db: Session, user_id: int, flow_id: int, company_id: int = 1) -> List[Dict[str, Any]]:
        """A specialist workflow that chains mapping and generation skills."""
        
        # 1. Load Original Flow Data
        flow = db.query(FlowDB).filter(FlowDB.id == flow_id).first()
        if not flow:
            raise ValueError("Original flow not found.")

        # Load full structure (nodes, edges, cardData)
        flow_data = FlowService.load(db, flow.project_id, company_id, flow.id, flow.flow_type)
        
        # 2. Map the Flow (Phase 1)
        mapping_context = {
            "nodes": flow_data.get("nodes", []),
            "edges": flow_data.get("edges", []),
            "cardData": flow_data.get("cardData", {}),
            "flow_type": flow.flow_type
        }
        
        mapping_result_raw = SkillService.execute_skill(db, user_id, "mapping_flow_skill", mapping_context)
        
        try:
            blueprint = _robust_json_parse(mapping_result_raw)
        except Exception as e:
            logger.error(f"Failed to parse blueprint from mapping skill: {e}")
            blueprint = {"logic_summary": mapping_result_raw} # Fallback to raw text

        # 3. Generate Alternatives (Phase 2)
        # Load feedback memory
        feedback_context = "No previous feedback."
        memory_dir = os.path.join(SkillService.SKILLS_DIR, "memory")
        feedback_file = os.path.join(memory_dir, f"feedback_flow_{flow_id}.json")
        if os.path.exists(feedback_file):
            try:
                import json
                with open(feedback_file, "r", encoding="utf-8") as fb:
                    feedbacks = json.load(fb)
                    if feedbacks:
                        feedback_lines = []
                        for idx, fb_item in enumerate(feedbacks):
                            feedback_lines.append(f"{idx+1}. REJECTED SUGGESTION: '{fb_item.get('suggestion_name')}' -> REASON: {fb_item.get('reason')}")
                        feedback_context = "\n".join(feedback_lines)
            except Exception as e:
                logger.error(f"Failed to load feedback memory: {e}")

        generation_context = {
            "blueprint": blueprint,
            "nodes": flow_data.get("nodes", []),
            "cardData": flow_data.get("cardData", {}),
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
