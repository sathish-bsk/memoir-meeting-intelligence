"""
Async SQLAlchemy engine and session management for Core API.
"""
import contextvars
import logging
import time
import uuid
from typing import Any, Optional

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── P3.2 per-endpoint timing infrastructure (2026-05-31) ─────────────
#
# The contextvar holds a per-request dict that travels through async
# call stacks. The middleware sets it at request entry; engine event
# listeners (below) accumulate per-query / per-pool counters into it;
# get_db teardown reads it at the very end and emits a structured log
# line. See plan/performance-optimization-audit.md §3.2 for the full
# rationale (three-responsibility split, response_ms vs total_ms
# naming discipline).
_request_metrics: contextvars.ContextVar[Optional[dict[str, Any]]] = (
    contextvars.ContextVar("request_metrics", default=None)
)


def get_request_metrics() -> Optional[dict[str, Any]]:
    """Public accessor used by the timing middleware to read/write the
    per-request metrics dict from outside this module."""
    return _request_metrics.get()


def set_request_metrics(metrics: dict[str, Any]) -> contextvars.Token:
    """Public setter used by the timing middleware. Returns a token the
    caller MUST pass to reset_request_metrics() at request exit so the
    dict doesn't leak across requests on the same event-loop task."""
    return _request_metrics.set(metrics)


def reset_request_metrics(token: contextvars.Token) -> None:
    _request_metrics.reset(token)

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=True,
    echo=settings.is_dev,
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# Query-level timing instrumentation — added 2026-05-14 to diagnose the
# slow meeting_lookup (5.5 s observed in production). Logs every query
# that takes longer than SLOW_QUERY_MS so we can see if it's a single
# slow SELECT vs. pool / pre-ping overhead. Listens at the sync layer
# (sqlalchemy.engine.Engine) which fires for both sync + async engines.
SLOW_QUERY_MS = 100  # log everything above this; tune up after debugging


@event.listens_for(Engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    context._query_start_time = time.monotonic()


@event.listens_for(Engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    elapsed_ms = (time.monotonic() - context._query_start_time) * 1000
    if elapsed_ms > SLOW_QUERY_MS:
        # Truncate the SQL to keep log lines readable. Parameters omitted to
        # avoid PII leakage in logs.
        sql_snippet = " ".join(statement.split())[:200]
        logger.info(
            f"[DB_QUERY] {elapsed_ms:.0f}ms: {sql_snippet}"
        )
    # P3.2: accumulate per-request counters into the contextvar-backed
    # metrics dict (if set by the timing middleware).
    metrics = _request_metrics.get()
    if metrics is not None:
        metrics["db_query_count"] = metrics.get("db_query_count", 0) + 1
        metrics["db_query_ms_total"] = metrics.get("db_query_ms_total", 0.0) + elapsed_ms


# Pool checkout / checkin timing — observe how long the connection
# pool takes to hand out a connection (includes pool_pre_ping's
# SELECT 1 round-trip).
@event.listens_for(engine.sync_engine, "connect")
def _on_connect(dbapi_connection, connection_record):
    logger.info("[DB_POOL] new connection established")


@event.listens_for(engine.sync_engine, "checkout")
def _on_checkout(dbapi_connection, connection_record, connection_proxy):
    connection_record.info["_checkout_t"] = time.monotonic()


@event.listens_for(engine.sync_engine, "checkin")
def _on_checkin(dbapi_connection, connection_record):
    t_checkout = connection_record.info.get("_checkout_t")
    if t_checkout is not None:
        elapsed_ms = (time.monotonic() - t_checkout) * 1000
        if elapsed_ms > 500:  # only log long-held connections
            logger.info(
                f"[DB_POOL] connection held {elapsed_ms:.0f}ms before checkin"
            )
        # P3.2: accumulate cumulative pool-hold time for the request.
        metrics = _request_metrics.get()
        if metrics is not None:
            metrics["pool_hold_ms_total"] = (
                metrics.get("pool_hold_ms_total", 0.0) + elapsed_ms
            )


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """Dependency: yields a database session, auto-closes on exit.

    Instrumented (2026-05-14) to log how long pool checkout + pre-ping
    take. The 5.5 s meeting_lookup observed in production could be in
    the pool, the SELECT, or both — this isolates them.

    P3.2 (2026-05-31): the teardown step (after `yield`) is the only
    place guaranteed to observe every checkin event for the request,
    so it computes the canonical `total_ms` from the timing middleware's
    `request_start` and emits the structured per-request log line.
    """
    _t_enter = time.monotonic()
    async with async_session() as session:
        _t_session_ready = time.monotonic()
        pool_wait_ms = (_t_session_ready - _t_enter) * 1000
        if pool_wait_ms > 100:
            logger.info(
                f"[DB_POOL] async_session checkout took {pool_wait_ms:.0f}ms"
            )
        try:
            yield session
        finally:
            await session.close()
            # P3.2: emit the canonical per-request log line. If the
            # request didn't go through the timing middleware (CLI
            # scripts, background workers using async_session() directly),
            # the contextvar is None and we silently skip.
            metrics = _request_metrics.get()
            if metrics is not None and "request_start" in metrics:
                total_ms = (time.monotonic() - metrics["request_start"]) * 1000
                logger.info(
                    f"[REQ] endpoint={metrics.get('endpoint', '?')} "
                    f"total_ms={total_ms:.0f} "
                    f"response_ms={metrics.get('response_ms', 0):.0f} "
                    f"pool_hold_ms={metrics.get('pool_hold_ms_total', 0.0):.0f} "
                    f"db_query_count={metrics.get('db_query_count', 0)} "
                    f"db_query_ms={metrics.get('db_query_ms_total', 0.0):.0f} "
                    f"request_id={metrics.get('request_id', '-')}"
                )
                # Mark as logged so the middleware skips the non-DB
                # fallback log for this request.
                metrics["_logged"] = True
