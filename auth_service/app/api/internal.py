"""
Internal endpoints — service-to-service communication.
Protected by X-Service-Key header (or super admin JWT for admin endpoints).
Blocked at Traefik for external traffic.
"""

import json
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.plans import get_plan_limits, VALID_PLANS
from app.dependencies import (
    verify_internal_call,
    get_current_user,
    require_super_admin,
    CurrentUser,
)
from app.repositories.team_repo import TeamMemberRepository, TeamRepository
from app.repositories.user_repo import UserRepository
from app.schemas.permissions import (
    BatchCheckRequest,
    BatchCheckResponse,
    PermissionCheckRequest,
    PermissionCheckResponse,
)
from app.schemas.team import AdminTeamLookup, PlanInfo, UpdatePlanRequest
from app.services.permission_service import PermissionService


router = APIRouter(
    prefix="/auth/internal",
    tags=["internal"],
)


@router.get("/users/{user_id}", dependencies=[Depends(verify_internal_call)])
async def get_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Internal: get user info by ID (for background jobs)."""
    repo = UserRepository(db)
    user = await repo.get_by_id(uuid.UUID(user_id))
    if not user:
        raise HTTPException(404, "User not found")

    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "avatar_url": user.avatar_url,
        "is_active": user.is_active,
    }


@router.post("/users/batch", dependencies=[Depends(verify_internal_call)])
async def batch_get_users(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Internal: batch user lookup by IDs."""
    user_ids = body.get("user_ids", [])
    if not user_ids:
        return {"users": []}

    repo = UserRepository(db)
    users = await repo.find_users_by_ids(
        [uuid.UUID(uid) for uid in user_ids]
    )

    return {
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "name": u.name,
                "avatar_url": u.avatar_url,
            }
            for u in users
        ]
    }


