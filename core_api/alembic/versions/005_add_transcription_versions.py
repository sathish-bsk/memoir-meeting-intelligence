"""add version column to transcriptions and memoir_summaries

Revision ID: 005_add_transcription_versions
Revises: 004_transcription_jobs
Create Date: 2026-02-17 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "005_add_transcription_versions"
down_revision: Union[str, None] = "004_transcription_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add version column with default=1 to transcriptions
    op.add_column(
        "transcriptions",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    # Drop old unique constraint on meeting_id
    op.drop_constraint("transcriptions_meeting_id_key", "transcriptions", type_="unique")
    # Add composite unique constraint
    op.create_unique_constraint(
        "uq_transcriptions_meeting_version", "transcriptions", ["meeting_id", "version"]
    )

    # Add version column with default=1 to memoir_summaries
    op.add_column(
        "memoir_summaries",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    # Drop old unique constraint on meeting_id
    op.drop_constraint("memoir_summaries_meeting_id_key", "memoir_summaries", type_="unique")
    # Add composite unique constraint
    op.create_unique_constraint(
        "uq_memoir_summaries_meeting_version", "memoir_summaries", ["meeting_id", "version"]
    )


def downgrade() -> None:
    # memoir_summaries: drop composite, re-add single
    op.drop_constraint("uq_memoir_summaries_meeting_version", "memoir_summaries", type_="unique")
    op.create_unique_constraint("memoir_summaries_meeting_id_key", "memoir_summaries", ["meeting_id"])
    op.drop_column("memoir_summaries", "version")

    # transcriptions: drop composite, re-add single
    op.drop_constraint("uq_transcriptions_meeting_version", "transcriptions", type_="unique")
    op.create_unique_constraint("transcriptions_meeting_id_key", "transcriptions", ["meeting_id"])
    op.drop_column("transcriptions", "version")
