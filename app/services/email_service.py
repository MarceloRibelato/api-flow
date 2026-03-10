from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
from app.config import settings
from pydantic import EmailStr, ValidationError
import logging

logger = logging.getLogger(__name__)

def get_mail_config():
    try:
        return ConnectionConfig(
            MAIL_USERNAME=settings.SMTP_USER,
            MAIL_PASSWORD=settings.SMTP_PASSWORD,
            MAIL_FROM=settings.MAIL_FROM,
            MAIL_PORT=settings.SMTP_PORT,
            MAIL_SERVER=settings.SMTP_HOST,
            MAIL_STARTTLS=True,
            MAIL_SSL_TLS=False,
            USE_CREDENTIALS=True,
            VALIDATE_CERTS=True
        )
    except ValidationError as e:
        logger.error(f"Invalid Email Configuration: {e}")
        return None

class EmailService:
    @staticmethod
    async def send_reset_password_email(email: str, reset_link: str):
        if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
            logger.warning("SMTP credentials not configured. Email will not be sent.")
            return

        conf = get_mail_config()
        if not conf:
            logger.error("Skipping email sending due to invalid configuration.")
            return

        html = f"""
        <div style="font-family: inherit; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #eee; border-radius: 10px;">
            <h2 style="color: #2c3e50; text-align: center;">Recuperação de Senha</h2>
            <p>Olá,</p>
            <p>Você solicitou a redefinição de sua senha. Clique no botão abaixo para prosseguir:</p>
            <div style="text-align: center; margin: 30px 0;">
                <a href="{reset_link}" style="background-color: #3498db; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold;">Redefinir Senha</a>
            </div>
            <p>Este link expirará em 15 minutos.</p>
            <p>Se você não solicitou esta alteração, ignore este e-mail.</p>
            <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">
            <p style="font-size: 0.8em; color: #7f8c8d; text-align: center;">Este é um e-mail automático do sistema Flow. Por favor, não responda.</p>
        </div>
        """

        try:
            message = MessageSchema(
                subject="Recuperação de Senha - Flow",
                recipients=[email],
                body=html,
                subtype=MessageType.html
            )

            fm = FastMail(conf)
            await fm.send_message(message)
            logger.info(f"Password reset email sent to {email}")
        except Exception as e:
            logger.error(f"Failed to send email to {email}: {str(e)}")
