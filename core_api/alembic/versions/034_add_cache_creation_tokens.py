"""tier 4.0.a — add cache_creation_tokens to ai_prompt_log

Anthropic's prompt caching API distinguishes:
  - cache_creation_input_tokens (1.25× base for 5m TTL, 2× for 1h)
  - cache_read_input_tokens (0.1× base) — already mapped to cache_read_tokens

Today's schema only tracks the read side. Without this column, write
costs either get lumped into input_tokens (under-attributed at 1× rate)
or vanish — and we lose the ability to compute cache hit ratio, which
is the key health metric for prompt caching.

Additive + non-blocking: nullable=False with server_default="0", safe to
apply on a live DB.

Revision ID: 034_cache_creation_tokens
Revises: 033_summary_email_sent_at
Create Date: 2026-05-09 23:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "034_cache_creation_tokens"
down_revision: Union[str, None] = "033_summary_email_sent_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_prompt_log",
        sa.Column(
            "cache_creation_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_prompt_log", "cache_creation_tokens")
