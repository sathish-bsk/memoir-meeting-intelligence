"""Structured per-phase timing log for join-to-record latency analysis.

Emits a single JSON line per phase boundary so post-hoc analysis can compute
mean/p50/p95 join times by phase. Read by Tier 0 of the join-latency
optimization plan (`plan/bot_join_latency_optimization.md`).

Usage:
    from app.services.phase_log import log_phase, set_job_context

    # In /join handler (when request arrives):
    set_job_context(request.job_id, request.dispatch_sent_at)
    log_phase("dispatch_received")

    # At any phase boundary:
    log_phase("browser_launched")
    log_phase("admission_detected", path="auto_admit")
    log_phase("recording_started")

Each log line:
    {"event": "phase_transition", "phase": "browser_launched",
     "ts_ms": 1715520123456, "job_id": "...",
     "t_since_container_started_ms": 4321,
     "t_since_dispatch_ms": 1234, ...extra}

t_since_dispatch_ms is the user-perceived latency. It's only present once
set_job_context() has been called with a dispatch_sent_at timestamp (i.e.
after /join arrives).
"""
import json
import logging
import time
from typing import Optional

# Tier 0: log to the "botworker" logger (configured in app/core/logging.py).
# Using logging.getLogger(__name__) silently drops messages because only the
# "botworker" logger has a StreamHandler attached — validated 2026-05-12 in
# EC2 prod where zero phase_transition lines appeared in container logs
# despite every code path firing log_phase().
logger = logging.getLogger("botworker")

# Module-level mutable state — set once per job lifecycle.
_job_context = {
    "job_id": None,
    "dispatch_sent_at": None,  # unix seconds, from orchestrator
    "container_started_at": time.time(),  # bot's own start time
}


def set_job_context(job_id: str, dispatch_sent_at: Optional[float] = None) -> None:
    """Bind the current job + dispatch timestamp to all subsequent log_phase calls.

    Call this from the /join handler as soon as the request body is parsed.
    """
    _job_context["job_id"] = job_id
    if dispatch_sent_at is not None:
        _job_context["dispatch_sent_at"] = dispatch_sent_at


def log_phase(phase: str, **extra) -> None:
    """Emit a structured phase_transition JSON log line.

    `phase` should be one of the well-known names from the plan:
      container_started · dispatch_received · browser_launched ·
      navigation_complete · pre_join_done · admission_detected · recording_started

    `**extra` is merged into the log line for context (e.g. platform="meet").
    """
    now = time.time()
    payload = {
        "event": "phase_transition",
        "phase": phase,
        "ts_ms": int(now * 1000),
        "job_id": _job_context["job_id"],
        "t_since_container_started_ms": int(
            (now - _job_context["container_started_at"]) * 1000
        ),
    }
    if _job_context["dispatch_sent_at"] is not None:
        payload["t_since_dispatch_ms"] = int(
            (now - _job_context["dispatch_sent_at"]) * 1000
        )
    payload.update(extra)
    logger.info(json.dumps(payload))
