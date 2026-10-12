"""Add denormalized columns to memoir_summaries for list-view filtering and card display.

Revision ID: 011_filter_and_card_enhancements
Revises: 010_intelligence_pipeline
Create Date: 2026-02-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "011_filter_and_card_enhancements"
down_revision: Union[str, None] = "010_intelligence_pipeline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "memoir_summaries",
        sa.Column("summary_excerpt", sa.String(200), nullable=True),
    )
    op.add_column(
        "memoir_summaries",
        sa.Column("primary_participant_name", sa.String(255), nullable=True),
    )
    op.add_column(
        "memoir_summaries",
        sa.Column("primary_participant_company", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("memoir_summaries", "primary_participant_company")
    op.drop_column("memoir_summaries", "primary_participant_name")
    op.drop_column("memoir_summaries", "summary_excerpt")
