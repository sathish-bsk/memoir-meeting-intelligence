"""
Job processor API endpoints — called by the transcription worker service.
"""
import asyncio
import logging
import os

from fastapi import APIRouter, HTTPException

from app.job_processor.db import get_job
from app.job_processor.processor import process_job

logger = logging.getLogger("job_processor.router")

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# Limit concurrent transcription jobs to avoid resource contention (torch/VAD deadlocks)
_max_concurrent = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))
_semaphore = asyncio.Semaphore(_max_concurrent)


async def _process_with_semaphore(job_id: str):
    """Run process_job with concurrency limit."""
    async with _semaphore:
        await process_job(job_id)


@router.post("/{job_id}/process", status_code=202)
async def trigger_process(job_id: str):
    """Accept a job for processing. Returns 202 immediately; processing runs in background."""
    job = await get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")

    if job["status"] != "in_progress":
        raise HTTPException(
            409,
            f"Job {job_id} status is '{job['status']}', expected 'in_progress'",
        )

    # Start processing in background (concurrency-limited)
    asyncio.create_task(_process_with_semaphore(job_id))

    return {"status": "accepted", "job_id": job_id}


@router.get("/{job_id}/status")
async def get_job_status(job_id: str):
    """Return the current status of a transcription job."""
    job = await get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")

    return {
        "job_id": str(job["id"]),
        "status": job["status"],
        "retry_count": job["retry_count"],
        "error_message": job["error_message"],
        "created_at": job["created_at"].isoformat() if job["created_at"] else None,
        "started_at": job["started_at"].isoformat() if job["started_at"] else None,
        "completed_at": job["completed_at"].isoformat() if job["completed_at"] else None,
        "failed_at": job["failed_at"].isoformat() if job["failed_at"] else None,
    }
