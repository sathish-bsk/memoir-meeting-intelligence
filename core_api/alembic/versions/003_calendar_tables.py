"""add calendar_connections and calendar_events tables

Revision ID: 003_calendar_tables
Revises: 002_team_member_invite_fields
Create Date: 2026-02-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "003_calendar_tables"
down_revision: Union[str, None] = "002_team_member_invite_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # calendar_connections
    op.create_table(
        "calendar_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(50), nullable=False, server_default="google"),
        sa.Column("encrypted_tokens", sa.Text(), nullable=False),
        sa.Column("provider_email", sa.String(320), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_calendar_connections_team_provider",
        "calendar_connections",
        ["team_id", "provider"],
        unique=True,
    )

    # calendar_events
    op.create_table(
        "calendar_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calendar_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider_event_id", sa.String(500), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meeting_url", sa.String(1000), nullable=True),
        sa.Column(
            "linked_meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_calendar_events_connection_provider_event",
        "calendar_events",
        ["connection_id", "provider_event_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_calendar_events_connection_provider_event", table_name="calendar_events")
    op.drop_table("calendar_events")
    op.drop_index("ix_calendar_connections_team_provider", table_name="calendar_connections")
    op.drop_table("calendar_connections")
