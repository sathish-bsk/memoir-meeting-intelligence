"""
Centralized CSS/aria selectors for Google Meet automation.
All selectors in one file for easy maintenance when Google updates their UI.
"""


class PreJoinSelectors:
    """Selectors for the pre-join / lobby screen."""

    # Cookie consent banner
    COOKIE_ACCEPT_BUTTON = 'button:has-text("Accept all")'
    COOKIE_REJECT_BUTTON = 'button:has-text("Reject all")'

    # Got it / dismiss dialogs
    GOT_IT_BUTTON = 'button:has-text("Got it")'
    DISMISS_BUTTON = 'button:has-text("Dismiss")'

    # Name input
    NAME_INPUT = 'input[aria-label="Your name"]'
    NAME_INPUT_ALT = 'input[placeholder="Your name"]'

    # Media toggles (pre-join)
    MIC_TOGGLE = 'button[aria-label*="microphone" i]'
    MIC_TOGGLE_ALT = 'button[data-is-muted]'
    CAMERA_TOGGLE = 'button[aria-label*="camera" i]'
    CAMERA_TOGGLE_ALT = 'button[aria-label*="video" i]'
    CAMERA_TOGGLE_ALT2 = 'button[aria-label*="Turn off camera" i]'
    CAMERA_TOGGLE_ALT3 = 'button[aria-label*="Turn on camera" i]'

    # Join buttons
    JOIN_NOW_BUTTON = 'button:has-text("Join now")'
    ASK_TO_JOIN_BUTTON = 'button:has-text("Ask to join")'
    JOIN_BUTTON_ALT = 'button[jsname="Qx7uuf"]'

    # Waiting room
    WAITING_ROOM_TEXT = 'text="Waiting for someone to let you in"'
    WAITING_ROOM_ALT = 'text="Ask someone in the meeting to let you in"'


class InMeetingSelectors:
    """Selectors for in-meeting controls and state detection."""

    # Leave call button (indicates we're in the meeting)
    LEAVE_CALL_BUTTON = 'button[aria-label="Leave call"]'
    LEAVE_CALL_ALT = 'button[aria-label="Leave the call"]'

    # Meeting ended indicators
    MEETING_ENDED_TEXT = 'text="You left the meeting"'
    MEETING_ENDED_ALT = 'text="The meeting has ended"'
    REMOVED_TEXT = 'text="You\'ve been removed from the meeting"'
    REMOVED_ALT = 'text="You were removed from this meeting"'

    # Return to home after leaving
    RETURN_HOME_BUTTON = 'a:has-text("Return to home screen")'
    REJOIN_BUTTON = 'button:has-text("Rejoin")'

    # Participant count / people panel
    PEOPLE_BUTTON = 'button[aria-label*="Show everyone"]'
    PEOPLE_BUTTON_ALT = 'button[aria-label*="people"]'
    PEOPLE_BUTTON_ALT2 = 'button[aria-label*="participant"]'

    # Additional in-meeting indicators (admission detection fallbacks)
    # These elements only appear once truly inside the meeting, not in waiting room
    CAPTION_BUTTON = 'button[aria-label*="captions"]'
    CAPTION_BUTTON_ALT = 'button[aria-label*="subtitle"]'
    MORE_OPTIONS_BUTTON = 'button[aria-label*="More options"]'
    ACTIVITIES_BUTTON = 'button[aria-label*="Activities"]'
    MIC_IN_MEETING = 'button[aria-label*="Turn off microphone"]'
    MIC_IN_MEETING_ALT = 'button[aria-label*="Turn on microphone"]'
    MEETING_INFO = 'button[aria-label*="Meeting details"]'
    MEETING_INFO_ALT = 'button[aria-label*="meeting info"]'

    # In-meeting popups / notifications
    CLOSE_POPUP = 'button[aria-label="Close"]'
    DISMISS_NOTIFICATION = 'button:has-text("Dismiss")'


class ErrorSelectors:
    """Selectors for error states."""

    # Denied entry (use :has-text for partial matching — Google changes wording)
    DENIED_TEXT = ':has-text("can\'t join this")'
    DENIED_ALT = ':has-text("request to join was denied")'
    NOT_ALLOWED_TEXT = ':has-text("aren\'t allowed to join")'
    DENIED_ENTRY = ':has-text("denied entry")'
    DENIED_BY_HOST = ':has-text("denied your request")'

    # Meeting not found
    NOT_FOUND_TEXT = 'text="Check your meeting code"'
    INVALID_MEETING = 'text="This meeting doesn\'t exist"'
    INVALID_ALT = 'text="Invalid video call name"'
