"""
Digest generator — aggregates call intelligence for a period and produces a narrative summary.

The narrative is generated via LLM (Groq) when GROQ_API_KEY is set.
Falls back to a template-based narrative when no LLM provider is available.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import String, cast, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_intelligence import (
    CallCompetitor,
    CallFeatureRequest,
    CallIntelligence,
    CallObjection,
    CallPainPoint,
    IntelligenceDigest,
)

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("DIGEST_MODEL", "llama-3.3-70b-versatile")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


async def generate_digest(
    db: AsyncSession,
    team_id,
    since: datetime | None = None,
) -> IntelligenceDigest | None:
    """Generate an intelligence digest for the team.

    Args:
        db: Database session
        team_id: Team UUID
        since: Start of period (defaults to 7 days ago)

    Returns:
        IntelligenceDigest row (already added to session), or None if no calls.
    """
    now = datetime.now(timezone.utc)
    period_start = since or (now - timedelta(days=7))

    # Count external calls in period
    count_result = await db.execute(
        select(func.count(CallIntelligence.id)).where(
            CallIntelligence.team_id == team_id,
            CallIntelligence.created_at >= period_start,
            CallIntelligence.call_type != "internal",
        )
    )
    total_calls = count_result.scalar() or 0

    if total_calls == 0:
        return None

    # Compute averages
    avg_result = await db.execute(
        select(
            func.avg(CallIntelligence.pull_score).label("avg_pull"),
            func.avg(CallIntelligence.icp_fit_score).label("avg_icp"),
            func.avg(CallIntelligence.pain_fit_score).label("avg_pain_fit"),
        ).where(
            CallIntelligence.team_id == team_id,
            CallIntelligence.created_at >= period_start,
            CallIntelligence.call_type != "internal",
        )
    )
    avgs = avg_result.one()

    # Top pain points
    top_pains = await db.execute(
        select(
            CallPainPoint.pain,
            func.count(CallPainPoint.id).label("cnt"),
        )
        .where(
            CallPainPoint.team_id == team_id,
            CallPainPoint.call_date >= period_start.date(),
        )
        .group_by(CallPainPoint.pain)
        .order_by(desc("cnt"))
        .limit(5)
    )
    pain_list = [(row.pain, row.cnt) for row in top_pains.all()]

    # Top objections
    top_objections = await db.execute(
        select(
            CallObjection.objection,
            func.count(CallObjection.id).label("cnt"),
        )
        .where(
            CallObjection.team_id == team_id,
            CallObjection.call_date >= period_start.date(),
        )
        .group_by(CallObjection.objection)
        .order_by(desc("cnt"))
        .limit(5)
    )
    objection_list = [(row.objection, row.cnt) for row in top_objections.all()]

    # Top competitors
    top_competitors = await db.execute(
        select(
            CallCompetitor.competitor,
            func.count(CallCompetitor.id).label("cnt"),
        )
        .where(
            CallCompetitor.team_id == team_id,
            CallCompetitor.call_date >= period_start.date(),
        )
        .group_by(CallCompetitor.competitor)
        .order_by(desc("cnt"))
        .limit(5)
    )
    competitor_list = [(row.competitor, row.cnt) for row in top_competitors.all()]

    # Top feature requests
    top_features = await db.execute(
        select(
            CallFeatureRequest.feature,
            func.count(CallFeatureRequest.id).label("cnt"),
        )
        .where(
            CallFeatureRequest.team_id == team_id,
            CallFeatureRequest.call_date >= period_start.date(),
        )
        .group_by(CallFeatureRequest.feature)
        .order_by(desc("cnt"))
        .limit(5)
    )
    feature_list = [(row.feature, row.cnt) for row in top_features.all()]

    # Gather additional LLM context data
    buying_signals = []
    hesitation_signals = []
    unexpected_insights = []
    individual_scores = []

    try:
        # Per-call PMF scores + buying/hesitation signals from JSONB columns
        intel_rows = await db.execute(
            select(
                CallIntelligence.pull_score,
                CallIntelligence.icp_fit_score,
                CallIntelligence.pain_fit_score,
                CallIntelligence.buying_signals,
                CallIntelligence.pmf_signals,
                CallIntelligence.summary,
                cast(CallIntelligence.meeting_id, String).label("meeting_id"),
            ).where(
                CallIntelligence.team_id == team_id,
                CallIntelligence.created_at >= period_start,
                CallIntelligence.call_type != "internal",
            )
        )
        for row in intel_rows.all():
            summary_data = row.summary or {}
            pmf_data = row.pmf_signals or {}

            individual_scores.append({
                "pull_score": row.pull_score,
                "icp_fit": row.icp_fit_score,
                "pain_fit": row.pain_fit_score,
                "company": summary_data.get("prospect_company", "Unknown"),
                "one_liner": summary_data.get("one_line_summary") or (summary_data.get("summary", {}).get("tldr", "")[:200] if isinstance(summary_data.get("summary"), dict) else ""),
            })

            # Buying signals from JSONB column
            bs_list = row.buying_signals if isinstance(row.buying_signals, list) else []
            for bs in bs_list:
                signal_text = (bs.get("signal") or bs.get("text") or str(bs)) if isinstance(bs, dict) else str(bs)
                if signal_text:
                    buying_signals.append(signal_text)

            # Hesitation signals from pmf_signals JSONB
            for hs in pmf_data.get("hesitation_signals", []) if isinstance(pmf_data, dict) else []:
                signal_text = (hs.get("signal") or hs.get("text") or str(hs)) if isinstance(hs, dict) else str(hs)
                if signal_text:
                    hesitation_signals.append(signal_text)

            # Unexpected insights from pmf_signals JSONB
            for ui in pmf_data.get("unexpected_insights", []) if isinstance(pmf_data, dict) else []:
                insight_text = (ui.get("insight") or ui.get("text") or str(ui)) if isinstance(ui, dict) else str(ui)
                if insight_text:
                    unexpected_insights.append(insight_text)

    except Exception:
        logger.exception("Failed to gather LLM context data for digest")

    # Build structured metrics
    structured_metrics = {
        "total_calls": total_calls,
        "avg_pull_score": round(float(avgs.avg_pull), 1) if avgs.avg_pull else None,
        "avg_icp_fit": round(float(avgs.avg_icp), 1) if avgs.avg_icp else None,
        "avg_pain_fit": round(float(avgs.avg_pain_fit), 1) if avgs.avg_pain_fit else None,
        "top_pain_points": [{"pain": p, "count": c} for p, c in pain_list],
        "top_objections": [{"objection": o, "count": c} for o, c in objection_list],
        "top_competitors": [{"competitor": c, "count": n} for c, n in competitor_list],
        "top_feature_requests": [{"feature": f, "count": c} for f, c in feature_list],
    }

    # Generate narrative — LLM or fallback
    narrative = await _generate_narrative_llm(
        metrics=structured_metrics,
        period_start=period_start,
        period_end=now,
        buying_signals=buying_signals[:20],
        hesitation_signals=hesitation_signals[:20],
        unexpected_insights=unexpected_insights[:10],
        individual_scores=individual_scores[:20],
    )

    # Build metrics dict
    metrics: dict[str, Any] = {**structured_metrics}

    # FAQ grouping (piggybacks on digest generation)
    try:
        from app.services.faq_grouper import group_similar_questions
        faq_groups = await group_similar_questions(db, team_id, since=period_start)
        if faq_groups:
            metrics["faq_groups"] = faq_groups
    except Exception:
        logger.exception("FAQ grouping failed during digest generation")

    digest = IntelligenceDigest(
        team_id=team_id,
        period_start=period_start.date(),
        period_end=now.date(),
        narrative=narrative,
        metrics=metrics,
        total_calls=total_calls,
    )
    db.add(digest)
    return digest


def _format_list(items: list[dict], key: str, count_key: str = "count") -> str:
    """Format a list of items for the prompt."""
    if not items:
        return "  (none)"
    return "\n".join(f"  - {item[key]} ({item[count_key]}x)" for item in items)


def _format_signals(signals: list[str]) -> str:
    """Format signal strings for the prompt."""
    if not signals:
        return "  (none detected)"
    unique = list(dict.fromkeys(signals))[:10]
    return "\n".join(f"  - {s}" for s in unique)


def _format_individual_scores(scores: list[dict]) -> str:
    """Format per-call scores for the prompt."""
    if not scores:
        return "  (none)"
    lines = []
    for s in scores:
        company = s.get("company", "Unknown")
        pull = s.get("pull_score")
        one_liner = s.get("one_liner", "")
        pull_str = f"{pull:.1f}/10" if pull else "N/A"
        lines.append(f"  - {company}: Pull={pull_str} — {one_liner}")
    return "\n".join(lines)


async def _generate_narrative_llm(
    metrics: dict,
    period_start: datetime,
    period_end: datetime,
    buying_signals: list[str],
    hesitation_signals: list[str],
    unexpected_insights: list[str],
    individual_scores: list[dict],
) -> str:
    """Generate rich PMF narrative using LLM (Groq). Falls back to template on failure."""

    if not GROQ_API_KEY:
        logger.info("GROQ_API_KEY not set — using template-based digest narrative")
        return _build_narrative_template(
            total_calls=metrics["total_calls"],
            period_start=period_start,
            period_end=period_end,
            avg_pull=metrics.get("avg_pull_score"),
            avg_icp=metrics.get("avg_icp_fit"),
            avg_pain_fit=metrics.get("avg_pain_fit"),
            pain_list=[(p["pain"], p["count"]) for p in metrics.get("top_pain_points", [])],
            objection_list=[(o["objection"], o["count"]) for o in metrics.get("top_objections", [])],
            competitor_list=[(c["competitor"], c["count"]) for c in metrics.get("top_competitors", [])],
            feature_list=[(f["feature"], f["count"]) for f in metrics.get("top_feature_requests", [])],
        )

    start_str = period_start.strftime("%b %d")
    end_str = period_end.strftime("%b %d, %Y")

    prompt = f"""You are a product-market fit advisor to a B2B SaaS founder.
