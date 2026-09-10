import io
import logging
import re
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger(__name__)


class MobileVisionService:
    """
    Serviço especializado em Visão Computacional para automação móvel.
    Analisa capturas de tela (PNG) para localizar contornos e regiões
    de elementos visuais (botões, containers) de forma dinâmica em qualquer resolução.
    """

    @staticmethod
    def detect_button_coordinates(
        screenshot_bytes: Optional[bytes] = None,
        container_bounds: Optional[Dict[str, Any]] = None,
        target_text: Optional[str] = None,
        page_source: Optional[str] = None
    ) -> Optional[Tuple[int, int]]:
        """
        Analisa a captura de tela (PNG) e detecta visualmente o contorno do botão
        (retângulo preenchido de cor destacada dentro do container).
        Calcula as coordenadas (x, y) exatas dinamicamente para qualquer aparelho e resolução.
        """
        if not screenshot_bytes:
            return None

        # Validação de segurança para busca em tela cheia (sem container bounds):
        # Se target_text foi informado, verificar se palavras-chave dele aparecem no page_source.
        # Se não existirem na tela atual, aborta imediatamente para evitar clicar em telas/botões não relacionados.
        if not container_bounds and target_text and page_source:
            try:
                clean_t = re.sub(
                    r"[^a-zA-Z0-9\s_áàâãéèêíïóôõöúçñÁÀÂÃÉÈÊÍÏÓÔÕÖÚÇÑ]",
                    " ",
                    str(target_text)
                ).strip().lower()
                tokens = [
                    t for t in clean_t.split()
                    if len(t) > 2 and t not in ('button', 'btn', 'clickable', 'view', 'android', 'widget', 'text')
                ]
                if tokens:
                    ps_lower = page_source.lower()
                    if not any(t in ps_lower for t in tokens):
                        logger.warning(
                            f"⚠️ [Vision AI] Target '{target_text}' (keywords: {tokens}) "
                            "was NOT found anywhere in current screen page_source. "
                            "Aborting visual detection to prevent false positive click."
                        )
                        return None
            except Exception as ps_err:
                logger.debug(f"[Vision AI] page_source validation check skipped: {ps_err}")

        try:
            from PIL import Image
            import numpy as np

            image = Image.open(io.BytesIO(screenshot_bytes)).convert('RGB')
            img_w, img_h = image.size

            if container_bounds:
                cx0 = max(0, int(container_bounds.get('x', 0)))
                cy0 = max(0, int(container_bounds.get('y', 0)))
                cw = int(container_bounds.get('width', img_w))
                ch = int(container_bounds.get('height', img_h))
                cx1 = min(img_w, cx0 + cw)
                cy1 = min(img_h, cy0 + ch)
            else:
                cx0, cy0, cx1, cy1 = 0, 0, img_w, img_h

            cropped = image.crop((cx0, cy0, cx1, cy1))
            crop_w, crop_h = cropped.size
            if crop_w <= 10 or crop_h <= 10:
                return None

            # Estratégia 1: OpenCV Contour Analysis (Se opencv-python estiver disponível)
            try:
                import cv2
                np_img = np.array(cropped)
                gray = cv2.cvtColor(np_img, cv2.COLOR_RGB2GRAY)
                blurred = cv2.GaussianBlur(gray, (5, 5), 0)
                edges = cv2.Canny(blurred, 30, 150)
                contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                candidate_buttons = []
                for cnt in contours:
                    x, y, w, h = cv2.boundingRect(cnt)
                    aspect_ratio = w / float(h) if h > 0 else 0
                    if 1.2 <= aspect_ratio <= 12.0 and (crop_w * 0.20) <= w <= (crop_w * 0.98) and 25 <= h <= 140:
                        # Pontua candidatos localizados no terço/metade inferior do container (padrão de botões de ação)
                        score = (y + h / 2) + (w * h * 0.05)
                        candidate_buttons.append((score, x, y, w, h))

                if candidate_buttons:
                    # Em varredura de tela cheia (sem container bounds), se houver múltiplos botões candidatos,
                    # não chutar aleatoriamente para evitar cliques incorretos
                    if not container_bounds and len(candidate_buttons) > 1:
                        logger.warning(
                            f"⚠️ [Vision AI] Multiple button candidates ({len(candidate_buttons)}) "
                            "found on full screen without container bounds. "
                            "Aborting detection to avoid clicking wrong button."
                        )
                        return None

                    candidate_buttons.sort(key=lambda item: item[0], reverse=True)
                    _, bx, by, bw, bh = candidate_buttons[0]
                    target_x = cx0 + bx + (bw // 2)
                    target_y = cy0 + by + (bh // 2)
                    return target_x, target_y
            except Exception as cv_err:
                logger.debug(f"[Vision AI] OpenCV contour strategy skipped/failed: {cv_err}")

            # Estratégia 2: Varredura de baixa variância de cor (Apenas quando container delimitado foi fornecido)
            if container_bounds:
                np_img = np.array(cropped)
                start_row = int(crop_h * 0.5)
                row_scores = []
                for r in range(start_row, crop_h - 10):
                    row_pixels = np_img[r, int(crop_w * 0.15):int(crop_w * 0.85)]
                    if len(row_pixels) > 0:
                        var = np.var(row_pixels, axis=0).mean()
                        row_scores.append((var, r))

                if row_scores:
                    row_scores.sort(key=lambda item: item[0])
                    best_r = row_scores[0][1]
                    target_x = cx0 + (crop_w // 2)
                    target_y = cy0 + best_r
                    return target_x, target_y

        except Exception as e:
            logger.warning(f"⚠️ [Vision AI] Exceção ao detectar botão na screenshot: {e}")

        return None

