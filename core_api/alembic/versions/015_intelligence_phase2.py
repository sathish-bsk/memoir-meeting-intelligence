"""Intelligence Phase B2 — call_questions table + created_by columns + backfill.

Revision ID: 015_intelligence_phase2
Revises: 014_share_token
Create Date: 2026-02-23 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "015_intelligence_phase2"
down_revision: Union[str, None] = "014_share_token"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create call_questions table
    op.create_table(
        "call_questions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("call_intelligence_id", UUID(as_uuid=True), sa.ForeignKey("call_intelligence.id", ondelete="CASCADE")),
        sa.Column("meeting_id", UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("exact_quote", sa.Text(), nullable=True),
        sa.Column("category", sa.String(20), nullable=True),
        sa.Column("was_answered", sa.Boolean(), nullable=True),
        sa.Column("answer_quality", sa.String(20), nullable=True),
        sa.Column("call_date", sa.Date(), nullable=True),
        sa.Column("prospect_company", sa.String(255), nullable=True),
    )
    op.create_index("ix_call_questions_team_date", "call_questions", ["team_id", "call_date"])
    op.create_index("ix_call_questions_team_user_date", "call_questions", ["team_id", "created_by", "call_date"])

    # 2. Add created_by column to intelligence tables
    for table in ["call_intelligence", "call_pain_points", "call_feature_requests", "call_competitors", "call_objections"]:
        op.add_column(table, sa.Column("created_by", UUID(as_uuid=True), nullable=True))

    # Add composite indexes for role-based filtering
    for table in ["call_pain_points", "call_feature_requests", "call_competitors", "call_objections"]:
        op.create_index(
            f"ix_{table}_team_user_date",
            table,
            ["team_id", "created_by", "call_date"],
        )

    # 3. Backfill created_by from meetings
    op.execute("""
        UPDATE call_intelligence ci
        SET created_by = m.created_by
        FROM meetings m
        WHERE ci.meeting_id = m.id AND ci.created_by IS NULL
    """)
    op.execute("""
        UPDATE call_pain_points cp
        SET created_by = m.created_by
        FROM meetings m
        WHERE cp.meeting_id = m.id AND cp.created_by IS NULL
    """)
    op.execute("""
        UPDATE call_feature_requests cf
        SET created_by = m.created_by
        FROM meetings m
        WHERE cf.meeting_id = m.id AND cf.created_by IS NULL
    """)
    op.execute("""
        UPDATE call_competitors cc
        SET created_by = m.created_by
        FROM meetings m
        WHERE cc.meeting_id = m.id AND cc.created_by IS NULL
    """)
    op.execute("""
        UPDATE call_objections co
        SET created_by = m.created_by
        FROM meetings m
        WHERE co.meeting_id = m.id AND co.created_by IS NULL
    """)


def downgrade() -> None:
    # Drop indexes
    for table in ["call_pain_points", "call_feature_requests", "call_competitors", "call_objections"]:
        op.drop_index(f"ix_{table}_team_user_date", table_name=table)

    # Drop created_by columns
    for table in ["call_intelligence", "call_pain_points", "call_feature_requests", "call_competitors", "call_objections"]:
        op.drop_column(table, "created_by")

    # Drop call_questions table
    op.drop_index("ix_call_questions_team_user_date", table_name="call_questions")
    op.drop_index("ix_call_questions_team_date", table_name="call_questions")
    op.drop_table("call_questions")
