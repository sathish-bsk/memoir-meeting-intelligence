"""Add domain to teams + requested fields to team_members for domain-based join.

Revision ID: 012_domain_and_request_to_join
Revises: 011_filter_and_card_enhancements
Create Date: 2026-02-22 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "012_domain_and_request_to_join"
down_revision: Union[str, None] = "011_filter_and_card_enhancements"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # teams.domain — extracted from admin's email at team creation
    op.add_column("teams", sa.Column("domain", sa.String(255), nullable=True))
    op.create_index("ix_teams_domain", "teams", ["domain"])

    # team_members.requested_at / requested_message — for domain-based join requests
    op.add_column(
        "team_members",
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "team_members",
        sa.Column("requested_message", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("team_members", "requested_message")
    op.drop_column("team_members", "requested_at")
    op.drop_index("ix_teams_domain", table_name="teams")
    op.drop_column("teams", "domain")
