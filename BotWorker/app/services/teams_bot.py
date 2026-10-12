"""
Playwright-based Microsoft Teams web client automation.
Handles joining Teams meetings via the browser, recording, and detecting meeting end.
"""
import asyncio
import re
from typing import Optional

from app.core.config import settings
from app.models.enums import EventType
from app.services.base_bot import MeetingBot, MeetingDeniedError, BrowserError
from app.selectors.teams import (
    InterstitialSelectors,
    PreJoinSelectors,
    InMeetingSelectors,
    ErrorSelectors,
)


class TeamsBot(MeetingBot):
    """Automates joining a Microsoft Teams meeting via the web client."""

    async def _is_in_meeting(self) -> bool:
        """Return True iff any in-meeting toolbar selector is present.

        Tier 1.2 (Teams variant): the lifecycle's adaptive toolbar wait
        uses this to skip the legacy hardcoded sleep(3) the moment the
        in-meeting UI is visible. Hangup button is the strongest signal
        because it doesn't exist in Teams pre-join or lobby UI. Chat /
        participants / share buttons are accepted as supporting evidence.
        MIC_IN_MEETING is deliberately excluded — its aria-label matches
        the pre-join mic toggle.
        """
        if not self._page:
            return False
        selectors = [
            InMeetingSelectors.HANGUP_BUTTON,
            InMeetingSelectors.HANGUP_BUTTON_ALT,
            InMeetingSelectors.HANGUP_BUTTON_ALT2,
            InMeetingSelectors.HANGUP_BUTTON_ALT3,
            InMeetingSelectors.CHAT_BUTTON,
            InMeetingSelectors.CHAT_BUTTON_ALT,
            InMeetingSelectors.PARTICIPANTS_BUTTON,
            InMeetingSelectors.PARTICIPANTS_BUTTON_ALT,
            InMeetingSelectors.SHARE_BUTTON,
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
        """Navigate to Teams web client and join the meeting."""
        if not self._page:
            raise BrowserError("Browser not launched")

        # Phase 3 — swap to authenticated context before goto. See browser.py
        # for full rationale; falls back to guest on any failure.
        try:
            upgraded = await self.maybe_use_authenticated_context(meeting_url)
            if upgraded:
                self.logger.info("Teams join using authenticated context")
        except Exception as e:
            self.logger.warning(f"Auth context upgrade failed; continuing as guest: {e}")

        self.logger.info(f"Navigating to Teams meeting: {meeting_url}")
        # Tier 1.1: see browser.py note. Same rationale for Teams.
        await self._page.goto(meeting_url, wait_until="load", timeout=30000)
        try:
            from app.services.phase_log import log_phase
            log_phase("navigation_complete", platform="teams")
        except Exception:
            pass
        await self._random_delay()
        await self._take_screenshot("teams_after_navigate")

        # Handle "Continue on this browser" interstitial
        await self._handle_interstitial()

        # Handle "Join as a guest" if prompted
        await self._handle_guest_join()

        # Check for errors (sign-in required, not found, etc.)
        await self._check_for_errors()

        # Wait for pre-join screen
        await self._wait_for_prejoin_screen()
        await self._take_screenshot("teams_prejoin_loaded")

        # Dismiss "Continue without audio or video" dialog if present
        await self._dismiss_media_permission_dialog()

        # Enter bot name
        await self._enter_name()

        # Disable microphone and camera
        await self._disable_media()

        # Click join button
        await self._click_join()
        await self._take_screenshot("teams_after_join_click")

        # Wait for admission (lobby → meeting)
        admitted = await self._wait_for_admission()
        if admitted:
            self.logger.info("Successfully joined Teams meeting")
            # Ensure mic is muted in-meeting (pre-join mute may not stick)
            await self._mute_in_meeting()
            await self._take_screenshot("teams_in_meeting")
        return admitted

    async def detect_meeting_end(self) -> Optional[str]:
        """Check if the Teams meeting has ended.

        Uses post-call detection + single fast JS evaluate for text checks,
        then falls back to hangup button check only if needed.
        """
        if not self._page:
            return "browser_closed"

        try:
            # Check for post-call page first (most reliable signal)
            if await self._is_post_call_page():
                return "meeting_ended"

            # Single JS call checks all text conditions at once (fast)
            result = await self._page.evaluate("""() => {
                const body = (document.body.innerText || '').toLowerCase();
                // Meeting ended / you left
                if (body.includes('meeting has ended') ||
                    body.includes('you left the meeting') ||
                    body.includes('call has ended') ||
                    body.includes('meeting ended'))
                    return 'meeting_ended';
                // Removed by host
                if (body.includes('you were removed') ||
                    body.includes('been removed from'))
                    return 'bot_kicked';
                // Alone in meeting
                if (body.includes("you're the only one") ||
                    body.includes("no one else has joined") ||
                    body.includes("only you are in this meeting") ||
                    body.includes("waiting for others"))
                    return 'everyone_left';
                return null;
            }""")
            if result:
                return result

            # Quick toolbar check — move mouse to keep toolbar visible
            try:
                await self._page.mouse.move(960, 540)
            except Exception:
                pass

            # Check if hangup button exists
            hangup = await self._find_element([
                InMeetingSelectors.HANGUP_BUTTON,
                InMeetingSelectors.HANGUP_BUTTON_ALT,
                InMeetingSelectors.HANGUP_BUTTON_ALT2,
                InMeetingSelectors.HANGUP_BUTTON_ALT3,
            ])
            if not hangup:
                # Brief pause and retry once
                await asyncio.sleep(1)
                hangup = await self._find_element([
                    InMeetingSelectors.HANGUP_BUTTON,
                    InMeetingSelectors.HANGUP_BUTTON_ALT,
                    InMeetingSelectors.HANGUP_BUTTON_ALT2,
                    InMeetingSelectors.HANGUP_BUTTON_ALT3,
                ])
                if not hangup:
                    # Verify we're truly disconnected
                    any_meeting_el = await self._find_element([
                        InMeetingSelectors.MIC_IN_MEETING,
                        InMeetingSelectors.MIC_IN_MEETING_ALT,
                        InMeetingSelectors.CHAT_BUTTON,
                        InMeetingSelectors.CHAT_BUTTON_ALT,
                        InMeetingSelectors.PARTICIPANTS_BUTTON,
                        InMeetingSelectors.PARTICIPANTS_BUTTON_ALT,
                    ])
                    if any_meeting_el:
                        return None
                    await self._take_screenshot("teams_disconnect_check")
                    return "disconnected"

            return None

        except Exception as e:
            self.logger.warning(f"Error detecting Teams meeting end: {e}")
            return "detection_error"

    async def leave_meeting(self):
        """Leave the Teams meeting by navigating away.

        Navigating to about:blank tears down WebRTC/WebSocket connections
        reliably, avoiding fragile hangup button selectors.
        """
        if not self._page:
            return

        self.logger.info("Leaving Teams meeting...")
        try:
            await self._page.goto("about:blank", timeout=5000)
            self.logger.info("Left Teams meeting (navigated away)")
        except Exception as e:
            self.logger.warning(f"Navigation away failed: {e}")
            try:
                await self._page.close()
                self.logger.info("Left Teams meeting (page closed)")
            except Exception:
                pass

    async def dismiss_in_meeting_popups(self):
        """Dismiss any in-meeting popups."""
        if not self._page:
            return
        await self._try_click(
            [
                InMeetingSelectors.CLOSE_POPUP,
                InMeetingSelectors.DISMISS_BUTTON,
                InMeetingSelectors.GOT_IT_BUTTON,
            ],
            "Teams popup",
        )

    async def get_participant_count(self) -> Optional[int]:
        """Try to extract participant count from the People button or JS fallback."""
        if not self._page:
            return None

        # Move mouse to trigger toolbar visibility (Teams auto-hides it)
        try:
            await self._page.mouse.move(960, 540)
        except Exception:
            pass
        await asyncio.sleep(0.5)

        for selector in [
            InMeetingSelectors.PARTICIPANTS_BUTTON,
            InMeetingSelectors.PARTICIPANTS_BUTTON_ALT,
        ]:
            try:
                btn = await self._page.query_selector(selector)
                if btn:
                    label = await btn.get_attribute("aria-label") or ""
                    match = re.search(r'(\d+)', label)
                    if match:
                        return int(match.group(1))
                    text = await btn.inner_text()
                    match = re.search(r'(\d+)', text)
                    if match:
                        return int(match.group(1))
            except Exception:
                continue

        # JS fallback: comprehensive search for participant count in Teams DOM
        try:
            count = await self._page.evaluate("""() => {
                // 1. Check all buttons for People/participant labels with count
                const btns = document.querySelectorAll('button');
                for (const btn of btns) {
                    const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                    if (label.includes('people') || label.includes('participant') || label.includes('roster')) {
                        const match = label.match(/(\\d+)/);
                        if (match) return parseInt(match[1]);
                        const text = btn.innerText || '';
                        const textMatch = text.match(/(\\d+)/);
                        if (textMatch) return parseInt(textMatch[1]);
                    }
                }
                // 2. Check roster/participant list items
                const rosterSelectors = [
                    '[data-tid="roster-participant"]',
                    '[data-tid="participantItem"]',
                    '[data-cid="roster-participant"]',
                    '.roster-list-item',
                    '[role="listitem"][data-tid*="participant"]',
                ];
                for (const sel of rosterSelectors) {
                    const items = document.querySelectorAll(sel);
                    if (items.length > 0) return items.length;
                }
                // 3. Check for "only one here" / alone indicators → return 1
                const body = document.body.innerText || '';
                if (body.includes("You're the only one here") ||
                    body.includes("you're the only one") ||
                    body.includes("No one else has joined") ||
                    body.includes("Only you are in this meeting") ||
                    body.includes("Waiting for others")) {
                    return 1;
                }
                return null;
            }""")
            if count is not None and count > 0:
                return count
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
                // Fallback: scrape directly
                const names = new Set();
                document.querySelectorAll('[data-tid*="participantsInCall"]').forEach(el => {
                    const label = el.getAttribute('aria-label') || '';
                    const match = label.match(/^([^,]+)/);
                    if (match) {
                        const n = match[1].trim();
                        if (n && n.length < 100) names.add(n);
                    }
                });
                document.querySelectorAll('[data-cid="roster-participant"] span[title]').forEach(el => {
                    const n = el.getAttribute('title')?.trim();
                    if (n && n.length < 100) names.add(n);
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
                info.captionWrapper = !!document.querySelector('[data-tid="closed-caption-renderer-wrapper"]');
                info.authorSpans = document.querySelectorAll('span[data-tid="author"]').length;
                info.captionTextSpans = document.querySelectorAll('span[data-tid="closed-caption-text"]').length;
                return JSON.stringify(info);
            }""")
        except Exception as e:
            return f"debug error: {e}"

    async def enable_captions(self):
        """Enable Teams closed captions and inject MutationObserver for speaker detection."""
        if not self._page:
            return

        self.logger.info("Enabling Teams captions...")

        # Check if captions already active
        already_active = await self._page.evaluate(
            "!!document.querySelector('[data-tid=\"closed-caption-renderer-wrapper\"]')"
        )
        if already_active:
            self.logger.info("Teams captions already active")
        else:
            # Primary: keyboard shortcut Ctrl+Shift+A
            try:
                await self._page.keyboard.press("Control+Shift+a")
                self.logger.info("Sent Ctrl+Shift+A to enable Teams captions")
                await asyncio.sleep(2)
            except Exception as e:
                self.logger.debug(f"Caption keyboard shortcut failed: {e}")

            # Check if it worked
            active = await self._page.evaluate(
                "!!document.querySelector('[data-tid=\"closed-caption-renderer-wrapper\"]')"
            )
            if not active:
                # Fallback: More menu → Show live captions
                self.logger.info("Shortcut didn't work, trying More menu fallback...")
                try:
                    await self._page.mouse.move(960, 540)
                    await asyncio.sleep(1)
                except Exception:
                    pass

                more_clicked = await self._try_click(
                    ['button[aria-label="More"]', 'button[aria-label="More actions"]'],
                    "Teams More button",
                )
                if more_clicked:
                    await asyncio.sleep(1)
                    # Click "Show live captions"
                    try:
                        cap_btn = await self._page.query_selector('#closed-captions-button')
                        if not cap_btn:
                            cap_btn = await self._page.query_selector(
                                'div[aria-label="Show live captions"][role="menuitemcheckbox"]'
                            )
                        if cap_btn:
                            checked = await cap_btn.get_attribute("aria-checked")
                            if checked != "true":
                                await cap_btn.click()
                                self.logger.info("Clicked Show live captions in More menu")
                            else:
                                self.logger.info("Live captions already checked in menu")
                        else:
                            self.logger.warning("Show live captions button not found in menu")
                    except Exception as e:
                        self.logger.debug(f"Captions menu click failed: {e}")

                # Wait for captions to appear
                await asyncio.sleep(2)
                active = await self._page.evaluate(
                    "!!document.querySelector('[data-tid=\"closed-caption-renderer-wrapper\"]')"
                )
                if active:
                    self.logger.info("Teams captions verified active via fallback")
                else:
                    self.logger.warning("Teams captions may not have been enabled")

        # Inject MutationObserver for speaker detection
        await self._page.evaluate("""() => {
            if (window.__memoirCaptionObserver) return;
            window.__memoirCaption = { name: null, ts: 0 };

            const observer = new MutationObserver(() => {
                const authors = document.querySelectorAll('span[data-tid="author"]');
                if (authors.length > 0) {
                    const name = authors[authors.length - 1].textContent.trim();
                    if (name) window.__memoirCaption = { name, ts: Date.now() };
                }
            });
            observer.observe(document.body, { childList: true, characterData: true, subtree: true });
            window.__memoirCaptionObserver = observer;
        }""")
        self.logger.info("Injected Teams caption MutationObserver")

    async def open_participant_panel(self):
        """Open the Teams People/Roster panel and inject observer for name scraping."""
        if not self._page:
            return

        self.logger.info("Opening Teams People panel...")

        # Move mouse to trigger toolbar
        try:
            await self._page.mouse.move(960, 540)
            await asyncio.sleep(1)
        except Exception:
            pass

        # Click People button
        clicked = await self._try_click(
            [
                'button[aria-label*="People" i]',
                'button[data-tid="roster-button"]',
                'button[aria-label*="people" i]',
            ],
            "Teams People button",
        )
        if clicked:
            await asyncio.sleep(2)  # Wait for panel to render
        else:
            self.logger.warning("Teams People button not found")

        # Inject MutationObserver for participant names
        await self._page.evaluate("""() => {
            if (window.__memoirParticipantObserver) return;
            window.__memoirParticipants = [];

            function scrapeParticipants() {
                const names = new Set();
                // participantsInCall tree items
                document.querySelectorAll('[data-tid*="participantsInCall"]').forEach(el => {
                    const label = el.getAttribute('aria-label') || '';
                    const match = label.match(/^([^,]+)/);
                    if (match) {
                        const n = match[1].trim();
                        if (n && n.length < 100) names.add(n);
                    }
                });
                // roster-participant spans with title
                document.querySelectorAll('[data-cid="roster-participant"] span[title]').forEach(el => {
                    const n = el.getAttribute('title')?.trim();
                    if (n && n.length < 100) names.add(n);
                });
                // fui-StyledText with title attribute
                document.querySelectorAll('span.fui-StyledText[title]').forEach(el => {
                    const n = el.getAttribute('title')?.trim();
                    if (n && n.length < 100 && n.length > 1) names.add(n);
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
        self.logger.info("Injected Teams participant panel observer")

    # --- Private helpers ---

    async def _mute_in_meeting(self):
        """Ensure microphone is muted after joining the meeting."""
        if not self._page:
            return
        try:
            # Use keyboard shortcut first (fastest, no UI dependency)
            await self._page.keyboard.press("Control+Shift+m")
            await asyncio.sleep(0.3)

            # Move mouse to trigger toolbar and verify state
            await self._page.mouse.move(960, 540)
            await asyncio.sleep(0.5)

            for selector in [
                InMeetingSelectors.MIC_IN_MEETING,
                InMeetingSelectors.MIC_IN_MEETING_ALT,
            ]:
                btn = await self._page.query_selector(selector)
                if btn:
                    label = (await btn.get_attribute("aria-label") or "").lower()
                    # "Mute" means mic is currently ON → click to mute
                    if "mute" in label and "unmute" not in label:
                        await btn.click()
                        self.logger.info("Muted microphone in-meeting via button")
                    elif "unmute" in label:
                        self.logger.info("Microphone already muted in-meeting")
                    return
            self.logger.info("Mute keyboard shortcut sent (no button found to verify)")
        except Exception as e:
            self.logger.warning(f"Failed to mute in-meeting: {e}")

    async def _is_post_call_page(self) -> bool:
        """Check if Teams is showing the post-call page (meeting ended / you left)."""
        if not self._page:
            return False
        try:
            return await self._page.evaluate("""() => {
                const body = document.body.innerText || '';
                return body.includes('Enjoy your call') ||
                       body.includes('Did you leave by mistake') ||
                       body.includes('Rejoin the call') ||
                       body.includes('Join Teams today for free');
            }""")
        except Exception:
            return False

    async def _handle_interstitial(self):
        """Handle Teams' interstitial page asking to open the desktop app."""
        # Wait for the interstitial to fully render (teams.live.com can be slow)
        await asyncio.sleep(3)

        interstitial_selectors = [
            InterstitialSelectors.CONTINUE_ON_BROWSER,
            InterstitialSelectors.CONTINUE_ON_BROWSER_ALT,
            InterstitialSelectors.JOIN_ON_WEB,
            InterstitialSelectors.JOIN_ON_WEB_ALT,
            InterstitialSelectors.JOIN_ON_WEB_ALT2,
            InterstitialSelectors.USE_WEB_APP,
            InterstitialSelectors.USE_WEB_APP_ALT,
        ]

        # Try multiple rounds — interstitial may take time to render
        for attempt in range(3):
            for selector in interstitial_selectors:
                try:
                    link = await self._page.query_selector(selector)
                    if link:
                        self.logger.info(f"Teams interstitial detected, clicking: {selector}")
                        await link.click()
                        await self._page.wait_for_load_state("networkidle", timeout=15000)
                        await self._random_delay()
                        await self._take_screenshot("teams_after_interstitial")
                        return
                except Exception:
                    continue

            # Check for "Rejoin the call" button (post-call recovery)
            try:
                rejoin = await self._page.query_selector('button:has-text("Rejoin the call")')
                if not rejoin:
                    rejoin = await self._page.query_selector('a:has-text("Rejoin the call")')
                if rejoin:
                    self.logger.info("Post-call page detected, clicking 'Rejoin the call'")
                    await rejoin.click()
                    await self._page.wait_for_load_state("networkidle", timeout=15000)
                    await self._random_delay()
                    await self._take_screenshot("teams_after_rejoin")
                    return
            except Exception:
                pass

            if attempt < 2:
                await asyncio.sleep(2)

        self.logger.warning("No interstitial buttons found — page may have loaded directly to pre-join")

    async def _handle_guest_join(self):
        """Click 'Join as a guest' if Teams shows that option."""
        await self._try_click(
            [PreJoinSelectors.JOIN_AS_GUEST, PreJoinSelectors.JOIN_AS_GUEST_ALT],
            "Join as guest button",
        )
        if await self._find_element([PreJoinSelectors.JOIN_AS_GUEST, PreJoinSelectors.JOIN_AS_GUEST_ALT]):
            await asyncio.sleep(2)

    async def _check_for_errors(self):
        """Check for Teams error states that prevent joining."""
        error_checks = [
            ([ErrorSelectors.SIGN_IN_REQUIRED, ErrorSelectors.SIGN_IN_REQUIRED_ALT],
             "Teams requires sign-in — org policy blocks guest join"),
            ([ErrorSelectors.NOT_FOUND, ErrorSelectors.NOT_FOUND_ALT],
             "Teams meeting not found"),
            ([ErrorSelectors.EXPIRED, ErrorSelectors.EXPIRED_ALT],
             "Teams meeting link expired"),
            ([ErrorSelectors.DENIED, ErrorSelectors.DENIED_ALT],
             "Teams denied entry"),
        ]
        for selectors, message in error_checks:
            el = await self._find_element(selectors)
            if el:
                await self._take_screenshot("teams_error")
                raise MeetingDeniedError(message)

    async def _wait_for_prejoin_screen(self):
        """Wait for the Teams pre-join screen (name input or join button)."""
        self.logger.info("Waiting for Teams pre-join screen...")
        prejoin_selectors = [
            PreJoinSelectors.NAME_INPUT,
            PreJoinSelectors.NAME_INPUT_ALT,
            PreJoinSelectors.NAME_INPUT_ALT2,
            PreJoinSelectors.NAME_INPUT_ALT3,
            PreJoinSelectors.JOIN_BUTTON,
            PreJoinSelectors.JOIN_BUTTON_ALT,
        ]
        deadline = asyncio.get_event_loop().time() + 30
        while asyncio.get_event_loop().time() < deadline:
            for sel in prejoin_selectors:
                el = await self._page.query_selector(sel)
                if el:
                    self.logger.info("Teams pre-join screen loaded")
                    return
            await asyncio.sleep(1)
        self.logger.warning("Teams pre-join screen wait timed out")
        await self._take_screenshot("teams_prejoin_timeout")

    async def _enter_name(self):
        """Enter the bot name in the Teams name input."""
        selectors = [
            PreJoinSelectors.NAME_INPUT,
            PreJoinSelectors.NAME_INPUT_ALT,
            PreJoinSelectors.NAME_INPUT_ALT2,
            PreJoinSelectors.NAME_INPUT_ALT3,
        ]
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
                except Exception:
                    continue
            await asyncio.sleep(0.5)
        self.logger.warning("Teams name input not found — continuing without name")

    async def _dismiss_media_permission_dialog(self):
        """Dismiss the 'Continue without audio or video' dialog if Teams shows it.

        This appears when the browser doesn't provide real media devices.
        Must be dismissed before the bot can interact with the pre-join form.
        """
        clicked = await self._try_click(
            [
                PreJoinSelectors.CONTINUE_NO_MEDIA,
                PreJoinSelectors.CONTINUE_NO_MEDIA_ALT,
            ],
            "Continue without audio/video dialog",
        )
        if clicked:
            await asyncio.sleep(2)
            await self._take_screenshot("teams_after_media_dialog_dismiss")

    async def _disable_media(self):
        """Turn off microphone and camera on the pre-join screen.

        Uses a multi-layered approach for reliability:
        1. WebRTC-level: stop all audio+video tracks
        2. Keyboard shortcuts: Ctrl+Shift+M (mic), Ctrl+Shift+O (camera)
        3. CSS selector fallback: click toggle buttons on pre-join screen
        """
        await asyncio.sleep(1)

        # --- 1. Stop all media tracks at WebRTC level (most reliable) ---
        try:
            stopped = await self._page.evaluate("""() => {
                let stopped = 0;
                document.querySelectorAll('video, audio').forEach(el => {
                    if (el.srcObject) {
                        el.srcObject.getTracks().forEach(t => {
                            t.enabled = false;
                            t.stop();
                            stopped++;
                        });
                    }
                });
                // Also stop tracks from getUserMedia if accessible
                if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
                    try {
                        const streams = document.querySelectorAll('*');
                        streams.forEach(el => {
                            if (el.srcObject && el.srcObject.getTracks) {
                                el.srcObject.getTracks().forEach(t => {
                                    t.enabled = false;
                                    t.stop();
                                    stopped++;
                                });
                            }
                        });
                    } catch(e) {}
                }
                return stopped;
            }""")
            if stopped and stopped > 0:
                self.logger.info(f"Stopped {stopped} media track(s) via WebRTC API")
            else:
                self.logger.info("No active media tracks found to stop")
        except Exception as e:
            self.logger.warning(f"WebRTC media stop failed: {e}")

        # --- 2. Keyboard shortcuts (Teams shortcuts) ---
        # Ctrl+Shift+M = toggle mic, Ctrl+Shift+O = toggle camera
        try:
            await self._page.keyboard.press("Control+Shift+m")
            self.logger.info("Sent Ctrl+Shift+M to toggle mic off")
            await asyncio.sleep(0.3)
        except Exception as e:
            self.logger.debug(f"Mic keyboard shortcut failed: {e}")

        try:
            await self._page.keyboard.press("Control+Shift+o")
            self.logger.info("Sent Ctrl+Shift+O to toggle camera off")
            await asyncio.sleep(0.3)
        except Exception as e:
            self.logger.debug(f"Camera keyboard shortcut failed: {e}")

        # --- 3. CSS selector fallback: verify and fix via UI buttons ---
        for device, selectors in [
            ("microphone", [
                PreJoinSelectors.MIC_TOGGLE,
                PreJoinSelectors.MIC_TOGGLE_ALT,
                PreJoinSelectors.MIC_TOGGLE_ALT2,
            ]),
            ("camera", [
                PreJoinSelectors.CAMERA_TOGGLE,
                PreJoinSelectors.CAMERA_TOGGLE_ALT,
                PreJoinSelectors.CAMERA_TOGGLE_ALT2,
            ]),
        ]:
            found = False
            for selector in selectors:
                try:
                    btn = await self._page.query_selector(selector)
                    if btn:
                        found = True
                        label = (await btn.get_attribute("aria-label") or "").lower()
                        self.logger.debug(f"Pre-join {device} button label: '{label}'")
                        # Already off — labels like "turn on", "unmute"
                        if "turn on" in label or "unmute" in label:
                            self.logger.info(f"{device} already disabled on pre-join")
                            break
                        # Currently on — labels like "turn off", "mute mic", "microphone on"
                        if ("turn off" in label or "mute" in label or
                                f"{device} on" in label or "disable" in label):
                            await btn.click()
                            self.logger.info(f"Disabled {device} on pre-join via CSS")
                            break
                        # Ambiguous label — check aria-pressed or data-state
                        pressed = await btn.get_attribute("aria-pressed")
                        data_state = await btn.get_attribute("data-state")
                        if pressed == "true" or data_state == "on":
                            await btn.click()
                            self.logger.info(f"Disabled {device} on pre-join (toggle state)")
                            break
                except Exception:
                    continue
            if not found:
                self.logger.debug(f"Pre-join {device} CSS toggle not found (keyboard shortcut used)")

    async def _click_join(self):
        """Click the Join now button."""
        selectors = [
            PreJoinSelectors.JOIN_BUTTON,
            PreJoinSelectors.JOIN_BUTTON_ALT,
            PreJoinSelectors.JOIN_BUTTON_ALT2,
        ]
        deadline = asyncio.get_event_loop().time() + 15
        dismiss_attempted = False
        while asyncio.get_event_loop().time() < deadline:
            for selector in selectors:
                try:
                    btn = await self._page.query_selector(selector)
                    if btn:
                        # Check if actually visible/clickable
                        is_visible = await btn.is_visible()
                        if not is_visible:
                            continue
                        await self._random_delay()
                        try:
                            from app.services.phase_log import log_phase
                            log_phase("pre_join_done", platform="teams")
                        except Exception:
                            pass
                        await btn.click()
                        self.logger.info(f"Clicked Teams join button: {selector}")
                        return
                except Exception:
                    continue
            # If join button not found/visible, try dismissing media dialog once
            if not dismiss_attempted:
                dismiss_attempted = True
                await self._dismiss_media_permission_dialog()
            await asyncio.sleep(0.5)
        await self._take_screenshot("teams_join_button_not_found")
        raise BrowserError("Teams join button not found")

    async def _is_in_lobby(self) -> bool:
        """Check if the page is showing a Teams lobby / waiting room."""
        if not self._page:
            return False
        try:
            return await self._page.evaluate("""() => {
                const body = document.body.innerText || '';
                return body.includes('let you in soon') ||
                       body.includes('waiting to be let in') ||
                       body.includes('Someone in the meeting should let you in') ||
                       body.includes('will let you in when the meeting starts') ||
                       body.includes('Someone will let you in');
            }""")
        except Exception:
            return False

    async def _wait_for_admission(self) -> bool:
        """Wait to be admitted to the Teams meeting.

        Uses three layers (all gated by lobby/post-call checks):
        1. CSS selectors — hangup button (primary) or 3+ toolbar buttons + hangup
        2. PulseAudio sink-input — only trusted when NOT in lobby
        3. JS fallback — hangup button present + toolbar count + no lobby text
        """
        self.logger.info(f"Waiting for Teams admission (timeout: {settings.WAITING_ROOM_TIMEOUT_SECONDS}s)...")

        # Tier 1.6 (Teams variant): auto-admit early-exit probe. Before
        # the 3 s grace period, do a quick 1.5 s probe to detect
        # already-admitted meetings (no lobby — organizer pre-approved
        # or open-access). Requires 2 consecutive positive hangup-button
        # hits 100 ms apart to avoid pre-join false positives. Hangup
        # button is the strongest signal because it doesn't exist in
        # the Teams pre-join or lobby UI. On hit, return immediately,
        # skipping the 3 s grace and the main admission loop — saves
        # ~3-8 s.
        early_admitted_selectors = [
            InMeetingSelectors.HANGUP_BUTTON,
            InMeetingSelectors.HANGUP_BUTTON_ALT,
            InMeetingSelectors.HANGUP_BUTTON_ALT2,
            InMeetingSelectors.HANGUP_BUTTON_ALT3,
        ]
        EARLY_PROBE_WINDOW = 1.5
        EARLY_PROBE_INTERVAL = 0.1
        EARLY_PROBE_CONSECUTIVE_HITS = 2
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
                        "Tier 1.6: Teams auto-admit detected via early-probe — "
                        "skipping 3 s grace period"
                    )
                    try:
                        from app.services.phase_log import log_phase
                        log_phase("admission_detected", platform="teams",
                                  path="auto_admit_early")
                    except Exception:
                        pass
                    return True
            else:
                consecutive_hits = 0
            await asyncio.sleep(EARLY_PROBE_INTERVAL)

        # Small grace period for page transition after clicking join
        await asyncio.sleep(3)

        # Hangup button selectors — must find one for CSS-based admission
        hangup_selectors = [
            InMeetingSelectors.HANGUP_BUTTON,
            InMeetingSelectors.HANGUP_BUTTON_ALT,
            InMeetingSelectors.HANGUP_BUTTON_ALT2,
            InMeetingSelectors.HANGUP_BUTTON_ALT3,
        ]
        # Additional in-meeting indicators (used alongside hangup for confidence)
        extra_admitted_selectors = [
            InMeetingSelectors.MIC_IN_MEETING,
            InMeetingSelectors.MIC_IN_MEETING_ALT,
            InMeetingSelectors.CHAT_BUTTON,
            InMeetingSelectors.CHAT_BUTTON_ALT,
            InMeetingSelectors.PARTICIPANTS_BUTTON,
            InMeetingSelectors.PARTICIPANTS_BUTTON_ALT,
            InMeetingSelectors.SHARE_BUTTON,
        ]
        denied_selectors = [
            ErrorSelectors.DENIED,
            ErrorSelectors.DENIED_ALT,
            ErrorSelectors.DENIED_ENTRY,
            ErrorSelectors.DENIED_CANT_JOIN,
        ]
        deadline = asyncio.get_event_loop().time() + settings.WAITING_ROOM_TIMEOUT_SECONDS
        screenshot_taken = False
        audio_confirmed_at = None
        lobby_logged = False

        while asyncio.get_event_loop().time() < deadline:
            # Check for denial
            for sel in denied_selectors:
                el = await self._page.query_selector(sel)
                if el:
                    await self._take_screenshot("teams_denied")
                    raise MeetingDeniedError("Teams entry denied by organizer")

            # Check for post-call page (meeting already ended / got kicked immediately)
            if await self._is_post_call_page():
                self.logger.warning("Post-call page detected during admission wait")
                await self._take_screenshot("teams_post_call_during_admission")
                raise BrowserError("Teams meeting ended before bot could join")

            # Detect lobby state (blocks PulseAudio false positives)
            in_lobby = await self._is_in_lobby()
            if in_lobby and not lobby_logged:
                self.logger.info("In Teams lobby — waiting for host to admit...")
                await self._take_screenshot("teams_in_lobby")
                lobby_logged = True
                # Notify backend so frontend shows "Waiting in lobby"
                if self._event_sender:
                    await self._event_sender.send_event(EventType.WAITING_ROOM_ENTERED)

            # Layer 1: CSS selectors — require hangup button as primary signal
            hangup_found = await self._find_element(hangup_selectors)
            if hangup_found:
                self.logger.info("Teams admission detected via hangup button")
                try:
                    from app.services.phase_log import log_phase
                    log_phase("admission_detected", platform="teams",
                              path=("lobby" if in_lobby else "auto_admit"))
                except Exception:
                    pass
                return True

            # Also accept if multiple non-hangup in-meeting buttons are found
            # (hangup may take a moment to render) — but NOT if in lobby
            if not in_lobby:
                extra_count = 0
                for sel in extra_admitted_selectors:
                    el = await self._page.query_selector(sel)
                    if el:
                        extra_count += 1
                if extra_count >= 3:
                    # Double-check: wait a moment and verify hangup appears
                    await asyncio.sleep(2)
                    hangup_found = await self._find_element(hangup_selectors)
                    if hangup_found:
                        self.logger.info("Teams admission detected via toolbar buttons + hangup")
                        try:
                            from app.services.phase_log import log_phase
                            log_phase("admission_detected", platform="teams",
                                      path="toolbar_plus_hangup")
                        except Exception:
                            pass
                        return True

            # Layer 2: PulseAudio — only trust if NOT in lobby (fake devices
            # create sink-inputs even on the pre-join/lobby camera preview)
            if not in_lobby and await self._is_audio_streaming():
                if audio_confirmed_at is None:
                    audio_confirmed_at = asyncio.get_event_loop().time()
                    self.logger.info("Audio stream detected, confirming...")
                elif asyncio.get_event_loop().time() - audio_confirmed_at >= 3:
                    self.logger.info("Teams admission detected via PulseAudio")
                    try:
                        from app.services.phase_log import log_phase
                        log_phase("admission_detected", platform="teams",
                                  path="pulseaudio")
                    except Exception:
                        pass
                    return True
            else:
                audio_confirmed_at = None

            # Layer 3: JS fallback — requires hangup/leave button present + no lobby
            if not in_lobby:
                try:
                    in_meeting = await self._page.evaluate("""() => {
                        // Must find a hangup/leave button
                        const hangup = document.querySelector('#hangup-button') ||
                                       document.querySelector('button[data-tid="hangup-btn"]') ||
                                       document.querySelector('button[aria-label*="hang up" i]') ||
                                       document.querySelector('button[aria-label*="Leave" i]');
                        if (!hangup) return false;
                        // Also check for other toolbar buttons
                        const buttons = document.querySelectorAll('button[aria-label]');
                        let toolbarCount = 0;
                        for (const btn of buttons) {
                            const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                            if (label.includes('mute') || label.includes('camera') ||
                                label.includes('share') || label.includes('people') ||
                                label.includes('chat') || label.includes('hang up') ||
                                label.includes('leave') || label.includes('react'))
                                toolbarCount++;
                        }
                        return toolbarCount >= 3;
                    }""")
                    if in_meeting:
                        self.logger.info("Teams admission detected via JS toolbar fallback")
                        try:
                            from app.services.phase_log import log_phase
                            log_phase("admission_detected", platform="teams",
                                      path="js_fallback")
                        except Exception:
                            pass
                        return True
                except Exception as e:
                    self.logger.debug(f"JS admission check failed: {e}")

            # Diagnostic screenshot at halfway point
            elapsed = settings.WAITING_ROOM_TIMEOUT_SECONDS - (deadline - asyncio.get_event_loop().time())
            if not screenshot_taken and elapsed > settings.WAITING_ROOM_TIMEOUT_SECONDS / 2:
                await self._take_screenshot("teams_admission_waiting")
                screenshot_taken = True

            await asyncio.sleep(2)

        await self._take_screenshot("teams_admission_timeout")
        raise BrowserError("Teams admission timeout — host did not admit the bot")
