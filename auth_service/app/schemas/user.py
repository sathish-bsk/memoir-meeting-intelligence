"""
User profile schemas.
"""

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, EmailStr, Field


class UserProfile(BaseModel):
    id: str
    email: str
    email_verified: bool
    name: str
    avatar_url: Optional[str] = None
    is_active: bool
    has_password: bool
    onboarding_completed: bool = False
    oauth_providers: List[str] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    avatar_url: Optional[str] = Field(None, max_length=2048)
    onboarding_completed: Optional[bool] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


class ChangeEmailRequest(BaseModel):
    new_email: EmailStr
    password: str
