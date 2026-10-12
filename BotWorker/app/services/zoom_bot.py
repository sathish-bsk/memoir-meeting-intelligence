"""
Playwright-based Zoom web client automation.
Handles joining Zoom meetings via the browser, recording, and detecting meeting end.
"""
import asyncio
import re
from typing import Optional
from urllib.parse import urlparse, parse_qs, urlencode

from app.core.config import settings
from app.models.enums import EventType
from app.services.base_bot import MeetingBot, MeetingDeniedError, BrowserError
from app.selectors.zoom import (
    InterstitialSelectors,
    PreJoinSelectors,
    InMeetingSelectors,
    ErrorSelectors,
)


def rewrite_zoom_url(url: str) -> str:
    """Rewrite a Zoom meeting URL to force the web client.

    Converts URLs like:
      https://zoom.us/j/12345678?pwd=abc
      https://us02web.zoom.us/j/12345678?pwd=abc
    To:
      https://app.zoom.us/wc/join/12345678?pwd=abc
    """
    parsed = urlparse(url)
    # Extract meeting ID from path like /j/12345678 or /wc/12345678/join
    meeting_id_match = re.search(r'/(?:j|wc(?:/join)?)/(\d+)', parsed.path)
    if not meeting_id_match:
        return url  # Return as-is if we can't parse it

    meeting_id = meeting_id_match.group(1)
    query_params = parse_qs(parsed.query)

    # Build web client URL
    params = {}
    if "pwd" in query_params:
        params["pwd"] = query_params["pwd"][0]

    base_url = f"https://app.zoom.us/wc/join/{meeting_id}"
    if params:
        base_url += f"?{urlencode(params)}"
    return base_url


