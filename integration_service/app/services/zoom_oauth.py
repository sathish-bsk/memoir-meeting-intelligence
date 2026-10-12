"""
Zoom OAuth service — handles OAuth flow, token encryption, and Zoom API calls.
Mirrors the GoogleCalendarService pattern from google_calendar.py.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from cryptography.fernet import Fernet

from app.core.config import settings

logger = logging.getLogger(__name__)

ZOOM_AUTH_URL = "https://zoom.us/oauth/authorize"
ZOOM_TOKEN_URL = "https://zoom.us/oauth/token"
ZOOM_API_BASE = "https://api.zoom.us/v2"


class ZoomOAuthService:
    """Manages Zoom OAuth flow and token encryption."""

    def __init__(self):
        self._fernet: Optional[Fernet] = None

    @property
    def fernet(self) -> Fernet:
        if self._fernet is None:
            key = settings.ZOOM_ENCRYPTION_KEY or settings.CALENDAR_ENCRYPTION_KEY
            if not key:
                raise ValueError("ZOOM_ENCRYPTION_KEY (or CALENDAR_ENCRYPTION_KEY) not configured")
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        return self._fernet

    def get_auth_url(self, redirect_uri: str, state: str) -> str:
        """Build Zoom OAuth consent URL."""
        params = {
            "response_type": "code",
            "client_id": settings.ZOOM_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{ZOOM_AUTH_URL}?{qs}"

    async def exchange_code_for_tokens(
        self, code: str, redirect_uri: str
    ) -> tuple[str, str, str]:
        """Exchange authorization code for tokens.

        POST https://zoom.us/oauth/token with Basic auth (client_id:client_secret).

        Returns: (encrypted_tokens, zoom_email, zoom_user_id)
        """
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                ZOOM_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                auth=(settings.ZOOM_CLIENT_ID, settings.ZOOM_CLIENT_SECRET),
            )
            resp.raise_for_status()
            token_data = resp.json()

        access_token = token_data["access_token"]
        refresh_token = token_data["refresh_token"]
        expires_in = token_data.get("expires_in", 3600)
        expires_at = datetime.now(timezone.utc).timestamp() + expires_in

        # Encrypt tokens
        tokens_blob = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
        }
        encrypted = self.fernet.encrypt(json.dumps(tokens_blob).encode()).decode()

        # Get user info from Zoom
        user_info = await self.get_user_info(access_token)
        zoom_email = user_info.get("email", "unknown")
        zoom_user_id = user_info.get("id", "unknown")

        return encrypted, zoom_email, zoom_user_id

    def decrypt_tokens(self, encrypted_tokens: str) -> dict:
        """Decrypt stored tokens into a dict with access_token, refresh_token, expires_at."""
        decrypted = self.fernet.decrypt(encrypted_tokens.encode())
        return json.loads(decrypted)

    def encrypt_tokens(self, tokens: dict) -> str:
        """Encrypt a tokens dict back to storage format."""
        return self.fernet.encrypt(json.dumps(tokens).encode()).decode()

    async def refresh_access_token(self, encrypted_tokens: str) -> str:
        """Refresh expired OAuth token.

        Returns: new encrypted_tokens blob.
        """
        tokens = self.decrypt_tokens(encrypted_tokens)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                ZOOM_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                },
                auth=(settings.ZOOM_CLIENT_ID, settings.ZOOM_CLIENT_SECRET),
            )
            resp.raise_for_status()
            new_data = resp.json()

        expires_in = new_data.get("expires_in", 3600)
        tokens_blob = {
            "access_token": new_data["access_token"],
            "refresh_token": new_data.get("refresh_token", tokens["refresh_token"]),
            "expires_at": datetime.now(timezone.utc).timestamp() + expires_in,
        }
        return self.fernet.encrypt(json.dumps(tokens_blob).encode()).decode()

    def is_token_expired(self, encrypted_tokens: str) -> bool:
        """Check if the access token is expired (or within 5 min of expiring)."""
        tokens = self.decrypt_tokens(encrypted_tokens)
        expires_at = tokens.get("expires_at", 0)
        return datetime.now(timezone.utc).timestamp() >= (expires_at - 300)

    async def get_valid_access_token(self, encrypted_tokens: str) -> tuple[str, Optional[str]]:
        """Get a valid access token, refreshing if needed.

        Returns: (access_token, new_encrypted_tokens_or_None)
        """
        if self.is_token_expired(encrypted_tokens):
            new_encrypted = await self.refresh_access_token(encrypted_tokens)
            tokens = self.decrypt_tokens(new_encrypted)
            return tokens["access_token"], new_encrypted

        tokens = self.decrypt_tokens(encrypted_tokens)
        return tokens["access_token"], None

    async def get_user_info(self, access_token: str) -> dict:
        """GET https://api.zoom.us/v2/users/me"""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{ZOOM_API_BASE}/users/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            return resp.json()

    async def check_recording_settings(self, access_token: str) -> dict:
        """GET https://api.zoom.us/v2/users/me/settings

        Returns recording settings to verify cloud recording is enabled.
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{ZOOM_API_BASE}/users/me/settings",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            return resp.json()

    async def revoke_token(self, access_token: str) -> None:
        """Revoke an OAuth token."""
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    "https://zoom.us/oauth/revoke",
                    data={"token": access_token},
                    auth=(settings.ZOOM_CLIENT_ID, settings.ZOOM_CLIENT_SECRET),
                )
        except Exception as e:
            logger.warning(f"Failed to revoke Zoom token: {e}")


zoom_oauth_service = ZoomOAuthService()
