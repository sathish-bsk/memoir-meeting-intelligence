"""
Team and team membership repository.
"""

import uuid
from typing import List, Optional

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.team import Team, TeamMember
from app.repositories.base import BaseRepository


class TeamRepository(BaseRepository[Team]):
    def __init__(self, db: AsyncSession):
        super().__init__(Team, db)

    async def find_by_slug(self, slug: str) -> Optional[Team]:
        result = await self.db.execute(
            select(Team).where(Team.slug == slug)
        )
        return result.scalar_one_or_none()

    async def find_with_members(self, team_id: uuid.UUID) -> Optional[Team]:
        result = await self.db.execute(
            select(Team)
            .options(selectinload(Team.members))
            .where(Team.id == team_id)
        )
        return result.scalar_one_or_none()

    async def create_team(
        self,
        name: str,
        slug: str,
        created_by: uuid.UUID,
    ) -> Team:
        team = Team(name=name, slug=slug, created_by=created_by)
        return await self.create(team)

    async def get_member_count(self, team_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(TeamMember)
            .where(TeamMember.team_id == team_id)
        )
        return result.scalar_one()

    async def update_plan(
        self,
        team_id: uuid.UUID,
        plan: str,
        plan_limits: Optional[str] = None,
        trial_ends_at=None,
    ) -> Optional[Team]:
        team = await self.get_by_id(team_id)
        if not team:
            return None
        team.plan = plan
        if plan_limits is not None:
            team.plan_limits = plan_limits
        if trial_ends_at is not None:
            team.trial_ends_at = trial_ends_at
        await self.db.flush()
        return team


class TeamMemberRepository(BaseRepository[TeamMember]):
    def __init__(self, db: AsyncSession):
        super().__init__(TeamMember, db)

    async def find_membership(
        self, team_id: uuid.UUID, user_id: uuid.UUID
    ) -> Optional[TeamMember]:
        result = await self.db.execute(
            select(TeamMember).where(
                TeamMember.team_id == team_id,
                TeamMember.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def add_member(
        self,
        team_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str = "member",
        invited_by: Optional[uuid.UUID] = None,
    ) -> TeamMember:
        member = TeamMember(
            team_id=team_id,
            user_id=user_id,
            role=role,
            invited_by=invited_by,
        )
        return await self.create(member)

    async def remove_member(
        self, team_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        await self.db.execute(
            delete(TeamMember).where(
                TeamMember.team_id == team_id,
                TeamMember.user_id == user_id,
            )
        )

    async def update_role(
        self, team_id: uuid.UUID, user_id: uuid.UUID, role: str
    ) -> Optional[TeamMember]:
        member = await self.find_membership(team_id, user_id)
        if member:
            member.role = role
            await self.db.flush()
        return member

    async def get_user_teams(self, user_id: uuid.UUID) -> List[TeamMember]:
        """Get all team memberships for a user."""
        result = await self.db.execute(
            select(TeamMember).where(TeamMember.user_id == user_id)
        )
        return list(result.scalars().all())

    async def get_team_members(self, team_id: uuid.UUID) -> List[TeamMember]:
        """Get all members of a team."""
        result = await self.db.execute(
            select(TeamMember).where(TeamMember.team_id == team_id)
        )
        return list(result.scalars().all())

    async def get_first_team_for_user(self, user_id: uuid.UUID) -> Optional[TeamMember]:
        """Get user's default team membership (for login).
        Prefers most recently joined team so invited members land on the right team.
        """
        result = await self.db.execute(
            select(TeamMember)
            .where(TeamMember.user_id == user_id)
            .order_by(TeamMember.joined_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
