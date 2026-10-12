"""initial schema - teams, meetings, transcriptions, memoir_summaries, weekly_digests

Revision ID: 001_initial_schema
Revises: None
Create Date: 2026-02-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- teams ---
    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("crm_type", sa.String(50)),
        sa.Column("team_size_range", sa.String(50)),
        sa.Column("intelligence_priority", sa.String(100)),
        sa.Column("company_description", sa.Text()),
        sa.Column("icp_description", sa.Text()),
        sa.Column("known_competitors", postgresql.JSONB(), server_default="[]"),
        sa.Column("digest_day", sa.String(50), server_default="monday_morning"),
        sa.Column("blocker_alerts", sa.Boolean(), server_default="true"),
    )

    # --- team_members ---
    op.create_table(
        "team_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column("invite_code", sa.String(20), unique=True),
        sa.Column("invite_status", sa.String(20), server_default="accepted"),
        sa.Column("joined_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_team_members_team_user", "team_members", ["team_id", "user_id"], unique=True
    )

    # --- meetings ---
    op.create_table(
        "meetings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(500)),
        sa.Column("meeting_url", sa.Text(), nullable=False),
        sa.Column("bot_job_id", sa.String(255)),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending", index=True),
        sa.Column("source", sa.String(20), server_default="manual"),
        sa.Column("calendar_event_id", sa.String(500)),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("recording_path", sa.Text()),
        sa.Column("transcript_path", sa.Text()),
        sa.Column("analysis_path", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_meetings_team_status", "meetings", ["team_id", "status"])
    op.create_index("ix_meetings_team_created", "meetings", ["team_id", "created_at"])

    # --- transcriptions ---
    op.create_table(
        "transcriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column("search_vector", postgresql.TSVECTOR()),
        sa.Column("speaker_count", sa.Integer(), server_default="0"),
        sa.Column("word_count", sa.Integer(), server_default="0"),
        sa.Column("language", sa.String(10), server_default="en"),
        sa.Column("segments_path", sa.Text()),
        sa.Column("full_text_path", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_transcriptions_search",
        "transcriptions",
        ["search_vector"],
        postgresql_using="gin",
    )

    # --- memoir_summaries ---
    op.create_table(
        "memoir_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column("overall_confidence_score", sa.Integer(), server_default="0"),
        sa.Column("crm_push_status", sa.String(20), server_default="pending"),
        sa.Column("crm_pushed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "previous_meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="SET NULL"),
        ),
        sa.Column("field_changes_path", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )

    # --- weekly_digests ---
    op.create_table(
        "weekly_digests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("week_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("week_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_calls", sa.Integer(), server_default="0"),
        sa.Column("total_reps", sa.Integer(), server_default="0"),
        sa.Column("headline", sa.Text()),
        sa.Column("content_path", sa.Text()),
        sa.Column(
            "previous_digest_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("weekly_digests.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_weekly_digests_team_week",
        "weekly_digests",
        ["team_id", "week_start"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("weekly_digests")
    op.drop_table("memoir_summaries")
    op.drop_table("transcriptions")
    op.drop_table("meetings")
    op.drop_table("team_members")
    op.drop_table("teams")
