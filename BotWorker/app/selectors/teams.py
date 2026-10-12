"""
Centralized CSS/aria selectors for Microsoft Teams web client automation.
All selectors in one file for easy maintenance when Teams updates their UI.

Teams URLs:
  - https://teams.microsoft.com/l/meetup-join/...
  - https://teams.live.com/meet/...
"""


class InterstitialSelectors:
    """Selectors for the Teams app launch interstitial page."""

    # "Continue on this browser" / "Join on the web instead"
    CONTINUE_ON_BROWSER = 'button:has-text("Continue on this browser")'
    CONTINUE_ON_BROWSER_ALT = 'a:has-text("Continue on this browser")'
    JOIN_ON_WEB = 'button:has-text("Join on the web instead")'
    JOIN_ON_WEB_ALT = 'a:has-text("Join on the web instead")'
    JOIN_ON_WEB_ALT2 = 'a[data-tid="joinOnWeb"]'
    # Sometimes Teams shows "Use web app instead"
    USE_WEB_APP = 'button:has-text("Use web app instead")'
    USE_WEB_APP_ALT = 'a:has-text("Use web app instead")'

    # "Cancel" the app launch prompt
    CANCEL_APP_LAUNCH = 'button:has-text("Cancel")'


class PreJoinSelectors:
    """Selectors for the Teams pre-join screen."""

    # Guest name input (no sign-in)
    NAME_INPUT = 'input#username'
    NAME_INPUT_ALT = 'input[data-tid="prejoin-display-name"]'
    NAME_INPUT_ALT2 = 'input[placeholder*="name" i]'
    NAME_INPUT_ALT3 = 'input[aria-label*="name" i]'

    # "Join as a guest" button (appears before name input on some flows)
    JOIN_AS_GUEST = 'button:has-text("Join as a guest")'
    JOIN_AS_GUEST_ALT = 'a:has-text("Join as a guest")'

    # Media toggles (pre-join)
    MIC_TOGGLE = 'button[aria-label*="microphone" i]'
    MIC_TOGGLE_ALT = 'button[data-tid="toggle-mute"]'
    MIC_TOGGLE_ALT2 = 'button[aria-label*="Mic" i]'
    CAMERA_TOGGLE = 'button[aria-label*="camera" i]'
    CAMERA_TOGGLE_ALT = 'button[data-tid="toggle-video"]'
    CAMERA_TOGGLE_ALT2 = 'button[aria-label*="Video" i]'

    # Join button
    JOIN_BUTTON = 'button[data-tid="prejoin-join-button"]'
    JOIN_BUTTON_ALT = 'button:has-text("Join now")'
    JOIN_BUTTON_ALT2 = 'button:has-text("Join meeting")'

    # "Continue without audio or video" dialog (browser media permission denial)
    CONTINUE_NO_MEDIA = 'button:has-text("Continue without audio or video")'
    CONTINUE_NO_MEDIA_ALT = 'button:has-text("Continue without audio")'

    # Lobby / waiting room
    LOBBY_TEXT = 'text="Someone in the meeting should let you in soon"'
    LOBBY_ALT = ':has-text("let you in soon")'
    LOBBY_ALT2 = '[data-tid="lobby-screen"]'
    LOBBY_ALT3 = ':has-text("waiting to be let in")'
    LOBBY_ALT4 = ':has-text("will let you in when the meeting starts")'
    LOBBY_ALT5 = ':has-text("Someone will let you in")'


class InMeetingSelectors:
    """Selectors for in-meeting controls and state detection."""

    # Hangup / leave button
    HANGUP_BUTTON = '#hangup-button'
    HANGUP_BUTTON_ALT = 'button[data-tid="hangup-btn"]'
    HANGUP_BUTTON_ALT2 = 'button[aria-label*="hang up" i]'
    HANGUP_BUTTON_ALT3 = 'button[aria-label*="Leave" i]'

    # Meeting ended indicators
    MEETING_ENDED_TEXT = 'text="You left the meeting"'
    MEETING_ENDED_ALT = ':has-text("meeting has ended")'
    MEETING_ENDED_ALT2 = 'text="The meeting has ended"'
    MEETING_ENDED_ALT3 = ':has-text("You left the meeting")'

    # Removed from meeting
    REMOVED_TEXT = ':has-text("removed you from the meeting")'
    REMOVED_ALT = ':has-text("been removed from")'

    # In-meeting toolbar buttons (admission indicators)
    MIC_IN_MEETING = 'button[aria-label*="Mute" i]'
    MIC_IN_MEETING_ALT = 'button[aria-label*="Unmute" i]'
    CAMERA_IN_MEETING = 'button[aria-label*="Turn camera on" i]'
    CAMERA_IN_MEETING_ALT = 'button[aria-label*="Turn camera off" i]'
    CHAT_BUTTON = 'button[aria-label*="Chat" i]'
    CHAT_BUTTON_ALT = 'button[data-tid="chat-button"]'
    PARTICIPANTS_BUTTON = 'button[aria-label*="People" i]'
    PARTICIPANTS_BUTTON_ALT = 'button[data-tid="roster-button"]'
    SHARE_BUTTON = 'button[aria-label*="Share" i]'
    REACTIONS_BUTTON = 'button[aria-label*="React" i]'
    MORE_BUTTON = 'button[aria-label*="More" i]'

    # Popups / notifications
    CLOSE_POPUP = 'button[aria-label="Close"]'
    DISMISS_BUTTON = 'button:has-text("Dismiss")'
    GOT_IT_BUTTON = 'button:has-text("Got it")'


class ErrorSelectors:
    """Selectors for Teams error states."""

    # Meeting not found
    NOT_FOUND = ':has-text("couldn\'t find this meeting")'
    NOT_FOUND_ALT = ':has-text("meeting doesn\'t exist")'

    # Sign-in required (org policy blocks guest join)
    SIGN_IN_REQUIRED = ':has-text("sign in to join")'
    SIGN_IN_REQUIRED_ALT = ':has-text("Sign in to join this meeting")'

    # Meeting expired
    EXPIRED = ':has-text("meeting link has expired")'
    EXPIRED_ALT = ':has-text("meeting invitation has expired")'

    # Denied by organizer
    DENIED = ':has-text("didn\'t let you in")'
    DENIED_ALT = ':has-text("denied your request")'
    DENIED_ENTRY = ':has-text("denied entry")'
    DENIED_CANT_JOIN = ':has-text("can\'t join this meeting")'
