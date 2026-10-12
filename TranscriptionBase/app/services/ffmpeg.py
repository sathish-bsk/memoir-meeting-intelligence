"""
FFmpeg audio processing: normalize and extract audio to 16kHz mono WAV.
"""
import asyncio
import uuid
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_request_logger


class FFmpegError(Exception):
    """Raised when FFmpeg processing fails."""
    pass


async def normalize_audio(
    input_path: Path,
    request_id: str,
    temp_dir: Path
) -> Path:
    """
    Convert audio to 16kHz mono WAV format required by Whisper.
    Returns the path to the normalized audio file.
    """
    logger = get_request_logger(request_id)

    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_normalized.wav"

    logger.info(f"Normalizing audio: {input_path} -> {output_path}")

    cmd = [
        "ffmpeg",
        "-i", str(input_path),
        "-af", "highpass=f=80,lowpass=f=8000",  # Bandpass: speech band 80Hz-8kHz
        "-ar", "16000",      # 16kHz sample rate
        "-ac", "1",          # Mono
        "-c:a", "pcm_s16le", # 16-bit PCM
        "-y",                # Overwrite output
        "-loglevel", "error",
        str(output_path)
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=300  # 5 min timeout for ffmpeg
            )
        except asyncio.TimeoutError:
            process.kill()
            raise FFmpegError("FFmpeg timeout after 300s")

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise FFmpegError(f"FFmpeg failed: {error_msg}")

    except FileNotFoundError:
        raise FFmpegError("FFmpeg not installed. Please install it.")

    if not output_path.exists():
        raise FFmpegError("FFmpeg completed but output file not found")

    logger.info(f"Normalized audio: {output_path} ({output_path.stat().st_size} bytes)")
    return output_path
