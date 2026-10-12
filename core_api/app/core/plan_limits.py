"""
Plan limit checking — fetches team plan from auth service and enforces limits.
"""

import logging

from fastapi import HTTPException

from app.clients.auth_client import get_team_plan

logger = logging.getLogger(__name__)


async def check_plan_limit(
    resource: str,
    team_id: str,
    current_count: int,
) -> None:
    """Check if the current count exceeds the plan limit for a resource.

    Args:
        resource: The limit key (e.g., "calls_per_month", "team_members").
        team_id: The team UUID string.
        current_count: Current usage count.

    Raises:
        HTTPException 403 if over limit.
    """
    plan_info = await get_team_plan(team_id)
    if not plan_info:
        # If we can't reach auth service, fail open (don't block the user)
        logger.warning(f"Could not fetch plan for team {team_id}, allowing action")
        return

    limits = plan_info.get("limits", {})
    limit_value = limits.get(resource)

    if limit_value is None:
        return  # No limit defined for this resource

    if limit_value == -1:
        return  # Unlimited

    if current_count >= limit_value:
        plan_name = plan_info.get("plan", "current")
        raise HTTPException(
            403,
            f"Plan limit reached: your {plan_name} plan allows {limit_value} {resource.replace('_', ' ')}. "
            f"Please upgrade your plan to continue.",
        )
