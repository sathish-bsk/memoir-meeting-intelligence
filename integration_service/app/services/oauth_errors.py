"""Typed OAuth errors shared across calendar / CRM / recording services."""


class TokenInvalidError(Exception):
    """Raised when a stored OAuth refresh token is no longer accepted by the provider.

    Symptoms: Google returns `invalid_grant`, Microsoft returns
    `invalid_grant` / `AADSTS70008` (token expired). The connection row should
    be marked `token_invalid=true` and the user prompted to re-authenticate.

    Callers should catch this in the API layer (NOT the service layer) so they
    can flip the DB flag + commit + return a 401 with `{error: "token_invalid",
    provider: ..., reconnect_url: ...}` so the frontend can show a reconnect CTA.
    """

    def __init__(self, provider: str, reason: str = "invalid_grant"):
        super().__init__(f"{provider} OAuth token invalid: {reason}")
        self.provider = provider
        self.reason = reason
