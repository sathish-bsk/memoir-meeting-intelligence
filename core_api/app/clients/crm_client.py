"""
CRM client — calls integration_service internal CRM endpoints via httpx.
Follows the same pattern as slack_client.py.
"""

import logging
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

INTEGRATION_URL = settings.INTEGRATION_SERVICE_URL.rstrip("/")
TIMEOUT = 30  # CRM pushes can be slow (multiple API calls)


async def push_to_crm(
    team_id: str,
    meeting_id: str,
    provider: str,
    crm_extraction: dict,
    call_metadata: dict,
    edited_data: dict | None = None,
) -> dict:
    """Push data to a CRM provider via integration_service.

    Returns dict with status, crm_contact_id, crm_deal_id, crm_record_id, etc.
    """
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{INTEGRATION_URL}/integrations/internal/crm/push",
                json={
                    "team_id": team_id,
                    "meeting_id": meeting_id,
                    "provider": provider,
                    "crm_extraction": crm_extraction,
                    "call_metadata": call_metadata,
                    "edited_data": edited_data,
                },
                headers={"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"CRM push HTTP error for {provider}: {e.response.status_code} — {e.response.text[:300]}")
        return {"status": "failed", "error_message": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        logger.error(f"CRM push failed for {provider}: {e}")
        return {"status": "failed", "error_message": str(e)}


async def get_crm_connections(team_id: str, provider: Optional[str] = None) -> list[dict]:
    """Check which CRM providers are connected for a team."""
    try:
        params = {"team_id": team_id}
        if provider:
            params["provider"] = provider

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{INTEGRATION_URL}/integrations/internal/crm/connection",
                params=params,
                headers={"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
            )
            resp.raise_for_status()
            return resp.json().get("connections", [])
    except Exception as e:
        logger.error(f"Failed to get CRM connections: {e}")
        return []


async def list_crm_contacts(team_id: str, provider: Optional[str] = None) -> list[dict]:
    """Fetch contacts (names + emails) from connected CRMs via integration_service.

    Returns list of dicts with "name" and "email" keys for ASR context
    (Whisper prompt conditioning, post-ASR name correction, and email
    reconstruction). Returns empty list if no CRM is connected or if
    the call fails.
    """
    try:
        params = {"team_id": team_id}
        if provider:
            params["provider"] = provider

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{INTEGRATION_URL}/integrations/internal/crm/contacts",
                params=params,
                headers={"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
            )
            resp.raise_for_status()
            return resp.json().get("contacts", [])
    except Exception as e:
        logger.warning(f"Failed to fetch CRM contacts: {e}")
        return []
