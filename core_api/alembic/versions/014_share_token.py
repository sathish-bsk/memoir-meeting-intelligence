"""Add share_token and share_views columns to meetings.

Revision ID: 014_share_token
Revises: 013_join_email_templates
Create Date: 2026-02-22 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "014_share_token"
down_revision: Union[str, None] = "013_join_email_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("meetings", sa.Column("share_token", sa.String(64), nullable=True))
    op.add_column(
        "meetings",
        sa.Column("share_views", sa.Integer(), nullable=False, server_default="0"),
    )
    # Partial unique index — only on rows where share_token is not null
    op.create_index(
        "ix_meetings_share_token",
        "meetings",
        ["share_token"],
        unique=True,
        postgresql_where=sa.text("share_token IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_meetings_share_token", table_name="meetings")
    op.drop_column("meetings", "share_views")
    op.drop_column("meetings", "share_token")
