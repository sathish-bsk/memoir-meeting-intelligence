"""
Pydantic schemas for Slack integration endpoints.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class SlackAuthUrlResponse(BaseModel):
    auth_url: str


class SlackCallbackRequest(BaseModel):
    code: str
    state: str


class SlackNotificationSettingsResponse(BaseModel):
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    notify_on_meeting_complete: bool = True
    notify_on_crm_pushed: bool = True
    notify_on_blocker_detected: bool = True
    notify_on_team_invite: bool = False

    model_config = {"from_attributes": True}


class SlackNotificationSettingsUpdate(BaseModel):
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    notify_on_meeting_complete: Optional[bool] = None
    notify_on_crm_pushed: Optional[bool] = None
    notify_on_blocker_detected: Optional[bool] = None
    notify_on_team_invite: Optional[bool] = None


class SlackConnectionResponse(BaseModel):
    id: UUID
    user_id: UUID
    workspace_id: str
    workspace_name: Optional[str] = None
    scopes: Optional[list[str]] = None
    notification_settings: Optional[SlackNotificationSettingsResponse] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SlackChannelInfo(BaseModel):
    id: str
    name: str
    is_private: bool = False


class SlackChannelsResponse(BaseModel):
    channels: list[SlackChannelInfo]
