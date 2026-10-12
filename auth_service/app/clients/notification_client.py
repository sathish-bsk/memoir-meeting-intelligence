"""
HTTP client for the Notification Service.
Sends transactional emails via POST /notifications/internal/send-email.
"""

import logging
from typing import Any, Dict

import httpx

from app.core.config import settings

logger = logging.getLogger("auth.notification_client")


async def send_email(to: str, template: str, data: Dict[str, Any]) -> None:
    """Send an email via the notification service. Fails silently (logs errors)."""
    url = f"{settings.NOTIFICATION_SERVICE_URL}/notifications/internal/send-email"
    payload = {"to": to, "template": template, "data": data}
    headers = {"X-Service-Key": settings.INTERNAL_SERVICE_KEY}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    f"Notification service returned {resp.status_code}: {resp.text}"
                )
            else:
                logger.debug(f"Email queued: template={template} to={to}")
    except Exception as e:
        logger.error(f"Failed to reach notification service: {e}")


async def send_verification_email(email: str, name: str, token: str) -> None:
    """Send email verification link."""
    link = f"{settings.BASE_URL}/verify-email?token={token}"
    await send_email(email, "email_verification", {"name": name, "link": link})


async def send_email_change_verification(email: str, name: str, token: str) -> None:
    """Send email change verification link to the new email."""
    link = f"{settings.BASE_URL}/verify-email-change?token={token}"
    await send_email(email, "email_change_verification", {"name": name, "link": link})


async def send_password_reset_email(email: str, name: str, token: str) -> None:
    """Send password reset link."""
    link = f"{settings.BASE_URL}/reset-password?token={token}"
    await send_email(email, "password_reset", {"name": name, "link": link})
