"""add token_invalid columns to calendar_connections

Revision ID: 032_add_token_invalid
Revises: 031_add_intelligence_status
Create Date: 2026-04-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "032_add_token_invalid"
down_revision: Union[str, None] = "031_add_intelligence_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "calendar_connections",
        sa.Column("token_invalid", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "calendar_connections",
        sa.Column("token_invalid_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("calendar_connections", "token_invalid_at")
    op.drop_column("calendar_connections", "token_invalid")
