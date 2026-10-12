"""
ASR service using Whisper + Aksharamukha transliteration.
2-step pipeline: Whisper (Tamil auto-detect → Tamil script) → Aksharamukha (Romanization)
Optimized for Tamil + English code-mixed speech with romanized output.
"""
import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import asyncio
import threading
import re
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.logging import get_request_logger


class AI4BharatASRError(Exception):
    """Raised when AI4Bharat ASR processing fails."""
    pass


class AI4BharatASRService:
    """
    2-step pipeline:
    1. Whisper (existing model): Speech → Tamil script (auto-detect)
    2. Aksharamukha: Tamil script → Romanized text

    Reuses the existing Whisper model, no extra model download needed.
    """
    _instance: Optional["AI4BharatASRService"] = None
    _lock = threading.Lock()
    _model = None
    _model_loaded = False
    _model_lock = threading.Lock()

    def __new__(cls) -> "AI4BharatASRService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def _load_model(self):
        """Load the Whisper model (reuses existing model config)."""
        if self._model_loaded:
            return

        with self._model_lock:
            if self._model_loaded:
                return

            import logging
            logger = logging.getLogger("transcription")
            import whisper

            model_size = settings.ASR_MODEL_SIZE
            device = settings.ASR_DEVICE

            logger.info(f"Loading Whisper model for AI4Bharat pipeline: {model_size} on {device}")
            self._model = whisper.load_model(model_size, device=device)
            self._model_loaded = True
            logger.info("Whisper model loaded for AI4Bharat pipeline")

    def _transliterate_tamil(self, text: str, request_id: str) -> str:
        """
        Transliterate Tamil script to romanized text using Aksharamukha.
        Preserves English words, numbers, and punctuation.
        """
        from aksharamukha import transliterate as akshar_transliterate

        logger = get_request_logger(request_id)

        if not text.strip():
            return text

        result_parts = []
        words = text.split()

        for word in words:
            has_tamil = any('\u0B80' <= char <= '\u0BFF' for char in word)

            if has_tamil:
                try:
                    roman_word = akshar_transliterate.process('Tamil', 'ISO', word)

                    # Replace diacritics with ASCII-friendly equivalents
                    diacritic_map = {
                        'ā': 'aa', 'ī': 'ee', 'ū': 'oo', 'ē': 'e', 'ō': 'o',
                        'ṇ': 'n', 'ḷ': 'l', 'ṟ': 'r', 'ṉ': 'n', 'ḻ': 'zh',
                        'ṅ': 'ng', 'ñ': 'ny', 'ṭ': 't', 'ḍ': 'd',
                        'ṃ': 'm', 'ḥ': 'h',
                        'Ā': 'Aa', 'Ī': 'Ee', 'Ū': 'Oo', 'Ē': 'E', 'Ō': 'O',
                        'Ṇ': 'N', 'Ḷ': 'L', 'Ṟ': 'R', 'Ṉ': 'N', 'Ḻ': 'Zh',
                    }
                    for diacritic, replacement in diacritic_map.items():
                        roman_word = roman_word.replace(diacritic, replacement)

                    result_parts.append(roman_word)
                except Exception as e:
                    logger.warning(f"Transliteration failed for '{word}': {e}")
                    result_parts.append(word)
            else:
                result_parts.append(word)

        return " ".join(result_parts)

    async def transcribe(
        self,
        audio_path: Path,
        request_id: str,
        language: Optional[str] = None
    ) -> str:
        """
        Transcribe audio using 2-step pipeline:
        Step 1: Whisper transcription (auto-detect → Tamil script)
        Step 2: Aksharamukha transliteration (Tamil script → Roman)
        """
        logger = get_request_logger(request_id)
        logger.info(f"Starting AI4Bharat 2-step transcription: {audio_path}")

        self._load_model()

        loop = asyncio.get_event_loop()

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._transcribe_sync, audio_path, request_id),
                timeout=settings.ASR_TIMEOUT
            )
            return result
        except asyncio.TimeoutError:
            raise AI4BharatASRError(f"Transcription timeout after {settings.ASR_TIMEOUT}s")

    def _transcribe_sync(self, audio_path: Path, request_id: str) -> str:
        """Synchronous 2-step transcription."""
        logger = get_request_logger(request_id)

        try:
            # Step 1: Transcribe to Tamil script (let Whisper auto-detect)
            logger.info("Step 1: Whisper transcription (auto-detect → Tamil script)...")

            result = self._model.transcribe(
                str(audio_path),
                task="transcribe",
                verbose=False,
            )

            tamil_text = result.get("text", "").strip()
            detected_lang = result.get("language", "unknown")
            logger.info(f"Detected language: {detected_lang}")
            logger.info(f"Tamil script output: {tamil_text[:200]}...")

            # Step 2: Transliterate Tamil portions to Roman
            logger.info("Step 2: Transliterating Tamil → Roman...")

            romanized_text = self._transliterate_tamil(tamil_text, request_id)
            romanized_text = re.sub(r'\s+', ' ', romanized_text).strip()

            logger.info(f"Romanized output: {romanized_text[:200]}...")
            logger.info(f"AI4Bharat transcription complete: {len(romanized_text)} characters")

            return romanized_text

        except Exception as e:
            raise AI4BharatASRError(f"AI4Bharat transcription failed: {e}")


# Global instance
ai4bharat_asr_service = AI4BharatASRService()


async def transcribe_audio_ai4bharat(audio_path: Path, request_id: str, language: Optional[str] = None) -> str:
    """Convenience function to transcribe audio using AI4Bharat pipeline."""
    return await ai4bharat_asr_service.transcribe(audio_path, request_id, language)
