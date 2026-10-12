"""speaker_profiles, meeting_participants, voice_embedding_samples tables;
transcriptions.speaker_map, calendar_events.attendees columns

Revision ID: 009_speaker_identification
Revises: 008_zoom_cloud_recording
Create Date: 2026-02-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "009_speaker_identification"
down_revision: Union[str, None] = "008_zoom_cloud_recording"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── speaker_profiles table (team contact book) ──
    op.create_table(
        "speaker_profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("company", sa.String(255), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("internal_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("crm_provider", sa.String(50), nullable=True),
        sa.Column("crm_contact_id", sa.String(255), nullable=True),
        sa.Column(
            "source",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
        sa.Column("meeting_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_talk_seconds", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Unique email per team (partial index — only when email is not null)
    op.execute(
        """
        CREATE UNIQUE INDEX uq_speaker_profiles_team_email
        ON speaker_profiles (team_id, email)
        WHERE email IS NOT NULL
        """
    )
    op.create_index(
        "ix_speaker_profiles_team_name",
        "speaker_profiles",
        ["team_id", "display_name"],
    )
    op.execute(
        """
        CREATE INDEX ix_speaker_profiles_team_user
        ON speaker_profiles (team_id, internal_user_id)
        WHERE internal_user_id IS NOT NULL
        """
    )

    # ── meeting_participants table (per-meeting participation) ──
    op.create_table(
        "meeting_participants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "speaker_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("speaker_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("diarization_label", sa.String(20), nullable=True),
        sa.Column("platform_display_name", sa.String(255), nullable=True),
        sa.Column("role", sa.String(20), nullable=True),
        sa.Column(
            "mapping_source",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'unidentified'"),
        ),
        sa.Column(
            "mapping_confidence",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "talk_time_seconds",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "segment_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "word_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("joined_at", sa.Float(), nullable=True),
        sa.Column("left_at", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "meeting_id",
            "diarization_label",
            name="uq_meeting_participants_meeting_label",
        ),
    )
    op.create_index(
        "ix_meeting_participants_meeting",
        "meeting_participants",
        ["meeting_id"],
    )
    op.create_index(
        "ix_meeting_participants_profile",
        "meeting_participants",
        ["speaker_profile_id"],
    )

    # ── voice_embedding_samples table ──
    op.create_table(
        "voice_embedding_samples",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "speaker_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("speaker_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sa.Column(
            "embedding_model",
            sa.String(100),
            nullable=False,
            server_default=sa.text("'ecapa-tdnn'"),
        ),
        sa.Column("sample_duration_seconds", sa.Float(), nullable=False),
        sa.Column(
            "source_meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_diarization_label", sa.String(20), nullable=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column(
            "quality_score",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_voice_embedding_samples_profile",
        "voice_embedding_samples",
        ["speaker_profile_id"],
    )

    # ── transcriptions: add speaker_map JSONB ──
    op.add_column(
        "transcriptions",
        sa.Column("speaker_map", postgresql.JSONB(), nullable=True),
    )

    # ── calendar_events: add attendees JSONB ──
    op.add_column(
        "calendar_events",
        sa.Column("attendees", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("calendar_events", "attendees")
    op.drop_column("transcriptions", "speaker_map")

    op.drop_index("ix_voice_embedding_samples_profile", table_name="voice_embedding_samples")
    op.drop_table("voice_embedding_samples")

    op.drop_index("ix_meeting_participants_profile", table_name="meeting_participants")
    op.drop_index("ix_meeting_participants_meeting", table_name="meeting_participants")
    op.drop_table("meeting_participants")

    op.execute("DROP INDEX IF EXISTS ix_speaker_profiles_team_user")
    op.drop_index("ix_speaker_profiles_team_name", table_name="speaker_profiles")
    op.execute("DROP INDEX IF EXISTS uq_speaker_profiles_team_email")
    op.drop_table("speaker_profiles")
