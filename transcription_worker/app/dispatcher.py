"""
Dispatcher — sends jobs to TranscriptionBase for processing.
"""
import logging

import httpx

from app.config import TRANSCRIPTION_SERVICE_URL

logger = logging.getLogger("transcription_worker.dispatcher")


class DispatchError(Exception):
    pass


async def dispatch_job(job: dict) -> None:
    """Dispatch a claimed job to TranscriptionBase for processing.

    The worker has already set the job to in_progress. TranscriptionBase
    will read the job from the DB and process it.
    """
    job_id = str(job["id"])

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{TRANSCRIPTION_SERVICE_URL}/api/jobs/{job_id}/process",
            )
            if resp.status_code == 202:
                logger.info(f"Job {job_id} dispatched to TranscriptionBase")
            elif resp.status_code == 409:
                logger.warning(f"Job {job_id} conflict: {resp.text}")
            else:
                raise DispatchError(
                    f"TranscriptionBase returned {resp.status_code}: {resp.text[:200]}"
                )
    except httpx.RequestError as e:
        raise DispatchError(f"Failed to reach TranscriptionBase: {e}") from e


async def handle_dispatch_failure(job: dict, error: str, max_retries: int) -> None:
    """Handle a failed dispatch by re-queuing or marking as failed."""
    import asyncpg
    from app.db import _pool

    if not _pool:
        logger.error("DB pool not available for failure handling")
        return

    job_id = str(job["id"])
    current_retry = (job.get("retry_count") or 0) + 1

    if current_retry < max_retries:
        await _pool.execute(
            """
            UPDATE transcription_jobs
            SET status = 'waiting', started_at = NULL,
                error_message = $2, retry_count = $3
            WHERE id = $1
            """,
            job["id"],
            error,
            current_retry,
        )
        logger.warning(f"Job {job_id} re-queued after dispatch failure (retry {current_retry}/{max_retries})")
    else:
        from datetime import datetime, timezone

        await _pool.execute(
            """
            UPDATE transcription_jobs
            SET status = 'failed', failed_at = $2,
                error_message = $3, retry_count = $4
            WHERE id = $1
            """,
            job["id"],
            datetime.now(timezone.utc),
            error,
            current_retry,
        )
        logger.error(f"Job {job_id} permanently failed after {current_retry} dispatch attempts")
