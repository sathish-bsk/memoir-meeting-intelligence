"""
Auth-related request/response schemas.
"""

from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str = Field(min_length=1, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class LogoutRequest(BaseModel):
    """Optionally specify which session to revoke. If omitted, revokes current."""
    refresh_token: Optional[str] = None


class SwitchTeamRequest(BaseModel):
    team_id: str


class VerifyResponse(BaseModel):
    """Response for ForwardAuth /auth/verify endpoint."""
    user_id: str
    email: str
    name: str
    team_id: Optional[str] = None
    team_role: Optional[str] = None
    plan: Optional[str] = None
