"""
PMF weekly digest generator — computes 5-dimension PMF data from existing tables.

Dimensions:
1. Feature Demand — from call_feature_requests
2. Objection Frequency — from call_objections
3. Competitor Mentions — from call_competitors
4. Buy Signal Strength — from call_intelligence.has_committed_next_step
5. Pain Clarity — from call_pain_points

Run weekly alongside intelligence digest.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text, and_, distinct, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_intelligence import (
    CallIntelligence,
    CallFeatureRequest,
    CallObjection,
    CallCompetitor,
    CallPainPoint,
)
from app.models.pmf_weekly_digest import PmfWeeklyDigest

logger = logging.getLogger(__name__)


# Star thresholds
def ratio_to_stars(ratio: float) -> int:
    if ratio > 0.60: return 5
    if ratio > 0.40: return 4
    if ratio > 0.25: return 3
    if ratio > 0.10: return 2
    return 1


def buy_signal_stars(rate: float) -> int:
    if rate > 0.75: return 5
    if rate > 0.60: return 4
    if rate > 0.45: return 3
    if rate > 0.30: return 2
    return 1


def pain_clarity_stars(rate: float) -> int:
    if rate > 0.70: return 5
    if rate > 0.50: return 4
    if rate > 0.35: return 3
    if rate > 0.20: return 2
    return 1


def _trend_arrow(current: float, previous: float | None) -> str:
    if previous is None:
        return "new"
    if current > previous + 0.05:
        return "up"
    if current < previous - 0.05:
        return "down"
    return "same"


async def generate_pmf_weekly_digest(
    db: AsyncSession,
    team_id,
    week_start: date | None = None,
) -> PmfWeeklyDigest | None:
    """Generate PMF weekly digest for a team. Uses Monday-Sunday weeks."""

    if week_start is None:
        # Default to last complete week (Mon-Sun)
        today = date.today()
        # Last Monday
        last_monday = today - timedelta(days=today.weekday() + 7)
        week_start = last_monday

    week_end = week_start + timedelta(days=7)

    # Previous week for trend comparison
    prev_week_start = week_start - timedelta(days=7)

    # Count total calls in the week
    total_result = await db.execute(
        select(func.count(CallIntelligence.id)).where(
            CallIntelligence.team_id == team_id,
            CallIntelligence.created_at >= datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc),
            CallIntelligence.created_at < datetime.combine(week_end, datetime.min.time()).replace(tzinfo=timezone.utc),
            CallIntelligence.call_type != "internal",
        )
    )
    total_calls = total_result.scalar() or 0

    if total_calls < 3:
        logger.info(f"Team {team_id}: only {total_calls} calls in week {week_start}, skipping PMF digest (minimum 3)")
        return None

    # Dimension 1: Feature Demand
    feature_demand = await _compute_dimension(
        db, team_id, CallFeatureRequest, CallFeatureRequest.feature,
        week_start, week_end, prev_week_start, total_calls
    )

    # Dimension 2: Objection Frequency
    objection_data = await _compute_dimension(
        db, team_id, CallObjection, CallObjection.objection,
        week_start, week_end, prev_week_start, total_calls
    )

    # Dimension 3: Competitor Mentions
    competitor_data = await _compute_dimension(
        db, team_id, CallCompetitor, CallCompetitor.competitor,
        week_start, week_end, prev_week_start, total_calls
    )

    # Dimension 4: Buy Signal Strength
    buy_signal_result = await db.execute(
        select(
            func.count(case((CallIntelligence.has_committed_next_step == True, 1))),
            func.count(CallIntelligence.id),
        ).where(
            CallIntelligence.team_id == team_id,
            CallIntelligence.created_at >= datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc),
            CallIntelligence.created_at < datetime.combine(week_end, datetime.min.time()).replace(tzinfo=timezone.utc),
            CallIntelligence.call_type != "internal",
        )
    )
    buy_row = buy_signal_result.one()
    buy_count = buy_row[0] or 0
    buy_total = buy_row[1] or 1
    buy_rate = buy_count / buy_total

    # Previous week buy signal rate
    prev_buy_result = await db.execute(
        select(
            func.count(case((CallIntelligence.has_committed_next_step == True, 1))),
            func.count(CallIntelligence.id),
        ).where(
            CallIntelligence.team_id == team_id,
            CallIntelligence.created_at >= datetime.combine(prev_week_start, datetime.min.time()).replace(tzinfo=timezone.utc),
            CallIntelligence.created_at < datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc),
            CallIntelligence.call_type != "internal",
        )
    )
    prev_buy_row = prev_buy_result.one()
    prev_buy_rate = (prev_buy_row[0] or 0) / max(prev_buy_row[1] or 1, 1)

    # Dimension 5: Pain Clarity (top pain's convergence)
    pain_result = await db.execute(
        select(
            CallPainPoint.pain,
            func.count(distinct(CallPainPoint.meeting_id)).label("call_count"),
        ).where(
            CallPainPoint.team_id == team_id,
            CallPainPoint.call_date >= week_start,
            CallPainPoint.call_date < week_end,
        ).group_by(CallPainPoint.pain).order_by(func.count(distinct(CallPainPoint.meeting_id)).desc()).limit(1)
    )
    top_pain_row = pain_result.first()
    if top_pain_row:
        pain_rate = top_pain_row[1] / total_calls
        top_pain = top_pain_row[0]
    else:
        pain_rate = 0.0
        top_pain = None

    # Previous week pain clarity
    prev_pain_result = await db.execute(
        select(
            func.count(distinct(CallPainPoint.meeting_id)).label("call_count"),
        ).where(
            CallPainPoint.team_id == team_id,
            CallPainPoint.call_date >= prev_week_start,
            CallPainPoint.call_date < week_start,
        )
    )
    prev_pain_total_mentions = prev_pain_result.scalar() or 0
    # Get previous week total calls for rate calculation
    prev_total_result = await db.execute(
        select(func.count(CallIntelligence.id)).where(
            CallIntelligence.team_id == team_id,
            CallIntelligence.created_at >= datetime.combine(prev_week_start, datetime.min.time()).replace(tzinfo=timezone.utc),
            CallIntelligence.created_at < datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc),
            CallIntelligence.call_type != "internal",
        )
    )
    prev_total_calls = prev_total_result.scalar() or 1
    prev_pain_rate = prev_pain_total_mentions / max(prev_total_calls, 1)

    # Generate narrative via LLM (optional, best-effort)
    narrative = await _generate_narrative(
        feature_demand, objection_data, competitor_data,
        buy_rate, pain_rate, top_pain, total_calls
    )

    # Upsert into pmf_weekly_digest
    existing = await db.execute(
        select(PmfWeeklyDigest).where(
            PmfWeeklyDigest.team_id == team_id,
            PmfWeeklyDigest.week_start == week_start,
        )
    )
    digest = existing.scalar_one_or_none()

    if digest:
        # Update existing
        digest.total_calls = total_calls
        digest.feature_demand = feature_demand
        digest.objections = objection_data
        digest.competitors = competitor_data
        digest.buy_signal_rate = buy_rate
        digest.buy_signal_stars = buy_signal_stars(buy_rate)
        digest.buy_signal_prev_rate = prev_buy_rate
        digest.pain_clarity_rate = pain_rate
        digest.pain_clarity_stars = pain_clarity_stars(pain_rate)
        digest.pain_clarity_top_pain = top_pain
        digest.pain_clarity_prev_rate = prev_pain_rate
        digest.narrative = narrative
        digest.generated_at = datetime.now(timezone.utc)
    else:
        digest = PmfWeeklyDigest(
            team_id=team_id,
            week_start=week_start,
            total_calls=total_calls,
            feature_demand=feature_demand,
            objections=objection_data,
            competitors=competitor_data,
            buy_signal_rate=buy_rate,
            buy_signal_stars=buy_signal_stars(buy_rate),
            buy_signal_prev_rate=prev_buy_rate,
            pain_clarity_rate=pain_rate,
            pain_clarity_stars=pain_clarity_stars(pain_rate),
            pain_clarity_top_pain=top_pain,
            pain_clarity_prev_rate=prev_pain_rate,
            narrative=narrative,
        )
        db.add(digest)

    await db.flush()
    return digest


async def _compute_dimension(
    db: AsyncSession,
    team_id,
    model_class,
    group_column,
    week_start: date,
    week_end: date,
    prev_week_start: date,
    total_calls: int,
) -> list[dict[str, Any]]:
    """Compute a grouped dimension (feature/objection/competitor) with trends."""

    # Current week
    result = await db.execute(
        select(
            group_column,
            func.count(distinct(model_class.meeting_id)).label("calls_mentioned"),
            func.count(model_class.id).label("total_mentions"),
        ).where(
            model_class.team_id == team_id,
            model_class.call_date >= week_start,
            model_class.call_date < week_end,
        ).group_by(group_column).order_by(func.count(distinct(model_class.meeting_id)).desc()).limit(10)
    )
    current_rows = result.all()

    # Previous week counts for trend
    prev_result = await db.execute(
        select(
            group_column,
            func.count(distinct(model_class.meeting_id)).label("calls_mentioned"),
        ).where(
            model_class.team_id == team_id,
            model_class.call_date >= prev_week_start,
            model_class.call_date < week_start,
        ).group_by(group_column)
    )
    prev_map = {row[0]: row[1] for row in prev_result.all()}

    items = []
    for row in current_rows:
        value = row[0]
        calls_mentioned = row[1]
        total_mentions = row[2]
        prev_count = prev_map.get(value, 0)
        ratio = calls_mentioned / max(total_calls, 1)

        items.append({
            "value": value,
            "calls_mentioned": calls_mentioned,
            "total_calls": total_calls,
            "stars": ratio_to_stars(ratio),
            "trend": _trend_arrow(calls_mentioned, prev_count if prev_count > 0 else None),
            "prev_count": prev_count,
            "mentions": total_mentions,
        })

    return items


async def _generate_narrative(
    features, objections, competitors,
    buy_rate, pain_rate, top_pain, total_calls,
) -> str:
    """Generate LLM narrative for PMF digest (best-effort, falls back to template)."""
    import os

    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        return _template_narrative(features, objections, competitors, buy_rate, pain_rate, top_pain, total_calls)

    try:
        import httpx

        top_features = [f["value"] for f in (features or [])[:3]]
        top_objections = [o["value"] for o in (objections or [])[:3]]
        top_competitors = [c["value"] for c in (competitors or [])[:3]]

        prompt = f"""Write a brief 2-3 sentence PMF (product-market fit) analysis paragraph for a sales team.

