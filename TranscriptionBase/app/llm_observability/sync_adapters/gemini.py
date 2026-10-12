"""SyncGeminiAdapter — Google Gemini via sync google-genai SDK.

Phase 9 — closes the sync-surface provider gap (plan §9.6). Enables Hub-driven
routing of LLMCorrector / PunctuationRefiner / MemoirExtractor prompts to
Gemini.

Note: Gemini's SDK has both sync and async clients. We use the sync one here
since this adapter is invoked from sync class call sites.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from .base import SyncProvider

logger = logging.getLogger(__name__)


class SyncGeminiAdapter(SyncProvider):
    def __init__(self, api_key: str, default_model: str, timeout: float = 60.0):
        super().__init__()
        self.api_key = api_key
        self.default_model = default_model
        self.timeout = timeout
        self._client = None  # lazy init

    def _ensure_client(self):
        if self._client is None:
            try:
                from google import genai
            except ImportError as e:
                logger.error("SyncGeminiAdapter: google-genai not installed: %s", e)
                raise
            self._client = genai.Client(api_key=self.api_key)

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model_override: Optional[str] = None,
        temperature_override: Optional[float] = None,
        max_tokens_override: Optional[int] = None,
        max_retries: int = 2,
    ) -> Optional[dict]:
        try:
            self._ensure_client()
        except ImportError:
            return None

        from google.genai import types

        model = model_override or self.default_model

        # Gemini uses systemInstruction separately from contents
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
        )
        if temperature_override is not None:
            config.temperature = temperature_override
        if max_tokens_override is not None:
            config.max_output_tokens = max_tokens_override

        call_start = time.time()
        try:
            response = self._client.models.generate_content(
                model=model,
                contents=user_prompt,
                config=config,
            )
            duration_ms = int((time.time() - call_start) * 1000)
            content = getattr(response, "text", "") or ""
            usage = getattr(response, "usage_metadata", None)
            input_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
            output_tokens = getattr(usage, "candidates_token_count", 0) if usage else 0

            parsed = None
            parse_error = None
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                parse_error = f"json_decode: {e}"

            self._last_call_stats = {
                "provider": "gemini", "model": model,
                "input_tokens": int(input_tokens or 0),
                "output_tokens": int(output_tokens or 0),
                "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "parse_success": parsed is not None,
                "error": parse_error,
            }
            return parsed
        except Exception as e:
            logger.warning("SyncGeminiAdapter call failed: %s", e)
            self._last_call_stats = {
                "provider": "gemini", "model": model,
                "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": int((time.time() - call_start) * 1000),
                "parse_success": False, "error": str(e),
            }
            return None
