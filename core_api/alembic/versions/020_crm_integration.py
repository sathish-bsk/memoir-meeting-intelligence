"""CRM integration tables + speaker_profiles dual-CRM support.

Revision ID: 020_crm_integration
Revises: 019_meeting_notes
Create Date: 2026-02-25 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "020_crm_integration"
down_revision = "019_meeting_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── crm_connections ──────────────────────────────────────────────
    op.create_table(
        "crm_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("encrypted_tokens", sa.Text(), nullable=False),
        sa.Column("provider_account_id", sa.String(255), nullable=True),
        sa.Column("provider_account_name", sa.String(255), nullable=True),
        sa.Column("api_domain", sa.String(255), nullable=True),
        sa.Column("accounts_url", sa.String(255), nullable=True),
        sa.Column("field_mapping", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("stage_mapping", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("auto_push_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connection_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "provider", name="uq_crm_connections_team_provider"),
    )
    op.create_index("ix_crm_connections_team", "crm_connections", ["team_id"])

    # ── crm_push_results ─────────────────────────────────────────────
    op.create_table(
        "crm_push_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("crm_contact_id", sa.String(255), nullable=True),
        sa.Column("crm_deal_id", sa.String(255), nullable=True),
        sa.Column("crm_record_id", sa.String(255), nullable=True),
        sa.Column("crm_record_url", sa.String(1024), nullable=True),
        sa.Column("pushed_data", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("edited_data", postgresql.JSONB(), nullable=True),
        sa.Column("extraction_snapshot", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pushed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("meeting_id", "provider", name="uq_crm_push_results_meeting_provider"),
    )
    op.create_index("ix_crm_push_results_meeting", "crm_push_results", ["meeting_id"])
    op.create_index("ix_crm_push_results_team_status", "crm_push_results", ["team_id", "status"])

    # ── speaker_profiles: replace crm_provider + crm_contact_id with crm_links JSONB ─
    op.add_column("speaker_profiles", sa.Column("crm_links", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.drop_column("speaker_profiles", "crm_provider")
    op.drop_column("speaker_profiles", "crm_contact_id")


def downgrade() -> None:
    # Restore speaker_profiles columns
    op.add_column("speaker_profiles", sa.Column("crm_contact_id", sa.String(255), nullable=True))
    op.add_column("speaker_profiles", sa.Column("crm_provider", sa.String(50), nullable=True))
    op.drop_column("speaker_profiles", "crm_links")

    op.drop_index("ix_crm_push_results_team_status", table_name="crm_push_results")
    op.drop_index("ix_crm_push_results_meeting", table_name="crm_push_results")
    op.drop_table("crm_push_results")

    op.drop_index("ix_crm_connections_team", table_name="crm_connections")
    op.drop_table("crm_connections")
