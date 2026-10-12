"""
ASR service using Pingala V1 Universal (ShunyaLabs).
Supports 204 languages including Tamil and other Indic languages.
Uses direct transformers inference for better compatibility.
"""
import os
# Fix OpenMP library conflict on macOS (must be set before importing torch/numpy)
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import asyncio
import threading
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.logging import get_request_logger


class PingalaASRError(Exception):
    """Raised when Pingala ASR processing fails."""
    pass


class PingalaASRService:
    """
    Singleton Pingala ASR service with thread-safe model loading.
    Model is loaded once at startup and reused for all requests.
    Uses direct transformers inference instead of pingala-shunya for compatibility.
    """
    _instance: Optional["PingalaASRService"] = None
    _lock = threading.Lock()
    _model = None
    _processor = None
    _model_loaded = False
    _model_lock = threading.Lock()

    def __new__(cls) -> "PingalaASRService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def _load_model(self):
        """Load the Pingala model (called once, protected by lock)."""
        if self._model_loaded:
            return

        with self._model_lock:
            if self._model_loaded:
                return

            import logging
            logger = logging.getLogger("transcription")

            # Check for Hugging Face token
            if not settings.HUGGINGFACE_HUB_TOKEN:
                raise PingalaASRError(
                    "HUGGINGFACE_HUB_TOKEN environment variable is required for Pingala V1. "
                    "Please set it with your Hugging Face access token."
                )

            # Login to Hugging Face
            logger.info("Authenticating with Hugging Face...")
            from huggingface_hub import login
            login(token=settings.HUGGINGFACE_HUB_TOKEN)

            # Import and initialize model using transformers directly
            logger.info(f"Loading Pingala model: {settings.PINGALA_MODEL_NAME}")

            import torch
            from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

            # Determine device and dtype
            device = settings.ASR_DEVICE
            torch_dtype = torch.float16 if device == "cuda" else torch.float32

            # Load model
            self._model = AutoModelForSpeechSeq2Seq.from_pretrained(
                settings.PINGALA_MODEL_NAME,
                torch_dtype=torch_dtype,
                low_cpu_mem_usage=True,
            )
            self._model.to(device)

            # Load processor
            self._processor = AutoProcessor.from_pretrained(settings.PINGALA_MODEL_NAME)

            self._model_loaded = True
            logger.info("Pingala model loaded successfully")

    async def transcribe(
        self,
        audio_path: Path,
        request_id: str,
        language: Optional[str] = None
    ) -> str:
        """
        Transcribe audio file to plain text using Pingala V1.
        Returns the full transcription as a string.

        Args:
            audio_path: Path to the audio file
            request_id: Unique request ID for logging
            language: Language code (e.g., "en", "ta" for Tamil). None for auto-detect.
        """
        logger = get_request_logger(request_id)
        logger.info(f"Starting Pingala transcription: {audio_path}, language: {language or 'auto-detect'}")

        # Ensure model is loaded
        self._load_model()

        # Run transcription in thread pool to not block event loop
        loop = asyncio.get_event_loop()

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._transcribe_sync, audio_path, request_id, language),
                timeout=settings.ASR_TIMEOUT
            )
            return result
        except asyncio.TimeoutError:
            raise PingalaASRError(f"Transcription timeout after {settings.ASR_TIMEOUT}s")

    def _transcribe_sync(self, audio_path: Path, request_id: str, language: Optional[str] = None) -> str:
        """Synchronous transcription (runs in thread pool)."""
        logger = get_request_logger(request_id)

        try:
            import torch
            import librosa

            logger.info(f"Transcribing with Pingala, language: {language or 'auto'}")

            # Load audio file
            logger.info(f"Loading audio file: {audio_path}")
            audio, sample_rate = librosa.load(str(audio_path), sr=16000)

            # Process audio
            inputs = self._processor(audio, sampling_rate=16000, return_tensors="pt")

            device = settings.ASR_DEVICE
            inputs = inputs.to(device)

            # Build generation config
            # max_new_tokens must be less than 448 - decoder_input_ids length (typically 4)
            generate_kwargs = {
                "max_new_tokens": 440,
            }

            # Set language if specified (for Whisper-based models)
            if language:
                generate_kwargs["language"] = language
                generate_kwargs["task"] = "transcribe"

            # Generate transcription
            logger.info("Generating transcription...")
            with torch.no_grad():
                generated_ids = self._model.generate(
                    inputs["input_features"],
                    **generate_kwargs
                )

            # Decode
            transcription = self._processor.batch_decode(generated_ids, skip_special_tokens=True)
            full_text = transcription[0] if transcription else ""

            # Clean up extra whitespace
            import re
            full_text = re.sub(r'\s+', ' ', full_text).strip()

            logger.info(f"Pingala transcription complete: {len(full_text)} characters")
            return full_text

        except Exception as e:
            raise PingalaASRError(f"Pingala transcription failed: {e}")


# Global Pingala ASR service instance
pingala_asr_service = PingalaASRService()


async def transcribe_audio_pingala(audio_path: Path, request_id: str, language: Optional[str] = None) -> str:
    """Convenience function to transcribe audio using Pingala."""
    return await pingala_asr_service.transcribe(audio_path, request_id, language)
