"""
ES256 JWT key management and token creation/verification.
"""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from jose import JWTError, jwt

from app.core.config import settings
from app.core.logging import app_logger


# ─── Key Management ───────────────────────────────────────────────

_private_key: Optional[str] = None
_public_key: Optional[str] = None


def _load_key(path: str) -> str:
    """Load a PEM key from file."""
    return Path(path).read_text().strip()


def get_private_key() -> str:
    """Get the ES256 private key (PEM string). Cached after first load."""
    global _private_key
    if _private_key is None:
        _private_key = _load_key(settings.JWT_PRIVATE_KEY_PATH)
    return _private_key


def get_public_key() -> str:
    """Get the ES256 public key (PEM string). Cached after first load."""
    global _public_key
    if _public_key is None:
        _public_key = _load_key(settings.JWT_PUBLIC_KEY_PATH)
    return _public_key


def ensure_keypair():
    """Generate ES256 keypair if files don't exist. Dev convenience only."""
    priv_path = Path(settings.JWT_PRIVATE_KEY_PATH)
    pub_path = Path(settings.JWT_PUBLIC_KEY_PATH)

    if priv_path.exists() and pub_path.exists():
        return

    app_logger.info("Generating ES256 keypair for development")

    priv_path.parent.mkdir(parents=True, exist_ok=True)
    pub_path.parent.mkdir(parents=True, exist_ok=True)

    private_key = ec.generate_private_key(ec.SECP256R1())

    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)

    app_logger.info(f"ES256 keypair written to {priv_path} and {pub_path}")


# ─── JWT Creation ─────────────────────────────────────────────────

def create_access_token(
    user_id: str,
    email: str,
    name: str,
    token_version: int,
    team_id: Optional[str] = None,
    team_role: Optional[str] = None,
    plan: Optional[str] = None,
    onboarding_completed: bool = False,
    is_super_admin: bool = False,
    scope_overrides: Optional[str] = None,
) -> str:
    """Create a signed ES256 JWT access token (short-lived)."""
    now = datetime.now(timezone.utc)
    payload = {
        "iss": settings.JWT_ISSUER,
        "sub": user_id,
        "aud": settings.JWT_AUDIENCE,
        "exp": now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": now,
        "jti": str(uuid.uuid4()),
        "email": email,
        "name": name,
        "tv": token_version,
    }

    if team_id:
        payload["team_id"] = team_id
    if team_role:
        payload["team_role"] = team_role
    if plan:
        payload["plan"] = plan
    if is_super_admin:
        payload["is_super_admin"] = True
    if scope_overrides:
        payload["so"] = scope_overrides

    payload["onboarding_completed"] = onboarding_completed

    return jwt.encode(payload, get_private_key(), algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and verify an ES256 JWT access token.

    Returns the payload dict on success, None on failure.
    """
    try:
        payload = jwt.decode(
            token,
            get_public_key(),
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
        return payload
    except JWTError as e:
        app_logger.debug(f"JWT decode failed: {e}")
        return None
