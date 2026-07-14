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
    """Parses JSON safely by stripping trailing commas that LLMs often hallucinate."""
    text = text.strip()
    text = re.sub(r',\s*([\]}])', r'\1', text)
    return json.loads(text)

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
    def execute_skill(db: Session, user_id: int, skill_id: str, context: Dict[str, Any]) -> str:
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
        result = AnalysisService._call_llm(db, user_id, prompt, temperature=0.1)
        
        if not result:
            raise ValueError(f"LLM failed to return a result for skill '{skill_id}'.")
            
        return result.replace("```json", "").replace("```", "").strip()

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
        generation_context = {
            "blueprint": blueprint,
            "nodes": flow_data.get("nodes", []),
            "cardData": flow_data.get("cardData", {}),
            "project_id": flow.project_id,
            "flow_type": flow.flow_type
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
