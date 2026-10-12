"""
AI prompt log model — per-prompt token usage and cost tracking.

Covers ALL cost-incurring pipeline steps:
- LLM prompts: P1_classification, P2_crm, P3_intelligence, P4_pmf, P5_summary, P6_faq, digest
- Infrastructure: asr, diarization, llm_corrector (GPU-based cost)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class AiPromptLog(Base):
    __tablename__ = "ai_prompt_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    team_id = Column(UUID(as_uuid=True), nullable=False)

    # Step identification: P1_classification, P2_crm, asr, diarization, llm_corrector, digest, etc.
    prompt_name = Column(String(50), nullable=False)
    provider = Column(String(20), nullable=True)
    model = Column(String(100), nullable=True)

    # Token usage (LLM prompts)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    cache_read_tokens = Column(Integer, nullable=False, default=0)
    # Tier 4.0.a — Anthropic prompt caching distinguishes cache writes
    # (1.25× base for 5m TTL, 2× for 1h) from cache reads (0.1× base).
    # Without this column, write costs either get under-attributed via
    # input_tokens or disappear entirely. Defaults to 0 for non-Claude
    # providers (Ollama/OpenAI).
    cache_creation_tokens = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Float, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    # Operation context (added in migration 023)
    operation_type = Column(String(30), nullable=True)    # transcription / retranscribe / crm_extraction / intelligence_rerun
    gpu_type = Column(String(50), nullable=True)          # T4 / CPU — for ASR/diarization rows
    audio_duration_seconds = Column(Float, nullable=True)  # for ASR rows
    processing_seconds = Column(Float, nullable=True)      # wall-clock for non-LLM steps
    recording_source = Column(String(20), nullable=True)   # bot / zoom_cloud / extension / upload / teams_cloud
    triggered_by = Column(UUID(as_uuid=True), nullable=True)  # user who triggered (null for auto)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_ai_prompt_log_meeting", "meeting_id"),
        Index("ix_ai_prompt_log_team_created", "team_id", "created_at"),
        Index("ix_ai_prompt_log_team_source", "team_id", "recording_source"),
        Index("ix_ai_prompt_log_operation", "team_id", "operation_type"),
    )
