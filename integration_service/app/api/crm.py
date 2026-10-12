"""
CRM integration API routes — OAuth flows + internal push endpoint.
Public routes handle OAuth; internal routes called by core_api.
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.crm import CrmConnection
from app.services.hubspot_oauth import hubspot_oauth_service
from app.services.zoho_oauth import zoho_oauth_service

logger = logging.getLogger(__name__)

VALID_PROVIDERS = {"hubspot", "zoho"}

router = APIRouter(prefix="/integrations/crm", tags=["crm"])
internal_router = APIRouter(prefix="/integrations/internal/crm", tags=["crm-internal"])


# ─── Helpers ──────────────────────────────────────────────────────────

def _validate_provider(provider: str) -> str:
    if provider not in VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid CRM provider: {provider}. Must be one of {VALID_PROVIDERS}")
    return provider


def _get_redirect_uri(provider: str) -> str:
    return f"{settings.FRONTEND_URL}/integrations?code={{code}}&state={{state}}"


# ─── Public OAuth Routes ─────────────────────────────────────────────

@router.get("/{provider}/auth-url")
async def get_crm_auth_url(
    provider: str,
    user_id: str = Query(...),
    team_id: str = Query(...),
    region: str = Query("us"),
):
    """Get OAuth URL for a CRM provider."""
    _validate_provider(provider)

    state = f"{user_id}:{provider}:{uuid.uuid4().hex[:8]}"

    # Use the integrations page as redirect URI
    redirect_uri = f"{settings.FRONTEND_URL}/integrations"

    if provider == "hubspot":
        auth_url = hubspot_oauth_service.get_auth_url(redirect_uri, state)
    else:
        auth_url = zoho_oauth_service.get_auth_url(redirect_uri, state, region=region)

    return {"auth_url": auth_url, "state": state}


@router.post("/{provider}/callback")
async def crm_oauth_callback(
    provider: str,
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Handle OAuth callback, store tokens."""
    _validate_provider(provider)

    # Parse state: user_id:provider:random
    parts = state.split(":")
    if len(parts) < 3:
        raise HTTPException(400, "Invalid state parameter")

    user_id = parts[0]
    state_provider = parts[1]
    if state_provider != provider:
        raise HTTPException(400, "Provider mismatch in state")

    redirect_uri = f"{settings.FRONTEND_URL}/integrations"

    try:
        if provider == "hubspot":
            encrypted, portal_id, hub_domain = await hubspot_oauth_service.exchange_code_for_tokens(
                code, redirect_uri
            )

            # Check for existing connection for this team
            # We need team_id — pass it via a follow-up call from core_api
            # For now, the core_api proxy will supply team_id
            raise HTTPException(400, "Use core_api proxy for callback with team_id")

        else:
            # Zoho — determine accounts_url from state or default
            encrypted, org_id, org_name, api_domain, accounts_url = await zoho_oauth_service.exchange_code_for_tokens(
                code, redirect_uri
            )
            raise HTTPException(400, "Use core_api proxy for callback with team_id")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CRM OAuth callback failed for {provider}: {e}")
        raise HTTPException(502, f"Failed to connect {provider}: {str(e)}")


