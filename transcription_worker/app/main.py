"""
Transcription Worker — polls the transcription_jobs table and dispatches
jobs to TranscriptionBase for processing.
"""
import asyncio
import logging
import signal
import sys

from app.config import POLL_INTERVAL_SECONDS
from app.db import init_db, close_db, pick_next_job, recover_stale_jobs
from app.dispatcher import dispatch_job, handle_dispatch_failure, DispatchError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("transcription_worker")

_running = True


def _handle_signal(signum, frame):
    global _running
    logger.info(f"Received signal {signum}, shutting down...")
    _running = False


async def main():
    global _running

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("Starting transcription worker")
    await init_db()

    # Recover any stale in_progress jobs on startup
    recovered = await recover_stale_jobs()
    if recovered:
        logger.info(f"Recovered {recovered} stale jobs on startup")

    logger.info(f"Polling every {POLL_INTERVAL_SECONDS}s for waiting jobs...")

    while _running:
        try:
            job = await pick_next_job()
            if job:
                try:
                    await dispatch_job(job)
                except DispatchError as e:
                    params = job.get("params", {})
                    if isinstance(params, str):
                        import json
                        params = json.loads(params)
                    max_retries = params.get("max_retries", 3)
                    await handle_dispatch_failure(job, str(e), max_retries)
            else:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except Exception as e:
            logger.exception(f"Unexpected error in poll loop: {e}")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    await close_db()
    logger.info("Worker shut down cleanly")


if __name__ == "__main__":
    asyncio.run(main())
