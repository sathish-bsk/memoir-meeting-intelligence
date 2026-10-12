"""Phase 2 Track B endpoints — calendar connection health surface.

Three endpoints:

  POST /api/calendar/health/refresh
       Called by the frontend on signin. Async-refreshes any of the
       caller's calendar connections within 2 days of expiry. Fire-and-
       forget; the response returns immediately with the count queued.

  GET /api/calendar/health/status
       Returns the current health state for the caller's connections.
       Frontend polls this and shows the prominent "reconnect" banner
       whenever any connection has token_invalid=true and
       auto_record=true.

  POST /api/calendar/health/daily-check  (internal, service-auth)
       Cron entry point. Pings each user connection and flips
       token_invalid on invalid_grant. Service-key gated so it can be
       called from a Kubernetes CronJob / systemd timer.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.calendar import CalendarConnection
from app.services.calendar_health import (
    daily_health_check,
    refresh_expiring_watches_for_user,
)
from videoclip_common.auth import UserContext, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calendar/health", tags=["calendar-health"])


class RefreshResponse(BaseModel):
    queued: int


class HealthStatusItem(BaseModel):
    connection_id: str
    provider: str
    provider_email: Optional[str]
    auto_record: bool
    token_invalid: bool
    token_invalid_at: Optional[str]
    watch_expires_at: Optional[str]


class HealthStatusResponse(BaseModel):
    needs_reconnect: bool
    connections: list[HealthStatusItem]


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_on_signin(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Frontend calls this immediately after a successful signin.

    Triggers async watch renewal for any calendar connections close to
    expiry — closes the gap where the hourly cron may have been down.
    Fire-and-forget; response returns within a few hundred ms even if
    the actual renewals take longer.
    """
    queued = await refresh_expiring_watches_for_user(db, user.user_id)
    return RefreshResponse(queued=queued)


@router.get("/status", response_model=HealthStatusResponse)
async def status(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Current health of the caller's calendar connections.

    Frontend uses `needs_reconnect = true` to render a prominent banner.
    Per-connection rows let a settings page show "Reconnect" buttons
    next to the specific provider that broke.
    """
    import uuid as _uuid
    try:
        user_uuid = _uuid.UUID(user.user_id)
    except (TypeError, ValueError):
        return HealthStatusResponse(needs_reconnect=False, connections=[])

    result = await db.execute(
        select(CalendarConnection).where(CalendarConnection.user_id == user_uuid)
    )
    connections = result.scalars().all()

    items: list[HealthStatusItem] = []
    needs_reconnect = False
    for c in connections:
        items.append(
            HealthStatusItem(
                connection_id=str(c.id),
                provider=c.provider,
                provider_email=c.provider_email,
                auto_record=c.auto_record,
                token_invalid=c.token_invalid,
                token_invalid_at=c.token_invalid_at.isoformat() if c.token_invalid_at else None,
                watch_expires_at=c.watch_expires_at.isoformat() if c.watch_expires_at else None,
            )
        )
        if c.token_invalid and c.auto_record:
            needs_reconnect = True

    return HealthStatusResponse(needs_reconnect=needs_reconnect, connections=items)


@router.post("/daily-check")
async def daily_check(
    x_internal_service_key: str = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Internal cron endpoint — pings every user connection with a
    cheap API call and flips token_invalid on invalid_grant. Gated by
    INTERNAL_SERVICE_KEY so external callers can't trigger the load.
    """
    if not settings.INTERNAL_SERVICE_KEY:
        raise HTTPException(503, "INTERNAL_SERVICE_KEY not configured")
    if x_internal_service_key != settings.INTERNAL_SERVICE_KEY:
        raise HTTPException(401, "Invalid service key")

    summary = await daily_health_check(db)
    return summary
