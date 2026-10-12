"""Phase 3 (notetaker-email plan §5) — cookies-only authenticated
browser session for the bot's Meet / Teams joins.

Replaces the prior persistent-profile approach. The persistent profile
crashed on cross-platform OS-binary incompatibility (Mac-bootstrapped
profile → Linux Chromium → `launch_persistent_context` aborted). Cookies
are pure data; they ride across platforms cleanly.

Verified 2026-05-24 via scripts/probe-cookies-*.py against
hello@meetmemoir.com on Workspace:
  - meet.google.com/landing  → AUTH_OK
  - mail.google.com          → AUTH_OK (inbox)
  - myaccount.google.com     → AUTH_OK

Failure semantics are unchanged: any error path returns None and the
caller falls back to anonymous guest mode. Recording is never blocked
by Phase 3 going sideways.

Storage layout in R2 (private bucket, same as recordings):
    Key:  bot-profiles/{provider}-cookies.json
    Body: JSON array, each element a Playwright cookie dict
          (output of `BrowserContext.cookies()` on the bootstrap host)

Env vars retain the legacy `*_PROFILE_KEY` name even though the object
behind them is now JSON; renaming would force operator env-var churn.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Playwright

logger = logging.getLogger(__name__)


# ── Provider detection ────────────────────────────────────────────────


def provider_from_url(meeting_url: str) -> Optional[str]:
    """Return 'google' / 'microsoft' for URLs we can authenticate on,
    or None for anything else (Zoom, consumer Meet without Workspace,
    Whereby, etc).
    """
    u = (meeting_url or "").lower()
    if "meet.google.com" in u:
        return "google"
    if "teams.microsoft.com" in u or "teams.live.com" in u:
        return "microsoft"
    return None


def should_use_authenticated_session(meeting_url: str) -> bool:
    """Combine the auth-mode env var with provider eligibility."""
    if settings.MEMOIR_BOT_AUTH_MODE != "authenticated":
        return False
    return provider_from_url(meeting_url) in {"google", "microsoft"}


# ── Cookie storage management ─────────────────────────────────────────


@dataclass
class _AuthSession:
    provider: str
    source_key: str
    cookie_count: int


def _profile_key_for(provider: str) -> Optional[str]:
    """R2 object key holding the cookies JSON for `provider`, or None
    when Phase 3 isn't configured for it."""
    if provider == "google":
        return settings.MEMOIR_BOT_GOOGLE_PROFILE_KEY or None
    if provider == "microsoft":
        return settings.MEMOIR_BOT_MICROSOFT_PROFILE_KEY or None
    return None


# Host-mounted cache dir. Orchestrator mounts /data/bot-profiles RW
# into every bot container so a single download is reused across bots.
# One-time per host: `sudo mkdir -p /data/bot-profiles/cache`.
_COOKIE_CACHE_DIR = Path("/data/bot-profiles/cache")


def _head_r2_etag(provider: str, key: str) -> Optional[str]:
    """Cheap HEAD against R2 to read the current ETag. None on any
    failure — caller treats that as "freshness unverifiable" and uses
    the cached copy if one exists.
    """
    try:
        import boto3
        from botocore.client import Config as BotoConfig

        endpoint = os.environ.get("S3_ENDPOINT_URL") or ""
        bucket = os.environ.get("S3_BUCKET") or ""
        region = os.environ.get("S3_REGION") or os.environ.get("AWS_REGION") or "auto"
        if not endpoint or not bucket:
            return None

        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            config=BotoConfig(signature_version="s3v4"),
        )
        resp = s3.head_object(Bucket=bucket, Key=key)
        return (resp.get("ETag") or "").strip('"') or None
    except Exception as e:
        logger.warning(
            "Phase 3 (%s): R2 HEAD failed (will try cache then fresh download): %s",
            provider, e,
        )
        return None


