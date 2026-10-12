"""
Abstract storage interface with local implementation.
Chunks are saved as they complete; after meeting ends, merge into a single audio file.
"""
import asyncio
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

from app.core.config import settings
from app.core.logging import get_job_logger


class StorageError(Exception):
    """Error during storage operations."""
    pass


class ChunkStorage(ABC):
    """Abstract base class for chunk storage backends."""

    @abstractmethod
    async def save_chunk(self, chunk_path: Path, job_id: str, chunk_index: int) -> str:
        """Save a chunk file. Returns a storage key."""
        ...

    @abstractmethod
    async def get_all_chunks(self, job_id: str) -> List[Path]:
        """Return all chunk paths for a job, sorted by index."""
        ...

    @abstractmethod
    async def get_merged_audio(self, job_id: str) -> Path:
        """Concatenate all chunks into one file via FFmpeg. Returns path to merged file."""
        ...

    @abstractmethod
    async def cleanup(self, job_id: str):
        """Remove stored chunks after transcription."""
        ...


class LocalStorage(ChunkStorage):
    """Stores chunks on the local filesystem."""

    def __init__(self):
        self.base_dir = Path(settings.STORAGE_DIR)

    def _job_dir(self, job_id: str) -> Path:
        return self.base_dir / job_id

    async def save_chunk(self, chunk_path: Path, job_id: str, chunk_index: int) -> str:
        logger = get_job_logger(job_id)
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)

        dest = job_dir / f"chunk_{chunk_index:04d}.wav"
        shutil.copy2(str(chunk_path), str(dest))

        storage_key = str(dest.relative_to(self.base_dir))
        logger.info(f"Saved chunk {chunk_index} -> {dest} ({dest.stat().st_size} bytes)")
        return storage_key

    async def get_all_chunks(self, job_id: str) -> List[Path]:
        job_dir = self._job_dir(job_id)
        if not job_dir.exists():
            return []
        chunks = sorted(job_dir.glob("chunk_*.wav"))
        return chunks

    async def get_merged_audio(self, job_id: str) -> Path:
        logger = get_job_logger(job_id)
        job_dir = self._job_dir(job_id)
        chunks = await self.get_all_chunks(job_id)

        if not chunks:
            raise StorageError(f"No chunks found for job {job_id}")

        # Create FFmpeg concat file list (even for single chunk, we need
        # to run through ffmpeg to trim leading silence)
        filelist = job_dir / "filelist.txt"
        with open(filelist, "w") as f:
            for chunk in chunks:
                f.write(f"file '{chunk}'\n")

        # Tier 1.5 — opt-in FLAC output to halve R2 upload time. FLAC is
        # lossless (bit-perfect reconstruction of pcm_s16le, no ASR-quality
        # regression; ~50% compression ratio on speech).
        #
        # Default is WAV for compatibility — Type 1 in TranscriptionBase
        # currently copies the input bytes verbatim to a path named
        # `meetings/{id}/recording.wav` (see processor.py around line 227),
        # which would contain FLAC bytes if we flipped this default. FFmpeg
        # consumers handle the mismatch fine, but the frontend audio
        # playback path may not. Set BOTWORKER_AUDIO_FORMAT=flac to enable
        # once Type 1's recording.wav writer is updated to honor the input
        # extension AND the frontend has been verified.
        audio_format = os.getenv("BOTWORKER_AUDIO_FORMAT", "wav").lower()
        if audio_format not in ("flac", "wav"):
            logger.warning(f"Unknown BOTWORKER_AUDIO_FORMAT={audio_format!r}, defaulting to wav")
            audio_format = "wav"

        merged_path = job_dir / f"merged.{audio_format}"

        # Trim only LEADING dead air (before bot is admitted). Matches the
        # May 5 baseline behavior. Do NOT add stop_periods — trailing trim
        # with a fixed dB threshold is unsafe on uniformly quiet audio (a
        # 124s recording got chopped to 1.26s on 2026-05-07). Tail trimming,
        # if needed, belongs in TranscriptionBase where VAD-aware logic can
        # decide based on speech presence, not amplitude.
        #
        # Mono output: Chrome's bot_sink delivers a downmixed stream that
        # we record stereo, but L-R diff measures at -93 dBFS = bit-exact
        # dual-mono. -ac 1 halves R2 storage at zero quality cost.
        #
        # Triangular HP dither makes the f32→s16 quantization deterministic
        # across FFmpeg versions rather than relying on swresample defaults
        # — relevant in WAV mode; FLAC is lossless so dither is moot, but
        # we keep the flag for parity with the legacy WAV path.
        codec_args = (
            ["-c:a", "flac", "-compression_level", "8"]
            if audio_format == "flac"
            else ["-c:a", "pcm_s16le"]
        )
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(filelist),
            "-af", "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB",
            "-dither_method", "triangular_hp",
            *codec_args,
            "-ar", "48000",
            "-ac", "1",
            str(merged_path),
        ]

        logger.info(f"Merging {len(chunks)} chunks -> {merged_path}")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace")
            raise StorageError(f"FFmpeg merge failed: {error_msg}")

        logger.info(f"Merged audio: {merged_path} ({merged_path.stat().st_size} bytes)")
        return merged_path

    async def cleanup(self, job_id: str):
        logger = get_job_logger(job_id)
        job_dir = self._job_dir(job_id)
        if job_dir.exists():
            shutil.rmtree(str(job_dir))
            logger.info(f"Cleaned up storage for job {job_id}")


class S3Storage(ChunkStorage):
    """
    S3-based storage for production use.
    Placeholder — uses presigned URLs from the backend.
    """

    async def save_chunk(self, chunk_path: Path, job_id: str, chunk_index: int) -> str:
        # TODO: Fetch presigned URL from backend, PUT to S3
        raise NotImplementedError("S3Storage not yet implemented")

    async def get_all_chunks(self, job_id: str) -> List[Path]:
        raise NotImplementedError("S3Storage not yet implemented")

    async def get_merged_audio(self, job_id: str) -> Path:
        raise NotImplementedError("S3Storage not yet implemented")

    async def cleanup(self, job_id: str):
        raise NotImplementedError("S3Storage not yet implemented")


def create_storage() -> ChunkStorage:
    """Factory function to create the configured storage backend.

    BotWorker always uses local storage — chunks are saved inside the
    Docker container's filesystem. The orchestrator (Docker host) handles
    uploading the merged recording to R2 after the meeting ends.
    """
    return LocalStorage()
