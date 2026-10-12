"""
Refresh token generation and hashing utilities.
Token rotation logic lives in the repository/service layer.
"""

import hashlib
import secrets


def generate_refresh_token() -> str:
    """Generate a cryptographically secure refresh token (URL-safe, 64 bytes)."""
    return secrets.token_urlsafe(64)


def hash_token(token: str) -> str:
    """Hash a token using SHA-256 for storage. Never store raw tokens."""
    return hashlib.sha256(token.encode()).hexdigest()
