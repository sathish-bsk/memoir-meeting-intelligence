"""
Google OAuth provider implementation using authlib.
"""

import secrets
from typing import Tuple

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client

from app.core.config import settings
from app.providers.base import OAuthProvider


class GoogleOAuthProvider(OAuthProvider):
    AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
    SCOPES = ["openid", "email", "profile"]

    async def get_authorize_url(self) -> Tuple[str, str]:
        state = secrets.token_urlsafe(32)
        client = AsyncOAuth2Client(
            client_id=settings.OAUTH_GOOGLE_CLIENT_ID,
            client_secret=settings.OAUTH_GOOGLE_CLIENT_SECRET,
            redirect_uri=settings.OAUTH_GOOGLE_REDIRECT_URI,
            scope=" ".join(self.SCOPES),
        )
        url, _ = client.create_authorization_url(
            self.AUTHORIZE_URL,
            state=state,
            access_type="offline",
            prompt="consent",
        )
        return url, state

    async def exchange_code(self, code: str) -> dict:
        client = AsyncOAuth2Client(
            client_id=settings.OAUTH_GOOGLE_CLIENT_ID,
            client_secret=settings.OAUTH_GOOGLE_CLIENT_SECRET,
            redirect_uri=settings.OAUTH_GOOGLE_REDIRECT_URI,
        )

        token = await client.fetch_token(
            self.TOKEN_URL,
            code=code,
            grant_type="authorization_code",
        )

        # Fetch user info
        async with httpx.AsyncClient() as http:
            resp = await http.get(
                self.USERINFO_URL,
                headers={"Authorization": f"Bearer {token['access_token']}"},
            )
            resp.raise_for_status()
            profile = resp.json()

        return {
            "id": profile["sub"],
            "email": profile["email"],
            "name": profile.get("name", profile["email"].split("@")[0]),
            "email_verified": profile.get("email_verified", False),
            "raw_profile": profile,
        }
