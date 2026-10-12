"""
Configuration management via environment variables.
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
    """Auth Service settings loaded from environment variables."""

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "production")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost")

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://auth_user:authpass@auth-db:5432/auth_db",
    )
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "20"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    DB_POOL_RECYCLE: int = int(os.getenv("DB_POOL_RECYCLE", "3600"))

    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")

    # JWT (ES256)
    JWT_PRIVATE_KEY_PATH: str = os.getenv("JWT_PRIVATE_KEY_PATH", "./keys/private.pem")
    JWT_PUBLIC_KEY_PATH: str = os.getenv("JWT_PUBLIC_KEY_PATH", "./keys/public.pem")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
        os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "15")
    )
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = int(
        os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7")
    )
    JWT_ISSUER: str = "videoclip-auth"
    JWT_AUDIENCE: str = "videoclip"
    JWT_ALGORITHM: str = "ES256"

    # Inter-service auth
    INTERNAL_SERVICE_KEY: str = os.getenv("INTERNAL_SERVICE_KEY", "")

    # Core API
    CORE_API_URL: str = os.getenv("CORE_API_URL", "http://localhost:8005")

    # Notification service
    NOTIFICATION_SERVICE_URL: str = os.getenv("NOTIFICATION_SERVICE_URL", "http://localhost:8002")

    # OAuth — Google
    OAUTH_GOOGLE_CLIENT_ID: str = os.getenv("OAUTH_GOOGLE_CLIENT_ID", "")
    OAUTH_GOOGLE_CLIENT_SECRET: str = os.getenv("OAUTH_GOOGLE_CLIENT_SECRET", "")
    OAUTH_GOOGLE_REDIRECT_URI: str = os.getenv("OAUTH_GOOGLE_REDIRECT_URI", f"{BASE_URL}/auth/oauth/google/callback")

    # OAuth — Microsoft
    OAUTH_MICROSOFT_CLIENT_ID: str = os.getenv("OAUTH_MICROSOFT_CLIENT_ID", "")
    OAUTH_MICROSOFT_CLIENT_SECRET: str = os.getenv("OAUTH_MICROSOFT_CLIENT_SECRET", "")
    OAUTH_MICROSOFT_REDIRECT_URI: str = os.getenv("OAUTH_MICROSOFT_REDIRECT_URI", f"{BASE_URL}/oauth/callback/microsoft")

    # Encryption (Fernet key for OAuth token storage)
    FERNET_KEY: str = os.getenv("FERNET_KEY", "")

    # Password policy (NIST 800-63B)
    PASSWORD_MIN_LENGTH: int = int(os.getenv("PASSWORD_MIN_LENGTH", "8"))
    HIBP_CHECK_ENABLED: bool = os.getenv("HIBP_CHECK_ENABLED", "true").lower() == "true"

    # Rate limiting
    RATE_LIMIT_LOGIN_WINDOW: int = int(os.getenv("RATE_LIMIT_LOGIN_WINDOW", "900"))  # 15 min
    RATE_LIMIT_LOGIN_MAX: int = int(os.getenv("RATE_LIMIT_LOGIN_MAX", "25"))

    @property
    def is_dev(self) -> bool:
        return self.ENVIRONMENT == "development"


settings = Settings()
