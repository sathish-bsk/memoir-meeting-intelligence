"""Create notification_preferences + notification_log tables.

Revision ID: 018_notification_preferences
Revises: 017_share_links
Create Date: 2026-02-24 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


# revision identifiers, used by Alembic.
revision: str = "018_notification_preferences"
down_revision: Union[str, None] = "017_share_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. notification_preferences — one row per user/team/event_type/channel
    op.create_table(
        "notification_preferences",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "team_id", "event_type", "channel", name="uq_notif_pref_user_team_event_channel"),
    )
    op.create_index("ix_notif_pref_user_team", "notification_preferences", ["user_id", "team_id"])

    # 2. notification_log — activity log for bell icon
    op.create_table(
        "notification_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text),
        sa.Column("link", sa.String(500)),
        sa.Column("channel", sa.String(20)),
        sa.Column("status", sa.String(20), nullable=False, server_default="sent"),
        sa.Column("read", sa.Boolean, server_default="false", nullable=False),
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_notif_log_user_read_created", "notification_log", ["user_id", "read", sa.text("created_at DESC")])
    op.create_index("ix_notif_log_team_created", "notification_log", ["team_id", sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_table("notification_log")
    op.drop_table("notification_preferences")
