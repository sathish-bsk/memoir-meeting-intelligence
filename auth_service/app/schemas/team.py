"""
Team management schemas.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


class CreateTeamRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: Optional[str] = Field(None, min_length=1, max_length=255, pattern=r"^[a-z0-9-]+$")


class UpdateTeamRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)


class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: str = Field(default="member", pattern=r"^(member|admin)$")


class ChangeMemberRoleRequest(BaseModel):
    role: str = Field(pattern=r"^(member|admin|owner)$")


class TeamMemberResponse(BaseModel):
    id: str
    user_id: str
    email: str
    name: str
    role: str
    joined_at: datetime

    model_config = {"from_attributes": True}


class TeamResponse(BaseModel):
    id: str
    name: str
    slug: str
    created_by: str
    member_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TeamDetailResponse(TeamResponse):
    members: List[TeamMemberResponse] = []


class SessionResponse(BaseModel):
    id: str
    device_name: Optional[str] = None
    ip_address: Optional[str] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime
    is_current: bool = False

    model_config = {"from_attributes": True}


class PlanInfo(BaseModel):
    plan: str
    limits: dict
    trial_ends_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class UpdatePlanRequest(BaseModel):
    email: str
    plan: str = Field(pattern=r"^(trial|free|paid)$")
    plan_limits: Optional[dict] = None


class ScopeOverridesRequest(BaseModel):
    overrides: dict[str, bool] = Field(
        ...,
        description="Scope overrides: {scope: true/false}. Only content module scopes allowed.",
    )


class MemberScopesResponse(BaseModel):
    effective_scopes: list[str]
    role_defaults: list[str]
    overrides: dict[str, bool]

    model_config = {"from_attributes": True}


class AdminTeamLookup(BaseModel):
    user_id: str
    user_name: str
    user_email: str
    team_id: str
    team_name: str
    plan: str
    plan_limits: dict
    trial_ends_at: Optional[datetime] = None
