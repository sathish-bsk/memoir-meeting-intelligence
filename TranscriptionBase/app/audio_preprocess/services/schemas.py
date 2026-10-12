"""
Phase 7: Pydantic schemas for CallIntelligence JSON output.

Self-contained module — zero imports from pipeline/transcribe/audio.
Designed for future extraction into post-processing microservice (Phase 9).

All fields are Optional/nullable — if information was not discussed in the call,
the value should be None, not hallucinated.
"""

import json
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# --- Enums ---

class SpeakerRole(str, Enum):
    rep = "rep"
    customer = "customer"
    unknown = "unknown"


class Sentiment(str, Enum):
    positive = "positive"
    negative = "negative"
    neutral = "neutral"


class OverallSentimentLabel(str, Enum):
    positive = "positive"
    negative = "negative"
    neutral = "neutral"
    mixed = "mixed"


class SentimentTrajectory(str, Enum):
    improving = "improving"
    declining = "declining"
    stable_positive = "stable_positive"
    stable_negative = "stable_negative"
    stable_neutral = "stable_neutral"
    volatile = "volatile"


class NextStepOwner(str, Enum):
    rep = "rep"
    customer = "customer"
    both = "both"


class DealStageLabel(str, Enum):
    discovery = "discovery"
    qualification = "qualification"
    demo = "demo"
    evaluation = "evaluation"
    negotiation = "negotiation"
    closing = "closing"
    unknown = "unknown"


class TalkRatioAssessment(str, Enum):
    optimal = "optimal"
    rep_dominant = "rep_dominant"
    customer_dominant = "customer_dominant"


# QuestionType, ObjectionType, NextStepType are free-text strings (not enums)
# because the 7B model picks contextually appropriate labels that may not
# match a predefined list. The schema prompt provides examples as guidance.


class DealHealthLabel(str, Enum):
    healthy = "healthy"
    at_risk = "at_risk"
    critical = "critical"


