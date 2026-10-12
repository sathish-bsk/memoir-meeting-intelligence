"""Per-request timing middleware (P3.2, 2026-05-31).

Responsibility split (see plan/performance-optimization-audit.md §3.2):

  1. **This middleware** sets a `request_id` contextvar at request
     entry and stores `request_start`, the resolved `endpoint`, and a
     placeholder `response_ms` into the per-request metrics dict that
     the SQLAlchemy engine listeners (in `app/core/database.py`) write
     into. At exit it computes `response_ms` (time up to response
     generation, NOT total wall-clock). It does NOT emit the
     structured log line for DB-backed routes — `get_db` teardown does
     that, after `session.close()` has run and every checkin event
     has been observed.

  2. **Engine event listeners** (in `app/core/database.py`) accumulate
     `db_query_count`, `db_query_ms_total`, and `pool_hold_ms_total`
     into the metrics dict throughout the request lifecycle.

  3. **`get_db` teardown** (in `app/core/database.py`) computes the
     canonical `total_ms = now - request_start` AFTER `session.close()`
     and emits the structured `[REQ]` log line. Sets `_logged=True`
     on the metrics dict so this middleware knows not to emit a
     fallback line.

  4. **This middleware (fallback)**: if no `get_db` ran for the request
     (non-DB-backed routes like `/health`), the metrics dict will not
     have been logged. The middleware emits a thin fallback log line
     with just `endpoint` and `response_ms` — `total_ms` isn't
     computable here without observing teardown.

Naming discipline:
  * `response_ms` — middleware measurement, time up to response generation.
  * `total_ms` — teardown measurement, full wall-clock including teardown.
  * They are NOT interchangeable. `total_ms >= response_ms` always.
"""
from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.database import set_request_metrics, reset_request_metrics

logger = logging.getLogger(__name__)


class RequestTimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        # Endpoint is best-effort here — Starlette resolves the route
        # AFTER routing, so before call_next we only have the raw path.
        # Good enough for the log line; if you want the route template
        # (e.g. "/api/meetings/{meeting_id}/status") instead of the
        # concrete path, post-process the log via grouping or use
        # request.scope["route"].path inside a downstream middleware.
        endpoint = request.url.path
        metrics: dict = {
            "request_id": request_id,
            "endpoint": endpoint,
            "request_start": time.monotonic(),
            # Counters populated by SQLA engine event listeners:
            "db_query_count": 0,
            "db_query_ms_total": 0.0,
            "pool_hold_ms_total": 0.0,
            # _logged flips to True when get_db teardown emits the line;
            # the fallback log below skips when True.
            "_logged": False,
        }
        token = set_request_metrics(metrics)
        try:
            response: Response = await call_next(request)
            metrics["response_ms"] = (
                time.monotonic() - metrics["request_start"]
            ) * 1000
            return response
        finally:
            # Fallback for non-DB-backed routes (health checks, static).
            # DB-backed routes are logged by get_db teardown, which runs
            # AFTER call_next returns but may run BEFORE this `finally`
            # depending on FastAPI's yield-dependency execution order.
            # Either way, if teardown ran it set _logged=True and we
            # skip here.
            if not metrics.get("_logged"):
                logger.info(
                    f"[REQ] endpoint={metrics['endpoint']} "
                    f"response_ms={metrics.get('response_ms', 0):.0f} "
                    f"request_id={metrics['request_id']}"
                )
            reset_request_metrics(token)
