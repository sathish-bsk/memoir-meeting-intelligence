"""
Audit log helpers for recording auth events.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuthAuditLog


async def log_event(
    db: AsyncSession,
    event_type: str,
    success: bool,
    user_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    metadata: Optional[dict] = None,
):
    """Record an audit log entry.

    Event types:
    - signup, login_success, login_failed, logout
    - password_change, password_reset_request, password_reset_complete
    - email_verify
    - oauth_login, oauth_link, oauth_unlink
    - token_refresh, token_revoke, token_family_revoke
    - team_create, team_invite, team_remove, team_role_change
    - session_revoke
    """
    entry = AuthAuditLog(
        user_id=uuid.UUID(user_id) if user_id else None,
        event_type=event_type,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata_json=json.dumps(metadata) if metadata else "{}",
        success=success,
    )
    db.add(entry)
    # Don't commit — let the caller's transaction handle it
