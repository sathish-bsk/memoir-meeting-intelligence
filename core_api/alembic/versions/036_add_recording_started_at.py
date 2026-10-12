"""add recording_started_at column to meetings

Production-observed (May 2026): the floating button flipped to
"Stop Recording" before the bot's FFmpeg recorder was actually
capturing audio, losing the first 1-2 s of speech.

Today meeting.status="recording" is set by the joined_meeting webhook
(bot admitted) — but the bot's recorder takes another 2-3 s to spin
up before actual capture begins. The recording_started webhook fires
when the bot's recorder is actually capturing audio, but until this
migration it had no DB side-effect.

This column gives the recording_started webhook somewhere to write
its timestamp. The extension polls (status, recording_started_at) and
only flips the button to "Stop Recording" when BOTH:
  - status = "recording"   (bot admitted)
  - recording_started_at is not null   (bot recorder actually capturing)

Old meetings (no recording_started_at) all have status in terminal
states, so the new poll branch never fires for them. Nullable, no
backfill needed.

Revision ID: 036_recording_started_at
Revises: 035_call_intelligence_version
Create Date: 2026-05-14 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "036_recording_started_at"
down_revision: Union[str, None] = "035_call_intelligence_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("recording_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meetings", "recording_started_at")
