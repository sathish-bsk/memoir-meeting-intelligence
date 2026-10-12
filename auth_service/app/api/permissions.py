"""
User-facing resource permission routes — sharing (Google Docs pattern).
"""

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import has_scope
from app.dependencies import get_current_user, CurrentUser
from app.schemas.common import MessageResponse
from app.schemas.permissions import (
    AccessibleResourceResponse,
    GrantPermissionRequest,
    PermissionEntryResponse,
    UpdatePermissionRequest,
)
from app.services.permission_service import PermissionService


router = APIRouter(prefix="/auth/resources", tags=["permissions"])


@router.get(
    "/{resource_type}/{resource_id}/permissions",
    response_model=List[PermissionEntryResponse],
)
async def list_resource_permissions(
    resource_type: str,
    resource_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """List who has access to a resource and their actions."""
    # User needs share scope on this resource type to view permissions
    scope = f"{resource_type}s:share"
    if not has_scope(user.team_role, scope):
        from fastapi import HTTPException
        raise HTTPException(403, f"Requires {scope} scope")

    svc = PermissionService(db)
    entries = await svc.list_permissions(resource_type, resource_id)
    return [PermissionEntryResponse(**e) for e in entries]


@router.post(
    "/{resource_type}/{resource_id}/permissions",
    response_model=List[PermissionEntryResponse],
)
async def grant_resource_permission(
    resource_type: str,
    resource_id: str,
    body: GrantPermissionRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Grant access to a resource (preset or custom actions)."""
    scope = f"{resource_type}s:share"
    if not has_scope(user.team_role, scope):
        from fastapi import HTTPException
        raise HTTPException(403, f"Requires {scope} scope")

    if not user.team_id:
        from fastapi import HTTPException
        raise HTTPException(400, "No active team")

    svc = PermissionService(db)
    actions = set(body.actions) if body.actions else None

    entries = await svc.grant_access(
        team_id=user.team_id,
        resource_type=resource_type,
        resource_id=resource_id,
        subject_type=body.subject_type,
        subject_id=body.subject_id,
        actions=actions,
        preset=body.preset,
        granted_by=user.user_id,
    )
    return [PermissionEntryResponse(**e) for e in entries]


@router.patch(
    "/{resource_type}/{resource_id}/permissions/{permission_id}",
    response_model=List[PermissionEntryResponse],
)
async def update_resource_permission(
    resource_type: str,
    resource_id: str,
    permission_id: str,
    body: UpdatePermissionRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Update granted actions for an existing permission."""
    scope = f"{resource_type}s:share"
    if not has_scope(user.team_role, scope):
        from fastapi import HTTPException
        raise HTTPException(403, f"Requires {scope} scope")

    if not user.team_id:
        from fastapi import HTTPException
        raise HTTPException(400, "No active team")

    svc = PermissionService(db)
    actions = set(body.actions) if body.actions else None

    entries = await svc.update_access(
        permission_id=permission_id,
        team_id=user.team_id,
        resource_type=resource_type,
        resource_id=resource_id,
        actions=actions,
        preset=body.preset,
    )
    return [PermissionEntryResponse(**e) for e in entries]


@router.delete(
    "/{resource_type}/{resource_id}/permissions/{permission_id}",
    response_model=MessageResponse,
)
async def revoke_resource_permission(
    resource_type: str,
    resource_id: str,
    permission_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Revoke access — removes all actions for the subject on this resource."""
    scope = f"{resource_type}s:share"
    if not has_scope(user.team_role, scope):
        from fastapi import HTTPException
        raise HTTPException(403, f"Requires {scope} scope")

    if not user.team_id:
        from fastapi import HTTPException
        raise HTTPException(400, "No active team")

    svc = PermissionService(db)
    await svc.revoke_access(
        permission_id=permission_id,
        team_id=user.team_id,
    )
    return MessageResponse(message="Access revoked")


@router.get("/accessible", response_model=List[AccessibleResourceResponse])
async def list_accessible_resources(
    type: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """List resources the user has explicit permissions on."""
    if not user.team_id:
        from fastapi import HTTPException
        raise HTTPException(400, "No active team")

    svc = PermissionService(db)
    resources = await svc.list_accessible(
        user_id=user.user_id,
        team_id=user.team_id,
        resource_type=type,
    )
    return [AccessibleResourceResponse(**r) for r in resources]
