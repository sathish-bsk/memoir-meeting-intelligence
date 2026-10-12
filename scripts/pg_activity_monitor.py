#!/usr/bin/env python3
"""pg_stat_activity snapshotter for slow-query diagnosis.

Polls pg_stat_activity every PG_ACTIVITY_POLL_SEC (default 0.5 s) and
logs any non-idle backend whose current query has been running longer
than PG_ACTIVITY_THRESHOLD_MS (default 1000 ms).

Each slow query is logged once when first observed above the threshold,
then again every time its observed running_ms increases by another
threshold-multiple — so a 10 s query produces ~10 log lines, not 20.

Logs include wait_event_type + wait_event, which is the critical signal:
    Lock              row/table lock contention
    LWLock            internal Postgres lock
    IO                disk read (RDS-side EBS / instance class limit)
    Client            waiting on app to consume result
    (none)            actually CPU-bound execution

Output goes to stdout, captured by systemd journald. Tail with:
    sudo journalctl -u memoir-pg-monitor -f

Env vars:
    DATABASE_URL                — required, asyncpg or postgresql:// URL
    PG_ACTIVITY_THRESHOLD_MS    — default 1000
    PG_ACTIVITY_POLL_SEC        — default 0.5
"""
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime

import asyncpg

THRESHOLD_MS = int(os.getenv("PG_ACTIVITY_THRESHOLD_MS", "1000"))
POLL_INTERVAL_SEC = float(os.getenv("PG_ACTIVITY_POLL_SEC", "0.5"))
DATABASE_URL = os.environ["DATABASE_URL"].replace(
    "postgresql+asyncpg://", "postgresql://"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("pg_activity")

QUERY = """
SELECT pid,
       coalesce(application_name, '') AS app,
       state,
       coalesce(wait_event_type, '-') AS wait_type,
       coalesce(wait_event, '-') AS wait_event,
       EXTRACT(EPOCH FROM (now() - query_start)) * 1000 AS running_ms,
       EXTRACT(EPOCH FROM (now() - xact_start)) * 1000 AS xact_ms,
       left(coalesce(query, ''), 240) AS q
FROM pg_stat_activity
WHERE state IS NOT NULL
  AND state <> 'idle'
  AND query_start IS NOT NULL
  AND now() - query_start > make_interval(secs => $1::float)
  AND pid <> pg_backend_pid()
ORDER BY query_start
"""


async def run_loop(conn: asyncpg.Connection) -> None:
    # Track (pid, query_start_ms) → last logged running_ms so we
    # only emit each slow query once per threshold-multiple of running time.
    last_logged: dict[tuple[int, int], int] = {}

    while True:
        rows = await conn.fetch(QUERY, THRESHOLD_MS / 1000.0)
        active_keys: set[tuple[int, int]] = set()

        for r in rows:
            running_ms = int(r["running_ms"])
            xact_ms = int(r["xact_ms"]) if r["xact_ms"] else 0
            # Key by pid + integer-second of running_ms-at-start so a
            # restart of the same backend with a new query gets a new entry.
            key_seed = running_ms - (running_ms % THRESHOLD_MS)
            key = (r["pid"], key_seed)
            active_keys.add(key)

            prev = last_logged.get(key, 0)
            if running_ms - prev >= THRESHOLD_MS:
                last_logged[key] = running_ms
                # Compress query whitespace for grep-ability
                q = " ".join((r["q"] or "").split())
                log.info(
                    f"[SLOW pid={r['pid']} app={r['app']} state={r['state']} "
                    f"wait={r['wait_type']}/{r['wait_event']} "
                    f"running={running_ms}ms xact={xact_ms}ms] {q}"
                )

        # GC entries for queries that have completed
        for k in list(last_logged):
            if k not in active_keys:
                del last_logged[k]

        await asyncio.sleep(POLL_INTERVAL_SEC)


async def main() -> None:
    log.info(
        f"pg_activity_monitor starting | threshold={THRESHOLD_MS}ms "
        f"poll={POLL_INTERVAL_SEC}s"
    )
    while True:
        try:
            conn = await asyncpg.connect(
                DATABASE_URL,
                server_settings={"application_name": "memoir-pg-monitor"},
                timeout=10,
            )
            log.info("connected to postgres")
            try:
                await run_loop(conn)
            finally:
                await conn.close()
        except (
            asyncpg.exceptions.ConnectionDoesNotExistError,
            asyncpg.exceptions.InterfaceError,
            OSError,
        ) as e:
            log.warning(f"connection lost: {e}; reconnecting in 5s")
            await asyncio.sleep(5)
        except Exception as e:
            log.error(f"unexpected error: {e}; restarting in 10s")
            await asyncio.sleep(10)


def _sigterm(*_):
    log.info("received SIGTERM, exiting")
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)
    asyncio.run(main())
