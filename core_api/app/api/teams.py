"""
Team management API routes.
"""

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_db
from app.models.meeting import Meeting
from app.models.team import Team, TeamMember
from app.schemas.team import (
    InviteCreate,
    InviteResponse,
    JoinTeamRequest,
    RequestJoinBody,
    TeamCreate,
    TeamMemberResponse,
    TeamResponse,
    TeamUpdate,
)
from pydantic import BaseModel as PydanticBaseModel
from videoclip_common.auth import UserContext, get_current_user
from videoclip_common.service_auth import verify_internal_call

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/teams", tags=["teams"])

settings = Settings()

PLACEHOLDER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")

FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "icloud.com", "proton.me", "protonmail.com", "aol.com",
    "live.com", "msn.com", "ymail.com",
}


def _generate_slug(name: str) -> str:
    """Generate a URL-friendly slug from a team name."""
    base = name.lower().replace(" ", "-").replace("_", "-")
    # Remove any non-alphanumeric characters except hyphens
    base = "".join(c for c in base if c.isalnum() or c == "-")
    # Remove consecutive hyphens
    while "--" in base:
        base = base.replace("--", "-")
    return base.strip("-")


def _generate_invite_code() -> str:
    """Generate a MEM-XXXX-XXXX format invite code."""
    part1 = secrets.token_hex(4).upper()[:4]
    part2 = secrets.token_hex(4).upper()[:4]
    return f"MEM-{part1}-{part2}"


async def _verify_membership(
    db: AsyncSession, team_id: uuid.UUID, user_id: str
) -> TeamMember:
    """Verify user is an active member of the team. Returns the TeamMember."""
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == uuid.UUID(user_id),
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(403, "Not a member of this team")
    return member


async def _verify_admin_or_owner(
    db: AsyncSession, team_id: uuid.UUID, user_id: str
) -> TeamMember:
    """Verify user is an admin or owner of the team."""
    member = await _verify_membership(db, team_id, user_id)
    if member.role not in ("admin", "owner"):
        raise HTTPException(403, "Requires admin or owner role")
    return member


