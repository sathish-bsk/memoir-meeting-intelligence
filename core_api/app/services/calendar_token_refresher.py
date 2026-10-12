"""Proactive OAuth token refresher for calendar_connections.

Runs as an APScheduler job inside core_api. Every minute, pulls up to
BATCH_SIZE connections whose `next_refresh_at` is in the past, attempts
to refresh each, and updates `next_refresh_at` based on the outcome:

    success         → next_refresh_at = NOW() + REFRESH_INTERVAL
    transient fail  → next_refresh_at = NOW() + RETRY_INTERVAL
                      refresh_attempt_count += 1
                      (after MAX_ATTEMPTS transients → fall through to
                       permanent giveup)
    permanent fail  → next_refresh_at = NULL  (parked, drops from index)
                      token_invalid = true
                      token_invalid_at = NOW()

The selection query relies on the partial index from migration 041:

    CREATE INDEX idx_calendar_connections_refresh_due
        ON calendar_connections (next_refresh_at)
     WHERE token_invalid = false AND next_refresh_at IS NOT NULL

This makes the cron query sub-millisecond regardless of table size.

The cron is intentionally separate from the on-demand `refresh_if_needed`
path in calendar_sync.py — that one fires during sync. Both paths use
the same underlying token-refresh primitives, so they share invalidation
semantics. FOR UPDATE SKIP LOCKED keeps this safe if we ever scale to
multiple cron workers.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session
from app.services.calendar_sync import (
    _decrypt_google_tokens,
    _decrypt_outlook_tokens,
    _encrypt_tokens,
    _refresh_google_token_if_needed,
    _refresh_outlook_token_if_needed,
)

logger = logging.getLogger(__name__)

# ── Tunables ─────────────────────────────────────────────────────────

# Cron cadence and batch size — see plan §C. 50 rows/min = 3000/h is
# comfortably more than current scale needs and keeps per-pass DB cost
# tiny. Override via env if a deployment ever needs to scale.
BATCH_SIZE = int(getattr(settings, "CALENDAR_REFRESH_BATCH_SIZE", 50))

# Window before access-token expiry to schedule the next refresh.
# Google + Microsoft both issue ~1 h access tokens; 50 min keeps a 10 min
# safety margin without burning extra refresh cycles.
REFRESH_INTERVAL_MIN = int(getattr(settings, "CALENDAR_REFRESH_INTERVAL_MIN", 50))

# How long to wait before retrying after a transient failure. Short
# enough that a momentary network blip resolves quickly, long enough
# that we don't hammer Google/Microsoft during an actual outage.
RETRY_INTERVAL_MIN = int(getattr(settings, "CALENDAR_REFRESH_RETRY_MIN", 5))

# Number of transient failures to tolerate before giving up on a
# connection. After this many in a row we mark token_invalid=true so
# the user gets a banner instead of silent retry forever.
MAX_TRANSIENT_ATTEMPTS = 5


# ── Failure classification ───────────────────────────────────────────


# Permanent failures — refresh_token is no longer valid; the user must
# reconsent via the Reconnect banner. No amount of retrying helps.
_PERMANENT_GOOGLE = {"invalid_grant", "unauthorized_client", "invalid_client"}
_PERMANENT_OUTLOOK = {"invalid_grant", "unauthorized_client", "AADSTS70008", "AADSTS70043"}


class _PermanentFailure(Exception):
    """Marker exception for refresh outcomes that should park the row."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _TransientFailure(Exception):
    """Marker exception for refresh outcomes that should retry."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _classify_http_error(provider: str, err: Exception) -> Exception:
    """Map a raised exception to either _PermanentFailure or _TransientFailure.

    Caller decides what to do with the outcome. The classifier itself
    never touches the DB or makes more network calls.
    """
    # Network-level failures are always transient — they describe the
    # path between us and Google/Microsoft, not the validity of the
    # stored credential.
    if isinstance(err, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError)):
        return _TransientFailure(f"network: {type(err).__name__}")

    if isinstance(err, httpx.HTTPStatusError):
        status = err.response.status_code
        # Provider 5xx and 429 are always transient.
        if status >= 500 or status == 429:
            return _TransientFailure(f"http_{status}")
        # 400/401 with a permanent error code → reconsent required.
        if status in (400, 401):
            try:
                body = err.response.json()
            except Exception:
                body = {}
            err_code = body.get("error") or body.get("error_code") or ""
            permanent_set = _PERMANENT_OUTLOOK if provider == "outlook" else _PERMANENT_GOOGLE
            if err_code in permanent_set:
                return _PermanentFailure(err_code)
            # 400/401 without a recognized permanent code — treat as
            # transient to avoid prematurely locking out a row on an
            # unexpected response shape. The MAX_TRANSIENT_ATTEMPTS cap
            # still ensures we eventually give up.
            return _TransientFailure(f"http_{status}: {err_code or 'unknown'}")

    # ValueError("No refresh token available") is permanent — there's
    # nothing to retry against. The underlying helper raises with either
    # "refresh token" or "refresh_token" depending on provider; check
    # both.
    if isinstance(err, ValueError):
        msg = str(err).lower()
        if "refresh token" in msg or "refresh_token" in msg:
            return _PermanentFailure("no_refresh_token")

    # Anything else: assume transient. The cap will catch persistent
    # failures eventually.
    return _TransientFailure(f"unknown: {type(err).__name__}")


# ── Per-row refresh ──────────────────────────────────────────────────


def _do_provider_refresh(provider: str, encrypted_tokens: str) -> str:
    """Decrypt, refresh, re-encrypt. Returns the new encrypted token blob.

    Raises whatever the underlying refresh helper raises so the caller's
    classifier can decide transient vs permanent.
    """
    if provider == "google":
        token_data = _decrypt_google_tokens(encrypted_tokens)
        _refresh_google_token_if_needed(token_data)
        return _encrypt_tokens(token_data)
    if provider == "outlook":
        token_data = _decrypt_outlook_tokens(encrypted_tokens)
        _refresh_outlook_token_if_needed(token_data)
        return _encrypt_tokens(token_data)
    raise _PermanentFailure(f"unsupported_provider: {provider}")


async def _refresh_one(db: AsyncSession, row) -> None:
    """Refresh one connection row + persist the outcome via UPDATE.

    `row` is the result of the SELECT…FOR UPDATE in run_refresh_pass —
    has id, provider, encrypted_tokens, refresh_attempt_count fields.
    """
    now = datetime.now(timezone.utc)
    next_due = now + timedelta(minutes=REFRESH_INTERVAL_MIN)

    try:
        new_encrypted = _do_provider_refresh(row.provider, row.encrypted_tokens)
    except Exception as raw:
        classified = _classify_http_error(row.provider, raw)
        if isinstance(classified, _PermanentFailure):
            await _mark_permanent(db, row.id, classified.reason)
            logger.warning(
                f"calendar refresh: connection {row.id} ({row.provider}) "
                f"permanent failure: {classified.reason}"
            )
            return
        # Transient — increment attempt count, decide retry vs giveup.
        new_count = (row.refresh_attempt_count or 0) + 1
        if new_count >= MAX_TRANSIENT_ATTEMPTS:
            await _mark_permanent(
                db, row.id, f"transient_exhausted_after_{new_count}"
            )
            logger.warning(
                f"calendar refresh: connection {row.id} ({row.provider}) "
                f"transient cap reached; parking as invalid"
            )
            return
        retry_at = now + timedelta(minutes=RETRY_INTERVAL_MIN)
        await db.execute(
            sa_text(
                "UPDATE calendar_connections "
                "SET next_refresh_at = :next, "
                "    refresh_attempt_count = :count, "
                "    last_refresh_error = :err "
                "WHERE id = :id"
            ),
            {
                "next": retry_at,
                "count": new_count,
                "err": f"transient: {classified.reason}",
                "id": row.id,
            },
        )
        logger.info(
            f"calendar refresh: connection {row.id} ({row.provider}) "
            f"transient failure (attempt {new_count}/{MAX_TRANSIENT_ATTEMPTS}): "
            f"{classified.reason}"
        )
        return

    # Success path
    await db.execute(
        sa_text(
            "UPDATE calendar_connections "
            "SET encrypted_tokens = :tokens, "
            "    next_refresh_at = :next, "
            "    refresh_attempt_count = 0, "
            "    last_refresh_error = NULL "
            "WHERE id = :id"
        ),
        {
            "tokens": new_encrypted,
            "next": next_due,
            "id": row.id,
        },
    )
    logger.debug(
        f"calendar refresh: connection {row.id} ({row.provider}) refreshed; "
        f"next due {next_due.isoformat()}"
    )


async def _mark_permanent(db: AsyncSession, conn_id, reason: str) -> None:
    """Park the row out of the refresh index + set token_invalid=true."""
    await db.execute(
        sa_text(
            "UPDATE calendar_connections "
            "SET next_refresh_at = NULL, "
            "    token_invalid = true, "
            "    token_invalid_at = NOW(), "
            "    last_refresh_error = :err "
            "WHERE id = :id"
        ),
        {"err": f"permanent: {reason}", "id": conn_id},
    )


# ── Top-level cron entrypoint ────────────────────────────────────────


async def run_refresh_pass() -> int:
    """Process up to BATCH_SIZE due connections. Returns count processed.

    Called once per cron tick. Each row gets its own commit so a poison
    row can't block the rest of the batch. SKIP LOCKED makes this safe
    to run concurrently with another worker (or with the on-demand
    refresh path in calendar_sync).
    """
    processed = 0
    async with async_session() as db:  # type: ignore[call-arg]
        rows_result = await db.execute(
            sa_text(
                "SELECT id, provider, encrypted_tokens, refresh_attempt_count "
                "FROM calendar_connections "
                "WHERE token_invalid = false "
                "  AND next_refresh_at IS NOT NULL "
                "  AND next_refresh_at < NOW() "
                "ORDER BY next_refresh_at ASC "
                "LIMIT :limit "
                "FOR UPDATE SKIP LOCKED"
            ),
            {"limit": BATCH_SIZE},
        )
        rows = list(rows_result.mappings().all())

        for row in rows:
            # Wrap each row in its own try/commit so one failure (or
            # poison row) doesn't roll back the whole batch.
            try:
                await _refresh_one(
                    db,
                    type("Row", (), dict(row))(),  # turn mapping into attr access
                )
                await db.commit()
                processed += 1
            except Exception as e:
                await db.rollback()
                logger.error(
                    f"calendar refresh: unexpected error on row {row.get('id')}: {e}",
                    exc_info=True,
                )

    if processed:
        logger.info(f"calendar refresh pass: {processed} connections processed")
    return processed
