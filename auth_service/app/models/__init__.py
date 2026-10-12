"""
Import all models so Alembic can detect them for auto-migration.
"""

from app.models.base import TimestampMixin
from app.models.user import User, OAuthAccount
from app.models.token import RefreshToken, AuthToken
from app.models.team import Team, TeamMember
from app.models.audit import AuthAuditLog
from app.models.permission import ResourcePermission
from app.models.future_stubs import MFAMethod, SSOConnection, APIKey

__all__ = [
    "TimestampMixin",
    "User",
    "OAuthAccount",
    "RefreshToken",
    "AuthToken",
    "Team",
    "TeamMember",
    "AuthAuditLog",
    "ResourcePermission",
    "MFAMethod",
    "SSOConnection",
    "APIKey",
]
