"""
Microsoft OAuth provider implementation using authlib.
"""

import secrets
from typing import Tuple

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client

from app.core.config import settings
from app.providers.base import OAuthProvider


class MicrosoftOAuthProvider(OAuthProvider):
    AUTHORIZE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    USERINFO_URL = "https://graph.microsoft.com/v1.0/me"
    SCOPES = ["openid", "email", "profile", "User.Read"]

    async def get_authorize_url(self) -> Tuple[str, str]:
        state = secrets.token_urlsafe(32)
        client = AsyncOAuth2Client(
            client_id=settings.OAUTH_MICROSOFT_CLIENT_ID,
            client_secret=settings.OAUTH_MICROSOFT_CLIENT_SECRET,
            redirect_uri=settings.OAUTH_MICROSOFT_REDIRECT_URI,
            scope=" ".join(self.SCOPES),
        )
        url, _ = client.create_authorization_url(
            self.AUTHORIZE_URL,
            state=state,
            response_type="code",
        )
        return url, state

    async def exchange_code(self, code: str) -> dict:
        client = AsyncOAuth2Client(
            client_id=settings.OAUTH_MICROSOFT_CLIENT_ID,
            client_secret=settings.OAUTH_MICROSOFT_CLIENT_SECRET,
            redirect_uri=settings.OAUTH_MICROSOFT_REDIRECT_URI,
        )

        token = await client.fetch_token(
            self.TOKEN_URL,
            code=code,
            grant_type="authorization_code",
        )

        # Fetch user info from Microsoft Graph
        async with httpx.AsyncClient() as http:
            resp = await http.get(
                self.USERINFO_URL,
                headers={"Authorization": f"Bearer {token['access_token']}"},
            )
            resp.raise_for_status()
            profile = resp.json()

        email = profile.get("mail") or profile.get("userPrincipalName", "")

        return {
            "id": profile["id"],
            "email": email,
            "name": profile.get("displayName", email.split("@")[0]),
            "email_verified": True,  # Microsoft verifies emails
            "raw_profile": profile,
        }
