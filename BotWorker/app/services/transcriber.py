"""
Post-meeting transcription: merge chunks and call TranscriptionBase.
"""
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.logging import get_job_logger
from app.services.storage import ChunkStorage


class TranscriptionError(Exception):
    """Error during transcription."""
    pass


class MeetingTranscriber:
    """Merges audio chunks and sends to TranscriptionBase for transcription."""

    def __init__(self, job_id: str, storage: ChunkStorage):
        self.job_id = job_id
        self.storage = storage
        self.logger = get_job_logger(job_id)

    async def transcribe(self) -> tuple[str, dict]:
        """
        Merge all chunks into one file and POST to TranscriptionBase.
        Returns (transcript_text, full_response_json).
        """
        # Merge chunks
        self.logger.info("Merging audio chunks...")
        merged_path = await self.storage.get_merged_audio(self.job_id)
        self.logger.info(f"Merged audio: {merged_path} ({merged_path.stat().st_size} bytes)")

        # POST to TranscriptionBase
        self.logger.info(f"Sending to TranscriptionBase at {settings.TRANSCRIPTION_URL}")
        transcript, full_result = await self._call_transcription_api(merged_path)
        self.logger.info(f"Transcription complete ({len(transcript)} chars)")
        return transcript, full_result

    async def _call_transcription_api(self, audio_path: Path) -> tuple[str, dict]:
        """POST the merged audio file to TranscriptionBase /audio-preprocess/process endpoint."""
        url = f"{settings.TRANSCRIPTION_URL}/audio-preprocess/process"

        data = {
            "processing_mode": "path_b",
            "language": "auto",
            "engine": "faster-whisper",
            "model_size": "medium",
            "multilingual": "true",
            "phone_mode": "true",
        }

        mime = "audio/wav" if audio_path.suffix == ".wav" else "audio/ogg"
        self.logger.info(
            f"Transcription request: POST {url} | "
            f"file={audio_path.name} ({audio_path.stat().st_size} bytes, {mime}) | "
            f"params={data}"
        )

        async with httpx.AsyncClient(timeout=settings.TRANSCRIPTION_TIMEOUT) as client:
            with open(audio_path, "rb") as f:
                files = {"file": (audio_path.name, f, mime)}
                try:
                    resp = await client.post(url, data=data, files=files)
                except httpx.TimeoutException:
                    raise TranscriptionError(
                        f"Transcription timed out after {settings.TRANSCRIPTION_TIMEOUT}s"
                    )
                except httpx.ConnectError:
                    raise TranscriptionError(
                        f"Could not connect to TranscriptionBase at {settings.TRANSCRIPTION_URL}"
                    )

            self.logger.info(f"Transcription response: {resp.status_code} ({len(resp.text)} bytes)")

            if resp.status_code != 200:
                self.logger.error(f"Transcription failed response: {resp.text[:500]}")
                raise TranscriptionError(
                    f"TranscriptionBase returned {resp.status_code}: {resp.text[:500]}"
                )

            result = resp.json()
            transcript = result["path_b"]["transcription"]
            self.logger.info(f"Transcript: {transcript}")
            return transcript, result
