"""Seed approve/decline join request email templates.

Revision ID: 013_join_email_templates
Revises: 012_domain_and_request_to_join
Create Date: 2026-02-22 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "013_join_email_templates"
down_revision: Union[str, None] = "012_domain_and_request_to_join"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
INSERT INTO email_templates (name, subject, html_body, description, category) VALUES
(
  'join_request_approved',
  'Welcome to {team_name} on Memoir',
  '<div style="font-family: -apple-system, BlinkMacSystemFont, ''Segoe UI'', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
  <h2 style="color: #18181b; margin-bottom: 8px;">You''re in!</h2>
  <p style="color: #52525b; font-size: 15px; line-height: 1.6;">
    Hi {name}, great news — your request to join <strong>{team_name}</strong> has been approved.
  </p>
  <p style="color: #52525b; font-size: 15px; line-height: 1.6;">
    You now have full access to your team''s calls, intelligence, and insights.
  </p>
  <a href="{link}" style="display: inline-block; background: #18181b; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 500; margin: 16px 0;">
    Go to your dashboard
  </a>
  <p style="color: #a1a1aa; font-size: 13px; margin-top: 24px;">
    You received this email because you requested to join a team on Memoir.
  </p>
</div>',
  'Sent when an admin approves a domain-based join request',
  'transactional'
),
(
  'join_request_declined',
  'Update on your request to join {team_name}',
  '<div style="font-family: -apple-system, BlinkMacSystemFont, ''Segoe UI'', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
  <h2 style="color: #18181b; margin-bottom: 8px;">Request update</h2>
  <p style="color: #52525b; font-size: 15px; line-height: 1.6;">
    Hi {name}, your request to join <strong>{team_name}</strong> was not approved at this time.
  </p>
  <p style="color: #52525b; font-size: 15px; line-height: 1.6;">
    You can still use Memoir with your own workspace — all your calls and insights are waiting for you.
  </p>
  <a href="{link}" style="display: inline-block; background: #18181b; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 500; margin: 16px 0;">
    Go to your dashboard
  </a>
  <p style="color: #a1a1aa; font-size: 13px; margin-top: 24px;">
    You received this email because you requested to join a team on Memoir.
  </p>
</div>',
  'Sent when an admin declines a domain-based join request',
  'transactional'
);
""")


def downgrade() -> None:
    op.execute("DELETE FROM email_templates WHERE name IN ('join_request_approved', 'join_request_declined');")