async def _materialize_cookies(provider: str) -> Optional[list[dict]]:
    """Return the cookie list for `provider`, or None to fall back to
    anonymous guest mode.

    Cache strategy: HEAD R2 for the current ETag, cache hit if it
    matches the sidecar, else download fresh and replace atomically.
    Concurrent first-bot races resolve to one winner via `.replace()`.
    """
    key = _profile_key_for(provider)
    if not key:
        return None

    cache_path = _COOKIE_CACHE_DIR / f"{provider}-cookies.json"
    cache_etag_path = _COOKIE_CACHE_DIR / f"{provider}-cookies.etag"

    current_etag = _head_r2_etag(provider, key)

    use_cache = False
    if current_etag is not None and cache_path.exists() and cache_etag_path.exists():
        try:
            cached_etag = cache_etag_path.read_text().strip()
            if cached_etag and cached_etag == current_etag:
                use_cache = True
                logger.info(
                    "Phase 3 (%s): cache hit (etag %s…)",
                    provider, current_etag[:12],
                )
        except Exception:
            pass

    if not use_cache:
        try:
            import boto3
            from botocore.client import Config as BotoConfig

            endpoint = os.environ.get("S3_ENDPOINT_URL") or ""
            bucket = os.environ.get("S3_BUCKET") or ""
            region = os.environ.get("S3_REGION") or os.environ.get("AWS_REGION") or "auto"
            if not endpoint or not bucket:
                logger.warning(
                    "Phase 3 (%s): S3_ENDPOINT_URL or S3_BUCKET not set — falling back to guest",
                    provider,
                )
                return None

            s3 = boto3.client(
                "s3",
                endpoint_url=endpoint,
                region_name=region,
                config=BotoConfig(signature_version="s3v4"),
            )

            _COOKIE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp_path = cache_path.with_suffix(".json.tmp")
            s3.download_file(bucket, key, str(tmp_path))
            tmp_path.replace(cache_path)
            if current_etag:
                cache_etag_path.write_text(current_etag)
            logger.info(
                "Phase 3 (%s): downloaded cookies from R2 (etag %s…)",
                provider,
                (current_etag or "unknown")[:12],
            )
        except Exception as e:
            logger.warning(
                "Phase 3 (%s): could not fetch cookies from R2 key %s — falling back to guest: %s",
                provider, key, e,
            )
            return None

    try:
        cookies = json.loads(cache_path.read_text())
        if not isinstance(cookies, list):
            logger.warning(
                "Phase 3 (%s): cookies file at %s is not a JSON list — falling back to guest",
                provider, cache_path,
            )
            return None
        for c in cookies:
            ss = c.get("sameSite", "Lax")
            if ss not in ("Strict", "Lax", "None"):
                c["sameSite"] = "Lax"
        return cookies
    except Exception as e:
        logger.warning(
            "Phase 3 (%s): failed to parse cookies file — falling back to guest: %s",
            provider, e,
        )
        return None


# Light fingerprint evasion — the four signals Google's bot detection
# checks first. Verified sufficient by probe-cookies-inject-linux.py.
_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
"""


async def launch_authenticated_context(
    playwright: "Playwright",
    *,
    meeting_url: str,
    user_agent: str,
    browser_args: list[str],
) -> tuple[Optional["BrowserContext"], Optional[_AuthSession]]:
    """Launch a Chromium context with cookies-only auth for `meeting_url`.
    Returns `(context, session_marker)` on success or `(None, None)` on
    any failure — caller falls back to guest mode.
    """
    provider = provider_from_url(meeting_url)
    if provider is None:
        return None, None
    cookies = await _materialize_cookies(provider)
    if cookies is None:
        return None, None

    try:
        merged_args = list(browser_args) + ["--disable-blink-features=AutomationControlled"]
        browser = await playwright.chromium.launch(
            headless=False,  # Xvfb display already up via entrypoint.sh
            args=merged_args,
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=user_agent,
            permissions=["microphone", "camera"],
        )
        await context.add_init_script(_STEALTH_INIT_SCRIPT)
        await context.add_cookies(cookies)

        key = _profile_key_for(provider) or ""
        marker = _AuthSession(provider=provider, source_key=key, cookie_count=len(cookies))
        logger.info(
            "Phase 3 (%s): authenticated context ready (%d cookies, key=%s)",
            provider, marker.cookie_count, marker.source_key,
        )
        return context, marker
    except Exception as e:
        logger.warning(
            "Phase 3 (%s): failed to launch authenticated Chromium — falling back to guest: %s",
            provider, e,
        )
        return None, None


async def cleanup_profile(session: Optional[_AuthSession]) -> None:
    """No-op. Cookies-only auth has no on-disk session state to clean
    up — Playwright tears down the in-memory context on browser.close().
    Kept for caller-compat with the previous persistent-profile API.
    """
    return None
