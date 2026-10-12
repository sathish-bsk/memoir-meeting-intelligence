"""
Internal service-to-service authentication via X-Service-Key header.
Matches auth_service/app/dependencies.py verify_internal_call().
"""

import os
from typing import Optional

from fastapi import Header, HTTPException


async def verify_internal_call(
    x_service_key: Optional[str] = Header(default=None, alias="X-Service-Key"),
):
    """Verify that the caller is an internal service.

    Usage:
        @router.post("/internal/webhooks/bot-events")
        async def webhook(
            payload: dict,
            _: None = Depends(verify_internal_call),
        ):
            ...
    """
    expected = os.getenv("INTERNAL_SERVICE_KEY", "")
    if not x_service_key:
        raise HTTPException(403, "Missing service key")
    if not expected:
        raise HTTPException(500, "Internal service key not configured")
    if x_service_key != expected:
        raise HTTPException(403, "Invalid service key")
