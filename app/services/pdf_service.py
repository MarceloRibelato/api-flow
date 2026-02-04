from fpdf import FPDF
from sqlalchemy.orm import Session
from datetime import datetime
from app.models.api_test_history_models import ApiExecutionHistory
from app.models.schedule_models import ScheduleModel

class PDFService:
    @staticmethod
    def generate_execution_report(db: Session, schedule_id: int):
        # Fetch Data
        schedule = db.query(ScheduleModel).filter(ScheduleModel.id == schedule_id).first()
        if not schedule:
            return None
            
        history = db.query(ApiExecutionHistory).filter(ApiExecutionHistory.schedule_id == schedule_id).order_by(ApiExecutionHistory.created_at).all()
        
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
        
        pdf.cell(0, 5, f'Projeto: {project_name} | Ambiente: {env_name}', ln=True)
        pdf.cell(0, 5, f'Data: {datetime.utcnow().strftime("%d/%m/%Y, %H:%M:%S UTC")}', ln=True)
        
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
               
        return pdf.output(dest='S').encode('latin-1')
