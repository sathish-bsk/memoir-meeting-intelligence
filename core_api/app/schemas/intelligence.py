"""
Pydantic schemas for intelligence, coaching, memoir, search, and dashboard.
"""

from datetime import date, datetime, time
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class CoachingResponse(BaseModel):
    """Coaching metrics from analysis.json."""

    meeting_id: UUID
    coaching_metrics: dict[str, Any]


class IntelligenceResponse(BaseModel):
    """Intelligence data from analysis.json."""

    meeting_id: UUID
    intelligence: dict[str, Any]


class MemoirResponse(BaseModel):
    """Memoir section from analysis.json."""

    meeting_id: UUID
    narrative_summary: Optional[str] = None
    discovery_fields: Optional[list[dict[str, Any]]] = None
    action_items: Optional[list[dict[str, Any]]] = None
    signals: Optional[dict[str, Any]] = None
    next_steps: Optional[dict[str, Any]] = None
    call_metadata: Optional[dict[str, Any]] = None
    disclaimer: Optional[str] = None
    overall_confidence_score: Optional[int] = None
    is_edited: bool = False


class FieldEditRequest(BaseModel):
    """Request to edit a CRM field in the memoir."""

    field_name: str
    value: str
    confidence: Optional[str] = None


class MemoirEditRequest(BaseModel):
    """Request to edit memoir fields (narrative summary, action items, signals, next steps)."""

    narrative_summary: Optional[str] = None
    action_items: Optional[list[dict[str, Any]]] = None
    signals: Optional[dict[str, Any]] = None
    next_steps: Optional[dict[str, Any]] = None


class SearchResult(BaseModel):
    """A single search result."""

    meeting_id: UUID
    title: Optional[str] = None
    prospect_name: Optional[str] = None
    prospect_company: Optional[str] = None
    snippet: str
    score: float
    date: Optional[datetime] = None


class SearchResponse(BaseModel):
    """Search results response."""

    results: list[SearchResult]
    total: int
    query: str


class EnhancedSearchResult(BaseModel):
    """A single search result with type and timestamp."""

    meeting_id: UUID
    title: Optional[str] = None
    prospect_name: Optional[str] = None
    prospect_company: Optional[str] = None
    snippet: str
    score: float
    date: Optional[datetime] = None
    result_type: str  # "call" or "transcript"
    timestamp_seconds: Optional[int] = None


class EnhancedSearchResponse(BaseModel):
    """Grouped search results with counts."""

    call_results: list[EnhancedSearchResult]
    transcript_results: list[EnhancedSearchResult]
    call_total: int
    transcript_total: int
    query: str


class DashboardStats(BaseModel):
    """Dashboard statistics."""

    total_meetings: int = 0
    total_minutes: int = 0
    total_speakers: int = 0
    this_week_meetings: int = 0
    last_week_meetings: int = 0


# ---------------------------------------------------------------------------
# Call Intelligence — per-call 6-prompt pipeline results
# ---------------------------------------------------------------------------

