"""add plan fields to teams and is_super_admin to users

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-02-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add is_super_admin to users
    op.add_column(
        "users",
        sa.Column(
            "is_super_admin",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    # Add plan fields to teams
    op.add_column(
        "teams",
        sa.Column(
            "plan",
            sa.String(50),
            nullable=False,
            server_default="free",
        ),
    )
    op.add_column(
        "teams",
        sa.Column("plan_limits", sa.Text(), nullable=True),
    )
    op.add_column(
        "teams",
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("teams", "trial_ends_at")
    op.drop_column("teams", "plan_limits")
    op.drop_column("teams", "plan")
    op.drop_column("users", "is_super_admin")
