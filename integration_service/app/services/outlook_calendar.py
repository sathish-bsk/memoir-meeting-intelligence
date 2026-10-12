"""
Outlook / Office 365 Calendar OAuth + event fetching service.
Uses Microsoft Graph API via plain httpx (no MSAL dependency).
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet

from app.core.config import settings

logger = logging.getLogger(__name__)

SCOPES = "Calendars.Read User.Read offline_access"

AUTHORIZE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me"
GRAPH_CALENDARVIEW_URL = "https://graph.microsoft.com/v1.0/me/calendarview"

# Regex patterns for meeting URLs
MEETING_URL_PATTERNS = [
    re.compile(r"https?://teams\.microsoft\.com/l/meetup-join/[^\s\"<]+", re.IGNORECASE),
    re.compile(r"https?://meet\.google\.com/[a-z\-]+", re.IGNORECASE),
    re.compile(r"https?://[\w.-]*zoom\.us/j/\d+[^\s\"<]*", re.IGNORECASE),
]


class OutlookCalendarService:
    """Manages Outlook Calendar OAuth flow and event fetching via Microsoft Graph."""

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

    def get_auth_url(self, redirect_uri: str, state: str) -> str:
        """Generate Microsoft OAuth consent URL."""
        params = {
            "client_id": settings.OUTLOOK_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "state": state,
            "response_mode": "query",
            "prompt": "consent",
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code_for_tokens(
        self, code: str, redirect_uri: str
    ) -> tuple[str, str]:
        """Exchange authorization code for tokens.

        Returns (encrypted_tokens, provider_email).
        """
        resp = httpx.post(
            TOKEN_URL,
            data={
                "client_id": settings.OUTLOOK_CLIENT_ID,
                "client_secret": settings.OUTLOOK_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "scope": SCOPES,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        token_resp = resp.json()

        # Build token blob
        token_data = {
            "access_token": token_resp["access_token"],
            "refresh_token": token_resp.get("refresh_token", ""),
            "token_type": token_resp.get("token_type", "Bearer"),
            "expires_at": int(time.time()) + token_resp.get("expires_in", 3600),
            "scope": token_resp.get("scope", SCOPES),
        }

        encrypted = self.fernet.encrypt(json.dumps(token_data).encode()).decode()

        # Fetch user email from Microsoft Graph
        me_resp = httpx.get(
            GRAPH_ME_URL,
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        provider_email = "unknown"
        if me_resp.status_code == 200:
            me_data = me_resp.json()
            provider_email = me_data.get("mail") or me_data.get("userPrincipalName") or "unknown"

        return encrypted, provider_email

    def decrypt_tokens(self, encrypted_tokens: str) -> dict:
        """Decrypt stored tokens into a dict."""
        return json.loads(self.fernet.decrypt(encrypted_tokens.encode()))

    def refresh_if_needed(self, encrypted_tokens: str) -> tuple[str, Optional[str]]:
        """Check if token is expired; refresh if so.

        Returns (access_token, new_encrypted_tokens_or_None).

        Raises TokenInvalidError when Microsoft rejects the refresh_token
        (invalid_grant / AADSTS70008 / AADSTS70043 etc). The caller is
        expected to mark the connection token_invalid=true and surface a
        401 so the user can re-authenticate.
        """
        from .oauth_errors import TokenInvalidError

        token_data = self.decrypt_tokens(encrypted_tokens)

        # Add 60-second buffer
        if token_data.get("expires_at", 0) > time.time() + 60:
            return token_data["access_token"], None

        # Token expired — refresh
        if not token_data.get("refresh_token"):
            raise TokenInvalidError("outlook", reason="no_refresh_token")

        resp = httpx.post(
            TOKEN_URL,
            data={
                "client_id": settings.OUTLOOK_CLIENT_ID,
                "client_secret": settings.OUTLOOK_CLIENT_SECRET,
                "refresh_token": token_data["refresh_token"],
                "grant_type": "refresh_token",
                "scope": SCOPES,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code in (400, 401):
            body = resp.text.lower()
            if "invalid_grant" in body or "aadsts7" in body:
                raise TokenInvalidError("outlook", reason="invalid_grant")
        resp.raise_for_status()
        new_resp = resp.json()

        token_data["access_token"] = new_resp["access_token"]
        token_data["expires_at"] = int(time.time()) + new_resp.get("expires_in", 3600)
        if new_resp.get("refresh_token"):
            token_data["refresh_token"] = new_resp["refresh_token"]

        new_encrypted = self.fernet.encrypt(json.dumps(token_data).encode()).decode()
        return token_data["access_token"], new_encrypted

    def fetch_events(self, encrypted_tokens: str, days_ahead: int = 7) -> list[dict]:
        """Fetch upcoming calendar events for the next N days via Microsoft Graph."""
        access_token, _ = self.refresh_if_needed(encrypted_tokens)

        now = datetime.now(timezone.utc)
        start_dt = now.strftime("%Y-%m-%dT%H:%M:%S.0000000")
        end_dt = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%dT%H:%M:%S.0000000")

        resp = httpx.get(
            GRAPH_CALENDARVIEW_URL,
            params={
                "startDateTime": start_dt,
                "endDateTime": end_dt,
                "$top": 50,
                "$orderby": "start/dateTime",
                "$select": "id,subject,body,start,end,isAllDay,onlineMeeting,location,attendees,organizer",
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "Prefer": 'outlook.timezone="UTC"',
            },
        )
        resp.raise_for_status()
        data = resp.json()

        events = []
        for event in data.get("value", []):
            # Skip all-day events
            if event.get("isAllDay"):
                continue

            start = event.get("start", {})
            end = event.get("end", {})
            start_time = start.get("dateTime")
            end_time = end.get("dateTime")
            if not start_time or not end_time:
                continue

            meeting_url = self._extract_meeting_url(event)
            attendees = self._extract_attendees(event)

            # Graph API returns UTC times (we request outlook.timezone="UTC")
            # Append Z suffix if no timezone info to ensure proper parsing
            if not start_time.endswith("Z") and "+" not in start_time:
                start_time = start_time.rstrip("0").rstrip(".") + "Z"
            if not end_time.endswith("Z") and "+" not in end_time:
                end_time = end_time.rstrip("0").rstrip(".") + "Z"

            events.append({
                "provider_event_id": event["id"],
                "title": event.get("subject", "Untitled"),
                "description": (event.get("body") or {}).get("content"),
                "start_time": start_time,
                "end_time": end_time,
                "meeting_url": meeting_url,
                "attendees": attendees,
            })

        return events

    @staticmethod
    def _extract_attendees(event: dict) -> list[dict] | None:
        """Extract attendee list from a Microsoft Graph calendar event."""
        raw = event.get("attendees")
        if not raw:
            return None
        organizer_email = (event.get("organizer", {}).get("emailAddress", {}).get("address") or "").lower()
        attendees = []
        for a in raw:
            email_addr = a.get("emailAddress", {})
            email = email_addr.get("address")
            attendees.append({
                "email": email,
                "name": email_addr.get("name"),
                "response": (a.get("status", {}).get("response") or "").replace("Response", "").lower(),
                "organizer": email and email.lower() == organizer_email,
                "self": False,
            })
        return attendees if attendees else None

    def _extract_meeting_url(self, event: dict) -> Optional[str]:
        """Extract video meeting URL from an Outlook calendar event."""
        # 1. Check onlineMeeting.joinUrl
        online_meeting = event.get("onlineMeeting")
        if online_meeting and online_meeting.get("joinUrl"):
            return online_meeting["joinUrl"]

        # 2. Fallback: regex in body content and location
        body_content = (event.get("body") or {}).get("content", "")
        location = (event.get("location") or {}).get("displayName", "")

        for field in [body_content, location]:
            if not field:
                continue
            for pattern in MEETING_URL_PATTERNS:
                match = pattern.search(field)
                if match:
                    return match.group(0)

        return None


outlook_calendar_service = OutlookCalendarService()
