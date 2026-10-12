"""Service-account auth for the bot calendar (Phase 2 §4.3, §4.4).

The bot has a synthetic `calendar_connections` row whose
`encrypted_tokens` field holds a *pointer* to its credential in AWS
Secrets Manager (not the credential itself). This module:

1. Decodes the pointer from the connection's encrypted_tokens.
2. Fetches the service-account JSON from Secrets Manager (cached).
3. Mints a delegated Google access token via JWT bearer flow.
4. Provides a `build_calendar_client()` helper the rest of integration_service uses.

The minted access tokens are also cached in-memory for ~50 min (Google's
tokens are valid ~60 min; we refresh ~10 min before expiry).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

from cryptography.fernet import Fernet

from videoclip_common.secrets import fetch_secret_json

logger = logging.getLogger(__name__)

# Re-mint tokens 10 min before Google says they expire.
_ACCESS_TOKEN_REFRESH_SKEW = 600

# Scopes the bot's Workspace service account requires. The Workspace
# admin must grant these to the service account in the admin console
# under "Domain-wide delegation".
BOT_CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]


@dataclass
class _BotPointer:
    auth_type: str           # always "service_account_dwd" for v1
    secret_arn: str
    delegated_email: str


@dataclass
class _AccessToken:
    token: str
    expires_at: float


# In-process cache of the most recent access token per delegated email.
_token_cache: dict[str, _AccessToken] = {}


def parse_bot_pointer(encrypted_tokens: str, *, fernet_key: bytes) -> _BotPointer:
    """Decrypt the connection's encrypted_tokens blob and return the
    Secrets Manager pointer payload defined in plan §4.2.
    """
    plaintext = Fernet(fernet_key).decrypt(encrypted_tokens.encode()).decode()
    payload = json.loads(plaintext)
    return _BotPointer(
        auth_type=payload["auth_type"],
        secret_arn=payload["secret_arn"],
        delegated_email=payload["delegated_email"],
    )


def get_bot_access_token(
    pointer: _BotPointer,
    *,
    scopes: Optional[list[str]] = None,
    force_refresh: bool = False,
) -> str:
    """Mint (or reuse cached) Google access token for the delegated bot email.

    Uses domain-wide delegation: signs a JWT as the service account with
    `sub=delegated_email`, exchanges for a delegated access token at
    Google's OAuth2 token endpoint.
    """
    scope_list = scopes or BOT_CALENDAR_SCOPES
    cache_key = f"{pointer.delegated_email}:{','.join(sorted(scope_list))}"

    now = time.time()
    if not force_refresh:
        cached = _token_cache.get(cache_key)
        if cached and cached.expires_at - now > _ACCESS_TOKEN_REFRESH_SKEW:
            return cached.token

    # Fetch the service-account JSON (cached at the secrets layer).
    sa_json = fetch_secret_json(pointer.secret_arn)

    # Lazy imports — these libs are required only when actually using
    # the bot calendar (which most non-prod environments won't be).
    from google.oauth2 import service_account  # type: ignore

    credentials = service_account.Credentials.from_service_account_info(
        sa_json,
        scopes=scope_list,
        subject=pointer.delegated_email,
    )
    # Refresh forces the JWT exchange to happen and populates token + expiry.
    from google.auth.transport.requests import Request  # type: ignore

    credentials.refresh(Request())

    expires_at = credentials.expiry.timestamp() if credentials.expiry else now + 3600
    _token_cache[cache_key] = _AccessToken(token=credentials.token, expires_at=expires_at)
    logger.info(
        "Minted bot access token for %s (expires in %ss)",
        pointer.delegated_email,
        int(expires_at - now),
    )
    return credentials.token


def clear_token_cache() -> None:
    """Reset the access-token cache. Useful for tests + key rotation."""
    _token_cache.clear()


def is_bot_connection(provider: str) -> bool:
    """True iff this calendar_connection represents the synthetic bot calendar."""
    return provider == "bot_calendar"