class CallIntelligenceResponse(BaseModel):
    """Full per-call intelligence extraction results."""

    id: UUID
    meeting_id: UUID
    call_type: str
    classification_confidence: Optional[str] = None

    crm_extraction: Optional[dict[str, Any]] = None
    pain_points: Optional[list[dict[str, Any]]] = None
    desired_outcomes: Optional[list[dict[str, Any]]] = None
    feature_requests: Optional[list[dict[str, Any]]] = None
    competitors_mentioned: Optional[list[dict[str, Any]]] = None
    objections: Optional[list[dict[str, Any]]] = None
    buying_signals: Optional[list[dict[str, Any]]] = None
    red_flags: Optional[list[dict[str, Any]]] = None
    tech_stack: Optional[list[dict[str, Any]]] = None
    budget_signals: Optional[dict[str, Any]] = None
    decision_process: Optional[dict[str, Any]] = None
    product_perception: Optional[dict[str, Any]] = None

    pmf_signals: Optional[dict[str, Any]] = None
    pull_score: Optional[int] = None
    icp_fit_score: Optional[int] = None
    pain_fit_score: Optional[int] = None
    overall_pmf_signal: Optional[str] = None

    # Deal intelligence
    deal_momentum: Optional[str] = None
    validated_stage: Optional[str] = None
    crm_stage_id: Optional[str] = None
    crm_stage_label: Optional[str] = None
    has_committed_next_step: Optional[bool] = None
    next_step_type: Optional[str] = None

    summary: Optional[dict[str, Any]] = None
    call_health: Optional[str] = None
    questions: Optional[list[dict[str, Any]]] = None

    total_tokens_used: Optional[int] = None
    total_cost_usd: Optional[float] = None
    processing_status: str = "completed"
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Aggregation schemas — used by /api/intelligence/* endpoints
# ---------------------------------------------------------------------------

class MentionDetail(BaseModel):
    """A single mention/quote from a specific call."""

    meeting_id: Optional[UUID] = None
    exact_quote: Optional[str] = None
    speaker_name: Optional[str] = None
    prospect_company: Optional[str] = None
    timestamp_in_call: Optional[str] = None
    call_date: Optional[str] = None


class PainPointAggregation(BaseModel):
    """Aggregated pain point across calls."""

    pain: str
    mention_count: int
    avg_severity: Optional[float] = None
    quotes: list[str] = Field(default_factory=list)
    mentions: list[MentionDetail] = Field(default_factory=list)
    call_ids: list[UUID] = Field(default_factory=list)


class FeatureRequestAggregation(BaseModel):
    """Aggregated feature request across calls."""

    feature: str
    request_count: int
    urgency_breakdown: Optional[dict[str, int]] = None
    mentions: list[MentionDetail] = Field(default_factory=list)
    call_ids: list[UUID] = Field(default_factory=list)


class CompetitorAggregation(BaseModel):
    """Aggregated competitor mentions across calls."""

    competitor: str
    mention_count: int
    sentiment_breakdown: Optional[dict[str, int]] = None
    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    mentions: list[MentionDetail] = Field(default_factory=list)
    call_ids: list[UUID] = Field(default_factory=list)


class ObjectionAggregation(BaseModel):
    """Aggregated objections across calls."""

    objection: str
    count: int
    category: Optional[str] = None
    resolution_rate: Optional[float] = None
    mentions: list[MentionDetail] = Field(default_factory=list)
    call_ids: list[UUID] = Field(default_factory=list)


class QuestionAggregation(BaseModel):
    """Aggregated prospect question across calls."""

    question: str
    ask_count: int
    categories: list[str] = Field(default_factory=list)
    mentions: list[MentionDetail] = Field(default_factory=list)
    call_ids: list[UUID] = Field(default_factory=list)
    has_unanswered: bool = False


class PMFTrendPoint(BaseModel):
    """PMF signals for a single time period."""

    period_start: date
    period_end: date
    avg_pull_score: Optional[float] = None
    avg_pain_fit: Optional[float] = None
    avg_icp_fit: Optional[float] = None
    call_count: int = 0


# ---------------------------------------------------------------------------
# Digest schemas
# ---------------------------------------------------------------------------

class DigestResponse(BaseModel):
    """Weekly/periodic intelligence digest."""

    id: UUID
    team_id: UUID
    period_start: date
    period_end: date
    narrative: Optional[str] = None
    metrics: Optional[dict[str, Any]] = None
    total_calls: int = 0
    cost_usd: Optional[float] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class DigestListResponse(BaseModel):
    """Paginated list of digests."""

    digests: list[DigestResponse]
    total: int


# ---------------------------------------------------------------------------
# Scheduled job schemas — user-configurable digest settings
# ---------------------------------------------------------------------------

class DigestSettingsResponse(BaseModel):
    """Current digest schedule settings."""

    id: UUID
    frequency: str
    preferred_time: Optional[time] = None
    preferred_day: Optional[int] = None
    enabled: bool = True
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_status: Optional[str] = None

    model_config = {"from_attributes": True}


class DigestSettingsUpdate(BaseModel):
    """Update digest schedule settings."""

    frequency: Optional[str] = None
    preferred_time: Optional[time] = None
    preferred_day: Optional[int] = None
    enabled: Optional[bool] = None


class JobRunResponse(BaseModel):
    """A single scheduled job run (audit log entry)."""

    id: UUID
    job_type: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: str
    result_summary: Optional[str] = None
    error_message: Optional[str] = None
    records_processed: int = 0
    cost_usd: Optional[float] = None

    model_config = {"from_attributes": True}


class JobRunListResponse(BaseModel):
    """Paginated list of job runs."""

    runs: list[JobRunResponse]
    total: int


# ---------------------------------------------------------------------------
# Intelligence overview — single call for dashboard initial load
# ---------------------------------------------------------------------------

class IntelligenceOverview(BaseModel):
    """Top-level intelligence dashboard response."""

    call_count: int = 0
    date_range_start: Optional[date] = None
    date_range_end: Optional[date] = None

    pain_points: list[PainPointAggregation] = Field(default_factory=list)
    feature_requests: list[FeatureRequestAggregation] = Field(default_factory=list)
    competitors: list[CompetitorAggregation] = Field(default_factory=list)
    objections: list[ObjectionAggregation] = Field(default_factory=list)
    questions: list[QuestionAggregation] = Field(default_factory=list)
    pmf_trend: list[PMFTrendPoint] = Field(default_factory=list)

    current_digest: Optional[DigestResponse] = None


# ---------------------------------------------------------------------------
# Deal Intelligence schemas
# ---------------------------------------------------------------------------

class DealStageCall(BaseModel):
    """A call within a deal stage."""
    meeting_id: UUID
    title: Optional[str] = None
    prospect_name: Optional[str] = None
    prospect_company: Optional[str] = None
    date: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    pull_score: Optional[int] = None
    deal_momentum: Optional[str] = None
    validated_stage: Optional[str] = None
    crm_stage_label: Optional[str] = None
    call_health: Optional[str] = None
    has_committed_next_step: Optional[bool] = None

class DealStageSummary(BaseModel):
    """Summary of a CRM deal stage with call count."""
    stage_label: str
    stage_id: Optional[str] = None
    call_count: int = 0
    display_order: Optional[int] = None

class DealStagesResponse(BaseModel):
    """Response for deal stages overview."""
    has_crm: bool = False
    stages: list[DealStageSummary] = Field(default_factory=list)
    unassigned_count: int = 0

class DealStageCallsResponse(BaseModel):
    """Paginated calls for a specific deal stage."""
    calls: list[DealStageCall] = Field(default_factory=list)
    total: int = 0
    stage_label: str = ""

class DealTimelineEntry(BaseModel):
    """A single entry in the deal timeline."""
    meeting_id: UUID
    title: Optional[str] = None
    date: Optional[datetime] = None
    call_number: Optional[int] = None
    validated_stage: Optional[str] = None
    crm_stage_label: Optional[str] = None
    call_health: Optional[str] = None
    deal_momentum: Optional[str] = None
    pull_score: Optional[int] = None
    one_line_summary: Optional[str] = None

class DealTimelineResponse(BaseModel):
    """Timeline of all calls in a deal."""
    deal_id: UUID
    prospect_company: Optional[str] = None
    prospect_name: Optional[str] = None
    status: Optional[str] = None
    current_stage: Optional[str] = None
    total_calls: int = 0
    entries: list[DealTimelineEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# PMF Weekly Digest schemas
# ---------------------------------------------------------------------------

class PmfSignalItem(BaseModel):
    """A single item in a PMF dimension (feature/objection/competitor)."""
    value: str
    calls_mentioned: int = 0
    total_calls: int = 0
    stars: int = 1
    trend: str = "same"  # up | down | same | new
    prev_count: int = 0
    mentions: int = 0

class PmfSignalsResponse(BaseModel):
    """Full PMF weekly digest response with 5 dimensions."""
    week_start: Optional[date] = None
    total_calls: int = 0
    feature_demand: list[PmfSignalItem] = Field(default_factory=list)
    feature_demand_stars: int = 0
    objections: list[PmfSignalItem] = Field(default_factory=list)
    objections_stars: int = 0
    competitors: list[PmfSignalItem] = Field(default_factory=list)
    competitors_stars: int = 0
    buy_signal_rate: Optional[float] = None
    buy_signal_stars: int = 0
    buy_signal_prev_rate: Optional[float] = None
    pain_clarity_rate: Optional[float] = None
    pain_clarity_stars: int = 0
    pain_clarity_top_pain: Optional[str] = None
    pain_clarity_prev_rate: Optional[float] = None
    narrative: Optional[str] = None

class PmfSignalCallsResponse(BaseModel):
    """Paginated calls for a specific PMF signal drill-down."""
    calls: list[DealStageCall] = Field(default_factory=list)
    total: int = 0
    signal_type: str = ""
    signal_value: Optional[str] = None

class PmfHistoryEntry(BaseModel):
    """Weekly PMF scores for trend chart."""
    week_start: date
    total_calls: int = 0
    feature_demand_stars: int = 0
    objections_stars: int = 0
    competitors_stars: int = 0
    buy_signal_stars: int = 0
    pain_clarity_stars: int = 0

class PmfHistoryResponse(BaseModel):
    """PMF history over multiple weeks."""
    weeks: list[PmfHistoryEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# CRM Pipeline schemas
# ---------------------------------------------------------------------------

class CrmPipelineStage(BaseModel):
    """A single CRM pipeline stage."""
    id: str
    label: str
    pipeline_id: Optional[str] = None
    pipeline_name: Optional[str] = None
    display_order: int = 0

class CrmPipelineResponse(BaseModel):
    """CRM pipeline stages response."""
    provider: str
    stages: list[CrmPipelineStage] = Field(default_factory=list)
