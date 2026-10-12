"""
Resource permission repository — DB access for the resource_permissions table.
"""

import uuid
from typing import List, Optional

from sqlalchemy import and_, delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.permission import ResourcePermission
from app.repositories.base import BaseRepository


class ResourcePermissionRepository(BaseRepository[ResourcePermission]):
    def __init__(self, db: AsyncSession):
        super().__init__(ResourcePermission, db)

    async def find_permissions(
        self,
        resource_type: str,
        resource_id: uuid.UUID,
        subject_type: str,
        subject_id: uuid.UUID,
    ) -> List[ResourcePermission]:
        """Get all action grants for a subject on a specific resource."""
        result = await self.db.execute(
            select(ResourcePermission).where(
                ResourcePermission.resource_type == resource_type,
                ResourcePermission.resource_id == resource_id,
                ResourcePermission.subject_type == subject_type,
                ResourcePermission.subject_id == subject_id,
            )
        )
        return list(result.scalars().all())

    async def find_by_resource(
        self,
        resource_type: str,
        resource_id: uuid.UUID,
    ) -> List[ResourcePermission]:
        """Get all permissions for a resource (all subjects)."""
        result = await self.db.execute(
            select(ResourcePermission).where(
                ResourcePermission.resource_type == resource_type,
                ResourcePermission.resource_id == resource_id,
            )
        )
        return list(result.scalars().all())

    async def find_by_subject(
        self,
        subject_type: str,
        subject_id: uuid.UUID,
        resource_type: Optional[str] = None,
    ) -> List[ResourcePermission]:
        """Get all resources a subject has permissions on."""
        conditions = [
            ResourcePermission.subject_type == subject_type,
            ResourcePermission.subject_id == subject_id,
        ]
        if resource_type:
            conditions.append(ResourcePermission.resource_type == resource_type)

        result = await self.db.execute(
            select(ResourcePermission).where(*conditions)
        )
        return list(result.scalars().all())

    async def grant(
        self,
        team_id: uuid.UUID,
        resource_type: str,
        resource_id: uuid.UUID,
        subject_type: str,
        subject_id: uuid.UUID,
        action: str,
        granted_by: Optional[uuid.UUID] = None,
    ) -> ResourcePermission:
        """Grant a single action. Upserts (ignores if already exists)."""
        # Check if already exists
        result = await self.db.execute(
            select(ResourcePermission).where(
                ResourcePermission.resource_type == resource_type,
                ResourcePermission.resource_id == resource_id,
                ResourcePermission.subject_type == subject_type,
                ResourcePermission.subject_id == subject_id,
                ResourcePermission.action == action,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        perm = ResourcePermission(
            team_id=team_id,
            resource_type=resource_type,
            resource_id=resource_id,
            subject_type=subject_type,
            subject_id=subject_id,
            action=action,
            granted_by=granted_by,
        )
        return await self.create(perm)

    async def grant_bulk(
        self,
        team_id: uuid.UUID,
        resource_type: str,
        resource_id: uuid.UUID,
        subject_type: str,
        subject_id: uuid.UUID,
        actions: set[str],
        granted_by: Optional[uuid.UUID] = None,
    ) -> List[ResourcePermission]:
        """Grant multiple actions at once (for presets). Replaces all existing."""
        # Remove existing permissions for this subject+resource
        await self.db.execute(
            delete(ResourcePermission).where(
                ResourcePermission.resource_type == resource_type,
                ResourcePermission.resource_id == resource_id,
                ResourcePermission.subject_type == subject_type,
                ResourcePermission.subject_id == subject_id,
            )
        )

        perms = []
        for action in sorted(actions):
            perm = ResourcePermission(
                team_id=team_id,
                resource_type=resource_type,
                resource_id=resource_id,
                subject_type=subject_type,
                subject_id=subject_id,
                action=action,
                granted_by=granted_by,
            )
            self.db.add(perm)
            perms.append(perm)

        await self.db.flush()
        return perms

    async def revoke(self, permission_id: uuid.UUID) -> bool:
        """Revoke a single permission by ID. Returns True if found."""
        perm = await self.get_by_id(permission_id)
        if not perm:
            return False
        await self.delete(perm)
        return True

    async def revoke_all(
        self,
        resource_type: str,
        resource_id: uuid.UUID,
        subject_type: str,
        subject_id: uuid.UUID,
    ) -> int:
        """Revoke all permissions for a subject on a resource. Returns count."""
        result = await self.db.execute(
            delete(ResourcePermission)
            .where(
                ResourcePermission.resource_type == resource_type,
                ResourcePermission.resource_id == resource_id,
                ResourcePermission.subject_type == subject_type,
                ResourcePermission.subject_id == subject_id,
            )
        )
        await self.db.flush()
        return result.rowcount

    async def batch_find_permissions(
        self,
        subject_type: str,
        subject_id: uuid.UUID,
        checks: list[tuple[str, uuid.UUID]],
    ) -> dict[tuple[str, uuid.UUID], set[str]]:
        """Find permissions for a subject across multiple resources in one query.

        Args:
            subject_type: 'user' or 'team'
            subject_id: The subject UUID
            checks: List of (resource_type, resource_id) tuples

        Returns:
            Dict mapping (resource_type, resource_id) → set of granted actions.
            Missing keys mean no direct permissions found.
        """
        if not checks:
            return {}

        # Build (resource_type, resource_id) pairs for IN clause
        pairs = [(rt, rid) for rt, rid in checks]

        result = await self.db.execute(
            select(ResourcePermission).where(
                ResourcePermission.subject_type == subject_type,
                ResourcePermission.subject_id == subject_id,
                tuple_(
                    ResourcePermission.resource_type,
                    ResourcePermission.resource_id,
                ).in_(pairs),
            )
        )
        rows = result.scalars().all()

        # Group by (resource_type, resource_id)
        perms: dict[tuple[str, uuid.UUID], set[str]] = {}
        for row in rows:
            key = (row.resource_type, row.resource_id)
            perms.setdefault(key, set()).add(row.action)
        return perms
