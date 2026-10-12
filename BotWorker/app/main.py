"""
BotWorker FastAPI application.
Joins Google Meet, Zoom, and Microsoft Teams meetings, records audio,
and sends recordings for transcription via TranscriptionBase.
"""
import asyncio
import os
import re
import signal
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Response

from app.core.config import settings
from app.core.logging import setup_logging, get_job_logger, app_logger
from app.models.enums import BotState
from app.models.schemas import (
    JoinRequest,
    StatusResponse,
    StopRequest,
    HealthResponse,
)
from app.services.phase_log import log_phase, set_job_context

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Register SIGTERM handler + schedule pool-member auto-warm."""
    loop = asyncio.get_running_loop()

    def _handle_sigterm():
        app_logger.info("SIGTERM received, initiating graceful shutdown...")
        if _state["stop_event"]:
            _state["stop_event"].set()

    try:
        loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)
    except NotImplementedError:
        pass  # Signal handlers not supported on some platforms in tests
    # Tier 0: log container_started right before yielding control to FastAPI's
    # serve loop. By this point uvicorn has bound the socket and is about to
    # accept requests.
    log_phase("container_started")

    # Pool pre-warm: when launched as a pool member (env BOT_POOL_MEMBER=1),
    # fire-and-forget the warm sequence so /health flips green immediately
    # (orchestrator gates on /health) and /ready flips green ~12-15 s later
    # when Chromium reaches about:blank. Do NOT add a parallel
    # @app.on_event("startup") hook — lifespan and on_event are mutually
    # exclusive entry points, and the current app uses lifespan.
    if os.environ.get("BOT_POOL_MEMBER") == "1":
        asyncio.create_task(_auto_warm_after_grace())

    yield


async def _auto_warm_after_grace():
    """Background warm-up for pool members.

    Sleeps briefly so /health responds before the heavy Chromium launch starts
    (orchestrator pool manager polls /health first as a "container is up"
    sanity check, then polls /ready to confirm warm).
    """
    await asyncio.sleep(0.5)
    try:
        from app.services.prewarm import prewarm_browser
        _state["state"] = BotState.WARMING
        log_phase("warm_starting", trigger="auto")
        await prewarm_browser()
        _state["state"] = BotState.WARM
        log_phase("warm_ready", trigger="auto")
    except Exception as e:
        _state["state"] = BotState.IDLE
        log_phase("warm_failed", error=str(e)[:200], trigger="auto")
        app_logger.warning(f"pool auto-warm failed: {e}")


app = FastAPI(
    title="BotWorker",
    description="Meeting bot that joins Google Meet, Zoom, and Teams meetings and records audio",
    version="2.0.0",
    lifespan=lifespan,
)

# Global state — one meeting per container
_state: dict = {
    "job_id": None,
    "state": BotState.IDLE,
    "meeting_url": None,
    "joined_at": None,
    "chunks_saved": 0,
    "transcript": None,
    "error": None,
    "task": None,  # asyncio.Task for the lifecycle
    "start_time": None,
    "stop_event": None,  # asyncio.Event to signal manual stop
}

# Multi-platform URL patterns
MEETING_URL_PATTERNS = {
    "google_meet": re.compile(r"^https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}"),
    "zoom": re.compile(r"^https://([\w-]+\.)?zoom\.us/(j|wc(/join)?)/\d+"),
    "teams": re.compile(r"^https://(teams\.microsoft\.com|teams\.live\.com)/"),
}


def detect_platform(meeting_url: str) -> str:
    """Detect the meeting platform from the URL. Raises ValueError if unsupported."""
    for platform, pattern in MEETING_URL_PATTERNS.items():
        if pattern.match(meeting_url):
            return platform
    raise ValueError(f"Unsupported meeting URL: {meeting_url}")


def create_bot(meeting_url: str, job_id: str, bot_name: str):
    """Factory: create the right bot subclass based on the meeting URL."""
    from app.services.browser import GoogleMeetBot
    from app.services.zoom_bot import ZoomBot
    from app.services.teams_bot import TeamsBot

    platform = detect_platform(meeting_url)

    if platform == "google_meet":
        return GoogleMeetBot(job_id, bot_name)
    elif platform == "zoom":
        return ZoomBot(job_id, bot_name)
    elif platform == "teams":
        return TeamsBot(job_id, bot_name)
    else:
        raise ValueError(f"No bot implementation for platform: {platform}")


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    return HealthResponse(status="ok", state=_state["state"])


@app.post("/warm")
async def warm():
    """Manually trigger pre-launch of Chromium (idempotent).

    Pool members auto-warm on container startup when BOT_POOL_MEMBER=1, so
    this endpoint exists mainly for explicit re-warm requests and local
    testing of the warm path. Returns 409 if the bot is in a state that
    can't be warmed (e.g., already in a meeting).
    """
    if _state["state"] == BotState.WARM:
        return {"status": "already_warm"}
    if _state["state"] != BotState.IDLE:
        raise HTTPException(
            status_code=409, detail=f"cannot warm from state={_state['state']}"
        )

    _state["state"] = BotState.WARMING
    log_phase("warm_starting", trigger="manual")

    try:
        from app.services.prewarm import prewarm_browser
        await prewarm_browser()
        _state["state"] = BotState.WARM
        log_phase("warm_ready", trigger="manual")
        return {"status": "warm"}
    except Exception as e:
        _state["state"] = BotState.IDLE
        log_phase("warm_failed", error=str(e)[:200], trigger="manual")
        raise HTTPException(status_code=500, detail=f"prewarm failed: {e}")


@app.get("/ready")
async def ready():
    """Pool-manager readiness gate: 200 when fully warm, 503 otherwise.

    Distinct from /health (which returns 200 once uvicorn is up). The pool
    manager polls /ready to know which warm bots are claimable; the
    one-shot dispatch path uses /health.
    """
    if _state["state"] == BotState.WARM:
        return {"state": "warm", "ready": True}
    return Response(
        status_code=503,
        content=f'{{"state":"{_state["state"]}","ready":false}}',
        media_type="application/json",
    )


@app.get("/status", response_model=StatusResponse)
async def status():
    """Get current bot status."""
    uptime = 0.0
    if _state["start_time"]:
        uptime = time.time() - _state["start_time"]

    return StatusResponse(
        job_id=_state["job_id"],
        state=_state["state"],
        meeting_url=_state["meeting_url"],
        joined_at=_state["joined_at"],
        chunks_saved=_state["chunks_saved"],
        uptime_seconds=round(uptime, 1),
        transcript=_state["transcript"],
        error=_state["error"],
    )


@app.post("/join")
async def join(request: JoinRequest):
    """Start joining a meeting (Google Meet, Zoom, or Teams)."""
    # Pool members arrive in state WARM. WARMING is deliberately excluded —
    # the orchestrator's pool manager only claims entries already in "warm"
    # state, so a /join during WARMING would be a race we want surfaced as
    # 409, not silently accepted.
    if _state["state"] not in (
        BotState.IDLE, BotState.WARM, BotState.COMPLETED, BotState.ERROR
    ):
        raise HTTPException(status_code=409, detail="Bot is busy")

    # Pool-claim path: orchestrator sets auto_exit_after=True when handing
    # a meeting to a pool member that started with AUTO_EXIT=0. Flip
    # AUTO_EXIT on so the container exits after this one meeting, matching
    # today's one-shot cold-bot behavior. No-op for cold bots (which already
    # have AUTO_EXIT=1 from the orchestrator's container env).
    if request.auto_exit_after:
        settings.AUTO_EXIT = True
        app_logger.info("Pool claim: AUTO_EXIT flipped on for this meeting")

    # Tier 0: bind job context to phase_log + emit dispatch_received marker.
    # set_job_context records dispatch_sent_at so every subsequent log_phase
    # call can compute t_since_dispatch_ms (user-perceived latency).
    set_job_context(request.job_id, request.dispatch_sent_at)
    log_phase("dispatch_received", platform_url=request.meeting_url[:80])

    # Validate meeting URL against all supported platforms
    try:
        platform = detect_platform(request.meeting_url)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Unsupported meeting URL. Supported: Google Meet, Zoom, Microsoft Teams",
        )

    # Reset state
    _state["job_id"] = request.job_id
    _state["state"] = BotState.IDLE
    _state["meeting_url"] = request.meeting_url
    _state["joined_at"] = None
    _state["chunks_saved"] = 0
    _state["transcript"] = None
    _state["error"] = None
    _state["start_time"] = time.time()
    _state["stop_event"] = asyncio.Event()

    bot_name = request.bot_name or settings.BOT_NAME
    logger = get_job_logger(request.job_id)
    logger.info(f"Join requested for {request.meeting_url} (platform: {platform}) as '{bot_name}'")

    # Import here to avoid circular imports
    from app.services.lifecycle import run_meeting_lifecycle

    task = asyncio.create_task(
        run_meeting_lifecycle(request, _state, bot_name)
    )
    _state["task"] = task

    # Auto-exit after lifecycle completes (when managed by orchestrator)
    if settings.AUTO_EXIT:
        async def _exit_after_lifecycle():
            await task
            delay = settings.AUTO_EXIT_DELAY_SECONDS
            app_logger.info(f"Lifecycle complete, exiting in {delay}s (AUTO_EXIT=1)")
            await asyncio.sleep(delay)
            import os
            os._exit(0)

        asyncio.create_task(_exit_after_lifecycle())

    return {"status": "accepted", "job_id": request.job_id, "platform": platform}


@app.post("/stop")
async def stop(request: Optional[StopRequest] = None):
    """Stop the current meeting."""
    # Timing instrumentation — added 2026-05-14. The orchestrator's
    # fetch to bot/stop has been observed taking ~6 s in production
    # despite the handler body being trivial. If the event loop is
    # blocked when /stop arrives, [STOP_ENTRY] timestamp will be late
    # relative to the orchestrator's "Sent /stop" log. Compare the
    # ENTRY -> EVENT_SET -> RETURN deltas to see whether the delay
    # is queue wait (entry late) vs handler body slow.
    _ep_t0 = time.monotonic()
    logger_root = get_job_logger(_state.get("job_id") or "unknown")
    logger_root.info(f"[STOP_ENTRY] /stop handler entered at monotonic={_ep_t0:.3f}")

    if _state["state"] in (BotState.IDLE, BotState.COMPLETED):
        raise HTTPException(status_code=400, detail="No active meeting")

    reason = request.reason if request else "manual_stop"
    logger = get_job_logger(_state["job_id"] or "unknown")
    logger.info(f"Stop requested: {reason}")

    # Signal the lifecycle to stop
    if _state["stop_event"]:
        _state["stop_event"].set()
    _ep_t1 = time.monotonic()
    logger.info(f"[STOP_EVENT_SET] stop_event.set() done at +{(_ep_t1 - _ep_t0) * 1000:.0f}ms")

    _ep_t2 = time.monotonic()
    logger.info(f"[STOP_RETURN] /stop returning at +{(_ep_t2 - _ep_t0) * 1000:.0f}ms")
    return {"status": "stopping", "job_id": _state["job_id"]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
