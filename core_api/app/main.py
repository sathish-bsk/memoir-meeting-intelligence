"""
VideoClip Core API — Product backend for meetings, transcripts, intelligence.
"""

import asyncio
import logging
import signal
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.core.database import engine

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("core_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("Core API starting up")

    # Warn about missing critical config
    if not settings.INTERNAL_SERVICE_KEY:
        logger.warning("INTERNAL_SERVICE_KEY is empty — transcription webhooks will fail with 403")

    # Store settings on app state so videoclip_common can access them
    app.state.settings = settings

    # Graceful shutdown on SIGTERM
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, lambda: (_ for _ in ()).throw(SystemExit(0)))
    except NotImplementedError:
        pass

    # Start scheduled job polling (1-minute interval)
    scheduler_task = asyncio.create_task(_scheduler_loop())

    yield

    # Stop scheduler
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass

    logger.info("Core API shutting down")
    await engine.dispose()


async def _scheduler_loop():
    """Poll scheduled_jobs table every 60 seconds for due jobs."""
    from app.core.database import async_session
    from app.services.job_scheduler import check_and_run_due_jobs
    from app.services.teams_subscription_renewer import renew_expiring_subscriptions

    # Wait 30 seconds after startup before first poll
    await asyncio.sleep(30)

    _teams_renewal_counter = 0
    _orphan_cleanup_counter = 0
    _channel_renewal_counter = 0
    _meeting_sub_cleanup_counter = 0
    _web_recorder_sweeper_counter = 0

    while True:
        try:
            async with async_session() as session:
                processed = await check_and_run_due_jobs(session)
                if processed:
                    logger.info(f"Scheduler: executed {processed} due job(s)")
        except Exception as e:
            logger.error(f"Scheduler poll error: {e}")

        # Auto-schedule bots for upcoming calendar events (every 60s)
        try:
            from app.services.calendar_auto_scheduler import check_and_schedule_upcoming_bots
            async with async_session() as session:
                scheduled = await check_and_schedule_upcoming_bots(session)
                if scheduled:
                    logger.info(f"Auto-scheduled {scheduled} bot(s) for calendar events")
        except Exception as e:
            logger.error(f"Calendar auto-scheduler error: {e}")

        # Create meeting event subscriptions for upcoming Teams/Meet events (every 60s)
        try:
            from app.services.teams_meeting_subscriptions import create_subscriptions_for_upcoming_teams_events
            async with async_session() as session:
                teams_subs = await create_subscriptions_for_upcoming_teams_events(session)
                if teams_subs:
                    logger.info(f"Created {teams_subs} Teams meeting event subscription(s)")
        except Exception as e:
            logger.error(f"Teams meeting subscription creation error: {e}")

        try:
            from app.services.meet_event_subscriptions import create_subscriptions_for_upcoming_meet_events
            async with async_session() as session:
                meet_subs = await create_subscriptions_for_upcoming_meet_events(session)
                if meet_subs:
                    logger.info(f"Created {meet_subs} Meet event subscription(s)")
        except Exception as e:
            logger.error(f"Meet subscription creation error: {e}")

        # Proactive calendar OAuth token refresh (every 60s). Pulls up to
        # BATCH_SIZE due rows via the partial index on next_refresh_at.
        # See app/services/calendar_token_refresher.py for the cron query
        # + failure classification. Sub-ms per pass regardless of total
        # connection count; opens its own session per refresh row.
        try:
            from app.services.calendar_token_refresher import run_refresh_pass
            await run_refresh_pass()
        except Exception as e:
            logger.error(f"Calendar token refresh pass error: {e}")

        # Renew Teams subscriptions every ~2 hours (120 × 60s intervals)
        _teams_renewal_counter += 1
        if _teams_renewal_counter >= 120:
            _teams_renewal_counter = 0
            try:
                async with async_session() as session:
                    renewed = await renew_expiring_subscriptions(session)
                    if renewed:
                        logger.info(f"Renewed {renewed} Teams subscription(s)")
            except Exception as e:
                logger.error(f"Teams subscription renewal error: {e}")

        # Abort orphan tab audio sessions every ~24 hours (1440 × 60s intervals)
        _orphan_cleanup_counter += 1
        if _orphan_cleanup_counter >= 1440:
            _orphan_cleanup_counter = 0
            try:
                from app.core.database import async_session as _as
                from sqlalchemy import text as _sa_text
                from datetime import datetime, timezone, timedelta
                cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                async with _as() as _session:
                    result = await _session.execute(
                        _sa_text(
                            "UPDATE tab_audio_upload_sessions SET status='aborted' "
                            "WHERE status='active' AND created_at < :cutoff"
                        ),
                        {"cutoff": cutoff},
                    )
                    await _session.commit()
                    if result.rowcount:
                        logger.info(f"Aborted {result.rowcount} orphan tab audio session(s)")
            except Exception as e:
                logger.error(f"Orphan tab audio session cleanup error: {e}")

        # Renew calendar watch channels/subscriptions every ~6 hours (360 × 60s)
        _channel_renewal_counter += 1
        if _channel_renewal_counter >= 360:
            _channel_renewal_counter = 0
            try:
                from app.services.calendar_sync import renew_expiring_channels
                async with async_session() as session:
                    renewed = await renew_expiring_channels(session)
                    if renewed:
                        logger.info(f"Renewed {renewed} calendar watch channel(s)")
            except Exception as e:
                logger.error(f"Calendar channel renewal error: {e}")

        # Cleanup expired meeting event subscriptions every ~6 hours
        _meeting_sub_cleanup_counter += 1
        if _meeting_sub_cleanup_counter >= 360:
            _meeting_sub_cleanup_counter = 0
            try:
                from app.services.teams_meeting_subscriptions import cleanup_expired_subscriptions as teams_cleanup
                from app.services.meet_event_subscriptions import cleanup_expired_subscriptions as meet_cleanup
                async with async_session() as session:
                    teams_cleaned = await teams_cleanup(session)
                    meet_cleaned = await meet_cleanup(session)
                    total = teams_cleaned + meet_cleaned
                    if total:
                        logger.info(f"Cleaned up {total} expired meeting event subscription(s)")
            except Exception as e:
                logger.error(f"Meeting subscription cleanup error: {e}")

        # Sweep orphaned web_recorder meetings every 5 min (5 × 60s).
        # Catches cancellations where the page couldn't call the cancel
        # endpoint (tab closed during prompt, sendBeacon dropped, etc.)
        _web_recorder_sweeper_counter += 1
        if _web_recorder_sweeper_counter >= 5:
            _web_recorder_sweeper_counter = 0
            try:
                from app.services.web_recorder_sweeper import sweep_orphan_web_recorder_meetings
                async with async_session() as session:
                    await sweep_orphan_web_recorder_meetings(session)
            except Exception as e:
                logger.error(f"Web recorder sweeper error: {e}")

        await asyncio.sleep(60)


