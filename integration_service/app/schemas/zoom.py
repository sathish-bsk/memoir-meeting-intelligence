"""
Pydantic schemas for Zoom integration endpoints.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ZoomAuthUrlResponse(BaseModel):
    auth_url: str


class ZoomCallbackRequest(BaseModel):
    code: str
    state: str


class ZoomConnectionResponse(BaseModel):
    id: UUID
    user_id: UUID
    zoom_email: str = Field(validation_alias="provider_email")
    auto_import: bool = True
    recording_preference: str = "auto"
    cloud_recording_enabled: Optional[bool] = None
    created_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True}


class ZoomSettingsUpdate(BaseModel):
    auto_import: Optional[bool] = None
    recording_preference: Optional[str] = None
