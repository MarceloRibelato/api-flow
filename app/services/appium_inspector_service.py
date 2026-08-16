import logging
import json
import xml.etree.ElementTree as ET
import re
from typing import Optional, Dict, Any, List
import time
import redis

from app.services.appium_executor_service import AppiumExecutorService
from app.config import settings

logger = logging.getLogger(__name__)

# Configuração do Redis para estado compartilhado usando settings
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

# Em memória (Cache L1) local do Worker atual
_active_sessions: Dict[str, AppiumExecutorService] = {}
_session_progress: Dict[str, Dict[str, Any]] = {}

class AppiumInspectorService:
    """
    A persistent session manager that borrows the AppiumExecutorService
    strictly for an interactive visual inspection UI (taking screenshots and parsing DOM).
    """

    def __init__(self):
        pass

    @classmethod
    def start_session(cls, session_id: str, db, product_id: int, steps: List[Dict[str, Any]] = None) -> dict:
        """Starts a persistent driver session if one does not exist."""
        # 1. Verifica cache L1 (Memória local)
        if session_id in _active_sessions:
            logger.info(f"🔍 [Inspector] Session {session_id} is already active locally.")
            return cls.get_snapshot(session_id)
            
        # 2. Verifica cache L2 (Redis) para ver se outro worker já iniciou
        try:
            metadata = redis_client.get(f"appium_session:{session_id}")
            if metadata:
                logger.info(f"🔍 [Inspector] Session {session_id} exists in Redis. Attaching proxy logic here.")
                # TODO: Implement webdriver.Remote override to attach to existing Appium Session ID
                # For now, we will fallback to restarting or using the local active session if available
        except Exception as e:
            logger.warning(f"🔍 [Inspector] Redis fetch failed: {e}")

        logger.info(f"🔍 [Inspector] Starting new session {session_id} for product {product_id}...")
        executor = AppiumExecutorService()
        try:
            executor.start_sync(db=db, product_id=product_id)
            _active_sessions[session_id] = executor
            
            # Salva metadados no Redis
            if executor._driver:
                try:
                    meta = {
                        "appium_session_id": executor._driver.session_id,
                        "server_url": executor._settings.get("server_url", "http://localhost:4723"),
                        "product_id": product_id
                    }
                    redis_client.set(f"appium_session:{session_id}", json.dumps(meta), ex=3600)
                except Exception as e:
                    logger.warning(f"🔍 [Inspector] Failed to save session to Redis: {e}")
            
            # If we have steps, try to replay them
            failed_step_index = None
            failed_step_error = None
            
            if steps:
                total_steps = len(steps)
                for idx, step in enumerate(steps):
                    step_name = step.get("name") or step.get("type") or f"Passo {idx + 1}"
                    node_name = step.get("_sourceNodeName") or step.get("nodeName") or "Nó Atual"
                    _session_progress[session_id] = {
                        "current_index": idx,
                        "total_steps": total_steps,
                        "step_name": step_name,
                        "node_name": node_name,
                        "status": "running"
                    }
                    try:
                        logger.info(f"🔍 [Inspector] Replaying step {idx + 1}/{total_steps} ({node_name} - {step_name}): {step}")
                        result = executor.execute_step_sync(step, db=db)
                        if result.get("status", 500) >= 400:
                            raise Exception(result.get("text", "Unknown Appium Execution Error"))
                        
                        if result.get("warning"):
                            logger.warning(f"⚠️ [Inspector] Step {idx + 1} Warning: {result.get('warning')}")
                            _session_progress[session_id]["warning"] = result.get("warning")
                        else:
                            _session_progress[session_id]["warning"] = None

                        if step.get("type") == "wait":
                            logger.info(f"⏸️ [Inspector] Pause step reached at index {idx} ({step_name}). Halting replay for interactive debug.")
                            _session_progress[session_id] = {
                                "current_index": idx,
                                "total_steps": total_steps,
                                "step_name": step_name,
                                "node_name": node_name,
                                "status": "paused"
                            }
                            snapshot = cls.get_snapshot(session_id)
                            snapshot["paused_step_index"] = idx
                            snapshot["is_paused"] = True
                            return snapshot

                        time.sleep(1.0)
                    except Exception as e:
                        logger.error(f"🔍 [Inspector] Replay failed at step {idx}: {e}")
                        failed_step_index = idx
                        failed_step_error = str(e)
                        _session_progress[session_id] = {
                            "current_index": idx,
                            "total_steps": total_steps,
                            "step_name": step_name,
                            "node_name": node_name,
                            "error": str(e),
                            "status": "failed"
                        }
                        break
            
            _session_progress[session_id] = {
                "current_index": len(steps) if steps else 0,
                "total_steps": len(steps) if steps else 0,
                "status": "completed" if failed_step_index is None else "failed"
            }
                        
            snapshot = cls.get_snapshot(session_id)
            if failed_step_index is not None:
                snapshot["failed_step_index"] = failed_step_index
                snapshot["failed_step_error"] = failed_step_error
            
            return snapshot
        except Exception as e:
            logger.error(f"🔍 [Inspector] Failed to start session: {e}")
            if session_id in _session_progress:
                _session_progress[session_id]["status"] = "failed"
            # Ensure we clean up if start fails
            if executor._driver:
                executor.close_sync()
            raise e

    @classmethod
    def get_progress(cls, session_id: str) -> dict:
        return _session_progress.get(session_id, {"status": "idle", "current_index": 0, "total_steps": 0})

    @classmethod
    def stop_session(cls, session_id: str):
        if session_id in _session_progress:
            del _session_progress[session_id]
        if session_id in _active_sessions:
            logger.info(f"🔍 [Inspector] Stopping session {session_id}.")
            try:
                _active_sessions[session_id].close_sync()
            except Exception as e:
                logger.warning(f"🔍 [Inspector] Error while closing session: {e}")
            del _active_sessions[session_id]
            
        # Limpa do Redis
        try:
            redis_client.delete(f"appium_session:{session_id}")
        except Exception: pass

    @classmethod
    def get_snapshot(cls, session_id: str) -> dict:
        """Takes a base64 screenshot and retrieves current DOM tree parsed into JSON."""
        executor = _active_sessions.get(session_id)
        if not executor or not executor._driver:
            raise ValueError(f"Session {session_id} not found or driver disposed.")
        
        driver = executor._driver
        try:
            b64_image = driver.get_screenshot_as_base64()
            xml_source = driver.page_source
            
            # Parse XML into an easily consumable JSON hierarchical list
            parsed_tree = cls._parse_page_source(xml_source)
            window_size = driver.get_window_size()
            
            return {
                "image_b64": b64_image,
                "tree": parsed_tree,
                "window": {
                    "width": window_size["width"],
                    "height": window_size["height"]
                }
            }
        except Exception as e:
            logger.error(f"🔍 [Inspector] Error getting snapshot: {e}")
            raise e

    @classmethod
    def interact(cls, session_id: str, action: dict, db=None) -> dict:
        """
        Executes a direct Appium action and returns the new snapshot.
        action struct: { "type": "tap", "properties": {"selector": "..."} }
        """
        executor = _active_sessions.get(session_id)
        if not executor or not executor._driver:
            raise ValueError(f"Session {session_id} not found or driver disposed.")

        try:
            logger.info(f"🔍 [Inspector] Interactive Execution: {action}")
            # Borrow execute_step_sync from executor
            result = executor.execute_step_sync(action, db=db)
            if result.get("status", 500) >= 400:
                raise Exception(result.get("text", "Unknown Appium Execution Error"))
            
            # Sleep briefly to let animations settle
            time.sleep(1.0)
            
            return cls.get_snapshot(session_id)
        except Exception as e:
            logger.error(f"🔍 [Inspector] Interaction failed: {e}")
            raise e

    @classmethod
    def test_selector(cls, session_id: str, selector: str) -> dict:
        """
        Tests a selector against the current screen and returns the number of matches.
        Returns the bounds of the first element if found.
        """
        executor = _active_sessions.get(session_id)
        if not executor:
            logger.warning(f"🔍 [Inspector] Session {session_id} not found in active list.")
            raise ValueError(f"Session {session_id} not found or driver disposed.")
        
        if not executor._driver:
            logger.warning(f"🔍 [Inspector] Driver for session {session_id} is null.")
            raise ValueError(f"Driver for session {session_id} is null.")
        
        try:
            logger.info(f"🔍 [Inspector] Testing selector: {selector} (Session: {session_id})")
            els = executor._find_elements(selector, timeout_ms=3000)
            
            if not els:
                logger.info(f"🔍 [Inspector] Selector '{selector}' NOT FOUND.")
                return {"found": False, "count": 0, "message": "Element not found"}
            
            logger.info(f"🔍 [Inspector] Selector '{selector}' FOUND {len(els)} matches.")
            first_el = els[0]
            loc = first_el.location
            size = first_el.size
            
            return {
                "found": True,
                "count": len(els),
                "bounds": {
                    "x": loc['x'],
                    "y": loc['y'],
                    "width": size['width'],
                    "height": size['height']
                }
            }
        except Exception as e:
            logger.error(f"🔍 [Inspector] Selector test failed: {e}", exc_info=True)
            return {"found": False, "count": 0, "message": str(e)}

    @classmethod
    def _parse_page_source(cls, xml_str: str) -> List[dict]:
        """
        Converts Appium XML page source into a flat list of node dictionaries with bounding boxes.
        Only keeps nodes with valid bounds.
        """
        result_nodes = []
        try:
            root = ET.fromstring(xml_str)
            
            # recursive traversal
            def traverse(element):
                bounds_str = element.attrib.get("bounds", "")
                
                # Match bounds format: [0,0][1440,2960]
                match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
                if match:
                    x1, y1, x2, y2 = map(int, match.groups())
                    width = x2 - x1
                    height = y2 - y1
                    
                    if width > 0 and height > 0:
                        node_data = {
                            "class": element.attrib.get("class", ""),
                            "text": element.attrib.get("text", ""),
                            "resource_id": element.attrib.get("resource-id", ""),
                            "content_desc": element.attrib.get("content-desc", ""),
                            "hint": element.attrib.get("hint", ""),
                            # Universal Support (iOS / Hybrid)
                            "name": element.attrib.get("name", ""),
                            "label": element.attrib.get("label", ""),
                            "value": element.attrib.get("value", ""),
                            "placeholder": element.attrib.get("placeholder", ""),
                            # Properties
                            "checkable": element.attrib.get("checkable", "false") == "true",
                            "checked": element.attrib.get("checked", "false") == "true",
                            "clickable": element.attrib.get("clickable", "false") == "true",
                            "focusable": element.attrib.get("focusable", "false") == "true",
                            "focused": element.attrib.get("focused", "false") == "true",
                            "scrollable": element.attrib.get("scrollable", "false") == "true",
                            "long_clickable": element.attrib.get("long-clickable", "false") == "true",
                            "enabled": element.attrib.get("enabled", "true") == "true",
                            "index": element.attrib.get("index", "0"),
                            "bounds": {
                                "x": x1,
                                "y": y1,
                                "width": width,
                                "height": height
                            }
                        }
                        result_nodes.append(node_data)
                
                for child in element:
                    traverse(child)

            traverse(root)
            
            # Group 3: Melhoria de Precisão de Clique (Painter's Algorithm)
            # Ordenamos por área DECRESCENTE para que os elementos maiores fiquem embaixo (primeiros no array)
            # e os menores (mais específicos) fiquem em cima (últimos no array), recebendo o clique primeiro.
            result_nodes.sort(key=lambda n: n["bounds"]["width"] * n["bounds"]["height"], reverse=True)
            
            return result_nodes
        except Exception as e:
            logger.error(f"🔍 [Inspector] XML Parse failed: {e}")
            return []

    @classmethod
    def generate_selectors_for_element(cls, session_id: str, lws_id: str = None, text: str = None) -> dict:
        """
        Mock AI logic for mobile selector generation using basic attributes.
        In a real scenario, this would use LLM like WebInspectorService does.
        """
        snapshot = cls.get_snapshot(session_id)
        tree = snapshot.get("tree", [])
        
        target_node = None
        
        # Approximate matching to find the node
        if lws_id:
            target_node = next((n for n in tree if str(id(n)) == str(lws_id) or (n.get("bounds") and f"{n['bounds']['x']}_{n['bounds']['y']}" == lws_id)), None)
            
        if not target_node and text:
            candidates = [n for n in tree if text.lower() in (n.get("text") or "").lower() or text.lower() in (n.get("content_desc") or "").lower()]
            if candidates:
                target_node = candidates[0]
                
        if not target_node:
            return {"success": False, "error": "Element not found on current screen."}
            
        selectors = []
        text_val = (target_node.get("text") or "").strip()
        desc_val = (target_node.get("content_desc") or "").strip()
        res_val = (target_node.get("resource_id") or "").strip()
        name_val = (target_node.get("name") or "").strip()
        label_val = (target_node.get("label") or "").strip()

        # Sanitize accessibility tab index suffixes (e.g. "Guia 1 de 2", "Tab 2 of 4") universally
        import re
        desc_no_tab = re.sub(r'Guia \d+ de \d+|Tab \d+ of \d+', '', desc_val, flags=re.IGNORECASE).strip() if desc_val else ""
        text_no_tab = re.sub(r'Guia \d+ de \d+|Tab \d+ of \d+', '', text_val, flags=re.IGNORECASE).strip() if text_val else ""

        # Sanitize multiline strings
        desc_first_line = " ".join(desc_no_tab.splitlines()[0].split()) if desc_no_tab else ""
        desc_clean = " ".join(desc_no_tab.split()) if desc_no_tab else ""
        text_first_line = " ".join(text_no_tab.splitlines()[0].split()) if text_no_tab else ""
        text_clean = " ".join(text_no_tab.split()) if text_no_tab else ""
        
        # Dynamic Action Line Extraction for merged React Native / Flutter container nodes (Universal for ANY app)
        desc_lines = [l.strip() for l in desc_val.splitlines() if l.strip()] if desc_val else []
        text_lines = [l.strip() for l in text_val.splitlines() if l.strip()] if text_val else []

        full_clean = f"{desc_clean} {text_clean}".strip()

        # Sub-token & Action Keyword Extraction for Compound Accessibility Strings
        # Handles React Native / Flutter compound modal nodes like "Bomba teste A...btn_confirm_pumpConfirmar bomba" or "Código do motorista inválido!... Ok, entendi"
        action_match = re.search(r'(btn_[a-zA-Z0-9_]+|confirm[a-zA-Z]*|salvar|continuar|avançar|ok[,\s]*entendi|ok|entendi|fechar|cancelar|sim|não|voltar|abastecer|bomba[^\n]*)', full_clean, flags=re.IGNORECASE)
        if action_match:
            act_kw = action_match.group(1).strip()
            if len(act_kw) >= 2:
                selectors.append({"value": f"//*[contains(translate(@text, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{act_kw.lower()}')]", "strategy": "xpath", "count": 1})
                selectors.append({"value": f"//*[contains(translate(@content-desc, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{act_kw.lower()}')]", "strategy": "xpath", "count": 1})

        # Multiline strings in modals/cards: LAST LINE is almost always the action button (e.g. "Ok, entendi")
        if len(text_lines) > 1:
            last_text = " ".join(text_lines[-1].split())
            if last_text and len(last_text) < 40:
                selectors.append({"value": f"//*[contains(@text, '{last_text}')]", "strategy": "xpath", "count": 1})
        elif text_clean and len(text_clean.split()) > 3:
            words = text_clean.split()
            last_words = " ".join(words[-2:])
            if len(last_words) < 30:
                selectors.append({"value": f"//*[contains(@text, '{last_words}')]", "strategy": "xpath", "count": 1})

        if len(desc_lines) > 1:
            last_desc = " ".join(desc_lines[-1].split())
            if last_desc and len(last_desc) < 40:
                selectors.append({"value": f"//*[contains(@content-desc, '{last_desc}')]", "strategy": "xpath", "count": 1})
                selectors.append({"value": f"accessibility_id={last_desc}", "strategy": "accessibility_id", "count": 1})
        elif desc_clean and len(desc_clean.split()) > 3:
            words = desc_clean.split()
            last_words = " ".join(words[-2:])
            if len(last_words) < 30:
                selectors.append({"value": f"//*[contains(@content-desc, '{last_words}')]", "strategy": "xpath", "count": 1})
                selectors.append({"value": f"accessibility_id={last_words}", "strategy": "accessibility_id", "count": 1})

        # 1. Native Strategies (High Performance)
        if res_val:
            selectors.append({"value": f"id={res_val}", "strategy": "id", "count": 1})
        if desc_clean and len(desc_clean) < 40:
            selectors.append({"value": f"accessibility_id={desc_clean}", "strategy": "accessibility_id", "count": 1})
        elif desc_first_line and len(desc_first_line) < 40:
            selectors.append({"value": f"accessibility_id={desc_first_line}", "strategy": "accessibility_id", "count": 1})
            
        # 2. Universal & Contains XPath Fallback (Android + iOS)
        if desc_first_line and '\n' in desc_val:
            selectors.append({"value": f"//*[contains(@content-desc, '{desc_first_line}')]", "strategy": "xpath", "count": 1})
        if text_first_line and '\n' in text_val:
            selectors.append({"value": f"//*[contains(@text, '{text_first_line}')]", "strategy": "xpath", "count": 1})

        val_to_use = text_clean or desc_clean or res_val or name_val or label_val
        if val_to_use:
            uni_xpath = f"//*[@resource-id='{val_to_use}' or @name='{val_to_use}' or @label='{val_to_use}' or @text='{val_to_use}' or @content-desc='{val_to_use}']"
            selectors.append({"value": uni_xpath, "strategy": "xpath", "count": 1})
            
        if not selectors:
            selectors.append({"value": f"//{target_node.get('class', '*')}", "strategy": "xpath", "count": 1})
            
        return {"success": True, "selectors": selectors}

    @classmethod
    def ai_analyze_full_tree_and_correct(cls, session_id: str, failed_step: dict, db=None) -> dict:
        """
        Analyzes the full mobile DOM tree (xml_source and node list) using AI / heuristic matching
        and returns the best matching component and selector candidates.
        """
        executor = _active_sessions.get(session_id)
        if not executor or not executor._driver:
            raise ValueError(f"Session {session_id} not found or driver disposed.")

        try:
            snapshot = cls.get_snapshot(session_id)
            tree = snapshot.get("tree", [])
            
            step_type = failed_step.get("type", "tap")
            props = failed_step.get("properties", {})
            step_name = failed_step.get("name", "")
            old_selector = props.get("selector", "")
            expected_val = props.get("value", "")

            target_candidates = []
            search_tokens = [t.lower() for t in [step_name, expected_val, old_selector] if t]

            for node in tree:
                score = 0
                n_text = (node.get("text") or "").strip()
                n_desc = (node.get("content_desc") or "").strip()
                n_res = (node.get("resource_id") or "").strip()
                n_cls = (node.get("class") or "").strip()
                n_hint = (node.get("hint") or "").strip()

                # For 'type' step, prioritize editable inputs (EditText/TextField)
                if step_type == "type" and ("edittext" in n_cls.lower() or "textfield" in n_cls.lower() or node.get("focusable") is True):
                    score += 5

                # Compare token matches (including hint/placeholder)
                for tok in search_tokens:
                    if tok and (tok in n_text.lower() or tok in n_desc.lower() or tok in n_res.lower() or tok in n_hint.lower()):
                        score += 8
                    elif tok and (n_text.lower() in tok or n_desc.lower() in tok or n_hint.lower() in tok):
                        score += 4

                if score > 0:
                    target_candidates.append({"node": node, "score": score})

            target_candidates.sort(key=lambda c: c["score"], reverse=True)
            best = target_candidates[0]["node"] if target_candidates else (tree[0] if tree else None)

            if not best:
                return {"success": False, "error": "Unable to analyze DOM tree for correction."}

            # Generate resilient selectors for the best matching node
            res_val = (best.get("resource_id") or "").strip()
            desc_val = (best.get("content_desc") or "").strip()
            text_val = (best.get("text") or "").strip()
            hint_val = (best.get("hint") or "").strip()
            cls_val = best.get("class", "*")

            desc_first = " ".join(desc_val.splitlines()[0].split()) if desc_val else ""
            desc_clean = " ".join(desc_val.split()) if desc_val else ""
            text_first = " ".join(text_val.splitlines()[0].split()) if text_val else ""
            text_clean = " ".join(text_val.split()) if text_val else ""

            selectors = []
            if res_val:
                selectors.append({"value": f"id={res_val}", "strategy": "id", "count": 1})
                selectors.append({"value": f"//*[@resource-id='{res_val}']", "strategy": "xpath", "count": 1})
            if desc_first:
                selectors.append({"value": f"accessibility_id={desc_first}", "strategy": "accessibility_id", "count": 1})
                if '\n' in desc_val:
                    selectors.append({"value": f"//*[contains(@content-desc, '{desc_first}')]", "strategy": "xpath", "count": 1})
                else:
                    selectors.append({"value": f"//*[@content-desc='{desc_clean}']", "strategy": "xpath", "count": 1})
            if text_first:
                if '\n' in text_val:
                    selectors.append({"value": f"//*[contains(@text, '{text_first}')]", "strategy": "xpath", "count": 1})
                else:
                    selectors.append({"value": f"//*[@text='{text_clean}']", "strategy": "xpath", "count": 1})
            if hint_val and not res_val:
                selectors.append({"value": f"//*[@hint='{hint_val}']", "strategy": "xpath", "count": 1})

            # Generic class fallback — only used when no specific selector could be generated
            if not selectors:
                selectors.append({"value": f"//{cls_val}", "strategy": "xpath", "count": 1})

            best_selector = selectors[0]["value"] if selectors else f"//{cls_val}"

            return {
                "success": True,
                "suggested_selector": best_selector,
                "selectors": selectors,
                "matched_node": best
            }
        except Exception as e:
            logger.error(f"AI Full Tree Analysis failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
