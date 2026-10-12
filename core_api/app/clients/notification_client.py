"""
HTTP client for the Notification Service — sends transactional emails.
"""

import logging
import secrets
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings
from app.services.email_renderer import (
    build_external_email,
    build_internal_email,
    build_recorder_email,
)

logger = logging.getLogger(__name__)


async def send_email(
    to: str,
    template: str,
    data: Dict[str, Any],
    trigger_type: str = "system",
    triggered_by: Optional[str] = None,
) -> bool:
    """Send an email via the notification service.

    Returns True on success (HTTP 2xx), False on HTTP 4xx/5xx, network error,
    or service unavailable. Existing fire-and-forget callers are unaffected
    (Python ignores unused return values). The fast-summary email gate relies
    on this boolean to decide whether to set meetings.summary_email_sent_at.
    """
    url = f"{settings.NOTIFICATION_SERVICE_URL}/notifications/internal/send-email"
    payload: Dict[str, Any] = {
        "to": to,
        "template": template,
        "data": data,
        "trigger_type": trigger_type,
    }
    if triggered_by:
        payload["triggered_by"] = triggered_by
    headers = {"X-Service-Key": settings.INTERNAL_SERVICE_KEY}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                logger.warning(f"Notification service returned {resp.status_code}: {resp.text}")
                return False
            logger.debug(f"Email queued: template={template} to={to}")
            return True
    except Exception as e:
        logger.error(f"Failed to reach notification service: {e}")
        return False


async def send_calendar_disconnected_email(
    user_id: str,
    provider: str,
    provider_email: Optional[str],
) -> None:
    """Phase 2 Track B (notetaker-email plan §4.5 layer B4).

    Fired the first time a user's calendar connection is detected as
    `token_invalid`. The notification service template renders a
    one-click reconnect link. Best-effort — failures are logged but
    do not surface to the caller (the in-app banner is the durable
    signal; email is the prompt to act).
    """
    # Look up the user's email via the auth service.
    from app.clients.auth_client import batch_get_users
    try:
        users = await batch_get_users([user_id])
        info = users.get(user_id) or {}
    except Exception as e:
        logger.warning("Failed to look up user %s for disconnect email: %s", user_id, e)
        return
    to_email = info.get("email")
    if not to_email:
        return
    provider_label = "Google Calendar" if provider == "google" else "Microsoft Outlook"
    await send_email(
        to=to_email,
        template="calendar_disconnected",
        data={
            "user_name": info.get("name") or "",
            "provider_label": provider_label,
            "provider_email": provider_email or "",
            "reconnect_url": "/settings/integrations",
        },
        trigger_type="calendar_health",
    )


async def send_join_approved_email(
    email: str,
    user_name: Optional[str],
    team_name: str,
    approver_user_id: Optional[str] = None,
) -> None:
    """Send an email notifying the user their join request was approved."""
    await send_email(
        to=email,
        template="join_request_approved",
        data={
            "name": user_name or "there",
            "team_name": team_name,
            "link": f"{settings.FRONTEND_URL}/home",
        },
        trigger_type="user",
        triggered_by=approver_user_id,
    )


async def send_join_declined_email(
    email: str,
    user_name: Optional[str],
    team_name: str,
    decliner_user_id: Optional[str] = None,
) -> None:
    """Send an email notifying the user their join request was declined."""
    await send_email(
        to=email,
        template="join_request_declined",
        data={
            "name": user_name or "there",
            "team_name": team_name,
            "link": f"{settings.FRONTEND_URL}/home",
        },
        trigger_type="user",
        triggered_by=decliner_user_id,
    )


async def send_team_invite_email(
    email: str,
    inviter_name: str,
    team_name: str,
    invite_code: str,
    inviter_user_id: Optional[str] = None,
) -> None:
    """Send a team invite email with the invite code."""
    await send_email(
        to=email,
        template="team_invite",
        data={
            "inviter_name": inviter_name,
            "team_name": team_name,
            "invite_code": invite_code,
            "link": f"{settings.FRONTEND_URL}/join?code={invite_code}",
        },
        trigger_type="user",
        triggered_by=inviter_user_id,
    )


