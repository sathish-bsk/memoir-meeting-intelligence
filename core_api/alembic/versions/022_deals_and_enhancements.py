"""Add deal threading, PMF weekly digest, AI prompt logging, and CRM push audit.

New tables: memoir_deals, deal_match_log, ai_prompt_log, crm_push_audit, pmf_weekly_digest
Alter tables: meetings (memoir_deal_id, call_number_in_deal),
              call_intelligence (deal_momentum, validated_stage, crm_stage_id/label,
                                 has_committed_next_step, next_step_type)

Revision ID: 022_deals_and_enhancements
Revises: 021_job_operation_type
Create Date: 2026-02-28 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "022_deals_and_enhancements"
down_revision = "021_job_operation_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- New table: memoir_deals ---
    op.create_table(
        "memoir_deals",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prospect_name", sa.String(255), nullable=True),
        sa.Column("prospect_company", sa.String(255), nullable=False),
        sa.Column("prospect_email", sa.String(255), nullable=True),
        sa.Column("prospect_domain", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), server_default=sa.text("'open'"), nullable=False),
        sa.Column("current_stage", sa.String(20), nullable=True),
        sa.Column("hubspot_deal_id", sa.String(50), nullable=True),
        sa.Column("zoho_deal_id", sa.String(50), nullable=True),
        sa.Column("crm_current_stage_id", sa.String(100), nullable=True),
        sa.Column("crm_current_stage_label", sa.String(255), nullable=True),
        sa.Column("total_calls", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("first_call_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_call_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_sentiment", sa.String(20), nullable=True),
        sa.Column("latest_call_health", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memoir_deals_team", "memoir_deals", ["team_id"])
    op.create_index("ix_memoir_deals_team_company", "memoir_deals", ["team_id", "prospect_company"])
    op.create_index("ix_memoir_deals_team_domain", "memoir_deals", ["team_id", "prospect_domain"])
    op.create_index("ix_memoir_deals_team_email", "memoir_deals", ["team_id", "prospect_email"])

    # --- New table: deal_match_log ---
    op.create_table(
        "deal_match_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("memoir_deal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memoir_deals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("match_method", sa.String(20), nullable=False),
        sa.Column("match_confidence", sa.Float(), nullable=True),
        sa.Column("matched_on", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deal_match_log_meeting", "deal_match_log", ["meeting_id"])
    op.create_index("ix_deal_match_log_deal", "deal_match_log", ["memoir_deal_id"])

    # --- New table: ai_prompt_log ---
    op.create_table(
        "ai_prompt_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prompt_name", sa.String(50), nullable=False),
        sa.Column("provider", sa.String(20), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_prompt_log_meeting", "ai_prompt_log", ["meeting_id"])
    op.create_index("ix_ai_prompt_log_team_created", "ai_prompt_log", ["team_id", "created_at"])

    # --- New table: crm_push_audit ---
    op.create_table(
        "crm_push_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("push_result_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("crm_push_results.id", ondelete="SET NULL"), nullable=True),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("push_type", sa.String(10), nullable=False),
        sa.Column("previous_values", postgresql.JSONB(), nullable=True),
        sa.Column("new_values", postgresql.JSONB(), nullable=True),
        sa.Column("pushed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pushed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_crm_push_audit_meeting", "crm_push_audit", ["meeting_id"])
    op.create_index("ix_crm_push_audit_team", "crm_push_audit", ["team_id", "pushed_at"])

    # --- New table: pmf_weekly_digest ---
    op.create_table(
        "pmf_weekly_digest",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("total_calls", sa.Integer(), nullable=False),
        sa.Column("feature_demand", postgresql.JSONB(), nullable=True),
        sa.Column("objections", postgresql.JSONB(), nullable=True),
        sa.Column("competitors", postgresql.JSONB(), nullable=True),
        sa.Column("buy_signal_rate", sa.Float(), nullable=True),
        sa.Column("buy_signal_stars", sa.Integer(), nullable=True),
        sa.Column("buy_signal_prev_rate", sa.Float(), nullable=True),
        sa.Column("pain_clarity_rate", sa.Float(), nullable=True),
        sa.Column("pain_clarity_stars", sa.Integer(), nullable=True),
        sa.Column("pain_clarity_top_pain", sa.Text(), nullable=True),
        sa.Column("pain_clarity_prev_rate", sa.Float(), nullable=True),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "week_start", name="uq_pmf_weekly_digest_team_week"),
    )
    op.create_index("ix_pmf_weekly_digest_team", "pmf_weekly_digest", ["team_id", "week_start"])

    # --- Alter meetings ---
    op.add_column("meetings", sa.Column("memoir_deal_id", postgresql.UUID(as_uuid=True),
                                        sa.ForeignKey("memoir_deals.id", ondelete="SET NULL"), nullable=True))
    op.add_column("meetings", sa.Column("call_number_in_deal", sa.Integer(), nullable=True))
    op.create_index("ix_meetings_deal", "meetings", ["memoir_deal_id"])

    # --- Alter call_intelligence ---
    op.add_column("call_intelligence", sa.Column("deal_momentum", sa.String(20), nullable=True))
    op.add_column("call_intelligence", sa.Column("validated_stage", sa.String(20), nullable=True))
    op.add_column("call_intelligence", sa.Column("crm_stage_id", sa.String(100), nullable=True))
    op.add_column("call_intelligence", sa.Column("crm_stage_label", sa.String(255), nullable=True))
    op.add_column("call_intelligence", sa.Column("has_committed_next_step", sa.Boolean(), nullable=True))
    op.add_column("call_intelligence", sa.Column("next_step_type", sa.String(30), nullable=True))


def downgrade() -> None:
    # Reverse call_intelligence columns
    op.drop_column("call_intelligence", "next_step_type")
    op.drop_column("call_intelligence", "has_committed_next_step")
    op.drop_column("call_intelligence", "crm_stage_label")
    op.drop_column("call_intelligence", "crm_stage_id")
    op.drop_column("call_intelligence", "validated_stage")
    op.drop_column("call_intelligence", "deal_momentum")

    # Reverse meetings columns
    op.drop_index("ix_meetings_deal", table_name="meetings")
    op.drop_column("meetings", "call_number_in_deal")
    op.drop_column("meetings", "memoir_deal_id")

    # Drop new tables (reverse order)
    op.drop_index("ix_pmf_weekly_digest_team", table_name="pmf_weekly_digest")
    op.drop_table("pmf_weekly_digest")

    op.drop_index("ix_crm_push_audit_team", table_name="crm_push_audit")
    op.drop_index("ix_crm_push_audit_meeting", table_name="crm_push_audit")
    op.drop_table("crm_push_audit")

    op.drop_index("ix_ai_prompt_log_team_created", table_name="ai_prompt_log")
    op.drop_index("ix_ai_prompt_log_meeting", table_name="ai_prompt_log")
    op.drop_table("ai_prompt_log")

    op.drop_index("ix_deal_match_log_deal", table_name="deal_match_log")
    op.drop_index("ix_deal_match_log_meeting", table_name="deal_match_log")
    op.drop_table("deal_match_log")

    op.drop_index("ix_memoir_deals_team_email", table_name="memoir_deals")
    op.drop_index("ix_memoir_deals_team_domain", table_name="memoir_deals")
    op.drop_index("ix_memoir_deals_team_company", table_name="memoir_deals")
    op.drop_index("ix_memoir_deals_team", table_name="memoir_deals")
    op.drop_table("memoir_deals")
