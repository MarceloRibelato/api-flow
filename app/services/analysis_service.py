from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from app.models.flow_models import FlowCardDataDB, FlowDB
from app.models.api_test_history_models import ApiExecutionHistory
from app.models.agent_models import AgentSettingsDB
from datetime import datetime, timedelta
from urllib.parse import urlparse
import json
import re
import time
import requests
import logging

logger = logging.getLogger(__name__)

# --- In-memory cache for auto-detected AI model names ---
# Key: base_url, Value: (model_name, timestamp)
_model_cache: dict = {}
_MODEL_CACHE_TTL = 300  # 5 minutes


def _get_custom_license_key(db: Session, user_id: int) -> str:
    """Build composite license key (email+cnpj). Uses joinedload on company_rel to fetch
    user + company in a single SQL JOIN instead of two separate queries."""
    from app.models.user_models import UserDB
    user = db.query(UserDB).filter(UserDB.id == user_id).options(
        joinedload(UserDB.company_rel)
    ).first()
    email = user.email if user and user.email else f"user{user_id}@qaflow.com"
    cnpj = "00000000000000"
    if user:
        # company_rel is the SQLAlchemy relationship to CompanyDB
        company = getattr(user, 'company_rel', None)
        if company and getattr(company, 'cnpj', None):
            cnpj = company.cnpj
        elif getattr(user, 'cnpj', None):
            # Fall back to legacy cnpj column directly on UserDB
            cnpj = user.cnpj
    cnpj_clean = re.sub(r'[^0-9]', '', cnpj)
    return f"{email}+{cnpj_clean}"


def _detect_model(base_url: str) -> str:
    """Auto-detect Ollama/Open WebUI model with in-memory TTL cache."""
    if not base_url:
        base_url = "http://host.docker.internal:11434/v1"

    cached = _model_cache.get(base_url)
    if cached:
        model_name, ts = cached
        if time.time() - ts < _MODEL_CACHE_TTL:
            return model_name

    urls_to_try = [base_url]
    # Only fallback to localhost if base_url is not host.docker.internal (e.g. running outside docker)
    if "host.docker.internal" not in base_url and "localhost" not in base_url:
        urls_to_try.append(base_url.replace("127.0.0.1", "localhost"))

    model_name = "qwen2.5:14b-instruct-q4_K_M"  # Default fallback if detection fails
    for u in urls_to_try:
        try:
            clean_base = u.rstrip('/')
            url_models = f"{clean_base}/models" if not clean_base.endswith("/models") else clean_base
            m_resp = requests.get(url_models, timeout=3)
            if m_resp.status_code == 200 and m_resp.json().get("data"):
                model_name = m_resp.json()["data"][0]["id"]
                break
            else:
                tags_url = clean_base.replace("/v1", "") + "/api/tags"
                o_resp = requests.get(tags_url, timeout=3)
                if o_resp.status_code == 200 and o_resp.json().get("models"):
                    model_name = o_resp.json()["models"][0]["name"]
                    break
        except Exception as e:
            logger.warning(f"Failed to auto-detect model from {u}: {e}")

    _model_cache[base_url] = (model_name, time.time())
    return model_name


def _build_flow_ia_request_params(db: Session, user_id: int, settings) -> tuple:
    """Build (url, headers, base_url) for flow_ia provider, routing through License Manager."""
    from app.config import settings as app_settings
    default_url = "http://host.docker.internal:11434/v1"
    base_url = settings.ai_base_url if settings.ai_base_url else default_url
    if "localhost" in base_url or "127.0.0.1" in base_url:
        base_url = base_url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
    if not base_url.endswith("/"):
        base_url += "/"
    parsed = urlparse(base_url)
    if parsed.path in ("", "/"):
        base_url += "v1/"

    custom_license = _get_custom_license_key(db, user_id)
    license_url = app_settings.LICENSE_MANAGER_URL
    if license_url:
        url = f"{license_url.rstrip('/')}/api/ai/chat"
        headers = {
            "Content-Type": "application/json",
            "X-License-Key": custom_license,
            "X-Target-Url": base_url,
            "X-User-Identifier": f"user_{user_id}",
        }
    else:
        url = f"{base_url}chat/completions" if "chat/completions" not in base_url else base_url
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer local-dummy-token",
        }
    return url, headers, base_url

