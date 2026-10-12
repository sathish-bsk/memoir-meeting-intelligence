"""
Pydantic schemas for permission endpoints.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ─── Internal check endpoints ────────────────────────────────────

class PermissionCheckRequest(BaseModel):
    user_id: str
    team_id: str
    team_role: Optional[str] = None
    resource_type: str
    resource_id: str
    action: str


class PermissionCheckResponse(BaseModel):
    allowed: bool
    source: str  # "direct_grant" | "team_grant" | "role_scope"


class BatchCheckItem(BaseModel):
    resource_type: str
    resource_id: str
    action: str


class BatchCheckRequest(BaseModel):
    user_id: str
    team_id: str
    team_role: Optional[str] = None
    checks: List[BatchCheckItem]


class BatchCheckResponse(BaseModel):
    results: dict[str, bool]  # resource_id → allowed


# ─── User-facing sharing endpoints ──────────────────────────────

class GrantPermissionRequest(BaseModel):
    subject_type: str = Field(pattern=r"^(user|team)$")
    subject_id: str
    preset: Optional[str] = Field(None, pattern=r"^(viewer|editor|owner)$")
    actions: Optional[List[str]] = None


class UpdatePermissionRequest(BaseModel):
    preset: Optional[str] = Field(None, pattern=r"^(viewer|editor|owner)$")
    actions: Optional[List[str]] = None


class PermissionEntryResponse(BaseModel):
    id: str
    subject_type: str
    subject_id: str
    actions: List[str]
    granted_by: Optional[str] = None
    created_at: str


class AccessibleResourceResponse(BaseModel):
    resource_type: str
    resource_id: str
    actions: List[str]
    source: str  # "direct_grant" | "team_grant"
