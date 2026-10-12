"""call_intelligence + aggregation tables, scheduled_jobs, meetings.intelligence_path

Revision ID: 010_intelligence_pipeline
Revises: 009_speaker_identification
Create Date: 2026-02-21 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "010_intelligence_pipeline"
down_revision: Union[str, None] = "009_speaker_identification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add intelligence_path to meetings (same pattern as analysis_path)
    op.add_column("meetings", sa.Column("intelligence_path", sa.Text(), nullable=True))

    # -- call_intelligence: main per-call extraction results --
    op.create_table(
        "call_intelligence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("call_type", sa.String(20), nullable=False),  # external_sales / internal / uncertain
        sa.Column("classification_confidence", sa.String(10)),
        # Prompt 2: CRM extraction
        sa.Column("crm_extraction", postgresql.JSONB),
        # Prompt 3: Intelligence extraction
        sa.Column("pain_points", postgresql.JSONB),
        sa.Column("desired_outcomes", postgresql.JSONB),
        sa.Column("feature_requests", postgresql.JSONB),
        sa.Column("competitors_mentioned", postgresql.JSONB),
        sa.Column("objections", postgresql.JSONB),
        sa.Column("buying_signals", postgresql.JSONB),
        sa.Column("red_flags", postgresql.JSONB),
        sa.Column("tech_stack", postgresql.JSONB),
        sa.Column("budget_signals", postgresql.JSONB),
        sa.Column("decision_process", postgresql.JSONB),
        sa.Column("product_perception", postgresql.JSONB),
        # Prompt 4: PMF signals
        sa.Column("pmf_signals", postgresql.JSONB),
        sa.Column("pull_score", sa.Integer),
        sa.Column("icp_fit_score", sa.Integer),
        sa.Column("pain_fit_score", sa.Integer),
        sa.Column("overall_pmf_signal", sa.String(20)),
        # Prompt 5: Summary
        sa.Column("summary", postgresql.JSONB),
        sa.Column("call_health", sa.String(20)),
        # Prompt 6: FAQ
        sa.Column("questions", postgresql.JSONB),
        # Processing status
        sa.Column("processing_status", sa.String(20), nullable=False, server_default="completed"),
        sa.Column("processing_started_at", sa.DateTime(timezone=True)),
        sa.Column("processing_completed_at", sa.DateTime(timezone=True)),
        sa.Column("processing_error", sa.Text),
        sa.Column("total_tokens_used", sa.Integer),
        sa.Column("total_cost_usd", sa.Numeric(10, 6)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_call_intelligence_meeting", "call_intelligence", ["meeting_id"])
    op.create_index("ix_call_intelligence_team_created", "call_intelligence", ["team_id", "created_at"])

    # -- call_pain_points: flattened for aggregation --
    op.create_table(
        "call_pain_points",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("call_intelligence_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("call_intelligence.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pain", sa.Text, nullable=False),
        sa.Column("severity", sa.String(10)),
        sa.Column("exact_quote", sa.Text),
        sa.Column("timestamp_in_call", sa.String(10)),
        sa.Column("explicitly_stated", sa.Boolean, default=True),
        sa.Column("call_date", sa.Date),
        sa.Column("prospect_company", sa.String(255)),
    )
    op.create_index("ix_call_pain_points_team_date", "call_pain_points", ["team_id", "call_date"])

    # -- call_feature_requests: flattened --
    op.create_table(
        "call_feature_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("call_intelligence_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("call_intelligence.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feature", sa.Text, nullable=False),
        sa.Column("use_case", sa.Text),
        sa.Column("urgency", sa.String(20)),
        sa.Column("exact_quote", sa.Text),
        sa.Column("call_date", sa.Date),
    )
    op.create_index("ix_call_feature_requests_team_date", "call_feature_requests", ["team_id", "call_date"])

    # -- call_competitors: flattened --
    op.create_table(
        "call_competitors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("call_intelligence_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("call_intelligence.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("competitor", sa.String(255), nullable=False),
        sa.Column("context", sa.String(50)),
        sa.Column("sentiment", sa.String(10)),
        sa.Column("what_they_like", sa.Text),
        sa.Column("what_they_dislike", sa.Text),
        sa.Column("call_date", sa.Date),
    )
    op.create_index("ix_call_competitors_team_date", "call_competitors", ["team_id", "call_date"])

    # -- call_objections: flattened --
    op.create_table(
        "call_objections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("call_intelligence_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("call_intelligence.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("objection", sa.Text, nullable=False),
        sa.Column("category", sa.String(20)),
        sa.Column("how_handled", sa.String(20)),
        sa.Column("call_date", sa.Date),
    )
    op.create_index("ix_call_objections_team_date", "call_objections", ["team_id", "call_date"])

    # -- weekly_digests: PMF narrative --
    op.create_table(
        "intelligence_digests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("narrative", sa.Text),
        sa.Column("metrics", postgresql.JSONB),
        sa.Column("total_calls", sa.Integer, default=0),
        sa.Column("cost_usd", sa.Numeric(10, 6)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_intelligence_digests_team", "intelligence_digests", ["team_id", "period_start"])

    # -- scheduled_jobs: user-configurable job definitions --
    op.create_table(
        "scheduled_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(50), nullable=False),  # intelligence_digest, faq_grouping
        sa.Column("frequency", sa.String(20), nullable=False, server_default="weekly"),
        sa.Column("preferred_time", sa.Time, server_default=sa.text("'06:00:00'")),
        sa.Column("preferred_day", sa.Integer),  # 0=Mon for weekly, 1-28 for monthly
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_status", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_scheduled_jobs_team_type", "scheduled_jobs", ["team_id", "job_type"], unique=True)
    op.create_index("ix_scheduled_jobs_next_run", "scheduled_jobs", ["enabled", "next_run_at"])

    # -- scheduled_job_runs: audit log --
    op.create_table(
        "scheduled_job_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("scheduled_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("result_summary", sa.Text),
        sa.Column("error_message", sa.Text),
        sa.Column("records_processed", sa.Integer, server_default=sa.text("0")),
        sa.Column("cost_usd", sa.Numeric(10, 6)),
    )
    op.create_index("ix_scheduled_job_runs_job", "scheduled_job_runs", ["job_id", "started_at"])


def downgrade() -> None:
    op.drop_table("scheduled_job_runs")
    op.drop_table("scheduled_jobs")
    op.drop_table("intelligence_digests")
    op.drop_table("call_objections")
    op.drop_table("call_competitors")
    op.drop_table("call_feature_requests")
    op.drop_table("call_pain_points")
    op.drop_table("call_intelligence")
    op.drop_column("meetings", "intelligence_path")
