"""Add tab audio upload sessions and chunks tables.

Changes:
- tab_audio_upload_sessions: one row per tab-audio recording session,
  keyed by meeting_id (UNIQUE)
- tab_audio_upload_chunks: one row per uploaded chunk, keyed by
  (session_id, seq)

Revision ID: 024_tab_audio_sessions
Revises: 023_dashboard_and_teams_cloud
Create Date: 2026-03-03 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "024_tab_audio_sessions"
down_revision = "023_dashboard_and_teams_cloud"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Create tab_audio_upload_sessions
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE tab_audio_upload_sessions (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            meeting_id   UUID NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
            status       VARCHAR(30) NOT NULL DEFAULT 'active',
            total_chunks INTEGER DEFAULT 0,
            total_bytes  BIGINT DEFAULT 0,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            UNIQUE(meeting_id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_tab_audio_sessions_meeting
            ON tab_audio_upload_sessions(meeting_id)
    """)

    # -------------------------------------------------------------------------
    # 2. Create tab_audio_upload_chunks
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE tab_audio_upload_chunks (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id  UUID NOT NULL REFERENCES tab_audio_upload_sessions(id) ON DELETE CASCADE,
            seq         INTEGER NOT NULL,
            sha256      VARCHAR(64),
            size_bytes  INTEGER,
            start_ms    BIGINT,
            duration_ms BIGINT,
            file_path   VARCHAR(500),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(session_id, seq)
        )
    """)
    op.execute("""
        CREATE INDEX ix_tab_audio_chunks_session
            ON tab_audio_upload_chunks(session_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tab_audio_upload_chunks")
    op.execute("DROP TABLE IF EXISTS tab_audio_upload_sessions")
