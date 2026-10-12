"""Add calendar auto-join columns.

calendar_connections: auto_record, sync_token, watch_channel_id, watch_resource_id,
                      watch_expires_at, subscription_id
calendar_events: bot_scheduled, bot_retry_count

Revision ID: 028_calendar_auto_join
Revises: 027_widen_intel_cols
Create Date: 2026-03-25
"""

from alembic import op
import sqlalchemy as sa

revision = "028_calendar_auto_join"
down_revision = "027_widen_intel_cols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # calendar_connections — auto-record + push notification state
    op.add_column("calendar_connections", sa.Column("auto_record", sa.Boolean(), server_default="true", nullable=False))
    op.add_column("calendar_connections", sa.Column("sync_token", sa.Text(), nullable=True))
    op.add_column("calendar_connections", sa.Column("watch_channel_id", sa.String(100), nullable=True))
    op.add_column("calendar_connections", sa.Column("watch_resource_id", sa.String(200), nullable=True))
    op.add_column("calendar_connections", sa.Column("watch_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("calendar_connections", sa.Column("subscription_id", sa.String(200), nullable=True))

    # calendar_events — bot scheduling state
    op.add_column("calendar_events", sa.Column("bot_scheduled", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("calendar_events", sa.Column("bot_retry_count", sa.Integer(), server_default="0", nullable=False))

    # Index for auto-scheduler query performance
    op.create_index(
        "ix_calendar_events_auto_schedule",
        "calendar_events",
        ["bot_scheduled", "start_time"],
        postgresql_where=sa.text("bot_scheduled = false"),
    )


def downgrade() -> None:
    op.drop_index("ix_calendar_events_auto_schedule", table_name="calendar_events")
    op.drop_column("calendar_events", "bot_retry_count")
    op.drop_column("calendar_events", "bot_scheduled")
    op.drop_column("calendar_connections", "subscription_id")
    op.drop_column("calendar_connections", "watch_expires_at")
    op.drop_column("calendar_connections", "watch_resource_id")
    op.drop_column("calendar_connections", "watch_channel_id")
    op.drop_column("calendar_connections", "sync_token")
    op.drop_column("calendar_connections", "auto_record")
