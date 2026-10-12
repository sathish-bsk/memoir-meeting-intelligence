"""Redis cache for per-request TeamMember authorization checks.

The polling endpoints (/api/meetings/{id}/status,
/api/meetings/{id}/processing-status) and many others verify that the
caller belongs to the team the resource belongs to. Today that runs an
indexed `SELECT TeamMember.id WHERE team_id=:t AND user_id=:u AND
invite_status IN ('accepted', NULL) LIMIT 1` on every hit. With 5/sec
polling per active user, that's 5/sec/user of pure auth-overhead DB
traffic.

Cache contract:
  * Key: `team_member:{team_id}:{user_id}`
  * Value: "1" for accepted member, "0" for non-member.
  * TTL: 30 s (short — membership changes propagate within one TTL
    window; explicit invalidate() exists for the hot-path mutations).
  * Hit path: HGET + return.
  * Miss path: DB SELECT, then SET with TTL, then return.

Failure modes are absorbed silently — if Redis is down or misbehaves,
we fall through to a DB SELECT and serve the request correctly.

Reuses the same module-level aioredis client from
meeting_status_cache.py (avoids per-call TCP handshake — see
project_session_may30_pool_pressure_resolved memory).
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.team import TeamMember
from app.services import meeting_status_cache

logger = logging.getLogger(__name__)

# 30 s strikes a balance between staleness on revoke (user kicked from
# team has 30 s of residual access) and DB-pressure reduction. Combined
# with explicit invalidate() on member mutations, the worst-case lag
# becomes a non-issue in practice — only an attacker actively timing
# revoke vs. one in-flight request would see residual access.
TTL_SECONDS = 30


def _key(team_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return f"team_member:{team_id}:{user_id}"


async def is_team_member(
    db: AsyncSession,
    team_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """Return True if user_id is an accepted member of team_id.

    Tries Redis cache first; on miss runs the canonical DB SELECT and
    backfills cache. Identical semantics to the inline check that lived
    in /status and /processing-status before this helper existed.
    """
    redis = meeting_status_cache.get_shared_redis()
    key = _key(team_id, user_id)

    # Fast path: cache hit
    try:
        cached = await redis.get(key)
        if cached == "1":
            return True
        if cached == "0":
            return False
    except Exception as e:
        # Cache outage — fall through to DB. Don't poison logs at INFO
        # level; this could repeat thousands of times if Redis is down.
        logger.debug(f"team_membership_cache.is_team_member cache read failed: {e}")

    # Slow path: DB SELECT + backfill
    result = await db.execute(
        select(TeamMember.id)
        .where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
            or_(
                TeamMember.invite_status == "accepted",
                TeamMember.invite_status.is_(None),
            ),
        )
        .limit(1)
    )
    is_member = result.first() is not None

    try:
        await redis.set(key, "1" if is_member else "0", ex=TTL_SECONDS)
    except Exception as e:
        logger.debug(f"team_membership_cache.is_team_member cache write failed: {e}")

    return is_member


async def invalidate(team_id: uuid.UUID, user_id: Optional[uuid.UUID] = None) -> None:
    """Explicit DEL after team membership mutations.

    If user_id is None, scans and deletes every key for the team — used
    when bulk member operations (delete-all, role-mass-change) make it
    impractical to track individual user_ids. SCAN is bounded by Redis
    cursor behavior and won't block the server even on a large keyset.
    """
    redis = meeting_status_cache.get_shared_redis()
    try:
        if user_id is not None:
            await redis.delete(_key(team_id, user_id))
            return
        # Bulk path: SCAN team_member:{team_id}:*
        pattern = f"team_member:{team_id}:*"
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                await redis.delete(*keys)
            if cursor == 0:
                break
    except Exception as e:
        logger.warning(f"team_membership_cache.invalidate failed for team {team_id}: {e}")
