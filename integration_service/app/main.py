"""
VideoClip Integration Service — OAuth, calendar sync, and Slack integration.
"""

import asyncio
import logging
import signal
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.core.database import engine
from app.services.slack_events import slack_event_consumer

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("integration_service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("Integration service starting up")

    # Store settings on app state so videoclip_common can access them
    app.state.settings = settings

    # Start Slack event consumer (Redis pub/sub)
    await slack_event_consumer.start()
    logger.info("Slack event consumer started")

    # Graceful shutdown on SIGTERM
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, lambda: (_ for _ in ()).throw(SystemExit(0)))
    except NotImplementedError:
        pass

    yield

    logger.info("Integration service shutting down")
    await slack_event_consumer.stop()
    await engine.dispose()


app = FastAPI(
    title="VideoClip Integration Service",
    description="OAuth, calendar, and Slack integration",
    version="1.0.0",
    lifespan=lifespan,
    redirect_slashes=False,
    docs_url="/integrations/docs" if settings.is_dev else None,
    redoc_url=None,
)

# Add middleware
from videoclip_common.middleware import RequestIDMiddleware  # noqa: E402

app.add_middleware(RequestIDMiddleware)

# Import and register API routers
from app.api.calendar import router as calendar_router  # noqa: E402
from app.api.crm import router as crm_router, internal_router as crm_internal_router  # noqa: E402
from app.api.slack import router as slack_router, internal_router as slack_internal_router  # noqa: E402
from app.api.teams_cloud import router as teams_cloud_router  # noqa: E402
from app.api.zoom import router as zoom_router  # noqa: E402

app.include_router(calendar_router)
app.include_router(crm_router)
app.include_router(crm_internal_router)
app.include_router(slack_router)
app.include_router(slack_internal_router)
app.include_router(teams_cloud_router)
app.include_router(zoom_router)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "integration-service"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
