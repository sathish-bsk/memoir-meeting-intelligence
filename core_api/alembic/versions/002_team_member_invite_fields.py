"""add invited_email and invited_name to team_members

Revision ID: 002_team_member_invite_fields
Revises: 001_initial_schema
Create Date: 2026-02-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002_team_member_invite_fields"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PLACEHOLDER_USER_ID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    # Add invited_email and invited_name columns
    op.add_column("team_members", sa.Column("invited_email", sa.String(320), nullable=True))
    op.add_column("team_members", sa.Column("invited_name", sa.String(255), nullable=True))

    # Drop old unique index that doesn't allow multiple pending invites
    op.drop_index("ix_team_members_team_user", table_name="team_members")

    # Recreate as partial unique index: only enforce uniqueness for real users (not placeholder)
    op.execute(
        f"""
        CREATE UNIQUE INDEX ix_team_members_team_user
        ON team_members (team_id, user_id)
        WHERE user_id != '{PLACEHOLDER_USER_ID}'
        """
    )

    # Add index on (team_id, invited_email) for dedup lookups
    op.create_index(
        "ix_team_members_team_invited_email",
        "team_members",
        ["team_id", "invited_email"],
    )


def downgrade() -> None:
    op.drop_index("ix_team_members_team_invited_email", table_name="team_members")
    op.drop_index("ix_team_members_team_user", table_name="team_members")

    # Restore original unique index
    op.create_index(
        "ix_team_members_team_user", "team_members", ["team_id", "user_id"], unique=True
    )

    op.drop_column("team_members", "invited_name")
    op.drop_column("team_members", "invited_email")
