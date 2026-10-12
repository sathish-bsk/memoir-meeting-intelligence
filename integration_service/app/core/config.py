"""
Integration service configuration — loaded from environment variables.
Loads .env.{ENVIRONMENT} file automatically (development by default).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load environment-specific .env file before reading any config.
_env = os.getenv("ENVIRONMENT", "development")
_env_file = Path(__file__).resolve().parent.parent.parent / f".env.{_env}"
if _env_file.exists():
    load_dotenv(_env_file, override=False)


class Settings:
    """Integration service settings."""

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8006"))
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "production")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Database (shared videoclip_db)
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://videoclip:videoclipdev@localhost:5432/videoclip_db",
    )
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "10"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "5"))
    DB_POOL_RECYCLE: int = int(os.getenv("DB_POOL_RECYCLE", "3600"))

    # JWT (public key only — for decoding tokens issued by auth service)
    JWT_PUBLIC_KEY_PATH: str = os.getenv("JWT_PUBLIC_KEY_PATH", "./keys/public.pem")
    JWT_ISSUER: str = "videoclip-auth"
    JWT_AUDIENCE: str = "videoclip"
    JWT_ALGORITHM: str = "ES256"

    # Inter-service auth
    INTERNAL_SERVICE_KEY: str = os.getenv("INTERNAL_SERVICE_KEY", "")

    # Google OAuth
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")

    # Outlook / Office 365 OAuth
    OUTLOOK_CLIENT_ID: str = os.getenv("OUTLOOK_CLIENT_ID", "")
    OUTLOOK_CLIENT_SECRET: str = os.getenv("OUTLOOK_CLIENT_SECRET", "")

    # Fernet key for encrypting OAuth tokens at rest
    CALENDAR_ENCRYPTION_KEY: str = os.getenv("CALENDAR_ENCRYPTION_KEY", "")

    # Zoom OAuth
    ZOOM_CLIENT_ID: str = os.getenv("ZOOM_CLIENT_ID", "")
    ZOOM_CLIENT_SECRET: str = os.getenv("ZOOM_CLIENT_SECRET", "")
    ZOOM_ENCRYPTION_KEY: str = os.getenv("ZOOM_ENCRYPTION_KEY", "")

    # Slack OAuth
    SLACK_CLIENT_ID: str = os.getenv("SLACK_CLIENT_ID", "")
    SLACK_CLIENT_SECRET: str = os.getenv("SLACK_CLIENT_SECRET", "")
    SLACK_SIGNING_SECRET: str = os.getenv("SLACK_SIGNING_SECRET", "")
    SLACK_ENCRYPTION_KEY: str = os.getenv("SLACK_ENCRYPTION_KEY", "")
    SLACK_OAUTH_REDIRECT_URI: str = os.getenv("SLACK_OAUTH_REDIRECT_URI", "")

    # CRM OAuth (HubSpot + Zoho)
    CRM_ENCRYPTION_KEY: str = os.getenv("CRM_ENCRYPTION_KEY", "")
    HUBSPOT_CLIENT_ID: str = os.getenv("HUBSPOT_CLIENT_ID", "")
    HUBSPOT_CLIENT_SECRET: str = os.getenv("HUBSPOT_CLIENT_SECRET", "")
    ZOHO_CLIENT_ID: str = os.getenv("ZOHO_CLIENT_ID", "")
    ZOHO_CLIENT_SECRET: str = os.getenv("ZOHO_CLIENT_SECRET", "")

    # Teams Cloud Recording (Graph API — reuses OUTLOOK_CLIENT_ID / OUTLOOK_CLIENT_SECRET)
    TEAMS_CLOUD_WEBHOOK_URL: str = os.getenv("TEAMS_CLOUD_WEBHOOK_URL", "")
    TEAMS_CLOUD_CLIENT_STATE_SECRET: str = os.getenv("TEAMS_CLOUD_CLIENT_STATE_SECRET", "")

    # Redis (for event pub/sub)
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/2")

    # Frontend URL (for OAuth redirect)
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")

    @property
    def is_dev(self) -> bool:
        return self.ENVIRONMENT == "development"


settings = Settings()
