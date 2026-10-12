"""One-shot speaker-profile merge utility.

Used to clean up duplicate profiles that landed in the DB BEFORE the
voice-aware _find_or_create_profile fix shipped — e.g. the "Sathish
Kumar" / "Sathish Kumar Balasubramani" pair we identified by running
voice-embedding cosine comparison and confirming same speaker.

Usage:
    # Dry-run — report counts only, no writes
    DATABASE_URL=postgresql+asyncpg://... \\
        python merge_speaker_profiles.py \\
            --source <duplicate-profile-uuid> \\
            --target <canonical-profile-uuid> \\
            --dry-run

    # Live — execute the merge
    DATABASE_URL=... python merge_speaker_profiles.py \\
        --source <duplicate-uuid> --target <canonical-uuid>

The script wraps app.services.profile_merge.merge_profiles which also
backs the manual-merge UI endpoint, so semantics stay identical.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

# Make `app.*` imports work whether the script is run from core_api/ or scripts/
HERE = os.path.dirname(os.path.abspath(__file__))
CORE_API_ROOT = os.path.dirname(HERE)
sys.path.insert(0, CORE_API_ROOT)

from app.services.profile_merge import merge_profiles, MergeError  # noqa: E402


async def main():
    parser = argparse.ArgumentParser(description="Merge one speaker profile into another.")
    parser.add_argument("--source", required=True, help="UUID of the duplicate profile to remove")
    parser.add_argument("--target", required=True, help="UUID of the canonical profile to keep")
    parser.add_argument("--dry-run", action="store_true", help="Report counts only — no DB writes")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL env var required", file=sys.stderr)
        sys.exit(2)
    if not db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    source_id = uuid.UUID(args.source)
    target_id = uuid.UUID(args.target)

    engine = create_async_engine(db_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        try:
            report = await merge_profiles(db, source_id, target_id, dry_run=args.dry_run)
        except MergeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            await engine.dispose()
            sys.exit(1)

        if args.dry_run:
            print("DRY RUN — no writes performed")
        else:
            await db.commit()
            print("COMMITTED")

        print(json.dumps(report, indent=2, default=str))

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
