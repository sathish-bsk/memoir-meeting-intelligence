"""
Permission service — orchestrates team-level RBAC and resource-level sharing.

Permission check order for a resource:
  1. Direct user grant on resource → use those actions (replaces team defaults)
  2. Team grant on resource → use those actions
  3. Fall back to team-level scope from role
"""

import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import (
    PRESETS,
    RESOURCE_ACTIONS,
    expand_preset,
    get_effective_scopes,
    get_scopes_for_role,
    validate_resource_actions,
)
from app.repositories.permission_repo import ResourcePermissionRepository


class PermissionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.perm_repo = ResourcePermissionRepository(db)

    @staticmethod
    def _group_permissions(perms) -> list[dict]:
        """Group individual permission rows by (subject_type, subject_id)."""
        grouped: dict[tuple[str, str], dict] = {}
        for p in perms:
            key = (p.subject_type, str(p.subject_id))
            if key not in grouped:
                grouped[key] = {
                    "id": str(p.id),
                    "subject_type": p.subject_type,
                    "subject_id": str(p.subject_id),
                    "actions": [],
                    "granted_by": str(p.granted_by) if p.granted_by else None,
                    "created_at": p.created_at.isoformat(),
                }
            grouped[key]["actions"].append(p.action)
        return list(grouped.values())

    # ─── Single Permission Check ─────────────────────────────────

    async def check_permission(
        self,
        user_id: str,
        team_id: str,
        team_role: Optional[str],
        resource_type: str,
        resource_id: str,
        action: str,
        scope_overrides: Optional[str] = None,
    ) -> dict:
        """Check if a user can perform an action on a resource.

        Returns: {"allowed": bool, "source": "direct_grant"|"team_grant"|"role_scope"}
        """
        uid = uuid.UUID(user_id)
        tid = uuid.UUID(team_id)
        rid = uuid.UUID(resource_id)

        # 1. Check direct user permissions on this resource
        user_perms = await self.perm_repo.find_permissions(
            resource_type, rid, "user", uid
        )
        if user_perms:
            actions = {p.action for p in user_perms}
            return {"allowed": action in actions, "source": "direct_grant"}

        # 2. Check team-level grant on this resource
        team_perms = await self.perm_repo.find_permissions(
            resource_type, rid, "team", tid
        )
        if team_perms:
            actions = {p.action for p in team_perms}
            return {"allowed": action in actions, "source": "team_grant"}

        # 3. Fall back to role-based scope with overrides
        scope = f"{resource_type}s:{action}"  # e.g. "projects:read"
        scopes = get_effective_scopes(team_role, scope_overrides) if team_role else frozenset()
        return {"allowed": scope in scopes, "source": "role_scope"}

    # ─── Batch Permission Check ──────────────────────────────────

    async def batch_check(
        self,
        user_id: str,
        team_id: str,
        team_role: Optional[str],
        checks: list[dict],
        scope_overrides: Optional[str] = None,
    ) -> dict:
        """Batch check permissions for multiple resources.

        Args:
            checks: [{"resource_type": "project", "resource_id": "...", "action": "read"}, ...]

        Returns: {"results": {"<resource_id>": bool, ...}}
        """
        uid = uuid.UUID(user_id)
        tid = uuid.UUID(team_id)

        # Collect all (resource_type, resource_id) pairs
        check_pairs = [
            (c["resource_type"], uuid.UUID(c["resource_id"]))
            for c in checks
        ]

        # Batch fetch: user direct permissions
        user_perms = await self.perm_repo.batch_find_permissions(
            "user", uid, check_pairs
        )
        # Batch fetch: team permissions
        team_perms = await self.perm_repo.batch_find_permissions(
            "team", tid, check_pairs
        )

        role_scopes = get_effective_scopes(team_role, scope_overrides) if team_role else frozenset()

        results = {}
        for check in checks:
            rt = check["resource_type"]
            rid = uuid.UUID(check["resource_id"])
            action = check["action"]
            key = (rt, rid)

            # Use resource_id:action as result key when multiple actions
            # for the same resource exist; otherwise just resource_id
            result_key = f"{check['resource_id']}:{action}"

            # Same priority order as single check
            if key in user_perms:
                results[result_key] = action in user_perms[key]
            elif key in team_perms:
                results[result_key] = action in team_perms[key]
            else:
                scope = f"{rt}s:{action}"
                results[result_key] = scope in role_scopes

        return {"results": results}

    # ─── Grant Access ────────────────────────────────────────────

    async def grant_access(
        self,
        team_id: str,
        resource_type: str,
        resource_id: str,
        subject_type: str,
        subject_id: str,
        actions: Optional[set[str]] = None,
        preset: Optional[str] = None,
        granted_by: Optional[str] = None,
    ) -> list[dict]:
        """Grant access to a resource using actions or a preset.

        If preset is provided, it's expanded to actions. If both, actions wins.
        Uses grant_bulk (replaces existing permissions for this subject+resource).
        """
        if not actions and not preset:
            raise HTTPException(400, "Must provide either 'actions' or 'preset'")

        if preset and not actions:
            actions = set(expand_preset(preset))

        validate_resource_actions(resource_type, actions)

        tid = uuid.UUID(team_id)
        rid = uuid.UUID(resource_id)
        sid = uuid.UUID(subject_id)
        gby = uuid.UUID(granted_by) if granted_by else None

        perms = await self.perm_repo.grant_bulk(
            team_id=tid,
            resource_type=resource_type,
            resource_id=rid,
            subject_type=subject_type,
            subject_id=sid,
            actions=actions,
            granted_by=gby,
        )

        await self.db.commit()

        return self._group_permissions(perms)

    # ─── Update Access ───────────────────────────────────────────

    async def update_access(
        self,
        permission_id: str,
        team_id: str,
        resource_type: str,
        resource_id: str,
        actions: Optional[set[str]] = None,
        preset: Optional[str] = None,
    ) -> list[dict]:
        """Update granted actions for an existing permission entry.

        Looks up the subject from the existing permission, then replaces all
        actions for that subject+resource with the new set.
        """
        pid = uuid.UUID(permission_id)
        existing = await self.perm_repo.get_by_id(pid)
        if not existing:
            raise HTTPException(404, "Permission not found")

        if str(existing.team_id) != team_id:
            raise HTTPException(403, "Permission does not belong to this team")

        if preset and not actions:
            actions = set(expand_preset(preset))

        if not actions:
            raise HTTPException(400, "Must provide either 'actions' or 'preset'")

        validate_resource_actions(resource_type, actions)

        perms = await self.perm_repo.grant_bulk(
            team_id=existing.team_id,
            resource_type=existing.resource_type,
            resource_id=existing.resource_id,
            subject_type=existing.subject_type,
            subject_id=existing.subject_id,
            actions=actions,
            granted_by=existing.granted_by,
        )

        await self.db.commit()

        return self._group_permissions(perms)

    # ─── Revoke Access ───────────────────────────────────────────

    async def revoke_access(
        self,
        permission_id: str,
        team_id: str,
    ) -> None:
        """Revoke a permission by ID. Looks up the subject and revokes ALL
        actions for that subject+resource (not just one row)."""
        pid = uuid.UUID(permission_id)
        existing = await self.perm_repo.get_by_id(pid)
        if not existing:
            raise HTTPException(404, "Permission not found")

        if str(existing.team_id) != team_id:
            raise HTTPException(403, "Permission does not belong to this team")

        await self.perm_repo.revoke_all(
            resource_type=existing.resource_type,
            resource_id=existing.resource_id,
            subject_type=existing.subject_type,
            subject_id=existing.subject_id,
        )
        await self.db.commit()

    # ─── List Permissions ────────────────────────────────────────

    async def list_permissions(
        self,
        resource_type: str,
        resource_id: str,
    ) -> list[dict]:
        """List who has access to a resource and their actions."""
        rid = uuid.UUID(resource_id)
        perms = await self.perm_repo.find_by_resource(resource_type, rid)
        return self._group_permissions(perms)

    # ─── List Accessible Resources ───────────────────────────────

    async def list_accessible(
        self,
        user_id: str,
        team_id: str,
        resource_type: str,
    ) -> list[dict]:
        """List resources the user has explicit permissions on.

        Returns resources with direct user grants or team grants.
        Does NOT include resources accessible only via role-based scope
        (that's the default — those are all resources in the team).
        """
        uid = uuid.UUID(user_id)
        tid = uuid.UUID(team_id)

        # Direct user grants
        user_perms = await self.perm_repo.find_by_subject(
            "user", uid, resource_type
        )
        # Team grants
        team_perms = await self.perm_repo.find_by_subject(
            "team", tid, resource_type
        )

        # Merge: group by resource_id
        resources: dict[str, dict] = {}
        for p in user_perms:
            rid = str(p.resource_id)
            if rid not in resources:
                resources[rid] = {
                    "resource_type": p.resource_type,
                    "resource_id": rid,
                    "actions": set(),
                    "source": "direct_grant",
                }
            resources[rid]["actions"].add(p.action)

        for p in team_perms:
            rid = str(p.resource_id)
            if rid not in resources:
                resources[rid] = {
                    "resource_type": p.resource_type,
                    "resource_id": rid,
                    "actions": set(),
                    "source": "team_grant",
                }
            resources[rid]["actions"].add(p.action)

        # Convert sets to sorted lists for JSON serialization
        result = []
        for r in resources.values():
            r["actions"] = sorted(r["actions"])
            result.append(r)

        return result
