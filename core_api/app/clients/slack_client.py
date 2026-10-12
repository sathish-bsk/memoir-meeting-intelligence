"""
HTTP client for sending Slack messages via integration_service internal endpoint.
"""

import logging
import uuid

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

INTEGRATION_SERVICE_URL = settings.INTEGRATION_SERVICE_URL


async def send_slack_message(
    user_id: uuid.UUID,
    text: str,
    blocks: list[dict] | None = None,
) -> bool:
    """Send a Slack message for the given user via integration_service.

    Returns True on success, False on failure. Never raises.
    """
    url = f"{INTEGRATION_SERVICE_URL}/integrations/internal/slack/send-message"
    payload = {
        "user_id": str(user_id),
        "text": text,
    }
    if blocks:
        payload["blocks"] = blocks

    headers = {"X-Service-Key": settings.INTERNAL_SERVICE_KEY}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                logger.warning(f"integration_service slack send returned {resp.status_code}: {resp.text}")
                return False
            logger.debug(f"Slack message sent for user {user_id}")
            return True
    except Exception as e:
        logger.error(f"Failed to reach integration_service for Slack: {e}")
        return False
