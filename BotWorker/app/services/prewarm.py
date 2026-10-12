"""Pre-launch Chromium so /join can skip the ~12-15 s browser-launch cost.

Pool members run prewarm_browser() at startup (gated by BOT_POOL_MEMBER=1).
The launched browser + context + page are stashed in module-level handles.
When /join arrives, base_bot.MeetingBot.launch() calls consume_prewarm() to
hand them off and skip the cold-launch path — saving ~12-15 s on every
first-meeting-after-pool-refill.

System-mode only. Browser mode (AUDIO_CAPTURE_METHOD=browser) needs
launch_persistent_context() with a per-job user_data_dir and cannot reuse
a warm handle; consume_prewarm() returns None and the cold path runs.
"""
import asyncio
from typing import Optional

from playwright.async_api import async_playwright

from app.core.config import settings
from app.core.logging import app_logger

# Module-global warm handles. consume_prewarm() clears them so a subsequent
# /join can't double-consume (though by construction only one /join ever
# reaches a given pool-member container — single-use, AUTO_EXIT after meeting).
_prewarmed: dict = {
    "playwright": None,
    "browser": None,
    "context": None,
    "page": None,
}


async def _cleanup_partial() -> None:
    """Tear down whatever portion of the warm stack was constructed.

    Called from prewarm_browser's except block before re-raising so a failure
    midway through (e.g., new_context raises, goto times out, Chromium dies
    during launch) doesn't leak Playwright/Chromium subprocesses. Without
    this, a retry — manual /warm or a future restart of the auto-warm path
    — would stack a second browser on top of the orphaned first one.

    Closes in reverse construction order; each step swallows exceptions
    because we may be cleaning up an inconsistent state (e.g., context was
    set but the underlying browser already died), and we always want to
    finish by clearing the dict.
    """
    page = _prewarmed.get("page")
    if page is not None:
        try:
            await page.close()
        except Exception:
            pass
    context = _prewarmed.get("context")
    if context is not None:
        try:
            await context.close()
        except Exception:
            pass
    browser = _prewarmed.get("browser")
    if browser is not None:
        try:
            await browser.close()
        except Exception:
            pass
    playwright = _prewarmed.get("playwright")
    if playwright is not None:
        try:
            await playwright.stop()
        except Exception:
            pass
    for k in _prewarmed:
        _prewarmed[k] = None


async def prewarm_browser() -> None:
    """Launch Chromium, create a context + tab at about:blank. Idempotent.

    On failure at any step, the partially constructed Playwright/Chromium
    handles are torn down before re-raising so retries don't stack
    orphaned browser processes inside the same container.
    """
    if _prewarmed["page"] is not None:
        app_logger.info("prewarm: already warm, skipping re-init")
        return

    user_agent = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    app_logger.info("prewarm: starting Playwright + Chromium launch")
    try:
        _prewarmed["playwright"] = await async_playwright().start()
        _prewarmed["browser"] = await _prewarmed["playwright"].chromium.launch(
            headless=False,
            args=settings.BROWSER_ARGS,
        )
        _prewarmed["context"] = await _prewarmed["browser"].new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=user_agent,
            permissions=["microphone", "camera"],
        )
        _prewarmed["page"] = await _prewarmed["context"].new_page()
        # Park the tab at about:blank. Deliberately NOT pre-loading
        # meet.google.com — that would save another ~3 s but invites Meet
        # cookie / fingerprint state to leak into the warm browser.
        await _prewarmed["page"].goto("about:blank", wait_until="load", timeout=10000)
    except Exception as e:
        app_logger.warning(f"prewarm: failed mid-launch, cleaning up partial state: {e}")
        await _cleanup_partial()
        raise
    app_logger.info("prewarm: Chromium ready at about:blank")


def consume_prewarm() -> Optional[dict]:
    """Hand off the warm handles to a MeetingBot. Returns None when not warm.

    Clears module state on consume so a second caller (or a stale retry) gets
    None instead of a stale Browser reference.
    """
    if _prewarmed["page"] is None:
        return None
    handles = dict(_prewarmed)
    for k in _prewarmed:
        _prewarmed[k] = None
    return handles


def is_warm() -> bool:
    """Cheap check for the /ready endpoint — true after prewarm_browser
    has completed AND consume_prewarm has not yet been called."""
    return _prewarmed["page"] is not None