Data from {total_calls} calls this week:
- Top features requested: {', '.join(top_features) or 'None'}
- Top objections: {', '.join(top_objections) or 'None'}
- Top competitors mentioned: {', '.join(top_competitors) or 'None'}
- Buy signal rate: {buy_rate:.0%} of calls had committed next steps
- Pain clarity: {pain_rate:.0%} convergence on top pain point ({top_pain or 'N/A'})

Write in a direct, analytical tone. Focus on actionable insights. No headers or bullet points."""

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {groq_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200,
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"LLM narrative generation failed: {e}")
        return _template_narrative(features, objections, competitors, buy_rate, pain_rate, top_pain, total_calls)


def _template_narrative(features, objections, competitors, buy_rate, pain_rate, top_pain, total_calls) -> str:
    """Fallback template-based narrative."""
    parts = [f"This week's {total_calls} calls"]

    if features:
        parts.append(f"showed demand for {features[0]['value']} ({features[0]['calls_mentioned']} calls)")

    if buy_rate > 0.5:
        parts.append(f"with strong buy signals ({buy_rate:.0%} committed next steps)")
    elif buy_rate > 0.3:
        parts.append(f"with moderate buy signals ({buy_rate:.0%} committed next steps)")

    if top_pain and pain_rate > 0.3:
        parts.append(f"and convergence around '{top_pain}' as the primary pain point")

    return ". ".join(parts) + "." if parts else "Insufficient data for narrative."
