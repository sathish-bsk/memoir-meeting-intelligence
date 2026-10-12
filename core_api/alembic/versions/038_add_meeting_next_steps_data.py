"""add meetings.next_steps_data JSONB column

The v3 meeting detail page surfaces editable Milestone + Next Meeting
fields under the Next Steps card. Those need shared (not per-user)
storage that survives retranscribe. meeting_notes is per-user;
call_intelligence belongs to the pipeline run.

This migration adds a free-form JSONB column so the new endpoint
can store whatever shape the v3 page needs without further schema
churn (currently {milestone, next_meeting_date, updated_by, updated_at}).

Revision ID: 038_meeting_next_steps_data
Revises: 037_meetings_status_check
Create Date: 2026-05-20 11:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "038_meeting_next_steps_data"
down_revision: Union[str, None] = "037_meetings_status_check"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column(
            "next_steps_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("meetings", "next_steps_data")
