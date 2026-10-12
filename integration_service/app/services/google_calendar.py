"""
Google Calendar OAuth + event fetching service.
Handles token encryption, refresh, and Calendar API calls.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.core.config import settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/meetings.space.readonly",
    "openid",
]

# Regex patterns for meeting URLs
MEETING_URL_PATTERNS = [
    re.compile(r"https?://meet\.google\.com/[a-z\-]+", re.IGNORECASE),
    re.compile(r"https?://[\w.-]*zoom\.us/j/\d+[^\s]*", re.IGNORECASE),
    re.compile(r"https?://teams\.microsoft\.com/l/meetup-join/[^\s]+", re.IGNORECASE),
]


class GoogleCalendarService:
    """Manages Google Calendar OAuth flow and event fetching."""

    def __init__(self):
        self._fernet: Optional[Fernet] = None

    @property
    def fernet(self) -> Fernet:
        if self._fernet is None:
            key = settings.CALENDAR_ENCRYPTION_KEY
            if not key:
                raise ValueError("CALENDAR_ENCRYPTION_KEY not configured")
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        return self._fernet

    def _create_flow(self, redirect_uri: str) -> Flow:
        """Create an OAuth2 flow for Google Calendar."""
        client_config = {
            "web": {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        flow = Flow.from_client_config(client_config, scopes=SCOPES)
        flow.redirect_uri = redirect_uri
        return flow

    def get_auth_url(self, redirect_uri: str, state: str) -> str:
        """Generate Google OAuth consent URL (without PKCE)."""
        import urllib.parse
        params = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"https://accounts.google.com/o/oauth2/auth?{urllib.parse.urlencode(params)}"

    def exchange_code_for_tokens(
        self, code: str, redirect_uri: str
    ) -> tuple[str, str]:
        """Exchange authorization code for tokens via direct HTTP POST.

        Returns (encrypted_tokens, provider_email).
        """
        import httpx

        # Token exchange — no PKCE code_verifier needed for confidential clients
        token_resp = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        token_resp.raise_for_status()
        tokens = token_resp.json()

        token_data = {
            "token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token"),
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "scopes": SCOPES,
        }
        if "expires_in" in tokens:
            from datetime import datetime, timezone
            expiry = datetime.now(timezone.utc) + timedelta(seconds=tokens["expires_in"])
            token_data["expiry"] = expiry.isoformat()

        encrypted = self.fernet.encrypt(json.dumps(token_data).encode()).decode()

        # Get the user's email from Google's userinfo endpoint
        resp = httpx.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        provider_email = resp.json().get("email", "unknown") if resp.status_code == 200 else "unknown"

        return encrypted, provider_email

    def decrypt_tokens(self, encrypted_tokens: str) -> Credentials:
        """Decrypt stored tokens into Google Credentials."""
        token_json = json.loads(self.fernet.decrypt(encrypted_tokens.encode()))
        expiry = None
        if token_json.get("expiry"):
            expiry = datetime.fromisoformat(token_json["expiry"])
            if expiry.tzinfo:
                expiry = expiry.replace(tzinfo=None)

        return Credentials(
            token=token_json["token"],
            refresh_token=token_json.get("refresh_token"),
            token_uri=token_json.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_json.get("client_id", settings.GOOGLE_CLIENT_ID),
            client_secret=token_json.get("client_secret", settings.GOOGLE_CLIENT_SECRET),
            scopes=token_json.get("scopes", SCOPES),
            expiry=expiry,
        )

    def refresh_if_needed(self, encrypted_tokens: str) -> tuple[Credentials, Optional[str]]:
        """Check if credentials need refresh; refresh and return new encrypted tokens if so.

        Returns (credentials, new_encrypted_tokens_or_None).

        Raises TokenInvalidError when Google rejects the refresh_token (e.g.
        invalid_grant after 7 days of inactivity in a Testing-status OAuth app,
        user-side revocation, or client_secret rotation). The caller is
        expected to flip calendar_connections.token_invalid=true and surface
        a 401 to the frontend so the user can re-authenticate.
        """
        credentials = self.decrypt_tokens(encrypted_tokens)

        if credentials.expired and credentials.refresh_token:
            from google.auth.transport.requests import Request
            from google.auth.exceptions import RefreshError
            from .oauth_errors import TokenInvalidError

            try:
                credentials.refresh(Request())
            except RefreshError as exc:
                # google-auth packs the error_description into args; check both
                # the string form and the dict payload for "invalid_grant".
                msg = str(exc).lower()
                if "invalid_grant" in msg or "token has been expired or revoked" in msg:
                    raise TokenInvalidError("google", reason="invalid_grant") from exc
                raise

            token_data = {
                "token": credentials.token,
                "refresh_token": credentials.refresh_token,
                "token_uri": credentials.token_uri,
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
                "scopes": list(credentials.scopes or SCOPES),
            }
            if credentials.expiry:
                token_data["expiry"] = credentials.expiry.isoformat()

            new_encrypted = self.fernet.encrypt(json.dumps(token_data).encode()).decode()
            return credentials, new_encrypted

        return credentials, None

    def fetch_events(self, encrypted_tokens: str, days_ahead: int = 7) -> list[dict]:
        """Fetch upcoming calendar events for the next N days."""
        credentials, _ = self.refresh_if_needed(encrypted_tokens)
        service = build("calendar", "v3", credentials=credentials)

        now = datetime.now(timezone.utc)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=days_ahead)).isoformat()

        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            )
            .execute()
        )

        events = []
        for event in events_result.get("items", []):
            start = event.get("start", {})
            end = event.get("end", {})

            # Skip all-day events (they have 'date' instead of 'dateTime')
            start_dt = start.get("dateTime")
            end_dt = end.get("dateTime")
            if not start_dt or not end_dt:
                continue

            meeting_url = self._extract_meeting_url(event)
            attendees = self._extract_attendees(event)

            events.append({
                "provider_event_id": event["id"],
                "title": event.get("summary", "Untitled"),
                "description": event.get("description"),
                "start_time": start_dt,
                "end_time": end_dt,
                "meeting_url": meeting_url,
                "attendees": attendees,
            })

        return events

    @staticmethod
    def _extract_attendees(event: dict) -> list[dict] | None:
        """Extract attendee list from a Google Calendar event."""
        raw = event.get("attendees")
        if not raw:
            return None
        attendees = []
        for a in raw:
            attendees.append({
                "email": a.get("email"),
                "name": a.get("displayName"),
                "response": a.get("responseStatus"),  # accepted, declined, tentative, needsAction
                "organizer": a.get("organizer", False),
                "self": a.get("self", False),
            })
        return attendees if attendees else None

    def _extract_meeting_url(self, event: dict) -> Optional[str]:
        """Extract video meeting URL from a Google Calendar event."""
        # 1. Check conferenceData entry points
        conference_data = event.get("conferenceData", {})
        for entry_point in conference_data.get("entryPoints", []):
            if entry_point.get("entryPointType") == "video":
                return entry_point.get("uri")

        # 2. Fallback: regex in description and location
        for field in [event.get("description", ""), event.get("location", "")]:
            if not field:
                continue
            for pattern in MEETING_URL_PATTERNS:
                match = pattern.search(field)
                if match:
                    return match.group(0)

        return None


google_calendar_service = GoogleCalendarService()