app = FastAPI(
    title="VideoClip Core API",
    description="Product backend — meetings, transcripts, intelligence, teams",
    version="1.0.0",
    lifespan=lifespan,
    redirect_slashes=False,
    docs_url="/api/docs" if settings.is_dev else None,
    redoc_url=None,
)

# Add middleware
from videoclip_common.middleware import RequestIDMiddleware  # noqa: E402
from app.middleware.request_timing import RequestTimingMiddleware  # noqa: E402

# P3.2 (2026-05-31): RequestTimingMiddleware MUST be added BEFORE
# RequestIDMiddleware so it wraps OUTSIDE — the contextvar-backed
# metrics dict needs to be set before any inner middleware (or route
# handler) can do DB work. Starlette runs middlewares in REVERSE
# add_middleware() order on the way in, so the last-added is the
# outermost. We want RequestTimingMiddleware outermost.
app.add_middleware(RequestIDMiddleware)
app.add_middleware(RequestTimingMiddleware)

# Import and register API routers
from app.api.teams import router as teams_router  # noqa: E402
from app.api.meetings import router as meetings_router  # noqa: E402
from app.api.webhooks import router as webhooks_router  # noqa: E402
from app.api.read_endpoints import router as read_router, speaker_router  # noqa: E402
from app.api.search import router as search_router  # noqa: E402
from app.api.dashboard import router as dashboard_router  # noqa: E402
from app.api.calendar import router as calendar_router  # noqa: E402
from app.api.zoom_webhooks import router as zoom_webhooks_router  # noqa: E402
from app.api.teams_webhooks import router as teams_webhooks_router  # noqa: E402
from app.api.intelligence import router as intelligence_router  # noqa: E402
from app.api.notifications import router as notifications_router  # noqa: E402
from app.api.shared import router as shared_router  # noqa: E402
from app.api.crm import router as crm_router, internal_router as crm_internal_router  # noqa: E402
from app.api.meetings_internal import internal_router as meetings_internal_router  # noqa: E402
from app.api.admin_dashboard import router as admin_dashboard_router  # noqa: E402
from app.api.tab_audio import router as tab_audio_router  # noqa: E402
from app.api.calendar_webhooks import router as calendar_webhooks_router  # noqa: E402
from app.api.calendar_health_api import router as calendar_health_router  # noqa: E402

app.include_router(teams_router)
app.include_router(meetings_router)
app.include_router(webhooks_router)
app.include_router(read_router)
app.include_router(search_router)
app.include_router(dashboard_router)
app.include_router(calendar_router)
app.include_router(zoom_webhooks_router)
app.include_router(teams_webhooks_router)
app.include_router(speaker_router)
app.include_router(intelligence_router)
app.include_router(notifications_router)
app.include_router(shared_router)
app.include_router(crm_router)
app.include_router(crm_internal_router)
app.include_router(meetings_internal_router)
app.include_router(admin_dashboard_router)
app.include_router(tab_audio_router)
app.include_router(calendar_webhooks_router)
app.include_router(calendar_health_router)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "core-api"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