Analyse this week's sales call data ({start_str} — {end_str}) and write a candid, specific PMF assessment.

WEEK'S DATA:
- Total calls: {metrics['total_calls']}
- Average pull score: {metrics.get('avg_pull_score') or 'N/A'}
- Average genuine pain fit: {metrics.get('avg_pain_fit') or 'N/A'}
- Average ICP fit: {metrics.get('avg_icp_fit') or 'N/A'}

TOP PAIN POINTS THIS WEEK:
{_format_list(metrics.get('top_pain_points', []), 'pain')}

TOP OBJECTIONS:
{_format_list(metrics.get('top_objections', []), 'objection')}

TOP COMPETITORS:
{_format_list(metrics.get('top_competitors', []), 'competitor')}

TOP FEATURE REQUESTS:
{_format_list(metrics.get('top_feature_requests', []), 'feature')}

BUYING SIGNALS DETECTED:
{_format_signals(buying_signals)}

HESITATION SIGNALS:
{_format_signals(hesitation_signals)}

UNEXPECTED INSIGHTS FROM THIS WEEK:
{_format_signals(unexpected_insights)}

PMF SCORES FROM INDIVIDUAL CALLS:
{_format_individual_scores(individual_scores)}

Write a 3-5 paragraph PMF assessment covering:
1. Overall signal this week — are you moving toward or away from PMF?
2. The strongest evidence of fit you're seeing
3. The most important concern or gap to address
4. One specific recommendation for the founder this week
5. What to watch for in next week's calls

