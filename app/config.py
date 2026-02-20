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
    INTERNAL_GATEWAY_URL: str = "http://flow-frontend" # For Docker internal rewrite
    TARGET_URL_REPLACEMENT: str = "" # Optional global replacement for localhost
    HISTORY_RETENTION_DAYS: int = 30
    HISTORY_ARCHIVE_RETENTION_DAYS: int = 180
    MAX_CONCURRENT_FEATURES: int = 5

    model_config = SettingsConfigDict(
        env_file=ENV_PATH, env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
