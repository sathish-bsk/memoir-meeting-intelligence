"""
Zoho CRM OAuth service — handles OAuth flow with multi-region support.
Follows the ZoomOAuthService pattern.

Key Zoho differences:
- Region-specific auth/API domains (zoho.com, zoho.in, zoho.eu, etc.)
- Auth header: "Zoho-oauthtoken {token}" (not Bearer)
- Refresh tokens never expire (unless revoked)
- Token response includes api_domain and accounts-server
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet

from app.core.config import settings

logger = logging.getLogger(__name__)

# Default Zoho endpoints (overridden by region)
ZOHO_REGIONS = {
    "us": {"accounts": "https://accounts.zoho.com", "api": "https://www.zohoapis.com"},
    "eu": {"accounts": "https://accounts.zoho.eu", "api": "https://www.zohoapis.eu"},
    "in": {"accounts": "https://accounts.zoho.in", "api": "https://www.zohoapis.in"},
    "au": {"accounts": "https://accounts.zoho.com.au", "api": "https://www.zohoapis.com.au"},
    "jp": {"accounts": "https://accounts.zoho.jp", "api": "https://www.zohoapis.jp"},
    "ca": {"accounts": "https://accounts.zohocloud.ca", "api": "https://www.zohoapis.ca"},
}

ZOHO_SCOPES = "ZohoCRM.modules.ALL,ZohoCRM.settings.ALL,ZohoCRM.org.READ,ZohoCRM.users.READ"


class ZohoOAuthService:
    """Manages Zoho CRM OAuth flow and token encryption."""

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

    def get_auth_url(self, redirect_uri: str, state: str, region: str = "us") -> str:
        """Build Zoho OAuth consent URL for the given region."""
        region_info = ZOHO_REGIONS.get(region, ZOHO_REGIONS["us"])
        accounts_url = region_info["accounts"]

        params = {
            "response_type": "code",
            "client_id": settings.ZOHO_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": ZOHO_SCOPES,
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{accounts_url}/oauth/v2/auth?{urlencode(params)}"

    async def exchange_code_for_tokens(
        self, code: str, redirect_uri: str, accounts_url: str = "https://accounts.zoho.com"
    ) -> tuple[str, str, str, str, str]:
        """Exchange authorization code for tokens.

        Returns: (encrypted_tokens, org_id, org_name, api_domain, accounts_url)
        """
        token_url = f"{accounts_url}/oauth/v2/token"
        logger.info(f"Zoho token exchange: accounts_url={accounts_url}, token_url={token_url}")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": settings.ZOHO_CLIENT_ID,
                    "client_secret": settings.ZOHO_CLIENT_SECRET,
                },
            )
            resp.raise_for_status()
            token_data = resp.json()

        logger.info(f"Zoho token response keys: {list(token_data.keys())}, api_domain={token_data.get('api_domain')}")

        if "error" in token_data:
            raise ValueError(f"Zoho OAuth error: {token_data['error']}")

        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token", "")
        expires_in = token_data.get("expires_in", 3600)  # Zoho default 60min
        expires_at = datetime.now(timezone.utc).timestamp() + expires_in

        # Zoho returns api_domain in token response
        api_domain = token_data.get("api_domain", "https://www.zohoapis.com")

        # Derive api_domain from accounts_url if Zoho returned generic .com but user is on another DC
        # e.g. accounts.zoho.in → zohoapis.in
        if api_domain == "https://www.zohoapis.com" and "zoho.com" not in accounts_url:
            # Map accounts URL to API domain
            if "zoho.in" in accounts_url:
                api_domain = "https://www.zohoapis.in"
            elif "zoho.eu" in accounts_url:
                api_domain = "https://www.zohoapis.eu"
            elif "zoho.com.au" in accounts_url:
                api_domain = "https://www.zohoapis.com.au"
            elif "zoho.jp" in accounts_url:
                api_domain = "https://www.zohoapis.jp"
            elif "zohocloud.ca" in accounts_url:
                api_domain = "https://www.zohoapis.ca"
            logger.info(f"Zoho api_domain overridden to {api_domain} based on accounts_url={accounts_url}")

        tokens_blob = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
        }
        encrypted = self.fernet.encrypt(json.dumps(tokens_blob).encode()).decode()

        # Get org info — try api_domain first, then try region-derived domain
        org_id = "unknown"
        org_name = ""
        domains_to_try = [api_domain]
        # If accounts_url hints at a different DC, try that API domain too
        if "zoho.in" in accounts_url and "zohoapis.in" not in api_domain:
            domains_to_try.append("https://www.zohoapis.in")
        elif "zoho.eu" in accounts_url and "zohoapis.eu" not in api_domain:
            domains_to_try.append("https://www.zohoapis.eu")
        elif "zoho.com.au" in accounts_url and "zohoapis.com.au" not in api_domain:
            domains_to_try.append("https://www.zohoapis.com.au")
        elif "zoho.jp" in accounts_url and "zohoapis.jp" not in api_domain:
            domains_to_try.append("https://www.zohoapis.jp")
        elif "zohocloud.ca" in accounts_url and "zohoapis.ca" not in api_domain:
            domains_to_try.append("https://www.zohoapis.ca")

        for domain in domains_to_try:
            try:
                logger.info(f"Zoho org info call: trying api_domain={domain}")
                org_info = await self.get_org_info(access_token, domain)
                org_id = str(org_info.get("id", "unknown"))
                org_name = org_info.get("company_name", org_info.get("display_name", ""))
                api_domain = domain  # Use the domain that actually worked
                logger.info(f"Zoho org info success: org_id={org_id}, org_name={org_name}, api_domain={api_domain}")
                break
            except Exception as e:
                logger.warning(f"Zoho org info failed for {domain}: {e}")
                if domain == domains_to_try[-1]:
                    # All domains failed — still save the connection with what we have
                    logger.error(f"All Zoho org info attempts failed, saving connection without org info")

        return encrypted, org_id, org_name, api_domain, accounts_url

    def decrypt_tokens(self, encrypted_tokens: str) -> dict:
        decrypted = self.fernet.decrypt(encrypted_tokens.encode())
        return json.loads(decrypted)

    def encrypt_tokens(self, tokens: dict) -> str:
        return self.fernet.encrypt(json.dumps(tokens).encode()).decode()

    async def refresh_access_token(self, encrypted_tokens: str, accounts_url: str = "https://accounts.zoho.com") -> str:
        """Refresh expired OAuth token. Returns new encrypted_tokens blob."""
        tokens = self.decrypt_tokens(encrypted_tokens)
        token_url = f"{accounts_url}/oauth/v2/token"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                    "client_id": settings.ZOHO_CLIENT_ID,
                    "client_secret": settings.ZOHO_CLIENT_SECRET,
                },
            )
            resp.raise_for_status()
            new_data = resp.json()

        if "error" in new_data:
            raise ValueError(f"Zoho refresh error: {new_data['error']}")

        expires_in = new_data.get("expires_in", 3600)
        tokens_blob = {
            "access_token": new_data["access_token"],
            # Zoho does not return a new refresh_token on refresh
            "refresh_token": tokens["refresh_token"],
            "expires_at": datetime.now(timezone.utc).timestamp() + expires_in,
        }
        return self.fernet.encrypt(json.dumps(tokens_blob).encode()).decode()

    def is_token_expired(self, encrypted_tokens: str) -> bool:
        tokens = self.decrypt_tokens(encrypted_tokens)
        expires_at = tokens.get("expires_at", 0)
        return datetime.now(timezone.utc).timestamp() >= (expires_at - 300)

    async def get_valid_access_token(
        self, encrypted_tokens: str, accounts_url: str = "https://accounts.zoho.com"
    ) -> tuple[str, Optional[str]]:
        """Get a valid access token, refreshing if needed.
        Returns: (access_token, new_encrypted_tokens_or_None)
        """
        if self.is_token_expired(encrypted_tokens):
            new_encrypted = await self.refresh_access_token(encrypted_tokens, accounts_url)
            tokens = self.decrypt_tokens(new_encrypted)
            return tokens["access_token"], new_encrypted

        tokens = self.decrypt_tokens(encrypted_tokens)
        return tokens["access_token"], None

    async def get_org_info(self, access_token: str, api_domain: str) -> dict:
        """GET /crm/v6/org — returns organization info."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{api_domain}/crm/v6/org",
                headers={"Authorization": f"Zoho-oauthtoken {access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            orgs = data.get("org", [])
            return orgs[0] if orgs else {}

    async def revoke_token(self, refresh_token: str, accounts_url: str = "https://accounts.zoho.com") -> None:
        """Revoke a Zoho refresh token."""
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{accounts_url}/oauth/v2/token/revoke",
                    params={"token": refresh_token},
                )
        except Exception as e:
            logger.warning(f"Failed to revoke Zoho token: {e}")


zoho_oauth_service = ZohoOAuthService()
