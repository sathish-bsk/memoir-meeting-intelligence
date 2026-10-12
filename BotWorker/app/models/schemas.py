"""
Pydantic request/response/event models.
"""
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field

from app.models.enums import BotState, EventType


class JoinRequest(BaseModel):
    job_id: str
    meeting_url: str
    bot_name: Optional[str] = None
    # Unix seconds (float) when orchestrator POSTed /join. Used by phase_log
    # to compute t_since_dispatch_ms for every phase. Optional so older
    # orchestrators that don't send it still work — phase_log just won't
    # emit t_since_dispatch_ms in that case.
    dispatch_sent_at: Optional[float] = None
    # Set to True by the orchestrator when claiming a warm pool member, so the
    # warmed container (started with AUTO_EXIT=0 so it would idle until /join)
    # exits after this one meeting, matching today's one-shot cold-bot
    # behavior. Cold bots ignore this — they already have AUTO_EXIT=1 from
    # the orchestrator's container env, and this flag would be a no-op.
    auto_exit_after: bool = False


class StatusResponse(BaseModel):
    job_id: Optional[str] = None
    state: BotState = BotState.IDLE
    meeting_url: Optional[str] = None
    joined_at: Optional[datetime] = None
    chunks_saved: int = 0
    uptime_seconds: float = 0.0
    transcript: Optional[str] = None
    error: Optional[str] = None


class StopRequest(BaseModel):
    reason: Optional[str] = "manual_stop"


class HealthResponse(BaseModel):
    status: str = "ok"
    state: BotState = BotState.IDLE


class BotEvent(BaseModel):
    job_id: str
    event_type: EventType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    data: dict[str, Any] = Field(default_factory=dict)


class ChunkSavedData(BaseModel):
    chunk_index: int
    storage_key: str
    duration_seconds: float = 0.0
    file_size_bytes: int = 0


class TranscriptionResult(BaseModel):
    transcript: str
    duration_seconds: float = 0.0
