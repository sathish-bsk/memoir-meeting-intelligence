"""
Zoom recording download service — downloads cloud recordings from Zoom.
Supports download_token (from webhook) with OAuth access_token fallback.
"""

import json
import logging
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

from app.core.config import settings

logger = logging.getLogger(__name__)


class DownloadTokenExpiredError(Exception):
    """Raised when the Zoom download_token has expired (HTTP 401)."""
    pass


def decrypt_zoom_tokens(encrypted_tokens: str) -> dict:
    """Decrypt Zoom OAuth tokens stored in zoom_connections.encrypted_tokens."""
    key = settings.ZOOM_ENCRYPTION_KEY or ""
    if not key:
        raise ValueError("ZOOM_ENCRYPTION_KEY not configured in core_api")
    fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return json.loads(fernet.decrypt(encrypted_tokens.encode()))


def _refresh_zoom_token_sync(refresh_token: str) -> dict:
    """Synchronously refresh a Zoom OAuth token. Used as a last-resort fallback."""
    import httpx as _httpx
    resp = _httpx.post(
        "https://zoom.us/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        auth=(settings.ZOOM_CLIENT_ID, settings.ZOOM_CLIENT_SECRET),
    )
    resp.raise_for_status()
    return resp.json()


async def download_zoom_recording(
    download_url: str,
    bearer_token: str,
    meeting_id: str,
    file_extension: str = ".m4a",
) -> Path:
    """Download Zoom recording file to local storage.

    Args:
        download_url: Zoom's download URL from webhook payload.
        bearer_token: Bearer token (download_token or OAuth access_token).
        meeting_id: Our internal meeting UUID for path construction.
        file_extension: File extension (default .m4a for audio_only).

    Returns:
        Path to downloaded file: {BOT_RECORDINGS_DIR}/zoom_cloud/{meeting_id}/original{ext}

    Raises:
        DownloadTokenExpiredError: If the bearer token is rejected (401).
    """
    output_dir = Path(settings.BOT_RECORDINGS_DIR) / "zoom_cloud" / meeting_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"original{file_extension}"

    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        response = await client.get(
            download_url,
            headers={"Authorization": f"Bearer {bearer_token}"},
        )

        if response.status_code == 401:
            raise DownloadTokenExpiredError(
                "Zoom bearer token expired/invalid, needs OAuth refresh"
            )

        response.raise_for_status()

        with open(output_path, "wb") as f:
            f.write(response.content)

    file_size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info(f"Downloaded Zoom recording to {output_path} ({file_size_mb:.1f} MB)")
    return output_path


async def download_with_oauth_fallback(
    download_url: str,
    download_token: str,
    encrypted_tokens: str,
    meeting_id: str,
    file_extension: str = ".m4a",
) -> Path:
    """Download with download_token, fall back to OAuth access_token if expired.

    Args:
        download_url: Zoom's download URL.
        download_token: Token from webhook payload (valid 24h).
        encrypted_tokens: Fernet-encrypted OAuth tokens from zoom_connections.
        meeting_id: Our internal meeting UUID.
        file_extension: File extension for the recording.

    Returns:
        Path to downloaded file.
    """
    # Try download_token first (simpler, no OAuth needed)
    try:
        return await download_zoom_recording(
            download_url, download_token, meeting_id, file_extension
        )
    except DownloadTokenExpiredError:
        logger.info(f"Download token expired for meeting {meeting_id}, trying OAuth fallback")

    # Fall back to OAuth access_token
    tokens = decrypt_zoom_tokens(encrypted_tokens)
    access_token = tokens.get("access_token", "")

    try:
        return await download_zoom_recording(
            download_url, access_token, meeting_id, file_extension
        )
    except DownloadTokenExpiredError:
        logger.info(f"OAuth access_token also expired for meeting {meeting_id}, refreshing")

    # Last resort: refresh the token and retry
    refresh_token = tokens.get("refresh_token", "")
    if not refresh_token:
        raise DownloadTokenExpiredError("No refresh token available")

    new_token_data = _refresh_zoom_token_sync(refresh_token)
    new_access_token = new_token_data["access_token"]

    return await download_zoom_recording(
        download_url, new_access_token, meeting_id, file_extension
    )
