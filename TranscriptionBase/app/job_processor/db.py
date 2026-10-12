"""
asyncpg connection pool for the job processor.
"""
import asyncio
import logging
from typing import Optional

import asyncpg

from app.job_processor.config import DATABASE_URL

logger = logging.getLogger("job_processor.db")

_pool: Optional[asyncpg.Pool] = None


async def init_db() -> None:
    global _pool
    dsn = DATABASE_URL
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    last_exc: Optional[BaseException] = None
    for attempt in range(1, 6):
        try:
            _pool = await asyncpg.create_pool(
                dsn, min_size=2, max_size=5, timeout=30
            )
            logger.info(
                "Job processor DB pool initialized (attempt %d)", attempt
            )
            return
        except (asyncio.TimeoutError, OSError, asyncpg.exceptions.PostgresError) as e:
            last_exc = e
            logger.warning(
                "init_db attempt %d failed: %r — retrying in %ds",
                attempt, e, 3,
            )
            await asyncio.sleep(3)
    assert last_exc is not None
    raise last_exc


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Job processor DB pool closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_db() first")
    return _pool


async def get_job(job_id: str) -> Optional[dict]:
    """Fetch a transcription job by ID."""
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM transcription_jobs WHERE id = $1",
        job_id,
    )
    return dict(row) if row else None


async def update_job_status(
    job_id: str,
    status: str,
    *,
    result: Optional[dict] = None,
    error_message: Optional[str] = None,
    retry_count: Optional[int] = None,
) -> None:
    """Update a transcription job's status and optional fields."""
    import json
    from datetime import datetime, timezone

    pool = get_pool()
    now = datetime.now(timezone.utc)

    sets = ["status = $2"]
    vals: list = [job_id, status]
    idx = 3

    if status == "in_progress":
        sets.append(f"started_at = ${idx}")
        vals.append(now)
        idx += 1
    elif status == "completed":
        sets.append(f"completed_at = ${idx}")
        vals.append(now)
        idx += 1
    elif status == "failed":
        sets.append(f"failed_at = ${idx}")
        vals.append(now)
        idx += 1

    if result is not None:
        sets.append(f"result = ${idx}")
        vals.append(json.dumps(result))
        idx += 1

    if error_message is not None:
        sets.append(f"error_message = ${idx}")
        vals.append(error_message)
        idx += 1

    if retry_count is not None:
        sets.append(f"retry_count = ${idx}")
        vals.append(retry_count)
        idx += 1

    query = f"UPDATE transcription_jobs SET {', '.join(sets)} WHERE id = $1"
    await pool.execute(query, *vals)
