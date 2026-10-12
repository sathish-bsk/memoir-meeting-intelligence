"""add email_templates and email_log tables

Revision ID: 007_email_templates_and_log
Revises: 006_calendar_user_and_slack
Create Date: 2026-02-18 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "007_email_templates_and_log"
down_revision: Union[str, None] = "006_calendar_user_and_slack"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- email_templates ---
    op.create_table(
        "email_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # --- email_log ---
    op.create_table(
        "email_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("to_email", sa.String(320), nullable=False),
        sa.Column("template_name", sa.String(255), nullable=False),
        sa.Column("template_data", postgresql.JSONB(), nullable=True),
        sa.Column("trigger_type", sa.String(50), nullable=False),
        sa.Column("triggered_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("via", sa.String(50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_email_log_created_at", "email_log", ["created_at"])
    op.create_index("ix_email_log_template_name", "email_log", ["template_name"])
    op.create_index("ix_email_log_triggered_by", "email_log", ["triggered_by"])

    # --- Seed templates ---
    op.execute("""
INSERT INTO email_templates (name, subject, html_body, description, category) VALUES
(
  'email_verification',
  'Verify your email — Memoir',
  '<div style="font-family: -apple-system, BlinkMacSystemFont, ''Segoe UI'', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
  <h2 style="color: #18181b; margin-bottom: 8px;">Verify your email</h2>
  <p style="color: #52525b; font-size: 15px; line-height: 1.6;">
    Hi {name}, please verify your email address by clicking the button below.
  </p>
  <a href="{link}" style="display: inline-block; background: #18181b; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 500; margin: 16px 0;">
    Verify email
  </a>
  <p style="color: #a1a1aa; font-size: 13px; margin-top: 24px;">
    If you didn''t create an account, you can safely ignore this email.
  </p>
</div>',
  'Sent when a user signs up to verify their email address',
  'transactional'
),
(
  'email_change_verification',
  'Confirm your new email — Memoir',
  '<div style="font-family: -apple-system, BlinkMacSystemFont, ''Segoe UI'', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
  <h2 style="color: #18181b; margin-bottom: 8px;">Confirm your new email</h2>
  <p style="color: #52525b; font-size: 15px; line-height: 1.6;">
    Hi {name}, you requested to change your email address. Click below to confirm.
  </p>
  <a href="{link}" style="display: inline-block; background: #18181b; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 500; margin: 16px 0;">
    Confirm new email
  </a>
  <p style="color: #a1a1aa; font-size: 13px; margin-top: 24px;">
    If you didn''t request this change, you can safely ignore this email.
  </p>
</div>',
  'Sent when a user requests to change their email address',
  'transactional'
),
(
  'password_reset',
  'Reset your password — Memoir',
  '<div style="font-family: -apple-system, BlinkMacSystemFont, ''Segoe UI'', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
  <h2 style="color: #18181b; margin-bottom: 8px;">Reset your password</h2>
  <p style="color: #52525b; font-size: 15px; line-height: 1.6;">
    Hi {name}, click below to reset your password. This link expires in 1 hour.
  </p>
  <a href="{link}" style="display: inline-block; background: #18181b; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 500; margin: 16px 0;">
    Reset password
  </a>
  <p style="color: #a1a1aa; font-size: 13px; margin-top: 24px;">
    If you didn''t request a password reset, you can safely ignore this email.
  </p>
</div>',
  'Sent when a user requests a password reset',
  'transactional'
),
(
  'team_invite',
  'You''re invited to join {team_name} on Memoir',
  '<div style="font-family: -apple-system, BlinkMacSystemFont, ''Segoe UI'', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
  <h2 style="color: #18181b; margin-bottom: 8px;">You''re invited!</h2>
  <p style="color: #52525b; font-size: 15px; line-height: 1.6;">
    {inviter_name} has invited you to join <strong>{team_name}</strong> on Memoir.
  </p>
  <p style="color: #52525b; font-size: 15px; line-height: 1.6;">
    Your invite code is: <strong style="font-size: 18px; letter-spacing: 1px;">{invite_code}</strong>
  </p>
  <a href="{link}" style="display: inline-block; background: #18181b; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 500; margin: 16px 0;">
    Accept invite
  </a>
  <p style="color: #a1a1aa; font-size: 13px; margin-top: 24px;">
    If you don''t have a Memoir account yet, you''ll need to sign up first, then use the link above to join the team.
  </p>
</div>',
  'Sent when a team member is invited to join a team',
  'transactional'
),
(
  'meeting_summary',
  'Call Summary: {title} — Memoir',
  '<div style="font-family: -apple-system, BlinkMacSystemFont, ''Segoe UI'', Roboto, sans-serif; max-width: 560px; margin: 0 auto; padding: 40px 20px;">
  <h2 style="color: #18181b; margin-bottom: 8px;">Your call has been transcribed</h2>
  <p style="color: #52525b; font-size: 15px; line-height: 1.6;">
    Hi {name}, your call <strong>{title}</strong> has been transcribed and analyzed.
  </p>

  <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
    <tr>
      <td style="padding: 8px 12px; background: #f4f4f5; border-radius: 6px 0 0 0; color: #52525b; font-size: 13px;">Duration</td>
      <td style="padding: 8px 12px; background: #f4f4f5; border-radius: 0 6px 0 0; color: #18181b; font-size: 13px; font-weight: 500;">{duration}</td>
    </tr>
    <tr>
      <td style="padding: 8px 12px; background: #fafafa; border-radius: 0 0 0 6px; color: #52525b; font-size: 13px;">Speakers</td>
      <td style="padding: 8px 12px; background: #fafafa; border-radius: 0 0 6px 0; color: #18181b; font-size: 13px; font-weight: 500;">{speaker_count}</td>
    </tr>
  </table>

  {summary_section}

  {action_items_section}

  {key_topics_section}

  <a href="{call_url}" style="display: inline-block; background: #18181b; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 500; margin: 16px 0;">
    View full call
  </a>
  <p style="color: #a1a1aa; font-size: 12px; margin-top: 32px;">
    You received this email because a call was recorded on your Memoir account.
    You can manage notification preferences in your account settings.
  </p>
</div>',
  'Sent after a meeting transcription and analysis is completed',
  'system'
);
""")


def downgrade() -> None:
    op.drop_index("ix_email_log_triggered_by", table_name="email_log")
    op.drop_index("ix_email_log_template_name", table_name="email_log")
    op.drop_index("ix_email_log_created_at", table_name="email_log")
    op.drop_table("email_log")
    op.drop_table("email_templates")
