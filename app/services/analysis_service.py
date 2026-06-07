from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.flow_models import FlowCardDataDB, FlowDB
from app.models.api_test_history_models import ApiExecutionHistory
from app.models.agent_models import AgentSettingsDB
from datetime import datetime, timedelta
import json
import requests
import logging

logger = logging.getLogger(__name__)

class AnalysisService:

    @staticmethod
    def _call_llm(db: Session, user_id: int, prompt: str, temperature: float = 0.2) -> str:
        """Helper to call the configured LLM for a user."""
        settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == user_id).first()
        if not settings or (not settings.ai_api_key and settings.ai_provider != "ollama"):
            return None

        try:
            if settings.ai_provider == "openai":
                headers = {"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"}
                data = {"model": settings.ai_model, "messages": [{"role": "user", "content": prompt}], "temperature": temperature}
                base_url = settings.ai_base_url if settings.ai_base_url else "https://api.openai.com/v1"
                if not base_url.endswith("/"): base_url += "/"
                url = f"{base_url}chat/completions" if "chat/completions" not in base_url else base_url
                resp = requests.post(url, headers=headers, json=data, timeout=60)
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"]

            elif settings.ai_provider == "anthropic":
                headers = {"x-api-key": settings.ai_api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
                data = {"model": settings.ai_model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 4096}
                resp = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=data, timeout=60)
                if resp.status_code == 200:
                    return resp.json()["content"][0]["text"]

            elif settings.ai_provider == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.ai_model}:generateContent?key={settings.ai_api_key}"
                data = {"contents": [{"parts": [{"text": prompt}]}]}
                resp = requests.post(url, json=data, timeout=60)
                if resp.status_code == 200:
                    return resp.json().get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')

            elif settings.ai_provider == "deepseek":
                headers = {"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"}
                data = {"model": settings.ai_model, "messages": [{"role": "user", "content": prompt}], "temperature": temperature, "max_tokens": 4096}
                resp = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=data, timeout=60)
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"]

            elif settings.ai_provider == "ollama":
                default_url = "http://host.docker.internal:11434/v1"
                base_url = settings.ai_base_url if settings.ai_base_url else default_url
                if not base_url.endswith("/"): base_url += "/"
                url = f"{base_url}chat/completions" if "chat/completions" not in base_url else base_url
                headers = {"Content-Type": "application/json"}
                if settings.ai_api_key: headers["Authorization"] = f"Bearer {settings.ai_api_key}"
                data = {"model": settings.ai_model or "llama3", "messages": [{"role": "user", "content": prompt}], "stream": False}
                resp = requests.post(url, headers=headers, json=data, timeout=60)
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"]

        except Exception as e:
            logger.error(f"Error calling LLM {settings.ai_provider}: {e}")
            if resp is not None:
                logger.error(f"LLM Response Error: {resp.status_code} - {resp.text}")
        return None

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
        
        Return a JSON response with a list of suggestions. Format:
        [
          {{ "type": "optimization|security|logic", "severity": "low|medium|high", "message": "Short title", "details": "Detailed explanation" }}
        ]
        Do not include markdown formatting, just raw JSON.
        """
        
        content = AnalysisService._call_llm(db, user_id, prompt)
        if not content: return []
        
        content = content.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(content)
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
        
        content = content.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(content)
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

        # Minimal serialization to avoid token explosion
        min_nodes = [{"id": n.get("id"), "type": n.get("type"), "position": n.get("position")} for n in original_flow_data.get("nodes", [])]
        min_edges = [{"id": e.get("id"), "source": e.get("source"), "target": e.get("target")} for e in original_flow_data.get("edges", [])]
        min_card_data = {}
        for k, v in original_flow_data.get("cardData", {}).items():
            min_card_data[k] = {
                "name": v.get("name"),
                "apiCalls": [{"method": a.get("method"), "url": a.get("url"), "body": a.get("body")} for a in v.get("apiCalls", [])]
            }

        prompt = f"""
        You are an expert QA automation engineer. Modify the following Flow to implement the test scenario: "{scenario_title}".
        
        Original Flow Data (JSON):
        nodes: {json.dumps(min_nodes)}
        edges: {json.dumps(min_edges)}
        cardData: {json.dumps(min_card_data)}
        
        Task: 
        1. Keep the core logic but inject a fault, change parameters, or add/remove nodes to match the scenario perfectly.
        2. Ensure node IDs and edges remain consistent where possible.
        3. Return a complete, valid JSON containing the new flow structure.
        
        Format exactly like this JSON:
        {{
           "name": "{orig_feat.name} - AI: {scenario_title}",
           "flow_type": "{flow.flow_type}",
           "nodes": [ ... ],
           "edges": [ ... ],
           "cardData": {{ "node_id": {{ ... }} }}
        }}
        
        Respond ONLY with raw JSON. No markdown backticks.
        """
        
        content = AnalysisService._call_llm(db, user_id, prompt, temperature=0.1)
        if not content: return None
        
        content = content.replace("```json", "").replace("```", "").strip()
        try:
            suggested_data = json.loads(content)
            
            # Create new Feature container
            new_feature_schema = FeatureCreate(
                name=f"[{scenario_title[:30]}] {orig_feat.name}",
                description=f"AI Generated Test Case: {scenario_title}",
                product_id=orig_feat.product_id
            )
            new_feat = FeatureService.create(db, new_feature_schema, company_id)
            if not new_feat:
                raise ValueError("Failed to create Feature container")

            # Emulate incoming payload
            suggested_data["projectId"] = new_feat.id
            if "name" not in suggested_data:
                suggested_data["name"] = new_feature_schema.name
            if "flow_type" not in suggested_data:
                suggested_data["flow_type"] = flow.flow_type

            # Save full flow structurally
            save_schema = FlowSaveSchema(**suggested_data)
            result = FlowService.save(db, save_schema, company_id, user_id)

            return {
                "id": result.get("id"),
                "project_id": new_feat.id,
                "name": new_feature_schema.name,
                "status": "generated",
                "message": "Test scenario flow generated successfully"
            }
            
        except Exception as e:
            logger.error(f"Failed to implement test scenario: {e}")
            return None

    @staticmethod
    def heal_selector(db: Session, user_id: int, broken_selector: str, action_value: str, step_type: str, html_snippet: str) -> str:
        """
        Uses AI to attempt to find a broken or missing CSS/XPath selector by analyzing a snippet of the current DOM.
        """
        settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == user_id).first()
        if not settings:
            logger.warning(f"⚠️ [Auto-Heal] Aborted: No AgentSettings found for user_id={user_id}")
            return None
        
        logger.info(f"🤖 [Auto-Heal] Provider Found: '{settings.ai_provider}'")
        
        trimmed_key = (settings.ai_api_key[:5] + "...") if settings.ai_api_key else "EMPTY"
        logger.info(f"🤖 [Auto-Heal] API Key Check: {trimmed_key}")
        
        if not settings.ai_api_key and settings.ai_provider != "ollama":
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
        Identify the correct new CSS Selector (or XPath if CSS is impossible) for the intended element.
        Return ONLY the raw selector string. No markdown formatting, no explanations, no JSON. Just the string the automation engine can use directly (e.g. `button#new-login` or `[name="email_addr"]`).
        """
        
        try:
            print(f"🤖 [Auto-Heal] Firing request to provider: {settings.ai_provider}")
            if settings.ai_provider == "openai":
                headers = {"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"}
                data = {"model": settings.ai_model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}
                base_url = settings.ai_base_url if settings.ai_base_url else "https://api.openai.com/v1"
                if not base_url.endswith("/"): base_url += "/"
                url = f"{base_url}chat/completions" if "chat/completions" not in base_url else base_url
                resp = requests.post(url, headers=headers, json=data, timeout=20)
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"].replace("```", "").strip()

            elif settings.ai_provider == "anthropic":
                headers = {"x-api-key": settings.ai_api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
                data = {"model": settings.ai_model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 100}
                resp = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=data, timeout=20)
                if resp.status_code == 200:
                    return resp.json()["content"][0]["text"].replace("```", "").strip()

            elif settings.ai_provider == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.ai_model}:generateContent?key={settings.ai_api_key}"
                data = {"contents": [{"parts": [{"text": prompt}]}]}
                resp = requests.post(url, json=data, timeout=20)
                if resp.status_code == 200:
                    text = resp.json().get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                    return text.replace("```", "").strip()
            
            elif settings.ai_provider == "deepseek":
                 headers = {"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"}
                 data = {"model": settings.ai_model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.0, "max_tokens": 100}
                 resp = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=data, timeout=20)
                 if resp.status_code == 200:
                     return resp.json()["choices"][0]["message"]["content"].replace("```", "").strip()
            
            elif settings.ai_provider == "ollama":
                # Inside Docker, 'localhost' points to the container. Ollama is usually on the host.
                default_url = "http://host.docker.internal:11434/v1"
                base_url = settings.ai_base_url if settings.ai_base_url else default_url
                if not base_url.endswith("/"): base_url += "/"
                url = f"{base_url}chat/completions" if "chat/completions" not in base_url else base_url
                headers = {"Content-Type": "application/json"}
                if settings.ai_api_key: headers["Authorization"] = f"Bearer {settings.ai_api_key}"
                data = {"model": settings.ai_model or "llama3", "messages": [{"role": "user", "content": prompt}], "stream": False}
                
                logger.info(f"📡 [Auto-Heal] Ollama Req: {url}")
                resp = requests.post(url, headers=headers, json=data, timeout=30)
                if resp.status_code == 200:
                    selector = resp.json()["choices"][0]["message"]["content"].replace("```", "").strip()
                    logger.info(f"✨ [Auto-Heal] Ollama Returned: {selector}")
                    return selector
                else:
                    logger.error(f"❌ [Auto-Heal] Ollama Error: {resp.status_code} - {resp.text}")
            else:
                logger.warning(f"❌ [Auto-Heal] Unrecognized Provider: {settings.ai_provider}")

        except Exception as e:
            logger.error(f"💥 [Auto-Heal] LLM Fatal Error: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        return None

    @staticmethod
    def generate_assertions(db: Session, user_id: int, api_data: dict):
        """
        Generates assertion suggestions based on API response data using AI.
        """
        
        # CONTRACT MODE: Recursive JSON Walker (Priority over AI/Simulation)
        # CONTRACT MODE: JSON Schema Generation (Using Genson)
        if api_data.get('mode') == 'contract':
             try:
                 from genson import SchemaBuilder
                 builder = SchemaBuilder()
                 builder.add_object(api_data.get('body'))
                 schema = builder.to_schema()
                 
                 # Enhance Schema with Format Detection (Email, UUID, URI) & Constraints (Min/Max)
                 def enrich_schema(schema_node, data_node):
                    if schema_node.get('type') == 'object' and isinstance(data_node, dict):
                        props = schema_node.get('properties', {})
                        for key, sub_schema in props.items():
                            if key in data_node:
                                enrich_schema(sub_schema, data_node[key])
                                
                    elif schema_node.get('type') == 'array' and isinstance(data_node, list):
                        items_schema = schema_node.get('items')
                        # Genson merges items into a single schema usually
                        if isinstance(items_schema, dict) and len(data_node) > 0:
                            # We can only safely infer constraints if we check all items or just the first one?
                            # For safety, let's just recurse into the first item as a sample if structure matches
                            # But better to iterate all items and find common constraints? Too complex for now.
                            # Let's just recurse into the first item to enrich children structure.
                            enrich_schema(items_schema, data_node[0])
                            
                    elif schema_node.get('type') == 'string' and isinstance(data_node, str):
                        # Min/Max Length
                        length = len(data_node)
                        schema_node['minLength'] = max(0, length - 1) # Tolerance? Or exact? User asked for min/max. 
                        # Let's set it to exact length for strict contract, or length-1 / length+1?
                        # Usually contracts want at least 1 if not empty.
                        if length > 0:
                            schema_node['minLength'] = 1 
                        else:
                            schema_node['minLength'] = 0
                            
                        # Max Length is good to have boundless or reasonable limit.
                        # Setting exact max length might be too brittle.
                        # Let's set maxLength to length + 20% or similar?
                        # User said "não esta colocando min e max length", implies they WANT it.
                        # Let's set minLength = length and maxLength = length for strictness?
                        # Or maybe minLength = 1 (if not empty) and maxLength = 255 (if reasonable)?
                        # Let's try to be smart:
                        schema_node['minLength'] = length 
                        schema_node['maxLength'] = length 
                        
                        # Format Detection
                        if "@" in data_node and "." in data_node: # Simple email check
                            schema_node['format'] = 'email'
                        # UUID check? 
                        import re
                        if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', data_node):
                            schema_node['format'] = 'uuid'

                 enrich_schema(schema, api_data.get('body'))

                 return [{
                     "source": "contract",
                     "property": "schema",
                     "operator": "json_schema",
                     "target": json.dumps(schema) # Send as stringified JSON for safe transport
                 }]
             except ImportError:
                 return [{ "source": "error", "property": "backend", "operator": "equals", "target": "genson_missing" }]
             except Exception as e:
                 print(f"Schema Gen Error: {e}")
                 return [{ "source": "error", "property": "backend", "operator": "equals", "target": str(e) }]

        settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == user_id).first()
        
        # Simulation Mode (Fallback if no key or specific provider)
        if not settings or not settings.ai_api_key or settings.ai_provider == 'simulator':
             # Simulate generic checks
             sim = []
             
             # Status
             st = api_data.get('status')
             if st:
                 sim.append({ "source": "status", "property": "", "operator": "equals", "target": st })
                 
             # Content-Type
             headers = api_data.get('headers', {})
             # Simple case-insensitive lookup
             ct = next((v for k,v in headers.items() if k.lower() == 'content-type'), '')
             if 'json' in ct.lower():
                 sim.append({ "source": "header", "property": "Content-Type", "operator": "contains", "target": "application/json" })

             # Performance
             sim.append({ "source": "responseTime", "property": "", "operator": "lessThan", "target": 2000 })
             
             # Body checks (simplified)
             body = api_data.get('body')
             if isinstance(body, dict):
                 if 'id' in body:
                     sim.append({ "source": "body", "property": "id", "operator": "exists", "target": None })
                 if 'success' in body:
                     sim.append({ "source": "body", "property": "success", "operator": "equals", "target": body['success'] })
             elif isinstance(body, list) and len(body) > 0:
                 sim.append({ "source": "body", "property": "0", "operator": "exists", "target": None })
            
             return sim



        # Construct Prompt
        prompt = f"""
        Analyze this API Response and suggest a list of assertions to validate it.
        
        Status: {api_data.get('status')}
        Headers: {json.dumps(api_data.get('headers', {}), indent=2)}
        Body (Truncated): {json.dumps(api_data.get('body', {}), indent=2)[:3000]}
        
        Return a JSON list of objects with the following schema:
        {{ "source": "status|header|body", "property": "field_path", "operator": "equals|contains|exists|>", "target": "expected_value" }}
        
        Example:
        [
          {{ "source": "status", "property": "", "operator": "equals", "target": 200 }},
          {{ "source": "header", "property": "content-type", "operator": "contains", "target": "application/json" }},
          {{ "source": "body", "property": "data.id", "operator": "exists", "target": null }}
        ]
        
        Return ONLY valid JSON.
        """

        # We reuse the logic from analyze_with_ai, but for brevity (and separate concerns), 
        # let's abstract the "Call LLM" part or copy it for now to ensure autonomy.
        # Refactoring to a private _call_llm would be better, but avoiding massive diffs.
        
        try:
             # QUICK IMPLEMENTATION: Re-using the logic pattern
             # For production, we should extract `_call_llm(settings, prompt, json_mode=True)`
             
            if settings.ai_provider == "openai":
                headers = { "Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json" }
                data = {
                    "model": settings.ai_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2
                }
                base_url = settings.ai_base_url if settings.ai_base_url else "https://api.openai.com/v1"
                if not base_url.endswith("/"): base_url += "/"
                url = f"{base_url}chat/completions" if "chat/completions" not in base_url else base_url

                resp = requests.post(url, headers=headers, json=data, timeout=30)
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"]
                    content = content.replace("```json", "").replace("```", "").strip()
                    return json.loads(content)
            
            elif settings.ai_provider == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.ai_model}:generateContent?key={settings.ai_api_key}"
                data = { "contents": [{ "parts": [{ "text": prompt }] }] }
                resp = requests.post(url, json=data, timeout=30)
                if resp.status_code == 200:
                    text = resp.json().get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                    text = text.replace("```json", "").replace("```", "").strip()
                    return json.loads(text)

            # ... supports others similarly ...
            
        except Exception as e:
            print(f"AI Assertion Gen Error: {e}")
            return []
            
        return []

    @staticmethod
    def analyze_flow_redundancy(db: Session, flow_id: int, user_id: int = None):
        """
        Identifies duplicate API calls within a specific Flow.
        Returns a list of suggestions.
        """
        ai_suggestions = []
        if user_id:
            try:
                ai_suggestions = AnalysisService.analyze_with_ai(db, flow_id, user_id)
            except Exception as e:
                print(f"AI Analysis failed: {e}")

        cards = db.query(FlowCardDataDB).filter(FlowCardDataDB.flow_id == flow_id).all()
        
        call_signatures = {}
        suggestions = []

        for card in cards:
            if not card.api_calls:
                continue
            
            # flow_card_data.api_calls is a dict or list. We need to handle both.
            api_data_raw = card.api_calls
            
            # Debug Log
            print(f"DEBUG Analysis: Card {card.name} (ID: {card.node_id}) Raw Data: {type(api_data_raw)}")

            if not api_data_raw:
                continue

            # Parse if string
            if isinstance(api_data_raw, str):
                try:
                    api_data_raw = json.loads(api_data_raw)
                except:
                    continue
            
            # Normalize to list of calls
            calls = []
            if isinstance(api_data_raw, list):
                calls = api_data_raw
            elif isinstance(api_data_raw, dict):
                # Check if it's a single call structure or a dict of calls
                if "url" in api_data_raw and "method" in api_data_raw:
                    calls = [api_data_raw]
                else:
                    calls = list(api_data_raw.values())

            for api_data in calls:
                method = api_data.get("method", "").upper()
                url = api_data.get("url", "")
                
                if not method or not url:
                    continue

                # Create a signature for deduplication
                signature = f"{method}:{url}"
                
                if signature not in call_signatures:
                    call_signatures[signature] = []
                
                call_signatures[signature].append({
                    "node_name": card.name,
                    "node_id": card.node_id
                })

        # Generate Algorithmic Suggestions
        for signature, usage in call_signatures.items():
            if len(usage) > 1:
                names = [u["node_name"] for u in usage]
                suggestions.append({
                    "type": "redundancy",
                    "severity": "warning",
                    "message": f"Duplicate request detected: {signature}",
                    "details": f"This request appears in {len(usage)} nodes: {', '.join(names)}. Consider extracting to a variable or deduplicating.",
                    "nodes": usage
                })
        
        # Merge with AI Suggestions
        if 'ai_suggestions' in locals() and ai_suggestions:
            suggestions.extend(ai_suggestions)

        return suggestions

    @staticmethod
    def analyze_performance_trends(db: Session, project_id: int = None, limit: int = 50):
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
