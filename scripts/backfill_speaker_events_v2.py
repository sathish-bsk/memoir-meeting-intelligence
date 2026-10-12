"""One-shot R2 backfill: speaker_events.json v1 → v2 schema.

After migration 040 + the BotWorker/tab_audio writer flip, every NEW
recording writes the v2 schema (structured object with events + participants
+ envelope fields). Historical recordings on R2 are still v1 (flat array
of events). The TranscriptionBase reader shim handles both schemas
forever, so this backfill is OPTIONAL for functional correctness — it
exists only to make every file on R2 structurally consistent.

Behavior:
  - Scans R2 prefixes for *speaker_events.json files
  - For each file:
      * If schema_version == 2 → skip (idempotent)
      * If isinstance(data, list) → wrap into v2 envelope with empty
        participants and source derived from the matching meeting row
        in the DB (best-effort)
      * Else → log + skip
  - Dry-run mode (--dry-run) reports counts + diff without writing

Recovery rule for participants on old meetings:
  Per plan §5.3 option 1, we backfill with `participants: []` rather
  than attempting to reconstruct the original DOM-scraped roster from
  the current meeting_participants DB state. speaker_mapping.py
  routinely mutates those rows in place (setting diarization_label,
  upgrading mapping_source to voice_match / active_speaker_overlap),
  so a query for "original captures" would either miss real names or
  pull in post-mapping artifacts. Empty participants is honest about
  what we can reconstruct; the TB shim treats empty equivalently to
  missing.

Run:
  # Dry-run on dev first
  python scripts/backfill_speaker_events_v2.py --dry-run --env dev

  # Then prod, in a quiet window
  python scripts/backfill_speaker_events_v2.py --env prod

R2 versioning consideration: if the bucket has object versioning ON,
the v1 originals are recoverable as previous object versions during the
rewrite window. If versioning is OFF, the backfill overwrites in place
and v1 is gone. Verify the bucket setting before running prod; consider
enabling versioning for the duration of the backfill if currently off.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "core_api"))


def _list_speaker_events_keys_s3(bucket: str, prefixes: list[str]) -> list[str]:
    """List all speaker_events.json keys under the given R2/S3 prefixes.

    The videoclip_common StorageBackend abstraction doesn't expose a list
    method (see videoclip_common/storage.py — only save/read/exists). For a
    one-shot backfill we drop down to boto3 directly so we don't have to
    extend the production interface for an offline script.
    """
    import boto3
    from botocore.client import Config as BotoConfig

    from app.core.config import settings as _settings

    s3 = boto3.client(
        "s3",
        endpoint_url=_settings.S3_ENDPOINT_URL or None,
        region_name=_settings.S3_REGION or "us-east-1",
        config=BotoConfig(signature_version="s3v4"),
    )

    keys: list[str] = []
    for prefix in prefixes:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                k = obj["Key"]
                if k.endswith("/speaker_events.json"):
                    keys.append(k)
    return keys


async def _load_meeting_source(db, meeting_id: str) -> str | None:
    """Look up meeting.source for the row corresponding to this R2 key."""
    from sqlalchemy import text as sa_text

    result = await db.execute(
        sa_text("SELECT source FROM meetings WHERE id = :mid"),
        {"mid": meeting_id},
    )
    row = result.first()
    return row[0] if row else None


def _meeting_id_from_key(key: str) -> str | None:
    """Extract a meeting UUID from a key like 'uploads/{uuid}/speaker_events.json'
    or '{job_id}/speaker_events.json' (bot path — job_id != meeting_id).

    Returns None if the key shape doesn't expose a meeting_id. For the
    bot path the script still upgrades the file shape, just without a
    DB-derived source attribution (defaults to "bot_capture")."""
    parts = key.split("/")
    if len(parts) >= 2 and parts[-1] == "speaker_events.json":
        candidate = parts[-2]
        # Crude UUID shape check
        if len(candidate) == 36 and candidate.count("-") == 4:
            return candidate
    return None


def _wrap_v1_to_v2(
    events: list[dict],
    source_hint: str,
    captured_at: str | None,
) -> dict:
    """Build the v2 envelope from a v1 events list."""
    return {
        "schema_version": 2,
        "captured_at": captured_at or datetime.now(timezone.utc).isoformat(),
        "source": source_hint,
        "events": events,
        "participants": [],
    }


async def backfill(env: str, dry_run: bool) -> None:
    # Lazy imports so the script can be invoked from anywhere
    from app.core.config import settings  # noqa: E402
    from app.core.database import async_session  # noqa: E402
    from videoclip_common.storage import create_storage  # noqa: E402

    if settings.STORAGE_BACKEND != "s3":
        print(f"STORAGE_BACKEND={settings.STORAGE_BACKEND!r} — backfill only runs against s3/R2")
        return

    storage = create_storage()
    # Two prefix conventions in production:
    #   uploads/<meeting_id>/speaker_events.json    (tab_audio + web recorder)
    #   <job_id>/speaker_events.json                 (bot path)
    keys = _list_speaker_events_keys_s3(settings.S3_BUCKET, ["uploads/", ""])
    print(f"Found {len(keys)} speaker_events.json keys under R2 in env={env}")

    stats = {"v2_skipped": 0, "v1_upgraded": 0, "malformed": 0, "errors": 0}

    async with async_session() as db:
        for key in keys:
            try:
                raw = await storage.read_json(key)
            except Exception as e:
                print(f"  ERROR reading {key}: {e!r}")
                stats["errors"] += 1
                continue

            # Already v2 — skip
            if isinstance(raw, dict) and raw.get("schema_version") == 2:
                stats["v2_skipped"] += 1
                continue

            # v1 flat array — wrap
            if isinstance(raw, list):
                meeting_id = _meeting_id_from_key(key)
                source_hint = "bot_capture"
                captured_at = None
                if meeting_id:
                    try:
                        meeting_source = await _load_meeting_source(db, meeting_id)
                        if meeting_source == "web_recorder":
                            source_hint = "web_capture"
                    except Exception as e:
                        print(f"  WARN: source lookup for {meeting_id} failed: {e!r}")

                v2 = _wrap_v1_to_v2(raw, source_hint, captured_at)

                if dry_run:
                    print(f"  [dry-run] would upgrade {key} → v2 (source={source_hint}, events={len(raw)})")
                else:
                    try:
                        await storage.save_json(key, v2)
                        print(f"  upgraded {key} → v2 (source={source_hint}, events={len(raw)})")
                    except Exception as e:
                        print(f"  ERROR writing {key}: {e!r}")
                        stats["errors"] += 1
                        continue
                stats["v1_upgraded"] += 1
                continue

            # Unknown shape
            print(f"  WARN: unknown shape at {key} (type={type(raw).__name__})")
            stats["malformed"] += 1

    print("\nSummary:")
    print(f"  Already v2 (skipped):   {stats['v2_skipped']}")
    print(f"  v1 → v2 upgrades:       {stats['v1_upgraded']} {'(dry-run, no writes)' if dry_run else ''}")
    print(f"  Malformed / unknown:    {stats['malformed']}")
    print(f"  Errors:                 {stats['errors']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill speaker_events.json v1 → v2")
    parser.add_argument(
        "--env",
        choices=["dev", "staging", "prod"],
        default="dev",
        help="Target environment label (for logging only; actual config comes from env vars)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing to R2",
    )
    args = parser.parse_args()

    print(f"speaker_events.json v1 → v2 backfill (env={args.env}, dry_run={args.dry_run})")
    if not args.dry_run and args.env == "prod":
        print("PRODUCTION RUN — confirm bucket has versioning enabled before proceeding")
        confirm = input("Type 'yes' to continue: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            return

    asyncio.run(backfill(args.env, args.dry_run))


if __name__ == "__main__":
    main()
