"""calendar_connections channel-renewal tracking columns

Adds the same shape of scaffolding for watch-channel renewal that
migration 041 added for OAuth-token refresh. Until this lands, the
6-hourly renew_expiring_channels() job retries failed renewals forever
— for connections whose OAuth grant was revoked, that's one error log
every 6 hours indefinitely (witnessed in prod 2026-05-30 for connection
7aa4d607).

Two new columns:

* renewal_failure_count — incremented on transient renewal failures
  so we can cap retries. Reset to 0 on success.
* last_renewal_error — short string ("transient: <reason>" /
  "permanent: <reason>") for the audit trail and the calendar-health
  UI.

We reuse the existing token_invalid flag for the terminal marker —
a connection whose OAuth grant is dead can't renew the channel either,
so the two failure modes converge on the same UX (user must reconnect).
The renewal cron will additionally check token_invalid in its WHERE
clause so already-parked connections drop out.

No new index needed — the cron query is filtered by watch_expires_at
which is highly selective on its own.

Revision ID: 042_calendar_renewal_tracking
Revises: 041_calendar_refresh_tracking
Create Date: 2026-05-30 16:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "042_calendar_renewal_tracking"
down_revision: Union[str, None] = "041_calendar_refresh_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "calendar_connections",
        sa.Column(
            "renewal_failure_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "calendar_connections",
        sa.Column("last_renewal_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("calendar_connections", "last_renewal_error")
    op.drop_column("calendar_connections", "renewal_failure_count")
