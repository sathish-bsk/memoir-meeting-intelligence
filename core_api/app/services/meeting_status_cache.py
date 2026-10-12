"""Redis cache for the /api/meetings/{id}/status and /processing-status endpoints.

The extension polls /status during joining/starting_to_record (1.5-5 s
cadence), and the v3 detail page polls /processing-status while the
pipeline runs. Each hit historically ran 2-3 indexed Postgres lookups
(Meeting by PK + TeamMember JOIN, plus a transcription_jobs SELECT for
the latest T2 row in the /processing-status case) — cheap individually,
but the cumulative pool pressure was real (2026-05-14 incident: 15-18 s
connection holds under sustained polling saturated the pool and stalled
webhook handlers for 14 s).

This cache stores the four top-level status fields both endpoints need
plus team_id (for the auth check) in a Redis hash keyed by meeting_id.
Webhook handlers and the meeting CREATE path call publish() after every
status-mutating commit; the read path tries Redis first and falls back
to DB on miss.

Cache contract:
- Non-terminal status → publish() writes the hash with a 24 h TTL.
- Terminal status (completed / failed / fast_email_only) → publish()
  DELs the key. The extension stops polling on terminal anyway, and
  the DB fallback will serve any straggler reads correctly.
- All Redis errors are swallowed and logged. The DB fallback is the
  source of truth; cache outages degrade to slow-but-correct.
- Schema changes (adding a field): bump CACHE_SCHEMA_REQUIRED_FIELDS so
  old entries treat-as-miss. The first read after deploy backfills a
  fresh hash with the new schema instead of serving partial data.
"""

import logging
from datetime import datetime
from typing import Optional, TypedDict

import redis.asyncio as aioredis
from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# Module-level Redis client backed by a connection pool. Built lazily on
# first use so import order in tests / dev scripts that never touch
# Redis stays unaffected. The pool keeps a small set of TCP connections
# alive across the worker's lifetime — previously every cascade call did
# aioredis.from_url(...).aclose(), which means one fresh TCP handshake +
# AUTH per webhook event. Under sustained traffic that cost (50-300 ms
# cold) was being paid while the caller still held its DB session, and
# webhook handlers crossing 5 s triggered the orchestrator retry storm
# we kept hitting. One shared client = one persistent socket = ms-level
# HSET regardless of traffic.
_shared_redis: Optional[aioredis.Redis] = None


def get_shared_redis() -> aioredis.Redis:
    """Return the process-wide Redis client (lazy init from settings)."""
    global _shared_redis
    if _shared_redis is None:
        # Late import — settings reads .env, which we don't want to do
        # at module import time for test isolation.
        from app.core.config import settings

        _shared_redis = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            # Keep a small pool: each uvicorn worker only runs one event
            # loop, so we rarely need many concurrent Redis ops. The pool
            # silently caps at this number and recycles connections.
            max_connections=10,
            health_check_interval=30,
        )
    return _shared_redis

# Fields a complete cache entry must carry. Adding a new field means
# adding it here too — _from_hash() returns None when any of these are
# missing, so old (pre-deploy) hashes get treated as a miss and
# backfilled by cascade_from_db() on the next read. Self-healing
# schema migration with no migration script needed.
CACHE_SCHEMA_REQUIRED_FIELDS = frozenset({
    "status",
    "team_id",
    "crm_extraction_status",
})

# Statuses past which no more status mutations are expected. publish()
# DELs the cache when entering one of these — the extension has already
# stopped polling and any straggler read can take the slower DB path.
TERMINAL_STATUSES = frozenset({"completed", "failed", "fast_email_only"})

# 24 h. If a DEL is ever missed (process crash between commit and
# publish, network blip), the key self-cleans before it can cause
# real bugs. The fallback path corrects stale reads in the meantime.
TTL_SECONDS = 86400


def _key(meeting_id) -> str:
    return f"meeting_status:{meeting_id}"


class CachedStatus(TypedDict):
    """Decoded shape of a cache HIT — fields restored to their canonical
    Python types (None for missing optional fields, not empty strings)."""

    status: str
    intelligence_status: Optional[str]
    recording_started_at: Optional[datetime]
    team_id: str
    # Resolved CRM extraction state — pending / processing / completed /
    # failed / skipped. Derived at cascade time from the latest T2
    # transcription_jobs row + intelligence_status (see
    # _derive_crm_extraction_status). NOT a column on Meeting.
    crm_extraction_status: str