@router.post("", response_model=TeamResponse, status_code=201)
async def create_team(
    body: TeamCreate,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new team. The creating user becomes the owner."""
    # Generate slug, append random suffix if conflict
    base_slug = _generate_slug(body.name)
    slug = base_slug

    # Check for slug conflict
    result = await db.execute(select(Team).where(Team.slug == slug))
    if result.scalar_one_or_none():
        suffix = secrets.token_hex(3)[:6]
        slug = f"{base_slug}-{suffix}"

    # If a team ID is provided, check if it already exists (upsert for onboarding re-runs)
    existing_team = None
    if body.id:
        result = await db.execute(select(Team).where(Team.id == body.id))
        existing_team = result.scalar_one_or_none()

    # Auto-detect domain from creator's email
    email_domain = None
    if user.email and "@" in user.email:
        email_domain = user.email.split("@")[1].lower()
        # In development, allow all domains; in production, skip free email domains
        if settings.ENVIRONMENT != "development" and email_domain in FREE_EMAIL_DOMAINS:
            email_domain = None

    if existing_team:
        existing_team.name = body.name
        existing_team.slug = slug
        if body.crm_type is not None:
            existing_team.crm_type = body.crm_type
        if body.team_size_range is not None:
            existing_team.team_size_range = body.team_size_range
        if body.intelligence_priority is not None:
            existing_team.intelligence_priority = body.intelligence_priority
        if email_domain and not existing_team.domain:
            existing_team.domain = email_domain
        team = existing_team
    else:
        team = Team(
            name=body.name,
            slug=slug,
            created_by=uuid.UUID(user.user_id),
            crm_type=body.crm_type,
            team_size_range=body.team_size_range,
            intelligence_priority=body.intelligence_priority,
            domain=email_domain,
        )
        # Use auth_service team_id if provided to keep IDs in sync across databases
        if body.id:
            team.id = body.id
        db.add(team)
        await db.flush()

        # Add creating user as owner
        owner_member = TeamMember(
            team_id=team.id,
            user_id=uuid.UUID(user.user_id),
            role="owner",
            invite_status="accepted",
        )
        db.add(owner_member)

    await db.commit()
    await db.refresh(team)

    return TeamResponse(
        id=team.id,
        name=team.name,
        slug=team.slug,
        created_by=team.created_by,
        crm_type=team.crm_type,
        team_size_range=team.team_size_range,
        intelligence_priority=team.intelligence_priority,
        created_at=team.created_at,
        member_count=1,
    )


@router.get("", response_model=list[TeamResponse])
async def list_teams(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all teams the current user is a member of."""
    # Subquery for member count
    member_count_subq = (
        select(
            TeamMember.team_id,
            func.count(TeamMember.id).label("member_count"),
        )
        .where(TeamMember.invite_status == "accepted")
        .group_by(TeamMember.team_id)
        .subquery()
    )

    result = await db.execute(
        select(Team, func.coalesce(member_count_subq.c.member_count, 0).label("member_count"))
        .join(TeamMember, TeamMember.team_id == Team.id)
        .outerjoin(member_count_subq, member_count_subq.c.team_id == Team.id)
        .where(
            TeamMember.user_id == uuid.UUID(user.user_id),
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    rows = result.all()

    return [
        TeamResponse(
            id=team.id,
            name=team.name,
            slug=team.slug,
            created_by=team.created_by,
            crm_type=team.crm_type,
            team_size_range=team.team_size_range,
            intelligence_priority=team.intelligence_priority,
            created_at=team.created_at,
            member_count=count,
        )
        for team, count in rows
    ]


@router.get("/lookup-by-domain")
async def lookup_by_domain(
    domain: str = Query(..., min_length=1),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Look up teams by email domain. Returns matching teams for domain-based join."""
    domain = domain.strip().lower()
    result = await db.execute(
        select(Team.id, Team.name).where(Team.domain == domain)
    )
    rows = result.all()
    return {
        "found": len(rows) > 0,
        "teams": [{"id": str(r.id), "name": r.name} for r in rows],
    }


@router.get("/invite/details")
async def get_invite_details(
    code: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint: look up invite by code and check if the invited email has an account."""
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.invite_code == code,
            TeamMember.invite_status == "pending",
        )
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(404, "Invalid or expired invite code")

    email = invite.invited_email
    account_exists = False

    if email:
        from app.clients.auth_client import check_email_exists
        check = await check_email_exists(email)
        if check:
            account_exists = check.get("exists", False)

    return {
        "email": email,
        "account_exists": account_exists,
    }


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get team details. Requires membership."""
    await _verify_membership(db, team_id, user.user_id)

    result = await db.execute(select(Team).where(Team.id == team_id))
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(404, "Team not found")

    # Count members
    count_result = await db.execute(
        select(func.count(TeamMember.id)).where(
            TeamMember.team_id == team_id,
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    member_count = count_result.scalar() or 0

    return TeamResponse(
        id=team.id,
        name=team.name,
        slug=team.slug,
        created_by=team.created_by,
        crm_type=team.crm_type,
        team_size_range=team.team_size_range,
        intelligence_priority=team.intelligence_priority,
        created_at=team.created_at,
        member_count=member_count,
    )


@router.get("/{team_id}/members", response_model=list[TeamMemberResponse])
async def list_team_members(
    team_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all members of a team. Requires membership."""
    await _verify_membership(db, team_id, user.user_id)

    result = await db.execute(
        select(TeamMember).where(TeamMember.team_id == team_id)
    )
    members = result.scalars().all()

    # Collect real user IDs (non-placeholder) to batch-fetch from auth service
    real_user_ids = [
        str(m.user_id) for m in members
        if m.user_id != PLACEHOLDER_USER_ID
    ]

    user_map: dict = {}
    if real_user_ids:
        from app.clients.auth_client import batch_get_users
        user_map = await batch_get_users(real_user_ids)

    # Per-member calls this week
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

    call_count_result = await db.execute(
        select(Meeting.created_by, func.count(Meeting.id))
        .where(
            Meeting.team_id == team_id,
            Meeting.status == "completed",
            Meeting.created_at >= week_start,
        )
        .group_by(Meeting.created_by)
    )
    calls_map: dict[str, int] = {
        str(row[0]): row[1] for row in call_count_result.all()
    }

    responses = []
    for m in members:
        email = None
        name = None

        if m.user_id != PLACEHOLDER_USER_ID:
            # Real user — look up from auth service
            auth_user = user_map.get(str(m.user_id))
            if auth_user:
                email = auth_user.get("email")
                name = auth_user.get("name")
        else:
            # Pending invite — use stored invited_email/invited_name
            email = m.invited_email
            name = m.invited_name

        responses.append(
            TeamMemberResponse(
                id=m.id,
                user_id=m.user_id,
                email=email,
                name=name,
                role=m.role,
                invite_status=m.invite_status,
                joined_at=m.joined_at,
                calls_this_week=calls_map.get(str(m.user_id), 0),
                requested_at=m.requested_at,
            )
        )

    return responses


@router.patch("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: uuid.UUID,
    body: TeamUpdate,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update team settings. Requires admin or owner role."""
    await _verify_admin_or_owner(db, team_id, user.user_id)

    result = await db.execute(select(Team).where(Team.id == team_id))
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(404, "Team not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(team, field, value)

    await db.commit()
    await db.refresh(team)

    # Count members
    count_result = await db.execute(
        select(func.count(TeamMember.id)).where(
            TeamMember.team_id == team_id,
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    member_count = count_result.scalar() or 0

    return TeamResponse(
        id=team.id,
        name=team.name,
        slug=team.slug,
        created_by=team.created_by,
        crm_type=team.crm_type,
        team_size_range=team.team_size_range,
        intelligence_priority=team.intelligence_priority,
        created_at=team.created_at,
        member_count=member_count,
    )


@router.post("/{team_id}/invite", response_model=InviteResponse)
async def invite_member(
    team_id: uuid.UUID,
    body: InviteCreate,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate an invite code for a new team member. Requires admin or owner role."""
    await _verify_admin_or_owner(db, team_id, user.user_id)

    result = await db.execute(select(Team).where(Team.id == team_id))
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(404, "Team not found")

    invited_email = body.email.strip().lower()

    # Block invitations to existing users entirely
    from app.clients.auth_client import check_email_exists

    email_info = await check_email_exists(invited_email)
    if email_info is None:
        # Auth service unreachable — fail closed, don't allow invite
        raise HTTPException(
            503,
            "Unable to verify email. Please try again later or contact support.",
        )
    if email_info.get("exists"):
        raise HTTPException(
            400,
            "This email belongs to an existing account. Existing users cannot be invited. Please contact support if you need help.",
        )

    # Check for existing pending invite for same email
    existing_invite = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.invited_email == invited_email,
            TeamMember.invite_status == "pending",
        )
    )
    if existing_invite.scalar_one_or_none():
        raise HTTPException(400, "An invite has already been sent to this email. Please contact support if you need help.")

    # Check plan limit for team members
    from app.core.plan_limits import check_plan_limit

    member_count_result = await db.execute(
        select(func.count(TeamMember.id)).where(
            TeamMember.team_id == team_id,
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    current_members = member_count_result.scalar() or 0
    await check_plan_limit("team_members", str(team_id), current_members)

    invite_code = _generate_invite_code()

    # Create pending member record with invited_email
    pending_member = TeamMember(
        team_id=team_id,
        user_id=PLACEHOLDER_USER_ID,
        role="member",
        invite_code=invite_code,
        invite_status="pending",
        invited_email=invited_email,
        invited_name=None,
    )
    db.add(pending_member)
    await db.commit()

    logger.info(f"Invite created for team {team_id}: {invite_code} -> {invited_email}")

    # Send invite email (non-blocking)
    try:
        from app.clients.notification_client import send_team_invite_email

        await send_team_invite_email(
            email=invited_email,
            inviter_name=user.name,
            team_name=team.name,
            invite_code=invite_code,
        )
    except Exception as e:
        logger.warning(f"Failed to send invite email: {e}")

    return InviteResponse(
        invite_code=invite_code,
        team_name=team.name,
    )


@router.post("/{team_id}/members/{member_id}/reinvite", status_code=200)
async def reinvite_member(
    team_id: uuid.UUID,
    member_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Resend invite email for a pending member. Requires admin or owner role."""
    await _verify_admin_or_owner(db, team_id, user.user_id)

    result = await db.execute(
        select(TeamMember).where(
            TeamMember.id == member_id,
            TeamMember.team_id == team_id,
            TeamMember.invite_status == "pending",
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "Pending member not found")

    if not member.invited_email:
        raise HTTPException(400, "No email address on record for this invite")

    # Load team name
    team_result = await db.execute(select(Team).where(Team.id == team_id))
    team = team_result.scalar_one_or_none()

    try:
        from app.clients.notification_client import send_team_invite_email

        await send_team_invite_email(
            email=member.invited_email,
            inviter_name=user.name,
            team_name=team.name if team else "Your team",
            invite_code=member.invite_code,
        )
    except Exception as e:
        logger.warning(f"Failed to resend invite email: {e}")
        raise HTTPException(500, "Failed to send invite email")

    return {"message": "Invite resent successfully"}


@router.post("/{team_id}/request-join", status_code=201)
async def request_join_team(
    team_id: uuid.UUID,
    body: RequestJoinBody = RequestJoinBody(),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Request to join a team (domain-based). Creates a 'requested' member record."""
    # Check team exists
    result = await db.execute(select(Team).where(Team.id == team_id))
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(404, "Team not found")

    user_uuid = uuid.UUID(user.user_id)

    # Check if user is already a member of this team (any status)
    existing = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_uuid,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, "You already have a pending or active membership with this team")

    # Single-team constraint: users can only be in one team
    other_membership = await db.execute(
        select(TeamMember).where(
            TeamMember.user_id == user_uuid,
            TeamMember.team_id != team_id,
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    if other_membership.scalar_one_or_none():
        raise HTTPException(
            400,
            "You are already a member of another team. Please leave your current team first.",
        )

    # Create requested member record
    requested_member = TeamMember(
        team_id=team_id,
        user_id=user_uuid,
        role="member",
        invite_status="requested",
        requested_at=datetime.now(timezone.utc),
        requested_message=body.message,
    )
    db.add(requested_member)
    await db.commit()

    logger.info(f"Join request created for team {team_id} by user {user.user_id}")

    return {"status": "requested", "team_name": team.name}


@router.post("/{team_id}/members/{member_id}/approve", status_code=200)
async def approve_join_request(
    team_id: uuid.UUID,
    member_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve a domain-based join request. Admin or owner only."""
    await _verify_admin_or_owner(db, team_id, user.user_id)

    result = await db.execute(
        select(TeamMember).where(
            TeamMember.id == member_id,
            TeamMember.team_id == team_id,
            TeamMember.invite_status == "requested",
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "Requested member not found")

    # Check plan limit before approving
    from app.core.plan_limits import check_plan_limit

    member_count_result = await db.execute(
        select(func.count(TeamMember.id)).where(
            TeamMember.team_id == team_id,
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    current_members = member_count_result.scalar() or 0
    await check_plan_limit("team_members", str(team_id), current_members)

    member.invite_status = "accepted"
    member.joined_at = datetime.now(timezone.utc)
    await db.commit()

    # Sync membership to auth service
    from app.clients.auth_client import sync_team_member
    await sync_team_member(
        team_id=str(team_id),
        user_id=str(member.user_id),
        role=member.role,
    )

    # Send approval email
    try:
        team_result = await db.execute(select(Team).where(Team.id == team_id))
        team = team_result.scalar_one_or_none()
        from app.clients.auth_client import batch_get_users
        user_map = await batch_get_users([str(member.user_id)])
        auth_user = user_map.get(str(member.user_id))
        if auth_user and auth_user.get("email") and team:
            from app.clients.notification_client import send_join_approved_email
            await send_join_approved_email(
                auth_user["email"], auth_user.get("name"), team.name, user.user_id
            )
    except Exception as e:
        logger.warning(f"Failed to send join approved email: {e}")

    return {"message": "Join request approved"}


@router.post("/{team_id}/members/{member_id}/decline", status_code=200)
async def decline_join_request(
    team_id: uuid.UUID,
    member_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Decline a domain-based join request. Admin or owner only."""
    await _verify_admin_or_owner(db, team_id, user.user_id)

    result = await db.execute(
        select(TeamMember).where(
            TeamMember.id == member_id,
            TeamMember.team_id == team_id,
            TeamMember.invite_status == "requested",
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "Requested member not found")

    # Capture user info before deleting the member record
    declined_user_id = str(member.user_id)

    await db.delete(member)
    await db.commit()

    # Send decline email
    try:
        team_result = await db.execute(select(Team).where(Team.id == team_id))
        team = team_result.scalar_one_or_none()
        from app.clients.auth_client import batch_get_users
        user_map = await batch_get_users([declined_user_id])
        auth_user = user_map.get(declined_user_id)
        if auth_user and auth_user.get("email") and team:
            from app.clients.notification_client import send_join_declined_email
            await send_join_declined_email(
                auth_user["email"], auth_user.get("name"), team.name, user.user_id
            )
    except Exception as e:
        logger.warning(f"Failed to send join declined email: {e}")

    return {"message": "Join request declined"}


@router.post("/{team_id}/members/{member_id}/revoke", status_code=200)
async def revoke_invite(
    team_id: uuid.UUID,
    member_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a pending invite. Admin or owner only."""
    await _verify_admin_or_owner(db, team_id, user.user_id)

    result = await db.execute(
        select(TeamMember).where(
            TeamMember.id == member_id,
            TeamMember.team_id == team_id,
            TeamMember.invite_status == "pending",
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "Pending invite not found")

    await db.delete(member)
    await db.commit()

    return {"message": "Invite revoked"}


@router.delete("/{team_id}/members/{member_id}", status_code=200)
async def remove_member(
    team_id: uuid.UUID,
    member_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a member from the team. Requires admin or owner role."""
    acting_member = await _verify_admin_or_owner(db, team_id, user.user_id)

    result = await db.execute(
        select(TeamMember).where(
            TeamMember.id == member_id,
            TeamMember.team_id == team_id,
        )
    )
    target_member = result.scalar_one_or_none()
    if not target_member:
        raise HTTPException(404, "Member not found")

    # Cannot remove the owner
    if target_member.role == "owner":
        raise HTTPException(400, "Cannot remove the team owner")

    # Admin cannot remove another admin (only owner can)
    if target_member.role == "admin" and acting_member.role != "owner":
        raise HTTPException(403, "Only the owner can remove an admin")

    removed_user_id = str(target_member.user_id)
    await db.delete(target_member)
    await db.commit()

    # Sync removal to auth service
    if removed_user_id != str(PLACEHOLDER_USER_ID):
        from app.clients.auth_client import unsync_team_member
        await unsync_team_member(team_id=str(team_id), user_id=removed_user_id)

    return {"message": "Member removed successfully"}


@router.post("/join", response_model=TeamResponse)
async def join_team(
    body: JoinTeamRequest,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Join a team using an invite code."""
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.invite_code == body.invite_code,
            TeamMember.invite_status == "pending",
        )
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(404, "Invalid or expired invite code")

    # Validate joining user's email matches the invited email
    if invite.invited_email and user.email:
        if user.email.strip().lower() != invite.invited_email.strip().lower():
            raise HTTPException(
                403,
                "This invite was sent to a different email address. Please sign in with the invited email or contact support.",
            )

    # Check if user is already a member of this team
    existing = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == invite.team_id,
            TeamMember.user_id == uuid.UUID(user.user_id),
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Already a member of this team")

    # Single-team constraint: users can only be in one team
    other_membership = await db.execute(
        select(TeamMember).where(
            TeamMember.user_id == uuid.UUID(user.user_id),
            TeamMember.team_id != invite.team_id,
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    if other_membership.scalar_one_or_none():
        raise HTTPException(
            400,
            "You are already a member of another team. Please leave your current team first.",
        )

    # Update the invite record
    invite.user_id = uuid.UUID(user.user_id)
    invite.invite_status = "accepted"
    await db.commit()

    # Sync membership to auth service so JWT gets correct team_id/team_role
    from app.clients.auth_client import sync_team_member
    await sync_team_member(
        team_id=str(invite.team_id),
        user_id=user.user_id,
        role=invite.role,
    )

    # Load team for response
    team_result = await db.execute(select(Team).where(Team.id == invite.team_id))
    team = team_result.scalar_one_or_none()

    count_result = await db.execute(
        select(func.count(TeamMember.id)).where(
            TeamMember.team_id == invite.team_id,
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    member_count = count_result.scalar() or 0

    return TeamResponse(
        id=team.id,
        name=team.name,
        slug=team.slug,
        created_by=team.created_by,
        crm_type=team.crm_type,
        team_size_range=team.team_size_range,
        intelligence_priority=team.intelligence_priority,
        created_at=team.created_at,
        member_count=member_count,
    )


# ─── Internal Service Endpoints ──────────────────────────────────


class SyncMemberRoleRequest(PydanticBaseModel):
    team_id: str
    user_id: str
    role: str


@router.post("/internal/sync-member-role", status_code=200)
async def sync_member_role(
    body: SyncMemberRoleRequest,
    _: None = Depends(verify_internal_call),
    db: AsyncSession = Depends(get_db),
):
    """Internal endpoint: sync a role change from auth_service."""
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == uuid.UUID(body.team_id),
            TeamMember.user_id == uuid.UUID(body.user_id),
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "Member not found")

    member.role = body.role
    await db.commit()

    return {"message": "Role synced successfully"}
