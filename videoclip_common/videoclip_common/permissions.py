"""
Role-based permission checking for downstream services.
Matches auth_service/app/dependencies.py require_role().
"""

from fastapi import Depends, HTTPException

from videoclip_common.auth import UserContext, get_current_user

ROLE_HIERARCHY = {"member": 0, "admin": 1, "owner": 2}


def require_role(min_role: str):
    """Dependency factory: require a minimum team role.

    Usage:
        @router.post("/teams/{id}/invite")
        async def invite(user: UserContext = Depends(require_role("admin"))):
            ...
    """

    async def check(user: UserContext = Depends(get_current_user)) -> UserContext:
        if not user.team_id:
            raise HTTPException(403, "No team context")
        user_level = ROLE_HIERARCHY.get(user.team_role, -1)
        required_level = ROLE_HIERARCHY.get(min_role, 0)
        if user_level < required_level:
            raise HTTPException(403, f"Requires {min_role} role or above")
        return user

    return check
