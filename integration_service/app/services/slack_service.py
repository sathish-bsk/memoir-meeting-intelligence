"""
Slack OAuth + messaging service.
Handles token encryption, OAuth flow, channel listing, and message posting.
"""

import json
import logging
from typing import Optional
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet

from app.core.config import settings

logger = logging.getLogger(__name__)

SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"
SLACK_CONVERSATIONS_LIST_URL = "https://slack.com/api/conversations.list"
SLACK_CHAT_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
SLACK_AUTH_TEST_URL = "https://slack.com/api/auth.test"
SLACK_AUTH_REVOKE_URL = "https://slack.com/api/auth.revoke"
SLACK_CONVERSATIONS_JOIN_URL = "https://slack.com/api/conversations.join"

BOT_SCOPES = "chat:write,channels:join,channels:read,groups:read"


class SlackService:
    """Manages Slack OAuth flow, channel listing, and message posting."""

    def __init__(self):
        self._fernet: Optional[Fernet] = None

    @property
    def fernet(self) -> Fernet:
        if self._fernet is None:
            key = settings.SLACK_ENCRYPTION_KEY or settings.CALENDAR_ENCRYPTION_KEY
            if not key:
                raise ValueError("SLACK_ENCRYPTION_KEY not configured")
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        return self._fernet

    def get_auth_url(self, redirect_uri: str, state: str) -> str:
        """Generate Slack OAuth consent URL."""
        params = {
            "client_id": settings.SLACK_CLIENT_ID,
            "scope": BOT_SCOPES,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return f"{SLACK_AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code_for_tokens(
        self, code: str, redirect_uri: str
    ) -> tuple[str, str, str, list[str]]:
        """Exchange authorization code for tokens.

        Returns (encrypted_tokens, workspace_id, workspace_name, scopes).
        """
        resp = httpx.post(
            SLACK_TOKEN_URL,
            data={
                "client_id": settings.SLACK_CLIENT_ID,
                "client_secret": settings.SLACK_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("ok"):
            raise ValueError(f"Slack OAuth failed: {data.get('error', 'unknown')}")

        token_data = {
            "access_token": data["access_token"],
            "token_type": data.get("token_type", "bot"),
            "bot_user_id": data.get("bot_user_id", ""),
        }

        encrypted = self.fernet.encrypt(json.dumps(token_data).encode()).decode()

        workspace_id = data.get("team", {}).get("id", "")
        workspace_name = data.get("team", {}).get("name", "")
        scopes = data.get("scope", "").split(",") if data.get("scope") else []

        return encrypted, workspace_id, workspace_name, scopes

    def decrypt_token(self, encrypted_tokens: str) -> str:
        """Decrypt stored tokens and return the access_token."""
        token_data = json.loads(self.fernet.decrypt(encrypted_tokens.encode()))
        return token_data["access_token"]

    def revoke_token(self, encrypted_tokens: str) -> bool:
        """Revoke a Slack token."""
        try:
            access_token = self.decrypt_token(encrypted_tokens)
            resp = httpx.get(
                SLACK_AUTH_REVOKE_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            data = resp.json()
            return data.get("ok", False)
        except Exception as e:
            logger.warning(f"Failed to revoke Slack token: {e}")
            return False

    def list_channels(self, encrypted_tokens: str) -> list[dict]:
        """List channels the bot can post to."""
        access_token = self.decrypt_token(encrypted_tokens)
        channels = []
        cursor = None

        while True:
            params = {
                "exclude_archived": "true",
                "types": "public_channel,private_channel",
                "limit": "200",
            }
            if cursor:
                params["cursor"] = cursor

            resp = httpx.get(
                SLACK_CONVERSATIONS_LIST_URL,
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("ok"):
                raise ValueError(f"Slack API error: {data.get('error', 'unknown')}")

            for ch in data.get("channels", []):
                channels.append({
                    "id": ch["id"],
                    "name": ch["name"],
                    "is_private": ch.get("is_private", False),
                })

            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        return sorted(channels, key=lambda c: c["name"])

    def join_channel(self, encrypted_tokens: str, channel_id: str) -> bool:
        """Join a public channel. Required before posting."""
        access_token = self.decrypt_token(encrypted_tokens)
        resp = httpx.post(
            SLACK_CONVERSATIONS_JOIN_URL,
            json={"channel": channel_id},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        data = resp.json()
        if not data.get("ok") and data.get("error") != "already_in_channel":
            logger.warning(f"Slack join_channel failed: {data.get('error')}")
            return False
        return True

    def post_message(
        self, encrypted_tokens: str, channel_id: str, text: str, blocks: list[dict] | None = None
    ) -> bool:
        """Post a message to a Slack channel. Auto-joins if not in channel."""
        access_token = self.decrypt_token(encrypted_tokens)
        payload = {
            "channel": channel_id,
            "text": text,
        }
        if blocks:
            payload["blocks"] = blocks

        resp = httpx.post(
            SLACK_CHAT_POST_MESSAGE_URL,
            json=payload,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("ok"):
            # Auto-join and retry once if not in channel
            if data.get("error") == "not_in_channel":
                self.join_channel(encrypted_tokens, channel_id)
                retry = httpx.post(
                    SLACK_CHAT_POST_MESSAGE_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                retry_data = retry.json()
                if retry_data.get("ok"):
                    return True
                logger.error(f"Slack postMessage failed after join: {retry_data.get('error')}")
                return False
            logger.error(f"Slack postMessage failed: {data.get('error')}")
            return False
        return True


slack_service = SlackService()
