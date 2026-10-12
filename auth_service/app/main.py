"""
VideoClip Auth Service — FastAPI application.
User identity, JWT issuance, OAuth, team management, ForwardAuth.
"""

import asyncio
import signal
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.core.logging import setup_logging, app_logger
from app.core.database import engine
from app.dependencies import close_redis

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    app_logger.info("Auth Service starting up")

    # Generate ES256 keys if they don't exist (dev convenience)
    if settings.is_dev:
        from app.core.security import ensure_keypair
        ensure_keypair()

    # Register SIGTERM handler for graceful container shutdown
    loop = asyncio.get_running_loop()

    def _handle_sigterm():
        app_logger.info("SIGTERM received, shutting down gracefully")
        raise SystemExit(0)

    try:
        loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)
    except NotImplementedError:
        pass

    yield

    # Shutdown
    app_logger.info("Auth Service shutting down")
    await close_redis()
    await engine.dispose()


app = FastAPI(
    title="VideoClip Auth Service",
    description="User identity, authentication, and team management",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/auth/docs" if settings.is_dev else None,
    redoc_url=None,
)

# ─── Register Routers ─────────────────────────────────────────────
from app.api.auth import router as auth_router
from app.api.password import router as password_router
from app.api.profile import router as profile_router
from app.api.sessions import router as sessions_router
from app.api.teams import router as teams_router
from app.api.oauth import router as oauth_router
from app.api.internal import router as internal_router
from app.api.permissions import router as permissions_router

app.include_router(auth_router)
app.include_router(password_router)
app.include_router(profile_router)
app.include_router(sessions_router)
app.include_router(teams_router)
app.include_router(oauth_router)
app.include_router(internal_router)
app.include_router(permissions_router)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "auth"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
