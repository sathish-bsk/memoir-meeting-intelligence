"""add version column to call_intelligence

Retranscribed meetings create multiple call_intelligence rows (one per
pipeline run). Without a version column, queries like
`SELECT FROM call_intelligence WHERE meeting_id = X` with
`scalar_one_or_none()` raise MultipleResultsFound — silently breaking
the intelligence endpoint, the CRM auto-queue, and the CRM push
dispatcher (all caught + logged as 500/handler-error in May 11 audit).

Backfill: existing rows assigned version = row_number() ordered by
created_at ascending per meeting. The OLDEST row becomes v1, newest
becomes vN — matches the pattern existing client code already uses for
transcriptions/memoir_summaries.

Index added on (meeting_id, version) for the future "ORDER BY version
DESC LIMIT 1" query shape that replaces the latest-by-created_at hack.

Revision ID: 035_call_intelligence_version
Revises: 034_cache_creation_tokens
Create Date: 2026-05-11 14:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "035_call_intelligence_version"
down_revision: Union[str, None] = "034_cache_creation_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add nullable so the backfill UPDATE doesn't blow up the table,
    # then flip to NOT NULL once every row has a value.
    op.add_column(
        "call_intelligence",
        sa.Column("version", sa.Integer(), nullable=True),
    )

    # Backfill: number rows per meeting by created_at ascending, starting at 1.
    op.execute(
        """
        UPDATE call_intelligence ci
        SET version = numbered.rn
        FROM (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY meeting_id
                       ORDER BY created_at ASC, id ASC
                   ) AS rn
            FROM call_intelligence
        ) AS numbered
        WHERE ci.id = numbered.id
        """
    )

    # Flip to NOT NULL with server_default for future inserts that omit it.
    op.alter_column(
        "call_intelligence",
        "version",
        existing_type=sa.Integer(),
        nullable=False,
        server_default="1",
    )

    op.create_index(
        "ix_call_intelligence_meeting_version",
        "call_intelligence",
        ["meeting_id", "version"],
    )


def downgrade() -> None:
    op.drop_index("ix_call_intelligence_meeting_version", table_name="call_intelligence")
    op.drop_column("call_intelligence", "version")
