"""
Microsoft Teams Cloud Recording OAuth service.
Uses the same Azure AD app as Outlook calendar (OUTLOOK_CLIENT_ID / OUTLOOK_CLIENT_SECRET).
Fernet encryption with CALENDAR_ENCRYPTION_KEY.

Delegated scopes: User.Read OnlineMeetings.Read offline_access
Application permission (admin consent): CallRecords.Read.All, OnlineMeetings.Read.All
"""

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet

from app.core.config import settings

logger = logging.getLogger(__name__)

MS_AUTHORIZE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
MS_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Delegated scopes for user connection
DELEGATED_SCOPES = "User.Read OnlineMeetings.Read offline_access"

# Graph subscription expiry: callRecords subscriptions max 3 days
SUBSCRIPTION_MAX_HOURS = 71  # just under 3 days to be safe


class TeamsCloudOAuthService:
    """Manages Microsoft Teams Cloud Recording OAuth and Graph subscriptions."""

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
        """Build Microsoft OAuth consent URL for delegated user permissions."""
        params = {
            "client_id": settings.OUTLOOK_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": DELEGATED_SCOPES,
            "state": state,
            "response_mode": "query",
            "prompt": "consent",
        }
        return f"{MS_AUTHORIZE_URL}?{urlencode(params)}"

    def get_admin_consent_url(self, tenant_id: str, redirect_uri: str, state: str) -> str:
        """Build admin consent URL for CallRecords.Read.All application permission.

        A Microsoft admin must open this URL and grant consent for the app to
        receive callRecord webhook events.
        """
        params = {
            "client_id": settings.OUTLOOK_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        base = f"https://login.microsoftonline.com/{tenant_id}/adminconsent"
        return f"{base}?{urlencode(params)}"

    async def exchange_code_for_tokens(
        self, code: str, redirect_uri: str
    ) -> tuple[str, str, str, str]:
        """Exchange authorization code for tokens and fetch user info.

        Returns: (encrypted_tokens, ms_email, ms_user_id, tenant_id)
        """
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                MS_TOKEN_URL,
                data={
                    "client_id": settings.OUTLOOK_CLIENT_ID,
                    "client_secret": settings.OUTLOOK_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                    "scope": DELEGATED_SCOPES,
                },
            )
            resp.raise_for_status()
            token_data = resp.json()

        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token", "")
        expires_in = token_data.get("expires_in", 3600)
        expires_at = datetime.now(timezone.utc).timestamp() + expires_in

        # Extract tenant_id from access token JWT payload (no verification)
        tenant_id = self._extract_tenant_id(access_token)

        # Encrypt tokens for storage
        tokens_blob = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "tenant_id": tenant_id,
        }
        encrypted = self.fernet.encrypt(json.dumps(tokens_blob).encode()).decode()

        # Get user info from MS Graph
        user_info = await self._get_me(access_token)
        ms_email = user_info.get("userPrincipalName") or user_info.get("mail", "unknown")
        ms_user_id = user_info.get("id", "unknown")

        return encrypted, ms_email, ms_user_id, tenant_id

    def decrypt_tokens(self, encrypted_tokens: str) -> dict:
        """Decrypt stored tokens."""
        return json.loads(self.fernet.decrypt(encrypted_tokens.encode()))

    def encrypt_tokens(self, tokens: dict) -> str:
        """Encrypt a tokens dict."""
        return self.fernet.encrypt(json.dumps(tokens).encode()).decode()

    def is_token_expired(self, encrypted_tokens: str) -> bool:
        """Check if access token is expired (or within 5 min of expiring)."""
        tokens = self.decrypt_tokens(encrypted_tokens)
        expires_at = tokens.get("expires_at", 0)
        return datetime.now(timezone.utc).timestamp() >= (expires_at - 300)

    async def refresh_access_token(self, encrypted_tokens: str) -> str:
        """Refresh expired delegated access token. Returns new encrypted_tokens."""
        tokens = self.decrypt_tokens(encrypted_tokens)
        refresh_token = tokens.get("refresh_token", "")
        if not refresh_token:
            raise ValueError("No refresh token available")

        tenant_id = tokens.get("tenant_id", "common")
        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_url,
                data={
                    "client_id": settings.OUTLOOK_CLIENT_ID,
                    "client_secret": settings.OUTLOOK_CLIENT_SECRET,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                    "scope": DELEGATED_SCOPES,
                },
            )
            resp.raise_for_status()
            new_data = resp.json()

        expires_in = new_data.get("expires_in", 3600)
        new_blob = {
            "access_token": new_data["access_token"],
            "refresh_token": new_data.get("refresh_token", refresh_token),
            "expires_at": datetime.now(timezone.utc).timestamp() + expires_in,
            "tenant_id": tenant_id,
        }
        return self.fernet.encrypt(json.dumps(new_blob).encode()).decode()

    async def get_valid_access_token(self, encrypted_tokens: str) -> tuple[str, Optional[str]]:
        """Get a valid delegated access token, refreshing if needed.

        Returns: (access_token, new_encrypted_tokens_or_None)
        """
        if self.is_token_expired(encrypted_tokens):
            new_encrypted = await self.refresh_access_token(encrypted_tokens)
            tokens = self.decrypt_tokens(new_encrypted)
            return tokens["access_token"], new_encrypted
        tokens = self.decrypt_tokens(encrypted_tokens)
        return tokens["access_token"], None

    async def get_app_token(self, tenant_id: str) -> str:
        """Get an app-level access token using client credentials flow.

        Required for creating Graph subscriptions (CallRecords.Read.All permission).
        Admin consent must be granted first for this to work.
        """
        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_url,
                data={
                    "client_id": settings.OUTLOOK_CLIENT_ID,
                    "client_secret": settings.OUTLOOK_CLIENT_SECRET,
                    "grant_type": "client_credentials",
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
            resp.raise_for_status()
            return resp.json()["access_token"]

    async def create_subscription(
        self, tenant_id: str, webhook_url: str, client_state: str
    ) -> tuple[str, datetime]:
        """Create a Graph subscription for callRecords.

        Admin consent for CallRecords.Read.All must already be granted.

        Returns: (subscription_id, expiry_datetime)
        """
        app_token = await self.get_app_token(tenant_id)
        expiry = datetime.now(timezone.utc) + timedelta(hours=SUBSCRIPTION_MAX_HOURS)
        expiry_str = expiry.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")

        payload = {
            "changeType": "created",
            "notificationUrl": webhook_url,
            "resource": "communications/callRecords",
            "expirationDateTime": expiry_str,
            "clientState": client_state,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{GRAPH_BASE}/subscriptions",
                json=payload,
                headers={"Authorization": f"Bearer {app_token}"},
            )
            resp.raise_for_status()
            data = resp.json()

        subscription_id = data["id"]
        return subscription_id, expiry

    async def renew_subscription(
        self, tenant_id: str, subscription_id: str
    ) -> datetime:
        """Extend an existing subscription by patching expirationDateTime.

        Returns: new expiry datetime.
        """
        app_token = await self.get_app_token(tenant_id)
        expiry = datetime.now(timezone.utc) + timedelta(hours=SUBSCRIPTION_MAX_HOURS)
        expiry_str = expiry.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")

        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{GRAPH_BASE}/subscriptions/{subscription_id}",
                json={"expirationDateTime": expiry_str},
                headers={"Authorization": f"Bearer {app_token}"},
            )
            resp.raise_for_status()

        return expiry

    async def delete_subscription(self, tenant_id: str, subscription_id: str) -> None:
        """Delete a Graph subscription (called on disconnect)."""
        try:
            app_token = await self.get_app_token(tenant_id)
            async with httpx.AsyncClient() as client:
                await client.delete(
                    f"{GRAPH_BASE}/subscriptions/{subscription_id}",
                    headers={"Authorization": f"Bearer {app_token}"},
                )
        except Exception as e:
            logger.warning(f"Failed to delete Teams subscription {subscription_id}: {e}")

    async def _get_me(self, access_token: str) -> dict:
        """GET /me from Microsoft Graph."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GRAPH_BASE}/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def _extract_tenant_id(access_token: str) -> str:
        """Extract tenant_id (tid) from MS JWT payload without signature verification."""
        try:
            parts = access_token.split(".")
            if len(parts) < 2:
                return "common"
            # Add base64 padding
            payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            return payload.get("tid", "common")
        except Exception:
            return "common"


teams_cloud_oauth_service = TeamsCloudOAuthService()
