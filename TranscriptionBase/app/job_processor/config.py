"""
Job processor configuration — loaded from environment variables.
"""
import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://videoclip:videoclipdev@localhost:5432/videoclip_db",
)
BOT_RECORDINGS_DIR = os.getenv("BOT_RECORDINGS_DIR", "../BotWorker/recordings")
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local")
DATA_DIR = os.getenv("DATA_DIR", "../core_api/data")
