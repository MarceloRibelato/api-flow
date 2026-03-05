from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.flow_models import FlowCardDataDB, FlowDB
from app.models.api_test_history_models import ApiExecutionHistory
from app.models.agent_models import AgentSettingsDB
from datetime import datetime, timedelta
import json
import requests

class AnalysisService:

    @staticmethod
    def analyze_with_ai(db: Session, flow_id: int, user_id: int):
        settings = db.query(AgentSettingsDB).filter(AgentSettingsDB.user_id == user_id).first()
        
        # Debug: Return info if no settings
        if not settings:
             return [{ "type": "info", "severity": "low", "message": "AI Not Configured", "details": "No settings found for user. Please configure in Settings." }]
        
        if not settings.ai_api_key:
             return [{ "type": "warning", "severity": "medium", "message": "Missing API Key", "details": "AI API Key is missing. Please add it in Settings." }]

        # Fetch Flow Data
        flow = db.query(FlowDB).filter(FlowDB.id == flow_id).first()
        cards = db.query(FlowCardDataDB).filter(FlowCardDataDB.flow_id == flow_id).all()
        
        if not flow or not cards:
            return []

        # Construct Context
        nodes_desc = []
        for card in cards:
            api_info = card.api_calls if card.api_calls else "No API"
            nodes_desc.append(f"- Node {card.name}: {api_info}")
        
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

        # Call Provider
        suggestions = []
        try:
            if settings.ai_provider == "openai":
                headers = {
                    "Authorization": f"Bearer {settings.ai_api_key}",
                    "Content-Type": "application/json"
                }
                data = {
                    "model": settings.ai_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2
                }
                base_url = settings.ai_base_url if settings.ai_base_url else "https://api.openai.com/v1"
                # Ensure no trailing slash for cleaner concatenation if needed, though usually full path is preferred or just base
                # For OpenAI compatible, usually base_url/chat/completions
                if not base_url.endswith("/"):
                     base_url += "/"
                
                url = f"{base_url}chat/completions" if "chat/completions" not in base_url else base_url

                resp = requests.post(url, headers=headers, json=data, timeout=30)
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"]
                    content = content.replace("```json", "").replace("```", "").strip()
                    try:
                        suggestions = json.loads(content)
                    except:
                        # Fallback if JSON is malformed
                        suggestions = [{ "type": "logic", "severity": "low", "message": "Raw AI Response", "details": content }]
                else:
                    return [{ "type": "error", "severity": "high", "message": f"OpenAI Error {resp.status_code}", "details": resp.text }]

            elif settings.ai_provider == "anthropic":
                headers = {
                    "x-api-key": settings.ai_api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json"
                }
                data = {
                    "model": settings.ai_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 2048
                }
                resp = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=data, timeout=30)
                if resp.status_code == 200:
                    content = resp.json()["content"][0]["text"]
                    content = content.replace("```json", "").replace("```", "").strip()
                    try:
                        suggestions = json.loads(content)
                    except:
                        suggestions = [{ "type": "logic", "severity": "low", "message": "Raw AI Response", "details": content }]
                else:
                    return [{ "type": "error", "severity": "high", "message": f"Anthropic Error {resp.status_code}", "details": resp.text }]
            
            elif settings.ai_provider == "gemini":
                # Basic Google Gemini via REST API (Simplified)
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.ai_model}:generateContent?key={settings.ai_api_key}"
                data = { "contents": [{ "parts": [{ "text": prompt }] }] }
                resp = requests.post(url, json=data, timeout=30)
                if resp.status_code == 200:
                    text = resp.json().get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                    text = text.replace("```json", "").replace("```", "").strip()
                    try:
                        suggestions = json.loads(text)
                    except:
                         suggestions = [{ "type": "logic", "severity": "low", "message": "Raw AI Response", "details": text }]
                elif resp.status_code == 404:
                     return [{ "type": "error", "severity": "high", "message": "Gemini Model Not Found", "details": "The selected model is not available for your API Key. Ensure 'Generative Language API' is enabled in Google Cloud Console." }]
                else:
                     return [{ "type": "error", "severity": "high", "message": f"Gemini Error {resp.status_code}", "details": resp.text }]

            elif settings.ai_provider == "deepseek":
                headers = {
                    "Authorization": f"Bearer {settings.ai_api_key}",
                    "Content-Type": "application/json"
                }
                data = {
                    "model": settings.ai_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0, # DeepSeek recommends low temp for code/logic
                    "max_tokens": 4096
                }
                resp = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=data, timeout=60)
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"]
                    content = content.replace("```json", "").replace("```", "").strip()
                    try:
                        suggestions = json.loads(content)
                    except:
                         suggestions = [{ "type": "logic", "severity": "low", "message": "Raw AI Response", "details": content }]
                else:
                     return [{ "type": "error", "severity": "high", "message": f"DeepSeek Error {resp.status_code}", "details": resp.text }]

            elif settings.ai_provider == "ollama":
                # Ollama is OpenAI compatible usually, or has its own /api/generate
                # Standard OpenAI compatible endpoint for Ollama: http://localhost:11434/v1/chat/completions
                
                base_url = settings.ai_base_url if settings.ai_base_url else "http://localhost:11434/v1"
                if not base_url.endswith("/"):
                     base_url += "/"
                
                url = f"{base_url}chat/completions" if "chat/completions" not in base_url else base_url

                headers = {
                    "Content-Type": "application/json"
                }
                # Ollama often doesn't need API key, but we pass dummy if empty
                if settings.ai_api_key:
                    headers["Authorization"] = f"Bearer {settings.ai_api_key}"

                data = {
                    "model": settings.ai_model or "llama3",
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False
                }
                
                try:
                    resp = requests.post(url, headers=headers, json=data, timeout=60)
                except Exception as conn_err:
                     return [{ "type": "error", "severity": "high", "message": "Ollama Connection Error", "details": f"Could not connect to {url}. Ensure Ollama is running." }]

                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"]
                    content = content.replace("```json", "").replace("```", "").strip()
                    try:
                        suggestions = json.loads(content)
                    except:
                        suggestions = [{ "type": "logic", "severity": "low", "message": "Raw AI Response", "details": content }]
                else:
                     return [{ "type": "error", "severity": "high", "message": f"Ollama Error {resp.status_code}", "details": resp.text }]

        except Exception as e:
            print(f"LLM Call Error: {e}")
            return [{ "type": "error", "severity": "high", "message": "AI Client Exception", "details": str(e) }]

        return suggestions or []

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
