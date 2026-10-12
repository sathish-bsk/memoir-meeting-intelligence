"""
GPU cost estimation and budget alerting.

GPU cost is calculated from processing time × hourly rate.
LLM costs are already calculated by each provider class in intelligence_pipeline.py.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# GPU hourly rates (USD per hour)
# Source: DigitalOcean/Lambda/IndiaAI pricing
GPU_RATES: dict[str, float] = {
    "T4": 0.526,        # ~$0.526/hr DigitalOcean GPU Droplet
    "T4_india": 0.78,   # ~Rs 65/hr IndiaAI, converted to USD
    "A100": 2.21,       # ~$2.21/hr Lambda
    "CPU": 0.0,         # Local CPU — no cost
}


def estimate_gpu_cost(processing_seconds: float | None, gpu_type: str | None) -> float:
    """Estimate cost from wall-clock processing time and GPU type."""
    if not processing_seconds or not gpu_type:
        return 0.0
    rate = GPU_RATES.get(gpu_type, 0.0)
    return round((processing_seconds / 3600.0) * rate, 6)


async def check_budget_after_meeting(
    db: AsyncSession,
    team_id: Any,
) -> None:
    """
    Check if team has exceeded their monthly budget threshold after a new meeting is processed.
    Dispatches a budget_alert notification if threshold exceeded and not recently alerted.
    """
    from app.models.team_budget import TeamBudget
    from app.models.ai_prompt_log import AiPromptLog

    # Get team budget settings
    result = await db.execute(
        select(TeamBudget).where(TeamBudget.team_id == team_id)
    )
    budget = result.scalar_one_or_none()
    if not budget or not budget.monthly_budget_usd:
        return

    # Get current month total
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cost_result = await db.execute(
        select(func.sum(AiPromptLog.cost_usd)).where(
            AiPromptLog.team_id == team_id,
            AiPromptLog.created_at >= month_start,
        )
    )
    current_cost = cost_result.scalar() or 0.0

    threshold_usd = budget.monthly_budget_usd * (budget.alert_threshold_pct / 100.0)
    if current_cost < threshold_usd:
        return

    # Check if we already sent an alert recently (within last 24h)
    if budget.last_alert_sent_at:
        hours_since = (datetime.now(timezone.utc) - budget.last_alert_sent_at).total_seconds() / 3600
        if hours_since < 24:
            return

    # Dispatch notification
    try:
        from app.services.notification_dispatcher import dispatch_notification
        await dispatch_notification(
            db=db,
            team_id=team_id,
            event_type="budget_alert",
            payload={
                "current_cost_usd": round(current_cost, 4),
                "monthly_budget_usd": budget.monthly_budget_usd,
                "threshold_pct": budget.alert_threshold_pct,
                "threshold_usd": round(threshold_usd, 4),
                "pct_used": round((current_cost / budget.monthly_budget_usd) * 100, 1),
            },
        )
        budget.last_alert_sent_at = datetime.now(timezone.utc)
        logger.info(f"Budget alert dispatched for team {team_id}: ${current_cost:.4f} / ${budget.monthly_budget_usd}")
    except Exception as e:
        logger.warning(f"Failed to dispatch budget alert for team {team_id}: {e}")
