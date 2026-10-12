"""SyncVLLMAdapter — vLLM via sync httpx call (OpenAI-compat endpoint).

Phase 7a: enables router-driven dispatch of sync prompts to local vLLM.
Mirrors the in-class _call_vllm logic in PunctuationRefiner / MemoirExtractor /
LLMCorrector.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import httpx

from .base import SyncProvider

logger = logging.getLogger(__name__)


class SyncVLLMAdapter(SyncProvider):
    def __init__(self, base_url: str, default_model: str, timeout: float = 600.0):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.timeout = timeout

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
        # vLLM serves one model per instance, but model_override is still
        # honored — if it doesn't match what vLLM has loaded, the request
        # will error and we'll return None (router should have caught this
        # via _model_available_locally before reaching here).
        model = model_override or self.default_model

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature_override if temperature_override is not None else 0.0,
            "max_tokens": max_tokens_override or 4096,
            "response_format": {"type": "json_object"},
        }

        call_start = time.time()
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices") or []
            content = (choices[0].get("message", {}).get("content", "") or "") if choices else ""
            usage = data.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens", 0) or 0)
            output_tokens = int(usage.get("completion_tokens", 0) or 0)
            duration_ms = int((time.time() - call_start) * 1000)
            parsed = None
            parse_error = None
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                parse_error = f"json_decode: {e}"
            self._last_call_stats = {
                "provider": "vllm", "model": model,
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "parse_success": parsed is not None, "error": parse_error,
            }
            return parsed
        except httpx.HTTPError as e:
            logger.warning("SyncVLLMAdapter call failed: %s", e)
            self._last_call_stats = {
                "provider": "vllm", "model": model,
                "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": int((time.time() - call_start) * 1000),
                "parse_success": False, "error": str(e),
            }
            return None
