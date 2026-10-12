"""Add meeting_subscription_id to calendar_events.

Tracks per-event Graph API / Workspace Events subscription IDs for
Teams callStarted and Google Meet conference.started webhooks.

Revision ID: 029_meeting_subscription
Revises: 028_calendar_auto_join
Create Date: 2026-04-13
"""

from alembic import op
import sqlalchemy as sa

revision = "029_meeting_subscription"
down_revision = "028_calendar_auto_join"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calendar_events",
        sa.Column("meeting_subscription_id", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("calendar_events", "meeting_subscription_id")
