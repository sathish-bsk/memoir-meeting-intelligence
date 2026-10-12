"""
Team and membership management.
"""

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_event
from app.core.config import settings
from app.core.logging import app_logger
from app.core.permissions import has_scope
from app.models.team import Team, TeamMember
from app.repositories.team_repo import TeamMemberRepository, TeamRepository
from app.repositories.user_repo import UserRepository


class TeamService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.team_repo = TeamRepository(db)
        self.member_repo = TeamMemberRepository(db)
        self.user_repo = UserRepository(db)

    async def create_team(
        self,
        name: str,
        slug: Optional[str],
        created_by: str,
        ip_address: Optional[str] = None,
    ) -> Team:
        user_id = uuid.UUID(created_by)

        # Generate slug if not provided
        if not slug:
            slug = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
            slug = re.sub(r"-+", "-", slug)

        # Ensure unique slug
        base_slug = slug
        counter = 0
        while await self.team_repo.find_by_slug(slug):
            counter += 1
            slug = f"{base_slug}-{counter}"

        team = await self.team_repo.create_team(
            name=name, slug=slug, created_by=user_id
        )

        # Set trial plan on new team
        team.plan = "trial"
        team.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=14)
        await self.db.flush()

        # Auto-add creator as owner
        await self.member_repo.add_member(
            team_id=team.id, user_id=user_id, role="owner"
        )

        await log_event(
            self.db, "team_create", True,
            user_id=created_by, ip_address=ip_address,
            metadata={"team_id": str(team.id), "team_name": name},
        )
        await self.db.commit()
        return team

    async def get_user_teams(self, user_id: str) -> List[dict]:
        """Get all teams a user belongs to, with member counts."""
        memberships = await self.member_repo.get_user_teams(uuid.UUID(user_id))
        teams = []
        for m in memberships:
            team = await self.team_repo.get_by_id(m.team_id)
            if team:
                count = await self.team_repo.get_member_count(team.id)
                teams.append({
                    "id": str(team.id),
                    "name": team.name,
                    "slug": team.slug,
                    "created_by": str(team.created_by),
                    "member_count": count,
                    "role": m.role,
                    "created_at": team.created_at,
                    "updated_at": team.updated_at,
                })
        return teams

    async def get_team_detail(self, team_id: str, user_id: str) -> dict:
        """Get team details with members. Requires membership."""
        tid = uuid.UUID(team_id)
        uid = uuid.UUID(user_id)

        membership = await self.member_repo.find_membership(tid, uid)
        if not membership:
            raise HTTPException(403, "Not a member of this team")

        team = await self.team_repo.find_with_members(tid)
        if not team:
            raise HTTPException(404, "Team not found")

        # Fetch user details for each member
        member_ids = [m.user_id for m in team.members]
        users = await self.user_repo.find_users_by_ids(member_ids)
        user_map = {u.id: u for u in users}

        members = []
        for m in team.members:
            u = user_map.get(m.user_id)
            members.append({
                "id": str(m.id),
                "user_id": str(m.user_id),
                "email": u.email if u else "",
                "name": u.name if u else "",
                "role": m.role,
                "joined_at": m.joined_at,
            })

        count = len(team.members)

        return {
            "id": str(team.id),
            "name": team.name,
            "slug": team.slug,
            "created_by": str(team.created_by),
            "member_count": count,
            "created_at": team.created_at,
            "updated_at": team.updated_at,
            "members": members,
        }

    async def invite_member(
        self,
        team_id: str,
        email: str,
        role: str,
        invited_by: str,
        ip_address: Optional[str] = None,
    ) -> TeamMember:
        """Invite a user to a team by email."""
        tid = uuid.UUID(team_id)
        inviter_id = uuid.UUID(invited_by)

        # Check inviter has team:members:invite scope
        inviter_membership = await self.member_repo.find_membership(tid, inviter_id)
        if not inviter_membership or not has_scope(inviter_membership.role, "team:members:invite", inviter_membership.scope_overrides):
            raise HTTPException(403, "Requires team:members:invite permission")

        # Find or validate user
        user = await self.user_repo.find_by_email(email)
        if not user:
            raise HTTPException(404, "User not found. They must sign up first.")

        # Check if already a member
        existing = await self.member_repo.find_membership(tid, user.id)
        if existing:
            raise HTTPException(409, "User is already a team member")

        # Can't invite as owner
        if role == "owner":
            raise HTTPException(400, "Cannot invite as owner. Transfer ownership instead.")

        member = await self.member_repo.add_member(
            team_id=tid, user_id=user.id, role=role, invited_by=inviter_id
        )

        await log_event(
            self.db, "team_invite", True,
            user_id=invited_by, ip_address=ip_address,
            metadata={"team_id": team_id, "invited_email": email, "role": role},
        )
        await self.db.commit()
        return member

    async def remove_member(
        self,
        team_id: str,
        target_user_id: str,
        removed_by: str,
        ip_address: Optional[str] = None,
    ):
        """Remove a member from a team."""
        tid = uuid.UUID(team_id)
        target_uid = uuid.UUID(target_user_id)
        remover_id = uuid.UUID(removed_by)

        # Check remover has team:members:remove scope
        remover_membership = await self.member_repo.find_membership(tid, remover_id)
        if not remover_membership or not has_scope(remover_membership.role, "team:members:remove", remover_membership.scope_overrides):
            raise HTTPException(403, "Requires team:members:remove permission")

        target_membership = await self.member_repo.find_membership(tid, target_uid)
        if not target_membership:
            raise HTTPException(404, "User is not a member of this team")

        # Can't remove the owner
        if target_membership.role == "owner":
            raise HTTPException(400, "Cannot remove the team owner. Transfer ownership first.")

        # Admin can't remove another admin (only owner can)
        if (
            target_membership.role == "admin"
            and remover_membership.role != "owner"
        ):
            raise HTTPException(403, "Only the owner can remove admins")

        await self.member_repo.remove_member(tid, target_uid)

        await log_event(
            self.db, "team_remove", True,
            user_id=removed_by, ip_address=ip_address,
            metadata={"team_id": team_id, "removed_user_id": target_user_id},
        )
        await self.db.commit()

    async def change_member_role(
        self,
        team_id: str,
        target_user_id: str,
        new_role: str,
        changed_by: str,
        ip_address: Optional[str] = None,
    ):
        """Change a member's role."""
        tid = uuid.UUID(team_id)
        target_uid = uuid.UUID(target_user_id)
        changer_id = uuid.UUID(changed_by)

        # Check changer has team:members:role scope (owner only)
        changer_membership = await self.member_repo.find_membership(tid, changer_id)
        if not changer_membership or not has_scope(changer_membership.role, "team:members:role", changer_membership.scope_overrides):
            raise HTTPException(403, "Requires team:members:role permission")

        target_membership = await self.member_repo.find_membership(tid, target_uid)
        if not target_membership:
            raise HTTPException(404, "User is not a member of this team")

        if target_membership.role == "owner" and new_role != "owner":
            raise HTTPException(400, "Cannot demote the owner. Transfer ownership first.")

        # Ownership transfer
        if new_role == "owner":
            # Demote current owner to admin and clear their overrides
            await self.member_repo.update_role(tid, changer_id, "admin")
            changer_membership.scope_overrides = None
            await self.member_repo.update_role(tid, target_uid, "owner")
            target_membership.scope_overrides = None
        else:
            await self.member_repo.update_role(tid, target_uid, new_role)
            # Clear scope_overrides on role change (fresh start with new role defaults)
            target_membership.scope_overrides = None

        await log_event(
            self.db, "team_role_change", True,
            user_id=changed_by, ip_address=ip_address,
            metadata={
                "team_id": team_id,
                "target_user_id": target_user_id,
                "new_role": new_role,
            },
        )
        await self.db.commit()

        # Sync role change to core_api (best-effort)
        await self._sync_role_to_core_api(team_id, target_user_id, new_role)
        if new_role == "owner":
            await self._sync_role_to_core_api(team_id, changed_by, "admin")

    async def _sync_role_to_core_api(
        self, team_id: str, user_id: str, role: str
    ) -> None:
        """Sync a role change to core_api's team_members table. Best-effort."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{settings.CORE_API_URL}/api/teams/internal/sync-member-role",
                    json={"team_id": team_id, "user_id": user_id, "role": role},
                    headers={"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
                )
        except Exception as e:
            app_logger.warning(f"Failed to sync role change to core_api: {e}")
