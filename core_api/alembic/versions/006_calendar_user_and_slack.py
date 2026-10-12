"""migrate calendar to user-based and add slack integration tables

Revision ID: 006_calendar_user_and_slack
Revises: 005_add_transcription_versions
Create Date: 2026-02-18 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "006_calendar_user_and_slack"
down_revision: Union[str, None] = "005_add_transcription_versions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── Calendar: team_id → user_id ──────────────────────────────────
    # 1. Add user_id column (nullable initially)
    op.add_column(
        "calendar_connections",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # 2. Copy team owner's user_id from team_members as default
    #    For existing rows, use the team's owner as the user
    op.execute(
        """
        UPDATE calendar_connections cc
        SET user_id = (
            SELECT tm.user_id FROM team_members tm
            WHERE tm.team_id = cc.team_id
            AND tm.role = 'owner'
            LIMIT 1
        )
        """
    )

    # 3. For any remaining nulls, use created_by from teams table
    op.execute(
        """
        UPDATE calendar_connections cc
        SET user_id = (
            SELECT t.created_by FROM teams t WHERE t.id = cc.team_id
        )
        WHERE cc.user_id IS NULL
        """
    )

    # 4. Make user_id non-nullable
    op.alter_column("calendar_connections", "user_id", nullable=False)

    # 5. Drop old index, create new one
    op.drop_index("ix_calendar_connections_team_provider", table_name="calendar_connections")
    op.create_index(
        "ix_calendar_connections_user_provider",
        "calendar_connections",
        ["user_id", "provider"],
        unique=True,
    )

    # 6. Drop team_id column and its FK
    op.drop_constraint(
        "calendar_connections_team_id_fkey",
        "calendar_connections",
        type_="foreignkey",
    )
    op.drop_column("calendar_connections", "team_id")

    # ─── Slack: new tables ────────────────────────────────────────────

    # slack_connections
    op.create_table(
        "slack_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.String(50), nullable=False),
        sa.Column("workspace_name", sa.String(255), nullable=True),
        sa.Column("encrypted_tokens", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=True),
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
        "ix_slack_connections_user_workspace",
        "slack_connections",
        ["user_id", "workspace_id"],
        unique=True,
    )

    # slack_notification_settings
    op.create_table(
        "slack_notification_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("slack_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("channel_id", sa.String(255), nullable=True),
        sa.Column("channel_name", sa.String(255), nullable=True),
        sa.Column("notify_on_meeting_complete", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("notify_on_crm_pushed", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("notify_on_blocker_detected", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("notify_on_team_invite", sa.Boolean(), nullable=False, server_default="false"),
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


def downgrade() -> None:
    # ─── Drop Slack tables ────────────────────────────────────────────
    op.drop_table("slack_notification_settings")
    op.drop_index("ix_slack_connections_user_workspace", table_name="slack_connections")
    op.drop_table("slack_connections")

    # ─── Calendar: user_id → team_id ──────────────────────────────────
    op.add_column(
        "calendar_connections",
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # Reverse: look up team_id from team_members for the user
    op.execute(
        """
        UPDATE calendar_connections cc
        SET team_id = (
            SELECT tm.team_id FROM team_members tm
            WHERE tm.user_id = cc.user_id
            LIMIT 1
        )
        """
    )

    op.alter_column("calendar_connections", "team_id", nullable=False)

    op.drop_index("ix_calendar_connections_user_provider", table_name="calendar_connections")
    op.create_index(
        "ix_calendar_connections_team_provider",
        "calendar_connections",
        ["team_id", "provider"],
        unique=True,
    )

    op.create_foreign_key(
        "calendar_connections_team_id_fkey",
        "calendar_connections",
        "teams",
        ["team_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_column("calendar_connections", "user_id")
