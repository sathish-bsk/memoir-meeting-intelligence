"""
Cleanup utilities for temp directories.
"""
import shutil
import os
from pathlib import Path
from typing import Optional
from app.core.logging import get_request_logger


def cleanup_temp_dir(temp_dir: Optional[Path], request_id: str) -> None:
    """
    Safely remove a temporary directory and all its contents.
    Always called in finally blocks to ensure cleanup.
    """
    if temp_dir is None:
        return

    logger = get_request_logger(request_id)

    try:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info(f"Cleaned up temp directory: {temp_dir}")
    except Exception as e:
        logger.warning(f"Failed to cleanup temp directory {temp_dir}: {e}")


def ensure_temp_base_exists() -> Path:
    """Ensure the base temp directory exists."""
    from app.core.config import settings
    base = Path(settings.TEMP_DIR_BASE)
    base.mkdir(parents=True, exist_ok=True)
    return base