class ZoomBot(MeetingBot):
    """Automates joining a Zoom meeting via the web client."""

    async def _is_in_meeting(self) -> bool:
        """Return True iff any in-meeting toolbar selector is present.

        Tier 1.2 (Zoom variant): the lifecycle's adaptive toolbar wait
        uses this to skip the legacy hardcoded sleep(3). LEAVE_BUTTON
        is the strongest signal because it doesn't exist on the Zoom
        pre-join or waiting-room UI. Participants / chat / share /
        reactions are accepted as supporting evidence. MIC_IN_MEETING /
        CAMERA_IN_MEETING are deliberately excluded — their aria-labels
        ("Mute"/"Unmute"/"Start Video"/"Stop Video") match the pre-join
        media toggles and cause instant false-positive admission.
        """
        if not self._page:
            return False
        selectors = [
            InMeetingSelectors.LEAVE_BUTTON,
            InMeetingSelectors.LEAVE_BUTTON_ALT,
            InMeetingSelectors.LEAVE_BUTTON_ALT2,
            InMeetingSelectors.PARTICIPANTS_BUTTON,
            InMeetingSelectors.CHAT_BUTTON,
            InMeetingSelectors.SHARE_SCREEN_BUTTON,
            InMeetingSelectors.REACTIONS_BUTTON,
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
        """Navigate to Zoom web client and join the meeting."""
        if not self._page:
            raise BrowserError("Browser not launched")

        # Try direct web client URL first
        web_url = rewrite_zoom_url(meeting_url)
        self.logger.info(f"Navigating to Zoom web client: {web_url}")
        # Tier 1.1: see browser.py note. Same rationale for Zoom.
        await self._page.goto(web_url, wait_until="load", timeout=30000)
        try:
            from app.services.phase_log import log_phase
            log_phase("navigation_complete", platform="zoom")
        except Exception:
            pass
        await self._random_delay()
        await self._take_screenshot("zoom_after_navigate")

        # Handle interstitial page if we landed there instead of web client
        await self._handle_interstitial()

        # Wait for pre-join screen
        await self._wait_for_prejoin_screen()
        await self._take_screenshot("zoom_prejoin_loaded")

        # Accept ToS if prompted
        await self._accept_tos()

        # Dismiss cookie banners
        await self._dismiss_popups()

        # Enter bot name
        await self._enter_name()

        # Disable microphone and camera
        await self._disable_media()

        # Click join button
        await self._click_join()
        await self._take_screenshot("zoom_after_join_click")

        # Wait for admission
        admitted = await self._wait_for_admission()
        if admitted:
            self.logger.info("Successfully joined Zoom meeting")
            # Ensure mic is muted in-meeting (pre-join mute may not stick)
            await self._mute_in_meeting()
            await self._take_screenshot("zoom_in_meeting")
        return admitted

    async def detect_meeting_end(self) -> Optional[str]:
        """Check if the Zoom meeting has ended.

        Uses a single fast JS evaluate for all text-based checks,
        then falls back to CSS selectors only if needed.
        """
        if not self._page:
            return "browser_closed"

        try:
            # Single JS call checks all text conditions at once (fast)
            result = await self._page.evaluate("""() => {
                const body = (document.body.innerText || '').toLowerCase();
                // Meeting ended by host
                if (body.includes('meeting has been ended') ||
                    body.includes('meeting has ended') ||
                    body.includes('this meeting has ended') ||
                    body.includes('the host has ended'))
                    return 'meeting_ended';
                // Removed by host
                if (body.includes('host has removed you') ||
                    body.includes('been removed from'))
                    return 'bot_kicked';
                // Alone in meeting
                if (body.includes("you're the only one") ||
                    body.includes("you are the only") ||
                    body.includes("no one else is here") ||
                    body.includes("waiting for others to join"))
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

            # Check if leave button exists (toolbar should be visible from mouse move)
            leave_btn = await self._find_element([
                InMeetingSelectors.LEAVE_BUTTON,
                InMeetingSelectors.LEAVE_BUTTON_ALT,
                InMeetingSelectors.LEAVE_BUTTON_ALT2,
            ])
            if not leave_btn:
                # Brief pause and retry once — toolbar may need a moment
                await asyncio.sleep(1)
                leave_btn = await self._find_element([
                    InMeetingSelectors.LEAVE_BUTTON,
                    InMeetingSelectors.LEAVE_BUTTON_ALT,
                    InMeetingSelectors.LEAVE_BUTTON_ALT2,
                ])
                if not leave_btn:
                    # Verify we're truly disconnected (not just toolbar hidden)
                    any_meeting_el = await self._find_element([
                        InMeetingSelectors.MIC_IN_MEETING,
                        InMeetingSelectors.MIC_IN_MEETING_ALT,
                        InMeetingSelectors.CHAT_BUTTON,
                        InMeetingSelectors.PARTICIPANTS_BUTTON,
                    ])
                    if any_meeting_el:
                        return None
                    await self._take_screenshot("zoom_disconnect_check")
                    return "disconnected"

            return None

        except Exception as e:
            self.logger.warning(f"Error detecting Zoom meeting end: {e}")
            return "detection_error"

    async def leave_meeting(self):
        """Leave the Zoom meeting by clicking the Leave button via JS.

        Zoom distinguishes "intentional leave" (Leave button click) from
        "connection drop" (browser close).  On a connection drop Zoom keeps
        the participant for reconnection.  We must click Leave so Zoom sends
        the proper leave signal to its server.
        """
        if not self._page:
            return

        self.logger.info("Leaving Zoom meeting...")

        # Step 1: Move mouse to reveal the auto-hiding toolbar
        try:
            viewport = self._page.viewport_size or {"width": 1280, "height": 720}
            await self._page.mouse.move(viewport["width"] // 2, viewport["height"] - 10)
            await asyncio.sleep(0.5)
        except Exception:
            pass

        # Step 2: Use JavaScript to find and click the Leave button.
        # This is more reliable than Playwright selectors because we can
        # bypass visibility checks and use flexible matching.
        try:
            left = await self._page.evaluate("""() => {
                // Try multiple selectors for the leave button
                const selectors = [
                    'button[aria-label="Leave"]',
                    'button.footer__leave-btn',
                    'button[aria-label*="leave" i]',
                    'button[aria-label*="end" i]',
                ];
                for (const sel of selectors) {
                    const btn = document.querySelector(sel);
                    if (btn) {
                        btn.click();
                        return sel;
                    }
                }
                // Fallback: find any button with "Leave" text
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    if (btn.textContent.trim() === 'Leave' ||
                        btn.textContent.includes('Leave')) {
                        btn.click();
                        return 'text-match';
                    }
                }
                return null;
            }""")
            if left:
                self.logger.info(f"Clicked leave button via: {left}")
            else:
                self.logger.warning("Leave button not found via JS")
        except Exception as e:
            self.logger.warning(f"JS leave click failed: {e}")

        # Step 3: Wait for the confirmation dialog and click "Leave Meeting"
        await asyncio.sleep(1)
        try:
            await self._page.evaluate("""() => {
                const selectors = [
                    'button.leave-meeting-options__btn',
                    'button[data-testid="leave-meeting"]',
                ];
                for (const sel of selectors) {
                    const btn = document.querySelector(sel);
                    if (btn) { btn.click(); return sel; }
                }
                // Fallback: find button with "Leave Meeting" or "Leave" in dialogs
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    const text = btn.textContent.trim();
                    if (text === 'Leave Meeting' || text === 'Leave') {
                        btn.click();
                        return 'text-match: ' + text;
                    }
                }
                return null;
            }""")
            self.logger.info("Clicked leave confirmation")
        except Exception as e:
            self.logger.warning(f"Leave confirmation failed: {e}")

        # Step 4: Wait for Zoom to process the leave
        await asyncio.sleep(3)

        # Step 5: Close browser as final cleanup
        try:
            if self._page:
                await self._page.close()
                self._page = None
            if self._context:
                await self._context.close()
                self._context = None
            if self._browser:
                await self._browser.close()
                self._browser = None
        except Exception:
            pass

        self.logger.info("Left Zoom meeting")

    async def dismiss_in_meeting_popups(self):
        """Dismiss any in-meeting popups."""
        if not self._page:
            return
        await self._try_click(
            [
                InMeetingSelectors.CLOSE_POPUP,
                InMeetingSelectors.DISMISS_DIALOG,
                InMeetingSelectors.DISMISS_DIALOG_ALT,
            ],
            "Zoom popup",
        )

    async def get_participant_count(self) -> Optional[int]:
        """Try to extract participant count from the Participants button or JS fallback."""
        if not self._page:
            return None

        # Move mouse to trigger toolbar visibility
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
                    # Try inner text (Zoom sometimes shows count as badge text)
                    text = await btn.inner_text()
                    match = re.search(r'(\d+)', text)
                    if match:
                        return int(match.group(1))
            except Exception:
                continue

        # JS fallback: comprehensive search for participant count
        try:
            count = await self._page.evaluate("""() => {
                // 1. Check all buttons for participant labels with count
                const btns = document.querySelectorAll('button');
                for (const btn of btns) {
                    const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                    if (label.includes('participant')) {
                        const match = label.match(/(\\d+)/);
                        if (match) return parseInt(match[1]);
                        const text = btn.innerText || '';
                        const textMatch = text.match(/(\\d+)/);
                        if (textMatch) return parseInt(textMatch[1]);
                    }
                }
                // 2. Check for participant list items in DOM
                const participantSelectors = [
                    '.participants-item',
                    '[class*="participant-item"]',
                    '.attendees-list__item',
                ];
                for (const sel of participantSelectors) {
                    const items = document.querySelectorAll(sel);
                    if (items.length > 0) return items.length;
                }
                // 3. Check for "alone in meeting" indicators → return 1
                const body = (document.body.innerText || '').toLowerCase();
                if (body.includes("you're the only one") ||
                    body.includes("you are the only") ||
                    body.includes("no one else is here") ||
                    body.includes("waiting for others to join")) {
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
                document.querySelectorAll('.participants-li').forEach(el => {
                    const label = el.getAttribute('aria-label') || '';
                    const match = label.match(/^([^(,]+)/);
                    if (match) {
                        const n = match[1].trim();
                        if (n && n.length < 100) names.add(n);
                    }
                });
                document.querySelectorAll('.participants-item__display-name').forEach(el => {
                    const n = el.innerText?.trim();
                    if (n) names.add(n);
                });
                return [...names];
            }""")
            return [n for n in (names or []) if n]
        except Exception:
            return []

    async def get_active_speaker(self) -> Optional[str]:
        """Detect the active speaker via caption scraping with avatar matching."""
        return await self._get_caption_speaker()

    async def debug_active_speaker(self) -> Optional[str]:
        """Return diagnostic info about caption observer + avatar map state."""
        if not self._page:
            return None
        try:
            return await self._page.evaluate("""() => {
                const info = {};
                info.caption = window.__memoirCaption || null;
                info.captionAge = info.caption ? (Date.now() - info.caption.ts) : null;
                info.avatarMap = window.__memoirAvatarMap || {};
                info.avatarMapSize = Object.keys(info.avatarMap).length;
                info.participants = window.__memoirParticipants || [];
                info.participantCount = info.participants.length;
                // Check if subtitle element exists
                info.subtitleExists = !!document.querySelector('#live-transcription-subtitle');
                info.subtitleWrapExists = !!document.querySelector('.lt-subtitle-wrap');
                info.participantsUlExists = !!document.querySelector('#participants-ul');
                return JSON.stringify(info);
            }""")
        except Exception as e:
            return f"debug error: {e}"

    async def _dismiss_zoom_popups(self):
        """Dismiss Zoom notification popups that block toolbar interaction."""
        if not self._page:
            return
        try:
            # Click all notification close buttons in #notificationManager
            removed = await self._page.evaluate("""() => {
                let count = 0;
                // 1. Click all notification close buttons (X) — exact Zoom DOM selector
                document.querySelectorAll('.notification-message-wrap__close, i.close-jd[aria-label="close"]').forEach(btn => {
                    btn.click(); count++;
                });
                // 2. Click OK/Got it/Dismiss buttons for modal popups
                document.querySelectorAll('button').forEach(btn => {
                    const text = btn.textContent?.trim();
                    if (text === 'OK' || text === 'Got it' || text === 'Got It' || text === 'Dismiss' || text === 'Stay Participant') {
                        if (btn.offsetParent !== null) { btn.click(); count++; }
                    }
                });
                return count;
            }""")
            if removed > 0:
                self.logger.info(f"Removed {removed} Zoom popup element(s)")
                await asyncio.sleep(0.5)

            # Press Escape to close any remaining overlays
            await self._page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
        except Exception as e:
            self.logger.debug(f"Popup dismiss error: {e}")
            await asyncio.sleep(0.5)

    async def enable_captions(self):
        """Enable Zoom captions via More menu (3-step flow) and inject avatar-matching caption observer."""
        if not self._page:
            return

        self.logger.info("Enabling Zoom captions...")

        # Check if captions already active
        # NOTE: .lt-subtitle-wrap is ALWAYS present in Zoom — do NOT use it for detection
        # Real indicators: #live-transcription-subtitle (shows when someone speaks)
        # or .live-transcription-subtitle__on-open (shows "You have turned on live transcription")
        already_active = await self._page.evaluate(
            "!!document.querySelector('#live-transcription-subtitle') || !!document.querySelector('.live-transcription-subtitle__on-open')"
        )
        if already_active:
            self.logger.info("Zoom captions already active")
            await self._take_screenshot("zoom_captions_already_active")
        else:
            # Dismiss any Zoom popups/notifications that block toolbar interaction
            await self._dismiss_zoom_popups()
            await self._take_screenshot("zoom_step0_after_popup_dismiss")

            # Move mouse to trigger toolbar visibility (multiple moves)
            try:
                await self._page.mouse.move(960, 540)
                await asyncio.sleep(0.5)
                await self._page.mouse.move(960, 700)
                await asyncio.sleep(0.5)
                await self._page.mouse.move(960, 540)
                await asyncio.sleep(1)
            except Exception:
                pass

            await self._take_screenshot("zoom_step1_toolbar_visible")

            # Step 1: Click "More" button in toolbar
            more_clicked = await self._try_click(
                [
                    'div#moreButton button.footer-button-base__button',
                    'div[feature-type="more"] button.footer-button-base__button',
                    'div.more-button button.footer-button-base__button',
                    '[aria-label*="More meeting control"] button',
                    'div.WCL-footer-more-btn-container.more-button button',
                    'button[aria-label*="More" i]',
                ],
                "Zoom More button",
            )
            if more_clicked:
                await asyncio.sleep(1)
                await self._take_screenshot("zoom_step2_after_more_click")
            else:
                self.logger.warning("Zoom More button not found")
                await self._take_screenshot("zoom_step2_more_NOT_found")
                # Debug: dump footer buttons
                try:
                    footer_debug = await self._page.evaluate("""() => {
                        const footer = document.querySelector('[id="sdk-footer-sortable-list"]') || document.querySelector('.footer__button-wrap')?.parentElement || document.body;
                        const buttons = footer.querySelectorAll('button');
                        return Array.from(buttons).map(b => ({
                            id: b.id, label: b.getAttribute('aria-label')?.substring(0, 80),
                            featureType: b.getAttribute('feature-type'),
                        }));
                    }""")
                    self.logger.info(f"Footer buttons debug: {footer_debug}")
                except Exception:
                    pass

            if more_clicked:
                # Wait for dropdown to appear (class "dropdown-menu show")
                for _ in range(6):
                    dropdown_visible = await self._page.evaluate(
                        "!!document.querySelector('.dropdown-menu.show')"
                    )
                    if dropdown_visible:
                        break
                    await asyncio.sleep(0.5)

                if not dropdown_visible:
                    self.logger.warning("More dropdown not visible, retrying...")
                    await self._take_screenshot("zoom_step3_dropdown_NOT_visible")
                    # Dismiss popups again and retry
                    await self._dismiss_zoom_popups()
                    await asyncio.sleep(0.5)
                    await self._page.mouse.move(960, 540)
                    await asyncio.sleep(0.5)
                    await self._try_click(
                        ['div#moreButton button.footer-button-base__button',
                         'div[feature-type="more"] button.footer-button-base__button'],
                        "Zoom More button (retry)",
                    )
                    await asyncio.sleep(1.5)

                await self._take_screenshot("zoom_step3_dropdown_open")

                # Step 2: Click "Captions" in the dropdown
                # Exact selector from Zoom DOM: a.dropdown-item[aria-label="Captions"]
                captions_clicked = False
                try:
                    cap_item = await self._page.query_selector('a[aria-label="Captions"]')
                    if not cap_item:
                        cap_item = await self._page.query_selector('a.dropdown-item[aria-label="Captions"]')
                    if cap_item:
                        await cap_item.click()
                        captions_clicked = True
                        self.logger.info("Clicked Captions menu item")
                        await asyncio.sleep(1)
                        await self._take_screenshot("zoom_step4_after_captions_click")
                    else:
                        await self._take_screenshot("zoom_step4_captions_NOT_found")
                        # Debug: dump dropdown items
                        try:
                            dropdown_debug = await self._page.evaluate("""() => {
                                const items = document.querySelectorAll('.dropdown-menu.show a, .dropdown-menu.show [role="button"]');
                                return Array.from(items).map(el => ({
                                    label: el.getAttribute('aria-label')?.substring(0, 80),
                                    text: el.textContent?.trim().substring(0, 60),
                                    cls: el.className?.substring(0, 60),
                                }));
                            }""")
                            self.logger.info(f"Dropdown items debug: {dropdown_debug}")
                        except Exception:
                            pass
                except Exception as e:
                    self.logger.debug(f"Captions menu item click failed: {e}")

                if captions_clicked:
                    # Step 3: Click "Show Captions" in submenu
                    try:
                        show_cap = await self._page.query_selector(
                            'a[aria-label="Your caption settings grouping Show Captions"]'
                        )
                        if show_cap:
                            await show_cap.click()
                            self.logger.info("Clicked Show Captions submenu item")
                            await asyncio.sleep(2)
                            await self._take_screenshot("zoom_step5_after_show_captions")
                        else:
                            await self._take_screenshot("zoom_step5_show_captions_NOT_found")
                    except Exception as e:
                        self.logger.debug(f"Show Captions click failed: {e}")

                    # Step 4: Handle language selection dialog (intermittent)
                    try:
                        lang_dialog = await self._page.query_selector('div.zm-modal.big-dialog.lt-select-language')
                        if lang_dialog:
                            await self._take_screenshot("zoom_step6_language_dialog")
                            save_btn = await lang_dialog.query_selector('button.zm-btn.zm-btn--primary')
                            if save_btn:
                                await save_btn.click()
                                self.logger.info("Dismissed language selection dialog")
                                await asyncio.sleep(1)
                                await self._take_screenshot("zoom_step6_after_language_dismiss")
                    except Exception as e:
                        self.logger.debug(f"Language dialog handling: {e}")

            # Verify captions enabled (wait up to 5s)
            # Check for __on-open ("You have turned on live transcription") or the subtitle box itself
            for _ in range(10):
                active = await self._page.evaluate(
                    "!!document.querySelector('.live-transcription-subtitle__on-open') || !!document.querySelector('#live-transcription-subtitle') || !!document.querySelector('.live-transcription-subtitle__box')"
                )
                if active:
                    self.logger.info("Zoom captions verified active")
                    break
                await asyncio.sleep(0.5)
            else:
                self.logger.warning("Zoom captions may not have been enabled (host setting required)")
            await self._take_screenshot("zoom_step7_captions_verification")

        # Inject caption observer with avatar matching
        await self._page.evaluate("""() => {
            if (window.__memoirCaptionObserver) return;
            window.__memoirCaption = { name: null, ts: 0 };
            window.__memoirAvatarMap = window.__memoirAvatarMap || {};

            const observer = new MutationObserver(() => {
                const subtitle = document.querySelector('#live-transcription-subtitle');
                if (!subtitle) return;
                const img = subtitle.querySelector('.zmu-data-selector-item__icon');
                if (!img) return;
                const src = img.getAttribute('src') || '';
                const uuidMatch = src.match(/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/);
                const uuid = uuidMatch ? uuidMatch[1] : src;
                const map = window.__memoirAvatarMap || {};
                if (map[uuid]) {
                    window.__memoirCaption = { name: map[uuid], ts: Date.now() };
                }
            });
            observer.observe(document.body, { childList: true, characterData: true, subtree: true });
            window.__memoirCaptionObserver = observer;
        }""")
        self.logger.info("Injected Zoom caption MutationObserver (avatar matching)")

    async def open_participant_panel(self):
        """Open the Zoom Participants panel and inject observer for name scraping + avatar mapping."""
        if not self._page:
            return

        self.logger.info("Opening Zoom Participants panel...")

        # Dismiss the hardware acceleration banner first
        await self._dismiss_zoom_popups()

        # Move mouse to toolbar area and click Participants via JS (fast, before toolbar hides)
        try:
            await self._page.mouse.move(960, 540)
            await asyncio.sleep(0.3)
            await self._page.mouse.move(960, 750)  # Move near toolbar
            await asyncio.sleep(0.5)
        except Exception:
            pass

        await self._take_screenshot("zoom_before_participants")

        # Use JavaScript click for speed — toolbar auto-hides quickly
        clicked = await self._page.evaluate("""() => {
            const selectors = [
                'button#participant',
                'button[feature-type="participant"]',
                'div#participant button',
                'button[aria-label*="participants" i]',
                'button[aria-label*="participant" i]',
            ];
            for (const sel of selectors) {
                const btn = document.querySelector(sel);
                if (btn) { btn.click(); return sel; }
            }
            return null;
        }""")
        if clicked:
            self.logger.info(f"Clicked Zoom Participants button via JS: {clicked}")
        else:
            # Fallback: try Playwright click with mouse move
            await self._page.mouse.move(960, 750)
            await asyncio.sleep(0.3)
            clicked = await self._try_click(
                [
                    'button#participant',
                    'button[feature-type="participant"]',
                    'button[aria-label*="participants" i]',
                ],
                "Zoom Participants button (fallback)",
            )
        if clicked:
            await self._take_screenshot("zoom_participants_after_click")
            # Wait for #participants-ul to appear (up to 5s)
            panel_opened = False
            for _ in range(10):
                exists = await self._page.evaluate("!!document.querySelector('#participants-ul')")
                if exists:
                    panel_opened = True
                    break
                await asyncio.sleep(0.5)
            if panel_opened:
                self.logger.info("Zoom Participants panel opened (#participants-ul found)")
                await self._take_screenshot("zoom_participants_panel_open")
            else:
                self.logger.warning("Zoom Participants panel: #participants-ul not found after click")
                await self._take_screenshot("zoom_participants_ul_NOT_found")
        else:
            self.logger.warning("Zoom Participants button not found")
            await self._take_screenshot("zoom_participants_btn_NOT_found")
            # Debug: dump toolbar buttons
            try:
                toolbar_debug = await self._page.evaluate("""() => {
                    const buttons = document.querySelectorAll('button');
                    return Array.from(buttons).slice(0, 30).map(b => ({
                        id: b.id, label: b.getAttribute('aria-label')?.substring(0, 60),
                        featureType: b.getAttribute('feature-type'),
                        classes: b.className.substring(0, 60)
                    }));
                }""")
                self.logger.info(f"Toolbar buttons debug: {toolbar_debug}")
            except Exception:
                pass

        # Inject MutationObserver for participant names + avatar map
        await self._page.evaluate("""() => {
            if (window.__memoirParticipantObserver) return;
            window.__memoirParticipants = [];
            window.__memoirAvatarMap = {};

            function scrapeParticipants() {
                const names = new Set();
                const avatarMap = {};

                document.querySelectorAll('.participants-li').forEach(el => {
                    // Parse name from aria-label (e.g. "Sathish Kumar (Host, me),audio,video")
                    const label = el.getAttribute('aria-label') || '';
                    const match = label.match(/^([^(,]+)/);
                    let name = '';
                    if (match) {
                        name = match[1].trim();
                        if (name && name.length < 100) names.add(name);
                    }
                    // Fallback: span.participants-item__name-section
                    if (!name) {
                        const nameSpan = el.querySelector('span.participants-item__name-section');
                        if (nameSpan) {
                            name = nameSpan.textContent?.trim() || '';
                            if (name && name.length < 100) names.add(name);
                        }
                    }
                    // Build avatar map: extract UUID from avatar img src
                    if (name) {
                        const img = el.querySelector('img.participants-item__avatar');
                        if (img) {
                            const src = img.getAttribute('src') || '';
                            const uuidMatch = src.match(/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/);
                            if (uuidMatch) {
                                avatarMap[uuidMatch[1]] = name;
                            }
                        }
                    }
                });

                window.__memoirParticipants = [...names];
                window.__memoirAvatarMap = avatarMap;
            }

            // Initial scrape
            scrapeParticipants();

            // Observe participants list for joins/leaves
            const target = document.querySelector('#participants-ul') || document.body;
            const observer = new MutationObserver(() => {
                scrapeParticipants();
            });
            observer.observe(target, { childList: true, subtree: true });
            window.__memoirParticipantObserver = observer;
        }""")
        self.logger.info("Injected Zoom participant panel observer + avatar map")

    # --- Private helpers ---

    async def _is_in_waiting_room(self) -> bool:
        """Check if the page is showing Zoom's waiting room or 'waiting for host' screen."""
        if not self._page:
            return False
        try:
            return await self._page.evaluate("""() => {
                const body = (document.body.innerText || '').toLowerCase();
                return (body.includes('please wait') &&
                        (body.includes('host will let you in') ||
                         body.includes('host to start'))) ||
                       body.includes('waiting for the host') ||
                       body.includes('waiting room');
            }""")
        except Exception:
            return False

    async def _mute_in_meeting(self):
        """Ensure microphone is muted after joining the meeting."""
        if not self._page:
            return
        try:
            # Move mouse to trigger toolbar
            await self._page.mouse.move(960, 540)
            await asyncio.sleep(1)

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
                        self.logger.info("Muted microphone in-meeting")
                    elif "unmute" in label:
                        self.logger.info("Microphone already muted in-meeting")
                    return
        except Exception as e:
            self.logger.warning(f"Failed to mute in-meeting: {e}")

    async def _handle_interstitial(self):
        """Handle Zoom's interstitial page that asks to open the desktop app."""
        # Check if we're on an interstitial (has "Join from Your Browser" link)
        for selector in [
            InterstitialSelectors.JOIN_FROM_BROWSER,
            InterstitialSelectors.JOIN_FROM_BROWSER_ALT,
            InterstitialSelectors.JOIN_FROM_BROWSER_ALT2,
        ]:
            try:
                link = await self._page.query_selector(selector)
                if link:
                    self.logger.info("Interstitial detected, clicking 'Join from Your Browser'")
                    await link.click()
                    await self._page.wait_for_load_state("networkidle", timeout=15000)
                    await self._random_delay()
                    await self._take_screenshot("zoom_after_interstitial")
                    return
            except Exception:
                continue

    async def _wait_for_prejoin_screen(self):
        """Wait for the Zoom pre-join screen (name input or join button)."""
        self.logger.info("Waiting for Zoom pre-join screen...")
        prejoin_selectors = [
            PreJoinSelectors.NAME_INPUT,
            PreJoinSelectors.NAME_INPUT_ALT,
            PreJoinSelectors.NAME_INPUT_ALT2,
            PreJoinSelectors.NAME_INPUT_ALT3,
            PreJoinSelectors.JOIN_BUTTON,
            PreJoinSelectors.JOIN_BUTTON_ALT,
            PreJoinSelectors.JOIN_BUTTON_ALT3,
            PreJoinSelectors.JOIN_BUTTON_ALT4,
        ]
        error_selectors = [
            ErrorSelectors.INVALID_MEETING,
            ErrorSelectors.INVALID_ALT,
            ErrorSelectors.INVALID_ALT2,
            ErrorSelectors.MEETING_FULL,
        ]
        deadline = asyncio.get_event_loop().time() + 30
        while asyncio.get_event_loop().time() < deadline:
            for sel in error_selectors:
                el = await self._page.query_selector(sel)
                if el:
                    await self._take_screenshot("zoom_error")
                    raise MeetingDeniedError(f"Zoom error: {sel}")
            for sel in prejoin_selectors:
                el = await self._page.query_selector(sel)
                if el:
                    self.logger.info("Zoom pre-join screen loaded")
                    return
            await asyncio.sleep(1)
        self.logger.warning("Zoom pre-join screen wait timed out")
        await self._take_screenshot("zoom_prejoin_timeout")

    async def _accept_tos(self):
        """Accept Terms of Service if prompted."""
        await self._try_click(
            [PreJoinSelectors.AGREE_BUTTON, PreJoinSelectors.AGREE_BUTTON_ALT],
            "Zoom ToS agree button",
        )

    async def _dismiss_popups(self):
        """Dismiss cookie banners and dialogs."""
        await self._try_click(
            [PreJoinSelectors.COOKIE_ACCEPT, PreJoinSelectors.COOKIE_ACCEPT_ALT],
            "cookie banner",
        )

    async def _enter_name(self):
        """Enter the bot name in the Zoom name input."""
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
                        self.logger.info(f"Entered bot name: '{self.bot_name}' via {selector}")
                        await self._random_delay()
                        return
                except Exception:
                    continue
            await asyncio.sleep(0.5)

        # JS fallback: find input near "Your Name" label
        try:
            filled = await self._page.evaluate("""(name) => {
                // Try by label text
                const labels = document.querySelectorAll('label, span, div');
                for (const el of labels) {
                    if ((el.textContent || '').toLowerCase().includes('your name')) {
                        const input = el.closest('div')?.querySelector('input') ||
                                      el.parentElement?.querySelector('input');
                        if (input) {
                            input.value = name;
                            input.dispatchEvent(new Event('input', { bubbles: true }));
                            input.dispatchEvent(new Event('change', { bubbles: true }));
                            return true;
                        }
                    }
                }
                // Try any visible text input
                const inputs = document.querySelectorAll('input[type="text"], input:not([type])');
                for (const input of inputs) {
                    if (input.offsetParent !== null) {
                        input.value = name;
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    }
                }
                return false;
            }""", self.bot_name)
            if filled:
                self.logger.info(f"Entered bot name via JS fallback: '{self.bot_name}'")
                return
        except Exception as e:
            self.logger.debug(f"JS name input fallback failed: {e}")

        self.logger.warning("Zoom name input not found — continuing without name")

    async def _disable_media(self):
        """Turn off microphone and camera on the pre-join screen."""
        await asyncio.sleep(1)

        for device, selectors in [
            ("microphone", [PreJoinSelectors.MIC_TOGGLE, PreJoinSelectors.MIC_TOGGLE_ALT]),
            ("camera", [PreJoinSelectors.CAMERA_TOGGLE, PreJoinSelectors.CAMERA_TOGGLE_ALT]),
        ]:
            found = False
            for selector in selectors:
                try:
                    btn = await self._page.query_selector(selector)
                    if btn:
                        found = True
                        label = (await btn.get_attribute("aria-label") or "").lower()
                        self.logger.debug(f"Pre-join {device} button label: '{label}'")
                        # Already off
                        if "unmute" in label or "start video" in label or "turn on" in label:
                            self.logger.info(f"{device} already disabled on pre-join")
                            break
                        # Currently on — click to disable
                        if ("mute" in label or "stop" in label or
                                "turn off" in label or "disable" in label):
                            await btn.click()
                            self.logger.info(f"Disabled {device} on pre-join")
                            break
                        # Ambiguous — click anyway to be safe
                        await btn.click()
                        self.logger.info(f"Toggled {device} on pre-join (label: '{label}')")
                        break
                except Exception:
                    continue
            if not found:
                self.logger.warning(f"Pre-join {device} toggle button not found")

    async def _click_join(self):
        """Click the Join button."""
        selectors = [
            PreJoinSelectors.JOIN_BUTTON,
            PreJoinSelectors.JOIN_BUTTON_ALT,
            PreJoinSelectors.JOIN_BUTTON_ALT2,
            PreJoinSelectors.JOIN_BUTTON_ALT3,
            PreJoinSelectors.JOIN_BUTTON_ALT4,
            PreJoinSelectors.JOIN_BUTTON_ALT5,
            PreJoinSelectors.JOIN_BUTTON_ALT6,
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
                            log_phase("pre_join_done", platform="zoom")
                        except Exception:
                            pass
                        await btn.click()
                        self.logger.info(f"Clicked Zoom join button: {selector}")
                        return
                except Exception:
                    continue
            await asyncio.sleep(0.5)

        # JS fallback: click any element with "Join" text that looks like a button
        try:
            clicked = await self._page.evaluate("""() => {
                // Try buttons and inputs first
                const clickables = document.querySelectorAll('button, input[type="button"], input[type="submit"], [role="button"], a');
                for (const el of clickables) {
                    const text = (el.textContent || el.value || '').trim();
                    if (text === 'Join' || text === 'Join Meeting') {
                        el.click();
                        return text;
                    }
                }
                return null;
            }""")
            if clicked:
                self.logger.info(f"Clicked Zoom join button via JS fallback: '{clicked}'")
                return
        except Exception as e:
            self.logger.debug(f"JS join button fallback failed: {e}")

        await self._take_screenshot("zoom_join_button_not_found")
        raise BrowserError("Zoom join button not found")

    async def _wait_for_admission(self) -> bool:
        """Wait to be admitted to the Zoom meeting.

        Uses three layers of detection:
        1. CSS selectors — in-meeting-only toolbar buttons (NOT mic/camera — they exist on pre-join)
        2. PulseAudio sink-input — only trusted after waiting room confirmed
        3. JS fallback — waiting room text gone + toolbar buttons present

        A 5s grace period prevents false positives from pre-join elements.
        """
        self.logger.info(f"Waiting for Zoom admission (timeout: {settings.WAITING_ROOM_TIMEOUT_SECONDS}s)...")

        # Tier 1.6 (Zoom variant): auto-admit early-exit probe. Before
        # the 5 s GRACE_PERIOD, do a quick 1.5 s probe to detect
        # already-admitted meetings (no waiting room — host pre-approved
        # or open-access). Requires 2 consecutive positive LEAVE_BUTTON
        # hits 100 ms apart to avoid pre-join false positives. LEAVE
        # button is the strongest signal because it doesn't exist on the
        # Zoom pre-join or waiting-room UI. On hit, return immediately,
        # skipping both the 5 s grace and the main admission loop —
        # saves ~5-8 s.
        early_admitted_selectors = [
            InMeetingSelectors.LEAVE_BUTTON,
            InMeetingSelectors.LEAVE_BUTTON_ALT,
            InMeetingSelectors.LEAVE_BUTTON_ALT2,
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
                        "Tier 1.6: Zoom auto-admit detected via early-probe — "
                        "skipping 5 s grace period"
                    )
                    try:
                        from app.services.phase_log import log_phase
                        log_phase("admission_detected", platform="zoom",
                                  path="auto_admit_early")
                    except Exception:
                        pass
                    return True
            else:
                consecutive_hits = 0
            await asyncio.sleep(EARLY_PROBE_INTERVAL)

        # NOTE: MIC_IN_MEETING / CAMERA_IN_MEETING deliberately excluded —
        # their aria-labels ("Mute"/"Unmute"/"Start Video"/"Stop Video") also
        # match the pre-join media toggles, causing instant false-positive admission.
        admitted_selectors = [
            InMeetingSelectors.PARTICIPANTS_BUTTON,
            InMeetingSelectors.CHAT_BUTTON,
            InMeetingSelectors.SHARE_SCREEN_BUTTON,
            InMeetingSelectors.REACTIONS_BUTTON,
            InMeetingSelectors.LEAVE_BUTTON,
            InMeetingSelectors.LEAVE_BUTTON_ALT,
            InMeetingSelectors.LEAVE_BUTTON_ALT2,
        ]
        denied_selectors = [
            ErrorSelectors.INVALID_MEETING,
            ErrorSelectors.MEETING_FULL,
            ErrorSelectors.MEETING_FULL_ALT,
            ErrorSelectors.DENIED,
            ErrorSelectors.DENIED_ALT,
            ErrorSelectors.DENIED_REMOVED,
        ]
        deadline = asyncio.get_event_loop().time() + settings.WAITING_ROOM_TIMEOUT_SECONDS
        start_time = asyncio.get_event_loop().time()
        screenshot_taken = False
        audio_confirmed_at = None
        waiting_room_seen = False
        waiting_room_event_sent = False
        GRACE_PERIOD = 5  # seconds before checking admission signals

        while asyncio.get_event_loop().time() < deadline:
            elapsed = asyncio.get_event_loop().time() - start_time

            # Check for errors/denial (always active)
            for sel in denied_selectors:
                el = await self._page.query_selector(sel)
                if el:
                    await self._take_screenshot("zoom_denied")
                    raise MeetingDeniedError("Zoom meeting denied or full")

            # Detect waiting room state (always active)
            if not waiting_room_seen and await self._is_in_waiting_room():
                waiting_room_seen = True
                self.logger.info("In Zoom waiting room — waiting for host to admit...")
                await self._take_screenshot("zoom_in_waiting_room")
                if self._event_sender and not waiting_room_event_sent:
                    await self._event_sender.send_event(EventType.WAITING_ROOM_ENTERED)
                    waiting_room_event_sent = True

            # During grace period: only check for denial + waiting room, skip admission checks.
            if elapsed < GRACE_PERIOD and not waiting_room_seen:
                await asyncio.sleep(1)
                continue

            # Layer 1: CSS selectors — in-meeting-only toolbar buttons
            for sel in admitted_selectors:
                el = await self._page.query_selector(sel)
                if el:
                    self.logger.info(f"Zoom admission detected via selector: {sel}")
                    try:
                        from app.services.phase_log import log_phase
                        log_phase("admission_detected", platform="zoom",
                                  path=("waiting_room" if waiting_room_seen else "auto_admit"))
                    except Exception:
                        pass
                    return True

            # Layer 2: PulseAudio — only trust after waiting room confirmed
            if waiting_room_seen:
                if await self._is_audio_streaming():
                    if audio_confirmed_at is None:
                        audio_confirmed_at = asyncio.get_event_loop().time()
                        self.logger.info("Audio stream detected, confirming...")
                    elif asyncio.get_event_loop().time() - audio_confirmed_at >= 2:
                        self.logger.info("Zoom admission detected via PulseAudio")
                        try:
                            from app.services.phase_log import log_phase
                            log_phase("admission_detected", platform="zoom",
                                      path="pulseaudio")
                        except Exception:
                            pass
                        return True
                else:
                    audio_confirmed_at = None

            # Layer 3: JS fallback
            try:
                in_meeting = await self._page.evaluate("""() => {
                    const body = (document.body.innerText || '').toLowerCase();
                    if (body.includes('waiting for the host') ||
                        body.includes('please wait') ||
                        body.includes('waiting room'))
                        return false;
                    // Look for in-meeting-only toolbar buttons
                    const buttons = document.querySelectorAll('button[aria-label]');
                    let toolbarCount = 0;
                    for (const btn of buttons) {
                        const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                        if (label.includes('share') || label.includes('participant') ||
                            label.includes('chat') || label.includes('leave') ||
                            label.includes('react') || label.includes('record') ||
                            label.includes('show everyone'))
                            toolbarCount++;
                    }
                    return toolbarCount >= 3;
                }""")
                if in_meeting:
                    self.logger.info("Zoom admission detected via JS toolbar fallback")
                    try:
                        from app.services.phase_log import log_phase
                        log_phase("admission_detected", platform="zoom",
                                  path="js_fallback")
                    except Exception:
                        pass
                    return True
            except Exception as e:
                self.logger.debug(f"JS admission check failed: {e}")

            # Diagnostic screenshot
            if not screenshot_taken and elapsed > settings.WAITING_ROOM_TIMEOUT_SECONDS / 2:
                await self._take_screenshot("zoom_admission_waiting")
                screenshot_taken = True

            await asyncio.sleep(1)

        await self._take_screenshot("zoom_admission_timeout")
        raise BrowserError("Zoom admission timeout — host did not admit the bot")