def _from_hash(raw: dict) -> Optional[CachedStatus]:
    """Decode a Redis hash response into CachedStatus, or None if the
    entry is from an older schema (missing required fields).

    Empty strings round-trip back to None for nullable fields (Redis
    hashes can't store null directly without us picking some sentinel —
    empty-string is fine because none of these fields legitimately
    store empty).

    Treat-as-miss for missing required fields: returning None forces
    the caller to fall through to the DB path, which cascades a fresh
    hash with the current schema. Self-healing across deploys.
    """
    # Schema-required field guard. The OR-of-missing also coincidentally
    # protects against partial hash writes (HSET race, killed worker).
    if not CACHE_SCHEMA_REQUIRED_FIELDS.issubset(raw.keys()):
        return None
    rsa = raw.get("recording_started_at") or None
    return {
        "status": raw["status"],
        "intelligence_status": raw.get("intelligence_status") or None,
        "recording_started_at": datetime.fromisoformat(rsa) if rsa else None,
        "team_id": raw["team_id"],
        "crm_extraction_status": raw["crm_extraction_status"],
    }


def crm_status_with_processing_inference(
    cached_or_db_status: str, stages: dict
) -> str:
    """Read-time inference: if T2 is reporting progress but the
    top-level status hasn't been flipped off "pending" yet, surface it
    as "processing".

    Why: T2 transitions from pending → processing without firing a
    webhook, so the cached crm_extraction_status (only ever refreshed
    on webhook boundaries) stays at "pending" while T2 is actually
    in flight. TranscriptionBase writes crm_sync_percent to the
    meeting_progress hash as T2 runs, which is the same payload we
    already return. Use that signal — no DB write, no Redis write,
    no new webhook needed.

    Only "pending" gets upgraded. "completed" / "failed" / "skipped"
    are terminal-or-decisional and must pass through untouched.
    """
    if cached_or_db_status != "pending":
        return cached_or_db_status
    crm_percent = stages.get("crm_sync", {}).get("percent", 0)
    if crm_percent > 0:
        return "processing"
    return cached_or_db_status


async def _derive_crm_extraction_status(
    db: AsyncSession, meeting_id, intelligence_status: Optional[str]
) -> str:
    """Mirror the historical /processing-status crm_status derivation.

    - If a Type 2 transcription_jobs row exists, its status wins (the
      live T2 worker state: pending / processing / completed / failed).
    - Else, derive from intelligence_status:
        pending / processing → "pending" (T2 hasn't been queued yet)
        completed → "skipped" (intelligence finished, T2 was gated off
                    for this meeting — common for tab/upload paths)
        failed → "skipped" (no T2 will ever run)
        anything else → "pending"

    Note on pending→processing visibility: T2 flips from pending to
    processing without firing a webhook, so a cache entry written
    between intelligence_complete and crm_extraction_complete will say
    "pending" even when T2 is actually processing. The /processing-status
    endpoint compensates at read time using meeting_progress.crm_sync
    percent (writeup at the endpoint).
    """
    row = await db.execute(
        sa_text(
            "SELECT status FROM transcription_jobs "
            "WHERE meeting_id = :mid AND operation_type = 2 "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"mid": str(meeting_id)},
    )
    job = row.mappings().first()
    if job:
        return job["status"]
    if intelligence_status in ("pending", "processing"):
        return "pending"
    if intelligence_status in ("completed", "failed"):
        return "skipped"
    return "pending"


