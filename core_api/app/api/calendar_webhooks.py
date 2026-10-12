"""
Calendar webhook endpoints — receives push notifications from Google Calendar
and Microsoft Graph when calendar events change, then triggers incremental sync.
"""

import base64
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session
from app.models.calendar import CalendarConnection, CalendarEvent
from app.utils.meeting_url import extract_meet_code, extract_teams_join_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["calendar-webhooks"])


@router.post("/google-calendar")
async def google_calendar_webhook(request: Request):
    """Handle Google Calendar push notifications.

    Google sends headers only (empty body):
    - X-Goog-Channel-ID: our channel UUID
    - X-Goog-Resource-State: "sync" (initial) or "exists" (change happened)
    - X-Goog-Resource-ID: Google's resource identifier
    """
    channel_id = request.headers.get("X-Goog-Channel-ID")
    resource_state = request.headers.get("X-Goog-Resource-State")

    if not channel_id:
        raise HTTPException(400, "Missing X-Goog-Channel-ID header")

    # Initial sync verification — just acknowledge
    if resource_state == "sync":
        logger.info(f"Google watch channel {channel_id} verified")
        return Response(status_code=200)

    # Something changed — trigger incremental sync
    if resource_state in ("exists", "not_exists"):
        try:
            async with async_session() as db:
                result = await db.execute(
                    select(CalendarConnection).where(
                        CalendarConnection.watch_channel_id == channel_id
                    )
                )
                connection = result.scalar_one_or_none()

                if not connection:
                    logger.warning(f"No connection found for Google channel {channel_id}")
                    return Response(status_code=200)

                from app.services.calendar_sync import google_incremental_sync

                changed = await google_incremental_sync(db, connection)
                if changed:
                    logger.info(
                        f"Google webhook: synced {changed} event(s) for connection {connection.id}"
                    )
        except Exception as e:
            logger.error(f"Google webhook sync failed for channel {channel_id}: {e}")

    return Response(status_code=200)


@router.post("/microsoft-calendar")
async def microsoft_calendar_webhook(request: Request):
    """Handle Microsoft Graph subscription notifications.

    Microsoft sends:
    1. Validation request with ?validationToken=... (must echo it back)
    2. Change notifications with JSON body containing resource data
    """
    # Handle validation request (sent when subscription is first created)
    validation_token = request.query_params.get("validationToken")
    if validation_token:
        logger.info("Microsoft calendar subscription validated")
        return Response(content=validation_token, media_type="text/plain", status_code=200)

    # Process change notification
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    notifications = body.get("value", [])

    for notification in notifications:
        subscription_id = notification.get("subscriptionId")
        if not subscription_id:
            continue

        # Verify client state
        client_state = notification.get("clientState")
        from app.core.config import settings

        if client_state and client_state != settings.TEAMS_CLOUD_CLIENT_STATE_SECRET:
            logger.warning(f"Invalid clientState for subscription {subscription_id}")
            continue

        try:
            async with async_session() as db:
                result = await db.execute(
                    select(CalendarConnection).where(
                        CalendarConnection.subscription_id == subscription_id
                    )
                )
                connection = result.scalar_one_or_none()

                if not connection:
                    logger.warning(f"No connection found for subscription {subscription_id}")
                    continue

                from app.services.calendar_sync import outlook_incremental_sync

                changed = await outlook_incremental_sync(db, connection)
                if changed:
                    logger.info(
                        f"Microsoft webhook: synced {changed} event(s) for connection {connection.id}"
                    )
        except Exception as e:
            logger.error(f"Microsoft webhook sync failed for subscription {subscription_id}: {e}")

    return Response(status_code=202)


@router.post("/teams-meeting-events")
async def teams_meeting_event_webhook(request: Request):
    """Handle Teams callStarted events for early meeting join.

    Microsoft sends:
    1. Validation request with ?validationToken=... (must echo it back)
    2. Change notifications with JSON body
    """
    # Handle validation
    validation_token = request.query_params.get("validationToken")
    if validation_token:
        logger.info("Teams meeting event subscription validated")
        return Response(content=validation_token, media_type="text/plain", status_code=200)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    notifications = body.get("value", [])

    for notification in notifications:
        # Verify client state
        client_state = notification.get("clientState")
        if client_state and client_state != settings.TEAMS_CLOUD_CLIENT_STATE_SECRET:
            logger.warning("Invalid clientState for Teams meeting event notification")
            continue

        try:
            # Extract the join URL from the notification resource
            resource = notification.get("resource", "")
            resource_data = notification.get("resourceData", {})

            async with async_session() as db:
                # Find calendar events with Teams meeting URLs that match
                now = datetime.now(timezone.utc)
                result = await db.execute(
                    select(CalendarEvent, CalendarConnection)
                    .join(CalendarConnection, CalendarEvent.connection_id == CalendarConnection.id)
                    .where(
                        CalendarEvent.meeting_url.isnot(None),
                        CalendarEvent.meeting_url.op("~*")("teams\\.(microsoft\\.com|live\\.com)"),
                        CalendarEvent.bot_scheduled == False,  # noqa: E712
                        CalendarEvent.meeting_subscription_id.isnot(None),
                        CalendarEvent.start_time >= now - timedelta(minutes=10),
                        CalendarEvent.start_time <= now + timedelta(hours=24),
                        CalendarConnection.auto_record == True,  # noqa: E712
                    )
                )
                rows = result.all()

                # Match by subscription ID first (most reliable)
                subscription_id = notification.get("subscriptionId")
                from app.services.early_join_dispatcher import dispatch_bot_for_calendar_event

                for cal_event, connection in rows:
                    if cal_event.meeting_subscription_id == subscription_id:
                        logger.info(
                            f"Teams meeting started for event {cal_event.id} "
                            f"('{cal_event.title}'), dispatching bot"
                        )
                        await dispatch_bot_for_calendar_event(
                            db, cal_event, connection, trigger="teams_webhook"
                        )
                        break

        except Exception as e:
            logger.error(f"Teams meeting event handling failed: {e}", exc_info=True)

    return Response(status_code=202)


