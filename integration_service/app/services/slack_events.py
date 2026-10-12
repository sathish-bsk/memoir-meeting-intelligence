"""
Slack event consumer — listens for Redis pub/sub events and posts
Slack notifications to users who have Slack connected with matching
notification preferences.
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import async_session
from app.models.slack import SlackConnection, SlackNotificationSettings
from app.services.slack_service import slack_service
from videoclip_common.events import RedisEventConsumer

logger = logging.getLogger(__name__)


def _format_duration(seconds: int | None) -> str:
    if not seconds:
        return "N/A"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    remaining = minutes % 60
    return f"{hours}h {remaining}m"


def _build_meeting_completed_blocks(event: dict) -> tuple[str, list[dict]]:
    """Build Slack Block Kit message for meeting_completed event."""
    title = event.get("title") or "Untitled Call"
    duration = _format_duration(event.get("duration_seconds"))
    speaker_count = event.get("speaker_count", 0)
    confidence = event.get("overall_confidence_score")
    meeting_id = event.get("meeting_id", "")

    confidence_text = f"{round(confidence * 100)}%" if confidence else "N/A"
    frontend_url = settings.FRONTEND_URL.rstrip("/")
    call_url = f"{frontend_url}/meetings/{meeting_id}"

    fallback_text = f"Call Recorded: {title} ({duration})"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Call Recorded", "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Title:* {title}"},
                {"type": "mrkdwn", "text": f"*Duration:* {duration}"},
                {"type": "mrkdwn", "text": f"*Speakers:* {speaker_count}"},
                {"type": "mrkdwn", "text": f"*Confidence:* {confidence_text}"},
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View in Memoir"},
                    "url": call_url,
                    "style": "primary",
                }
            ],
        },
    ]

    return fallback_text, blocks


def _build_crm_pushed_blocks(event: dict) -> tuple[str, list[dict]]:
    """Build Slack Block Kit message for crm_push_requested event."""
    meeting_id = event.get("meeting_id", "")
    frontend_url = settings.FRONTEND_URL.rstrip("/")
    call_url = f"{frontend_url}/meetings/{meeting_id}"

    fallback_text = "CRM fields pushed for a call"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "CRM Push Completed", "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Discovery fields from a call have been pushed to CRM.",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View Call"},
                    "url": call_url,
                }
            ],
        },
    ]

    return fallback_text, blocks


async def _get_slack_connections_for_team(team_id: str, setting_field: str) -> list[tuple]:
    """Fetch all Slack connections for users in a team who have the given notification enabled.

    Returns list of (encrypted_tokens, channel_id) tuples.
    """
    async with async_session() as db:
        from sqlalchemy import text

        # Get all user_ids in the team
        result = await db.execute(
            text(
                "SELECT user_id FROM team_members "
                "WHERE team_id = :team_id "
                "AND (invite_status = 'accepted' OR invite_status IS NULL)"
            ),
            {"team_id": team_id},
        )
        team_user_ids = [row[0] for row in result.all()]

        if not team_user_ids:
            return []

        # Get Slack connections with notification settings for these users
        query = (
            select(SlackConnection)
            .options(selectinload(SlackConnection.notification_settings))
            .where(SlackConnection.user_id.in_(team_user_ids))
        )
        result = await db.execute(query)
        connections = result.scalars().all()

        targets = []
        for conn in connections:
            ns = conn.notification_settings
            if not ns or not ns.channel_id:
                continue
            if getattr(ns, setting_field, False):
                targets.append((conn.encrypted_tokens, ns.channel_id))

        return targets


async def handle_meeting_completed(event: dict) -> None:
    """Handle meeting_completed event — notify Slack channels."""
    team_id = event.get("team_id")
    if not team_id:
        return

    targets = await _get_slack_connections_for_team(
        team_id, "notify_on_meeting_complete"
    )
    if not targets:
        return

    fallback_text, blocks = _build_meeting_completed_blocks(event)

    for encrypted_tokens, channel_id in targets:
        try:
            slack_service.post_message(encrypted_tokens, channel_id, fallback_text, blocks)
        except Exception as e:
            logger.error(f"Failed to send Slack meeting notification: {e}")


async def handle_crm_push_requested(event: dict) -> None:
    """Handle crm_push_requested event — notify Slack channels."""
    team_id = event.get("team_id")
    if not team_id:
        return

    targets = await _get_slack_connections_for_team(
        team_id, "notify_on_crm_pushed"
    )
    if not targets:
        return

    fallback_text, blocks = _build_crm_pushed_blocks(event)

    for encrypted_tokens, channel_id in targets:
        try:
            slack_service.post_message(encrypted_tokens, channel_id, fallback_text, blocks)
        except Exception as e:
            logger.error(f"Failed to send Slack CRM push notification: {e}")


# Create the consumer singleton
_consumer = RedisEventConsumer(settings.REDIS_URL)
_consumer.subscribe("meeting_completed", handle_meeting_completed)
_consumer.subscribe("crm_push_requested", handle_crm_push_requested)


class SlackEventConsumer:
    """Wrapper to start/stop the Redis event consumer."""

    async def start(self):
        await _consumer.start()
        logger.info("Slack event consumer listening on: meeting_completed, crm_push_requested")

    async def stop(self):
        await _consumer.stop()
        logger.info("Slack event consumer stopped")


slack_event_consumer = SlackEventConsumer()
