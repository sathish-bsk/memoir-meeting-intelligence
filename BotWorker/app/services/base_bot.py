"""
Abstract base class for meeting platform bots.
Shared concerns: browser launch, PulseAudio detection, screenshots, cleanup.
Platform-specific logic (join, end detection, leave) is implemented by subclasses.
"""
import asyncio
import random
import shutil
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from app.core.config import settings
from app.core.logging import get_job_logger


class BrowserError(Exception):
    """General browser automation error."""
    pass


class MeetingDeniedError(BrowserError):
    """Bot was denied entry to the meeting."""
    pass


class MeetingEndedError(BrowserError):
    """Meeting has ended or bot was removed."""
    pass


class BotKickedError(BrowserError):
    """Bot was kicked/removed from the meeting."""
    pass


class MeetingBot(ABC):
    """Abstract base for platform-specific meeting bots."""

    def __init__(self, job_id: str, bot_name: str):
        self.job_id = job_id
        self.bot_name = bot_name
        self.logger = get_job_logger(job_id)
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._event_sender = None  # set by lifecycle for in-join event sending
        # Browser-mode persistent context user-data-dir; populated only when
        # AUDIO_CAPTURE_METHOD=browser. Cleaned up in close().
        self._user_data_dir: Optional[str] = None

    def set_event_sender(self, event_sender):
        """Allow lifecycle to inject event sender for mid-join events."""
        self._event_sender = event_sender

    # ── Shared: browser lifecycle ─────────────────────────────────

    async def launch(self):
        """Launch Playwright Chromium with required flags.

        - system mode: regular `launch()` + `new_context()`. Existing path.
        - browser mode: `launch_persistent_context()` is required for the
          tabCapture extension to load. The extension lives at /app/extension
          and is wired in via flags from settings.BROWSER_ARGS.
        - Pool pre-warmed bots (system mode only): when prewarm_browser ran
          at container startup, consume_prewarm returns the warm Playwright
          handles and we skip the cold launch entirely. Browser mode is
          excluded because it needs a per-job user_data_dir via
          launch_persistent_context, which is incompatible with reuse.
        """
        self.logger.info("Launching browser...")

        # Pool pre-warmed handoff path (system mode only). When set, skip
        # async_playwright().start() + chromium.launch() + new_context() —
        # those were done at warm time, saving ~12-15 s. consume_prewarm
        # returns None for cold bots and for browser-mode bots.
        if settings.AUDIO_CAPTURE_METHOD != "browser":
            try:
                from app.services.prewarm import consume_prewarm
                handoff = consume_prewarm()
            except Exception as e:
                self.logger.warning(f"prewarm consume failed, falling back to cold launch: {e}")
                handoff = None
            if handoff is not None:
                self._playwright = handoff["playwright"]
                self._browser = handoff["browser"]
                self._context = handoff["context"]
                self._page = handoff["page"]
                self.logger.info("Browser re-used from prewarm pool — skipping cold launch")
                try:
                    from app.services.phase_log import log_phase
                    log_phase("browser_launched", mode="prewarmed")
                except Exception:
                    pass
                return

        self._playwright = await async_playwright().start()

        user_agent = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )

        if settings.AUDIO_CAPTURE_METHOD == "browser":
            self._user_data_dir = tempfile.mkdtemp(prefix="memoir-chrome-")
            self.logger.info(
                f"browser mode: persistent context at {self._user_data_dir}"
            )
            self._context = await self._playwright.chromium.launch_persistent_context(
                self._user_data_dir,
                headless=False,
                args=settings.BROWSER_ARGS,
                viewport={"width": 1920, "height": 1080},
                user_agent=user_agent,
                permissions=["microphone", "camera"],
                timeout=60000,
            )
            self._browser = self._context.browser
            self._page = (
                self._context.pages[0]
                if self._context.pages
                else await self._context.new_page()
            )
        else:
            self._browser = await self._playwright.chromium.launch(
                headless=False,
                args=settings.BROWSER_ARGS,
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=user_agent,
                permissions=["microphone", "camera"],
            )
            self._page = await self._context.new_page()

        self.logger.info("Browser launched successfully")
        # Tier 0 phase log
        try:
            from app.services.phase_log import log_phase
            log_phase("browser_launched", mode=("browser" if settings.AUDIO_CAPTURE_METHOD == "browser" else "system"))
        except Exception:
            pass  # phase logging must never break the bot

        # Phase 3 hook tracking (notetaker-email plan §5) — track any
        # authenticated profile we materialize so we can clean it up
        # in close(). None when running in guest mode.
        self._auth_profile = None

    async def maybe_use_authenticated_context(self, meeting_url: str) -> bool:
        """Phase 3 entry point — opt-in.

        Subclasses (meet_bot, teams_bot) MAY call this before navigating
        to the meeting URL. If `MEMOIR_BOT_AUTH_MODE=authenticated` and
        the provider is eligible (Google Meet / Microsoft Teams) and a
        bot profile is configured, the current Chromium context is
        replaced with one using the persistent authenticated profile.

        Returns True if the context was upgraded to authenticated mode;
        False otherwise (caller continues with the existing guest
        context — behavior unchanged from today).

        Any failure (no profile configured, secrets fetch failed,
        profile extraction failed, stealth init failed) → returns False
        without disturbing the existing browser. The caller can proceed
        and `_wait_for_admission` will simply observe the lobby like
        any other guest join.
        """
        try:
            from app.services.auth_session import (
                launch_authenticated_context,
                should_use_authenticated_session,
            )
        except Exception as e:
            self.logger.warning("auth_session import failed; staying in guest mode: %s", e)
            return False

        if not should_use_authenticated_session(meeting_url):
            return False
        if self._playwright is None:
            self.logger.warning("maybe_use_authenticated_context called before launch()")
            return False

        user_agent = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )

        new_context, profile = await launch_authenticated_context(
            self._playwright,
            meeting_url=meeting_url,
            user_agent=user_agent,
            browser_args=settings.BROWSER_ARGS,
        )
        if new_context is None:
            return False

        # Teardown the guest context cleanly before swapping.
        try:
            if self._context is not None:
                await self._context.close()
        except Exception as e:
            self.logger.warning("Guest context close before auth swap failed: %s", e)
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception as e:
            self.logger.warning("Guest browser close before auth swap failed: %s", e)

        self._context = new_context
        self._browser = new_context.browser
        self._page = (
            new_context.pages[0] if new_context.pages else await new_context.new_page()
        )
        self._auth_profile = profile
        self.logger.info(
            "Switched to authenticated context (provider=%s, cookies=%d, key=%s)",
            profile.provider, profile.cookie_count, profile.source_key,
        )
        return True

    async def _is_in_meeting(self) -> bool:
        """Return True when the bot is confirmed in the meeting (toolbar visible).

        Base implementation raises NotImplementedError. Platforms that have
        an inventoried in-meeting toolbar selector list override this with
        a real check (Meet: GoogleMeetBot in browser.py). Platforms that
        haven't (Teams, Zoom) inherit the NotImplementedError, which the
        Tier 1.2 lifecycle code catches and falls back to the legacy 3 s
        defensive sleep — bit-for-bit identical to pre-Tier-1.2 behavior.

        See plan/bot_join_latency_optimization.md §13 for Teams/Zoom
        follow-up scoping.
        """
        raise NotImplementedError(
            "_is_in_meeting() not implemented for this platform; "
            "lifecycle will fall back to legacy sleep(3)"
        )

    async def close(self):
        """Close browser and clean up Playwright resources."""
        self.logger.info("Closing browser...")
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            self.logger.warning(f"Error closing browser: {e}")
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None
            # browser mode: drop the persistent user-data-dir we created.
            # Without this we leak GBs across container restarts.
            if self._user_data_dir:
                shutil.rmtree(self._user_data_dir, ignore_errors=True)
                self._user_data_dir = None
            # Phase 3: drop the authenticated profile's extracted dir.
            if getattr(self, "_auth_profile", None):
                try:
                    from app.services.auth_session import cleanup_profile
                    await cleanup_profile(self._auth_profile)
                except Exception as e:
                    self.logger.warning("Auth profile cleanup failed: %s", e)
                self._auth_profile = None

    # ── Abstract: platform-specific ───────────────────────────────

    @abstractmethod
    async def join_meeting(self, meeting_url: str) -> bool:
        """
        Navigate to the meeting URL and join.
        Returns True if successfully joined.
        Raises MeetingDeniedError, BrowserError on failure.
        """

    @abstractmethod
    async def detect_meeting_end(self) -> Optional[str]:
        """
        Check if the meeting has ended.
        Returns a reason string if ended, None otherwise.
        """

    @abstractmethod
    async def leave_meeting(self):
        """Click the leave/hangup button to exit the meeting."""

    # ── Virtual: override if needed ───────────────────────────────

    async def dismiss_in_meeting_popups(self):
        """Dismiss any in-meeting popups/notifications. Override per platform."""
        pass

    async def get_participant_count(self) -> Optional[int]:
        """Extract participant count. Override per platform. Default None."""
        return None

    async def get_participant_names(self) -> list[str]:
        """Extract participant display names from the meeting UI. Override per platform."""
        return []

    async def get_active_speaker(self) -> Optional[str]:
        """Return display name of the currently active speaker, or None."""
        return await self._get_caption_speaker()

    async def debug_active_speaker(self) -> Optional[str]:
        """Return diagnostic info about video tiles for debugging active speaker detection."""
        return None

    async def enable_captions(self):
        """Enable closed captions and inject MutationObserver for speaker detection. Override per platform."""
        pass

    async def open_participant_panel(self):
        """Open the participant/people panel and inject observer for name scraping. Override per platform."""
        pass

    # ── Shared helpers ────────────────────────────────────────────

    async def _get_caption_speaker(self) -> Optional[str]:
        """Read the active speaker name from the injected caption MutationObserver.

        Returns the speaker name if the caption was updated within the last 5 seconds,
        otherwise returns None. Works for all platforms after enable_captions() injects
        the observer that populates window.__memoirCaption.
        """
        if not self._page:
            return None
        try:
            result = await self._page.evaluate("""() => {
                const c = window.__memoirCaption;
                if (!c || !c.name) return null;
                if (Date.now() - c.ts > 5000) return null;
                return c.name;
            }""")
            return result
        except Exception:
            return None

    async def _is_audio_streaming(self) -> bool:
        """Check if browser is outputting audio via PulseAudio.

        When the bot is admitted to a meeting, the WebRTC audio stream
        starts and creates a PulseAudio sink-input. In the waiting room
        there are no active sink-inputs. This is UI-agnostic and works
        across all meeting platforms.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "pactl", "list", "sink-inputs", "short",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            return len(stdout.strip()) > 0
        except Exception:
            return False

    async def _wait_for_audio_admission(self, timeout: int) -> bool:
        """Shared PulseAudio-based admission detection.

        Waits for audio to stream for 2+ consecutive seconds, indicating
        the bot has been admitted and WebRTC audio is flowing.
        Returns True if audio detected within timeout, False otherwise.
        """
        audio_confirmed_at = None
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if await self._is_audio_streaming():
                if audio_confirmed_at is None:
                    audio_confirmed_at = asyncio.get_event_loop().time()
                    self.logger.info("Audio stream detected, confirming...")
                elif asyncio.get_event_loop().time() - audio_confirmed_at >= 2:
                    self.logger.info("Admission detected via PulseAudio sink-input")
                    return True
            else:
                audio_confirmed_at = None
            await asyncio.sleep(1)
        return False

    async def _random_delay(self):
        """Human-like delay between actions."""
        delay = random.uniform(0.1, 0.5)
        await asyncio.sleep(delay)

    async def _take_screenshot(self, name: str):
        """Save a debug screenshot to the job's storage directory."""
        if not self._page:
            return
        try:
            # Save to job storage dir (mounted to host) so screenshots persist
            path = Path(settings.STORAGE_DIR) / self.job_id / "screenshots"
            path.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = path / f"{timestamp}_{name}.png"
            await self._page.screenshot(path=str(filepath))
            self.logger.info(f"Screenshot saved: {filepath}")
        except Exception as e:
            self.logger.warning(f"Failed to save screenshot: {e}")

    async def _try_click(self, selectors: list[str], description: str = "") -> bool:
        """Try clicking the first matching selector from a list. Returns True if clicked."""
        if not self._page:
            return False
        for selector in selectors:
            try:
                btn = await self._page.query_selector(selector)
                if btn:
                    await btn.click()
                    self.logger.info(f"Clicked {description or selector}")
                    return True
            except Exception:
                continue
        return False

    async def _find_element(self, selectors: list[str]):
        """Return the first matching element from a list of selectors, or None."""
        if not self._page:
            return None
        for selector in selectors:
            try:
                el = await self._page.query_selector(selector)
                if el:
                    return el
            except Exception:
                continue
        return None
