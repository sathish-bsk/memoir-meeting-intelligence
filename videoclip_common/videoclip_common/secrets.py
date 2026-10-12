"""AWS Secrets Manager fetcher with in-memory cache.

Phase 2 (notetaker-email plan §4.4) — the bot's service-account JSON
key lives in AWS Secrets Manager, not the database. The
`calendar_connections.encrypted_tokens` field for the bot row carries
only an ARN pointer; this module resolves that pointer to the actual
credential at runtime.

Reliability layers:
  - In-memory cache keyed by ARN with a 1h TTL — typical Secrets Manager
    fetch latency is ~100-300ms; caching keeps the join path fast.
  - On Secrets Manager outage, the cached value keeps working until the
    TTL expires. Combined with the access-token cache one layer up
    (typical ~1h Google access-token TTL), the bot survives ~2h of
    Secrets Manager unavailability with zero impact.
  - Logs but does not crash on transient errors — the caller can decide
    whether to retry or fall back.

Environment fallback for local dev / tests:
  - If the env var `MEMOIR_BOT_GOOGLE_SA_JSON` is set (path to a local
    JSON file OR the raw JSON string), the secret manager is bypassed
    entirely and that value is returned. Lets tests run without AWS
    credentials.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Cache entry TTL — long enough that we're not chatting with Secrets
# Manager constantly, short enough that rotations propagate within a
# reasonable window.
_DEFAULT_TTL_SECONDS = 3600  # 1 hour


@dataclass
class _CacheEntry:
    value: dict
    fetched_at: float


_cache_lock = threading.Lock()
_cache: dict[str, _CacheEntry] = {}


class SecretsFetchError(RuntimeError):
    """Raised when a secret cannot be retrieved AND no usable cache exists."""


def _local_override_value() -> Optional[dict]:
    """If MEMOIR_BOT_GOOGLE_SA_JSON is set, return its parsed JSON content.

    Accepts either:
      - A filesystem path to a JSON file (preferred for local dev).
      - The raw JSON string itself (for CI / one-off testing).

    Returns None if the env var is not set.
    """
    raw = os.getenv("MEMOIR_BOT_GOOGLE_SA_JSON")
    if not raw:
        return None
    # Heuristic: if the value starts with '{' it's inline JSON; else
    # treat it as a filesystem path.
    raw = raw.strip()
    if raw.startswith("{"):
        return json.loads(raw)
    with open(raw, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_secret_json(
    arn_or_name: str,
    *,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    region_name: Optional[str] = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Fetch a JSON-typed secret from AWS Secrets Manager (or local override).

    Returns the parsed JSON dict. Raises SecretsFetchError on hard failure
    when no cached fallback is available.
    """
    # Local override path — short-circuits everything for dev/test.
    override = _local_override_value()
    if override is not None:
        return override

    now = time.time()
    cached = None
    if use_cache:
        with _cache_lock:
            entry = _cache.get(arn_or_name)
            if entry and (now - entry.fetched_at) < ttl_seconds:
                return entry.value
            cached = entry  # may be stale; useful as fallback below

    try:
        # Lazy boto3 import so the rest of the stack doesn't carry it as
        # a hard dep when local overrides are used.
        import boto3  # type: ignore

        client = boto3.client(
            "secretsmanager",
            region_name=region_name or os.getenv("AWS_REGION", "ap-south-1"),
        )
        resp = client.get_secret_value(SecretId=arn_or_name)
        value = json.loads(resp["SecretString"])
    except Exception as e:
        if cached is not None:
            logger.warning(
                "Secrets Manager fetch failed (%s); serving stale cache from %ss ago",
                e,
                int(now - cached.fetched_at),
            )
            return cached.value
        raise SecretsFetchError(f"Failed to fetch secret {arn_or_name}: {e}") from e

    if use_cache:
        with _cache_lock:
            _cache[arn_or_name] = _CacheEntry(value=value, fetched_at=now)
    return value


def clear_cache() -> None:
    """Reset the in-memory cache. Useful for tests + manual rotation runbooks."""
    with _cache_lock:
        _cache.clear()
