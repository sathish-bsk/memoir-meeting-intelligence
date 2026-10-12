"""
Store intelligence.json data into SQL tables for aggregation queries.

Called from the transcription_complete webhook handler after reading
the intelligence.json file that TranscriptionBase generated.
"""

import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_intelligence import (
    CallCompetitor,
    CallFeatureRequest,
    CallIntelligence,
    CallObjection,
    CallPainPoint,
    CallQuestion,
)
from app.models.meeting import Meeting

logger = logging.getLogger(__name__)



def _to_float(val: Any) -> float | None:
    """Convert value to float, handling strings like 'low'/'medium'/'high'."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        mapping = {"low": 0.3, "medium": 0.6, "high": 0.9}
        return mapping.get(str(val).lower())


async def store_intelligence_data(
    db: AsyncSession,
    meeting: Meeting,
    data: dict[str, Any],
) -> CallIntelligence:
    """Parse intelligence.json and store in call_intelligence + flattened tables."""

    # Extract nested sections
    intel = data.get("intelligence", {}) or {}
    crm = data.get("crm", {}) or {}
    pmf = data.get("pmf", {}) or {}
    summary = data.get("summary", {}) or {}
    faq = data.get("faq", {}) or {}
    usage = data.get("usage", {}) or {}

    # Buy signal (P3 enhancement)
    buy_signal = intel.get("buy_signal", {}) or {}

    # Get created_by from meeting for role-based filtering
    created_by = getattr(meeting, "created_by", None)

    # Compute next version: max(version) over this meeting's rows + 1.
    # First run for a meeting → version=1. Retranscribe v2 → version=2, etc.
    from sqlalchemy import func as _func, select as _select
    max_v_result = await db.execute(
        _select(_func.coalesce(_func.max(CallIntelligence.version), 0))
        .where(CallIntelligence.meeting_id == meeting.id)
    )
    next_version = (max_v_result.scalar() or 0) + 1

    call_intel = CallIntelligence(
        meeting_id=meeting.id,
        version=next_version,
        team_id=meeting.team_id,
        created_by=created_by,
        call_type=data.get("call_type", "uncertain"),
        classification_confidence=data.get("classification_confidence"),

        # Prompt 2: CRM extraction
        crm_extraction=crm if crm else None,

        # Prompt 3: Intelligence extraction
        pain_points=intel.get("pain_points"),
        desired_outcomes=intel.get("desired_outcomes"),
        feature_requests=intel.get("feature_requests"),
        competitors_mentioned=intel.get("competitors_mentioned"),
        objections=intel.get("objections"),
        buying_signals=intel.get("buying_signals"),
        red_flags=intel.get("red_flags"),
        tech_stack=intel.get("tech_stack"),
        budget_signals=intel.get("budget_signals"),
        decision_process=intel.get("decision_process"),
        product_perception=intel.get("product_perception"),

        # Prompt 4: PMF signals
        # LLM returns nested dicts (e.g. pull_score: {score: 4, evidence: "..."})
        # but columns store scalar values — extract accordingly.
        pmf_signals=pmf if pmf else None,
        pull_score=_extract_score(pmf.get("pull_score")),
        icp_fit_score=_extract_score(pmf.get("icp_fit"), key="icp_score") or _extract_score(pmf.get("icp_fit"), key="score") or _safe_int(pmf.get("icp_fit_score")),
        pain_fit_score=_extract_score(pmf.get("genuine_pain_fit")) or _safe_int(pmf.get("pain_fit_score")),
        overall_pmf_signal=_extract_string(pmf.get("overall_pmf_signal"), key="signal"),

        # Prompt 5: Summary
        summary=summary if summary else None,
        call_health=summary.get("call_health") if isinstance(summary, dict) else None,

        # Prompt 6: FAQ
        questions=faq.get("questions") if faq else None,

        # Deal intelligence fields (from pipeline)
        deal_momentum=data.get("deal_momentum"),
        validated_stage=data.get("validated_stage"),

        # Buy signal (P3 enhancement)
        has_committed_next_step=buy_signal.get("has_committed_next_step", False) if isinstance(buy_signal, dict) else False,
        next_step_type=buy_signal.get("next_step_type") if isinstance(buy_signal, dict) else None,

        # Processing metadata
        processing_status="completed",
        processing_completed_at=datetime.now(timezone.utc),
        total_tokens_used=usage.get("total_tokens"),
        total_cost_usd=usage.get("cost_usd"),
    )
    db.add(call_intel)
    await db.flush()  # Ensure call_intel.id is available for child FK references

    # Determine call date for flattened rows
    call_date = _get_call_date(meeting)

    # Extract prospect company from classification or CRM
    prospect_company = (data.get("prospect_company") or (crm.get("contact", {}).get("company") if isinstance(crm.get("contact"), dict) else None)) or "Unknown"

    # Flatten pain points
    for pp in (intel.get("pain_points") or []):
        if not isinstance(pp, dict):
            continue
        pain_text = pp.get("pain") or pp.get("description") or ""
        if not pain_text:
            continue
        db.add(CallPainPoint(
            call_intelligence_id=call_intel.id,
            meeting_id=meeting.id,
            team_id=meeting.team_id,
            created_by=created_by,
            pain=pain_text,
            severity=pp.get("severity"),
            exact_quote=pp.get("exact_quote"),
            timestamp_in_call=pp.get("timestamp"),
            speaker_name=pp.get("speaker_name"),
            explicitly_stated=pp.get("explicitly_stated", True),
            call_date=call_date,
            prospect_company=prospect_company,
        ))

    # Flatten feature requests
    for fr in (intel.get("feature_requests") or []):
        if not isinstance(fr, dict):
            continue
        feature_text = fr.get("feature") or fr.get("description") or ""
        if not feature_text:
            continue
        db.add(CallFeatureRequest(
            call_intelligence_id=call_intel.id,
            meeting_id=meeting.id,
            team_id=meeting.team_id,
            created_by=created_by,
            feature=feature_text,
            use_case=fr.get("use_case"),
            urgency=fr.get("urgency"),
            exact_quote=fr.get("exact_quote"),
            timestamp_in_call=fr.get("timestamp"),
            speaker_name=fr.get("speaker_name"),
            call_date=call_date,
            prospect_company=prospect_company,
        ))

    # Flatten competitor mentions
    for comp in (intel.get("competitors_mentioned") or []):
        if not isinstance(comp, dict):
            continue
        competitor_name = comp.get("competitor") or comp.get("name") or ""
        if not competitor_name:
            continue
        db.add(CallCompetitor(
            call_intelligence_id=call_intel.id,
            meeting_id=meeting.id,
            team_id=meeting.team_id,
            created_by=created_by,
            competitor=competitor_name,
            context=comp.get("context"),
            sentiment=comp.get("sentiment"),
            what_they_like=comp.get("what_they_like"),
            what_they_dislike=comp.get("what_they_dislike"),
            exact_quote=comp.get("exact_quote"),
            timestamp_in_call=comp.get("timestamp"),
            speaker_name=comp.get("speaker_name"),
            call_date=call_date,
            prospect_company=prospect_company,
        ))

    # Flatten objections
    for obj in (intel.get("objections") or []):
        if not isinstance(obj, dict):
            continue
        objection_text = obj.get("objection") or obj.get("description") or ""
        if not objection_text:
            continue
        db.add(CallObjection(
            call_intelligence_id=call_intel.id,
            meeting_id=meeting.id,
            team_id=meeting.team_id,
            created_by=created_by,
            objection=objection_text,
            category=obj.get("category"),
            how_handled=obj.get("how_handled"),
            exact_quote=obj.get("exact_quote"),
            timestamp_in_call=obj.get("timestamp"),
            speaker_name=obj.get("speaker_name"),
            call_date=call_date,
            prospect_company=prospect_company,
        ))

    # Flatten FAQ questions
    questions = faq.get("questions") or []
    for q in questions:
        if not isinstance(q, dict):
            continue
        text = (q.get("question") or "").strip()
        if not text:
            continue
        db.add(CallQuestion(
            call_intelligence_id=call_intel.id,
            meeting_id=meeting.id,
            team_id=meeting.team_id,
            created_by=created_by,
            question=text,
            exact_quote=q.get("exact_quote"),
            timestamp_in_call=q.get("timestamp"),
            speaker_name=q.get("speaker_name"),
            category=q.get("category"),
            was_answered=q.get("was_answered"),
            answer_quality=q.get("answer_quality"),
            call_date=call_date,
            prospect_company=prospect_company,
        ))

    return call_intel


async def store_deal_data(
    db: AsyncSession,
    meeting: Meeting,
    data: dict[str, Any],
) -> None:
    """Process deal matching data from intelligence.json and update memoir_deals + meetings."""
    from app.models.memoir_deal import MemoirDeal
    from app.models.deal_match_log import DealMatchLog

    deal_match = data.get("deal_match")
    if not deal_match:
        return

    match_method = deal_match.get("match_method", "none")
    matched_deal_id = deal_match.get("matched_deal_id")

    if match_method != "none" and matched_deal_id:
        # Update existing deal
        from sqlalchemy import select
        result = await db.execute(
            select(MemoirDeal).where(MemoirDeal.id == matched_deal_id)
        )
        deal = result.scalar_one_or_none()
        if deal:
            deal.total_calls += 1
            deal.last_call_date = meeting.created_at
            # Update latest sentiment/health from intelligence
            if data.get("summary", {}).get("call_health"):
                deal.latest_call_health = data["summary"]["call_health"]
            if data.get("validated_stage"):
                deal.current_stage = data["validated_stage"]
            deal.updated_at = datetime.now(timezone.utc)

            meeting.memoir_deal_id = deal.id
            meeting.call_number_in_deal = deal.total_calls
    elif deal_match.get("created_new_deal") and deal_match.get("new_deal_data"):
        # Create new deal
        new_data = deal_match["new_deal_data"]
        deal = MemoirDeal(
            team_id=meeting.team_id,
            prospect_company=new_data.get("prospect_company") or "Unknown",
            prospect_name=new_data.get("prospect_name"),
            prospect_email=new_data.get("prospect_email"),
            prospect_domain=new_data.get("prospect_domain"),
            total_calls=1,
            first_call_date=meeting.created_at,
            last_call_date=meeting.created_at,
            latest_call_health=data.get("summary", {}).get("call_health") if isinstance(data.get("summary"), dict) else None,
            current_stage=data.get("validated_stage"),
        )
        db.add(deal)
        await db.flush()

        meeting.memoir_deal_id = deal.id
        meeting.call_number_in_deal = 1
        matched_deal_id = deal.id

    # Write deal match log
    if matched_deal_id:
        db.add(DealMatchLog(
            meeting_id=meeting.id,
            memoir_deal_id=matched_deal_id,
            match_method=match_method if match_method != "none" else "new",
            match_confidence=_to_float(deal_match.get("match_confidence")),
            matched_on=deal_match.get("matched_on") or deal_match.get("extracted_company"),
        ))


async def store_prompt_logs(
    db: AsyncSession,
    meeting: Meeting,
    data: dict[str, Any],
) -> None:
    """Backward-compatible wrapper — delegates to store_all_costs()."""
    await store_all_costs(db, meeting, data)


async def store_all_costs(
    db: AsyncSession,
    meeting: Meeting,
    data: dict[str, Any],
    operation_type: str | None = None,
    recording_source: str | None = None,
    triggered_by=None,
    pipeline_cost_data: dict[str, Any] | None = None,
) -> None:
    """Store ALL cost-incurring pipeline steps as ai_prompt_log rows.

    Covers:
    - LLM prompts from intelligence.json prompt_log[] (P1-P6, digest)
    - Infrastructure steps from pipeline_cost.json (asr, diarization, llm_corrector)

    Args:
        data: intelligence.json content (for prompt_log)
        operation_type: "transcription" / "retranscribe" / "crm_extraction" / "intelligence_rerun"
        recording_source: "bot" / "zoom_cloud" / "extension" / "upload" / "teams_cloud"
        triggered_by: UUID of user who triggered (None for auto/webhook)
        pipeline_cost_data: parsed pipeline_cost.json (for infra step rows)
    """
    from app.models.ai_prompt_log import AiPromptLog
    from app.services.cost_calculator import estimate_gpu_cost

    # Determine operation_type from data if not provided
    if not operation_type:
        # Check if retranscribe (version > 1) vs first transcription
        version = data.get("version", 1)
        if isinstance(version, int) and version > 1:
            operation_type = "retranscribe"
        else:
            operation_type = "transcription"

    # Determine recording_source from data if not provided
    if not recording_source:
        recording_source = data.get("recording_source") or pipeline_cost_data and pipeline_cost_data.get("recording_source")

    # Store LLM prompt rows from intelligence.json prompt_log[]
    prompt_log = data.get("prompt_log", [])
    for entry in (prompt_log or []):
        if not isinstance(entry, dict):
            continue
        db.add(AiPromptLog(
            meeting_id=meeting.id,
            team_id=meeting.team_id,
            prompt_name=entry.get("prompt_name", "unknown"),
            provider=entry.get("provider"),
            model=entry.get("model"),
            input_tokens=entry.get("input_tokens"),
            output_tokens=entry.get("output_tokens"),
            cache_read_tokens=entry.get("cache_read_tokens", 0),
            # Tier 4.0.a — read the new field; defaults to 0 when absent
            # (Ollama/non-Claude providers don't populate it).
            cache_creation_tokens=entry.get("cache_creation_tokens", 0),
            cost_usd=entry.get("cost_usd"),
            duration_ms=entry.get("duration_ms"),
            operation_type=operation_type,
            recording_source=recording_source,
            triggered_by=triggered_by,
        ))

    # Store infrastructure rows from pipeline_cost.json
    if pipeline_cost_data:
        _store_infra_costs(db, meeting, pipeline_cost_data, operation_type, recording_source, triggered_by)


def _store_infra_costs(
    db,
    meeting: Meeting,
    cost_data: dict[str, Any],
    operation_type: str | None,
    recording_source: str | None,
    triggered_by,
) -> None:
    """Store ASR, diarization, and LLM corrector rows from pipeline_cost.json."""
    from app.models.ai_prompt_log import AiPromptLog
    from app.services.cost_calculator import estimate_gpu_cost

    # ASR row
    asr_seconds = cost_data.get("asr_processing_seconds")
    if asr_seconds is not None:
        gpu_type = cost_data.get("asr_gpu_type")
        db.add(AiPromptLog(
            meeting_id=meeting.id,
            team_id=meeting.team_id,
            prompt_name="asr",
            provider=cost_data.get("asr_engine", "faster-whisper"),
            model=cost_data.get("asr_model_size"),
            cost_usd=estimate_gpu_cost(asr_seconds, gpu_type),
            operation_type=operation_type,
            gpu_type=gpu_type,
            audio_duration_seconds=cost_data.get("audio_duration_seconds"),
            processing_seconds=asr_seconds,
            recording_source=recording_source,
            triggered_by=triggered_by,
        ))

    # Diarization row
    diar_seconds = cost_data.get("diarization_processing_seconds")
    if diar_seconds is not None:
        gpu_type = cost_data.get("diarization_gpu_type")
        db.add(AiPromptLog(
            meeting_id=meeting.id,
            team_id=meeting.team_id,
            prompt_name="diarization",
            provider="pyannote",
            model="3.1",
            cost_usd=estimate_gpu_cost(diar_seconds, gpu_type),
            operation_type=operation_type,
            gpu_type=gpu_type,
            processing_seconds=diar_seconds,
            recording_source=recording_source,
            triggered_by=triggered_by,
        ))

    # LLM corrector row
    corrector_seconds = cost_data.get("llm_corrector_processing_seconds")
    if corrector_seconds is not None:
        db.add(AiPromptLog(
            meeting_id=meeting.id,
            team_id=meeting.team_id,
            prompt_name="llm_corrector",
            provider=cost_data.get("llm_corrector_provider", "ollama"),
            model=cost_data.get("llm_corrector_model"),
            cost_usd=0.0,  # Ollama is local, no cost
            operation_type=operation_type,
            processing_seconds=corrector_seconds,
            recording_source=recording_source,
            triggered_by=triggered_by,
        ))


def _safe_int(value) -> int | None:
    """Safely convert a value to int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _extract_score(value, key: str = "score") -> int | None:
    """Extract an int score from a value that may be int or {score: int, ...}."""
    if value is None:
        return None
    if isinstance(value, dict):
        return _safe_int(value.get(key))
    return _safe_int(value)


def _extract_string(value, key: str = "signal") -> str | None:
    """Extract a string from a value that may be str or {signal: str, ...}."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(key)
    return str(value) if value else None


def _get_call_date(meeting: Meeting) -> date | None:
    """Get the call date from the meeting for flattened row indexing."""
    if meeting.started_at:
        return meeting.started_at.date()
    if meeting.created_at:
        return meeting.created_at.date() if isinstance(meeting.created_at, datetime) else meeting.created_at
    return date.today()