@router.post("/google-meet-events")
async def google_meet_event_webhook(request: Request):
    """Handle Google Meet conference.v2.started events for early meeting join.

    Google Workspace Events delivers via Pub/Sub push, which wraps the payload:
    {
        "message": {
            "data": "<base64-encoded JSON>",
            "messageId": "...",
            "publishTime": "..."
        },
        "subscription": "projects/.../subscriptions/..."
    }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    logger.info(f"Google Meet event webhook received: {json.dumps(body)[:500]}")

    # Extract event type and subscription info from Pub/Sub message attributes
    message = body.get("message", {})
    attributes = message.get("attributes", {})
    event_type = attributes.get("ce-type", "")
    subscription_source = attributes.get("ce-source", "")  # contains subscription name

    # Unwrap Pub/Sub data envelope
    if message:
        raw_data = message.get("data", "")
        if raw_data:
            try:
                decoded = base64.b64decode(raw_data).decode("utf-8")
                inner_body = json.loads(decoded)
                logger.info(f"Decoded Pub/Sub message: {decoded[:500]}")
            except Exception as e:
                logger.error(f"Failed to decode Pub/Sub message: {e}")

    if "conference.v2.started" not in event_type:
        logger.debug(f"Ignoring Google Meet event type: {event_type}")
        return Response(status_code=200)

    try:
        # Match by subscription ID stored on the calendar event.
        # The ce-source attribute contains the subscription name like:
        # //workspaceevents.googleapis.com/subscriptions/meet-spaces-<uuid>
        # We stored the full operation/subscription name in meeting_subscription_id.
        async with async_session() as db:
            now = datetime.now(timezone.utc)
            result = await db.execute(
                select(CalendarEvent, CalendarConnection)
                .join(CalendarConnection, CalendarEvent.connection_id == CalendarConnection.id)
                .where(
                    CalendarEvent.meeting_url.isnot(None),
                    CalendarEvent.meeting_url.like("%meet.google.com%"),
                    CalendarEvent.bot_scheduled == False,  # noqa: E712
                    CalendarEvent.meeting_subscription_id.isnot(None),
                    CalendarEvent.start_time >= now - timedelta(minutes=10),
                    CalendarEvent.start_time <= now + timedelta(hours=24),
                    CalendarConnection.auto_record == True,  # noqa: E712
                )
            )
            rows = result.all()

            from app.services.early_join_dispatcher import dispatch_bot_for_calendar_event

            # Extract the subscription UUID from ce-source for matching.
            # ce-source: //workspaceevents.googleapis.com/subscriptions/meet-spaces-<uuid>
            # We stored an operations ID which embeds the same UUID base64-encoded.
            # Instead, match by meet code extracted from the calendar event URL
            # against the space name in ce-subject.

            # ce-subject: //meet.googleapis.com/spaces/O2OWWt3SzssB (internal space ID)
            # This doesn't directly give us the meet code, so we match by checking
            # which subscribed events correspond to this notification.

            # Match by space name from ce-subject.
            # ce-subject: //meet.googleapis.com/spaces/O2OWWt3SzssB
            # meeting_subscription_id stores: spaces/O2OWWt3SzssB
            ce_subject = attributes.get("ce-subject", "")

            for cal_event, connection in rows:
                sub_id = cal_event.meeting_subscription_id or ""
                if sub_id and sub_id in ce_subject:
                    logger.info(
                        f"Google Meet started for event {cal_event.id} "
                        f"('{cal_event.title}'), dispatching bot"
                    )
                    await dispatch_bot_for_calendar_event(
                        db, cal_event, connection, trigger="meet_webhook"
                    )
                    return Response(status_code=200)

            logger.warning(
                f"Could not match Google Meet webhook to calendar event. "
                f"ce_subject={ce_subject}, candidates={len(rows)}"
            )

    except Exception as e:
        logger.error(f"Google Meet event handling failed: {e}", exc_info=True)

    return Response(status_code=200)
