import os

from pydantic_settings import BaseSettings, SettingsConfigDict

# Dynamically determine the path to the .env file
# app/config.py is in /app, so .env is in the parent directory
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")


class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    API_BASE_URL: str = "http://127.0.0.1:8000"
    FRONTEND_BASE_URL: str = "http://localhost:5173"
    INTERNAL_GATEWAY_URL: str = "http://flow-frontend" # For Docker internal rewrite
    TARGET_URL_REPLACEMENT: str = "" # Optional global replacement for localhost
    HISTORY_RETENTION_DAYS: int = 30
    HISTORY_ARCHIVE_RETENTION_DAYS: int = 180
    MAX_CONCURRENT_FEATURES: int = 5
    
    # License & AI Manager Integration
    LICENSE_MANAGER_URL: str = ""
    QA_FLOW_LICENSE_KEY: str = ""
    
    # SMTP Settings
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    MAIL_FROM: str = "noreply@flow.com"

    # Security
    ALLOWED_ORIGINS: str = ""  # Comma-separated list, e.g. "http://localhost:5173,http://flow.qa"
    VERIFY_SSL: bool = False   # Set to True in production

    model_config = SettingsConfigDict(
        env_file=ENV_PATH, env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
