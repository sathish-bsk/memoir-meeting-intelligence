"""Add track column to tab_audio_upload_chunks for dual-track recording.

Changes:
- Add `track` VARCHAR(10) column (default 'mixed' for backward compat)
- Drop old unique constraint (session_id, seq)
- Add new unique constraint (session_id, seq, track)

Values: 'tab' (remote participants), 'mic' (local microphone), 'mixed' (legacy)

Revision ID: 025_dual_track_chunks
Revises: 024_tab_audio_sessions
Create Date: 2026-03-04 00:00:00.000000
"""

from alembic import op

revision = "025_dual_track_chunks"
down_revision = "024_tab_audio_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add track column with default for existing rows
    op.execute("""
        ALTER TABLE tab_audio_upload_chunks
        ADD COLUMN track VARCHAR(10) NOT NULL DEFAULT 'mixed'
    """)

    # Drop old unique constraint and add new one including track
    op.execute("""
        ALTER TABLE tab_audio_upload_chunks
        DROP CONSTRAINT tab_audio_upload_chunks_session_id_seq_key
    """)
    op.execute("""
        ALTER TABLE tab_audio_upload_chunks
        ADD CONSTRAINT uq_chunk_session_seq_track UNIQUE(session_id, seq, track)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE tab_audio_upload_chunks
        DROP CONSTRAINT IF EXISTS uq_chunk_session_seq_track
    """)
    op.execute("""
        ALTER TABLE tab_audio_upload_chunks
        ADD CONSTRAINT tab_audio_upload_chunks_session_id_seq_key UNIQUE(session_id, seq)
    """)
    op.execute("""
        ALTER TABLE tab_audio_upload_chunks
        DROP COLUMN IF EXISTS track
    """)
