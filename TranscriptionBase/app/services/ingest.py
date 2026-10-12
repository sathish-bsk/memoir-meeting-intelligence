"""
Ingestion services: file upload, media URL download, YouTube download.
"""
import os
import asyncio
import uuid
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

import httpx
from fastapi import UploadFile

from app.core.config import settings
from app.core.security import (
    SecurityError,
    resolve_and_validate_url,
    validate_youtube_url,
    validate_file_extension,
    validate_content_type,
)
from app.core.logging import get_request_logger
from app.services.cleanup import ensure_temp_base_exists


class IngestError(Exception):
    """Raised when ingestion fails."""
    pass


def create_request_temp_dir(request_id: str) -> Path:
    """Create a unique temporary directory for a request."""
    base = ensure_temp_base_exists()
    temp_dir = base / request_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


async def ingest_upload(
    file: UploadFile,
    request_id: str,
    temp_dir: Path
) -> Path:
    """
    Save an uploaded file to the temp directory.
    Returns the path to the saved file.
    """
    logger = get_request_logger(request_id)

    if not file.filename:
        raise IngestError("No filename provided")

    # Validate extension
    if not validate_file_extension(file.filename):
        raise IngestError(
            f"Invalid file type. Allowed: {', '.join(settings.ALLOWED_MEDIA_EXTENSIONS)}"
        )

    # Validate content type
    if not validate_content_type(file.content_type):
        raise IngestError(f"Invalid content type: {file.content_type}")

    # Safe filename with UUID prefix to avoid collisions
    safe_filename = f"{uuid.uuid4().hex[:8]}_{Path(file.filename).name}"
    output_path = temp_dir / safe_filename

    logger.info(f"Saving uploaded file: {file.filename} -> {output_path}")

    # Read and write with size limit check
    total_size = 0
    try:
        with open(output_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > settings.MAX_FILE_SIZE_BYTES:
                    raise IngestError(
                        f"File too large. Maximum size: {settings.MAX_FILE_SIZE_MB}MB"
                    )
                f.write(chunk)
    except IngestError:
        if output_path.exists():
            output_path.unlink()
        raise

    logger.info(f"Saved file: {output_path} ({total_size} bytes)")
    return output_path


async def ingest_media_url(
    url: str,
    request_id: str,
    temp_dir: Path
) -> Path:
    """
    Download media from a URL with SSRF protection and size limits.
    Returns the path to the downloaded file.
    """
    logger = get_request_logger(request_id)

    # SSRF protection: validate URL and resolve DNS
    try:
        hostname, resolved_ip = resolve_and_validate_url(url)
        logger.info(f"Validated URL: {hostname} -> {resolved_ip}")
    except SecurityError as e:
        raise IngestError(f"Security check failed: {e}")

    # Extract filename from URL or generate one
    parsed = urlparse(url)
    path_name = Path(parsed.path).name if parsed.path else ""
    if not validate_file_extension(path_name):
        # Try to get extension from content-type later, use default for now
        path_name = f"media_{uuid.uuid4().hex[:8]}.mp4"

    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_{path_name}"

    logger.info(f"Downloading media from: {url}")

    total_size = 0
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.DOWNLOAD_TIMEOUT),
            follow_redirects=True,
            max_redirects=5
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()

                # Validate content type
                content_type = response.headers.get("content-type", "")
                if not validate_content_type(content_type):
                    raise IngestError(f"Invalid content type: {content_type}")

                # Check content-length if available
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > settings.MAX_FILE_SIZE_BYTES:
                    raise IngestError(
                        f"File too large ({int(content_length) // (1024*1024)}MB). "
                        f"Maximum: {settings.MAX_FILE_SIZE_MB}MB"
                    )

                with open(output_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                        total_size += len(chunk)
                        if total_size > settings.MAX_FILE_SIZE_BYTES:
                            raise IngestError(
                                f"File too large. Maximum size: {settings.MAX_FILE_SIZE_MB}MB"
                            )
                        f.write(chunk)

    except httpx.TimeoutException:
        if output_path.exists():
            output_path.unlink()
        raise IngestError(f"Download timeout after {settings.DOWNLOAD_TIMEOUT}s")
    except httpx.HTTPStatusError as e:
        if output_path.exists():
            output_path.unlink()
        raise IngestError(f"HTTP error {e.response.status_code}: {e.response.reason_phrase}")
    except IngestError:
        if output_path.exists():
            output_path.unlink()
        raise
    except Exception as e:
        if output_path.exists():
            output_path.unlink()
        raise IngestError(f"Download failed: {e}")

    logger.info(f"Downloaded: {output_path} ({total_size} bytes)")
    return output_path


async def ingest_youtube(
    url: str,
    request_id: str,
    temp_dir: Path
) -> Path:
    """
    Download audio from YouTube using yt-dlp.
    Returns the path to the downloaded audio file.
    """
    logger = get_request_logger(request_id)

    # Validate YouTube domain
    if not validate_youtube_url(url):
        raise IngestError(
            f"Invalid YouTube URL. Allowed domains: {', '.join(settings.YOUTUBE_ALLOWED_DOMAINS)}"
        )

    output_template = str(temp_dir / f"{uuid.uuid4().hex[:8]}_%(title).50s.%(ext)s")

    logger.info(f"Downloading YouTube audio: {url}")

    # Use yt-dlp with audio-only extraction
    # Added options to help avoid 403 errors from YouTube
    cmd = [
        "yt-dlp",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",  # Best quality
        "--no-playlist",
        "--no-warnings",
        "--quiet",
        "--max-filesize", f"{settings.MAX_FILE_SIZE_MB}M",
        # Options to help avoid 403 Forbidden errors
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "--referer", "https://www.youtube.com/",
        "--extractor-args", "youtube:player_client=android",
        "--no-check-certificates",
        "-o", output_template,
    ]

    # Add browser cookies if configured (helps with age-restricted/login-required videos)
    if settings.YOUTUBE_COOKIES_FROM_BROWSER:
        cmd.extend(["--cookies-from-browser", settings.YOUTUBE_COOKIES_FROM_BROWSER])

    cmd.append(url)

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=settings.YOUTUBE_TIMEOUT
            )
        except asyncio.TimeoutError:
            process.kill()
            raise IngestError(f"YouTube download timeout after {settings.YOUTUBE_TIMEOUT}s")

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise IngestError(f"yt-dlp failed: {error_msg}")

    except FileNotFoundError:
        raise IngestError("yt-dlp not installed. Please install it: pip install yt-dlp")

    # Find the downloaded file
    downloaded_files = list(temp_dir.glob("*.mp3")) + list(temp_dir.glob("*.m4a")) + list(temp_dir.glob("*.webm"))
    if not downloaded_files:
        raise IngestError("yt-dlp completed but no audio file found")

    # Return the most recently created file
    output_path = max(downloaded_files, key=lambda p: p.stat().st_mtime)
    logger.info(f"Downloaded YouTube audio: {output_path}")
    return output_path