class AnalysisService:

    @staticmethod
    def _call_llm(db: Session, user_id: int, prompt: str, temperature: float = 0.2, flow_id: int = None, chat_history: list = None, timeout: int = 30) -> str:
        """Helper to call the configured LLM for a user, with memory context."""
        from app.models.agent_models import AgentMemoryDB
        
        settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == user_id).first()
        if not settings:
            try:
                settings = AgentSettingsDB(
                    user_id=user_id,
                    ai_enabled=True,
                    ai_provider="flow_ia",
                    ai_model="qwen2.5:14b-instruct-q4_K_M"
                )
                db.add(settings)
                db.commit()
                db.refresh(settings)
            except Exception:
                db.rollback()
                settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == user_id).first()

        if not settings or not settings.ai_enabled or (not settings.ai_api_key and settings.ai_provider not in ("ollama", "flow_ia")):
            return None

        # Build messages array with history if flow_id is provided
        messages = []
        if chat_history is not None:
            messages.extend(chat_history)
        elif flow_id:
            # Fetch last 10 messages for this flow and user (filtering strictly valid LLM roles in chronological order)
            history = db.query(AgentMemoryDB).filter(
                AgentMemoryDB.user_id == user_id, 
                AgentMemoryDB.flow_id == flow_id,
                AgentMemoryDB.role.in_(["user", "assistant"])
            ).order_by(AgentMemoryDB.timestamp.desc()).limit(10).all()
            
            for msg in reversed(history):
                messages.append({"role": msg.role, "content": msg.content})
                
        # Append current prompt
        messages.append({"role": "user", "content": prompt})

        resp = None
        try:
            if settings.ai_provider == "openai":
                headers = {"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"}
                data = {"model": settings.ai_model, "messages": messages, "temperature": temperature, "max_tokens": 4096}
                base_url = settings.ai_base_url if settings.ai_base_url else "https://api.openai.com/v1"
                if not base_url.endswith("/"): base_url += "/"
                url = f"{base_url}chat/completions" if "chat/completions" not in base_url else base_url
                resp = requests.post(url, headers=headers, json=data, timeout=300)
                if resp.status_code == 200:
                    result_text = resp.json()["choices"][0]["message"]["content"]
                    if flow_id:
                        db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="user", content=prompt))
                        db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="assistant", content=result_text))
                        db.commit()
                    return result_text
                else:
                    raise Exception(f"OpenAI Error: {resp.status_code} - {resp.text}")

            elif settings.ai_provider == "anthropic":
                headers = {"x-api-key": settings.ai_api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
                data = {"model": settings.ai_model, "messages": messages, "max_tokens": 4096}
                resp = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=data, timeout=300)
                if resp.status_code == 200:
                    result_text = resp.json()["content"][0]["text"]
                    if flow_id:
                        db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="user", content=prompt))
                        db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="assistant", content=result_text))
                        db.commit()
                    return result_text
                else:
                    raise Exception(f"Anthropic Error: {resp.status_code} - {resp.text}")

            elif settings.ai_provider == "gemini":
                # Convert standard messages to Gemini format
                gemini_contents = []
                for m in messages:
                    gemini_contents.append({"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]})
                
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.ai_model}:generateContent?key={settings.ai_api_key}"
                data = {"contents": gemini_contents, "generationConfig": {"maxOutputTokens": 4096}}
                gem_err_msg = ""
                try:
                    resp = requests.post(url, json=data, timeout=300)
                    if resp.status_code == 200:
                        result_text = resp.json().get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                        if flow_id:
                            db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="user", content=prompt))
                            db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="assistant", content=result_text))
                            db.commit()
                        return result_text
                    else:
                        gem_err_msg = f"Gemini Error: {resp.status_code} - {resp.text}"
                        logger.warning(f"{gem_err_msg}. Tentando fallback com Ollama local.")
                except Exception as gem_exc:
                    gem_err_msg = f"Gemini exception: {gem_exc}"
                    logger.warning(f"{gem_err_msg}. Tentando fallback com Ollama local.")

                # Fallback resiliente para o Ollama local se Gemini falhar
                try:
                    ollama_url = "http://host.docker.internal:11434/v1/chat/completions"
                    ollama_data = {
                        "model": "qwen2.5:14b-instruct-q4_K_M",
                        "messages": messages,
                        "temperature": temperature,
                        "stream": False
                    }
                    fb_resp = requests.post(ollama_url, json=ollama_data, timeout=300)
                    if fb_resp.status_code == 200:
                        logger.info("Recuperado com sucesso usando Ollama local após falha do Gemini!")
                        result_text = fb_resp.json()["choices"][0]["message"]["content"]
                        if flow_id:
                            db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="user", content=prompt))
                            db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="assistant", content=result_text))
                            db.commit()
                        return result_text
                except Exception as fb_err:
                    logger.warning(f"Fallback no Ollama local também falhou: {fb_err}")

                raise Exception(gem_err_msg or "Gemini call failed and local Ollama unreachable")

            elif settings.ai_provider == "deepseek":
                headers = {"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"}
                data = {"model": settings.ai_model, "messages": messages, "temperature": temperature, "max_tokens": 4096}
                resp = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=data, timeout=300)
                if resp.status_code == 200:
                    result_text = resp.json()["choices"][0]["message"]["content"]
                    if flow_id:
                        db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="user", content=prompt))
                        db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="assistant", content=result_text))
                        db.commit()
                    return result_text

            elif settings.ai_provider == "flow_ia":
                url, headers, base_url = _build_flow_ia_request_params(db, user_id, settings)
                if settings.ai_model and settings.ai_model not in ("gpt-4o", "flow-ia", "llama3"):
                    model_name = settings.ai_model
                else:
                    model_name = _detect_model(base_url)
                data = {
                    "messages": messages, 
                    "stream": False, 
                    "model": model_name, 
                    "temperature": temperature,
                    "max_tokens": 8192, 
                    "options": {"num_predict": 8192, "temperature": temperature}
                }
                
                resp = None
                # If caller explicitly asked for a short timeout (e.g. <= 10s in fast assertion fallback), respect it.
                # Otherwise, grant a robust timeout (300s) for LLM inference (Ollama local model generation).
                if timeout and timeout <= 10:
                    lm_timeout = timeout
                    direct_timeout = timeout
                else:
                    lm_timeout = max(timeout or 30, 300)
                    direct_timeout = max(timeout or 30, 300)
                try:
                    # Attempt License Manager proxy first
                    resp = requests.post(url, headers=headers, json=data, timeout=lm_timeout)
                    if resp.status_code == 200:
                        result_text = resp.json()["choices"][0]["message"]["content"]
                        if flow_id:
                            db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="user", content=prompt))
                            db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="assistant", content=result_text))
                            db.commit()
                        return result_text
                    else:
                        logger.warning(f"Flow-IA License Manager returned HTTP {resp.status_code}: {resp.text}. Falling back to direct Ollama.")
                except Exception as lme:
                    logger.warning(f"Flow-IA License Manager request failed ({lme}). Falling back to direct Ollama.")

                # Resilient fallback: Direct host Ollama
                direct_url = f"{base_url}chat/completions" if "chat/completions" not in base_url else base_url
                direct_headers = {"Content-Type": "application/json"}
                direct_resp = requests.post(direct_url, headers=direct_headers, json=data, timeout=direct_timeout)
                if direct_resp.status_code == 200:
                    result_text = direct_resp.json()["choices"][0]["message"]["content"]
                    if flow_id:
                        db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="user", content=prompt))
                        db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="assistant", content=result_text))
                        db.commit()
                    return result_text
                else:
                    raise Exception(f"Direct Ollama Error: {direct_resp.status_code} - {direct_resp.text}")

            elif settings.ai_provider == "ollama":
                from app.config import settings as app_settings
                license_url = app_settings.LICENSE_MANAGER_URL
                license_key = _get_custom_license_key(db, user_id)
                user_ident = f"user_{user_id}"
                try:
                    if getattr(settings, 'user', None):
                        user_ident = settings.user.email or settings.user.username or user_ident
                except Exception as ue:
                    logger.warning(f"Could not retrieve user info: {ue}")

                default_url = "http://host.docker.internal:11434/v1"
                base_url = settings.ai_base_url if settings.ai_base_url else default_url
                if "localhost" in base_url or "127.0.0.1" in base_url:
                    base_url = base_url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
                if not base_url.endswith("/"):
                    base_url += "/"
                parsed = urlparse(base_url)
                if parsed.path in ("", "/"):
                    base_url += "v1/"

                if license_url and license_key:
                    url = f"{license_url.rstrip('/')}/api/ai/chat"
                    headers = {
                        "Content-Type": "application/json",
                        "X-License-Key": license_key,
                        "X-Target-Url": base_url,
                        "X-User-Identifier": user_ident,
                    }
                    data = {
                        "messages": messages, 
                        "stream": False, 
                        "temperature": temperature,
                        "max_tokens": 8192, 
                        "options": {"num_predict": 8192, "temperature": temperature}
                    }
                    if settings.ai_model and settings.ai_model not in ("gpt-4o", "flow-ia", "llama3"):
                        data["model"] = settings.ai_model
                    else:
                        data["model"] = _detect_model(base_url)
                    try:
                        resp = requests.post(url, headers=headers, json=data, timeout=600)
                        if resp.status_code == 200:
                            result_text = resp.json()["choices"][0]["message"]["content"]
                            if flow_id:
                                db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="user", content=prompt))
                                db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="assistant", content=result_text))
                                db.commit()
                            return result_text
                        else:
                            logger.warning(f"License Manager returned error: {resp.status_code} - {resp.text}. Falling back to direct Ollama.")
                    except Exception as lme:
                        logger.warning(f"License Manager request failed ({lme}). Falling back to direct Ollama.")
                
                # Direct Ollama host fallback
                default_url = "http://host.docker.internal:11434/v1"
                base_url = settings.ai_base_url if settings.ai_base_url else default_url
                if "localhost" in base_url or "127.0.0.1" in base_url:
                    base_url = base_url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
                if not base_url.endswith("/"): base_url += "/"
                url = f"{base_url}chat/completions" if "chat/completions" not in base_url else base_url
                headers = {"Content-Type": "application/json"}
                if settings.ai_api_key:
                    headers["Authorization"] = f"Bearer {settings.ai_api_key}"
                data = {"messages": messages, "stream": False, "max_tokens": 8192, "options": {"num_predict": 8192}}
                if settings.ai_model and settings.ai_model not in ("gpt-4o", "flow-ia", "llama3"):
                    data["model"] = settings.ai_model
                else:
                    data["model"] = _detect_model(base_url)
                resp = requests.post(url, headers=headers, json=data, timeout=600)
                if resp.status_code == 200:
                    result_text = resp.json()["choices"][0]["message"]["content"]
                    if flow_id:
                        db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="user", content=prompt))
                        db.add(AgentMemoryDB(user_id=user_id, flow_id=flow_id, role="assistant", content=result_text))
                        db.commit()
                    return result_text
                else:
                    raise Exception(f"Local Ollama Error: {resp.status_code} - {resp.text}")
                        
            raise Exception(f"Unsupported AI Provider: {settings.ai_provider}")

        except Exception as e:
            logger.error(f"Error calling LLM {settings.ai_provider}: {e}")
            if resp is not None:
                logger.error(f"LLM Response Error: {resp.status_code} - {resp.text}")
            raise Exception(f"LLM Call Failed: {str(e)}")

    @staticmethod
    def analyze_with_ai(db: Session, flow_id: int, user_id: int):
        flow = db.query(FlowDB).filter(FlowDB.id == flow_id).first()
        cards = db.query(FlowCardDataDB).filter(FlowCardDataDB.flow_id == flow_id).all()
        if not flow or not cards: return []

        nodes_desc = [f"- Node {c.name}: {c.api_calls if c.api_calls else 'No API'}" for c in cards]
        context_str = "\n".join(nodes_desc)

        prompt = f"""
        Analyze the following API Flow for optimizations, security risks, and logic errors.
        Flow Name: {flow.name}
        Nodes:
        {context_str}
        
        Important: When checking for redundant or duplicated API calls, ONLY flag them if they occur within the same logical path (sequentially). If they are in separate alternative paths (e.g., parallel branches or unhappy paths), they are NOT redundant.

        Return a JSON response with a list of suggestions. Format:
        [
          {{ "type": "optimization|security|logic", "severity": "low|medium|high", "message": "Short title", "details": "Detailed explanation" }}
        ]
        Do not include markdown formatting, just raw JSON.
        """
        
        content = AnalysisService._call_llm(db, user_id, prompt)
        if not content: return []
        
        from app.services.skill_service import _robust_json_parse
        try:
            parsed = _robust_json_parse(content)
            return parsed if isinstance(parsed, list) else [parsed]
        except:
            return [{ "type": "logic", "severity": "low", "message": "Raw AI Response", "details": content }]

    @staticmethod
    def suggest_test_scenarios(db: Session, flow_id: int, user_id: int):
        """Specifically suggests NEW test scenarios/flows derived from the current one."""
        flow = db.query(FlowDB).filter(FlowDB.id == flow_id).first()
        cards = [c for c in db.query(FlowCardDataDB).filter(FlowCardDataDB.flow_id == flow_id).all()]
        if not flow or not cards: return []

        # Simplify context for better prompt focus
        flow_summary = []
        for c in cards:
             apis = [f"{a.get('method')} {a.get('url')}" for a in (c.api_calls or [])]
             # Include E2E steps if available
             e2e = [f"{s.type}: {s.properties.get('url') or s.properties.get('selector') or ''}" for s in (c.e2e_steps_rel or [])]
             
             summary_parts = []
             if apis: summary_parts.append(f"APIs: {', '.join(apis)}")
             if e2e: summary_parts.append(f"E2E: {', '.join(e2e)}")
             
             flow_summary.append(f"Node: {c.name} ({' | '.join(summary_parts)})")
        
        context_str = "\n".join(flow_summary)

        prompt = f"""
        Analyze this Flow:
        {context_str}
        
        Suggest 3 new high-value test scenarios (unhappy paths, edge cases, or missing logic) based on this flow.
        For each, explain why it's important.
        
        Return a JSON array:
        [
          {{
            "title": "Scenario Name",
            "description": "Why this is important.",
            "concept": "Explanation of what to do (e.g. inject an invalid token in node X)"
          }}
        ]
        Respond ONLY with raw JSON.
        """
        
        content = AnalysisService._call_llm(db, user_id, prompt)
        if not content: return []
        
        from app.services.skill_service import _robust_json_parse
        try:
            parsed = _robust_json_parse(content)
            return parsed if isinstance(parsed, list) else [parsed]
        except:
             return []

    @staticmethod
    def suggest_test_scenarios_by_project(db: Session, project_id: int, user_id: int):
        """Finds the latest flow for a project and suggests tests for it."""
        flow = db.query(FlowDB).filter(FlowDB.project_id == project_id).order_by(FlowDB.updated_at.desc()).first()
        if not flow: return []
        return AnalysisService.suggest_test_scenarios(db, flow.id, user_id)

    @staticmethod
    def implement_test_scenario(db: Session, flow_id: int, user_id: int, scenario_title: str, company_id: int = 1):
        """Creates a NEW flow (and Feature) based on a scenario suggestion with fully populated nodes and edges."""
        import json
        flow = db.query(FlowDB).filter(FlowDB.id == flow_id).first()
        if not flow: return None

        from app.services.flow_service import FlowService
        from app.schemas.flow_schemas import FlowSaveSchema
        from app.services.feature_service import FeatureService
        from app.schemas.feature_schemas import FeatureCreate
        from app.models.feature_models import FeatureModel

        # Look up original feature
        orig_feat = db.query(FeatureModel).filter(FeatureModel.id == flow.project_id).first()
        if not orig_feat: return None

        # Get full flow structure
        try:
            original_flow_data = FlowService.load(db, flow.project_id, company_id, flow.id, flow.flow_type)
        except Exception as e:
            logger.error(f"Failed to load original flow for AI: {e}")
            return None

        effective_flow_type = "mobile" if flow.flow_type == "mobile" else ("e2e" if flow.flow_type in ["e2e", "web"] else "api")

        # Minimal serialization to avoid token explosion, but keep required validation fields
        min_nodes = []
        for n in original_flow_data.get("nodes", []):
            min_nodes.append({
                "id": n.get("id"),
                "type": n.get("type"),
                "position": n.get("position"),
                "data": n.get("data") or {"name": "Node", "color": "#10b981", "childCount": 0, "isCollapsed": False, "nodeType": effective_flow_type}
            })

        min_edges = [{"id": e.get("id"), "source": e.get("source"), "target": e.get("target")} for e in original_flow_data.get("edges", [])]
        
        min_card_data = {}
        for k, v in original_flow_data.get("cardData", {}).items():
            c_item = {
                "name": v.get("name"),
                "nodeType": v.get("nodeType") or effective_flow_type,
            }
            if effective_flow_type in ["mobile", "e2e"]:
                e_steps = v.get("e2eSteps") or v.get("steps") or v.get("e2e_steps") or []
                c_item["e2eSteps"] = [{
                    "id": s.get("id") or f"step-{idx}",
                    "name": s.get("name"),
                    "type": s.get("type"),
                    "properties": s.get("properties")
                } for idx, s in enumerate(e_steps) if isinstance(s, dict)]
            else:
                c_item["apiCalls"] = [{
                    "id": a.get("id") or f"step-{idx}",
                    "method": a.get("method"),
                    "url": a.get("url"),
                    "body": a.get("body")
                } for idx, a in enumerate(v.get("apiCalls", [])) if isinstance(a, dict)]
            min_card_data[k] = c_item

        step_field = "e2eSteps" if effective_flow_type in ["mobile", "e2e"] else "apiCalls"
        prompt = f"""
        You are an expert QA automation engineer. The user has an existing {effective_flow_type.upper()} test flow.
        You must create a NEW test scenario branch that implements: "{scenario_title}".
        
        Original Flow Data (JSON for context, DO NOT include in your response):
        nodes: {json.dumps(min_nodes)}
        edges: {json.dumps(min_edges)}
        cardData: {json.dumps(min_card_data)}
        
        Task: 
        1. Create a NEW set of nodes and edges that represent the alternative test scenario.
        2. DO NOT create fake or non-existent steps. Modify parameters, bodies, or properties of the existing {step_field} to inject the fault or test condition.
        3. The first node of your new branch MUST connect from the 'start' node. So create an edge with source="start" and target="your_first_new_node_id".
        4. RETURN ONLY THE NEW NODES, EDGES, AND CARD DATA. Do NOT return the original nodes.
        5. In `cardData` and `nodes`, ALWAYS set `"nodeType": "{effective_flow_type}"`.
        
        Format exactly like this JSON:
        {{
           "nodes": [ ... ],
           "edges": [ ... ],
           "cardData": {{ "your_node_id": {{ "name": "...", "nodeType": "{effective_flow_type}", "{step_field}": [...] }} }}
        }}
        
        Respond ONLY with raw JSON. No markdown backticks.
        """
        
        content = AnalysisService._call_llm(db, user_id, prompt, temperature=0.1)
        if not content:
            raise ValueError("O assistente de IA retornou uma resposta vazia ou ocorreu um timeout na chamada.")
        
        from app.services.skill_service import _robust_json_parse
        try:
            suggested_data = _robust_json_parse(content)
            if not isinstance(suggested_data, dict):
                suggested_data = {}
            
            # Merge with original first, avoiding duplicate node IDs
            merged_nodes_dict = {n.get("id"): n for n in original_flow_data.get("nodes", [])}
            
            for ai_node in suggested_data.get("nodes", []):
                nid = ai_node.get("id")
                if nid in merged_nodes_dict:
                    merged_nodes_dict[nid].update(ai_node)
                else:
                    merged_nodes_dict[nid] = ai_node
                    
            merged_nodes = list(merged_nodes_dict.values())
            
            # Ensure required Pydantic fields (position, data) exist and nodeType is set
            for idx, n in enumerate(merged_nodes):
                if "position" not in n:
                    n["position"] = {"x": idx * 150, "y": 150}
                if "data" not in n or not isinstance(n["data"], dict):
                    n["data"] = {"name": n.get("id", "Node"), "color": "#10b981", "childCount": 0, "isCollapsed": False, "nodeType": effective_flow_type}
                else:
                    n["data"]["nodeType"] = n["data"].get("nodeType") or effective_flow_type
                    
            merged_edges = original_flow_data.get("edges", []) + suggested_data.get("edges", [])
            
            # Normalize edges for Pydantic EdgeSchema
            import uuid
            for e in merged_edges:
                if "source" not in e and "from_node" in e:
                    e["source"] = e.pop("from_node")
                if "target" not in e and "to_node" in e:
                    e["target"] = e.pop("to_node")
                if "id" not in e:
                    e["id"] = f"reactflow__edge-{e.get('source')}-{e.get('target')}-{uuid.uuid4().hex[:6]}"
                    
            merged_card_data = original_flow_data.get("cardData", {})
            merged_card_data.update(suggested_data.get("cardData", {}))
            
            # Normalize AI generated headers/params and legacy DB data to match Pydantic ApiCallSchema (List of Dicts)
            for card_id, card_data in merged_card_data.items():
                if isinstance(card_data, dict):
                    card_data["nodeType"] = card_data.get("nodeType") or effective_flow_type
                    if effective_flow_type in ["mobile", "e2e"] and "e2eSteps" not in card_data:
                        if "steps" in card_data:
                            card_data["e2eSteps"] = card_data.pop("steps")
                        elif "e2e_steps" in card_data:
                            card_data["e2eSteps"] = card_data.pop("e2e_steps")
                
                # Gather all apiCalls lists to normalize (top-level + envData)
                all_api_calls_lists = []
                
                if "apiCalls" in card_data:
                    # If AI returned apiCalls as a stringified JSON array, parse it first
                    if isinstance(card_data["apiCalls"], str):
                        try:
                            card_data["apiCalls"] = json.loads(card_data["apiCalls"])
                        except:
                            card_data["apiCalls"] = []
                    # If AI returned apiCalls as a dict, we convert its values to a list
                    if isinstance(card_data["apiCalls"], dict):
                        card_data["apiCalls"] = list(card_data["apiCalls"].values())
                    
                    all_api_calls_lists.append(card_data.get("apiCalls", []))
                    
                env_data = card_data.get("envData", {})
                for env_name, env_details in env_data.items():
                    if isinstance(env_details, dict) and "apiCalls" in env_details:
                        if isinstance(env_details["apiCalls"], str):
                            try:
                                env_details["apiCalls"] = json.loads(env_details["apiCalls"])
                            except:
                                env_details["apiCalls"] = []
                        if isinstance(env_details["apiCalls"], dict):
                            env_details["apiCalls"] = list(env_details["apiCalls"].values())
                        
                        all_api_calls_lists.append(env_details.get("apiCalls", []))
                
                for api_calls_list in all_api_calls_lists:
                    if not isinstance(api_calls_list, list):
                        continue
                    for idx, api_call in enumerate(api_calls_list):
                        if not isinstance(api_call, dict):
                            continue
                            
                        # Ensure required Pydantic ApiCallSchema fields are present
                        if "id" not in api_call: api_call["id"] = f"ai-step-{idx}"
                        if "method" not in api_call: api_call["method"] = "GET"
                        if "url" not in api_call: api_call["url"] = "http://localhost"
                        
                        for field in ["headers", "params"]:
                            if field in api_call:
                                val = api_call[field]
                                new_val = []
                                if isinstance(val, dict):
                                    new_val = [{"key": str(k), "value": str(v)} for k, v in val.items()]
                                elif isinstance(val, list):
                                    for item in val:
                                        if isinstance(item, dict):
                                            if "key" in item and "value" in item:
                                                new_val.append({"key": str(item["key"]), "value": str(item["value"])})
                                            else:
                                                for k, v in item.items():
                                                    new_val.append({"key": str(k), "value": str(v)})
                                elif isinstance(val, str):
                                    try:
                                        parsed = json.loads(val)
                                        if isinstance(parsed, dict):
                                            new_val = [{"key": str(k), "value": str(v)} for k, v in parsed.items()]
                                        elif isinstance(parsed, list):
                                            for item in parsed:
                                                if isinstance(item, dict):
                                                    if "key" in item and "value" in item:
                                                        new_val.append({"key": str(item["key"]), "value": str(item["value"])})
                                                    else:
                                                        for k, v in item.items():
                                                            new_val.append({"key": str(k), "value": str(v)})
                                    except:
                                        pass
                                api_call[field] = new_val
            
            # Check if start node exists, if not inject it
            start_node_exists = any(n.get("id") == "start" for n in merged_nodes)
            if not start_node_exists:
                merged_nodes.append({
                    "id": "start",
                    "type": "startNode",
                    "position": {"x": 0, "y": 150},
                    "data": {"name": "Start", "color": "#10b981", "childCount": 0, "isCollapsed": False}
                })
            
            # Prepare payload for saving
            save_payload = {
                "projectId": flow.project_id,
                "name": flow.name,
                "flow_type": flow.flow_type,
                "nodes": merged_nodes,
                "edges": merged_edges,
                "cardData": merged_card_data
            }

            # Save full flow structurally (updates the existing flow)
            save_schema = FlowSaveSchema(**save_payload)
            result = FlowService.save(db, save_schema, company_id, user_id)

            return {
                "id": result.get("id"),
                "project_id": flow.project_id,
                "name": flow.name,
                "status": "generated",
                "message": "Test scenario flow updated successfully"
            }
            
        except json.decoder.JSONDecodeError as e:
            logger.error(f"JSON Decode Error in AI response: {e}\nContent: {content}")
            raise ValueError("O assistente de IA gerou uma resposta em formato inválido. Por favor, tente novamente.")
        except Exception as e:
            logger.error(f"Failed to implement test scenario: {e}")
            raise ValueError(f"Falha ao implementar cenário de teste: {str(e)}")

    @staticmethod
    def heal_selector(db: Session, user_id: int, broken_selector: str, action_value: str, step_type: str, html_snippet: str) -> str:
        """
        Uses AI to attempt to find a broken or missing CSS/XPath selector by analyzing a snippet of the current DOM.
        """
        import re
        def _clean(text):
            if not text: return ""
            text = text.strip()
            # Strip markdown code block headers like ```css\n
            text = re.sub(r"```[a-zA-Z]*\n?", "", text)
            # Remove remaining backticks
            return text.replace("```", "").replace("`", "").strip()

        settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == user_id).first()
        if not settings:
            try:
                settings = AgentSettingsDB(
                    user_id=user_id,
                    ai_enabled=True,
                    ai_provider="flow_ia",
                    ai_model="qwen2.5:14b-instruct-q4_K_M"
                )
                db.add(settings)
                db.commit()
                db.refresh(settings)
            except Exception:
                db.rollback()
                settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == user_id).first()

        if not settings or not settings.ai_enabled:
            logger.warning(f"⚠️ [Auto-Heal] Aborted: AI is disabled for user_id={user_id}")
            return None
        
        logger.info(f"🤖 [Auto-Heal] Provider Found: '{settings.ai_provider}'")
        
        trimmed_key = (settings.ai_api_key[:5] + "...") if settings.ai_api_key else "EMPTY"
        logger.info(f"🤖 [Auto-Heal] API Key Check: {trimmed_key}")
        
        if not settings.ai_api_key and settings.ai_provider not in ("ollama", "flow_ia"):
            logger.warning(f"⚠️ [Auto-Heal] Aborted: AI API Key is missing for provider {settings.ai_provider}")
            return None
            
        # Truncate HTML to prevent token overflow
        if len(html_snippet) > 8000:
            html_snippet = html_snippet[:8000] + "\n...[truncated]"

        prompt = f"""
        You are an expert QA Automation Engineer. A Playwright E2E test just failed because the element could not be found with this selector:
        Broken Selector: `{broken_selector}`
        
        The intended action was `{step_type}`. The value/text involved is: `{action_value}`.
        
        Here is a stripped down snippet of the current DOM structure where the element should be:
        ```html
        {html_snippet}
        ```
        
        Analyze the HTML. The class, ID, or structure might have changed slightly from the broken selector.
        Identify the correct new Playwright locator strategy for the intended element.
        CRITICAL RULES:
        1. Ensure your selector is STRICTLY UNIQUE and identifies EXACTLY ONE element on the page.
        2. Prefer exact text matches (e.g., `text="Exact Text"`) or specific attributes (e.g., `[data-testid="submit"]`, `button[type="submit"]`).
        3. AVOID generic substring matches like `:has-text("Login")` if there are multiple elements containing that text.
        4. NEVER use Playwright JS methods inside the selector string (DO NOT use `.exact()`, `.nth()`, `.first()`). The string must be a pure CSS or text selector.
        5. Return ONLY the raw selector string. No markdown formatting, no explanations, no JSON. Just the string the automation engine can use directly with Playwright (e.g. `button#new-login`, `text="Submit"`, or `[name="email_addr"]`).
        """
        
        try:
            logger.info(f"🤖 [Auto-Heal] Firing selector heal request via _call_llm for provider: {settings.ai_provider}")
            raw_result = AnalysisService._call_llm(db, user_id, prompt, temperature=0.0)
            if raw_result:
                selector = _clean(raw_result)
                logger.info(f"✨ [Auto-Heal] AI Returned selector: {selector}")
                return selector
            else:
                logger.warning(f"❌ [Auto-Heal] AI Provider returned empty response.")
                return None
        except Exception as e:
            logger.error(f"💥 [Auto-Heal] LLM Fatal Error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    @staticmethod
    def resolve_ambiguity(db: Session, user_id: int, generic_selector: str, action_value: str, step_type: str, candidates_html: str) -> str:
        """
        Uses AI to choose the correct element index from a list of candidates when a generic selector matches multiple elements.
        """
        import re
        def _clean(text):
            if not text: return ""
            text = text.strip()
            match = re.search(r'\d+', text)
            if match:
                return match.group()
            return None

        settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == user_id).first()
        if not settings or not settings.ai_enabled or (not settings.ai_api_key and settings.ai_provider not in ("ollama", "flow_ia")):
            return None
            
        prompt = f"""
        You are an expert QA Automation Engineer. A Playwright E2E test found multiple elements matching the generic selector `{generic_selector}`.
        
        The intended action is `{step_type}`. The step name or context is: `{action_value}`.
        
        Here are the candidates:
        {candidates_html}
        
        Analyze the candidates. Which index [0], [1], [2], etc., is the most likely target for the action?
        CRITICAL RULES:
        1. Return ONLY the integer number of the index (e.g. `0` or `1`).
        2. Do NOT return any text, explanation, or selector string. Just the number.
        """
        
        try:
            if settings.ai_provider == "openai":
                headers = {"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"}
                data = {"model": settings.ai_model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.0}
                base_url = settings.ai_base_url if settings.ai_base_url else "https://api.openai.com/v1"
                if not base_url.endswith("/"): base_url += "/"
                url = f"{base_url}chat/completions" if "chat/completions" not in base_url else base_url
                resp = requests.post(url, headers=headers, json=data, timeout=20)
                if resp.status_code == 200:
                    return _clean(resp.json()["choices"][0]["message"]["content"])

            elif settings.ai_provider == "anthropic":
                headers = {"x-api-key": settings.ai_api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
                data = {"model": settings.ai_model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 10}
                resp = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=data, timeout=20)
                if resp.status_code == 200:
                    return _clean(resp.json()["content"][0]["text"])

            elif settings.ai_provider == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.ai_model}:generateContent?key={settings.ai_api_key}"
                data = {"contents": [{"parts": [{"text": prompt}]}]}
                resp = requests.post(url, json=data, timeout=20)
                if resp.status_code == 200:
                    text = resp.json().get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                    return _clean(text)
            
            elif settings.ai_provider == "deepseek":
                 headers = {"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"}
                 data = {"model": settings.ai_model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.0, "max_tokens": 10}
                 resp = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=data, timeout=20)
                 if resp.status_code == 200:
                     return _clean(resp.json()["choices"][0]["message"]["content"])
            
            elif settings.ai_provider == "flow_ia":
                # Re-use the shared helper — no duplicate URL/header building
                url, headers, base_url = _build_flow_ia_request_params(db, user_id, settings)
                model_name = _detect_model(base_url)  # cached, no extra HTTP round-trip
                data = {"model": model_name, "messages": [{"role": "user", "content": prompt}], "stream": False}
                resp = requests.post(url, headers=headers, json=data, timeout=30)
                if resp.status_code == 200:
                    return _clean(resp.json()["choices"][0]["message"]["content"])
            
            elif settings.ai_provider == "ollama":
                from app.config import settings as app_settings
                license_url = settings.ai_base_url or app_settings.LICENSE_MANAGER_URL
                license_key = settings.ai_api_key
                if not (license_key and license_key.startswith("QAFLOW-")):
                    license_url = app_settings.LICENSE_MANAGER_URL
                    license_key = app_settings.QA_FLOW_LICENSE_KEY
                    
                if license_url and license_key:
                    url = f"{license_url.rstrip('/')}/api/ai/chat"
                    headers = {"Content-Type": "application/json", "X-License-Key": license_key, "X-User-Identifier": f"user_{settings.user_id}"}
                    model_name = settings.ai_model if settings.ai_model and settings.ai_model not in ("gpt-4o", "flow-ia", "llama3") else _detect_model(license_url or "http://host.docker.internal:11434/v1")
                    data = {"model": model_name, "messages": [{"role": "user", "content": prompt}], "stream": False}
                    resp = requests.post(url, headers=headers, json=data, timeout=300)
                    if resp.status_code == 200:
                        return _clean(resp.json()["choices"][0]["message"]["content"])
                else:
                    default_url = "http://host.docker.internal:11434/v1"
                    base_url = settings.ai_base_url if settings.ai_base_url else default_url
                    if not base_url.endswith("/"): base_url += "/"
                    url = f"{base_url}chat/completions" if "chat/completions" not in base_url else base_url
                    headers = {"Content-Type": "application/json"}
                    if settings.ai_api_key: headers["Authorization"] = f"Bearer {settings.ai_api_key}"
                    model_name = settings.ai_model if settings.ai_model and settings.ai_model not in ("gpt-4o", "flow-ia", "llama3") else _detect_model(base_url)
                    data = {"model": model_name, "messages": [{"role": "user", "content": prompt}], "stream": False}
                    resp = requests.post(url, headers=headers, json=data, timeout=300)
                    if resp.status_code == 200:
                        return _clean(resp.json()["choices"][0]["message"]["content"])
                        
        except Exception as e:
            logger.error(f"💥 [Resolve Ambiguity] LLM Fatal Error: {e}")
        
        return None

    @staticmethod
    def generate_assertions(db: Session, user_id: int, api_data: dict):
        """
        Generates assertion suggestions based on API response data using AI or smart heuristics.
        """
        from app.services.assertion_engine import _resolve_json_path

        # CONTRACT MODE: Recursive JSON Walker (Priority over AI/Simulation)
        # CONTRACT MODE: JSON Schema Generation (Using Genson or Swagger Schema)
        if api_data.get('mode') == 'contract':
             try:
                 def _sanitize_schema_refs(node, root=None, depth=0, seen=None):
                     if depth > 40:
                         return {"type": "object"}
                     if seen is None:
                         seen = set()
                     if root is None:
                         root = node if isinstance(node, dict) else {}
                     if not isinstance(node, dict):
                         if isinstance(node, list):
                             return [_sanitize_schema_refs(item, root, depth + 1, seen) for item in node]
                         return node
                     ref = node.get("$ref")
                     if isinstance(ref, str):
                         if ref in seen:
                             clean = {k: v for k, v in node.items() if k != "$ref"}
                             return {"type": "object", **clean}
                         new_seen = set(seen)
                         new_seen.add(ref)
                         if ref.startswith("#/"):
                             parts = [p.replace("~1", "/").replace("~0", "~") for p in ref.lstrip("#/").split("/")]
                             curr = root
                             resolved = None
                             try:
                                 for part in parts:
                                     if isinstance(curr, dict) and part in curr:
                                         curr = curr[part]
                                     else:
                                         curr = None
                                         break
                                 if isinstance(curr, dict):
                                     resolved = curr
                             except Exception:
                                 resolved = None
                             if resolved:
                                 res_clean = _sanitize_schema_refs(resolved, root, depth + 1, new_seen)
                                 if isinstance(res_clean, dict):
                                     merged = {k: v for k, v in res_clean.items() if k != "$ref"}
                                     for k, v in node.items():
                                         if k != "$ref":
                                             merged[k] = _sanitize_schema_refs(v, root, depth + 1, new_seen)
                                     return merged
                                 return res_clean
                         clean = {k: v for k, v in node.items() if k != "$ref"}
                         return {"type": "object", **clean}

                     result = {}
                     for k, v in node.items():
                         if k in ("properties", "patternProperties", "definitions") and isinstance(v, dict):
                             result[k] = {pk: _sanitize_schema_refs(pv, root, depth + 1, seen) for pk, pv in v.items()}
                         elif k in ("allOf", "oneOf", "anyOf") and isinstance(v, list):
                             result[k] = [_sanitize_schema_refs(item, root, depth + 1, seen) for item in v]
                         elif k == "items":
                             if isinstance(v, list):
                                 result[k] = [_sanitize_schema_refs(item, root, depth + 1, seen) for item in v]
                             elif isinstance(v, dict):
                                 result[k] = _sanitize_schema_refs(v, root, depth + 1, seen)
                             else:
                                 result[k] = v
                         elif k == "additionalProperties" and isinstance(v, dict):
                             result[k] = _sanitize_schema_refs(v, root, depth + 1, seen)
                         elif isinstance(v, dict):
                             result[k] = _sanitize_schema_refs(v, root, depth + 1, seen)
                         elif isinstance(v, list):
                             result[k] = [_sanitize_schema_refs(item, root, depth + 1, seen) for item in v]
                         else:
                             result[k] = v
                     return result

                 # Se o schema do Swagger/OpenAPI foi fornecido, utiliza diretamente o schema oficial sanitizado
                 raw_schema = api_data.get('schema')
                 if raw_schema:
                     if isinstance(raw_schema, str):
                         try:
                             raw_schema = json.loads(raw_schema)
                         except Exception:
                             pass
                     if isinstance(raw_schema, dict) and ('properties' in raw_schema or 'type' in raw_schema or '$schema' in raw_schema):
                         cleaned_schema = _sanitize_schema_refs(raw_schema)
                         return [{
                             "source": "contract",
                             "property": "schema",
                             "operator": "json_schema",
                             "target": json.dumps(cleaned_schema)
                         }]

                 from genson import SchemaBuilder
                 builder = SchemaBuilder()
                 builder.add_object(api_data.get('body'))
                 schema = builder.to_schema()
                 
                 # Enhance Schema with Format Detection (Email, UUID, Date-Time) WITHOUT locking lengths
                 def enrich_schema(schema_node, data_node):
                    if schema_node.get('type') == 'object' and isinstance(data_node, dict):
                        props = schema_node.get('properties', {})
                        for key, sub_schema in props.items():
                            if key in data_node:
                                enrich_schema(sub_schema, data_node[key])
                                
                    elif schema_node.get('type') == 'array' and isinstance(data_node, list):
                        items_schema = schema_node.get('items')
                        if isinstance(items_schema, dict) and len(data_node) > 0:
                            enrich_schema(items_schema, data_node[0])
                            
                    elif schema_node.get('type') == 'string' and isinstance(data_node, str):
                        # IMPORTANTE: Não travar minLength e maxLength no tamanho exato do valor de exemplo
                        # pois os valores de produção variam em tamanho.
                        import re
                        if "@" in data_node and "." in data_node:
                            schema_node['format'] = 'email'
                        elif re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', data_node, re.IGNORECASE):
                            schema_node['format'] = 'uuid'
                        elif re.match(r'^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2})?', data_node):
                            schema_node['format'] = 'date-time'

                 enrich_schema(schema, api_data.get('body'))

                 return [{
                     "source": "contract",
                     "property": "schema",
                     "operator": "json_schema",
                     "target": json.dumps(schema)
                 }]
             except ImportError:
                 return [{ "source": "error", "property": "backend", "operator": "equals", "target": "genson_missing" }]
             except Exception as e:
                 print(f"Schema Gen Error: {e}")
                 return [{ "source": "error", "property": "backend", "operator": "equals", "target": str(e) }]

        # Ensure body is parsed if provided as JSON string
        body = api_data.get('body')
        if isinstance(body, str):
            try:
                body = json.loads(body)
                api_data['body'] = body
            except Exception:
                pass

        def _normalize_assertions(raw_rules, body_data):
            """Normalizes operators and ensures no broken empty 'equals' assertions."""
            normalized = []
            seen = set()
            for r in raw_rules:
                if not isinstance(r, dict):
                    continue
                source = r.get("source") or r.get("type") or "body"
                prop = r.get("property") or ""
                op = str(r.get("operator") or "equals").strip().lower()
                target = r.get("target")
                if target is None and r.get("value") is not None:
                    target = r.get("value")

                # Map operator aliases
                if op in ("==", "eq", "is"):
                    op = "equals"
                elif op in ("!=", "neq", "notequals"):
                    op = "not_equals"
                elif op in (">", "gt", "greaterthan"):
                    op = "greater_than"
                elif op in ("<", "lt", "lessthan"):
                    op = "less_than"
                elif op in ("in",):
                    op = "contains"
                elif op in ("not_in", "notcontains"):
                    op = "not_contains"
                elif op in ("exists", "not_null"):
                    op = "is_not_null"
                elif op in ("notexists", "not_exists", "null"):
                    op = "is_null"

                # Fix empty target when operator is equals
                if op == "equals" and (target is None or target == "" or str(target).lower() in ("null", "none")):
                    actual_val = _resolve_json_path(body_data, prop) if prop and body_data else None
                    if actual_val is not None and actual_val != "":
                        target = actual_val
                    else:
                        op = "is_not_null"
                        target = ""

                # For unary operators, target is not required
                if op in ("is_not_null", "is_null"):
                    target = ""

                rule_key = f"{source}_{prop}_{op}"
                if rule_key in seen:
                    continue
                seen.add(rule_key)

                normalized.append({
                    "source": source,
                    "type": source,
                    "property": prop,
                    "operator": op,
                    "target": target,
                    "value": target
                })
            return normalized

        def _build_heuristic_assertions(data):
            """Intelligent heuristic assertion generator based on actual response data."""
            sim = []
            st = data.get('status')
            if st:
                sim.append({ "source": "statusCode", "property": "", "operator": "equals", "target": st })

            headers = data.get('headers', {}) or {}
            ct = next((v for k, v in headers.items() if k.lower() == 'content-type'), '')
            if 'json' in ct.lower():
                sim.append({ "source": "header", "property": "Content-Type", "operator": "contains", "target": "application/json" })

            sim.append({ "source": "responseTime", "property": "", "operator": "less_than", "target": 2000 })

            body_obj = data.get('body')
            if isinstance(body_obj, dict):
                for k, v in list(body_obj.items())[:12]:
                    k_lower = k.lower()
                    if k_lower in ('id', 'uuid', 'token', 'key') or k_lower.endswith('_id'):
                        if isinstance(v, (int, float)) and v > 0:
                            sim.append({ "source": "body", "property": k, "operator": "greater_than", "target": 0 })
                        else:
                            sim.append({ "source": "body", "property": k, "operator": "is_not_null", "target": "" })
                    elif isinstance(v, bool):
                        sim.append({ "source": "body", "property": k, "operator": "equals", "target": v })
                    elif k_lower in ('status', 'state', 'success', 'code', 'type', 'category', 'role', 'action'):
                        sim.append({ "source": "body", "property": k, "operator": "equals", "target": v })
                    elif k_lower in ('message', 'msg', 'description', 'detail', 'title', 'error'):
                        if isinstance(v, str) and len(v) > 25:
                            sim.append({ "source": "body", "property": k, "operator": "contains", "target": v[:25] })
                        else:
                            sim.append({ "source": "body", "property": k, "operator": "equals", "target": v })
                    elif 'email' in k_lower and isinstance(v, str) and '@' in v:
                        sim.append({ "source": "body", "property": k, "operator": "equals", "target": v })
                    elif isinstance(v, (str, int, float)) and v not in ('', None):
                        sim.append({ "source": "body", "property": k, "operator": "equals", "target": v })
                    elif isinstance(v, list) and len(v) > 0:
                        sim.append({ "source": "body", "property": f"{k}.0", "operator": "is_not_null", "target": "" })
            elif isinstance(body_obj, list) and len(body_obj) > 0:
                first_item = body_obj[0]
                if isinstance(first_item, dict):
                    for k, v in list(first_item.items())[:6]:
                        k_lower = k.lower()
                        if k_lower in ('id', 'uuid') or k_lower.endswith('_id'):
                            sim.append({ "source": "body", "property": f"0.{k}", "operator": "is_not_null", "target": "" })
                        elif isinstance(v, (str, int, float, bool)) and v not in ('', None):
                            sim.append({ "source": "body", "property": f"0.{k}", "operator": "equals", "target": v })
                else:
                    sim.append({ "source": "body", "property": "0", "operator": "is_not_null", "target": "" })

            return sim

        settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == user_id).first()
        
        # Simulation Mode (Fallback if no key or specific provider)
        if not settings or not settings.ai_api_key or settings.ai_provider == 'simulator':
            raw_sim = _build_heuristic_assertions(api_data)
            return _normalize_assertions(raw_sim, api_data.get('body'))

        # Construct Prompt for LLM with explicit usability constraints
        status_val = api_data.get('status') or 200
        headers_dump = "{}"
        try:
            headers_dump = json.dumps(api_data.get('headers') or {}, indent=2, default=str)
        except Exception:
            pass

        body_dump = "{}"
        try:
            body_dump = json.dumps(api_data.get('body') or {}, indent=2, default=str)[:3000]
        except Exception:
            pass

        prompt = f"""
        Analyze this API Response and suggest a list of robust, highly usable assertions to validate it.
        Make sure to validate the HTTP status, SLA latency, headers, and critical fields inside the JSON body.
        
        CRITICAL USABILITY RULES:
        1. Operators MUST be one of: "equals", "not_equals", "contains", "not_contains", "greater_than", "less_than", "is_not_null", "is_null".
        2. NEVER generate empty target values or 'target: null' when using 'equals'!
        3. For dynamic ID fields, tokens, UUIDs or timestamps, use operator "is_not_null" (or "greater_than" with target 0 if numeric).
        4. For status, type, category, and specific string/number/boolean fields (e.g. name, email, pet_id, status), use operator "equals" with the EXACT REAL VALUE present in the body!
        5. For descriptive message/error fields, use operator "contains" with a meaningful snippet or "equals".
        6. Include status code (equals {status_val}), responseTime (less_than 2000), and Content-Type if JSON.
        7. Keep the list concise and high-value (6 to 10 assertions maximum).
        
        Status: {status_val}
        Headers: {headers_dump}
        Body (Truncated): {body_dump}
        
        Return a JSON list of objects with the schema:
        [
          {{ "source": "statusCode", "property": "", "operator": "equals", "target": {status_val} }},
          {{ "source": "responseTime", "property": "", "operator": "less_than", "target": 2000 }},
          {{ "source": "header", "property": "Content-Type", "operator": "contains", "target": "application/json" }},
          {{ "source": "body", "property": "id", "operator": "is_not_null", "target": "" }},
          {{ "source": "body", "property": "status", "operator": "equals", "target": "success" }}
        ]
        
        Return ONLY valid JSON.
        """

        try:
            content = AnalysisService._call_llm(db, user_id, prompt, temperature=0.2, timeout=6)
            if content:
                from app.services.skill_service import _robust_json_parse
                parsed = _robust_json_parse(content)
                if isinstance(parsed, list) and len(parsed) > 0:
                    normalized = _normalize_assertions(parsed, api_data.get('body'))
                    if len(normalized) > 0:
                        return normalized
        except Exception as e:
            logger.error(f"AI Assertion Gen Error: {e}")

        # Fallback to heuristic rules if LLM failed or returned empty
        raw_sim = _build_heuristic_assertions(api_data)
        normalized_sim = _normalize_assertions(raw_sim, api_data.get('body'))
        if normalized_sim:
            return normalized_sim

        # Guaranteed minimal fallback so it never returns empty or errors out
        return [{
            "source": "statusCode",
            "type": "statusCode",
            "property": "",
            "operator": "equals",
            "target": status_val,
            "value": status_val
        }]

    @staticmethod
    def analyze_flow_redundancy(db: Session, flow_id: int, user_id: int = None):
        """
        Identifies duplicate API calls within a specific Flow by tracing paths.
        Returns a list of suggestions.
        """
        ai_suggestions = []
        if user_id:
            try:
                ai_suggestions = AnalysisService.analyze_with_ai(db, flow_id, user_id)
            except Exception as e:
                print(f"AI Analysis failed: {e}")

        cards = db.query(FlowCardDataDB).filter(FlowCardDataDB.flow_id == flow_id).all()
        from app.models.flow_models import FlowEdgeDB
        edges = db.query(FlowEdgeDB).filter(FlowEdgeDB.flow_id == flow_id).all()
        
        from collections import defaultdict
        adj = defaultdict(list)
        in_degree = defaultdict(int)
        nodes = {str(card.node_id): card for card in cards}
        
        for e in edges:
            src = str(e.source)
            tgt = str(e.target)
            adj[src].append(tgt)
            in_degree[tgt] += 1
            
        roots = [nid for nid in nodes.keys() if in_degree[nid] == 0]
        if not roots and nodes:
            roots = list(nodes.keys()) # Fallback if cyclic or missing edges
            
        paths = []
        def dfs(current_node, current_path, visited):
            current_path.append(current_node)
            visited.add(current_node)
            
            if not adj[current_node]:
                paths.append(list(current_path))
            else:
                for neighbor in adj[current_node]:
                    if neighbor not in visited:
                        dfs(neighbor, current_path, set(visited))
                    else:
                        paths.append(list(current_path)) # Stop at cycle
            current_path.pop()
            
        for r in roots:
            dfs(r, [], set())

        if not paths or (len(paths) > 1 and all(len(p) <= 1 for p in paths)):
            paths = [list(nodes.keys())]
            
        suggestions = []
        redundant_signatures_found = set()

        for path in paths:
            call_signatures = {}
            for node_id in path:
                if node_id not in nodes: continue
                card = nodes[node_id]
                
                if not card.api_calls: continue
                api_data_raw = card.api_calls
                
                if not api_data_raw: continue
                if isinstance(api_data_raw, str):
                    try:
                        api_data_raw = json.loads(api_data_raw)
                    except:
                        continue
                
                calls = []
                if isinstance(api_data_raw, list):
                    calls = api_data_raw
                elif isinstance(api_data_raw, dict):
                    if "url" in api_data_raw and "method" in api_data_raw:
                        calls = [api_data_raw]
                    else:
                        calls = list(api_data_raw.values())

                for api_data in calls:
                    method = api_data.get("method", "").upper()
                    url = api_data.get("url", "")
                    if not method or not url: continue

                    signature = f"{method}:{url}"
                    if signature not in call_signatures:
                        call_signatures[signature] = []
                    call_signatures[signature].append({
                        "node_name": card.name,
                        "node_id": card.node_id
                    })

            for signature, usage in call_signatures.items():
                if len(usage) > 1:
                    usage_ids = tuple(sorted([u["node_id"] for u in usage]))
                    red_key = f"{signature}|{usage_ids}"
                    if red_key not in redundant_signatures_found:
                        redundant_signatures_found.add(red_key)
                        names = [u["node_name"] for u in usage]
                        suggestions.append({
                            "type": "redundancy",
                            "severity": "warning",
                            "message": f"Duplicate request detected in same path: {signature}",
                            "details": f"This request appears {len(usage)} times in the same execution path across nodes: {', '.join(names)}. Consider extracting to a variable.",
                            "nodes": usage
                        })
        
        if 'ai_suggestions' in locals() and ai_suggestions:
            suggestions.extend(ai_suggestions)

        return suggestions

    @staticmethod
    def analyze_performance_trends(db: Session, project_id: int = None, limit: int = 50, company_id: int = None):
        """
        Analyzes execution history to find performance regressions.
        Compares last 10 runs vs previous 10 runs.
        """
        query = db.query(
            ApiExecutionHistory.url,
            ApiExecutionHistory.method,
            ApiExecutionHistory.node_name,
            ApiExecutionHistory.response_time,
            ApiExecutionHistory.created_at
        )
        
        if project_id:
            query = query.filter(ApiExecutionHistory.project_id == project_id)
        elif company_id:
            from app.models.product_models import ProductModel
            from app.models.feature_models import FeatureModel
            product_ids = [p[0] for p in db.query(ProductModel.id).filter(ProductModel.company_id == company_id).all()]
            feature_ids = [f[0] for f in db.query(FeatureModel.id).join(ProductModel).filter(ProductModel.company_id == company_id).all()]
            all_ids = set(product_ids + feature_ids)
            if all_ids:
                query = query.filter(ApiExecutionHistory.project_id.in_(list(all_ids)))
            
        # Get recent history
        history_items = query.order_by(ApiExecutionHistory.created_at.desc()).limit(limit * 4).all()
        
        # Group by Endpoint (Method + URL)
        endpoints = {}
        for item in history_items:
            key = f"{item.method} {item.url}"
            if key not in endpoints:
                endpoints[key] = []
            endpoints[key].append(item.response_time)

        alerts = []
        
        for key, times in endpoints.items():
            if len(times) < 10:
                continue # Not enough data
            
            # Split into Recent (latest 5) and Baseline (next 5)
            recent = times[:5]
            baseline = times[5:10]
            
            avg_recent = sum(recent) / len(recent)
            avg_baseline = sum(baseline) / len(baseline)
            
            # Logic 1: High Latency (> 2s)
            if avg_recent > 2000:
                alerts.append({
                    "type": "performance",
                    "severity": "critical",
                    "metric": "latency",
                    "resource": key,
                    "value": f"{avg_recent:.0f}ms",
                    "message": f"High latency detected on {key}. Average: {avg_recent:.0f}ms."
                })
                continue # Don't double report

            # Logic 2: Regression (> 30% slower)
            if avg_baseline > 0 and avg_recent > (avg_baseline * 1.3):
                increase = ((avg_recent - avg_baseline) / avg_baseline) * 100
                alerts.append({
                    "type": "performance",
                    "severity": "warning",
                    "metric": "regression",
                    "resource": key,
                    "value": f"+{increase:.1f}%",
                    "message": f"Performance regression: {key} is {increase:.0f}% slower than previous runs."
                })

        return alerts

    @staticmethod
    def analyze_performance_test(db: Session, job_id: str, user_id: int):
        from app.models.performance_models import PerformanceTestResult
        result = db.query(PerformanceTestResult).filter(PerformanceTestResult.id == job_id).first()
        if not result:
            return "Test result not found."

        summary = f"Test Name: {result.test_name}\nTarget: {result.target_url}\nVirtual Users: {result.virtual_users}\nDuration: {result.duration_seconds}s\n"
        summary += f"Total Requests: {result.total_requests} (Success: {result.success_requests}, Failed: {result.failed_requests})\n"
        summary += f"RPS: {result.requests_per_second:.2f}\nAvg Latency: {result.avg_latency:.2f}ms\nP50 Latency: {result.p50_latency:.2f}ms\nP95 Latency: {result.p95_latency:.2f}ms\n"

        prompt = f"""
        You are an expert QA Performance Engineer. Analyze the following load test result.
        
        {summary}
        
        Provide a professional diagnostic of this test execution. Discuss:
        - The reliability and stability of the API.
        - The latency and throughput (RPS).
        - Any bottlenecks or error rates that need attention.
        - Recommendations for improvement or scaling.
        
        Format your response in Markdown using bullet points, headers, and bold text for emphasis.
        Do not use code blocks for the entire response. Write directly in Markdown.
        """
        
        content = AnalysisService._call_llm(db, user_id, prompt, temperature=0.3)
        return content or "⚠️ Não foi possível gerar o diagnóstico da IA. Verifique se você configurou uma API Key válida no painel de Agentes."

    @staticmethod
    def analyze_performance_comparison(db: Session, job_id_1: str, job_id_2: str, user_id: int):
        from app.models.performance_models import PerformanceTestResult
        res1 = db.query(PerformanceTestResult).filter(PerformanceTestResult.id == job_id_1).first()
        res2 = db.query(PerformanceTestResult).filter(PerformanceTestResult.id == job_id_2).first()
        if not res1 or not res2:
            return "Test results not found."

        summary = f"TEST A: {res1.test_name} ({res1.target_url})\nUsers: {res1.virtual_users}, Duration: {res1.duration_seconds}s, Total Reqs: {res1.total_requests}, Errors: {res1.failed_requests}, RPS: {res1.requests_per_second:.2f}, Latency: {res1.avg_latency:.2f}ms\n\n"
        summary += f"TEST B: {res2.test_name} ({res2.target_url})\nUsers: {res2.virtual_users}, Duration: {res2.duration_seconds}s, Total Reqs: {res2.total_requests}, Errors: {res2.failed_requests}, RPS: {res2.requests_per_second:.2f}, Latency: {res2.avg_latency:.2f}ms\n"

        prompt = f"""
        You are an expert QA Performance Engineer. Compare the following two load test executions.
        
        {summary}
        
        Provide a professional comparative diagnostic. Discuss:
        - Which test performed better and why?
        - Was there a regression or improvement in Latency, RPS, or Error Rates?
        - Did the system handle the load difference (if any) well?
        - Conclusion and recommendations.
        
        Format your response in Markdown using bullet points, headers, and bold text for emphasis.
        Do not use code blocks for the entire response. Write directly in Markdown.
        """
        
        content = AnalysisService._call_llm(db, user_id, prompt, temperature=0.3)
        return content or "⚠️ Não foi possível gerar o diagnóstico comparativo com IA. Verifique se você configurou uma API Key válida no painel de Agentes."

    @staticmethod
    def generate_flow_from_text(db: Session, prompt: str, project_id: int, company_id: int, user_id: int):
        """Creates a NEW flow from a natural language prompt."""
        from app.services.flow_service import FlowService
        from app.schemas.flow_schemas import FlowSaveSchema
        
        llm_prompt = f"""
        You are an expert QA Automation Engineer. The user wants to create an automated API test flow.
        User Request: "{prompt}"
        
        Generate a complete Flow configuration. The Flow is composed of "nodes" (steps/cards) and "edges" (connections).
        Return a JSON structure with the following exact format:
        {{
           "name": "Suggested Name",
           "flow_type": "api", 
           "nodes": [
               {{ "id": "1", "type": "apiCard", "position": {{"x": 100, "y": 100}} }},
               {{ "id": "2", "type": "apiCard", "position": {{"x": 100, "y": 300}} }}
           ],
           "edges": [
               {{ "id": "e1-2", "source": "1", "target": "2", "type": "smoothstep", "animated": true }}
           ],
           "cardData": {{
              "1": {{
                 "name": "Step 1",
                 "description": "...",
                 "apiCalls": [ {{ "method": "GET", "url": "https://api.example.com", "body": "", "headers": "" }} ],
                 "bddScenarios": []
              }},
              "2": {{
                 "name": "Step 2",
                 "description": "...",
                 "apiCalls": [ {{ "method": "POST", "url": "...", "body": "{{\\"key\\": \\"value\\"}}", "headers": "{{\\"Content-Type\\": \\"application/json\\"}}" }} ],
                 "bddScenarios": []
              }}
           }}
        }}
        
        Rules:
        - If the user doesn't specify URLs, use generic placeholders.
        - Space nodes out by increasing the 'y' coordinate by 200 for each subsequent node.
        - Respond ONLY with raw JSON. No markdown backticks.
        """
        
        content = AnalysisService._call_llm(db, user_id, llm_prompt, temperature=0.1)
        if not content:
            raise ValueError("Failed to generate flow from AI. Please check AI settings/API Key.")
            
        content = content.replace("```json", "").replace("```", "").strip()
        try:
            suggested_data = json.loads(content)
        except Exception as e:
            logger.error(f"Failed to parse generated flow: {e}\\nContent: {content}")
            raise ValueError("AI returned invalid JSON format.")

        try:
            suggested_data["projectId"] = project_id
            save_schema = FlowSaveSchema(**suggested_data)
            result = FlowService.save(db, save_schema, company_id, user_id)
            return {
                "id": result.get("id"),
                "project_id": project_id,
                "status": "generated",
                "message": "Flow generated successfully"
            }
        except Exception as e:
            logger.error(f"Failed to save generated flow: {e}")
            raise ValueError(f"Failed to save flow: {str(e)}")
