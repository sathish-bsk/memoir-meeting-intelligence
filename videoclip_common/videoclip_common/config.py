"""
Base configuration for all VideoClip services.
Each service subclasses BaseServiceSettings with its own defaults.
"""

import os


class BaseServiceSettings:
    """Common settings shared by all services. Read from env vars."""

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "production")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "20"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    DB_POOL_RECYCLE: int = int(os.getenv("DB_POOL_RECYCLE", "3600"))

    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Inter-service auth
    INTERNAL_SERVICE_KEY: str = os.getenv("INTERNAL_SERVICE_KEY", "")

    # JWT (ES256 — public key only, for decode in downstream services)
    JWT_PUBLIC_KEY_PATH: str = os.getenv("JWT_PUBLIC_KEY_PATH", "./keys/public.pem")
    JWT_ISSUER: str = "videoclip-auth"
    JWT_AUDIENCE: str = "videoclip"
    JWT_ALGORITHM: str = "ES256"

    # Service URLs (for inter-service calls)
    AUTH_SERVICE_URL: str = os.getenv("AUTH_SERVICE_URL", "http://localhost:8001")
    CORE_API_URL: str = os.getenv("CORE_API_URL", "http://localhost:8005")
    BILLING_SERVICE_URL: str = os.getenv("BILLING_SERVICE_URL", "http://localhost:8003")
    NOTIFICATION_SERVICE_URL: str = os.getenv("NOTIFICATION_SERVICE_URL", "http://localhost:8002")
    CRM_SERVICE_URL: str = os.getenv("CRM_SERVICE_URL", "http://localhost:8004")
    ORCHESTRATOR_URL: str = os.getenv("ORCHESTRATOR_URL", "http://localhost:4000")

    # Storage
    STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "local")  # "local" or "s3"
    DATA_DIR: str = os.getenv("DATA_DIR", "./data")
    S3_BUCKET: str = os.getenv("S3_BUCKET", "")
    S3_ENDPOINT_URL: str = os.getenv("S3_ENDPOINT_URL", "")  # MinIO URL for dev
    S3_REGION: str = os.getenv("S3_REGION", "us-east-1")

    @property
    def is_dev(self) -> bool:
        return self.ENVIRONMENT == "development"
