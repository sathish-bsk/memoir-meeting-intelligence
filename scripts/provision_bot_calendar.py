"""Provision the synthetic bot calendar connection.

Phase 2 (notetaker-email plan §4.2) — creates the single
calendar_connections row that represents the bot's Workspace calendar.
This row owns the Calendar Watch we register on hello@meetmemoir.com's
calendar; the synthetic user_id is reserved purely so existing
non-NULL FK constraints are satisfied.

Run ONCE per environment after:
  1. The bot Workspace account exists (hello@meetmemoir.com).
  2. A service account with domain-wide delegation is created and its
     JSON key is stored in AWS Secrets Manager.
  3. Migration 039 has been applied.

Usage:
  python scripts/provision_bot_calendar.py \
      --secret-arn arn:aws:secretsmanager:ap-south-1:...:secret:memoir-bot-sa-xyz \
      --bot-email hello@meetmemoir.com

The script is idempotent — re-running it updates the pointer without
creating a duplicate connection.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Deterministic UUIDs so the synthetic rows are always the same
# regardless of when/where we provision. Using uuid5 with a fixed
# namespace string.
_NAMESPACE = uuid.UUID("00000000-0000-0000-0000-0000abc0bot0")
BOT_CALENDAR_USER_UUID = uuid.uuid5(_NAMESPACE, "system-bot-calendar-user")
BOT_CALENDAR_CONNECTION_UUID = uuid.uuid5(_NAMESPACE, "system-bot-calendar-connection")


async def provision(secret_arn: str, bot_email: str) -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("DATABASE_URL is required")

    fernet_key = os.environ.get("CALENDAR_ENCRYPTION_KEY")
    if not fernet_key:
        sys.exit("CALENDAR_ENCRYPTION_KEY is required")

    # The pointer payload defined in plan §4.2 — what gets stored in
    # calendar_connections.encrypted_tokens. JSON-encoded then
    # Fernet-encrypted so it can be decrypted by integration_service.
    pointer = {
        "auth_type": "service_account_dwd",
        "secret_arn": secret_arn,
        "delegated_email": bot_email,
    }
    encrypted = Fernet(fernet_key.encode()).encrypt(json.dumps(pointer).encode()).decode()

    engine = create_async_engine(db_url.replace("postgresql://", "postgresql+asyncpg://"))
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as db:  # type: AsyncSession
        # Use a raw SQL upsert so we don't need to import the SQLAlchemy
        # model (and pull in its dependency chain).
        result = await db.execute(
            select(1).where(
                __import__("sqlalchemy").literal_column("1").is_(
                    __import__("sqlalchemy").literal_column("1")
                )
            )
        )
        del result

        # Plain text upsert keeps this script dependency-light.
        await db.execute(
            __import__("sqlalchemy").text(
                """
                INSERT INTO calendar_connections (
                    id, user_id, provider, encrypted_tokens, provider_email,
                    auto_record, token_invalid, created_at, updated_at
                ) VALUES (
                    :id, :user_id, 'bot_calendar', :enc, :email,
                    true, false, now(), now()
                )
                ON CONFLICT (id) DO UPDATE SET
                    encrypted_tokens = EXCLUDED.encrypted_tokens,
                    provider_email = EXCLUDED.provider_email,
                    auto_record = true,
                    token_invalid = false,
                    updated_at = now()
                """
            ),
            {
                "id": str(BOT_CALENDAR_CONNECTION_UUID),
                "user_id": str(BOT_CALENDAR_USER_UUID),
                "enc": encrypted,
                "email": bot_email,
            },
        )
        await db.commit()

    print(f"✓ bot_calendar connection provisioned (id={BOT_CALENDAR_CONNECTION_UUID})")
    print(f"  delegated email: {bot_email}")
    print(f"  secret ARN:      {secret_arn}")
    print(f"  provisioned at:  {datetime.now(timezone.utc).isoformat()}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--secret-arn",
        required=True,
        help="ARN of the Secrets Manager secret holding the service-account JSON.",
    )
    p.add_argument(
        "--bot-email",
        default="hello@meetmemoir.com",
        help="The email the service account impersonates via domain-wide delegation.",
    )
    args = p.parse_args()
    asyncio.run(provision(args.secret_arn, args.bot_email))


if __name__ == "__main__":
    main()
