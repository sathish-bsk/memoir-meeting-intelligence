"""
JWT decode (ES256 public key) and UserContext for downstream services.

Matches the token format created by auth_service/app/core/security.py.
Only needs the public key — never the private key.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import Depends, HTTPException, Request
from jose import JWTError, jwt


_public_key: Optional[str] = None


def _get_public_key(path: str) -> str:
    """Load and cache the ES256 public key."""
    global _public_key
    if _public_key is None:
        _public_key = Path(path).read_text().strip()
    return _public_key


@dataclass
class UserContext:
    """User context extracted from ForwardAuth headers or JWT."""

    user_id: str
    email: str
    name: str
    team_id: Optional[str] = None
    team_role: Optional[str] = None
    plan: Optional[str] = None
    is_super_admin: bool = False


def decode_jwt(
    token: str,
    public_key_path: str = "./keys/public.pem",
    issuer: str = "videoclip-auth",
    audience: str = "videoclip",
    algorithm: str = "ES256",
) -> Optional[dict]:
    """Decode and verify an ES256 JWT. Returns payload or None."""
    try:
        key = _get_public_key(public_key_path)
        return jwt.decode(
            token,
            key,
            algorithms=[algorithm],
            audience=audience,
            issuer=issuer,
        )
    except JWTError:
        return None


def get_current_user(request: Request) -> UserContext:
    """FastAPI dependency: extract user from ForwardAuth headers or JWT fallback.

    In production, Traefik's ForwardAuth sets X-User-* headers after
    calling /auth/verify. For direct calls, falls back to JWT decode.
    """
    user_id = request.headers.get("X-User-ID")

    if user_id:
        return UserContext(
            user_id=user_id,
            email=request.headers.get("X-User-Email", ""),
            name=request.headers.get("X-User-Name", ""),
            team_id=request.headers.get("X-Team-ID"),
            team_role=request.headers.get("X-Team-Role"),
            plan=request.headers.get("X-Plan"),
            is_super_admin=request.headers.get("X-Is-Super-Admin", "").lower() == "true",
        )

    # Fallback: decode JWT directly
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")

    token = auth_header[7:]

    # Read settings from app state if available, otherwise use defaults
    settings = getattr(request.app.state, "settings", None)
    public_key_path = getattr(settings, "JWT_PUBLIC_KEY_PATH", "./keys/public.pem")
    issuer = getattr(settings, "JWT_ISSUER", "videoclip-auth")
    audience = getattr(settings, "JWT_AUDIENCE", "videoclip")
    algorithm = getattr(settings, "JWT_ALGORITHM", "ES256")

    payload = decode_jwt(token, public_key_path, issuer, audience, algorithm)
    if payload is None:
        raise HTTPException(401, "Invalid or expired token")

    return UserContext(
        user_id=payload["sub"],
        email=payload.get("email", ""),
        name=payload.get("name", ""),
        team_id=payload.get("team_id"),
        team_role=payload.get("team_role"),
        plan=payload.get("plan"),
        is_super_admin=bool(payload.get("is_super_admin", False)),
    )


def require_super_admin(user: UserContext = Depends(get_current_user)) -> UserContext:
    """FastAPI dependency: ensures the caller is a super admin."""
    if not user.is_super_admin:
        raise HTTPException(403, "Super admin access required")
    return user
