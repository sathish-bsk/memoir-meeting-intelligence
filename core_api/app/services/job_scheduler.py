"""
Generic scheduled job runner — polls the scheduled_jobs table and executes due jobs.

Uses a 1-minute polling interval. Actual schedule is driven by next_run_at in the DB,
which is computed from user-configurable frequency, preferred_day, and preferred_time.
"""

import logging
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_intelligence import ScheduledJob, ScheduledJobRun

logger = logging.getLogger(__name__)


def compute_next_run(
    frequency: str,
    preferred_day: int | None = None,
    preferred_time: time | None = None,
    from_time: datetime | None = None,
) -> datetime:
    """Compute the next run datetime based on frequency and preferences.

    Args:
        frequency: "daily", "weekly", "biweekly", or "monthly"
        preferred_day: Day of week (0=Mon) for weekly/biweekly, day of month (1-28) for monthly
        preferred_time: Time of day to run (default 06:00 UTC)
        from_time: Base time to compute from (default now)
    """
    now = from_time or datetime.now(timezone.utc)
    run_time = preferred_time or time(6, 0)

    if frequency == "daily":
        # Next day at preferred_time
        candidate = now.replace(
            hour=run_time.hour, minute=run_time.minute, second=0, microsecond=0,
        )
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    elif frequency == "weekly":
        day = preferred_day if preferred_day is not None else 0  # Monday
        days_ahead = day - now.weekday()
        if days_ahead < 0:
            days_ahead += 7
        candidate = now + timedelta(days=days_ahead)
        candidate = candidate.replace(
            hour=run_time.hour, minute=run_time.minute, second=0, microsecond=0,
        )
        if candidate <= now:
            candidate += timedelta(weeks=1)
        return candidate

    elif frequency == "biweekly":
        day = preferred_day if preferred_day is not None else 0
        days_ahead = day - now.weekday()
        if days_ahead < 0:
            days_ahead += 7
        candidate = now + timedelta(days=days_ahead)
        candidate = candidate.replace(
            hour=run_time.hour, minute=run_time.minute, second=0, microsecond=0,
        )
        if candidate <= now:
            candidate += timedelta(weeks=2)
        return candidate

    elif frequency == "monthly":
        target_day = preferred_day if preferred_day and 1 <= preferred_day <= 28 else 1
        # Try this month first
        candidate = now.replace(
            day=target_day,
            hour=run_time.hour, minute=run_time.minute, second=0, microsecond=0,
        )
        if candidate <= now:
            # Next month
            if now.month == 12:
                candidate = candidate.replace(year=now.year + 1, month=1)
            else:
                candidate = candidate.replace(month=now.month + 1)
        return candidate

    else:
        # Fallback: weekly
        logger.warning(f"Unknown frequency '{frequency}', defaulting to weekly")
        return compute_next_run("weekly", preferred_day, preferred_time, from_time)


async def check_and_run_due_jobs(db: AsyncSession) -> int:
    """Poll for due jobs and execute them. Returns number of jobs processed."""
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(ScheduledJob).where(
            ScheduledJob.enabled == True,  # noqa: E712
            ScheduledJob.next_run_at <= now,
        )
    )
    due_jobs = result.scalars().all()

    if not due_jobs:
        return 0

    processed = 0
    for job in due_jobs:
        try:
            await _execute_job(db, job)
            processed += 1
        except Exception as e:
            logger.error(f"Scheduled job {job.id} ({job.job_type}) failed: {e}")

    return processed


async def _execute_job(db: AsyncSession, job: ScheduledJob) -> None:
    """Execute a single scheduled job and update run history."""
    now = datetime.now(timezone.utc)

    # Create audit log entry
    run = ScheduledJobRun(
        job_id=job.id,
        team_id=job.team_id,
        job_type=job.job_type,
        started_at=now,
        status="running",
    )
    db.add(run)
    await db.flush()

    try:
        if job.job_type == "intelligence_digest":
            result_summary = await _run_digest_job(db, job)
        elif job.job_type == "faq_digest":
            result_summary = await _run_faq_digest_job(db, job)
        elif job.job_type == "pmf_digest":
            result_summary = await _run_pmf_digest_job(db, job)
        else:
            result_summary = f"Unknown job type: {job.job_type}"
            run.status = "failed"
            run.error_message = result_summary

        if run.status == "running":
            run.status = "success"
            run.result_summary = result_summary

    except Exception as e:
        run.status = "failed"
        run.error_message = str(e)[:1000]
        logger.error(f"Job {job.id} execution failed: {e}")

    # Update job metadata
    run.completed_at = datetime.now(timezone.utc)
    job.last_run_at = now
    job.last_status = run.status
    job.next_run_at = compute_next_run(
        job.frequency, job.preferred_day, job.preferred_time, from_time=now,
    )
    job.updated_at = datetime.now(timezone.utc)

    await db.commit()
    logger.info(
        f"Job {job.id} ({job.job_type}): {run.status} — next run at {job.next_run_at}"
    )


