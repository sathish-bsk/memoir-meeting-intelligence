"""Widen meetings.status column from VARCHAR(20) to VARCHAR(30).

The status 'waiting_for_transcription' (26 chars) exceeds VARCHAR(20).

Revision ID: 026_widen_meeting_status
Revises: 025_dual_track_chunks
Create Date: 2026-03-07 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "026_widen_meeting_status"
down_revision = "025_dual_track_chunks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "meetings",
        "status",
        type_=sa.String(30),
        existing_type=sa.String(20),
    )


def downgrade() -> None:
    op.alter_column(
        "meetings",
        "status",
        type_=sa.String(20),
        existing_type=sa.String(30),
    )
