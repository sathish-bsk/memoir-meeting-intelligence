"""
Database operations for the transcription worker job queue.
"""
import logging
from typing import Optional

import asyncpg

from app.config import DATABASE_URL, MAX_CONCURRENT_JOBS

logger = logging.getLogger("transcription_worker.db")

_pool: Optional[asyncpg.Pool] = None


async def init_db() -> None:
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=5)
    logger.info("Worker DB pool initialized")


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def pick_next_job() -> Optional[dict]:
    """Atomically claim the next waiting job.

    Only picks a job if no other job is currently in_progress,
    ensuring sequential processing (one at a time).
    Uses SELECT FOR UPDATE SKIP LOCKED to safely handle concurrent workers.
    """
    if not _pool:
        raise RuntimeError("DB pool not initialized")

    async with _pool.acquire() as conn:
        async with conn.transaction():
            # Don't pick a new job if one is already being processed
            in_progress = await conn.fetchval(
                "SELECT count(*) FROM transcription_jobs WHERE status = 'in_progress'"
            )
            if in_progress and in_progress >= MAX_CONCURRENT_JOBS:
                return None

            row = await conn.fetchrow(
                """
                SELECT * FROM transcription_jobs
                WHERE status = 'waiting'
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """
            )
            if not row:
                return None

            await conn.execute(
                """
                UPDATE transcription_jobs
                SET status = 'in_progress', started_at = now()
                WHERE id = $1
                """,
                row["id"],
            )

    logger.info(f"Claimed job {row['id']}")
    return dict(row)


async def recover_stale_jobs() -> int:
    """Reset in_progress jobs that have been stuck for too long."""
    from app.config import STALE_JOB_TIMEOUT_MINUTES

    if not _pool:
        raise RuntimeError("DB pool not initialized")

    result = await _pool.execute(
        """
        UPDATE transcription_jobs
        SET status = 'waiting', started_at = NULL
        WHERE status = 'in_progress'
          AND started_at < now() - ($1 || ' minutes')::interval
        """,
        str(STALE_JOB_TIMEOUT_MINUTES),
    )
    # result is like "UPDATE N"
    count = int(result.split()[-1]) if result else 0
    if count > 0:
        logger.warning(f"Recovered {count} stale jobs")
    return count