async def publish(
    redis: aioredis.Redis, meeting, *, crm_extraction_status: str
) -> None:
    """Push the meeting's current status fields to Redis.

    Call this AFTER db.commit() at every site that mutates
    meeting.status, meeting.intelligence_status, or
    meeting.recording_started_at. Calling before commit is incorrect —
    a rollback would leave Redis claiming a status that doesn't exist
    in Postgres.

    crm_extraction_status is derived (see _derive_crm_extraction_status)
    and passed in because the caller already has DB access — keeps this
    function pure-Redis. Most callers will use cascade_from_db() which
    bundles both DB reads and the publish call.

    On terminal status, DELs the key instead of writing. The TTL safety
    net catches the case where a process crashes between commit and the
    DEL — at worst, the cache lies for 24 h, and the DB-fallback path
    will still serve correct data to any reader that explicitly checks.
    """
    try:
        meeting_id = str(meeting.id)
        if meeting.status in TERMINAL_STATUSES:
            await redis.delete(_key(meeting_id))
            return
        rsa = (
            meeting.recording_started_at.isoformat()
            if meeting.recording_started_at
            else ""
        )
        await redis.hset(
            _key(meeting_id),
            mapping={
                "status": meeting.status,
                "intelligence_status": meeting.intelligence_status or "",
                "recording_started_at": rsa,
                "team_id": str(meeting.team_id),
                "crm_extraction_status": crm_extraction_status,
            },
        )
        await redis.expire(_key(meeting_id), TTL_SECONDS)
    except Exception as e:
        logger.warning(
            f"meeting_status_cache.publish failed for {meeting.id}: {e}"
        )


async def invalidate(redis: aioredis.Redis, meeting_id) -> None:
    """Explicit DEL. Use for paths that delete a meeting row outright
    (the publish() terminal path covers normal lifecycle completion)."""
    try:
        await redis.delete(_key(str(meeting_id)))
    except Exception as e:
        logger.warning(
            f"meeting_status_cache.invalidate failed for {meeting_id}: {e}"
        )


async def cascade_from_db(db: AsyncSession, redis_url: str, meeting_id) -> None:
    """Refresh cache from DB for the given meeting.

    Call AFTER db.commit() at every site that mutates meeting.status,
    meeting.intelligence_status, or meeting.recording_started_at — and
    at every site that inserts or transitions a Type 2 transcription
    job (T2 status drives crm_extraction_status).

    Re-reads the four Meeting fields by PK + the latest T2 row. The
    Meeting read is sub-ms (meetings_pkey index); the T2 read is one
    indexed lookup on (meeting_id, operation_type). Necessary because
    atomic UPDATE...WHERE paths in webhooks.py don't update the local
    ORM instance, so a publish(meeting) call alone would push stale
    data.

    Uses the process-wide Redis client (get_shared_redis). The
    redis_url argument is retained for call-site compatibility but
    ignored — switching to the shared client eliminated the per-call
    TCP handshake that was making each cascade 100-500 ms cold and
    triggering the orchestrator retry storm.
    """
    try:
        from app.models.meeting import Meeting

        row = (
            await db.execute(
                select(
                    Meeting.id,
                    Meeting.team_id,
                    Meeting.status,
                    Meeting.intelligence_status,
                    Meeting.recording_started_at,
                ).where(Meeting.id == meeting_id)
            )
        ).first()
        if row is None:
            return

        crm_status = await _derive_crm_extraction_status(
            db, meeting_id, row.intelligence_status
        )

        # Lightweight stand-in for the Meeting model — publish() reads
        # by attribute, doesn't need ORM semantics.
        from types import SimpleNamespace

        meeting_proxy = SimpleNamespace(
            id=row.id,
            team_id=row.team_id,
            status=row.status,
            intelligence_status=row.intelligence_status,
            recording_started_at=row.recording_started_at,
        )

        await publish(
            get_shared_redis(), meeting_proxy, crm_extraction_status=crm_status
        )
    except Exception as e:
        logger.warning(
            f"meeting_status_cache.cascade_from_db failed for {meeting_id}: {e}"
        )


async def read(redis: aioredis.Redis, meeting_id) -> Optional[CachedStatus]:
    """Return the cached status fields or None on miss / stale schema.

    A pre-schema-bump hash (missing crm_extraction_status) is treated
    as a miss so the caller falls back to DB and cascade_from_db()
    backfills with the current schema. Self-healing across deploys.
    """
    try:
        raw = await redis.hgetall(_key(str(meeting_id)))
        if not raw:
            return None
        return _from_hash(raw)
    except Exception as e:
        logger.warning(
            f"meeting_status_cache.read failed for {meeting_id}: {e}"
        )
        return None
