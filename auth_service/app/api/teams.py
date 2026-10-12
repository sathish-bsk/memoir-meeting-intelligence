"""
Team management routes — CRUD, invite, remove, role change.
"""

import json
import uuid as _uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.plans import get_plan_limits, VALID_PLANS
from app.dependencies import get_current_user, CurrentUser
from app.schemas.common import MessageResponse
from app.schemas.team import (
    ChangeMemberRoleRequest,
    CreateTeamRequest,
    InviteMemberRequest,
    MemberScopesResponse,
    PlanInfo,
    ScopeOverridesRequest,
    TeamDetailResponse,
    TeamResponse,
    UpdateTeamRequest,
)
from app.services.team_service import TeamService


router = APIRouter(prefix="/auth/teams", tags=["teams"])


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("", response_model=TeamResponse)
async def create_team(
    body: CreateTeamRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Create a new team."""
    svc = TeamService(db)
    ip = _get_client_ip(request)

    team = await svc.create_team(
        name=body.name,
        slug=body.slug,
        created_by=user.user_id,
        ip_address=ip,
    )

    return TeamResponse(
        id=str(team.id),
        name=team.name,
        slug=team.slug,
        created_by=str(team.created_by),
        member_count=1,
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


@router.get("", response_model=List[TeamResponse])
async def list_teams(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """List all teams the current user belongs to."""
    svc = TeamService(db)
    teams = await svc.get_user_teams(user.user_id)
    return [TeamResponse(**t) for t in teams]


@router.get("/{team_id}", response_model=TeamDetailResponse)
async def get_team(
    team_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Get team details with members."""
    svc = TeamService(db)
    detail = await svc.get_team_detail(team_id=team_id, user_id=user.user_id)
    return TeamDetailResponse(**detail)


@router.patch("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: str,
    body: UpdateTeamRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Update team name."""
    svc = TeamService(db)

    # Verify membership and get current team
    detail = await svc.get_team_detail(team_id=team_id, user_id=user.user_id)

    # Check role — requires team:manage scope (admin+)
    from app.core.permissions import has_scope
    import uuid

    from app.repositories.team_repo import TeamMemberRepository

    member_repo = TeamMemberRepository(db)
    membership = await member_repo.find_membership(
        uuid.UUID(team_id), uuid.UUID(user.user_id)
    )
    if not membership or not has_scope(membership.role, "team:manage", membership.scope_overrides):
        from fastapi import HTTPException
        raise HTTPException(403, "Requires team:manage permission")

    from app.repositories.team_repo import TeamRepository

    team_repo = TeamRepository(db)
    team = await team_repo.get_by_id(uuid.UUID(team_id))
    if body.name:
        team.name = body.name
    await db.commit()

    count = await team_repo.get_member_count(team.id)

    return TeamResponse(
        id=str(team.id),
        name=team.name,
        slug=team.slug,
        created_by=str(team.created_by),
        member_count=count,
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


@router.post("/{team_id}/invite", response_model=MessageResponse)
async def invite_member(
    team_id: str,
    body: InviteMemberRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Invite a user to the team by email."""
    svc = TeamService(db)
    ip = _get_client_ip(request)

    await svc.invite_member(
        team_id=team_id,
        email=body.email,
        role=body.role,
        invited_by=user.user_id,
        ip_address=ip,
    )

    return MessageResponse(message=f"Invited {body.email} as {body.role}")


@router.delete("/{team_id}/members/{member_user_id}", response_model=MessageResponse)
async def remove_member(
    team_id: str,
    member_user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Remove a member from the team."""
    svc = TeamService(db)
    ip = _get_client_ip(request)

    await svc.remove_member(
        team_id=team_id,
        target_user_id=member_user_id,
        removed_by=user.user_id,
        ip_address=ip,
    )

    return MessageResponse(message="Member removed")


@router.patch(
    "/{team_id}/members/{member_user_id}/role",
    response_model=MessageResponse,
)
async def change_member_role(
    team_id: str,
    member_user_id: str,
    body: ChangeMemberRoleRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Change a member's role."""
    svc = TeamService(db)
    ip = _get_client_ip(request)

    await svc.change_member_role(
        team_id=team_id,
        target_user_id=member_user_id,
        new_role=body.role,
        changed_by=user.user_id,
        ip_address=ip,
    )

    return MessageResponse(message=f"Role changed to {body.role}")


@router.get("/{team_id}/plan", response_model=PlanInfo)
async def get_team_plan(
    team_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Get team plan and resolved limits. Requires membership."""
    svc = TeamService(db)
    # Verify membership
    await svc.get_team_detail(team_id=team_id, user_id=user.user_id)

    import uuid as _uuid
    from app.repositories.team_repo import TeamRepository

    team_repo = TeamRepository(db)
    team = await team_repo.get_by_id(_uuid.UUID(team_id))
    if not team:
        from fastapi import HTTPException
        raise HTTPException(404, "Team not found")

    limits = get_plan_limits(team.plan, team.plan_limits)

    return PlanInfo(
        plan=team.plan,
        limits=limits,
        trial_ends_at=team.trial_ends_at,
    )


# ─── Scope Override Endpoints ──────────────────────────────────────

# Valid module scopes that can be overridden
_OVERRIDABLE_SCOPES = frozenset(
    f"{module}:{action}"
    for module in ("calls", "intelligence")
    for action in ("read", "write", "delete")
) | {"calls:share"}


@router.get(
    "/{team_id}/members/{member_user_id}/scopes",
    response_model=MemberScopesResponse,
)
async def get_member_scopes(
    team_id: str,
    member_user_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Get a member's effective scopes, role defaults, and overrides. Requires team:members:role scope."""
    from app.core.permissions import get_effective_scopes, get_scopes_for_role, has_scope
    from app.repositories.team_repo import TeamMemberRepository

    tid = _uuid.UUID(team_id)
    member_repo = TeamMemberRepository(db)

    # Verify caller has team:members:role scope
    caller_membership = await member_repo.find_membership(tid, _uuid.UUID(user.user_id))
    if not caller_membership or not has_scope(caller_membership.role, "team:members:role", caller_membership.scope_overrides):
        raise HTTPException(403, "Requires team:members:role permission")

    # Get target member
    target_membership = await member_repo.find_membership(tid, _uuid.UUID(member_user_id))
    if not target_membership:
        raise HTTPException(404, "User is not a member of this team")

    role_defaults = sorted(get_scopes_for_role(target_membership.role))
    overrides = json.loads(target_membership.scope_overrides) if target_membership.scope_overrides else {}
    effective = sorted(get_effective_scopes(target_membership.role, target_membership.scope_overrides))

    return MemberScopesResponse(
        effective_scopes=effective,
        role_defaults=role_defaults,
        overrides=overrides,
    )


@router.patch(
    "/{team_id}/members/{member_user_id}/scopes",
    response_model=MemberScopesResponse,
)
async def update_member_scopes(
    team_id: str,
    member_user_id: str,
    body: ScopeOverridesRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Update a member's scope overrides. Requires team:members:role scope."""
    from app.core.permissions import get_effective_scopes, get_scopes_for_role, has_scope
    from app.repositories.team_repo import TeamMemberRepository

    tid = _uuid.UUID(team_id)
    member_repo = TeamMemberRepository(db)

    # Verify caller has team:members:role scope
    caller_membership = await member_repo.find_membership(tid, _uuid.UUID(user.user_id))
    if not caller_membership or not has_scope(caller_membership.role, "team:members:role", caller_membership.scope_overrides):
        raise HTTPException(403, "Requires team:members:role permission")

    # Validate all override keys are overridable content module scopes
    invalid_scopes = set(body.overrides.keys()) - _OVERRIDABLE_SCOPES
    if invalid_scopes:
        raise HTTPException(
            400,
            f"Invalid scopes for override: {', '.join(sorted(invalid_scopes))}. "
            f"Only content module scopes (calls/intelligence × read/write/delete + calls:share) can be overridden.",
        )

    # Get target member
    target_membership = await member_repo.find_membership(tid, _uuid.UUID(member_user_id))
    if not target_membership:
        raise HTTPException(404, "User is not a member of this team")

    # Can't override owner scopes
    if target_membership.role == "owner":
        raise HTTPException(400, "Cannot override owner permissions")

    # Save overrides (empty dict = clear all)
    overrides_json = json.dumps(body.overrides) if body.overrides else None
    target_membership.scope_overrides = overrides_json
    await db.commit()

    role_defaults = sorted(get_scopes_for_role(target_membership.role))
    overrides = body.overrides
    effective = sorted(get_effective_scopes(target_membership.role, target_membership.scope_overrides))

    return MemberScopesResponse(
        effective_scopes=effective,
        role_defaults=role_defaults,
        overrides=overrides,
    )
