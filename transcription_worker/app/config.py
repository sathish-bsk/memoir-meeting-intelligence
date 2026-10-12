"""
Transcription worker configuration.
"""
import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://videoclip:videoclipdev@localhost:5432/videoclip_db",
)
TRANSCRIPTION_SERVICE_URL = os.getenv(
    "TRANSCRIPTION_SERVICE_URL",
    "http://localhost:8000",
)
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "5"))
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "1"))
STALE_JOB_TIMEOUT_MINUTES = int(os.getenv("STALE_JOB_TIMEOUT_MINUTES", "30"))
