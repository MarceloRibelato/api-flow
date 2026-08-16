import logging
import difflib
import re

logger = logging.getLogger(__name__)

class HeuristicHealerService:
    @staticmethod
    async def attempt_heal(page, broken_selector: str, action_val: str, step_type: str) -> tuple[str | None, list]:
        """
        Tries to automatically heal a selector using local heuristics (Levenshtein distance, exact tag matching).
        Returns a tuple of (best_selector_match, list_of_all_candidates_found).
        """
        if not page or not broken_selector:
            return None, []
            
        logger.info(f"🔍 [Heuristic-Heal] Analyzing broken selector '{broken_selector}' for step type '{step_type}'")
        
        try:
            # 1. Fetch potential candidates from the DOM
            js_script = """(stepType) => {
                const candidates = [];
                
                // User Request 3: Support Shadow DOM traversing
                const allNodes = [];
                function collectNodes(root) {
                    const nodes = root.querySelectorAll('*');
                    for (let node of nodes) {
                        allNodes.push(node);
                        if (node.shadowRoot) {
                            collectNodes(node.shadowRoot);
                        }
                    }
                }
                collectNodes(document);
                
                for (let el of allNodes) {
                    // User Request 1: Invisible elements (Visibility check)
                    const isVisible = el.offsetWidth > 0 && el.offsetHeight > 0;
                    if (!isVisible) continue;
                    
                    const tag = el.tagName.toLowerCase();
                    if (['script', 'style', 'meta', 'link', 'noscript', 'svg', 'path', 'iframe'].includes(tag)) continue;
                    
                    const role = el.getAttribute('role') || '';
                    const type = el.getAttribute('type') || '';
                    
                    let isMatch = false;
                    
                    // User Request: Filter by element type based on step_type!
                    if (stepType === 'click') {
                        if (['button', 'a', 'input', 'label', 'select', 'textarea'].includes(tag) || ['button', 'link', 'radio', 'checkbox', 'switch'].includes(role) || el.onclick || el.getAttribute('ng-click') || el.getAttribute('@click')) {
                            isMatch = true;
                        }
                    } else if (stepType === 'type') {
                        if (['input', 'textarea', 'select'].includes(tag) || role === 'textbox' || el.isContentEditable) {
                            if (tag === 'input' && ['hidden', 'submit', 'button', 'image'].includes(type)) {
                                isMatch = false; // Not a typical text input
                            } else {
                                isMatch = true;
                            }
                        }
                    } else {
                        // For other types (hover, wait), collect mostly everything interactive
                        isMatch = true; 
                    }
                    
                    // Add generic interactive elements just in case it's a weird SPA div
                    if (!isMatch && el.tabIndex >= 0) {
                         isMatch = true;
                    }
                    
                    if (isMatch) {
                        // Perform expensive style checks ONLY if the element is a candidate
                        if (stepType === 'click') {
                            const style = window.getComputedStyle(el);
                            if (style.pointerEvents === 'none' || style.opacity === '0' || style.visibility === 'hidden') {
                                continue;
                            }
                        }
                        candidates.push({
                            tag: tag,
                            id: el.id || '',
                            className: typeof el.className === 'string' ? el.className : '',
                            name: el.getAttribute('name') || '',
                            placeholder: el.getAttribute('placeholder') || '',
                            text: el.innerText ? el.innerText.trim().substring(0, 50) : ''
                        });
                    }
                }
                return candidates;
            }"""
            
            candidates = await page.evaluate(js_script, step_type)
            if not candidates:
                logger.info("🔍 [Heuristic-Heal] No valid candidates found in DOM.")
                return None, []
                
            # User Request 2: Regex parsing of broken selector
            expected_ids = re.findall(r'#([a-zA-Z0-9_-]+)', broken_selector)
            expected_classes = re.findall(r'\.([a-zA-Z0-9_:\/-]+)', broken_selector)
            expected_names = re.findall(r'\[name=["\']?([a-zA-Z0-9_-]+)["\']?\]', broken_selector)
            
            # If no ID/Class/Name prefix is found, treat the whole raw string as potential id/class/name
            is_raw = not any(p in broken_selector for p in ['#', '.', '[', '>'])
            if is_raw:
                expected_ids.append(broken_selector)
                expected_classes.append(broken_selector)
                expected_names.append(broken_selector)
            
            # Gather all potential candidates
            scored_candidates = []
            
            for c in candidates:
                # Check ID similarity
                if c['id']:
                    for expected_id in expected_ids:
                        sim = difflib.SequenceMatcher(None, expected_id, c['id']).ratio()
                        if sim >= 0.60:
                            scored_candidates.append((sim, f"#{c['id']}"))
                            
                # Check Class similarity
                if c['className']:
                    for expected_class in expected_classes:
                        for cls in c['className'].split():
                            sim = difflib.SequenceMatcher(None, expected_class, cls).ratio()
                            if sim >= 0.60:
                                sel = f"{c['tag']}.{cls}" if sim > 0.9 else f".{cls}"
                                scored_candidates.append((sim, sel))
                                
                # Check Name similarity
                if c['name']:
                    for expected_name in expected_names:
                        sim = difflib.SequenceMatcher(None, expected_name, c['name']).ratio()
                        if sim >= 0.60:
                            scored_candidates.append((sim, f"{c['tag']}[name='{c['name']}']"))
                            
                # Text fallback for clicks
                if step_type == 'click':
                    expected_text = action_val
                    if not expected_text:
                        m = re.search(r'text=["\']([^"\']+)["\']', broken_selector)
                        if not m:
                            m = re.search(r'has-text\((["\']?)([^"\']+)\1\)', broken_selector)
                        if m:
                            expected_text = m.group(1) if len(m.groups()) == 1 else m.group(2)
                            
                    if expected_text and c['text']:
                        if expected_text.lower() == c['text'].lower():
                            safe_text = expected_text.replace('"', '\\"')
                            scored_candidates.append((0.99, f"{c['tag']}:text-is(\"{safe_text}\")"))
                        elif expected_text.lower() in c['text'].lower() and len(expected_text) > 2:
                            safe_text = expected_text.replace('"', '\\"')
                            scored_candidates.append((0.95, f"{c['tag']}:has-text(\"{safe_text}\")"))
                        else:
                            sim = difflib.SequenceMatcher(None, expected_text.lower(), c['text'].lower()).ratio()
                            if sim >= 0.75:
                                safe_text = c['text'].replace('"', '\\"')
                                scored_candidates.append((sim, f"{c['tag']}:has-text(\"{safe_text}\")"))
                            
                # Text fallback for types (placeholder)
                if step_type == 'type' and c['placeholder']:
                    expected_pls = re.findall(r'\[placeholder=["\']?([^"\']+)["\']?\]', broken_selector)
                    for expected_pl in expected_pls:
                        sim = difflib.SequenceMatcher(None, expected_pl, c['placeholder']).ratio()
                        if sim >= 0.60:
                            scored_candidates.append((sim, f"{c['tag']}[placeholder='{c['placeholder']}']"))

            # Sort by score descending
            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            
            # User Request 3: Deduplicate to avoid multiple Playwright calls for the same selector
            seen_sels = set()
            dedup_candidates = []
            for score, sel in scored_candidates:
                if sel not in seen_sels:
                    seen_sels.add(sel)
                    dedup_candidates.append((score, sel))
            
            # Test each candidate for uniqueness (limit to top 10 to guarantee fast performance)
            for score, sel in dedup_candidates[:10]:
                try:
                    visible_sel = f"{sel} >> visible=true"
                    count = await page.locator(visible_sel).count()
                    if count == 1:
                        logger.info(f"✨ [Heuristic-Heal] Found unique match '{visible_sel}' with {(score*100):.1f}% confidence!")
                        return visible_sel, candidates
                    else:
                        logger.warning(f"⚠️ [Heuristic-Heal] Candidate '{visible_sel}' is not unique (count={count}). Trying next...")
                except Exception as loc_err:
                    logger.warning(f"⚠️ [Heuristic-Heal] Playwright rejected selector '{sel}': {loc_err}")
                    continue
                    
            logger.warning("⚠️ [Heuristic-Heal] All candidates failed uniqueness check or no candidates found. Falling back to AI.")
                    
        except Exception as e:
            logger.error(f"❌ [Heuristic-Heal] Exception: {e}")
            
        return None, candidates if 'candidates' in locals() else []
