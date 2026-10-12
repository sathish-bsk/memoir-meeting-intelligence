"""
Central notification dispatcher — routes events to email, slack, and in-app log.

Usage:
    await dispatch_notification(
        db, "transcription_complete", user_id, team_id,
        title="Call processed: Demo Call",
        body="Transcript and AI analysis are ready.",
        link="/meetings/{id}",
        email_template="meeting_summary",
        email_data={...},
        slack_text="*Demo Call* is ready",
    )
"""

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import NotificationLog, NotificationPreference

logger = logging.getLogger(__name__)


async def dispatch_notification(
    db: AsyncSession,
    event_type: str,
    user_id: uuid.UUID,
    team_id: uuid.UUID,
    title: str,
    body: str,
    link: str | None = None,
    metadata: dict[str, Any] | None = None,
    # Email-specific
    email_to: str | None = None,
    email_template: str | None = None,
    email_data: dict[str, Any] | None = None,
    # Slack-specific
    slack_text: str | None = None,
    slack_blocks: list[dict] | None = None,
) -> None:
    """Dispatch a notification to all enabled channels for the user.

    1. Always creates an in_app log entry (powers the bell icon).
    2. Checks user preferences for email/slack.
    3. Sends via enabled channels.
    """
    meta = metadata or {}

    # 1. Always create in-app notification log entry
    in_app_log = NotificationLog(
        user_id=user_id,
        team_id=team_id,
        event_type=event_type,
        title=title,
        body=body,
        link=link,
        channel="in_app",
        status="sent",
        metadata_=meta,
    )
    db.add(in_app_log)
    await db.flush()

    # 2. Query preferences
    result = await db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id,
            NotificationPreference.team_id == team_id,
            NotificationPreference.event_type == event_type,
        )
    )
    prefs = {row.channel: row.enabled for row in result.scalars().all()}

    # Default: email enabled, slack disabled (if no prefs exist yet)
    email_enabled = prefs.get("email", True)
    slack_enabled = prefs.get("slack", False)

    # 3. Send email if enabled
    if email_enabled and email_template and email_to:
        email_status = "sent"
        try:
            from app.clients.notification_client import send_email
            await send_email(
                to=email_to,
                template=email_template,
                data=email_data or {},
                trigger_type="system",
                triggered_by=str(user_id),
            )
        except Exception as e:
            email_status = "failed"
            logger.error(f"Email dispatch failed for {event_type} to {email_to}: {e}")

        email_log = NotificationLog(
            user_id=user_id,
            team_id=team_id,
            event_type=event_type,
            title=title,
            body=body,
            link=link,
            channel="email",
            status=email_status,
            metadata_=meta,
        )
        db.add(email_log)

    # 4. Send Slack if enabled
    if slack_enabled and slack_text:
        slack_status = "sent"
        try:
            from app.clients.slack_client import send_slack_message
            success = await send_slack_message(user_id, slack_text, slack_blocks)
            if not success:
                slack_status = "failed"
        except Exception as e:
            slack_status = "failed"
            logger.error(f"Slack dispatch failed for {event_type} to user {user_id}: {e}")

        slack_log = NotificationLog(
            user_id=user_id,
            team_id=team_id,
            event_type=event_type,
            title=title,
            body=body,
            link=link,
            channel="slack",
            status=slack_status,
            metadata_=meta,
        )
        db.add(slack_log)

    await db.flush()
