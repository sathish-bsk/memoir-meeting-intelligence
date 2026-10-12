"""
Teams subscription auto-renewer.
Graph API subscriptions for callRecords expire in ≤3 days and must be renewed.
This runs periodically from the scheduler loop in main.py.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text as sa_text

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SUBSCRIPTION_MAX_HOURS = 71  # just under 3 days


async def _get_app_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """Get app-level access token via client credentials flow."""
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            token_url,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def _renew_subscription(app_token: str, subscription_id: str) -> datetime:
    """PATCH subscription to extend its expiry. Returns new expiry datetime."""
    new_expiry = datetime.now(timezone.utc) + timedelta(hours=SUBSCRIPTION_MAX_HOURS)
    expiry_str = new_expiry.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")

    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"{GRAPH_BASE}/subscriptions/{subscription_id}",
            json={"expirationDateTime": expiry_str},
            headers={"Authorization": f"Bearer {app_token}"},
        )
        resp.raise_for_status()

    return new_expiry


async def renew_expiring_subscriptions(db: AsyncSession) -> int:
    """Renew Teams subscriptions expiring within the next 24 hours.

    Called periodically from the scheduler loop. Returns count of renewed subs.
    """
    from app.core.config import settings

    if not settings.OUTLOOK_CLIENT_ID or not settings.OUTLOOK_CLIENT_SECRET:
        return 0

    cutoff = datetime.now(timezone.utc) + timedelta(hours=24)

    # Find connections with subscriptions expiring soon
    result = await db.execute(
        sa_text(
            "SELECT id, tenant_id, subscription_id, subscription_expiry "
            "FROM recording_connections "
            "WHERE provider = 'teams' "
            "  AND subscription_id IS NOT NULL "
            "  AND tenant_id IS NOT NULL "
            "  AND (subscription_expiry IS NULL OR subscription_expiry < :cutoff)"
        ),
        {"cutoff": cutoff},
    )
    rows = result.fetchall()

    if not rows:
        return 0

    renewed = 0
    for row in rows:
        conn_id, tenant_id, subscription_id, current_expiry = row
        try:
            app_token = await _get_app_token(
                tenant_id,
                settings.OUTLOOK_CLIENT_ID,
                settings.OUTLOOK_CLIENT_SECRET,
            )
            new_expiry = await _renew_subscription(app_token, subscription_id)

            await db.execute(
                sa_text(
                    "UPDATE recording_connections "
                    "SET subscription_expiry = :expiry "
                    "WHERE id = :id"
                ),
                {"expiry": new_expiry, "id": str(conn_id)},
            )
            await db.commit()
            renewed += 1
            logger.info(
                f"Renewed Teams subscription {subscription_id} "
                f"for tenant {tenant_id}, new expiry: {new_expiry.isoformat()}"
            )
        except Exception as e:
            logger.error(
                f"Failed to renew Teams subscription {subscription_id} "
                f"for tenant {tenant_id}: {e}"
            )

    return renewed