async def _run_digest_job(db: AsyncSession, job: ScheduledJob) -> str:
    """Run the intelligence digest generation job and dispatch per-user notifications."""
    from app.services.digest_generator import generate_digest

    digest = await generate_digest(db, job.team_id, since=job.last_run_at)

    if digest is None:
        return "Skipped — no external calls in period"

    # Dispatch notifications to team members
    try:
        await _dispatch_digest_notifications(db, job.team_id, digest, "intelligence_digest")
    except Exception as e:
        logger.error(f"Failed to dispatch digest notifications: {e}")

    return f"Generated digest for {digest.total_calls} calls"


async def _run_faq_digest_job(db: AsyncSession, job: ScheduledJob) -> str:
    """Run the FAQ digest generation job (reuses digest generator for FAQ groups)."""
    from app.services.digest_generator import generate_digest

    digest = await generate_digest(db, job.team_id, since=job.last_run_at)

    if digest is None:
        return "Skipped — no external calls in period"

    faq_groups = (digest.metrics or {}).get("faq_groups", [])

    # Dispatch FAQ digest notifications
    try:
        await _dispatch_digest_notifications(db, job.team_id, digest, "faq_digest")
    except Exception as e:
        logger.error(f"Failed to dispatch FAQ digest notifications: {e}")

    return f"Generated FAQ digest with {len(faq_groups)} question groups"


async def _run_pmf_digest_job(db: AsyncSession, job: ScheduledJob) -> str:
    """Run the PMF weekly digest computation job."""
    from app.services.pmf_digest_generator import generate_pmf_weekly_digest

    digest = await generate_pmf_weekly_digest(db, job.team_id)

    if digest is None:
        return "Skipped — fewer than 3 calls in period"

    return f"Generated PMF digest: {digest.total_calls} calls, buy signal {digest.buy_signal_rate:.0%}"


async def _dispatch_digest_notifications(
    db: AsyncSession, team_id, digest, event_type: str,
) -> None:
    """Dispatch digest notifications to all team members based on their roles."""
    import uuid as _uuid

    from app.models.team import TeamMember
    from app.services.notification_dispatcher import dispatch_notification

    # Get all accepted team members
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.invite_status.in_(["accepted", None]),
        )
    )
    members = result.scalars().all()

    if not members:
        return

    # Fetch user emails
    user_ids = [str(m.user_id) for m in members]
    try:
        from app.clients.auth_client import batch_get_users
        users = await batch_get_users(user_ids)
    except Exception:
        users = {}

    narrative = digest.narrative or ""
    summary_preview = narrative[:200] + ("..." if len(narrative) > 200 else "")

    is_faq = event_type == "faq_digest"
    title = "FAQ Digest" if is_faq else "Intelligence Digest"

    for member in members:
        user_info = users.get(str(member.user_id), {})
        email = user_info.get("email")

        is_admin = member.role in ("owner", "admin")

        # Build body preview based on role
        if is_admin:
            body = summary_preview
        else:
            body = f"Your personal {title.lower()} is ready."

        slack_text = f"*{title}*\n{body[:500]}"

        await dispatch_notification(
            db=db,
            event_type=event_type,
            user_id=member.user_id,
            team_id=team_id,
            title=title,
            body=body,
            link="/intelligence",
            email_to=email,
            email_template="intelligence_digest" if not is_faq else "faq_digest",
            email_data={
                "name": user_info.get("name") or "there",
                "title": title,
                "narrative": narrative if is_admin else body,
                "is_admin": is_admin,
                "total_calls": digest.total_calls,
                "link": "/intelligence",
            },
            slack_text=slack_text,
        )
