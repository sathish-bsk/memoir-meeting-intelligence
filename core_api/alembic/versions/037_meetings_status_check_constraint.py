"""add CHECK constraint on meetings.status to enforce canonical enum

The meetings.status column is declared String(100) with a free-text
docstring listing allowed values. The docstring drifted from reality
twice: it was missing 'waiting_for_transcription' (in active use,
21 rows orphaned on May 16 2026 cleanup). Without DB enforcement,
typos at any code-write site go silent until they break downstream
state machines.

This migration adds a CHECK constraint matching the 10 statuses
actually written by core_api today. Future status additions require
a follow-up migration that widens the IN list — that's intentional,
the friction is the feature.

Canonical statuses (inventoried May 17 2026):
    pending, scheduled, joining, waiting_in_lobby, recording,
    waiting_for_transcription, processing, completed, failed,
    fast_email_only

Safety pre-step: rows outside this set are coerced to 'failed'
(with ended_at backfilled to NOW() if null) before the constraint
is added. Lost statuses are unrecoverable from the DB alone, but
the May 16/17 cleanup already moved prod into the canonical set,
so the pre-step is expected to be a no-op in practice.

Revision ID: 037_meetings_status_check
Revises: 036_recording_started_at
Create Date: 2026-05-17 14:30:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "037_meetings_status_check"
down_revision: Union[str, None] = "036_recording_started_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Single source of truth for the constraint definition. Keep in sync
# with the comment on Meeting.status in core_api/app/models/meeting.py
# AND the cleanup script's "terminal" allowlist.
_VALID_STATUSES = (
    "pending",
    "scheduled",
    "joining",
    "waiting_in_lobby",
    "recording",
    "waiting_for_transcription",
    "processing",
    "completed",
    "failed",
    "fast_email_only",
)

_CHECK_EXPR = "status IN (" + ", ".join(f"'{s}'" for s in _VALID_STATUSES) + ")"


def upgrade() -> None:
    # Safety: coerce any rogue status to 'failed' before the constraint
    # would reject it. Expected to be a no-op on prod (May 16/17 cleanup
    # already aligned the data).
    op.execute(
        f"""
        UPDATE meetings
        SET status = 'failed',
            ended_at = COALESCE(ended_at, NOW())
        WHERE NOT ({_CHECK_EXPR})
        """
    )
    op.create_check_constraint(
        "meetings_status_valid",
        "meetings",
        _CHECK_EXPR,
    )


def downgrade() -> None:
    op.drop_constraint(
        "meetings_status_valid",
        "meetings",
        type_="check",
    )
