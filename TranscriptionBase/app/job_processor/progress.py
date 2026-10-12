"""
Lightweight async Redis helper for reporting job progress.

Stores progress as a Redis hash at `job_progress:{job_id}` with fields
`percent` (0-100) and `label` (human-readable step name).  Keys expire
after 1 hour so stale entries clean themselves up.

All operations fail silently — progress reporting is non-critical and
must never break the transcription pipeline.
"""

import logging
import os
from typing import Optional

import redis.asyncio as redis

logger = logging.getLogger(__name__)

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/1")
PROGRESS_TTL: int = 3600  # 1 hour

_redis_client: Optional[redis.Redis] = None


async def _get_client() -> redis.Redis:
    """Return a lazily-initialised Redis client (singleton)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


async def update_progress(job_id: str, percent: int, label: str = "") -> None:
    """Set progress for a job. Fails silently on Redis errors."""
    try:
        client = await _get_client()
        key = f"job_progress:{job_id}"
        await client.hset(key, mapping={"percent": str(percent), "label": label})
        await client.expire(key, PROGRESS_TTL)
    except Exception:
        logger.warning("Failed to update progress for job %s", job_id, exc_info=True)


async def clear_progress(job_id: str) -> None:
    """Delete the progress key for a job. Fails silently on Redis errors."""
    try:
        client = await _get_client()
        await client.delete(f"job_progress:{job_id}")
    except Exception:
        logger.warning("Failed to clear progress for job %s", job_id, exc_info=True)


_STAGE_NAMES = {
    1: "preprocess",
    4: "enhance",
    7: "fast_summary",
    6: "diarization",
    5: "transcribe",
    3: "ai_analysis",
    2: "crm_sync",
}

STAGE_ORDER = ["preprocess", "enhance", "fast_summary", "diarization", "transcribe", "ai_analysis", "crm_sync"]


async def update_meeting_progress(
    meeting_id: str, operation_type: int, percent: int, label: str = ""
) -> None:
    """Write per-stage progress to a meeting-level Redis key.

    Each stage gets its own 0-100 percent. Frontend renders 5 individual loaders.
    """
    if not meeting_id:
        return
    try:
        client = await _get_client()
        stage = _STAGE_NAMES.get(operation_type, "unknown")
        key = f"meeting_progress:{meeting_id}"
        await client.hset(key, mapping={
            "active_stage": stage,
            f"{stage}_percent": str(min(percent, 100)),
            f"{stage}_label": label,
        })
        await client.expire(key, PROGRESS_TTL)
    except Exception:
        logger.warning("Failed to update meeting progress for %s", meeting_id, exc_info=True)


async def clear_meeting_progress(meeting_id: str) -> None:
    """Delete the meeting-level progress key."""
    if not meeting_id:
        return
    try:
        client = await _get_client()
        await client.delete(f"meeting_progress:{meeting_id}")
    except Exception:
        logger.warning("Failed to clear meeting progress for %s", meeting_id, exc_info=True)
