"""One-shot interactive bootstrap for the bot's authenticated browser
session (notetaker-email plan §5 Phase 3) — cookies-only mode.

Launches a headed Chromium with a FRESH (non-persistent) context. You
sign in to `hello@meetmemoir.com` (or whichever bot account) in the
browser, complete MFA + device-trust challenges, verify Meet works,
then press Enter. The script captures every google.com / meet.* /
mail.* cookie via `BrowserContext.cookies()`, writes the result as a
JSON blob, and uploads to Cloudflare R2 at:

    s3://<bucket>/bot-profiles/<provider>-cookies.json

The bot reads from this key at join time via
BotWorker/app/services/auth_session._materialize_cookies.

Why cookies-only instead of full persistent profile:
  - Persistent profiles (Chromium user_data_dir) contain OS-specific
    binary state — GPU caches, MediaDeviceSalts, SingletonLock, fonts.
    Capturing on macOS and loading in Linux Chromium crashes during
    `launch_persistent_context`.
  - Cookies are pure data. The cross-platform compatibility hop is
    free. Verified May 24 2026 against hello@meetmemoir.com Workspace.

Run this LOCALLY (your laptop), NOT on EC2 — the sign-in needs an
interactive desktop browser + your phone / authenticator for MFA.

Usage:
    # One-time venv setup:
    python3 -m venv scripts/.venv-bot-bootstrap
    scripts/.venv-bot-bootstrap/bin/pip install playwright boto3
    scripts/.venv-bot-bootstrap/bin/playwright install chromium

    # Set R2 credentials (grab from EC2 if you don't have them):
    ssh ubuntu@<ec2> 'sudo grep -E "^(S3|AWS)_" /etc/secrets/env/orchestrator.env'

    export R2_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
    export R2_ACCESS_KEY_ID=...
    export R2_SECRET_ACCESS_KEY=...
    export R2_BUCKET=memoir-recordings

    # Run the bootstrap:
    scripts/.venv-bot-bootstrap/bin/python scripts/bootstrap-bot-session.py \\
        --provider google \\
        --email hello@meetmemoir.com

What gets stored in R2:
    Key:  bot-profiles/<provider>-cookies.json
    Body: JSON array of Playwright cookie dicts (~10-30 KB)
    Metadata:
        x-amz-meta-provider=google
        x-amz-meta-email=hello@meetmemoir.com
        x-amz-meta-created-at=2026-05-24T12:34:56Z
        x-amz-meta-cookie-count=48

After successful upload, set in /etc/secrets/env/orchestrator.env on EC2:
    MEMOIR_BOT_GOOGLE_PROFILE_KEY=bot-profiles/google-cookies.json
    MEMOIR_BOT_AUTH_MODE=authenticated
    MEMOIR_BOT_INVITE_EMAIL=hello@meetmemoir.com

Then restart orchestrator (no BotWorker rebuild needed — the code
reads cookies dynamically from R2 each cold start):
    sudo systemctl restart memoir-orchestrator

Re-bootstrap (when the session expires, ~weeks to months): re-run
the same command. R2 object is overwritten in place. ETag changes,
each bot cache-misses once, then back to cache hits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


# Domains we always capture cookies for, regardless of which pages the
# operator visits during the interactive sign-in. The cross-domain set
# is necessary because Google's auth state is spread across these
# eTLD+1's.
COOKIE_DOMAINS = (
    "google.com",
    "google.co.in",
    "google.co.uk",
    "accounts.google.com",
    "meet.google.com",
    "mail.google.com",
    "myaccount.google.com",
    "youtube.com",  # Google sometimes mints session cookies under youtube.com
)


# Tier-1 verification URLs — these MUST resolve to signed-in pages
# before we capture, otherwise we'd ship an unauthenticated cookie set.
GOOGLE_VERIFY_URLS = [
    "https://meet.google.com/landing",
    "https://mail.google.com/",
]
MICROSOFT_VERIFY_URLS = [
    "https://teams.microsoft.com/",
    "https://outlook.office.com/mail/",
]


async def capture_cookies(provider: str, email: str) -> list[dict]:
    """Open headed Chromium, wait for operator to sign in, return the
    captured cookies as a list of Playwright cookie dicts."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print(
            "Error: playwright not installed in this venv. From repo root:\n"
            "  python3 -m venv scripts/.venv-bot-bootstrap\n"
            "  scripts/.venv-bot-bootstrap/bin/pip install playwright boto3\n"
            "  scripts/.venv-bot-bootstrap/bin/playwright install chromium",
            file=sys.stderr,
        )
        sys.exit(2)

    verify_urls = GOOGLE_VERIFY_URLS if provider == "google" else MICROSOFT_VERIFY_URLS
    # Match the bot's eventual UA so cookies are issued for that fingerprint.
    user_agent = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=user_agent,
        )
        page = await ctx.new_page()

        print("=" * 70)
        print(f"Sign in to {email} as {provider}.")
        print("Steps:")
        print(f"  1. Sign in at accounts.google.com (or login.microsoftonline.com)")
        print(f"  2. Complete MFA / device-trust prompts")
        print(f"  3. Visit each of these URLs to verify signed-in state:")
        for u in verify_urls:
            print(f"       {u}")
        print(f"  4. Come back here and press Enter to capture cookies.")
        print("=" * 70)
        print()

        if provider == "google":
            await page.goto("https://accounts.google.com/signin")
        else:
            await page.goto("https://login.microsoftonline.com/")

        try:
            await asyncio.get_event_loop().run_in_executor(
                None, input, "[press Enter once signed in + verified] "
            )
        except (KeyboardInterrupt, EOFError):
            print("Aborted before cookie capture.")
            await browser.close()
            sys.exit(1)

        all_cookies = await ctx.cookies()
        provider_cookies = [
            c for c in all_cookies
            if any(d in c.get("domain", "") for d in COOKIE_DOMAINS)
        ]
        # Normalize for Playwright's add_cookies on the bot side.
        for c in provider_cookies:
            ss = c.get("sameSite", "Lax")
            if ss not in ("Strict", "Lax", "None"):
                c["sameSite"] = "Lax"

        await browser.close()
        return provider_cookies


