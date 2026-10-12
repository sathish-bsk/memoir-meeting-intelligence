"""Add dashboard cost tracking and Teams cloud recording support.

Changes:
- ai_prompt_log: add operation_type, gpu_type, audio_duration_seconds,
  processing_seconds, recording_source, triggered_by + new indexes
- team_budget: new table for per-team monthly budget alerts
- zoom_connections -> recording_connections: rename table, add provider column,
  rename zoom_user_id->provider_user_id, zoom_email->provider_email,
  add Teams-specific nullable columns, update unique constraint
- meetings: add teams_meeting_id column + index

Revision ID: 023_dashboard_and_teams_cloud
Revises: 022_deals_and_enhancements
Create Date: 2026-03-03 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "023_dashboard_and_teams_cloud"
down_revision = "022_deals_and_enhancements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Extend ai_prompt_log with cost tracking columns
    # -------------------------------------------------------------------------
    op.add_column("ai_prompt_log", sa.Column("operation_type", sa.String(30), nullable=True))
    op.add_column("ai_prompt_log", sa.Column("gpu_type", sa.String(50), nullable=True))
    op.add_column("ai_prompt_log", sa.Column("audio_duration_seconds", sa.Float(), nullable=True))
    op.add_column("ai_prompt_log", sa.Column("processing_seconds", sa.Float(), nullable=True))
    op.add_column("ai_prompt_log", sa.Column("recording_source", sa.String(20), nullable=True))
    op.add_column("ai_prompt_log", sa.Column("triggered_by", postgresql.UUID(as_uuid=True), nullable=True))

    op.create_index("ix_ai_prompt_log_team_source", "ai_prompt_log", ["team_id", "recording_source"])
    op.create_index("ix_ai_prompt_log_operation", "ai_prompt_log", ["team_id", "operation_type"])

    # -------------------------------------------------------------------------
    # 2. Create team_budget table
    # -------------------------------------------------------------------------
    op.create_table(
        "team_budget",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("monthly_budget_usd", sa.Float(), nullable=True),
        sa.Column("alert_threshold_pct", sa.Integer(), nullable=False, server_default="80"),
        sa.Column("hard_limit_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_alert_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_team_budget_team", "team_budget", ["team_id"], unique=True)

    # -------------------------------------------------------------------------
    # 3. Rename zoom_connections -> recording_connections
    #    + add provider + rename columns + add Teams-specific columns
    #    + update unique constraint
    # -------------------------------------------------------------------------
    op.rename_table("zoom_connections", "recording_connections")

    # Drop old unique constraint on user_id (it's a UNIQUE CONSTRAINT, not a plain index)
    op.drop_constraint("uq_zoom_connections_user", "recording_connections", type_="unique")
    op.drop_index("ix_zoom_connections_email", table_name="recording_connections")
    op.drop_index("ix_zoom_connections_team", table_name="recording_connections")

    # Add provider column (default "zoom" for existing rows)
    op.add_column("recording_connections", sa.Column("provider", sa.String(20), nullable=True))
    op.execute("UPDATE recording_connections SET provider = 'zoom'")
    op.alter_column("recording_connections", "provider", nullable=False)

    # Rename zoom_user_id -> provider_user_id, zoom_email -> provider_email
    op.alter_column("recording_connections", "zoom_user_id", new_column_name="provider_user_id")
    op.alter_column("recording_connections", "zoom_email", new_column_name="provider_email")

    # Add Teams-specific nullable columns
    op.add_column("recording_connections", sa.Column("tenant_id", sa.String(255), nullable=True))
    op.add_column("recording_connections", sa.Column("subscription_id", sa.String(255), nullable=True))
    op.add_column("recording_connections", sa.Column("subscription_expiry", sa.DateTime(timezone=True), nullable=True))
    op.add_column("recording_connections", sa.Column("admin_consent_granted", sa.Boolean(), nullable=True, server_default="false"))

    # Create new indexes
    op.create_index("ix_recording_connections_user_provider", "recording_connections", ["user_id", "provider"], unique=True)
    op.create_index("ix_recording_connections_email", "recording_connections", ["provider_email"])
    op.create_index("ix_recording_connections_team", "recording_connections", ["team_id"])
    op.create_index("ix_recording_connections_tenant", "recording_connections", ["tenant_id"])

    # -------------------------------------------------------------------------
    # 4. Add teams_meeting_id to meetings
    # -------------------------------------------------------------------------
    op.add_column("meetings", sa.Column("teams_meeting_id", sa.String(255), nullable=True))
    op.create_index("ix_meetings_teams_meeting_id", "meetings", ["teams_meeting_id"])


def downgrade() -> None:
    # meetings
    op.drop_index("ix_meetings_teams_meeting_id", table_name="meetings")
    op.drop_column("meetings", "teams_meeting_id")

    # recording_connections -> zoom_connections
    op.drop_index("ix_recording_connections_tenant", table_name="recording_connections")
    op.drop_index("ix_recording_connections_team", table_name="recording_connections")
    op.drop_index("ix_recording_connections_email", table_name="recording_connections")
    op.drop_index("ix_recording_connections_user_provider", table_name="recording_connections")

    op.drop_column("recording_connections", "admin_consent_granted")
    op.drop_column("recording_connections", "subscription_expiry")
    op.drop_column("recording_connections", "subscription_id")
    op.drop_column("recording_connections", "tenant_id")

    op.alter_column("recording_connections", "provider_email", new_column_name="zoom_email")
    op.alter_column("recording_connections", "provider_user_id", new_column_name="zoom_user_id")
    op.drop_column("recording_connections", "provider")

    op.create_index("ix_zoom_connections_team", "recording_connections", ["team_id"])
    op.create_index("ix_zoom_connections_email", "recording_connections", ["zoom_email"])
    op.create_unique_constraint("uq_zoom_connections_user", "recording_connections", ["user_id"])

    op.rename_table("recording_connections", "zoom_connections")

    # team_budget
    op.drop_index("ix_team_budget_team", table_name="team_budget")
    op.drop_table("team_budget")

    # ai_prompt_log
    op.drop_index("ix_ai_prompt_log_operation", table_name="ai_prompt_log")
    op.drop_index("ix_ai_prompt_log_team_source", table_name="ai_prompt_log")
    op.drop_column("ai_prompt_log", "triggered_by")
    op.drop_column("ai_prompt_log", "recording_source")
    op.drop_column("ai_prompt_log", "processing_seconds")
    op.drop_column("ai_prompt_log", "audio_duration_seconds")
    op.drop_column("ai_prompt_log", "gpu_type")
    op.drop_column("ai_prompt_log", "operation_type")
