"""Meeting notes table for chrome extension quick notes.

Revision ID: 019_meeting_notes
Revises: 018_notification_preferences
Create Date: 2026-02-24 14:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "019_meeting_notes"
down_revision = "018_notification_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meeting_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("meeting_id", "user_id", name="uq_meeting_notes_meeting_user"),
    )


def downgrade() -> None:
    op.drop_table("meeting_notes")
