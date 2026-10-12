"""
ASR (Automatic Speech Recognition) service using official OpenAI Whisper.
Thread-safe model loading with singleton pattern.
Supports smart chunking via Silero VAD for long audio.
"""
import asyncio
import re
import threading
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import soundfile as sf

from app.core.config import settings
from app.core.logging import get_request_logger


class ASRError(Exception):
    """Raised when ASR processing fails."""
    pass


class ASRService:
    """
    Singleton ASR service with thread-safe model loading.
    Model is loaded once at startup and reused for all requests.
    """
    _instance: Optional["ASRService"] = None
    _lock = threading.Lock()
    _model = None
    _model_loaded = False
    _model_lock = threading.Lock()

    def __new__(cls) -> "ASRService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def _load_model(self):
        """Load the Whisper model (called once, protected by lock)."""
        if self._model_loaded:
            return

        with self._model_lock:
            if self._model_loaded:
                return

            import whisper

            model_size = settings.ASR_MODEL_SIZE
            device = settings.ASR_DEVICE

            # Log model loading
            import logging
            logger = logging.getLogger("transcription")
            logger.info(f"Loading Whisper model: {model_size} on {device}")

            self._model = whisper.load_model(model_size, device=device)
            self._model_loaded = True
            logger.info("Whisper model loaded successfully")

    async def transcribe(
        self,
        audio_path: Path,
        request_id: str,
        language: Optional[str] = None,
        use_chunking: bool = False
    ) -> str:
        """
        Transcribe audio file to plain text.
        Returns the full transcription as a string.

        Args:
            audio_path: Path to the audio file
            request_id: Unique request ID for logging
            language: Language code (e.g., "en" for English/romanized output, None for auto-detect)
            use_chunking: If True, use VAD-based smart chunking for long audio
        """
        logger = get_request_logger(request_id)
        logger.info(f"Starting transcription: {audio_path}, language: {language or 'auto-detect'}, chunking: {use_chunking}")

        # Ensure model is loaded
        self._load_model()

        # Run transcription in thread pool to not block event loop
        loop = asyncio.get_event_loop()

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None, self._transcribe_sync, audio_path, request_id, language, use_chunking
                ),
                timeout=settings.ASR_TIMEOUT
            )
            return result
        except asyncio.TimeoutError:
            raise ASRError(f"Transcription timeout after {settings.ASR_TIMEOUT}s")

    def _get_transcribe_options(self, language: Optional[str] = None) -> dict:
        """Build Whisper transcription options."""
        options = {
            "task": "transcribe",
            "verbose": False,
            "beam_size": 5,
            "best_of": 5,
            "temperature": 0,
            "condition_on_previous_text": True,
            "initial_prompt": (
                "This is a clear, well-structured transcription with proper punctuation, "
                "capitalization, and natural sentence breaks."
            ),
            "compression_ratio_threshold": 2.4,
            "logprob_threshold": -1.0,
            "no_speech_threshold": 0.6,
        }
        if language:
            options["language"] = language
        return options

    def _transcribe_sync(
        self, audio_path: Path, request_id: str,
        language: Optional[str] = None, use_chunking: bool = False
    ) -> str:
        """Synchronous transcription (runs in thread pool)."""
        logger = get_request_logger(request_id)

        try:
            # Check audio duration to decide chunking
            audio_data, sr = sf.read(audio_path, dtype='float32')
            duration = len(audio_data) / sr
            logger.info(f"Audio duration: {duration:.1f}s")

            if use_chunking and duration > 30.0:
                return self._transcribe_chunked(audio_data, sr, audio_path, request_id, language)

            # Standard single-pass transcription
            if language:
                logger.info(f"Using specified language: {language}")

            options = self._get_transcribe_options(language)
            result = self._model.transcribe(str(audio_path), **options)

            detected_lang = result.get("language", "unknown")
            logger.info(f"Detected/used language: {detected_lang}")

            full_text = result.get("text", "").strip()
            full_text = re.sub(r'\s+', ' ', full_text).strip()

            logger.info(f"Transcription complete: {len(full_text)} characters")
            return full_text

        except Exception as e:
            raise ASRError(f"Transcription failed: {e}")

    def _transcribe_chunked(
        self, audio_data: np.ndarray, sr: int,
        audio_path: Path, request_id: str, language: Optional[str] = None
    ) -> str:
        """Transcribe long audio using VAD-based smart chunking."""
        logger = get_request_logger(request_id)
        logger.info("Using smart chunking for long audio...")

        from app.services.audio_preprocessor import get_speech_chunks

        # Get speech-boundary chunks
        chunks = get_speech_chunks(audio_path, request_id)
        logger.info(f"Transcribing {len(chunks)} chunks...")

        if language:
            logger.info(f"Using specified language: {language}")

        options = self._get_transcribe_options(language)
        transcripts = []

        for i, (start_sample, end_sample) in enumerate(chunks):
            chunk_audio = audio_data[start_sample:end_sample]
            chunk_duration = len(chunk_audio) / sr
            logger.info(f"Transcribing chunk {i+1}/{len(chunks)}: {chunk_duration:.1f}s")

            # Write chunk to temp file for Whisper
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                sf.write(tmp.name, chunk_audio, sr, subtype='PCM_16')
                tmp_path = tmp.name

            try:
                # Use previous chunk's text as context for continuity
                if transcripts:
                    # Use last 200 chars of previous transcription as context
                    prev_text = transcripts[-1][-200:]
                    options["initial_prompt"] = prev_text

                result = self._model.transcribe(tmp_path, **options)
                chunk_text = result.get("text", "").strip()
                chunk_text = re.sub(r'\s+', ' ', chunk_text).strip()

                if chunk_text:
                    transcripts.append(chunk_text)
                    logger.info(f"Chunk {i+1}: {len(chunk_text)} chars")
                else:
                    logger.info(f"Chunk {i+1}: empty (no speech)")
            finally:
                import os
                os.unlink(tmp_path)

        full_text = " ".join(transcripts)
        full_text = re.sub(r'\s+', ' ', full_text).strip()

        logger.info(
            f"Chunked transcription complete: {len(chunks)} chunks, "
            f"{len(full_text)} characters"
        )
        return full_text


# Global ASR service instance
asr_service = ASRService()


async def transcribe_audio(
    audio_path: Path, request_id: str,
    language: Optional[str] = None, use_chunking: bool = False
) -> str:
    """Convenience function to transcribe audio."""
    return await asr_service.transcribe(audio_path, request_id, language, use_chunking)
