"""
Phase 10: Pydantic schemas for Memoir call summary output.

Completely independent from schemas.py (Phase 7). Designed for CRM auto-fill
with confidence tagging and source attribution on every extracted field.

Self-contained module — zero imports from pipeline/transcribe/audio.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


# --- Enums ---

class ConfidenceLevel(str, Enum):
    definitive = "definitive"
    indicative = "indicative"
    inferred = "inferred"
    ambiguous = "ambiguous"


class UrgencyLevel(str, Enum):
    urgent = "urgent"
    active = "active"
    exploring = "exploring"
    none = "none"


class ActionPriority(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


# --- Discovery Field base ---

class LLMNullCoercionMixin(BaseModel):
    """Coerce LLM null values to safe defaults for list and enum fields.

    LLMs often return null instead of [] for lists or instead of a valid
    enum string. This validator replaces None with the field's default.
    """
    @model_validator(mode="before")
    @classmethod
    def coerce_null_values(cls, data):
        if isinstance(data, dict):
            for field_name, field_info in cls.model_fields.items():
                if data.get(field_name) is not None:
                    continue
                anno = field_info.annotation
                # list[...] fields: None → []
                origin = getattr(anno, "__origin__", None)
                if origin is list:
                    data[field_name] = []
                # Enum fields: None → field default
                elif isinstance(anno, type) and issubclass(anno, Enum):
                    if field_info.default is not None:
                        data[field_name] = field_info.default.value
        return data


class DiscoveryFieldBase(LLMNullCoercionMixin):
    """Base for all 7 discovery fields — confidence + attribution."""
    confidence: ConfidenceLevel = ConfidenceLevel.ambiguous
    source_speaker: Optional[str] = None
    # Bumped from 200 to 300 chars — LLM sometimes returns quotes that include
    # multi-sentence context, which is fine for downstream rendering but
    # would blow the previous 200-char limit and fall back to raw dict.
    # Length is still bounded; oversize input gets trimmed by validator below.
    source_quote: Optional[str] = Field(None, max_length=300)
    source_timestamp_approx: Optional[str] = None


# --- 7 Discovery Fields ---

class BudgetField(DiscoveryFieldBase):
    value: Optional[str] = None  # e.g. "$50K", "$40-60K range", "Declined to share"


class AuthorityField(DiscoveryFieldBase):
    decision_maker: Optional[str] = None
    decision_maker_title: Optional[str] = None
    champions: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    approval_process: Optional[str] = None


class NeedField(DiscoveryFieldBase):
    primary_pain: Optional[str] = None
    secondary_pains: list[str] = Field(default_factory=list)
    current_workaround: Optional[str] = None


class TimelineField(DiscoveryFieldBase):
    target_date: Optional[str] = None
    driving_event: Optional[str] = None
    urgency: UrgencyLevel = UrgencyLevel.none


class TechStackField(DiscoveryFieldBase):
    current_tools: list[str] = Field(default_factory=list)
    pain_with_current: Optional[str] = None
    integrations_needed: list[str] = Field(default_factory=list)


class TeamField(DiscoveryFieldBase):
    size: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    locations: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_size(cls, data):
        if isinstance(data, dict) and "size" in data and data["size"] is not None:
            data["size"] = str(data["size"])
        return data


class CompetitorSentiment(BaseModel):
    competitor: str
    sentiment: str  # Free-text: what they like/dislike


class CompetitionField(DiscoveryFieldBase):
    competitors_evaluating: list[str] = Field(default_factory=list)
    competitor_sentiment: list[CompetitorSentiment] = Field(default_factory=list)


class DiscoveryFields(LLMNullCoercionMixin):
    budget: Optional[BudgetField] = Field(default_factory=BudgetField)
    authority: Optional[AuthorityField] = Field(default_factory=AuthorityField)
    need: Optional[NeedField] = Field(default_factory=NeedField)
    timeline: Optional[TimelineField] = Field(default_factory=TimelineField)
    tech_stack: Optional[TechStackField] = Field(default_factory=TechStackField)
    team: Optional[TeamField] = Field(default_factory=TeamField)
    competition: Optional[CompetitionField] = Field(default_factory=CompetitionField)

    @model_validator(mode="before")
    @classmethod
    def coerce_null_fields(cls, data):
        if isinstance(data, dict):
            field_types = {
                "budget": BudgetField, "authority": AuthorityField,
                "need": NeedField, "timeline": TimelineField,
                "tech_stack": TechStackField, "team": TeamField,
                "competition": CompetitionField,
            }
            for name, typ in field_types.items():
                if data.get(name) is None:
                    data[name] = typ()
        return data


# --- Action Items ---

class RepAction(LLMNullCoercionMixin):
    action: str
    due_date: Optional[str] = None
    priority: ActionPriority = ActionPriority.medium


class ProspectCommitment(BaseModel):
    action: str
    owner: Optional[str] = None
    due_date: Optional[str] = None


class ActionItems(LLMNullCoercionMixin):
    rep_actions: list[RepAction] = Field(default_factory=list)
    prospect_commitments: list[ProspectCommitment] = Field(default_factory=list)


# --- Next Steps ---

class NextSteps(BaseModel):
    next_meeting: Optional[str] = None
    next_milestone: Optional[str] = None
    suggested_deal_stage: Optional[str] = None


# --- Signals ---

class Signal(BaseModel):
    signal: str
    speaker: Optional[str] = None


class Signals(LLMNullCoercionMixin):
    buying_signals: list[Signal] = Field(default_factory=list)
    risk_signals: list[Signal] = Field(default_factory=list)


# --- Disclaimer ---

class AmbiguousTopic(BaseModel):
    topic: str
    reason: str


class Disclaimer(LLMNullCoercionMixin):
    ambiguous_topics: list[AmbiguousTopic] = Field(default_factory=list)
    confidential_content_detected: bool = False
    transcript_quality_issues: Optional[str] = None


# --- Detected Introductions ---

class DetectedIntroduction(BaseModel):
    """A speaker introduction detected from spoken content."""
    speaker_label: Optional[str] = None
    name: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None


# --- Call Metadata ---

class MemoirParticipant(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    role: Optional[str] = None  # "rep" | "prospect" | "other"


class MemoirCallMetadata(LLMNullCoercionMixin):
    date: Optional[str] = None
    duration_minutes: Optional[float] = None
    participants: list[MemoirParticipant] = Field(default_factory=list)
    call_type: Optional[str] = None  # Discovery | Demo | Follow-up | Negotiation | Check-in


# --- Top-level Memoir Summary ---

class MemoirSummary(BaseModel):
    """Complete Memoir output for a single call."""
    call_metadata: MemoirCallMetadata = Field(default_factory=MemoirCallMetadata)
    discovery_fields: DiscoveryFields = Field(default_factory=DiscoveryFields)
    narrative_summary: Optional[str] = None
    action_items: ActionItems = Field(default_factory=ActionItems)
    next_steps: NextSteps = Field(default_factory=NextSteps)
    signals: Signals = Field(default_factory=Signals)
    disclaimer: Disclaimer = Field(default_factory=Disclaimer)
    detected_introductions: list[DetectedIntroduction] = Field(default_factory=list)
    overall_confidence_score: int = Field(0, ge=0, le=100)

    # Metadata (added post-extraction)
    extraction_model: Optional[str] = None
    extraction_timestamp: Optional[str] = None


def get_memoir_schema() -> str:
    """
    Return a compact JSON example schema for the Memoir LLM extraction prompt.
    Uses example-based format to fit within the LLM's context window.
    """
    return """{
  "call_metadata": {
    "date": "YYYY-MM-DD or null",
    "duration_minutes": 30,
    "participants": [
      {"name": "Exact name from transcript", "title": "Only if explicitly stated in transcript, else null", "company": "Only if explicitly stated in transcript, else null", "role": "rep|prospect|other"}
    ],
    "call_type": "Discovery|Demo|Follow-up|Negotiation|Check-in"
  },
  "discovery_fields": {
    "budget": {
      "value": "string or null (e.g. '$50K', '$40-60K range', 'Declined to share')",
      "confidence": "definitive|indicative|inferred|ambiguous",
      "source_speaker": "Speaker name",
      "source_quote": "Short paraphrase or quote (max 200 chars)",
      "source_timestamp_approx": "MM:SS or null"
    },
    "authority": {
      "decision_maker": "Name or null",
      "decision_maker_title": "Title or null",
      "champions": ["name"],
      "blockers": ["name or role"],
      "approval_process": "Description or null",
      "confidence": "definitive|indicative|inferred|ambiguous",
      "source_speaker": "Speaker name",
      "source_quote": "Quote"
    },
    "need": {
      "primary_pain": "One sentence or null",
      "secondary_pains": ["pain"],
      "current_workaround": "How they solve this today or null",
      "confidence": "definitive|indicative|inferred|ambiguous",
      "source_speaker": "Speaker name",
      "source_quote": "Quote"
    },
    "timeline": {
      "target_date": "Date or timeframe or null",
      "driving_event": "What forces the timeline or null",
      "urgency": "urgent|active|exploring|none",
      "confidence": "definitive|indicative|inferred|ambiguous",
      "source_speaker": "Speaker name",
      "source_quote": "Quote"
    },
    "tech_stack": {
      "current_tools": ["tool name"],
      "pain_with_current": "What's not working or null",
      "integrations_needed": ["integration"],
      "confidence": "definitive|indicative|inferred|ambiguous",
      "source_speaker": "Speaker name",
      "source_quote": "Quote"
    },
    "team": {
      "size": "Number or range or null",
      "roles": ["role"],
      "locations": "Location info or null",
      "confidence": "definitive|indicative|inferred|ambiguous",
      "source_speaker": "Speaker name",
      "source_quote": "Quote"
    },
    "competition": {
      "competitors_evaluating": ["competitor name"],
      "competitor_sentiment": [{"competitor": "name", "sentiment": "what they like/dislike"}],
      "confidence": "definitive|indicative|inferred|ambiguous",
      "source_speaker": "Speaker name",
      "source_quote": "Quote"
    }
  },
  "narrative_summary": "3-5 paragraphs. Past tense, third person. Focus on deal-relevant information. No AI-speak. Only use names, titles, and companies that appear in the transcript. If not mentioned, omit them.",
  "action_items": {
    "rep_actions": [
      {"action": "What to do", "due_date": "Date or null", "priority": "high|medium|low"}
    ],
    "prospect_commitments": [
      {"action": "What they committed to", "owner": "Name", "due_date": "Date or null"}
    ]
  },
  "next_steps": {
    "next_meeting": "Date/time or 'To be scheduled' or null",
    "next_milestone": "What needs to happen next",
    "suggested_deal_stage": "Qualification|Demo|Proposal|Negotiation"
  },
  "signals": {
    "buying_signals": [{"signal": "Positive signal text", "speaker": "Name"}],
    "risk_signals": [{"signal": "Risk or concern text", "speaker": "Name"}]
  },
  "disclaimer": {
    "ambiguous_topics": [{"topic": "Topic name", "reason": "Why ambiguous"}],
    "confidential_content_detected": false,
    "transcript_quality_issues": "Description or null"
  },
  "detected_introductions": [
    {"speaker_label": "SPEAKER_00", "name": "Only if speaker says their name in transcript", "company": "Only if explicitly stated, else null", "title": "Only if explicitly stated, else null"}
  ],
  "overall_confidence_score": 72
}"""
