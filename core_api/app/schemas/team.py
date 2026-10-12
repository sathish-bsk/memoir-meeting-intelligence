"""
Pydantic schemas for team CRUD operations.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class TeamCreate(BaseModel):
    """Schema for creating a new team."""

    id: Optional[UUID] = None  # Pass auth_service team_id to keep IDs in sync
    name: str = Field(..., min_length=1, max_length=255)
    crm_type: Optional[str] = None
    team_size_range: Optional[str] = None
    intelligence_priority: Optional[str] = None
    domain: Optional[str] = None


class TeamUpdate(BaseModel):
    """Schema for updating an existing team."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    crm_type: Optional[str] = None
    team_size_range: Optional[str] = None
    intelligence_priority: Optional[str] = None
    company_description: Optional[str] = None
    icp_description: Optional[str] = None
    known_competitors: Optional[list[str]] = None
    digest_day: Optional[str] = None
    blocker_alerts: Optional[bool] = None


class TeamResponse(BaseModel):
    """Schema for team response."""

    id: UUID
    name: str
    slug: str
    created_by: UUID
    crm_type: Optional[str] = None
    team_size_range: Optional[str] = None
    intelligence_priority: Optional[str] = None
    created_at: datetime
    member_count: int = 0

    model_config = {"from_attributes": True}


class TeamMemberResponse(BaseModel):
    """Schema for team member response."""

    id: UUID
    user_id: UUID
    email: Optional[str] = None
    name: Optional[str] = None
    role: str
    invite_status: str
    joined_at: Optional[datetime] = None
    calls_this_week: int = 0
    requested_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class InviteCreate(BaseModel):
    """Schema for creating a team invite."""

    email: str = Field(..., min_length=1)


class InviteResponse(BaseModel):
    """Schema for invite response."""

    invite_code: str
    team_name: str


class JoinTeamRequest(BaseModel):
    """Schema for joining a team via invite code."""

    invite_code: str = Field(..., min_length=1)


class RequestJoinBody(BaseModel):
    """Schema for requesting to join a team via domain-based lookup."""

    message: Optional[str] = Field(None, max_length=500)