Be specific. Name actual companies, quotes, and patterns where relevant.
Tone: Direct, honest, like a trusted advisor. Conversational but data-backed.
Return as plain text (not JSON, not markdown). Max 400 words."""

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROQ_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 600,
                    "temperature": 0.7,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            narrative = data["choices"][0]["message"]["content"]
            logger.info("Digest narrative generated via LLM (%s)", GROQ_MODEL)
            return narrative
    except Exception:
        logger.exception("LLM digest generation failed — falling back to template")
        return _build_narrative_template(
            total_calls=metrics["total_calls"],
            period_start=period_start,
            period_end=period_end,
            avg_pull=metrics.get("avg_pull_score"),
            avg_icp=metrics.get("avg_icp_fit"),
            avg_pain_fit=metrics.get("avg_pain_fit"),
            pain_list=[(p["pain"], p["count"]) for p in metrics.get("top_pain_points", [])],
            objection_list=[(o["objection"], o["count"]) for o in metrics.get("top_objections", [])],
            competitor_list=[(c["competitor"], c["count"]) for c in metrics.get("top_competitors", [])],
            feature_list=[(f["feature"], f["count"]) for f in metrics.get("top_feature_requests", [])],
        )


def _build_narrative_template(
    total_calls: int,
    period_start: datetime,
    period_end: datetime,
    avg_pull: float | None,
    avg_icp: float | None,
    avg_pain_fit: float | None,
    pain_list: list[tuple[str, int]],
    objection_list: list[tuple[str, int]],
    competitor_list: list[tuple[str, int]],
    feature_list: list[tuple[str, int]],
) -> str:
    """Build a human-readable digest narrative from structured data (fallback)."""
    start_str = period_start.strftime("%b %d")
    end_str = period_end.strftime("%b %d, %Y")
    parts = [f"Intelligence Digest: {start_str} — {end_str}\n"]
    parts.append(f"Across {total_calls} external call{'s' if total_calls != 1 else ''} this period:\n")

    # PMF signals
    if any(v is not None for v in [avg_pull, avg_icp, avg_pain_fit]):
        parts.append("PMF Signals:")
        if avg_pull is not None:
            parts.append(f"  - Average pull score: {avg_pull:.1f}/10")
        if avg_icp is not None:
            parts.append(f"  - Average ICP fit: {avg_icp:.1f}/10")
        if avg_pain_fit is not None:
            parts.append(f"  - Average pain fit: {avg_pain_fit:.1f}/10")
        parts.append("")

    # Pain points
    if pain_list:
        parts.append("Top Pain Points:")
        for pain, count in pain_list:
            parts.append(f"  - {pain} ({count} mention{'s' if count != 1 else ''})")
        parts.append("")

    # Objections
    if objection_list:
        parts.append("Common Objections:")
        for obj, count in objection_list:
            parts.append(f"  - {obj} ({count}x)")
        parts.append("")

    # Competitors
    if competitor_list:
        parts.append("Competitor Mentions:")
        for comp, count in competitor_list:
            parts.append(f"  - {comp} ({count} mention{'s' if count != 1 else ''})")
        parts.append("")

    # Feature requests
    if feature_list:
        parts.append("Feature Requests:")
        for feat, count in feature_list:
            parts.append(f"  - {feat} ({count} request{'s' if count != 1 else ''})")
        parts.append("")

    return "\n".join(parts)
