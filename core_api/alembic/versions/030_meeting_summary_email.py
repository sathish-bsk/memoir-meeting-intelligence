"""add meeting summary email tables and columns

Revision ID: 030_meeting_summary_email
Revises: 029_meeting_subscription
Create Date: 2026-04-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "030_meeting_summary_email"
down_revision: Union[str, None] = "029_meeting_subscription"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Add columns to memoir_summaries ---
    op.add_column(
        "memoir_summaries",
        sa.Column("external_share_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "memoir_summaries",
        sa.Column("external_share_approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "memoir_summaries",
        sa.Column("external_share_approved_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "memoir_summaries",
        sa.Column("summary_json", postgresql.JSONB(), nullable=True),
    )

    # --- email_unsubscribe_tokens ---
    op.create_table(
        "email_unsubscribe_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("token", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # --- email_suppressions ---
    op.create_table(
        "email_suppressions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reason", sa.String(50), server_default=sa.text("'unsubscribed'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("email", "team_id", name="uq_email_suppression"),
    )

    # --- Insert meeting_summary_v2 template ---
    op.execute("""
INSERT INTO email_templates (name, subject, html_body, description, category)
VALUES (
  'meeting_summary_v2',
  '{subject}',
  '{body}',
  'Meeting summary email v2 — dynamic subject and body rendered by caller',
  'system'
);
""")

    # --- Deactivate old meeting_summary template ---
    op.execute("""
UPDATE email_templates SET is_active = false WHERE name = 'meeting_summary';
""")


def downgrade() -> None:
    # Re-activate old template
    op.execute("UPDATE email_templates SET is_active = true WHERE name = 'meeting_summary';")

    # Remove new template
    op.execute("DELETE FROM email_templates WHERE name = 'meeting_summary_v2';")

    # Drop tables
    op.drop_table("email_suppressions")
    op.drop_table("email_unsubscribe_tokens")

    # Drop columns from memoir_summaries
    op.drop_column("memoir_summaries", "summary_json")
    op.drop_column("memoir_summaries", "external_share_approved_by")
    op.drop_column("memoir_summaries", "external_share_approved_at")
    op.drop_column("memoir_summaries", "external_share_approved")
