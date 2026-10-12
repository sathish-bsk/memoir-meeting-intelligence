"""
HTTP client for the Auth Service — fetches team plan info.
"""

import logging
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Simple in-memory cache: team_id -> (plan_info, expires_at)
_plan_cache: Dict[str, tuple] = {}
_CACHE_TTL_SECONDS = 60


async def sync_team_member(team_id: str, user_id: str, role: str = "member") -> bool:
    """Sync a team member to the auth service. Returns True on success."""
    url = f"{settings.AUTH_SERVICE_URL}/auth/internal/teams/{team_id}/members/sync"
    headers = {"X-Service-Key": settings.INTERNAL_SERVICE_KEY}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"user_id": user_id, "role": role}, headers=headers)
            if resp.status_code == 200:
                return True
            else:
                logger.warning(f"Auth service returned {resp.status_code} for member sync: {resp.text}")
                return False
    except Exception as e:
        logger.error(f"Failed to sync team member to auth service: {e}")
        return False


async def unsync_team_member(team_id: str, user_id: str) -> bool:
    """Remove a team member from the auth service. Returns True on success."""
    url = f"{settings.AUTH_SERVICE_URL}/auth/internal/teams/{team_id}/members/sync"
    headers = {"X-Service-Key": settings.INTERNAL_SERVICE_KEY}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request("DELETE", url, json={"user_id": user_id}, headers=headers)
            if resp.status_code == 200:
                return True
            else:
                logger.warning(f"Auth service returned {resp.status_code} for member unsync: {resp.text}")
                return False
    except Exception as e:
        logger.error(f"Failed to unsync team member from auth service: {e}")
        return False


async def batch_get_users(user_ids: list[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch multiple users by ID from auth service. Returns {user_id: {email, name, avatar_url}}."""
    if not user_ids:
        return {}

    url = f"{settings.AUTH_SERVICE_URL}/auth/internal/users/batch"
    headers = {"X-Service-Key": settings.INTERNAL_SERVICE_KEY}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"user_ids": user_ids}, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return {u["id"]: u for u in data.get("users", [])}
            else:
                logger.warning(f"Auth service returned {resp.status_code} for batch user lookup: {resp.text}")
                return {}
    except Exception as e:
        logger.error(f"Failed to reach auth service for batch user lookup: {e}")
        return {}


async def check_email_exists(email: str) -> Optional[Dict[str, Any]]:
    """Check if an email exists in the auth service. Returns {exists, user_id, name} or None on error."""
    url = f"{settings.AUTH_SERVICE_URL}/auth/internal/users/check-email"
    headers = {"X-Service-Key": settings.INTERNAL_SERVICE_KEY}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={"email": email}, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.warning(f"Auth service returned {resp.status_code} for email check: {resp.text}")
                return None
    except Exception as e:
        logger.error(f"Failed to reach auth service for email check: {e}")
        return None


async def get_team_plan(team_id: str) -> Optional[Dict[str, Any]]:
    """Fetch team plan info from auth service internal API.

    Returns dict with keys: plan, limits, trial_ends_at.
    Caches for 60 seconds to avoid calling auth on every request.
    """
    import time

    # Check cache
    cached = _plan_cache.get(team_id)
    if cached:
        plan_info, expires_at = cached
        if time.time() < expires_at:
            return plan_info

    url = f"{settings.AUTH_SERVICE_URL}/auth/internal/teams/{team_id}/plan"
    headers = {"X-Service-Key": settings.INTERNAL_SERVICE_KEY}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                plan_info = resp.json()
                _plan_cache[team_id] = (plan_info, time.time() + _CACHE_TTL_SECONDS)
                return plan_info
            else:
                logger.warning(f"Auth service returned {resp.status_code} for plan lookup: {resp.text}")
                return None
    except Exception as e:
        logger.error(f"Failed to reach auth service for plan lookup: {e}")
        return None
