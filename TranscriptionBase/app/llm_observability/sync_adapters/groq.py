"""SyncGroqAdapter — Groq via sync groq SDK.

Phase 9 — closes the sync-surface provider gap (plan §9.6). Enables Hub-driven
routing of LLMCorrector / PunctuationRefiner / MemoirExtractor prompts to Groq's
Llama-hosted endpoints.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from .base import SyncProvider

logger = logging.getLogger(__name__)


class SyncGroqAdapter(SyncProvider):
    def __init__(self, api_key: str, default_model: str, timeout: float = 60.0):
        super().__init__()
        self.api_key = api_key
        self.default_model = default_model
        self.timeout = timeout
        self._client = None  # lazy init

    def _ensure_client(self):
        if self._client is None:
            try:
                import groq
            except ImportError as e:
                logger.error("SyncGroqAdapter: groq not installed: %s", e)
                raise
            self._client = groq.Groq(api_key=self.api_key, timeout=self.timeout)

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

        model = model_override or self.default_model

        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        if temperature_override is not None:
            kwargs["temperature"] = temperature_override
        if max_tokens_override is not None:
            kwargs["max_tokens"] = max_tokens_override

        call_start = time.time()
        try:
            response = self._client.chat.completions.create(**kwargs)
            duration_ms = int((time.time() - call_start) * 1000)
            content = response.choices[0].message.content if response.choices else ""
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
            output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0

            parsed = None
            parse_error = None
            try:
                parsed = json.loads(content or "")
            except json.JSONDecodeError as e:
                parse_error = f"json_decode: {e}"

            self._last_call_stats = {
                "provider": "groq", "model": model,
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
            logger.warning("SyncGroqAdapter call failed: %s", e)
            self._last_call_stats = {
                "provider": "groq", "model": model,
                "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": int((time.time() - call_start) * 1000),
                "parse_success": False, "error": str(e),
            }
            return None
