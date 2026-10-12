"""OAuth-related exception types.

Centralized so calendar / Slack / Zoom / CRM token-refresh code can
raise the same exception class and `calendar_health.py` can catch it
in one place. Referenced by `calendar_health.maybe_refresh_watch_on_token_use`
and `daily_health_check` when an OAuth refresh fails because the
provider says the refresh_token itself is invalid (revoked, expired
past the provider's max idle window, user removed the app, etc.).

Raisers typically catch the provider-specific HTTP error first
(e.g. 400 invalid_grant from Google, 401 from Microsoft) and re-raise
as TokenInvalidError so the higher-level code path doesn't have to
discriminate on provider error shapes.
"""
from __future__ import annotations


class TokenInvalidError(Exception):
    """Raised when an OAuth token refresh fails irrecoverably.

    Distinct from transient network / 5xx failures. When a token is
    invalid the only remediation is a re-OAuth from the user; we mark
    the connection token_invalid=true and surface the reconnect banner.
    """

    def __init__(self, message: str = "OAuth token is invalid", *, provider: str | None = None):
        super().__init__(message)
        self.provider = provider