def print_cookie_summary(cookies: list[dict]) -> None:
    from collections import Counter
    counts = Counter(c.get("domain", "?") for c in cookies)
    print(f"\nCaptured {len(cookies)} cookies:")
    for d, n in counts.most_common():
        print(f"  {n:3d}  {d}")


def upload_to_r2(
    *,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    bucket: str,
    key: str,
    body: bytes,
    metadata: dict[str, str],
) -> str:
    try:
        import boto3
        from botocore.client import Config as BotoConfig
    except ImportError:
        print("Error: boto3 not installed in this venv.", file=sys.stderr)
        sys.exit(2)

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name="auto",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        config=BotoConfig(signature_version="s3v4"),
    )
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
        Metadata=metadata,
    )
    return f"s3://{bucket}/{key}"


async def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--provider", choices=["google", "microsoft"], required=True)
    parser.add_argument("--email", required=True, help="Bot account email (logged in metadata).")
    parser.add_argument(
        "--bucket",
        default=os.environ.get("R2_BUCKET") or os.environ.get("S3_BUCKET", "memoir-recordings"),
    )
    parser.add_argument(
        "--key-prefix", default="bot-profiles",
        help="R2 key prefix (default: bot-profiles). Final key: {prefix}/{provider}-cookies.json",
    )
    parser.add_argument(
        "--endpoint-url",
        default=os.environ.get("R2_ENDPOINT_URL") or os.environ.get("S3_ENDPOINT_URL", ""),
    )
    parser.add_argument(
        "--access-key-id",
        default=os.environ.get("R2_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID", ""),
    )
    parser.add_argument(
        "--secret-access-key",
        default=os.environ.get("R2_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
    )
    parser.add_argument(
        "--output-cookies",
        default=None,
        help=(
            "Write the cookies JSON to this local path AND skip R2 upload. "
            "Use when you'd rather upload via Cloudflare R2 dashboard. "
            "Example: --output-cookies /tmp/google-cookies.json"
        ),
    )
    args = parser.parse_args()

    skip_upload = args.output_cookies is not None

    if not skip_upload:
        if not args.endpoint_url:
            print(
                "Error: R2 endpoint URL not set.\n"
                "Export R2_ENDPOINT_URL (or S3_ENDPOINT_URL) or pass --endpoint-url.\n"
                "Or use --output-cookies to skip upload and upload via dashboard.",
                file=sys.stderr,
            )
            sys.exit(2)
        if not args.access_key_id or not args.secret_access_key:
            print(
                "Error: R2 credentials not set.\n"
                "Export R2_ACCESS_KEY_ID + R2_SECRET_ACCESS_KEY. Grab from EC2:\n"
                "  ssh ubuntu@<ec2-ip> 'sudo grep \"^AWS_\" /etc/secrets/env/orchestrator.env'\n"
                "Or use --output-cookies to skip upload and upload via dashboard.",
                file=sys.stderr,
            )
            sys.exit(2)

    key = f"{args.key_prefix.rstrip('/')}/{args.provider}-cookies.json"

    print(f"\n{'=' * 70}")
    print(f" Memoir Bot Session Bootstrap (cookies-only mode)")
    print(f"   provider:  {args.provider}")
    print(f"   email:     {args.email}")
    print(f"   target:    {'(local file only) ' + args.output_cookies if skip_upload else 's3://' + args.bucket + '/' + key}")
    if not skip_upload:
        print(f"   endpoint:  {args.endpoint_url}")
    print(f"{'=' * 70}\n")

    try:
        cookies = await capture_cookies(args.provider, args.email)
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)

    if not cookies:
        print("ERROR: zero cookies captured — did you actually sign in?", file=sys.stderr)
        sys.exit(3)
    print_cookie_summary(cookies)

    body = json.dumps(cookies, indent=2).encode("utf-8")
    print(f"\nCookies JSON size: {len(body):,} bytes")

    metadata = {
        "provider": args.provider,
        "email": args.email,
        "created-at": datetime.now(timezone.utc).isoformat(),
        "cookie-count": str(len(cookies)),
    }

    if skip_upload:
        out_path = Path(args.output_cookies).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(body)
        print(f"\n{'=' * 70}")
        print(" COOKIES JSON WRITTEN (no upload — manual mode)")
        print(f"{'=' * 70}\n")
        print(f"Local file: {out_path}")
        print(f"  ({out_path.stat().st_size:,} bytes)\n")
        print("Upload it to R2 via the Cloudflare dashboard:")
        print(f"  1. Cloudflare → R2 → bucket: {args.bucket}")
        print(f"  2. Click 'Upload' and select the file above")
        print(f"  3. Object name / key (path inside bucket):")
        print(f"       {key}")
        print()
    else:
        print(f"\n→ Uploading to R2 (key=s3://{args.bucket}/{key})…")
        uri = upload_to_r2(
            endpoint_url=args.endpoint_url,
            access_key_id=args.access_key_id,
            secret_access_key=args.secret_access_key,
            bucket=args.bucket,
            key=key,
            body=body,
            metadata=metadata,
        )
        print(f"\n{'=' * 70}")
        print(" SUCCESS")
        print(f"{'=' * 70}\n")
        print(f"R2 object: {uri}\n")

    env_var = (
        "MEMOIR_BOT_GOOGLE_PROFILE_KEY"
        if args.provider == "google"
        else "MEMOIR_BOT_MICROSOFT_PROFILE_KEY"
    )
    print(f"On EC2, set in /etc/secrets/env/orchestrator.env:")
    print(f"  {env_var}={key}")
    print(f"  MEMOIR_BOT_AUTH_MODE=authenticated")
    print(f"  MEMOIR_BOT_INVITE_EMAIL={args.email}\n")
    print(f"Then restart orchestrator:")
    print(f"  sudo systemctl restart memoir-orchestrator")
    print()
    print(f"(BotWorker image rebuild NOT needed — auth_session.py reads")
    print(f" the cookies object dynamically. New cookies override the")
    print(f" cache via R2 ETag check on the next bot launch.)\n")


if __name__ == "__main__":
    asyncio.run(main())
