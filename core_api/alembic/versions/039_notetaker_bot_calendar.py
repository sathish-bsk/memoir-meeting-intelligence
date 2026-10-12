"""Phase 2: bot_calendar provider + reliability columns

Adds schema for the notetaker-email plan (Phase 2):

* calendar_events: resolved_team_id, resolved_user_id, ics_sequence,
  cancelled_at, bot_invited — five additive columns supporting the
  ICS reschedule semantics (§4.8), the listing+auth ownership change
  (§4.7), and the dedup-by-bot-presence flag (§4.3).

* meetings: failure_reason — nullable VARCHAR for "why did this fail"
  beyond the existing status enum. Phase 2 uses 'calendar_reschedule'
  and 'calendar_cancelled' values; future status-failure paths can add
  more without further migrations.

* bot_calendar_unmatched: new table for invites where the organizer
  doesn't match any users.email. Holds the raw invite + autoresponder
  state. 7-day retention runs as a separate cleanup cron (out of scope
  for this migration).

* Backfill: resolved_team_id/resolved_user_id are populated for
  existing rows by joining through the connection→user→team_member
  chain so the broader Track B work + GET /api/calendar/events listing
  rewrite have a clean column to query.

Revision ID: 039_notetaker_bot_calendar
Revises: 038_meeting_next_steps_data
Create Date: 2026-05-21 16:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "039_notetaker_bot_calendar"
down_revision: Union[str, None] = "038_meeting_next_steps_data"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── calendar_events: ownership + sequence + cancellation + bot-presence flag
    op.add_column(
        "calendar_events",
        sa.Column("resolved_team_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "calendar_events",
        sa.Column("resolved_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "calendar_events",
        sa.Column("ics_sequence", sa.Integer(), nullable=True),
    )
    op.add_column(
        "calendar_events",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "calendar_events",
        sa.Column("bot_invited", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_calendar_events_resolved_team",
        "calendar_events",
        ["resolved_team_id"],
    )
    op.create_index(
        "ix_calendar_events_resolved_user",
        "calendar_events",
        ["resolved_user_id"],
    )
    # Partial-ish index for active-event lookups (the auto-scheduler's
    # hot path already filters on bot_scheduled=false; adding
    # cancelled_at to the same predicate is essentially free).
    op.create_index(
        "ix_calendar_events_active",
        "calendar_events",
        ["start_time"],
        postgresql_where=sa.text("cancelled_at IS NULL AND bot_scheduled = false"),
    )

    # Backfill resolved_team_id / resolved_user_id for existing rows.
    # Chain: calendar_events → calendar_connections.user_id →
    # team_members.team_id (primary team_member row for that user).
    # Done in pure SQL to keep migration self-contained.
    op.execute(
        """
        WITH chain AS (
            SELECT
                e.id AS event_id,
                cc.user_id AS user_id,
                (
                    SELECT tm.team_id
                    FROM team_members tm
                    WHERE tm.user_id = cc.user_id
                      AND (tm.invite_status = 'accepted' OR tm.invite_status IS NULL)
                    ORDER BY tm.joined_at ASC NULLS LAST
                    LIMIT 1
                ) AS team_id
            FROM calendar_events e
            JOIN calendar_connections cc ON cc.id = e.connection_id
        )
        UPDATE calendar_events ce
        SET
            resolved_user_id = chain.user_id,
            resolved_team_id = chain.team_id
        FROM chain
        WHERE ce.id = chain.event_id
        """
    )

    # ── meetings.failure_reason — nullable, no backfill.
    op.add_column(
        "meetings",
        sa.Column("failure_reason", sa.String(length=100), nullable=True),
    )

    # ── bot_calendar_unmatched: invites from non-user organizers.
    op.create_table(
        "bot_calendar_unmatched",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organizer_email", sa.String(length=320), nullable=False),
        sa.Column("ics_uid", sa.String(length=500), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meeting_url", sa.String(length=1000), nullable=True),
        sa.Column("raw_event", postgresql.JSONB(), nullable=True),
        sa.Column("reason", sa.String(length=100), nullable=True),
        sa.Column("autoresponder_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("ics_uid", name="uq_bot_calendar_unmatched_ics_uid"),
    )
    op.create_index(
        "ix_bot_calendar_unmatched_organizer",
        "bot_calendar_unmatched",
        ["organizer_email"],
    )
    op.create_index(
        "ix_bot_calendar_unmatched_received_at",
        "bot_calendar_unmatched",
        ["received_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_bot_calendar_unmatched_received_at", table_name="bot_calendar_unmatched")
    op.drop_index("ix_bot_calendar_unmatched_organizer", table_name="bot_calendar_unmatched")
    op.drop_table("bot_calendar_unmatched")

    op.drop_column("meetings", "failure_reason")

    op.drop_index("ix_calendar_events_active", table_name="calendar_events")
    op.drop_index("ix_calendar_events_resolved_user", table_name="calendar_events")
    op.drop_index("ix_calendar_events_resolved_team", table_name="calendar_events")
    op.drop_column("calendar_events", "bot_invited")
    op.drop_column("calendar_events", "cancelled_at")
    op.drop_column("calendar_events", "ics_sequence")
    op.drop_column("calendar_events", "resolved_user_id")
    op.drop_column("calendar_events", "resolved_team_id")
