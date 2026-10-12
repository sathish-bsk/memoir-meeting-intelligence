"""
Playwright-based Google Meet browser automation.
Handles joining meetings, detecting meeting end, and leaving.
Inherits shared browser/audio logic from MeetingBot base class.
"""
import asyncio
import re
import time
from typing import Optional

from app.core.config import settings
from app.models.enums import EventType
from app.services.base_bot import MeetingBot, MeetingDeniedError, BrowserError
from app.selectors.google_meet import PreJoinSelectors, InMeetingSelectors, ErrorSelectors

# Re-export exceptions so existing imports don't break
from app.services.base_bot import (  # noqa: F401
    BrowserError,
    MeetingDeniedError,
    MeetingEndedError,
    BotKickedError,
)


async def _count_sink_inputs_on_bot_sink() -> int:
    """Return how many sink-inputs are currently attached to bot_sink.

    Tier 1.8.7 — used as a parallel admission signal alongside the DOM
    toolbar selectors. During WR, the bot's Chromium renderer plays back
    its own self-view fake media on bot_sink, so the count is non-zero
    even before admission. The delta between WR-entry baseline and
    post-admit count is what indicates Meet's WebRTC peer connecting.

    Returns 0 on any failure (pactl missing, parse error, etc.) so the
    caller treats it as "no signal" rather than blowing up the join.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "pactl", "list", "sinks", "short",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        sink_index: Optional[str] = None
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1] == settings.PULSE_SINK_NAME:
                sink_index = parts[0]
                break
        if sink_index is None:
            return 0

        proc = await asyncio.create_subprocess_exec(
            "pactl", "list", "sink-inputs", "short",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        count = 0
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            parts = line.split("\t")
            # `pactl list sink-inputs short` columns: index, sink_index,
            # client_index, sample_spec, volume, ...
            if len(parts) >= 2 and parts[1] == sink_index:
                count += 1
        return count
    except Exception:
        return 0


class GoogleMeetBot(MeetingBot):
    """Automates joining a Google Meet meeting via Playwright."""

    async def _is_in_meeting(self) -> bool:
        """Return True iff any in-meeting toolbar selector is present.

        Used by the lifecycle's Tier 1.2 adaptive toolbar wait — replaces
        the old hardcoded sleep(3) with poll-until-toolbar-visible (up to
        1 s) + 1 s fallback. Selectors match the in-meeting-only buttons
        that don't exist in the pre-join or waiting-room UI.
        """
        if not self._page:
            return False
        selectors = [
            InMeetingSelectors.PEOPLE_BUTTON,
            InMeetingSelectors.PEOPLE_BUTTON_ALT2,
            InMeetingSelectors.CAPTION_BUTTON,
            InMeetingSelectors.ACTIVITIES_BUTTON,
        ]
        for sel in selectors:
            try:
                el = await self._page.query_selector(sel)
                if el:
                    return True
            except Exception:
                continue
        return False

    async def join_meeting(self, meeting_url: str) -> bool:
        """
        Navigate to the meeting URL and join.
        Returns True if successfully joined.
        Raises MeetingDeniedError, BrowserError on failure.
        """
        if not self._page:
            raise BrowserError("Browser not launched")

        # Phase 3 (notetaker-email plan §5) — if MEMOIR_BOT_AUTH_MODE=authenticated
        # and a Google profile ARN is configured, swap the guest Chromium for one
        # signed in as hello@meetmemoir.com BEFORE navigating to the meeting URL.
        # Returns False on any failure (no profile, secrets fetch failed, stealth
        # init failed, etc.) and the goto below uses the existing guest context.
        # Bit-identical to pre-Phase-3 behavior when auth mode is off.
        try:
            upgraded = await self.maybe_use_authenticated_context(meeting_url)
            if upgraded:
                self.logger.info("Meet join using authenticated context")
        except Exception as e:
            self.logger.warning(f"Auth context upgrade failed; continuing as guest: {e}")

        self.logger.info(f"Navigating to {meeting_url}")
        # Tier 1.1: wait_until="load" instead of "networkidle". Meet keeps
        # polling telemetry endpoints indefinitely, so networkidle can take
        # the full 30 s timeout. load = DOMContentLoaded ≈ 2-3 s on Meet.
        # The pre-join form elements we interact with are present at load.
        await self._page.goto(meeting_url, wait_until="load", timeout=30000)
        try:
            from app.services.phase_log import log_phase
            log_phase("navigation_complete", platform="meet")
        except Exception:
            pass
        await self._random_delay()
        await self._take_screenshot("after_navigate")

        # Wait for pre-join screen to fully load
        await self._wait_for_prejoin_screen()
        await self._take_screenshot("prejoin_loaded")

        # Dismiss cookie banner if present
        await self._dismiss_popups()

        # Disable microphone and camera
        await self._disable_media()

        # Enter bot name
        await self._enter_name()

        # Click join button
        await self._click_join()
        await self._take_screenshot("after_join_click")

        # Wait for admission
        admitted = await self._wait_for_admission()
        if admitted:
            self.logger.info("Successfully joined meeting")
            # Tier 1.8.1: _mute_in_meeting + _take_screenshot("in_meeting") used
            # to run here, blocking the lifecycle from starting the recorder.
            # They've been moved to lifecycle.run_meeting_lifecycle as background
            # tasks fired AFTER recorder.start(), saving ~2-3 s per meeting.
            # Pre-join _disable_media (browser.py:83) already muted the mic; the
            # in-meeting call is a safety net, not a prerequisite.
        return admitted

    async def detect_meeting_end(self) -> Optional[str]:
        """
        Check if the meeting has ended.
        Returns a reason string if ended, None otherwise.
        Uses a single fast JS evaluate for all text-based checks.
        """
        if not self._page:
            return "browser_closed"

        try:
            # Single JS call checks all text conditions at once (fast)
            result = await self._page.evaluate("""() => {
                const body = (document.body.innerText || '').toLowerCase();
                // Meeting ended / left
                if (body.includes('you left the meeting') ||
                    body.includes('the meeting has ended') ||
                    body.includes('meeting ended') ||
                    body.includes('call ended') ||
                    body.includes('returned to home screen'))
                    return 'meeting_ended';
                // Removed by host
                if (body.includes('you were removed') ||
                    body.includes('been removed from'))
                    return 'bot_kicked';
                // Alone in meeting
                if (body.includes("you're the only one") ||
                    body.includes("no one else is here") ||
                    body.includes("waiting for others to join"))
                    return 'everyone_left';
                // Safety net: still on waiting room page (false admission)
                if (body.includes('please wait until a meeting host') ||
                    body.includes('waiting for someone to let you in') ||
                    body.includes('meeting host brings you') ||
                    body.includes('waiting to be let in'))
                    return 'stuck_in_waiting_room';
                return null;
            }""")
            if result:
                return result

            # Quick toolbar check — move mouse to keep toolbar visible
            try:
                await self._page.mouse.move(960, 540)
            except Exception:
                pass

            # Check if leave call button exists
            leave_btn = await self._find_element([
                InMeetingSelectors.LEAVE_CALL_BUTTON,
                InMeetingSelectors.LEAVE_CALL_ALT,
            ])
            if not leave_btn:
                # Brief pause and retry once
                await asyncio.sleep(1)
                leave_btn = await self._find_element([
                    InMeetingSelectors.LEAVE_CALL_BUTTON,
                    InMeetingSelectors.LEAVE_CALL_ALT,
                ])
                if not leave_btn:
                    # Verify we're truly disconnected
                    try:
                        in_meeting = await self._page.evaluate("""() => {
                            const buttons = document.querySelectorAll('button[aria-label]');
                            for (const btn of buttons) {
                                const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                                if (label.includes('microphone') || label.includes('camera') ||
                                    label.includes('caption') || label.includes('present'))
                                    return true;
                            }
                            return false;
                        }""")
                        if in_meeting:
                            return None
                    except Exception:
                        pass
                    return "disconnected"

            return None

        except Exception as e:
            self.logger.warning(f"Error detecting meeting end: {e}")
            return "detection_error"

    async def leave_meeting(self):
        """Leave the Google Meet meeting by closing the page.

        Instrumented 2026-05-14 — production-observed page.close() taking
        3.2 s. Logs the wall time inside the call so we know whether it's
        Playwright's RPC cleanup, beforeunload handlers in Meet's JS, or
        WebRTC peer teardown.
        """
        if not self._page:
            return

        _t_start = time.monotonic()
        self.logger.info("[LEAVE_TIMING] starting page.close()")
        try:
            await self._page.close()
            _t_end = time.monotonic()
            self.logger.info(
                f"[LEAVE_TIMING] page.close() returned in "
                f"{(_t_end - _t_start) * 1000:.0f}ms"
            )
        except Exception as e:
            _t_end = time.monotonic()
            self.logger.warning(
                f"[LEAVE_TIMING] page.close() FAILED in "
                f"{(_t_end - _t_start) * 1000:.0f}ms: {e}"
            )

    async def dismiss_in_meeting_popups(self):
        """Dismiss any in-meeting popups/notifications."""
        if not self._page:
            return

        try:
            for selector in [InMeetingSelectors.CLOSE_POPUP, InMeetingSelectors.DISMISS_NOTIFICATION]:
                btn = await self._page.query_selector(selector)
                if btn:
                    await btn.click()
                    await self._random_delay()
        except Exception:
            pass

    async def get_participant_count(self) -> Optional[int]:
        """Extract participant count from the 'Show everyone' button aria-label."""
        if not self._page:
            return None

        # Move mouse to trigger toolbar visibility
        try:
            await self._page.mouse.move(960, 540)
        except Exception:
            pass
        await asyncio.sleep(0.5)

        for selector in [
            InMeetingSelectors.PEOPLE_BUTTON,
            InMeetingSelectors.PEOPLE_BUTTON_ALT,
            InMeetingSelectors.PEOPLE_BUTTON_ALT2,
        ]:
            try:
                btn = await self._page.query_selector(selector)
                if btn:
                    label = await btn.get_attribute("aria-label") or ""
                    match = re.search(r'\((\d+)\)', label)
                    if match:
                        return int(match.group(1))
            except Exception:
                continue

        # Fallback: count participant video tiles via JS
        try:
            count = await self._page.evaluate(
                "document.querySelectorAll('[data-participant-id]').length"
            )
            if count and count > 0:
                return count
        except Exception:
            pass

        # Fallback: check for "alone in meeting" text
        try:
            alone = await self._page.evaluate("""() => {
                const body = document.body.innerText || '';
                if (body.includes("You're the only one here") ||
                    body.includes("No one else is here") ||
                    body.includes("you're the only one") ||
                    body.includes("Waiting for others to join")) {
                    return true;
                }
                return false;
            }""")
            if alone:
                return 1
        except Exception:
            pass

        return None

    async def get_participant_names(self) -> list[str]:
        """Read participant names from the injected panel observer, with fallback."""
        if not self._page:
            return []
        try:
            names = await self._page.evaluate("""() => {
                // Primary: read from panel observer
                if (window.__memoirParticipants && window.__memoirParticipants.length > 0) {
                    return [...window.__memoirParticipants];
                }
                // Fallback: scrape directly from video tiles + panel
                const names = new Set();
                document.querySelectorAll('[data-participant-id]').forEach(el => {
                    const nameEl = el.querySelector('[data-self-name]') || el.querySelector('[data-tooltip]');
                    if (nameEl) {
                        const n = nameEl.getAttribute('data-self-name') || nameEl.getAttribute('data-tooltip') || nameEl.innerText;
                        if (n && n.trim()) names.add(n.trim());
                    }
                });
                document.querySelectorAll('[data-participant-id]').forEach(el => {
                    const label = el.getAttribute('aria-label') || '';
                    if (label && !label.match(/^\\d/) && label.length < 100) {
                        const cleaned = label.replace(/\\s*\\(.*\\)$/, '').trim();
                        if (cleaned) names.add(cleaned);
                    }
                });
                return [...names];
            }""")
            return [n for n in (names or []) if n]
        except Exception:
            return []

    async def get_active_speaker(self) -> Optional[str]:
        """Detect the active speaker via caption scraping."""
        return await self._get_caption_speaker()

    async def debug_active_speaker(self) -> Optional[str]:
        """Return diagnostic info about caption observer state."""
        if not self._page:
            return None
        try:
            return await self._page.evaluate("""() => {
                const info = {};
                info.caption = window.__memoirCaption || null;
                info.captionAge = info.caption ? (Date.now() - info.caption.ts) : null;
                info.participants = window.__memoirParticipants || [];
                info.participantCount = info.participants.length;
                // Check if caption elements exist in DOM
                info.captionBadges = document.querySelectorAll('.NWpY1d').length;
                info.ariaLiveRegions = document.querySelectorAll('[aria-live]').length;
                return JSON.stringify(info);
            }""")
        except Exception as e:
            return f"debug error: {e}"

    async def enable_captions(self):
        """Enable Google Meet closed captions and inject MutationObserver for speaker detection."""
        if not self._page:
            return

        self.logger.info("Enabling Google Meet captions...")

        # Check if captions are already active (only .NWpY1d is reliable —
        # [aria-live] exists in Meet DOM by default even without captions)
        already_active = await self._page.evaluate(
            "document.querySelectorAll('.NWpY1d').length > 0"
        )
        if already_active:
            self.logger.info("Captions already active in DOM (.NWpY1d found)")
        else:
            # Move mouse to trigger toolbar visibility
            try:
                await self._page.mouse.move(960, 540)
                await asyncio.sleep(1.5)
            except Exception:
                pass

            # Try clicking the caption button
            clicked = False
            try:
                btn = await self._page.query_selector('button[aria-label*="captions" i]')
                if btn:
                    label = (await btn.get_attribute("aria-label") or "").lower()
                    if "turn off" in label or "disable" in label:
                        self.logger.info("Captions already enabled (button says turn off)")
                        clicked = True  # already on
                    else:
                        # "Turn on captions" or any other label — click it
                        await btn.click()
                        clicked = True
                        self.logger.info(f"Clicked caption button (label: '{label}')")
                else:
                    self.logger.debug("Caption button not found via aria-label")
            except Exception as e:
                self.logger.debug(f"Caption button click failed: {e}")

            # Fallback: keyboard shortcut 'c'
            if not clicked:
                try:
                    await self._page.keyboard.press("c")
                    self.logger.info("Sent 'c' keyboard shortcut to toggle captions")
                except Exception as e:
                    self.logger.debug(f"Caption keyboard shortcut failed: {e}")

            # Wait for captions to appear in DOM
            for _ in range(6):
                active = await self._page.evaluate(
                    "document.querySelectorAll('.NWpY1d').length > 0"
                )
                if active:
                    self.logger.info("Captions verified active (.NWpY1d appeared)")
                    break
                await asyncio.sleep(0.5)
            else:
                self.logger.warning("Caption badge .NWpY1d not yet in DOM — captions may need someone to speak")

        # Inject MutationObserver for speaker detection
        await self._page.evaluate("""() => {
            if (window.__memoirCaptionObserver) return; // Already injected
            window.__memoirCaption = { name: null, ts: 0 };
            let lastCheck = 0;

            const observer = new MutationObserver(() => {
                const now = Date.now();
                if (now - lastCheck < 200) return;
                lastCheck = now;
                const badges = document.querySelectorAll('.NWpY1d');
                if (badges.length > 0) {
                    const name = badges[badges.length - 1].textContent.trim();
                    if (name) window.__memoirCaption = { name, ts: now };
                }
            });
            observer.observe(document.body, { childList: true, characterData: true, subtree: true });
            window.__memoirCaptionObserver = observer;
        }""")
        self.logger.info("Injected Google Meet caption MutationObserver")

    async def open_participant_panel(self):
        """Open the Google Meet People panel and inject observer for name scraping."""
        if not self._page:
            return

        self.logger.info("Opening Google Meet People panel...")

        from app.selectors.google_meet import InMeetingSelectors
        people_selectors = [
            InMeetingSelectors.PEOPLE_BUTTON,
            InMeetingSelectors.PEOPLE_BUTTON_ALT,
            InMeetingSelectors.PEOPLE_BUTTON_ALT2,
        ]

        # Try multiple mouse positions + retries since toolbar auto-hides
        for attempt, (x, y) in enumerate([(960, 1000), (960, 540), (960, 900)]):
            try:
                await self._page.mouse.move(x, y)
                await asyncio.sleep(1.5)
            except Exception:
                pass

            clicked = await self._try_click(people_selectors, "People panel button")
            if clicked:
                await asyncio.sleep(2)  # Wait for panel to render
                return

        self.logger.warning("People panel button not found after 3 attempts")

        # Inject MutationObserver for participant names
        await self._page.evaluate("""() => {
            if (window.__memoirParticipantObserver) return; // Already injected
            window.__memoirParticipants = [];

            function scrapeParticipants() {
                const names = new Set();
                // data-participant-id elements with name attributes
                document.querySelectorAll('[data-participant-id]').forEach(el => {
                    const nameEl = el.querySelector('[data-self-name]') || el.querySelector('[data-tooltip]');
                    if (nameEl) {
                        const n = nameEl.getAttribute('data-self-name') || nameEl.getAttribute('data-tooltip') || nameEl.innerText;
                        if (n && n.trim()) names.add(n.trim());
                    }
                    // Fallback: aria-label
                    const label = el.getAttribute('aria-label') || '';
                    if (label && label.length < 100) {
                        const cleaned = label.replace(/\\s*\\(.*\\)$/, '').trim();
                        if (cleaned && !cleaned.match(/^\\d/)) names.add(cleaned);
                    }
                });
                // notranslate spans (name labels in sidebar/tiles)
                document.querySelectorAll('span.notranslate').forEach(el => {
                    const n = el.textContent?.trim();
                    if (n && n.length > 0 && n.length < 100) names.add(n);
                });
                window.__memoirParticipants = [...names];
            }

            // Initial scrape
            scrapeParticipants();

            const observer = new MutationObserver(() => {
                scrapeParticipants();
            });
            observer.observe(document.body, { childList: true, subtree: true });
            window.__memoirParticipantObserver = observer;
        }""")
        self.logger.info("Injected Google Meet participant panel observer")

    # --- Private helpers ---

    async def _wait_for_prejoin_screen(self):
        """Wait for the pre-join form to appear."""
        self.logger.info("Waiting for pre-join screen to load...")
        prejoin_selectors = [
            PreJoinSelectors.NAME_INPUT,
            PreJoinSelectors.NAME_INPUT_ALT,
            PreJoinSelectors.JOIN_NOW_BUTTON,
            PreJoinSelectors.ASK_TO_JOIN_BUTTON,
            PreJoinSelectors.JOIN_BUTTON_ALT,
        ]
        error_selectors = [
            ErrorSelectors.DENIED_TEXT,
            ErrorSelectors.NOT_ALLOWED_TEXT,
            ErrorSelectors.NOT_FOUND_TEXT,
        ]
        deadline = asyncio.get_event_loop().time() + 30
        while asyncio.get_event_loop().time() < deadline:
            for sel in error_selectors:
                el = await self._page.query_selector(sel)
                if el:
                    await self._take_screenshot("blocked_by_meet")
                    raise MeetingDeniedError(f"Google Meet blocked join: {sel}")
            for sel in prejoin_selectors:
                el = await self._page.query_selector(sel)
                if el:
                    self.logger.info("Pre-join screen loaded")
                    return
            await asyncio.sleep(1)
        self.logger.warning("Pre-join screen wait timed out")
        await self._take_screenshot("prejoin_wait_timeout")

    async def _dismiss_popups(self):
        """Dismiss cookie banners and other pre-join dialogs."""
        for selector in [
            PreJoinSelectors.COOKIE_ACCEPT_BUTTON,
            PreJoinSelectors.COOKIE_REJECT_BUTTON,
            PreJoinSelectors.GOT_IT_BUTTON,
            PreJoinSelectors.DISMISS_BUTTON,
        ]:
            try:
                btn = await self._page.query_selector(selector)
                if btn:
                    await btn.click()
                    self.logger.info(f"Dismissed popup: {selector}")
                    await self._random_delay()
            except Exception:
                pass

    async def _disable_media(self):
        """Turn off microphone and camera on the pre-join screen.

        For camera: stops video tracks at the WebRTC level (most reliable),
        then tries UI toggle as confirmation.
        For mic: uses keyboard shortcut Ctrl+D as primary (avoids hitting
        device dropdown instead of toggle), with CSS fallback.
        """
        await asyncio.sleep(2)

        # --- 1. Stop camera at WebRTC level (most reliable) ---
        try:
            stopped = await self._page.evaluate("""() => {
                let stopped = 0;
                // Stop video tracks on all <video> elements
                document.querySelectorAll('video').forEach(v => {
                    if (v.srcObject) {
                        v.srcObject.getVideoTracks().forEach(t => {
                            t.enabled = false;
                            t.stop();
                            stopped++;
                        });
                    }
                });
                return stopped;
            }""")
            if stopped and stopped > 0:
                self.logger.info(f"Stopped {stopped} video track(s) via WebRTC API")
            else:
                self.logger.info("No active video tracks found to stop")
        except Exception as e:
            self.logger.warning(f"WebRTC video stop failed: {e}")

        # --- 2. Toggle camera off via keyboard shortcut Ctrl+E ---
        try:
            await self._page.keyboard.press("Control+e")
            self.logger.info("Sent Ctrl+E to toggle camera off")
            await asyncio.sleep(0.5)
        except Exception as e:
            self.logger.debug(f"Camera keyboard shortcut failed: {e}")

        # --- 3. Toggle mic off via keyboard shortcut Ctrl+D ---
        try:
            await self._page.keyboard.press("Control+d")
            self.logger.info("Sent Ctrl+D to toggle mic off")
            await asyncio.sleep(0.5)
        except Exception as e:
            self.logger.debug(f"Mic keyboard shortcut failed: {e}")

        # --- 4. Verify and fix via UI buttons as backup ---
        for device_name, selectors in [
            ("microphone", [PreJoinSelectors.MIC_TOGGLE, PreJoinSelectors.MIC_TOGGLE_ALT]),
            ("camera", [PreJoinSelectors.CAMERA_TOGGLE, PreJoinSelectors.CAMERA_TOGGLE_ALT,
                        PreJoinSelectors.CAMERA_TOGGLE_ALT2, PreJoinSelectors.CAMERA_TOGGLE_ALT3]),
        ]:
            try:
                # Use JS to find the toggle button INSIDE the video preview area,
                # excluding device dropdown buttons (which contain "Fake" in their label)
                result = await self._page.evaluate("""(deviceName) => {
                    const buttons = document.querySelectorAll(
                        'button[aria-label], [role="button"][aria-label]'
                    );
                    for (const btn of buttons) {
                        const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                        const tooltip = (btn.getAttribute('data-tooltip') || '').toLowerCase();
                        const text = label || tooltip;
                        // Skip device dropdowns (contain device names like "fake", "default")
                        if (text.includes('fake') || text.includes('default') ||
                            text.includes('system') || text.includes('device'))
                            continue;
                        if (deviceName === 'camera') {
                            if (text.includes('turn off camera') ||
                                text.includes('stop camera') ||
                                text.includes('disable camera')) {
                                btn.click();
                                return 'clicked_off';
                            }
                            if (text.includes('turn on camera') ||
                                text.includes('start camera') ||
                                text.includes('enable camera')) {
                                return 'already_off';
                            }
                        }
                        if (deviceName === 'microphone') {
                            if (text.includes('turn off microphone') ||
                                text.includes('mute') && !text.includes('unmute')) {
                                btn.click();
                                return 'clicked_off';
                            }
                            if (text.includes('turn on microphone') ||
                                text.includes('unmute')) {
                                return 'already_off';
                            }
                        }
                    }
                    return null;
                }""", device_name)
                if result == "clicked_off":
                    self.logger.info(f"Disabled {device_name} via UI button (JS)")
                elif result == "already_off":
                    self.logger.info(f"{device_name} already disabled (verified via UI)")
                else:
                    self.logger.debug(f"{device_name} toggle button not found in UI")
            except Exception as e:
                self.logger.debug(f"UI {device_name} verify failed: {e}")

        # Take screenshot to verify media state
        await asyncio.sleep(0.5)
        await self._take_screenshot("after_media_disable")

    async def _enter_name(self):
        """Enter the bot name in the name input field, with retry."""
        selectors = [PreJoinSelectors.NAME_INPUT, PreJoinSelectors.NAME_INPUT_ALT]
        deadline = asyncio.get_event_loop().time() + 5
        while asyncio.get_event_loop().time() < deadline:
            for selector in selectors:
                try:
                    input_el = await self._page.query_selector(selector)
                    if input_el:
                        await input_el.click(click_count=3)
                        await input_el.fill(self.bot_name)
                        self.logger.info(f"Entered bot name: '{self.bot_name}'")
                        await self._random_delay()
                        return
                except Exception as e:
                    self.logger.debug(f"Name input attempt failed ({selector}): {e}")
                    continue
            await asyncio.sleep(0.5)
        self.logger.warning("Name input not found after 5s — bot name may not be applied")

    async def _click_join(self):
        """Click the Join now or Ask to join button."""
        selectors = [
            PreJoinSelectors.JOIN_NOW_BUTTON,
            PreJoinSelectors.ASK_TO_JOIN_BUTTON,
            PreJoinSelectors.JOIN_BUTTON_ALT,
        ]
        deadline = asyncio.get_event_loop().time() + 10
        while asyncio.get_event_loop().time() < deadline:
            for selector in selectors:
                try:
                    btn = await self._page.query_selector(selector)
                    if btn:
                        await self._random_delay()
                        try:
                            from app.services.phase_log import log_phase
                            log_phase("pre_join_done", platform="meet")
                        except Exception:
                            pass
                        await btn.click()
                        self.logger.info(f"Clicked join button: {selector}")
                        return
                except Exception:
                    continue
            await asyncio.sleep(0.5)
        await self._take_screenshot("join_button_not_found")
        raise BrowserError("Join button not found")

    async def _mute_in_meeting(self):
        """Safety-net: mute microphone after admission in case pre-join mute didn't work."""
        if not self._page:
            return
        try:
            await self._page.mouse.move(960, 540)
            # Tier 1.8.2: replaced unconditional sleep(1) with wait_for_selector
            # bounded at 500 ms. Mic button is usually in DOM immediately after
            # admission; the old sleep paid 1 s unconditionally even when the
            # selector was already present.
            try:
                await self._page.wait_for_selector(
                    InMeetingSelectors.MIC_IN_MEETING, timeout=500
                )
            except Exception:
                # Selector didn't appear within 500 ms — fall through, the loop
                # below will try both selectors anyway.
                pass
            for selector in [
                InMeetingSelectors.MIC_IN_MEETING,
                InMeetingSelectors.MIC_IN_MEETING_ALT,
            ]:
                btn = await self._page.query_selector(selector)
                if btn:
                    label = (await btn.get_attribute("aria-label") or "").lower()
                    if "turn off" in label:
                        await btn.click()
                        self.logger.info("Muted microphone in-meeting")
                    elif "turn on" in label:
                        self.logger.info("Microphone already muted in-meeting")
                    return
            self.logger.debug("In-meeting mic button not found")
        except Exception as e:
            self.logger.warning(f"Failed to mute in-meeting: {e}")

    async def _is_in_waiting_room(self) -> bool:
        """Check if we are currently in Google Meet's waiting room."""
        if not self._page:
            return False
        try:
            for sel in [PreJoinSelectors.WAITING_ROOM_TEXT, PreJoinSelectors.WAITING_ROOM_ALT]:
                el = await self._page.query_selector(sel)
                if el:
                    return True
            in_wr = await self._page.evaluate("""() => {
                const body = (document.body.innerText || '').toLowerCase();
                return body.includes('waiting for someone to let you in') ||
                       body.includes('ask someone in the meeting to let you in') ||
                       body.includes('please wait until a meeting host') ||
                       body.includes('waiting to be let in') ||
                       body.includes('meeting host brings you');
            }""")
            return bool(in_wr)
        except Exception:
            return False

    async def _wait_for_admission(self) -> bool:
        """
        Wait to be admitted to the meeting.
        Uses three layers of detection (whichever fires first wins):
        1. CSS selectors — in-meeting-only toolbar buttons (NOT mic/camera — those exist on pre-join too)
        2. PulseAudio sink-input — browser audio stream starts (only after waiting room confirmed)
        3. JS DOM fallback — waiting room text gone + toolbar button count

        A 5s grace period at the start prevents false positives from pre-join elements
        that are still visible right after clicking Join.

        Tier 1.6 (Meet-only): before entering the main loop, do a quick 1.5 s
        probe to detect already-admitted meetings (no waiting room — host
        pre-approved or open-access). Requires 2 consecutive positive selector
        hits 200 ms apart to avoid pre-join UI false positives. On hit, skips
        the 5 s grace + 3 s post-waiting-room cooldown — saves 5-8 s.
        """
        self.logger.info(f"Waiting for admission (timeout: {settings.WAITING_ROOM_TIMEOUT_SECONDS}s)...")

        # Tier 1.6: Meet auto-admit early-exit probe
        early_admitted_selectors = [
            InMeetingSelectors.PEOPLE_BUTTON,
            InMeetingSelectors.PEOPLE_BUTTON_ALT2,
            InMeetingSelectors.CAPTION_BUTTON,
            InMeetingSelectors.ACTIVITIES_BUTTON,
        ]
        # Tier 1.8.4: widened from 1.5 s to 2.5 s. Original window missed when
        # Meet's in-meeting toolbar lazy-rendered ≥1 s after admission (DOM
        # transition animations, side-panel deferred load). Kept the 2-hit
        # consecutive requirement so the anti-false-positive guard is intact.
        EARLY_PROBE_WINDOW = 2.5  # seconds (was 1.5)
        EARLY_PROBE_INTERVAL = 0.1  # 100 ms
        EARLY_PROBE_CONSECUTIVE_HITS = 2  # require 2 in a row
        consecutive_hits = 0
        probe_start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - probe_start < EARLY_PROBE_WINDOW:
            found = False
            for sel in early_admitted_selectors:
                try:
                    el = await self._page.query_selector(sel)
                    if el:
                        found = True
                        break
                except Exception:
                    continue
            if found:
                consecutive_hits += 1
                if consecutive_hits >= EARLY_PROBE_CONSECUTIVE_HITS:
                    self.logger.info(
                        "Tier 1.6: auto-admit detected via early-probe — "
                        "skipping 5 s grace period"
                    )
                    try:
                        from app.services.phase_log import log_phase
                        log_phase("admission_detected", platform="meet",
                                  path="auto_admit_early")
                    except Exception:
                        pass
                    return True
            else:
                consecutive_hits = 0
            await asyncio.sleep(EARLY_PROBE_INTERVAL)
        # Early probe did not confirm — fall through to the existing
        # grace + waiting-room logic below.

        # NOTE: Deliberately excluded from admitted_selectors:
        # - MIC_IN_MEETING / MIC_IN_MEETING_ALT — aria-label matches pre-join mic toggle
        # - LEAVE_CALL_BUTTON / LEAVE_CALL_ALT — hangup button visible in waiting room
        # - MORE_OPTIONS_BUTTON — three-dot menu visible in waiting room toolbar
        admitted_selectors = [
            InMeetingSelectors.PEOPLE_BUTTON,
            InMeetingSelectors.PEOPLE_BUTTON_ALT2,
            InMeetingSelectors.CAPTION_BUTTON,
            InMeetingSelectors.CAPTION_BUTTON_ALT,
            InMeetingSelectors.ACTIVITIES_BUTTON,
            InMeetingSelectors.MEETING_INFO,
            InMeetingSelectors.MEETING_INFO_ALT,
        ]
        denied_selectors = [
            ErrorSelectors.DENIED_TEXT,
            ErrorSelectors.DENIED_ALT,
            ErrorSelectors.NOT_ALLOWED_TEXT,
            ErrorSelectors.DENIED_ENTRY,
            ErrorSelectors.DENIED_BY_HOST,
        ]
        deadline = asyncio.get_event_loop().time() + settings.WAITING_ROOM_TIMEOUT_SECONDS
        start_time = asyncio.get_event_loop().time()
        screenshot_taken = False
        waiting_room_seen = False
        waiting_room_event_sent = False
        # Tier 1.8.7 — sink-input baseline. Captured the moment WR is
        # first detected so the polling loop can compare against it and
        # fire admission_detected the moment Meet's WebRTC peer adds a
        # new audio stream (typically 1-3 s before the in-meeting toolbar
        # renders in the DOM, per browser.py:868 comment that "PulseAudio
        # sink-inputs (fake media)" already exist in the waiting room).
        wr_sink_input_baseline: int | None = None
        wr_entered_at = None  # timestamp when waiting room was first detected
        GRACE_PERIOD = 5  # seconds before checking admission signals

        while asyncio.get_event_loop().time() < deadline:
            elapsed = asyncio.get_event_loop().time() - start_time

            # Check for denial first (always active)
            for sel in denied_selectors:
                el = await self._page.query_selector(sel)
                if el:
                    await self._take_screenshot("denied")
                    raise MeetingDeniedError("Entry denied by host")

            # Detect and report waiting room (always active)
            if not waiting_room_seen and await self._is_in_waiting_room():
                waiting_room_seen = True
                wr_entered_at = asyncio.get_event_loop().time()
                # Tier 1.8.7 — capture sink-input baseline at WR-entry.
                # Any post-admit increase signals Meet's WebRTC peer
                # connecting (~1-3 s before DOM toolbar renders). Done
                # once here so the polling loop is just a delta check.
                wr_sink_input_baseline = await _count_sink_inputs_on_bot_sink()
                self.logger.info(
                    f"In Google Meet waiting room — waiting for host to admit "
                    f"(sink-input baseline={wr_sink_input_baseline})..."
                )
                await self._take_screenshot("in_waiting_room")
                if self._event_sender and not waiting_room_event_sent:
                    await self._event_sender.send_event(EventType.WAITING_ROOM_ENTERED)
                    waiting_room_event_sent = True

            # During grace period: only check for denial + waiting room, skip admission checks.
            # This prevents pre-join buttons from triggering false admission.
            # Tier 1.8.6.b — tightened from 1 s to 200 ms so we exit the grace
            # period the moment WR is detected or the grace window elapses,
            # rather than rounding up to the next 1 s tick.
            if elapsed < GRACE_PERIOD and not waiting_room_seen:
                await asyncio.sleep(0.2)
                continue

            # --- Post-waiting-room admission ---
            # Google Meet's waiting room shares toolbar buttons (leave, mic,
            # camera, more options), PulseAudio sink-inputs (fake media),
            # participant tiles (self-view), AND its text is undetectable
            # via innerText/textContent (likely shadow DOM or canvas).
            #
            # Strategy:
            # 1. Mandatory 10s cooldown after entering waiting room — the
            #    DOM is unstable during the first seconds and any check
            #    would race against rendering.
            # 2. After cooldown, poll for meeting-only CSS selectors
            #    (Show everyone, captions, Activities, Meeting details)
            #    that do NOT exist in the waiting room toolbar.
            if waiting_room_seen:
                time_in_wr = asyncio.get_event_loop().time() - wr_entered_at

                # WR admission cooldown. Originally 3s (Tier 1.8.6) as a
                # defensive window for Meet's DOM to stabilize. Tier 1.8.7
                # added a sink-input baseline check that fires the moment
                # Meet's WebRTC peer adds an audio stream (~1-3 s before
                # the toolbar DOM renders) — so the sink-input signal is
                # already past this cooldown and the DOM-stability concern
                # only applies to the selector check below. Dropped to 1s
                # (2026-05-14): keeps a small floor against DOM startup
                # races, but unblocks admission-detected ~2s sooner for
                # the common path where the host admits immediately. If
                # we see false admissions in WR, raise this back to 2s.
                if time_in_wr < 1:
                    await asyncio.sleep(0.1)
                    continue

                # Tier 1.8.7 — sink-input delta check, runs IN ADDITION
                # to the DOM selector check below. Whichever fires first
                # wins. Meet's WebRTC peer adds a new sink-input ~1-3 s
                # before the in-meeting toolbar renders in the DOM, so
                # this often beats the selector check. Selector check is
                # kept as the safety net for any host/Meet config where
                # the audio signal is unreliable.
                if wr_sink_input_baseline is not None:
                    try:
                        cur_count = await _count_sink_inputs_on_bot_sink()
                        if cur_count > wr_sink_input_baseline:
                            self.logger.info(
                                f"Admission detected via sink-input delta: "
                                f"{wr_sink_input_baseline} -> {cur_count}"
                            )
                            await self._take_screenshot("admission_confirmed_audio")
                            try:
                                from app.services.phase_log import log_phase
                                log_phase(
                                    "admission_detected",
                                    platform="meet",
                                    path="waiting_room_audio",
                                    sink_inputs_before=wr_sink_input_baseline,
                                    sink_inputs_after=cur_count,
                                )
                            except Exception:
                                pass
                            return True
                    except Exception as e:
                        # Audio probe failure is non-fatal — the DOM
                        # selector check below remains the canonical
                        # path. Log once then carry on.
                        self.logger.debug(f"sink-input probe failed: {e}")

                # Check meeting-only CSS selectors at 100 ms cadence.
                # These buttons (Show everyone, captions, Activities,
                # Meeting details) do NOT exist in the waiting room
                # toolbar (which only has mic, camera, more, hangup),
                # so a single positive hit is sufficient.
                for sel in admitted_selectors:
                    el = await self._page.query_selector(sel)
                    if el:
                        label = await el.get_attribute("aria-label") or sel
                        await self._take_screenshot("admission_confirmed")
                        self.logger.info(
                            f"Admission detected via meeting-only selector: {label}"
                        )
                        try:
                            from app.services.phase_log import log_phase
                            log_phase("admission_detected", platform="meet", path="waiting_room")
                        except Exception:
                            pass
                        return True

                await asyncio.sleep(0.1)
                continue

            # --- Pre-waiting-room / direct admission (normal path) ---
            # Layer 1: CSS selectors for in-meeting-only toolbar buttons
            for sel in admitted_selectors:
                el = await self._page.query_selector(sel)
                if el:
                    self.logger.info(f"Admission detected via selector: {sel}")
                    try:
                        from app.services.phase_log import log_phase
                        log_phase("admission_detected", platform="meet", path="auto_admit")
                    except Exception:
                        pass
                    return True

            # Layer 2: JS DOM fallback
            try:
                in_meeting = await self._page.evaluate("""() => {
                    const body = (document.body.innerText || '').toLowerCase();
                    const inWaitingRoom = body.includes('waiting for someone to let you in') ||
                                          body.includes('ask someone in the meeting to let you in') ||
                                          body.includes('please wait until a meeting host') ||
                                          body.includes('meeting host brings you') ||
                                          body.includes('waiting to be let in');
                    if (inWaitingRoom) return false;
                    const buttons = document.querySelectorAll('button[aria-label]');
                    let toolbarCount = 0;
                    for (const btn of buttons) {
                        const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                        if (label.includes('caption') ||
                            label.includes('present') ||
                            label.includes('chat') || label.includes('hand') ||
                            label.includes('emoji') || label.includes('activities') ||
                            label.includes('show everyone'))
                            toolbarCount++;
                    }
                    return toolbarCount >= 3;
                }""")
                if in_meeting:
                    self.logger.info("Admission detected via JS toolbar button count fallback")
                    try:
                        from app.services.phase_log import log_phase
                        log_phase("admission_detected", platform="meet",
                                  path="js_fallback")
                    except Exception:
                        pass
                    return True
            except Exception as e:
                self.logger.debug(f"JS admission check failed: {e}")

            # Diagnostic screenshot halfway through
            if not screenshot_taken and elapsed > settings.WAITING_ROOM_TIMEOUT_SECONDS / 2:
                await self._take_screenshot("admission_waiting_midpoint")
                screenshot_taken = True

            # Tier 1.8.6.b — tightened from 1 s to 100 ms. This is the
            # auto-admit fall-through path (Tier 1.6 early-probe missed,
            # falls through to the main loop, no WR detected). The 1 s
            # cadence here was contributing 10-15 s on auto-admit meetings
            # when the early-probe missed — the same root cause Tier 1.8.6
            # addressed for the WR branch.
            await asyncio.sleep(0.1)
        await self._take_screenshot("admission_timeout")
        raise BrowserError("Admission timeout — host did not admit the bot")