async def send_meeting_summary_email(
    to_email: str,
    recipient_type: str,  # "recorder" | "internal" | "external"
    recipient_name: str,
    meeting_id: str,
    meeting_title: str,
    meeting_date: Optional[str],
    duration_seconds: Optional[int],
    platform: Optional[str],
    summary_data: Dict[str, Any],
    created_by: str,
    owner_name: Optional[str] = None,
    recording_source: Optional[str] = None,
    meeting_ended_at: Optional[str] = None,
    meeting_created_at: Optional[str] = None,
    recipient_email: Optional[str] = None,
    unsubscribe_url: Optional[str] = None,
    team_id: Optional[str] = None,
    delivery_minutes: Optional[int] = None,
) -> bool:
    """Send a meeting summary email (recorder, internal, or external variant).

    Uses the email_renderer module to build fully-rendered HTML, then sends
    via the notification service using the 'meeting_summary_v2' template which
    passes through the pre-built body unchanged.
    """
    # Extract participant count from summary_data metadata
    call_meta = summary_data.get("meeting", {}) if summary_data else {}
    participants = call_meta.get("participants", [])
    participant_count = len(participants) if isinstance(participants, list) else 0

    if recording_source == "upload":
        recorded_label = f"Uploaded by {owner_name}" if owner_name else "Uploaded"
    else:
        recorded_label = f"Recorded by {owner_name}" if owner_name else ""

    meeting = {
        "title": meeting_title or "Untitled Meeting",
        "date": meeting_date,
        "duration_seconds": duration_seconds,
        "platform": platform,
        "recorded_by": recorded_label,
        "participant_count": participant_count,
        "org_name": "",
        "ended_at": meeting_ended_at,
        "created_at": meeting_created_at,
    }

    call_url = f"{settings.FRONTEND_URL}/meetings/{meeting_id}"
    edit_url = f"{settings.FRONTEND_URL}/meetings/{meeting_id}/edit"
    transcript_url = f"{settings.FRONTEND_URL}/meetings/{meeting_id}/transcript"

    # Generate unsubscribe token for external recipients
    if recipient_type == "external" and not unsubscribe_url:
        token = secrets.token_urlsafe(32)
        unsubscribe_url = f"{settings.FRONTEND_URL}/unsubscribe?token={token}"

    # Default unsubscribe URL for non-external
    if not unsubscribe_url:
        unsubscribe_url = f"{settings.FRONTEND_URL}/settings/notifications"

    # Build HTML via the appropriate renderer
    if recipient_type == "recorder":
        subject, html_body = build_recorder_email(
            meeting=meeting,
            summary_data=summary_data or {},
            call_url=call_url,
            edit_url=edit_url,
            transcript_url=transcript_url,
            unsubscribe_url=unsubscribe_url,
            delivery_minutes=delivery_minutes,
        )
    elif recipient_type == "internal":
        subject, html_body = build_internal_email(
            meeting=meeting,
            summary_data=summary_data or {},
            recipient_email=recipient_email or to_email,
            recipient_name=recipient_name or "there",
            call_url=call_url,
            unsubscribe_url=unsubscribe_url,
            delivery_minutes=delivery_minutes,
        )
    elif recipient_type == "external":
        subject, html_body = build_external_email(
            meeting=meeting,
            summary_data=summary_data or {},
            recipient_email=recipient_email or to_email,
            recipient_name=recipient_name or "there",
            call_url=call_url,
            unsubscribe_url=unsubscribe_url,
            delivery_minutes=delivery_minutes,
        )
    else:
        logger.error(f"Unknown recipient_type: {recipient_type}")
        return False

    return await send_email(
        to=to_email,
        template="meeting_summary_v2",
        data={
            "subject": subject,
            "body": html_body,
            "unsubscribe_url": unsubscribe_url,
        },
        trigger_type="system",
        triggered_by=created_by,
    )
