"""Create share_links table, migrate call shares, drop old columns.

Revision ID: 017_share_links
Revises: 016_intelligence_rich_quotes
Create Date: 2026-02-23 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


# revision identifiers, used by Alembic.
revision: str = "017_share_links"
down_revision: Union[str, None] = "016_intelligence_rich_quotes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create share_links table
    op.create_table(
        "share_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("share_token", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column("share_type", sa.String(50), nullable=False),
        sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.Column("snapshot_data", JSONB, nullable=False, server_default="{}"),
        sa.Column("share_views", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 2. Migrate existing meeting share tokens into share_links
    op.execute("""
        INSERT INTO share_links (id, share_token, share_type, team_id, created_by, title, metadata, snapshot_data, share_views, created_at)
        SELECT
            gen_random_uuid(),
            m.share_token,
            'call',
            m.team_id,
            m.created_by,
            COALESCE(m.title, 'Untitled Call'),
            jsonb_build_object('meeting_id', m.id::text),
            '{}'::jsonb,
            COALESCE(m.share_views, 0),
            COALESCE(m.created_at, now())
        FROM meetings m
        WHERE m.share_token IS NOT NULL
    """)

    # 3. Drop old columns from meetings
    op.drop_index("ix_meetings_share_token", table_name="meetings")
    op.drop_column("meetings", "share_token")
    op.drop_column("meetings", "share_views")


def downgrade() -> None:
    # 1. Re-add columns to meetings
    op.add_column("meetings", sa.Column("share_token", sa.String(64), unique=True, nullable=True))
    op.add_column("meetings", sa.Column("share_views", sa.Integer, nullable=False, server_default="0"))
    op.create_index("ix_meetings_share_token", "meetings", ["share_token"], unique=True)

    # 2. Migrate call share data back
    op.execute("""
        UPDATE meetings m
        SET share_token = sl.share_token,
            share_views = sl.share_views
        FROM share_links sl
        WHERE sl.share_type = 'call'
          AND sl.metadata->>'meeting_id' = m.id::text
    """)

    # 3. Drop share_links table
    op.drop_table("share_links")
