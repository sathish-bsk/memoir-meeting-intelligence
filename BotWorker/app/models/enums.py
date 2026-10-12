"""
Enumerations for bot state and event types.
"""
from enum import Enum


class BotState(str, Enum):
    IDLE = "idle"
    # Pool-member-only intermediate states. WARMING = Chromium is launching;
    # WARM = Chromium at about:blank, waiting for /join. Cold bots (the only
    # path before the pre-warmed pool work) never enter these states.
    WARMING = "warming"
    WARM = "warm"
    LAUNCHING_BROWSER = "launching_browser"
    NAVIGATING = "navigating"
    WAITING_ROOM = "waiting_room"
    IN_MEETING = "in_meeting"
    MEETING_ENDED = "meeting_ended"
    TRANSCRIBING = "transcribing"
    CLEANING_UP = "cleaning_up"
    COMPLETED = "completed"
    ERROR = "error"


class EventType(str, Enum):
    BOT_READY = "bot_ready"
    JOIN_STARTED = "join_started"
    WAITING_ROOM_ENTERED = "waiting_room_entered"
    JOINED_MEETING = "joined_meeting"
    RECORDING_STARTED = "recording_started"
    CHUNK_SAVED = "chunk_saved"
    HEARTBEAT = "heartbeat"
    MEETING_ENDED = "meeting_ended"
    RECORDING_COMPLETE = "recording_complete"
    TRANSCRIPTION_STARTED = "transcription_started"
    TRANSCRIPTION_COMPLETE = "transcription_complete"
    TRANSCRIPTION_FAILED = "transcription_failed"
    BOT_DENIED = "bot_denied"
    BOT_KICKED = "bot_kicked"
    BOT_ERROR = "bot_error"
    CLEANUP_COMPLETE = "cleanup_complete"
