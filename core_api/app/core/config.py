"""
Core API configuration — loaded from environment variables.
Loads .env.{ENVIRONMENT} file automatically (development by default).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load environment-specific .env file before reading any config.
# ENVIRONMENT can be set as a real OS env var; defaults to "development".
_env = os.getenv("ENVIRONMENT", "development")
_env_file = Path(__file__).resolve().parent.parent.parent / f".env.{_env}"
if _env_file.exists():
    load_dotenv(_env_file, override=False)


class Settings:
    """Core API settings."""

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "production")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://core_user:corepass@localhost:5432/videoclip_db",
    )
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "20"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    DB_POOL_RECYCLE: int = int(os.getenv("DB_POOL_RECYCLE", "3600"))

    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/1")

    # JWT (public key only — for decoding tokens issued by auth service)
    JWT_PUBLIC_KEY_PATH: str = os.getenv("JWT_PUBLIC_KEY_PATH", "./keys/public.pem")
    JWT_ISSUER: str = "videoclip-auth"
    JWT_AUDIENCE: str = "videoclip"
    JWT_ALGORITHM: str = "ES256"

    # Inter-service auth
    INTERNAL_SERVICE_KEY: str = os.getenv("INTERNAL_SERVICE_KEY", "")

    # Service URLs
    AUTH_SERVICE_URL: str = os.getenv("AUTH_SERVICE_URL", "http://localhost:8001")
    ORCHESTRATOR_URL: str = os.getenv("ORCHESTRATOR_URL", "http://localhost:4000")
    # Callback URL for bot events. MUST be reachable from wherever the
    # transcription_worker runs (the GPU box, off-host). The old localhost
    # default broke the GPU-box worker's webhook callback — see
    # feedback_no_worker_on_ec2.md. Public URL is the safe default.
    BOT_CALLBACK_URL: str = os.getenv(
        "BOT_CALLBACK_URL",
        "https://app.meetmemoir.com/api/internal/webhooks/bot-events",
    )
    TRANSCRIPTION_WEBHOOK_URL: str = os.getenv(
        "TRANSCRIPTION_WEBHOOK_URL",
        os.getenv(
            "BOT_CALLBACK_URL",
            "https://app.meetmemoir.com/api/internal/webhooks/bot-events",
        ),
    )
    TRANSCRIPTION_SERVICE_URL: str = os.getenv("TRANSCRIPTION_SERVICE_URL", "http://localhost:8000")
    BILLING_SERVICE_URL: str = os.getenv("BILLING_SERVICE_URL", "http://localhost:8003")
    NOTIFICATION_SERVICE_URL: str = os.getenv("NOTIFICATION_SERVICE_URL", "http://localhost:8002")
    INTEGRATION_SERVICE_URL: str = os.getenv("INTEGRATION_SERVICE_URL", "http://localhost:8006")
    CRM_SERVICE_URL: str = os.getenv("CRM_SERVICE_URL", "http://localhost:8004")
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")

    # Zoom webhook + OAuth (for download token refresh)
    ZOOM_WEBHOOK_SECRET_TOKEN: str = os.getenv("ZOOM_WEBHOOK_SECRET_TOKEN", "")
    ZOOM_CLIENT_ID: str = os.getenv("ZOOM_CLIENT_ID", "")
    ZOOM_CLIENT_SECRET: str = os.getenv("ZOOM_CLIENT_SECRET", "")
    ZOOM_ENCRYPTION_KEY: str = os.getenv("ZOOM_ENCRYPTION_KEY", "")

    # Teams Cloud Recording (Graph API — reuses Outlook Azure AD app)
    OUTLOOK_CLIENT_ID: str = os.getenv("OUTLOOK_CLIENT_ID", "")
    OUTLOOK_CLIENT_SECRET: str = os.getenv("OUTLOOK_CLIENT_SECRET", "")
    TEAMS_CLOUD_WEBHOOK_URL: str = os.getenv("TEAMS_CLOUD_WEBHOOK_URL", "")
    TEAMS_CLOUD_CLIENT_STATE_SECRET: str = os.getenv("TEAMS_CLOUD_CLIENT_STATE_SECRET", "")

    # Calendar integration
    CALENDAR_ENCRYPTION_KEY: str = os.getenv("CALENDAR_ENCRYPTION_KEY", "")
    CALENDAR_WEBHOOK_BASE_URL: str = os.getenv("CALENDAR_WEBHOOK_BASE_URL", "")
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")

    # Google Meet early-join (Pub/Sub topic for Workspace Events notifications)
    GOOGLE_MEET_PUBSUB_TOPIC: str = os.getenv("GOOGLE_MEET_PUBSUB_TOPIC", "")

    # Storage
    STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "local")
    DATA_DIR: str = os.getenv("DATA_DIR", "./data")
    S3_BUCKET: str = os.getenv("S3_BUCKET", "")
    S3_ENDPOINT_URL: str = os.getenv("S3_ENDPOINT_URL", "")
    S3_REGION: str = os.getenv("S3_REGION", "us-east-1")

    # BotWorker recordings directory (host path where bot containers write output).
    # In local dev this is the bind-mounted directory; in prod this may be an
    # EFS/NFS mount or an S3 path — the webhook handler reads pipeline results
    # from here and copies them into the main storage via the storage abstraction.
    BOT_RECORDINGS_DIR: str = os.getenv("BOT_RECORDINGS_DIR", "../BotWorker/recordings")

    @property
    def is_dev(self) -> bool:
        return self.ENVIRONMENT == "development"


settings = Settings()
