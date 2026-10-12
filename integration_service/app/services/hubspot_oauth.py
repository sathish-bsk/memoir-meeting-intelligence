"""
HubSpot OAuth service — handles OAuth flow, token encryption, and user info.
Follows the ZoomOAuthService pattern.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from cryptography.fernet import Fernet

from app.core.config import settings

logger = logging.getLogger(__name__)

HUBSPOT_AUTH_URL = "https://app.hubspot.com/oauth/authorize"
HUBSPOT_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
HUBSPOT_API_BASE = "https://api.hubapi.com"

HUBSPOT_SCOPES = [
    "crm.objects.contacts.read",
    "crm.objects.contacts.write",
    "crm.objects.deals.read",
    "crm.objects.deals.write",
    "crm.objects.companies.read",
    "crm.objects.companies.write",
    "oauth",
]


class HubSpotOAuthService:
    """Manages HubSpot OAuth flow and token encryption."""

    def __init__(self):
        self._fernet: Optional[Fernet] = None

    @property
    def fernet(self) -> Fernet:
        if self._fernet is None:
            key = settings.CRM_ENCRYPTION_KEY or settings.CALENDAR_ENCRYPTION_KEY
            if not key:
                raise ValueError("CRM_ENCRYPTION_KEY (or CALENDAR_ENCRYPTION_KEY) not configured")
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        return self._fernet

    def get_auth_url(self, redirect_uri: str, state: str) -> str:
        """Build HubSpot OAuth consent URL."""
        params = {
            "response_type": "code",
            "client_id": settings.HUBSPOT_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": " ".join(HUBSPOT_SCOPES),
            "state": state,
        }
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{HUBSPOT_AUTH_URL}?{qs}"

    async def exchange_code_for_tokens(
        self, code: str, redirect_uri: str
    ) -> tuple[str, str, str]:
        """Exchange authorization code for tokens.

        Returns: (encrypted_tokens, portal_id, hub_domain)
        """
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                HUBSPOT_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": settings.HUBSPOT_CLIENT_ID,
                    "client_secret": settings.HUBSPOT_CLIENT_SECRET,
                },
            )
            resp.raise_for_status()
            token_data = resp.json()

        access_token = token_data["access_token"]
        refresh_token = token_data["refresh_token"]
        expires_in = token_data.get("expires_in", 1800)  # HubSpot default 30min
        expires_at = datetime.now(timezone.utc).timestamp() + expires_in

        tokens_blob = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
        }
        encrypted = self.fernet.encrypt(json.dumps(tokens_blob).encode()).decode()

        # Get account info
        account_info = await self.get_account_info(access_token)
        portal_id = str(account_info.get("portalId", "unknown"))
        hub_domain = account_info.get("hub_domain", account_info.get("uiDomain", ""))

        return encrypted, portal_id, hub_domain

    def decrypt_tokens(self, encrypted_tokens: str) -> dict:
        decrypted = self.fernet.decrypt(encrypted_tokens.encode())
        return json.loads(decrypted)

    def encrypt_tokens(self, tokens: dict) -> str:
        return self.fernet.encrypt(json.dumps(tokens).encode()).decode()

    async def refresh_access_token(self, encrypted_tokens: str) -> str:
        """Refresh expired OAuth token. Returns new encrypted_tokens blob."""
        tokens = self.decrypt_tokens(encrypted_tokens)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                HUBSPOT_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                    "client_id": settings.HUBSPOT_CLIENT_ID,
                    "client_secret": settings.HUBSPOT_CLIENT_SECRET,
                },
            )
            resp.raise_for_status()
            new_data = resp.json()

        expires_in = new_data.get("expires_in", 1800)
        tokens_blob = {
            "access_token": new_data["access_token"],
            "refresh_token": new_data.get("refresh_token", tokens["refresh_token"]),
            "expires_at": datetime.now(timezone.utc).timestamp() + expires_in,
        }
        return self.fernet.encrypt(json.dumps(tokens_blob).encode()).decode()

    def is_token_expired(self, encrypted_tokens: str) -> bool:
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

    async def get_account_info(self, access_token: str) -> dict:
        """GET /oauth/v1/access-tokens/{token} — returns portal info."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{HUBSPOT_API_BASE}/oauth/v1/access-tokens/{access_token}",
            )
            resp.raise_for_status()
            return resp.json()

    async def revoke_token(self, refresh_token: str) -> None:
        """Revoke a HubSpot refresh token."""
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{HUBSPOT_TOKEN_URL}",
                    data={
                        "grant_type": "refresh_token",
                        "client_id": settings.HUBSPOT_CLIENT_ID,
                        "client_secret": settings.HUBSPOT_CLIENT_SECRET,
                        "token": refresh_token,
                    },
                )
        except Exception as e:
            logger.warning(f"Failed to revoke HubSpot token: {e}")


hubspot_oauth_service = HubSpotOAuthService()
