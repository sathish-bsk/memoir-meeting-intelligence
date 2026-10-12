"""
Configuration management via environment variables.
"""
import os
from typing import List


class Settings:
    """Application settings loaded from environment variables."""

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # ASR Model (OpenAI Whisper)
    # Options: tiny, base, small, medium, large, large-v2, large-v3
    ASR_MODEL_SIZE: str = os.getenv("ASR_MODEL_SIZE", "base")
    ASR_DEVICE: str = os.getenv("ASR_DEVICE", "cpu")  # cpu or cuda

    # Pingala V1 Universal (ShunyaLabs)
    HUGGINGFACE_HUB_TOKEN: str = os.getenv("HUGGINGFACE_HUB_TOKEN", "")
    PINGALA_MODEL_NAME: str = os.getenv("PINGALA_MODEL_NAME", "shunyalabs/pingala-v1-universal")

    # File limits
    MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "200"))
    MAX_FILE_SIZE_BYTES: int = MAX_FILE_SIZE_MB * 1024 * 1024

    # Timeouts (seconds)
    DOWNLOAD_TIMEOUT: int = int(os.getenv("DOWNLOAD_TIMEOUT", "300"))
    YOUTUBE_TIMEOUT: int = int(os.getenv("YOUTUBE_TIMEOUT", "600"))
    ASR_TIMEOUT: int = int(os.getenv("ASR_TIMEOUT", "1800"))  # 30 min for long files

    # YouTube allowlist
    YOUTUBE_ALLOWED_DOMAINS: List[str] = os.getenv(
        "YOUTUBE_ALLOWED_DOMAINS",
        "youtube.com,youtu.be,www.youtube.com,m.youtube.com"
    ).split(",")

    # YouTube cookies - set to browser name (chrome, firefox, safari, edge) to use browser cookies
    # This helps avoid 403 errors by using your logged-in session
    YOUTUBE_COOKIES_FROM_BROWSER: str = os.getenv("YOUTUBE_COOKIES_FROM_BROWSER", "")

    # Security
    ALLOWED_MEDIA_EXTENSIONS: List[str] = [".mp3", ".mp4", ".m4a", ".wav", ".webm", ".ogg", ".flac"]
    ALLOWED_CONTENT_TYPES: List[str] = [
        "audio/mpeg", "audio/mp4", "audio/x-m4a", "audio/wav", "audio/webm",
        "audio/ogg", "audio/flac", "video/mp4", "video/webm",
        "application/octet-stream"  # fallback for some servers
    ]

    # Temp directory base
    TEMP_DIR_BASE: str = os.getenv("TEMP_DIR_BASE", "/tmp/transcription")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
