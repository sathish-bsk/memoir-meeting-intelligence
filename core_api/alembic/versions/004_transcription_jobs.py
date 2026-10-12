"""add transcription_jobs table for decoupled transcription queue

Revision ID: 004_transcription_jobs
Revises: 003_calendar_tables
Create Date: 2026-02-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "004_transcription_jobs"
down_revision: Union[str, None] = "003_calendar_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transcription_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("bot_job_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="waiting"),
        sa.Column("params", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column("result", postgresql.JSON(), nullable=True, server_default="{}"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSON(), nullable=True, server_default="{}"),
    )
    op.create_index("ix_transcription_jobs_status", "transcription_jobs", ["status"])
    op.create_index("ix_transcription_jobs_meeting", "transcription_jobs", ["meeting_id"])


def downgrade() -> None:
    op.drop_index("ix_transcription_jobs_meeting", table_name="transcription_jobs")
    op.drop_index("ix_transcription_jobs_status", table_name="transcription_jobs")
    op.drop_table("transcription_jobs")
