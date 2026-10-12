"""add summary_email_sent_at to meetings

Revision ID: 033_summary_email_sent_at
Revises: 032_add_token_invalid
Create Date: 2026-05-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "033_summary_email_sent_at"
down_revision: Union[str, None] = "032_add_token_invalid"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("summary_email_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meetings", "summary_email_sent_at")
