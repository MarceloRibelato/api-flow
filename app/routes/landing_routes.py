import os
import smtplib
from email.message import EmailMessage
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, EmailStr
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

class DownloadRequest(BaseModel):
    name: str
    email: EmailStr

def send_download_email(name: str, email: str):
    try:
        # Load SMTP settings from env (fallback to empty for mock)
        smtp_host = os.environ.get("SMTP_HOST", "")
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        smtp_user = os.environ.get("SMTP_USER", "")
        smtp_pass = os.environ.get("SMTP_PASS", "")

        msg = EmailMessage()
        msg['Subject'] = 'Seu Download do Flow-QA Chegou! 🚀'
        msg['From'] = smtp_user or "contato@flow-qa.com"
        msg['To'] = email
        msg.set_content(f"""Olá {name},

Obrigado pelo seu interesse no Flow-QA (Uso Pessoal)!

Em anexo, você encontrará:
1. docker-compose.prod.yml (Para rodar o Flow-QA)
2. instructions.txt (Com o passo a passo da instalação)

Se precisar de mais de 1 projeto simultâneo ou quiser integrar no seu CI/CD, não deixe de conferir nossa licença Comercial!

Abraços,
Equipe Flow-QA
""")

        # Paths to attachments
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        compose_path = os.path.join(base_dir, "docker-compose.prod.yml")
        instr_path = os.path.join(base_dir, "instructions.txt")

        files_to_attach = [compose_path, instr_path]
        for filepath in files_to_attach:
            if os.path.exists(filepath):
                with open(filepath, 'rb') as f:
                    file_data = f.read()
                    file_name = os.path.basename(filepath)
                    msg.add_attachment(file_data, maintype='application', subtype='octet-stream', filename=file_name)
            else:
                logger.warning(f"File not found for attachment: {filepath}")

        if smtp_host and smtp_user and smtp_pass:
            # Envio real se houver credenciais
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
            logger.info(f"Email sent successfully to {email}")
        else:
            # Mock para log se não houver credenciais
            logger.info(f"[MOCK EMAIL] To: {email} | Subject: {msg['Subject']}")
            logger.info("Env vars SMTP_HOST ou SMTP_USER não configurados. Mock apenas.")

    except Exception as e:
        logger.error(f"Failed to send email to {email}: {str(e)}")

@router.post("/landing/download")
async def handle_download(payload: DownloadRequest, background_tasks: BackgroundTasks):
    """
    Recebe os dados do modal de download e agenda o envio do e-mail em background.
    """
    background_tasks.add_task(send_download_email, payload.name, payload.email)
    return {"status": "success", "message": "Email disparado."}
