"""
Centralized CSS/aria selectors for Zoom web client automation.
All selectors in one file for easy maintenance when Zoom updates their UI.

Zoom web client URL: https://app.zoom.us/wc/join/<meeting-id>?pwd=<password>
"""


class InterstitialSelectors:
    """Selectors for the Zoom launch/interstitial page (zoom.us/j/...)."""

    # "Join from Your Browser" link on the interstitial page
    JOIN_FROM_BROWSER = 'a:has-text("Join from Your Browser")'
    JOIN_FROM_BROWSER_ALT = 'a:has-text("join from your browser")'
    JOIN_FROM_BROWSER_ALT2 = '#joinFromBrowser'
    LAUNCH_MEETING_LINK = 'a:has-text("Launch Meeting")'


class PreJoinSelectors:
    """Selectors for the Zoom web client pre-join screen."""

    # Terms of Service agreement (first-time)
    AGREE_BUTTON = 'button:has-text("I Agree")'
    AGREE_BUTTON_ALT = 'button:has-text("Agree")'

    # Cookie / privacy banners
    COOKIE_ACCEPT = 'button:has-text("Accept")'
    COOKIE_ACCEPT_ALT = 'button#onetrust-accept-btn-handler'

    # Name input
    NAME_INPUT = '#inputname'
    NAME_INPUT_ALT = 'input[placeholder*="name" i]'
    NAME_INPUT_ALT2 = 'input[aria-label*="name" i]'
    NAME_INPUT_ALT3 = 'input[type="text"]'  # fallback: first text input on page

    # Passcode input (if meeting requires passcode)
    PASSCODE_INPUT = '#inputpasscode'
    PASSCODE_INPUT_ALT = 'input[placeholder*="passcode" i]'

    # Join button
    JOIN_BUTTON = 'button.preview-join-button'
    JOIN_BUTTON_ALT = 'button:has-text("Join")'
    JOIN_BUTTON_ALT2 = 'button[id="joinBtn"]'
    JOIN_BUTTON_ALT3 = '#joinBtn'  # might not be a <button>
    JOIN_BUTTON_ALT4 = '[role="button"]:has-text("Join")'
    JOIN_BUTTON_ALT5 = 'input[type="button"][value*="Join" i]'
    JOIN_BUTTON_ALT6 = 'input[type="submit"][value*="Join" i]'

    # Media toggles (pre-join)
    MIC_TOGGLE = 'button[aria-label*="mute" i]'
    MIC_TOGGLE_ALT = 'button[aria-label*="microphone" i]'
    CAMERA_TOGGLE = 'button[aria-label*="video" i]'
    CAMERA_TOGGLE_ALT = 'button[aria-label*="camera" i]'

    # Waiting room
    WAITING_ROOM_TEXT = 'text="Please wait, the meeting host will let you in soon"'
    WAITING_ROOM_ALT = 'text="Please wait for the host to start this meeting"'
    WAITING_ROOM_ALT2 = ':has-text("waiting for the host")'


class InMeetingSelectors:
    """Selectors for in-meeting controls and state detection."""

    # Leave button
    LEAVE_BUTTON = 'button[aria-label="Leave"]'
    LEAVE_BUTTON_ALT = 'button:has-text("Leave")'
    LEAVE_BUTTON_ALT2 = 'button.footer__leave-btn'

    # Meeting ended indicators
    MEETING_ENDED_TEXT = 'text="This meeting has been ended by the host"'
    MEETING_ENDED_ALT = ':has-text("meeting has been ended")'
    MEETING_ENDED_ALT2 = ':has-text("meeting has ended")'

    # Removed from meeting
    REMOVED_TEXT = 'text="The host has removed you from this meeting"'
    REMOVED_ALT = ':has-text("host has removed you")'
    REMOVED_ALT2 = ':has-text("been removed from")'

    # In-meeting toolbar buttons (admission indicators)
    PARTICIPANTS_BUTTON = 'button[aria-label*="Participants" i]'
    PARTICIPANTS_BUTTON_ALT = 'button[aria-label*="participant" i]'
    CHAT_BUTTON = 'button[aria-label*="Chat" i]'
    SHARE_SCREEN_BUTTON = 'button[aria-label*="Share Screen" i]'
    REACTIONS_BUTTON = 'button[aria-label*="React" i]'
    MIC_IN_MEETING = 'button[aria-label*="Mute" i]'
    MIC_IN_MEETING_ALT = 'button[aria-label*="Unmute" i]'
    CAMERA_IN_MEETING = 'button[aria-label*="Stop Video" i]'
    CAMERA_IN_MEETING_ALT = 'button[aria-label*="Start Video" i]'

    # Popups
    CLOSE_POPUP = 'button[aria-label="Close"]'
    DISMISS_DIALOG = 'button:has-text("Got it")'
    DISMISS_DIALOG_ALT = 'button:has-text("OK")'


class ErrorSelectors:
    """Selectors for Zoom error states."""

    # Invalid meeting
    INVALID_MEETING = 'text="This meeting ID is not valid"'
    INVALID_ALT = ':has-text("meeting does not exist")'
    INVALID_ALT2 = ':has-text("Invalid meeting ID")'

    # Meeting not started
    NOT_STARTED = 'text="This meeting has not been started"'
    NOT_STARTED_ALT = ':has-text("waiting for the host to start")'

    # Passcode required
    PASSCODE_REQUIRED = ':has-text("Please enter your meeting passcode")'

    # Full meeting
    MEETING_FULL = ':has-text("This meeting is full")'
    MEETING_FULL_ALT = ':has-text("meeting has reached")'

    # Host denied entry
    DENIED = ':has-text("host has denied")'
    DENIED_ALT = ':has-text("denied your request")'
    DENIED_REMOVED = ':has-text("host has removed you")'