class CoachingGrade(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


# --- Sub-models ---

class Participant(BaseModel):
    speaker_label: str = Field(description="SPEAKER_00, SPEAKER_01, etc.")
    role: SpeakerRole = SpeakerRole.unknown
    name: Optional[str] = None


class CallMetadata(BaseModel):
    call_id: Optional[str] = None
    call_date: Optional[str] = None
    duration_seconds: Optional[int] = None
    participants: list[Participant] = Field(default_factory=list)
    language: Optional[str] = None
    extraction_model: Optional[str] = None
    extraction_timestamp: Optional[str] = None


class CallSummary(BaseModel):
    one_line: Optional[str] = Field(None, description="One-sentence summary")
    detailed: Optional[str] = Field(None, description="2-3 paragraph summary")
    key_topics: list[str] = Field(default_factory=list)


class CompetitorMention(BaseModel):
    name: str
    sentiment: Optional[Sentiment] = None
    context: Optional[str] = None
    quote: Optional[str] = None
    timestamp: Optional[float] = None


class PainPoint(BaseModel):
    description: str
    business_impact: Optional[str] = None
    quote: Optional[str] = None


class Objection(BaseModel):
    type: str = "other"
    description: str
    handled: bool = False
    rep_response: Optional[str] = None
    quote: Optional[str] = None


class NextStep(BaseModel):
    description: str
    owner: NextStepOwner = NextStepOwner.both
    due_date: Optional[str] = None
    type: str = "other"


class Budget(BaseModel):
    discussed: bool = False
    amount: Optional[float] = None
    currency: Optional[str] = None
    pricing_presented: bool = False
    discount_requested: bool = False
    roi_discussed: bool = False
    quote: Optional[str] = None


class DecisionMaking(BaseModel):
    decision_maker: Optional[str] = None
    buying_committee: list[str] = Field(default_factory=list)
    decision_process: Optional[str] = None
    decision_timeline: Optional[str] = None
    champion_identified: bool = False
    champion_name: Optional[str] = None


class DealStage(BaseModel):
    current_stage: DealStageLabel = DealStageLabel.unknown
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    stage_signals: list[str] = Field(default_factory=list)
    risk_signals: list[str] = Field(default_factory=list)


class Features(BaseModel):
    features_discussed: list[str] = Field(default_factory=list)
    feature_requests: list[str] = Field(default_factory=list)
    use_cases: list[str] = Field(default_factory=list)
    technical_requirements: list[str] = Field(default_factory=list)


class SegmentSentiment(BaseModel):
    segment_id: int
    sentiment: Sentiment = Sentiment.neutral
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class SentimentShift(BaseModel):
    timestamp: Optional[float] = None
    from_sentiment: Optional[str] = None
    to_sentiment: Optional[str] = None
    trigger: Optional[str] = None


class OverallSentiment(BaseModel):
    overall: OverallSentimentLabel = OverallSentimentLabel.neutral
    customer_net: Sentiment = Sentiment.neutral
    rep_net: Sentiment = Sentiment.neutral
    trajectory: SentimentTrajectory = SentimentTrajectory.stable_neutral
    notable_shifts: list[SentimentShift] = Field(default_factory=list)


class Question(BaseModel):
    question: str
    asked_by: Optional[SpeakerRole] = None
    question_type: str = "open"
    answered: bool = False
    timestamp: Optional[float] = None


# --- Coaching Metrics (computed from diarization, not LLM) ---

class TalkRatio(BaseModel):
    rep_percentage: float = 0.0
    customer_percentage: float = 0.0
    assessment: TalkRatioAssessment = TalkRatioAssessment.optimal


class QuestionCount(BaseModel):
    rep: int = 0
    customer: int = 0


class FillerWordCount(BaseModel):
    rep: int = 0
    customer: int = 0
    rep_details: dict[str, int] = Field(default_factory=dict)


class CoachingMetrics(BaseModel):
    talk_ratio: TalkRatio = Field(default_factory=TalkRatio)
    longest_monologue_seconds: float = 0.0
    longest_monologue_speaker: Optional[str] = None
    question_count: QuestionCount = Field(default_factory=QuestionCount)
    filler_word_count: FillerWordCount = Field(default_factory=FillerWordCount)
    interruption_count: int = 0
    average_response_time_seconds: float = 0.0
    silence_percentage: float = 0.0


# --- Methodology Scoring (7B) ---

class MEDDICScore(BaseModel):
    total_score: int = Field(0, ge=0, le=6)
    metrics: bool = False
    economic_buyer: bool = False
    decision_criteria: bool = False
    decision_process: bool = False
    identify_pain: bool = False
    champion: bool = False
    details: dict[str, Optional[str]] = Field(default_factory=dict)


class BANTScore(BaseModel):
    total_score: int = Field(0, ge=0, le=4)
    budget: bool = False
    authority: bool = False
    need: bool = False
    timeline: bool = False
    details: dict[str, Optional[str]] = Field(default_factory=dict)


class MethodologyScores(BaseModel):
    meddic: MEDDICScore = Field(default_factory=MEDDICScore)
    bant: BANTScore = Field(default_factory=BANTScore)


# --- Deal Health (7B) ---

class DealHealth(BaseModel):
    score: int = Field(50, ge=0, le=100)
    label: DealHealthLabel = DealHealthLabel.at_risk


# --- Coaching Scorecard (7B) ---

class CoachingScorecard(BaseModel):
    grade: CoachingGrade = CoachingGrade.C
    score: float = 0.0
    components: dict[str, float] = Field(default_factory=dict)


# --- Top-level CallIntelligence ---

class CallIntelligence(BaseModel):
    """Complete intelligence output for a single call."""
    metadata: CallMetadata = Field(default_factory=CallMetadata)
    summary: CallSummary = Field(default_factory=CallSummary)
    competitors: list[CompetitorMention] = Field(default_factory=list)
    pain_points: list[PainPoint] = Field(default_factory=list)
    objections: list[Objection] = Field(default_factory=list)
    next_steps: list[NextStep] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    decision_making: DecisionMaking = Field(default_factory=DecisionMaking)
    deal_stage: DealStage = Field(default_factory=DealStage)
    features: Features = Field(default_factory=Features)
    segment_sentiments: list[SegmentSentiment] = Field(default_factory=list)
    overall_sentiment: OverallSentiment = Field(default_factory=OverallSentiment)
    questions_asked: list[Question] = Field(default_factory=list)

    # Phase 7B — scoring (computed post-extraction, not by LLM)
    methodology_scores: Optional[MethodologyScores] = None
    deal_health: Optional[DealHealth] = None
    coaching_scorecard: Optional[CoachingScorecard] = None

    # Coaching metrics (computed from diarization, not LLM)
    coaching_metrics: Optional[CoachingMetrics] = None


def get_extraction_schema() -> str:
    """
    Return a compact JSON example schema for the LLM extraction prompt.
    Uses an example-based format instead of full JSON Schema to fit within
    the LLM's context window (~2K tokens instead of ~8K).
    """
    return """{
  "summary": {
    "one_line": "Brief one-line summary of the call",
    "detailed": "2-3 sentence detailed summary",
    "key_topics": ["topic1", "topic2"]
  },
  "competitors": [
    {"name": "CompetitorName", "sentiment": "positive|negative|neutral", "context": "How they were mentioned", "quote": "Exact quote from transcript"}
  ],
  "pain_points": [
    {"description": "What the pain point is", "business_impact": "Quantified impact if mentioned", "quote": "Exact quote"}
  ],
  "objections": [
    {"type": "pricing|timeline|feature_gap|competitor|authority|status_quo|other", "description": "What the objection is", "handled": true, "rep_response": "How the rep addressed it", "quote": "Exact quote"}
  ],
  "next_steps": [
    {"description": "What needs to happen next", "owner": "rep|customer|both", "due_date": "Date if mentioned or null", "type": "follow_up_call|demo|proposal|contract|internal_review|other"}
  ],
  "budget": {
    "discussed": true,
    "amount": 50000,
    "currency": "USD",
    "pricing_presented": true,
    "discount_requested": false,
    "roi_discussed": false,
    "quote": "Exact quote about budget"
  },
  "decision_making": {
    "decision_maker": "Name or title",
    "buying_committee": ["Person1", "Person2"],
    "decision_process": "Description of their evaluation process",
    "decision_timeline": "When they plan to decide",
    "champion_identified": false,
    "champion_name": null
  },
  "deal_stage": {
    "current_stage": "discovery|qualification|evaluation|negotiation|closing|unknown",
    "confidence": 0.8,
    "stage_signals": ["Signal that indicates this stage"],
    "risk_signals": ["Any deal risks identified"]
  },
  "features": {
    "features_discussed": ["feature1", "feature2"],
    "feature_requests": ["requested feature"],
    "use_cases": ["use case discussed"],
    "technical_requirements": ["requirement"]
  },
  "segment_sentiments": [
    {"segment_index": 0, "speaker": "SPEAKER_00", "sentiment": "positive|negative|neutral", "reason": "Why this sentiment"}
  ],
  "overall_sentiment": {
    "overall": "positive|negative|neutral|mixed",
    "customer_net": "positive|negative|neutral",
    "rep_net": "positive|negative|neutral",
    "trajectory": "improving|declining|stable_positive|stable_negative|stable_neutral|volatile",
    "notable_shifts": [{"from_sentiment": "neutral", "to_sentiment": "positive", "trigger": "What caused the shift"}]
  },
  "questions_asked": [
    {"question": "The question text", "speaker": "SPEAKER_00", "type": "discovery|technical|pricing|process|objection|other"}
  ]
}"""
