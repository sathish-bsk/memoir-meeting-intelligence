"""Widen short VARCHAR columns to TEXT across intelligence tables.

LLM providers (especially Ollama) return values longer than the original
VARCHAR limits. TEXT has no length limit and identical performance in PostgreSQL.

Revision ID: 027_widen_intel_cols
Revises: 026_widen_meeting_status
Create Date: 2026-03-07 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "027_widen_intel_cols"
down_revision = "026_widen_meeting_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── call_intelligence ──
    for col, old_len in [
        ("call_type", 20),
        ("classification_confidence", 10),
        ("overall_pmf_signal", 20),
        ("deal_momentum", 20),
        ("validated_stage", 20),
        ("next_step_type", 30),
        ("call_health", 20),
        ("processing_status", 20),
    ]:
        op.alter_column(
            "call_intelligence", col,
            type_=sa.Text(),
            existing_type=sa.String(old_len),
        )

    # ── call_pain_points ──
    op.alter_column("call_pain_points", "severity",
                    type_=sa.Text(), existing_type=sa.String(10))
    op.alter_column("call_pain_points", "timestamp_in_call",
                    type_=sa.Text(), existing_type=sa.String(10))

    # ── call_feature_requests ──
    op.alter_column("call_feature_requests", "urgency",
                    type_=sa.Text(), existing_type=sa.String(20))
    op.alter_column("call_feature_requests", "timestamp_in_call",
                    type_=sa.Text(), existing_type=sa.String(10))

    # ── call_competitors ──
    op.alter_column("call_competitors", "context",
                    type_=sa.Text(), existing_type=sa.String(50))
    op.alter_column("call_competitors", "sentiment",
                    type_=sa.Text(), existing_type=sa.String(10))
    op.alter_column("call_competitors", "timestamp_in_call",
                    type_=sa.Text(), existing_type=sa.String(10))

    # ── call_objections ──
    op.alter_column("call_objections", "category",
                    type_=sa.Text(), existing_type=sa.String(20))
    op.alter_column("call_objections", "how_handled",
                    type_=sa.Text(), existing_type=sa.String(20))
    op.alter_column("call_objections", "timestamp_in_call",
                    type_=sa.Text(), existing_type=sa.String(10))

    # ── call_questions ──
    op.alter_column("call_questions", "category",
                    type_=sa.Text(), existing_type=sa.String(20))
    op.alter_column("call_questions", "answer_quality",
                    type_=sa.Text(), existing_type=sa.String(20))
    op.alter_column("call_questions", "timestamp_in_call",
                    type_=sa.Text(), existing_type=sa.String(10))

    # ── memoir_deals ──
    op.alter_column("memoir_deals", "current_stage",
                    type_=sa.Text(), existing_type=sa.String(20))
    op.alter_column("memoir_deals", "latest_call_health",
                    type_=sa.Text(), existing_type=sa.String(20))
    op.alter_column("memoir_deals", "latest_sentiment",
                    type_=sa.Text(), existing_type=sa.String(20))
    op.alter_column("memoir_deals", "status",
                    type_=sa.Text(), existing_type=sa.String(20))

    # ── deal_match_log ──
    op.alter_column("deal_match_log", "match_method",
                    type_=sa.Text(), existing_type=sa.String(20))


def downgrade() -> None:
    # ── call_intelligence ──
    for col, old_len in [
        ("call_type", 20),
        ("classification_confidence", 10),
        ("overall_pmf_signal", 20),
        ("deal_momentum", 20),
        ("validated_stage", 20),
        ("next_step_type", 30),
        ("call_health", 20),
        ("processing_status", 20),
    ]:
        op.alter_column(
            "call_intelligence", col,
            type_=sa.String(old_len),
            existing_type=sa.Text(),
        )

    op.alter_column("call_pain_points", "severity",
                    type_=sa.String(10), existing_type=sa.Text())
    op.alter_column("call_pain_points", "timestamp_in_call",
                    type_=sa.String(10), existing_type=sa.Text())

    op.alter_column("call_feature_requests", "urgency",
                    type_=sa.String(20), existing_type=sa.Text())
    op.alter_column("call_feature_requests", "timestamp_in_call",
                    type_=sa.String(10), existing_type=sa.Text())

    op.alter_column("call_competitors", "context",
                    type_=sa.String(50), existing_type=sa.Text())
    op.alter_column("call_competitors", "sentiment",
                    type_=sa.String(10), existing_type=sa.Text())
    op.alter_column("call_competitors", "timestamp_in_call",
                    type_=sa.String(10), existing_type=sa.Text())

    op.alter_column("call_objections", "category",
                    type_=sa.String(20), existing_type=sa.Text())
    op.alter_column("call_objections", "how_handled",
                    type_=sa.String(20), existing_type=sa.Text())
    op.alter_column("call_objections", "timestamp_in_call",
                    type_=sa.String(10), existing_type=sa.Text())

    op.alter_column("call_questions", "category",
                    type_=sa.String(20), existing_type=sa.Text())
    op.alter_column("call_questions", "answer_quality",
                    type_=sa.String(20), existing_type=sa.Text())
    op.alter_column("call_questions", "timestamp_in_call",
                    type_=sa.String(10), existing_type=sa.Text())

    op.alter_column("memoir_deals", "current_stage",
                    type_=sa.String(20), existing_type=sa.Text())
    op.alter_column("memoir_deals", "latest_call_health",
                    type_=sa.String(20), existing_type=sa.Text())
    op.alter_column("memoir_deals", "latest_sentiment",
                    type_=sa.String(20), existing_type=sa.Text())
    op.alter_column("memoir_deals", "status",
                    type_=sa.String(20), existing_type=sa.Text())

    op.alter_column("deal_match_log", "match_method",
                    type_=sa.String(20), existing_type=sa.Text())
