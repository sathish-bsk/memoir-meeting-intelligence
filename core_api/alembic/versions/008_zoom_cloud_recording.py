"""add zoom_connections table, meetings.zoom_meeting_id, meetings.original_recording_path, transcriptions.recording_source

Revision ID: 008_zoom_cloud_recording
Revises: 007_email_templates_and_log
Create Date: 2026-02-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "008_zoom_cloud_recording"
down_revision: Union[str, None] = "007_email_templates_and_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── zoom_connections table ──
    op.create_table(
        "zoom_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("encrypted_tokens", sa.Text(), nullable=False),
        sa.Column("zoom_user_id", sa.String(255), nullable=False),
        sa.Column("zoom_email", sa.String(320), nullable=False),
        sa.Column("auto_import", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("recording_preference", sa.String(20), nullable=False, server_default=sa.text("'auto'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_zoom_connections_user"),
    )
    op.create_index("ix_zoom_connections_email", "zoom_connections", ["zoom_email"])
    op.create_index("ix_zoom_connections_team", "zoom_connections", ["team_id"])

    # ── meetings: add zoom_meeting_id + original_recording_path ──
    op.add_column("meetings", sa.Column("zoom_meeting_id", sa.String(100), nullable=True))
    op.add_column("meetings", sa.Column("original_recording_path", sa.Text(), nullable=True))
    op.create_index("ix_meetings_zoom_meeting_id", "meetings", ["zoom_meeting_id"])

    # ── transcriptions: add recording_source ──
    op.add_column("transcriptions", sa.Column("recording_source", sa.String(20), nullable=True, server_default=sa.text("'bot'")))


def downgrade() -> None:
    op.drop_column("transcriptions", "recording_source")
    op.drop_index("ix_meetings_zoom_meeting_id", table_name="meetings")
    op.drop_column("meetings", "original_recording_path")
    op.drop_column("meetings", "zoom_meeting_id")
    op.drop_index("ix_zoom_connections_team", table_name="zoom_connections")
    op.drop_index("ix_zoom_connections_email", table_name="zoom_connections")
    op.drop_table("zoom_connections")
