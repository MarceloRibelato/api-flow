import logging
import difflib
import re
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class MobileHealingEngine:
    """
    Motor de auto-recuperação (Self-Healing) para seletores mobile quebrados ou alterados.
    Analisa a árvore XML da tela (page_source) e correlaciona nós candidatos
    com o seletor original e os metadados do passo (nome, descrição).
    """

    STOPWORDS = {
        'tap', 'click', 'type', 'assert', 'the', 'on', 'in',
        'button', 'input', 'field', 'em', 'no', 'na', 'para',
        'botao', 'btn', 'element', 'elemento'
    }

    @classmethod
    def heal_selector(
        cls,
        page_source: str,
        original_selector: str,
        step_data: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Tenta reconstruir um seletor válido quando o original falha na localização.
        Retorna o seletor curado ou None se a pontuação de similaridade for insuficiente.
        """
        if not page_source:
            return None

        step_name = (step_data or {}).get('name', '')
        step_desc = (step_data or {}).get('description', '')

        logger.warning(
            f"🩹 [Self-Healing] Element not found: '{original_selector}'. "
            f"Attempting auto-heal for step '{step_name}'."
        )

        try:
            root = ET.fromstring(page_source.encode('utf-8'))

            keywords = []
            if original_selector:
                sel_tokens = [
                    t.lower() for t in re.split(r'[_:\-\s/]+', str(original_selector))
                    if len(t) >= 3
                ]
                keywords.extend(sel_tokens)

            if step_name:
                keywords.extend(step_name.lower().split())
            if step_desc:
                keywords.extend(step_desc.lower().split())

            clean_keywords = [
                k for k in keywords
                if k not in cls.STOPWORDS and len(k) >= 3
            ]

            if not clean_keywords:
                return None

            highest_score = 0.0
            best_node = None
            orig_sel_clean = str(original_selector).lower()

            for element in root.iter():
                text = element.attrib.get('text', '').lower()
                desc = element.attrib.get('content-desc', '').lower()
                res_id = element.attrib.get('resource-id', '').lower()

                node_texts = [t for t in [text, desc, res_id] if t]
                if not node_texts:
                    continue

                score = 0.0
                for kw in clean_keywords:
                    for target in node_texts:
                        if kw in target:
                            score += 2.0
                        ratio = difflib.SequenceMatcher(None, kw, target).ratio()
                        if ratio > 0.75:
                            score += (ratio * 2.0)

                        full_ratio = difflib.SequenceMatcher(None, orig_sel_clean, target).ratio()
                        if full_ratio > 0.7:
                            score += (full_ratio * 3.0)

                if score > highest_score:
                    highest_score = score
                    best_node = element

            if best_node is not None and highest_score >= 1.5:
                text_val = best_node.attrib.get('text', '')
                desc_val = best_node.attrib.get('content-desc', '')
                res_val = best_node.attrib.get('resource-id', '')

                healed_selector = None
                if res_val:
                    healed_selector = f"//*[@resource-id='{res_val}']"
                elif desc_val:
                    healed_selector = f"//*[@content-desc='{desc_val}']"
                elif text_val:
                    healed_selector = f"//*[@text='{text_val}']"

                if healed_selector:
                    logger.info(
                        f"✨ [Self-Healing] Universal Healed selector found: {healed_selector} "
                        f"(Score: {highest_score:.2f})"
                    )
                    return healed_selector

        except Exception as e:
            logger.error(f"🩹 [Self-Healing] Failed to heal: {e}")

        return None


# Alias para convenção de nomenclatura
MobileHealingService = MobileHealingEngine