@router.post("/teams/{team_id}/members/sync", dependencies=[Depends(verify_internal_call)])
async def sync_team_member(
    team_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Internal: sync a team member from core_api. Adds if not exists."""
    user_id = body.get("user_id")
    role = body.get("role", "member")
    if not user_id:
        raise HTTPException(400, "user_id is required")

    repo = TeamMemberRepository(db)
    existing = await repo.find_membership(uuid.UUID(team_id), uuid.UUID(user_id))
    if existing:
        return {"status": "already_exists", "member_id": str(existing.id)}

    member = await repo.add_member(
        team_id=uuid.UUID(team_id),
        user_id=uuid.UUID(user_id),
        role=role,
    )
    await db.commit()
    return {"status": "created", "member_id": str(member.id)}


@router.delete("/teams/{team_id}/members/sync", dependencies=[Depends(verify_internal_call)])
async def unsync_team_member(
    team_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Internal: remove a team member synced from core_api."""
    user_id = body.get("user_id")
    if not user_id:
        raise HTTPException(400, "user_id is required")

    repo = TeamMemberRepository(db)
    existing = await repo.find_membership(uuid.UUID(team_id), uuid.UUID(user_id))
    if not existing:
        return {"status": "not_found"}

    await repo.remove_member(uuid.UUID(team_id), uuid.UUID(user_id))
    await db.commit()
    return {"status": "removed"}


@router.get("/teams/{team_id}/members", dependencies=[Depends(verify_internal_call)])
async def get_team_members(
    team_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Internal: get team members list."""
    repo = TeamMemberRepository(db)
    user_repo = UserRepository(db)

    members = await repo.get_team_members(uuid.UUID(team_id))
    if not members:
        return {"members": []}

    user_ids = [m.user_id for m in members]
    users = await user_repo.find_users_by_ids(user_ids)
    user_map = {u.id: u for u in users}

    return {
        "members": [
            {
                "user_id": str(m.user_id),
                "email": user_map[m.user_id].email if m.user_id in user_map else "",
                "name": user_map[m.user_id].name if m.user_id in user_map else "",
                "role": m.role,
                "joined_at": m.joined_at.isoformat(),
            }
            for m in members
        ]
    }


@router.get("/users/check-email", dependencies=[Depends(verify_internal_call)])
async def check_email(
    email: str,
    db: AsyncSession = Depends(get_db),
):
    """Internal: check if an email exists and return basic info."""
    repo = UserRepository(db)
    user = await repo.find_by_email(email.strip().lower())
    if not user:
        return {"exists": False, "user_id": None, "name": None}
    return {"exists": True, "user_id": str(user.id), "name": user.name}


# ─── Permission Check Endpoints ─────────────────────────────────


@router.post("/permissions/check", response_model=PermissionCheckResponse, dependencies=[Depends(verify_internal_call)])
async def check_permission(
    body: PermissionCheckRequest,
    db: AsyncSession = Depends(get_db),
):
    """Internal: check if a user can perform an action on a resource."""
    svc = PermissionService(db)
    result = await svc.check_permission(
        user_id=body.user_id,
        team_id=body.team_id,
        team_role=body.team_role,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        action=body.action,
    )
    return PermissionCheckResponse(**result)


@router.post("/permissions/batch-check", response_model=BatchCheckResponse, dependencies=[Depends(verify_internal_call)])
async def batch_check_permissions(
    body: BatchCheckRequest,
    db: AsyncSession = Depends(get_db),
):
    """Internal: batch check permissions for multiple resources."""
    svc = PermissionService(db)
    result = await svc.batch_check(
        user_id=body.user_id,
        team_id=body.team_id,
        team_role=body.team_role,
        checks=[c.model_dump() for c in body.checks],
    )
    return BatchCheckResponse(**result)


# ─── Internal plan endpoint (service-to-service) ────────────────


@router.get("/teams/{team_id}/plan", response_model=PlanInfo, dependencies=[Depends(verify_internal_call)])
async def get_team_plan_internal(
    team_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Internal: get plan info for a team. Used by core_api for limit enforcement."""
    team_repo = TeamRepository(db)
    team = await team_repo.get_by_id(uuid.UUID(team_id))
    if not team:
        raise HTTPException(404, "Team not found")

    limits = get_plan_limits(team.plan, team.plan_limits)

    return PlanInfo(
        plan=team.plan,
        limits=limits,
        trial_ends_at=team.trial_ends_at,
    )


# ─── Super Admin endpoints (JWT-protected, not service key) ─────


@router.get("/admin/lookup-team", response_model=AdminTeamLookup)
async def admin_lookup_team(
    email: str,
    db: AsyncSession = Depends(get_db),
    admin: CurrentUser = Depends(require_super_admin),
):
    """Super admin: look up a user's team by email."""
    user_repo = UserRepository(db)
    user = await user_repo.find_by_email(email)
    if not user:
        raise HTTPException(404, "User not found")

    member_repo = TeamMemberRepository(db)
    membership = await member_repo.get_first_team_for_user(user.id)
    if not membership:
        raise HTTPException(404, "User has no team")

    team_repo = TeamRepository(db)
    team = await team_repo.get_by_id(membership.team_id)
    if not team:
        raise HTTPException(404, "Team not found")

    limits = get_plan_limits(team.plan, team.plan_limits)

    return AdminTeamLookup(
        user_id=str(user.id),
        user_name=user.name,
        user_email=user.email,
        team_id=str(team.id),
        team_name=team.name,
        plan=team.plan,
        plan_limits=limits,
        trial_ends_at=team.trial_ends_at,
    )


@router.put("/admin/update-plan")
async def admin_update_plan(
    body: UpdatePlanRequest,
    db: AsyncSession = Depends(get_db),
    admin: CurrentUser = Depends(require_super_admin),
):
    """Super admin: update a team's plan by user email."""
    if body.plan not in VALID_PLANS:
        raise HTTPException(400, f"Invalid plan. Must be one of: {', '.join(VALID_PLANS)}")

    user_repo = UserRepository(db)
    user = await user_repo.find_by_email(body.email)
    if not user:
        raise HTTPException(404, "User not found")

    member_repo = TeamMemberRepository(db)
    membership = await member_repo.get_first_team_for_user(user.id)
    if not membership:
        raise HTTPException(404, "User has no team")

    team_repo = TeamRepository(db)
    plan_limits_json = json.dumps(body.plan_limits) if body.plan_limits else None
    team = await team_repo.update_plan(
        team_id=membership.team_id,
        plan=body.plan,
        plan_limits=plan_limits_json,
    )
    if not team:
        raise HTTPException(404, "Team not found")

    await db.commit()

    limits = get_plan_limits(team.plan, team.plan_limits)
    return {
        "message": f"Plan updated to {body.plan}",
        "team_id": str(team.id),
        "team_name": team.name,
        "plan": team.plan,
        "limits": limits,
    }
