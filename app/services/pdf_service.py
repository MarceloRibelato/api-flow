from fpdf import FPDF
from sqlalchemy.orm import Session
from datetime import datetime
from app.models.api_test_history_models import ApiExecutionHistory
from app.models.schedule_models import ScheduleModel

import unicodedata

class PDFService:

    @staticmethod
    def _s(text) -> str:
        """Convert any value to a Latin-1-safe string (fpdf 1.7.2 requires Latin-1)."""
        if text is None:
            return ''
        s = str(text)
        # Normalize unicode to decomposed form, then drop combining marks
        nfkd = unicodedata.normalize('NFKD', s)
        ascii_approx = ''.join(c for c in nfkd if not unicodedata.combining(c))
        # Encode to latin-1, replacing anything still problematic
        return ascii_approx.encode('latin-1', 'replace').decode('latin-1')

    @staticmethod
    def generate_execution_report(db: Session, schedule_id: int):
        # Fetch Data
        schedule = db.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
        if not schedule:
            return None
            
        history = db.query(ApiExecutionHistory).filter(ApiExecutionHistory.schedule_id == schedule_id).order_by(ApiExecutionHistory.id.asc()).all()
        
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        
        # --- 1. Header ---
        pdf.set_font('Arial', 'B', 16)
        pdf.set_text_color(40, 40, 40)
        pdf.cell(0, 10, 'Relatório de Execução de API', ln=True)
        
        pdf.set_font('Arial', '', 10)
        pdf.set_text_color(100, 100, 100)
        
        # Safe Access to Env Name
        env_name = schedule.environment.name if schedule.environment else "Global / Padrão"
        project_name = schedule.name or "Geral" # Ideally fetch Product name if possible, or use Schedule Name
        
        from datetime import timedelta
        br_time = datetime.utcnow() - timedelta(hours=3)
        pdf.cell(0, 5, f'Projeto: {project_name} | Ambiente: {env_name}', ln=True)
        pdf.cell(0, 5, f'Data: {br_time.strftime("%d/%m/%Y, %H:%M:%S")}', ln=True)
        
        # Calculate Total Duration
        total_time_ms = sum(h.response_time or 0 for h in history)
        pdf.cell(0, 5, f'Tempo Total: {total_time_ms / 1000:.2f}s', ln=True)
        
        pdf.ln(5)
        
        # Divider Line
        pdf.set_draw_color(200, 200, 200)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(10)
        
        # --- 2. Summary Table ---
        pdf.set_font('Arial', 'B', 12)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 10, 'Resumo Geral', ln=True)
        
        total = len(history)
        passed = sum(1 for h in history if h.status_code and h.status_code < 400 and not h.error_message)
        failed = total - passed
        rate = f"{int((passed / total) * 100)}%" if total > 0 else "0%"
        
        # Table Header
        headers = ['Total', 'Sucesso', 'Falhas', 'Taxa', 'Duração']
        data = [str(total), str(passed), str(failed), rate, f"{total_time_ms / 1000:.2f}s"]
        
        col_width = 38
        pdf.set_font('Arial', 'B', 10)
        pdf.set_fill_color(59, 130, 246) # Blue
        pdf.set_text_color(255, 255, 255)
        
        for h in headers:
            pdf.cell(col_width, 8, h, border=1, fill=True, align='C')
        pdf.ln()
        
        pdf.set_font('Arial', '', 10)
        pdf.set_text_color(0, 0, 0)
        for d in data:
            pdf.cell(col_width, 8, d, border=1, align='C')
        pdf.ln(15)
        
        # --- 3. Detailed Results (Grouped) ---
        
        # Group by Node Name or Feature Name (Simulating Frontend Groups)
        # Using feature_name or node_name as grouper
        groups = {}
        for h in history:
            key = h.feature_name or h.api_name or "Fluxo Principal"
            if key not in groups: groups[key] = []
            groups[key].append(h)
            
        for group_name, items in groups.items():
            # Check Page Break
            if pdf.get_y() > 250: pdf.add_page()
            
            # Group Header
            pdf.set_fill_color(241, 245, 249) # Light Gray
            pdf.set_text_color(51, 65, 85)
            pdf.set_font('Arial', 'B', 11)
            pdf.cell(0, 8, f'Grupo: {group_name}', ln=True, fill=True)
            pdf.ln(2)
            
            # Table Header
            pdf.set_fill_color(71, 85, 105) # Slate
            pdf.set_text_color(255, 255, 255)
            pdf.set_font('Arial', 'B', 9)
            
            col_widths = [80, 20, 20, 30, 40] # Name, Method, Status, Time, Result
            pdf.cell(col_widths[0], 7, 'Cenário / Passo', border=1, fill=True)
            pdf.cell(col_widths[1], 7, 'Método', border=1, fill=True, align='C')
            pdf.cell(col_widths[2], 7, 'Status', border=1, fill=True, align='C')
            pdf.cell(col_widths[3], 7, 'Tempo', border=1, fill=True, align='C')
            pdf.cell(col_widths[4], 7, 'Resultado', border=1, fill=True, align='C')
            pdf.ln()
            
            # Rows
            pdf.set_font('Arial', '', 9)
            fill = False
            for item in items:
                fill = not fill # Striped
                pdf.set_fill_color(248, 250, 252) if fill else pdf.set_fill_color(255, 255, 255)
                pdf.set_text_color(0, 0, 0)
                
                status_ok = (item.status_code and item.status_code < 400 and not item.error_message)
                res_text = 'OK' if status_ok else 'ERRO'
                res_color = (22, 163, 74) if status_ok else (220, 38, 38)
                
                # Truncate Name
                name = item.api_name or item.node_name or 'Step'
                if len(name) > 40: name = name[:37] + '...'
                
                pdf.cell(col_widths[0], 7, name, border=1, fill=True)
                pdf.cell(col_widths[1], 7, item.method or '-', border=1, fill=True, align='C')
                pdf.cell(col_widths[2], 7, str(item.status_code or 0), border=1, fill=True, align='C')
                pdf.cell(col_widths[3], 7, f"{item.response_time or 0}ms", border=1, fill=True, align='C')
                
                pdf.set_text_color(*res_color)
                pdf.set_font('Arial', 'B', 9)
                pdf.cell(col_widths[4], 7, res_text, border=1, fill=True, align='C')
                pdf.set_font('Arial', '', 9)
                pdf.ln()
            
            pdf.ln(10)
            
        # --- 4. Detailed Logs ---
        pdf.add_page()
        pdf.set_font('Arial', 'B', 14)
        pdf.set_text_color(40, 40, 40)
        pdf.cell(0, 10, 'Detalhamento Técnico / Logs', ln=True)
        pdf.ln(5)
        
        for group_name, items in groups.items():
            if pdf.get_y() > 250: pdf.add_page()
            
            pdf.set_fill_color(226, 232, 240)
            pdf.set_text_color(30, 41, 59)
            pdf.set_font('Arial', 'B', 11)
            pdf.cell(0, 8, f'Grupo: {group_name}', ln=True, fill=True)
            pdf.ln(4)
            
            for item in items:
                if pdf.get_y() > 220: pdf.add_page()
                
                pdf.set_font('Courier', 'B', 9) # Monospaced for logs
                status_ok = (item.status_code and item.status_code < 400 and not item.error_message)
                status_color = (22, 163, 74) if status_ok else (220, 38, 38)
                
                # Header Line: [METHOD] Name ... Status
                pdf.set_fill_color(241, 245, 249)
                pdf.set_text_color(0, 0, 0)
                pdf.cell(140, 6, f"[{item.method}] {item.api_name or 'Step'}", border=1, fill=True)
                
                pdf.set_text_color(*status_color)
                pdf.cell(50, 6, f"{item.status_code} - {'OK' if status_ok else 'ERRO'}", border=1, fill=True, align='R')
                pdf.ln()
                
                # URL
                pdf.set_font('Courier', '', 8)
                pdf.set_text_color(100, 100, 100)
                pdf.multi_cell(0, 5, f"URL: {item.url}", border='LBR')
                
                # Helper for Body/Json
                def print_section(title, content):
                    if not content or content == '{}' or content == 'None': return
                    
                    pdf.set_font('Courier', 'B', 8)
                    pdf.set_text_color(0, 0, 0)
                    pdf.cell(0, 5, title, ln=True)
                    
                    pdf.set_font('Courier', '', 7)
                    pdf.set_text_color(50, 50, 50)
                    
                    # Truncate if huge
                    text_content = str(content)
                    if len(text_content) > 2000: text_content = text_content[:2000] + '... (Truncated)'
                    
                    pdf.multi_cell(0, 4, text_content, border=1)
                    pdf.ln(1)

                print_section("Envio (Body):", item.request_body)
                print_section("Recebido (Body):", item.response_body)
                
                if item.error_message:
                     pdf.set_text_color(220, 38, 38)
                     print_section("Erro:", item.error_message)
                
                pdf.ln(4)
               
        return pdf.output(dest='S').encode('latin-1', 'replace')

    @staticmethod
    def _extract_screenshot_path(text: str) -> str | None:
        """Extract the filesystem path from a [Screenshot: /...] marker in response_body."""
        if not text or not isinstance(text, str):
            return None
        import re
        match = re.search(r'\[Screenshot: (/[^\]]+)\]', text)
        return match.group(1) if match else None

    @staticmethod
    def _resolve_media_path(url_path: str) -> str | None:
        """Convert a /screenshots/... or /videos/... URL path to an absolute filesystem path."""
        import os
        try:
            from app.main import VIDEO_DIR
            base = os.path.dirname(VIDEO_DIR)  # /app/media
            fs_path = os.path.normpath(os.path.join(base, url_path.lstrip('/')))
            return fs_path if os.path.exists(fs_path) else None
        except Exception:
            return None


    @staticmethod
    def _get_browser_description(item) -> str:
        """
        Extract a human-readable action description from a BROWSER/E2E history item.
        Parses the response_body text produced by PlaywrightExecutorService.
        All output is pure ASCII / Latin-1 safe for fpdf.
        """
        import re
        body = item.response_body or ''
        if not isinstance(body, str):
            body = str(body)
        # Strip screenshot markers
        clean = re.sub(r'\[Screenshot:.*?\]', '', body).strip()

        # New format: Typed 'value' into selector
        m = re.search(r"Typed '([^']+)' into\s*(.+)", clean, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            sel = m.group(2).strip()
            return f"{val} (em {sel})"

        # Legacy: Typed text into selector
        m = re.search(r'Typed text into\s*(.+)', clean, re.IGNORECASE)
        if m:
            return m.group(1).strip()

        patterns = [
            (r'Navigated to (\S+)',            lambda m: m.group(1)),
            (r'Clicked element:\s*(.+)',       lambda m: m.group(1).strip()),
            (r'Hovered over\s*(.+)',           lambda m: m.group(1).strip()),
            (r'Scrolled (.+)',                 lambda m: m.group(1).strip()),
            (r'Pressed (.+)',                  lambda m: m.group(1).strip()),
            (r'Waited for (.+)',               lambda m: m.group(1).strip()),
            (r'Element (.+) is now present',   lambda m: m.group(1).strip()),
            (r'Assertion passed:\s*(.+)',      lambda m: m.group(1).strip()),
        ]
        for pattern, extractor in patterns:
            m = re.search(pattern, clean, re.IGNORECASE)
            if m:
                return extractor(m)

        if 'Screenshot captured' in clean or 'screenshot' in clean.lower():
            return 'Screenshot capturado'
        if 'Page refreshed' in clean:
            return 'Pagina recarregada'

        # Fallback: use stored url
        return (item.url or item.api_name or '')[:80]

    @staticmethod
    def _extract_video_frames(video_path: str, num_frames: int = 6) -> list[str]:
        """
        Extract N evenly-spaced frames from a webm/mp4 video using ffmpeg.
        Returns a list of absolute temp PNG paths. Empty list if ffmpeg not available.
        """
        import subprocess, os, tempfile

        frames = []
        try:
            # Get video duration
            probe = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
                capture_output=True, text=True, timeout=15
            )
            duration = float(probe.stdout.strip())
            if duration <= 0:
                return []

            tmpdir = tempfile.mkdtemp()
            interval = duration / (num_frames + 1)

            for i in range(1, num_frames + 1):
                ts = interval * i
                out_path = os.path.join(tmpdir, f"frame_{i:02d}.png")
                result = subprocess.run(
                    ['ffmpeg', '-ss', str(ts), '-i', video_path,
                     '-frames:v', '1', '-q:v', '2', out_path, '-y'],
                    capture_output=True, timeout=30
                )
                if os.path.exists(out_path):
                    frames.append(out_path)

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Video frame extraction failed: {e}")

        return frames

    @staticmethod
    def generate_e2e_report(db: Session, schedule_id: int):
        """
        Premium E2E PDF report with embedded screenshots and video frames.
        """
        import os, re

        schedule = db.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
        if not schedule: return None

        history_all = db.query(ApiExecutionHistory).filter(
            ApiExecutionHistory.schedule_id == schedule_id
        ).order_by(ApiExecutionHistory.id.asc()).all()

        # Fallback: if no direct schedule match, try to find via the most recent batch_id
        # that belongs to this schedule (useful when schedule was re-used or ID mismatch)
        if not history_all:
            recent = db.query(ApiExecutionHistory).filter(
                ApiExecutionHistory.schedule_id == schedule_id
            ).order_by(ApiExecutionHistory.id.desc()).first()
            if not recent:
                # Last-resort: pick the most recent block of history globally
                recent = db.query(ApiExecutionHistory).order_by(ApiExecutionHistory.id.desc()).first()
            if recent and recent.batch_id:
                history_all = db.query(ApiExecutionHistory).filter(
                    ApiExecutionHistory.batch_id == recent.batch_id
                ).order_by(ApiExecutionHistory.id.asc()).all()

        if not history_all: return None

        # All steps (browser + any API support steps)
        history = history_all

        # --- Metrics ---
        total    = len(history)
        passed   = sum(1 for h in history if h.status_code and h.status_code < 400 and not h.error_message)
        failed   = total - passed
        rate_pct = int((passed / total) * 100) if total > 0 else 0
        total_ms = sum(h.response_time or 0 for h in history)
        video_url_rel = next((h.video_url for h in history_all if h.video_url), None)

        env_name = schedule.environment.name if schedule.environment else "Global / Padrão"

        # --- PDF Setup ---
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)

        # =====================================================================
        # PAGE 1: COVER
        # =====================================================================
        pdf.add_page()

        # Dark header band
        pdf.set_fill_color(15, 23, 42)   # slate-900
        pdf.rect(0, 0, 210, 55, 'F')

        # Accent bar
        pdf.set_fill_color(99, 102, 241)  # indigo-500
        pdf.rect(0, 52, 210, 3, 'F')

        pdf.set_xy(12, 10)
        pdf.set_font('Arial', 'B', 22)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 12, PDFService._s('Relatorio de Automacao E2E'), ln=True)

        pdf.set_xy(12, 24)
        pdf.set_font('Arial', '', 10)
        pdf.set_text_color(148, 163, 184)  # slate-400
        pdf.cell(0, 6, PDFService._s(f'Fluxo: {schedule.name}'), ln=True)
        pdf.set_xy(12, 30)
        pdf.cell(0, 6, PDFService._s(f'Ambiente: {env_name}'), ln=True)
        pdf.set_xy(12, 36)
        from datetime import timedelta
        br_time = datetime.utcnow() - timedelta(hours=3)
        pdf.cell(0, 6, PDFService._s(f'Gerado em: {br_time.strftime("%d/%m/%Y as %H:%M")}'), ln=True)

        # Summary cards
        pdf.set_y(62)
        card_data = [
            ('TOTAL',   str(total),          (71, 85, 105)),
            ('PASSOU',  str(passed),          (22, 163, 74)),
            ('FALHOU',  str(failed),          (220, 38, 38)),
            ('TAXA',    f'{rate_pct}%',       (99, 102, 241)),
            ('DURACAO', f'{total_ms/1000:.1f}s', (245, 158, 11)),
        ]
        card_w, card_h = 36, 22
        x_start = 12
        for label, value, color in card_data:
            pdf.set_xy(x_start, 62)
            pdf.set_fill_color(*color)
            pdf.rect(x_start, 62, card_w, card_h, 'F')
            pdf.set_xy(x_start, 64)
            pdf.set_font('Arial', 'B', 14)
            pdf.set_text_color(255, 255, 255)
            pdf.cell(card_w, 8, value, align='C')
            pdf.set_xy(x_start, 73)
            pdf.set_font('Arial', '', 7)
            pdf.cell(card_w, 6, label, align='C')
            x_start += card_w + 3

        # spacing after summary cards
        pdf.set_y(90)
        
        # Top 5 Demoras (Performance Bottlenecks)
        pdf.set_font('Arial', 'B', 10)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(0, 8, PDFService._s('Top 5 Gargalos de Performance (Passos Mais Lentos)'), ln=True)
        pdf.set_font('Arial', '', 8)
        
        slowest_steps = sorted([h for h in history if h.response_time], key=lambda x: x.response_time, reverse=True)[:5]
        if slowest_steps:
            for idx, s in enumerate(slowest_steps):
                pdf.set_x(12)
                pdf.set_text_color(80, 80, 80)
                name = PDFService._s((s.api_name or s.node_name or 'Passo')[:60])
                time_str = f"{s.response_time}ms"
                pdf.cell(8, 5, f"{idx+1}.", ln=False)
                if s.response_time > 3000:
                    pdf.set_text_color(220, 38, 38)
                elif s.response_time > 1500:
                    pdf.set_text_color(245, 158, 11)
                pdf.cell(15, 5, time_str, ln=False)
                pdf.set_text_color(30, 41, 59)
                pdf.cell(0, 5, name, ln=True)
        else:
            pdf.set_x(12)
            pdf.cell(0, 5, 'Sem dados de tempo.', ln=True)
        
        pdf.ln(5)


        # =====================================================================
        # PAGE 2+: STEPS GROUPED BY NODE
        # =====================================================================
        pdf.add_page()

        pdf.set_font('Arial', 'B', 13)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(0, 8, PDFService._s('Detalhamento por Node do Fluxo'), ln=True)
        pdf.ln(2)

        screenshot_gallery = []

        # --- Group all items by node_name ---
        from collections import OrderedDict
        node_groups = OrderedDict()
        for item in history:
            node_key = item.node_name or 'Fluxo Principal'
            if node_key not in node_groups:
                node_groups[node_key] = []
            node_groups[node_key].append(item)

        HTTP_METHODS = {'GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'}

        col_w_e2e = [58, 96, 20, 16]   # [Passo, Descricao, Tempo, Resultado]
        col_w_api = [72, 18, 20, 26, 54]  # API call table columns (extra URL column)

        for node_name, items in node_groups.items():
            if pdf.get_y() > 240:
                pdf.add_page()

            # ── Node header bar ──
            pdf.set_fill_color(30, 41, 59)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font('Arial', 'B', 9)
            node_passed = sum(1 for i in items if i.status_code and i.status_code < 400 and not i.error_message)
            node_total  = len(items)
            node_failed = node_total - node_passed
            status_badge = f'  v{node_passed}  x{node_failed}' if node_failed > 0 else f'  v{node_passed}'
            label = PDFService._s(f'  Node: {node_name[:55]}') + status_badge.rjust(20)
            pdf.cell(0, 8, label, fill=True, ln=True)
            pdf.ln(1)

            # Deduplicate contiguous duplicate E2E steps to prevent noise
            deduped_items = []
            for step in items:
                step_is_api = (step.method or '').upper() in HTTP_METHODS
                if not step_is_api:
                    step_name = (step.api_name or '').strip().lower()
                    if deduped_items and not ((deduped_items[-1].method or '').upper() in HTTP_METHODS) and (deduped_items[-1].api_name or '').strip().lower() == step_name:
                        prev = deduped_items[-1]
                        if (step.response_time or 0) > (prev.response_time or 0):
                            deduped_items[-1] = step
                    else:
                        deduped_items.append(step)
                else:
                    deduped_items.append(step)

            # ── Unified Chronological Table ──
            if deduped_items:
                pdf.set_font('Arial', 'B', 7)
                pdf.set_text_color(99, 102, 241)
                pdf.cell(0, 5, PDFService._s('  Comandos Executados e APIs Interceptadas (Cronológico)'), ln=True)

                pdf.set_fill_color(71, 85, 105)
                pdf.set_text_color(255, 255, 255)
                pdf.set_font('Arial', 'B', 7)
                col_w = [45, 95, 16, 16, 18] # Total 190
                headers = ['Passo / Metodo', 'Descricao / URL', 'Status', 'Tempo', 'Resultado']
                for i, h in enumerate(headers):
                    pdf.cell(col_w[i], 6, h, border=1, fill=True, align='C')
                pdf.ln()

                for item in deduped_items:
                    if pdf.get_y() > 262:
                        pdf.add_page()

                    is_api = (item.method or '').upper() in HTTP_METHODS
                    status_ok  = (item.status_code and item.status_code < 400 and not item.error_message)
                    
                    if is_api:
                        fill_color = (240, 253, 244) if status_ok else (255, 241, 242)
                        name = PDFService._s(f"[{item.method}] {(item.api_name or 'API')[:30]}")
                        url  = (item.processed_url or item.url or '')
                        desc = PDFService._s((url[:65] + '...') if len(url) > 65 else url)
                        status_str = str(item.status_code or '-')
                    else:
                        fill_color = (250, 252, 255) if status_ok else (255, 241, 242) # Light blueish for E2E
                        name = PDFService._s((item.api_name or 'Passo E2E')[:30])
                        desc = PDFService._s(PDFService._get_browser_description(item)[:65])
                        status_str = '-'

                    res_text   = 'OK' if status_ok else 'ERRO'
                    res_color  = (22, 163, 74) if status_ok else (220, 38, 38)

                    pdf.set_fill_color(*fill_color)
                    pdf.set_text_color(30, 41, 59)
                    
                    # Nome (Bold se for E2E)
                    pdf.set_font('Arial', 'B' if not is_api else '', 7)
                    pdf.cell(col_w[0], 6, name, border=1, fill=True)
                    
                    # Desc
                    pdf.set_font('Arial', '', 6)
                    pdf.set_text_color(80, 80, 120)
                    pdf.cell(col_w[1], 6, desc, border=1, fill=True)
                    
                    # Variados
                    pdf.set_font('Arial', '', 7)
                    if is_api:
                        pdf.set_text_color(*((22, 163, 74) if status_ok else (220, 38, 38)))
                    else:
                        pdf.set_text_color(30, 41, 59)
                    pdf.cell(col_w[2], 6, PDFService._s(status_str), border=1, fill=True, align='C')
                    
                    pdf.set_text_color(30, 41, 59)
                    pdf.cell(col_w[3], 6, PDFService._s(f'{item.response_time or 0}ms'), border=1, fill=True, align='C')
                    
                    pdf.set_text_color(*res_color)
                    pdf.set_font('Arial', 'B', 7)
                    pdf.cell(col_w[4], 6, PDFService._s(res_text), border=1, fill=True, align='C')
                    
                    pdf.set_text_color(0, 0, 0)
                    pdf.ln()

                    if not status_ok and item.error_message:
                        pdf.set_x(15)
                        pdf.set_font('Arial', 'I', 6)
                        pdf.set_text_color(180, 0, 0)
                        err_msg = PDFService._s((item.error_message or '')[:155])
                        if len(item.error_message or '') > 155: err_msg += '...'
                        pdf.cell(190, 4, err_msg, ln=True)
                        pdf.set_text_color(0, 0, 0)

                    # Display Inline Screenshots for E2E steps
                    if not is_api:
                        combo = (item.response_body or '') + (item.error_message or '')
                        shot_rel = PDFService._extract_screenshot_path(combo)
                        if shot_rel:
                            shot_path = PDFService._resolve_media_path(shot_rel)
                            if shot_path:
                                try:
                                    pdf.ln(2)
                                    pdf.image(shot_path, x=15, w=80)
                                    pdf.ln(2)
                                except Exception:
                                    pass

                pdf.ln(2)

            pdf.ln(3)







        # =====================================================================
        # OUTPUT
        # =====================================================================
        try:
            result = pdf.output(dest='S')
            if isinstance(result, str):
                return result.encode('latin-1', 'replace')
            return result
        except Exception as e:
            import logging, traceback
            logging.getLogger(__name__).error(f"PDF generation error: {e}")
            traceback.print_exc()
            return None


