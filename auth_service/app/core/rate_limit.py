"""
Redis-based sliding window rate limiting with progressive delay.
"""

import asyncio
import time
from typing import Optional

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import app_logger


# Progressive delay thresholds (failures → delay in seconds)
_DELAY_THRESHOLDS = [
    (5, 1),      # 5 failures → 1s delay
    (10, 5),     # 10 failures → 5s delay
    (15, 15),    # 15 failures → 15s delay
    (20, 60),    # 20 failures → 60s delay
    (25, 300),   # 25 failures → 5 min delay
]


async def check_rate_limit(
    redis: aioredis.Redis,
    key: str,
    window: int = None,
    max_attempts: int = None,
) -> Optional[float]:
    """Check rate limit for a key. Returns delay in seconds if limited, None if OK.

    Uses a sliding window counter. The key should be prefixed with the action type,
    e.g., 'login:email:user@example.com' or 'login:ip:1.2.3.4'.
    """
    if window is None:
        window = settings.RATE_LIMIT_LOGIN_WINDOW
    if max_attempts is None:
        max_attempts = settings.RATE_LIMIT_LOGIN_MAX

    rate_key = f"rate_limit:{key}"
    now = time.time()

    pipe = redis.pipeline()
    # Remove entries outside the window
    pipe.zremrangebyscore(rate_key, 0, now - window)
    # Count entries in the window
    pipe.zcard(rate_key)
    results = await pipe.execute()

    count = results[1]

    # Determine progressive delay based on failure count
    delay = 0.0
    for threshold, seconds in _DELAY_THRESHOLDS:
        if count >= threshold:
            delay = float(seconds)

    if count >= max_attempts:
        return delay if delay > 0 else 300.0  # Max delay if over limit

    return delay if delay > 0 else None


async def record_attempt(redis: aioredis.Redis, key: str, window: int = None):
    """Record a failed attempt for rate limiting."""
    if window is None:
        window = settings.RATE_LIMIT_LOGIN_WINDOW

    rate_key = f"rate_limit:{key}"
    now = time.time()

    pipe = redis.pipeline()
    pipe.zadd(rate_key, {str(now): now})
    pipe.expire(rate_key, window)
    await pipe.execute()


async def reset_rate_limit(redis: aioredis.Redis, key: str):
    """Reset rate limit counter (e.g., on successful login)."""
    await redis.delete(f"rate_limit:{key}")


async def apply_delay(delay: Optional[float]):
    """Apply progressive delay if rate limited."""
    if delay and delay > 0:
        app_logger.debug(f"Rate limit delay: {delay}s")
        await asyncio.sleep(delay)
