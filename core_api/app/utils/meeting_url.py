"""
Meeting URL normalization and ID extraction utilities.

Used by webhook handlers to match incoming meeting events to calendar events
by extracting platform-specific meeting IDs from URLs.
"""

import re
from urllib.parse import urlparse, urlencode, parse_qs


# Zoom: zoom.us/j/12345678 or zoom.us/wc/12345678
_ZOOM_ID_RE = re.compile(r"zoom\.us/(?:j|wc)/(\d+)")

# Google Meet: meet.google.com/abc-defg-hij
_MEET_CODE_RE = re.compile(r"meet\.google\.com/([a-z]{3}-[a-z]{4}-[a-z]{3})", re.IGNORECASE)

# Teams: teams.microsoft.com/l/meetup-join/...
_TEAMS_JOIN_RE = re.compile(
    r"https?://teams\.microsoft\.com/l/meetup-join/([^\s\"<]+)", re.IGNORECASE
)


def extract_zoom_meeting_id(url: str) -> str | None:
    """Extract numeric Zoom meeting ID from URL (e.g. zoom.us/j/123456)."""
    m = _ZOOM_ID_RE.search(url)
    return m.group(1) if m else None


def extract_meet_code(url: str) -> str | None:
    """Extract Google Meet code from URL (e.g. meet.google.com/abc-defg-hij)."""
    m = _MEET_CODE_RE.search(url)
    return m.group(1).lower() if m else None


def extract_teams_join_url(url: str) -> str | None:
    """Extract the normalized Teams join URL path for matching."""
    m = _TEAMS_JOIN_RE.search(url)
    if not m:
        return None
    # Return the path portion (after /l/meetup-join/) for comparison
    return m.group(1).split("?")[0].lower()


def normalize_meeting_url(url: str) -> str:
    """Normalize meeting URL for comparison — strip query params, lowercase."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".lower().rstrip("/")
