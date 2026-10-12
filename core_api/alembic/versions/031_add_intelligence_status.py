"""add intelligence_status column to meetings

Revision ID: 031_add_intelligence_status
Revises: 030_meeting_summary_email
Create Date: 2026-04-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "031_add_intelligence_status"
down_revision: Union[str, None] = "030_meeting_summary_email"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("intelligence_status", sa.String(50), server_default=sa.text("'pending'"), nullable=True),
    )

    # Backfill: meetings that are already completed should have intelligence_status = 'completed'
    op.execute(
        "UPDATE meetings SET intelligence_status = 'completed' WHERE status = 'completed'"
    )


def downgrade() -> None:
    op.drop_column("meetings", "intelligence_status")
