"""Add missing columns for rich quotes across all flattened intelligence tables.

Revision ID: 016_intelligence_rich_quotes
Revises: 015_intelligence_phase2
Create Date: 2026-02-23 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "016_intelligence_rich_quotes"
down_revision: Union[str, None] = "015_intelligence_phase2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # call_pain_points — add speaker_name
    op.add_column("call_pain_points", sa.Column("speaker_name", sa.String(255), nullable=True))

    # call_feature_requests — add timestamp_in_call, speaker_name, prospect_company
    op.add_column("call_feature_requests", sa.Column("timestamp_in_call", sa.String(10), nullable=True))
    op.add_column("call_feature_requests", sa.Column("speaker_name", sa.String(255), nullable=True))
    op.add_column("call_feature_requests", sa.Column("prospect_company", sa.String(255), nullable=True))

    # call_competitors — add exact_quote, timestamp_in_call, speaker_name, prospect_company
    op.add_column("call_competitors", sa.Column("exact_quote", sa.Text(), nullable=True))
    op.add_column("call_competitors", sa.Column("timestamp_in_call", sa.String(10), nullable=True))
    op.add_column("call_competitors", sa.Column("speaker_name", sa.String(255), nullable=True))
    op.add_column("call_competitors", sa.Column("prospect_company", sa.String(255), nullable=True))

    # call_objections — add exact_quote, timestamp_in_call, speaker_name, prospect_company
    op.add_column("call_objections", sa.Column("exact_quote", sa.Text(), nullable=True))
    op.add_column("call_objections", sa.Column("timestamp_in_call", sa.String(10), nullable=True))
    op.add_column("call_objections", sa.Column("speaker_name", sa.String(255), nullable=True))
    op.add_column("call_objections", sa.Column("prospect_company", sa.String(255), nullable=True))

    # call_questions — add timestamp_in_call, speaker_name
    op.add_column("call_questions", sa.Column("timestamp_in_call", sa.String(10), nullable=True))
    op.add_column("call_questions", sa.Column("speaker_name", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("call_questions", "speaker_name")
    op.drop_column("call_questions", "timestamp_in_call")

    op.drop_column("call_objections", "prospect_company")
    op.drop_column("call_objections", "speaker_name")
    op.drop_column("call_objections", "timestamp_in_call")
    op.drop_column("call_objections", "exact_quote")

    op.drop_column("call_competitors", "prospect_company")
    op.drop_column("call_competitors", "speaker_name")
    op.drop_column("call_competitors", "timestamp_in_call")
    op.drop_column("call_competitors", "exact_quote")

    op.drop_column("call_feature_requests", "prospect_company")
    op.drop_column("call_feature_requests", "speaker_name")
    op.drop_column("call_feature_requests", "timestamp_in_call")

    op.drop_column("call_pain_points", "speaker_name")
