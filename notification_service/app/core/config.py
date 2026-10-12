"""
Configuration management via environment variables.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

_env = os.getenv("ENVIRONMENT", "development")
_env_file = Path(__file__).resolve().parent.parent.parent / f".env.{_env}"
if _env_file.exists():
    load_dotenv(_env_file, override=False)


class Settings:
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8002"))
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "production")

    SENDGRID_API_KEY: str = os.getenv("SENDGRID_API_KEY", "")
    FROM_EMAIL: str = os.getenv("FROM_EMAIL", "hello@meetmemoir.com")
    FROM_NAME: str = os.getenv("FROM_NAME", "Memoir")

    # SMTP fallback (e.g. Gmail)
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")

    INTERNAL_SERVICE_KEY: str = os.getenv("INTERNAL_SERVICE_KEY", "")

    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    @property
    def is_dev(self) -> bool:
        return self.ENVIRONMENT == "development"

    @property
    def can_send_email(self) -> bool:
        return bool(self.SENDGRID_API_KEY) or bool(self.SMTP_HOST)


settings = Settings()