@router.post("/{provider}/connect")
async def connect_crm(
    provider: str,
    code: str = Query(...),
    state: str = Query(...),
    user_id: str = Query(...),
    team_id: str = Query(...),
    region: str = Query("us"),
    accounts_server: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    """Complete CRM OAuth connection. Called by core_api proxy after callback."""
    _validate_provider(provider)
    logger.info(f"CRM connect: provider={provider}, region={region}, accounts_server={accounts_server!r}")

    redirect_uri = f"{settings.FRONTEND_URL}/integrations"

    try:
        if provider == "hubspot":
            encrypted, portal_id, hub_domain = await hubspot_oauth_service.exchange_code_for_tokens(
                code, redirect_uri
            )
            provider_account_id = portal_id
            provider_account_name = hub_domain or f"HubSpot ({portal_id})"
            api_domain = None
            accounts_url = None
        else:
            # Prefer accounts_server from Zoho callback (most reliable), fall back to region-based
            if accounts_server:
                accounts_base = accounts_server
            else:
                region_info = {"us": "https://accounts.zoho.com", "eu": "https://accounts.zoho.eu", "in": "https://accounts.zoho.in", "au": "https://accounts.zoho.com.au", "jp": "https://accounts.zoho.jp", "ca": "https://accounts.zohocloud.ca"}
                accounts_base = region_info.get(region, "https://accounts.zoho.com")
            encrypted, org_id, org_name, api_domain, accounts_url = await zoho_oauth_service.exchange_code_for_tokens(
                code, redirect_uri, accounts_url=accounts_base
            )
            provider_account_id = org_id
            provider_account_name = org_name or f"Zoho ({org_id})"
    except Exception as e:
        logger.error(f"CRM token exchange failed for {provider}: {e}")
        raise HTTPException(502, f"Failed to connect {provider}: {str(e)}")

    # Upsert connection
    team_uuid = uuid.UUID(team_id)
    user_uuid = uuid.UUID(user_id)

    result = await db.execute(
        select(CrmConnection).where(
            CrmConnection.team_id == team_uuid,
            CrmConnection.provider == provider,
        )
    )
    connection = result.scalar_one_or_none()

    if connection:
        connection.encrypted_tokens = encrypted
        connection.user_id = user_uuid
        connection.provider_account_id = provider_account_id
        connection.provider_account_name = provider_account_name
        connection.api_domain = api_domain
        connection.accounts_url = accounts_url
        connection.is_active = True
        connection.connection_error = None
        connection.updated_at = datetime.now(timezone.utc)
    else:
        connection = CrmConnection(
            team_id=team_uuid,
            user_id=user_uuid,
            provider=provider,
            encrypted_tokens=encrypted,
            provider_account_id=provider_account_id,
            provider_account_name=provider_account_name,
            api_domain=api_domain,
            accounts_url=accounts_url,
        )
        db.add(connection)

    await db.commit()
    await db.refresh(connection)

    return {
        "status": "connected",
        "provider": provider,
        "account_id": provider_account_id,
        "account_name": provider_account_name,
    }


@router.delete("/{provider}/disconnect")
async def disconnect_crm(
    provider: str,
    user_id: str = Query(...),
    team_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect a CRM provider."""
    _validate_provider(provider)

    team_uuid = uuid.UUID(team_id)
    result = await db.execute(
        select(CrmConnection).where(
            CrmConnection.team_id == team_uuid,
            CrmConnection.provider == provider,
        )
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, f"No {provider} connection found")

    # Revoke token
    try:
        tokens = None
        if provider == "hubspot":
            tokens = hubspot_oauth_service.decrypt_tokens(connection.encrypted_tokens)
            await hubspot_oauth_service.revoke_token(tokens.get("refresh_token", ""))
        else:
            tokens = zoho_oauth_service.decrypt_tokens(connection.encrypted_tokens)
            await zoho_oauth_service.revoke_token(
                tokens.get("refresh_token", ""),
                connection.accounts_url or "https://accounts.zoho.com",
            )
    except Exception as e:
        logger.warning(f"Token revocation failed for {provider}: {e}")

    await db.delete(connection)
    await db.commit()

    return {"status": "disconnected", "provider": provider}


@router.get("/{provider}/status")
async def get_crm_status(
    provider: str,
    team_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Check CRM connection status for a team."""
    _validate_provider(provider)

    team_uuid = uuid.UUID(team_id)
    result = await db.execute(
        select(CrmConnection).where(
            CrmConnection.team_id == team_uuid,
            CrmConnection.provider == provider,
        )
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, f"No {provider} connection found")

    return {
        "provider": connection.provider,
        "account_id": connection.provider_account_id,
        "account_name": connection.provider_account_name,
        "is_active": connection.is_active,
        "auto_push_enabled": connection.auto_push_enabled,
        "last_sync_at": connection.last_sync_at.isoformat() if connection.last_sync_at else None,
        "connection_error": connection.connection_error,
        "field_mapping": connection.field_mapping,
        "stage_mapping": connection.stage_mapping,
        "connected_at": connection.created_at.isoformat() if connection.created_at else None,
    }


class CrmSettingsUpdate(BaseModel):
    auto_push_enabled: bool | None = None


@router.put("/{provider}/settings")
async def update_crm_settings(
    provider: str,
    body: CrmSettingsUpdate,
    team_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Update CRM connection settings (auto_push toggle)."""
    _validate_provider(provider)

    team_uuid = uuid.UUID(team_id)
    result = await db.execute(
        select(CrmConnection).where(CrmConnection.team_id == team_uuid, CrmConnection.provider == provider)
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, f"No {provider} connection found")

    if body.auto_push_enabled is not None:
        connection.auto_push_enabled = body.auto_push_enabled
    connection.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return {"status": "updated", "auto_push_enabled": connection.auto_push_enabled}


@router.get("/{provider}/properties")
async def get_crm_properties(
    provider: str,
    team_id: str = Query(...),
    object_type: str = Query("contacts"),
    db: AsyncSession = Depends(get_db),
):
    """Fetch CRM field list for field mapping UI."""
    _validate_provider(provider)

    team_uuid = uuid.UUID(team_id)
    result = await db.execute(
        select(CrmConnection).where(
            CrmConnection.team_id == team_uuid,
            CrmConnection.provider == provider,
        )
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, f"No {provider} connection found")

    try:
        if provider == "hubspot":
            access_token, new_encrypted = await hubspot_oauth_service.get_valid_access_token(
                connection.encrypted_tokens
            )
            if new_encrypted:
                connection.encrypted_tokens = new_encrypted
                await db.commit()

            from app.services.hubspot_client import HubSpotClient
            client = HubSpotClient(access_token)
            if object_type == "deals":
                return {"properties": await client.get_deal_properties()}
            return {"properties": await client.get_contact_properties()}
        else:
            access_token, new_encrypted = await zoho_oauth_service.get_valid_access_token(
                connection.encrypted_tokens,
                connection.accounts_url or "https://accounts.zoho.com",
            )
            if new_encrypted:
                connection.encrypted_tokens = new_encrypted
                await db.commit()

            from app.services.zoho_client import ZohoClient
            client = ZohoClient(access_token, connection.api_domain or "https://www.zohoapis.com")
            module = "Deals" if object_type == "deals" else "Contacts"
            return {"properties": await client.get_module_fields(module)}
    except Exception as e:
        logger.error(f"Failed to fetch {provider} properties: {e}")
        raise HTTPException(502, f"Failed to fetch {provider} properties: {str(e)}")


@router.get("/{provider}/stages")
async def get_crm_stages(
    provider: str,
    team_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Fetch CRM pipeline stages for stage mapping UI."""
    _validate_provider(provider)

    team_uuid = uuid.UUID(team_id)
    result = await db.execute(
        select(CrmConnection).where(
            CrmConnection.team_id == team_uuid,
            CrmConnection.provider == provider,
        )
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, f"No {provider} connection found")

    try:
        if provider == "hubspot":
            access_token, new_encrypted = await hubspot_oauth_service.get_valid_access_token(
                connection.encrypted_tokens
            )
            if new_encrypted:
                connection.encrypted_tokens = new_encrypted
                await db.commit()

            from app.services.hubspot_client import HubSpotClient
            client = HubSpotClient(access_token)
            return {"stages": await client.get_pipeline_stages()}
        else:
            access_token, new_encrypted = await zoho_oauth_service.get_valid_access_token(
                connection.encrypted_tokens,
                connection.accounts_url or "https://accounts.zoho.com",
            )
            if new_encrypted:
                connection.encrypted_tokens = new_encrypted
                await db.commit()

            from app.services.zoho_client import ZohoClient
            client = ZohoClient(access_token, connection.api_domain or "https://www.zohoapis.com")
            return {"stages": await client.get_deal_stages()}
    except Exception as e:
        logger.error(f"Failed to fetch {provider} stages: {e}")
        raise HTTPException(502, f"Failed to fetch {provider} stages: {str(e)}")


# ─── Internal Routes (called by core_api) ────────────────────────────

class CrmPushRequest(BaseModel):
    team_id: str
    meeting_id: str
    provider: str
    crm_extraction: dict
    call_metadata: dict  # title, duration, url, timestamp
    edited_data: dict | None = None


class CrmPushResponse(BaseModel):
    status: str
    crm_contact_id: str | None = None
    crm_deal_id: str | None = None
    crm_record_id: str | None = None
    crm_record_url: str | None = None
    pushed_data: dict = {}
    error_message: str | None = None


@internal_router.post("/push")
async def internal_push_to_crm(
    body: CrmPushRequest,
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Execute CRM push. Called by core_api after transcription_complete."""
    if not x_service_key or x_service_key != settings.INTERNAL_SERVICE_KEY:
        raise HTTPException(403, "Invalid or missing service key")

    _validate_provider(body.provider)

    team_uuid = uuid.UUID(body.team_id)
    result = await db.execute(
        select(CrmConnection).where(
            CrmConnection.team_id == team_uuid,
            CrmConnection.provider == body.provider,
        )
    )
    connection = result.scalar_one_or_none()
    if not connection:
        return CrmPushResponse(status="no_connection", error_message=f"No {body.provider} connection for team")

    if not connection.is_active:
        return CrmPushResponse(status="inactive", error_message=f"{body.provider} connection is inactive")

    try:
        # Refresh token if needed
        if body.provider == "hubspot":
            access_token, new_encrypted = await hubspot_oauth_service.get_valid_access_token(
                connection.encrypted_tokens
            )
        else:
            access_token, new_encrypted = await zoho_oauth_service.get_valid_access_token(
                connection.encrypted_tokens,
                connection.accounts_url or "https://accounts.zoho.com",
            )

        if new_encrypted:
            connection.encrypted_tokens = new_encrypted
            connection.updated_at = datetime.now(timezone.utc)
            await db.commit()

        # Execute push
        from app.services.crm_push_service import execute_crm_push
        push_result = await execute_crm_push(
            provider=body.provider,
            access_token=access_token,
            api_domain=connection.api_domain,
            crm_extraction=body.edited_data or body.crm_extraction,
            call_metadata=body.call_metadata,
            field_mapping=connection.field_mapping or {},
            stage_mapping=connection.stage_mapping or {},
        )

        # Update last_sync_at
        connection.last_sync_at = datetime.now(timezone.utc)
        connection.connection_error = None
        await db.commit()

        return push_result

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        error_msg = f"CRM API error ({status_code}): {e.response.text[:500]}"
        logger.error(f"CRM push failed for {body.provider}: {error_msg}")

        if status_code in (401, 403):
            connection.is_active = False
            connection.connection_error = "Authentication failed — please reconnect"
            await db.commit()

        return CrmPushResponse(status="failed", error_message=error_msg)

    except Exception as e:
        error_msg = f"CRM push error: {str(e)}"
        logger.error(f"CRM push failed for {body.provider}: {error_msg}")
        return CrmPushResponse(status="failed", error_message=error_msg)


class MappingSaveRequest(BaseModel):
    mapping: dict


@router.put("/{provider}/field-mapping")
async def save_field_mapping(
    provider: str,
    body: MappingSaveRequest,
    team_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Save field mapping for a CRM provider."""
    _validate_provider(provider)
    team_uuid = uuid.UUID(team_id)
    result = await db.execute(
        select(CrmConnection).where(CrmConnection.team_id == team_uuid, CrmConnection.provider == provider)
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, f"No {provider} connection found")

    connection.field_mapping = body.mapping
    connection.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "saved", "field_mapping": connection.field_mapping}


@router.put("/{provider}/stage-mapping")
async def save_stage_mapping(
    provider: str,
    body: MappingSaveRequest,
    team_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Save stage mapping for a CRM provider."""
    _validate_provider(provider)
    team_uuid = uuid.UUID(team_id)
    result = await db.execute(
        select(CrmConnection).where(CrmConnection.team_id == team_uuid, CrmConnection.provider == provider)
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, f"No {provider} connection found")

    connection.stage_mapping = body.mapping
    connection.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "saved", "stage_mapping": connection.stage_mapping}


@internal_router.get("/search-deals")
async def search_deals(
    team_id: str = Query(...),
    provider: str = Query(...),
    company: str = Query(None),
    email: str = Query(None),
    query: str = Query(None),
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Search for CRM deals (called by core_api for deal matching at push time)."""
    if not x_service_key or x_service_key != settings.INTERNAL_SERVICE_KEY:
        raise HTTPException(403, "Invalid or missing service key")

    _validate_provider(provider)

    team_uuid = uuid.UUID(team_id)
    result = await db.execute(
        select(CrmConnection).where(
            CrmConnection.team_id == team_uuid,
            CrmConnection.provider == provider,
        )
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, f"No {provider} connection found for team")

    if not connection.is_active:
        raise HTTPException(400, f"{provider} connection is inactive")

    try:
        if provider == "hubspot":
            access_token, new_encrypted = await hubspot_oauth_service.get_valid_access_token(
                connection.encrypted_tokens
            )
            if new_encrypted:
                connection.encrypted_tokens = new_encrypted
                await db.commit()

            from app.services.hubspot_client import HubSpotClient
            client = HubSpotClient(access_token)
        else:
            access_token, new_encrypted = await zoho_oauth_service.get_valid_access_token(
                connection.encrypted_tokens,
                connection.accounts_url or "https://accounts.zoho.com",
            )
            if new_encrypted:
                connection.encrypted_tokens = new_encrypted
                await db.commit()

            from app.services.zoho_client import ZohoClient
            client = ZohoClient(access_token, connection.api_domain or "https://www.zohoapis.com")

        deals = await client.search_deals(query=query, company=company, email=email)
        return {"deals": deals, "provider": provider}

    except Exception as e:
        logger.error(f"Failed to search {provider} deals: {e}")
        raise HTTPException(502, f"Failed to search {provider} deals: {str(e)}")


@internal_router.get("/connection")
async def internal_get_connection(
    team_id: str = Query(...),
    provider: str = Query(None),
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Check CRM connection status. Called by core_api."""
    if not x_service_key or x_service_key != settings.INTERNAL_SERVICE_KEY:
        raise HTTPException(403, "Invalid or missing service key")

    team_uuid = uuid.UUID(team_id)
    query = select(CrmConnection).where(CrmConnection.team_id == team_uuid)
    if provider:
        query = query.where(CrmConnection.provider == provider)

    result = await db.execute(query)
    connections = result.scalars().all()

    return {
        "connections": [
            {
                "provider": c.provider,
                "account_id": c.provider_account_id,
                "account_name": c.provider_account_name,
                "is_active": c.is_active,
                "auto_push_enabled": c.auto_push_enabled,
                "connection_error": c.connection_error,
            }
            for c in connections
        ]
    }


@router.get("/contacts")
async def internal_list_contacts(
    team_id: str = Query(...),
    provider: str = Query(None),
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
    db: AsyncSession = Depends(get_db),
):
    """List contacts (names + emails) from connected CRMs for ASR context.

    Fetches recent contacts from one or all connected providers.
    Used for Whisper prompt conditioning, post-ASR name correction,
    and email reconstruction.
    """
    if not x_service_key or x_service_key != settings.INTERNAL_SERVICE_KEY:
        raise HTTPException(403, "Invalid or missing service key")

    team_uuid = uuid.UUID(team_id)
    query = select(CrmConnection).where(
        CrmConnection.team_id == team_uuid,
        CrmConnection.is_active == True,
    )
    if provider:
        query = query.where(CrmConnection.provider == provider)

    result = await db.execute(query)
    connections = result.scalars().all()

    all_contacts = []
    for connection in connections:
        try:
            if connection.provider == "hubspot":
                access_token, new_encrypted = await hubspot_oauth_service.get_valid_access_token(
                    connection.encrypted_tokens
                )
                if new_encrypted:
                    connection.encrypted_tokens = new_encrypted
                    connection.updated_at = datetime.now(timezone.utc)
                    await db.commit()
                from app.services.hubspot_client import HubSpotClient
                client = HubSpotClient(access_token)
                contacts = await client.list_recent_contacts(limit=100)
                all_contacts.extend(contacts)

            elif connection.provider == "zoho":
                access_token, new_encrypted = await zoho_oauth_service.get_valid_access_token(
                    connection.encrypted_tokens,
                    connection.accounts_url or "https://accounts.zoho.com",
                )
                if new_encrypted:
                    connection.encrypted_tokens = new_encrypted
                    connection.updated_at = datetime.now(timezone.utc)
                    await db.commit()
                from app.services.zoho_client import ZohoClient
                client = ZohoClient(access_token, connection.api_domain or "https://www.zohoapis.com")
                contacts = await client.list_recent_contacts(limit=100)
                all_contacts.extend(contacts)

        except Exception as e:
            logger.warning(f"Failed to list contacts from {connection.provider} for team {team_id}: {e}")

    # Dedup by name, preserving order
    seen = set()
    unique = []
    for contact in all_contacts:
        name = contact.get("name", "") if isinstance(contact, dict) else contact
        if name and name not in seen:
            seen.add(name)
            unique.append(contact)

    return {"contacts": unique[:200]}
