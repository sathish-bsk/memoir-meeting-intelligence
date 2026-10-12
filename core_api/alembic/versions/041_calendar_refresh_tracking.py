"""calendar_connections refresh tracking columns + partial index

Adds proactive OAuth-token refresh scaffolding (plan §C, 2026-05-29).
Until this lands, calendar tokens only refresh on-demand during a sync
attempt — when the refresh token expires, the next sync hits
invalid_grant, sets token_invalid=true, and the user must reconsent via
the Reconnect banner. A scheduled refresher (services/calendar_token_
refresher.py) needs three pieces of state per connection:

* next_refresh_at — when this row becomes refresh-eligible. Cron query
  filters on this; setting it to NULL parks a connection (used when
  refresh has permanently failed and we want to stop trying without
  losing the row).
* refresh_attempt_count — incremented on transient failures so we can
  cap retries and eventually give up.
* last_refresh_error — short string (e.g. "transient: timeout",
  "permanent: invalid_grant") for debugging via the Reconnect UI.

A partial b-tree index on (next_refresh_at) WHERE token_invalid=false
makes the cron query sub-millisecond regardless of table size. The
condition mirrors the cron's WHERE clause exactly so the planner can
use an index-only scan.

Backfill spreads the existing rows' next_refresh_at across the next
50 minutes (uniform random) so the first cron pass doesn't issue every
refresh request at once.

Revision ID: 041_calendar_refresh_tracking
Revises: 040_web_recorder_source
Create Date: 2026-05-29 22:50:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "041_calendar_refresh_tracking"
down_revision: Union[str, None] = "040_web_recorder_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "calendar_connections",
        sa.Column("next_refresh_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "calendar_connections",
        sa.Column(
            "refresh_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "calendar_connections",
        sa.Column("last_refresh_error", sa.Text(), nullable=True),
    )

    # Backfill: stagger existing valid rows across the next 50 min so
    # the first cron pass after deploy isn't a thundering herd.
    op.execute(
        """
        UPDATE calendar_connections
           SET next_refresh_at = NOW() + (random() * INTERVAL '50 minutes')
         WHERE token_invalid = false
        """
    )

    # Partial b-tree index. Cron query is:
    #   WHERE token_invalid = false AND next_refresh_at < NOW()
    # Including NULL-guard so the index doesn't carry parked rows.
    op.execute(
        """
        CREATE INDEX idx_calendar_connections_refresh_due
            ON calendar_connections (next_refresh_at)
         WHERE token_invalid = false
           AND next_refresh_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_calendar_connections_refresh_due")
    op.drop_column("calendar_connections", "last_refresh_error")
    op.drop_column("calendar_connections", "refresh_attempt_count")
    op.drop_column("calendar_connections", "next_refresh_at")
