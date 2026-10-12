"""SyncOllamaAdapter — Ollama via sync httpx call.

Phase 7a: enables router-driven dispatch of sync prompts to local Ollama.
Mirrors the in-class _call_ollama logic in PunctuationRefiner / MemoirExtractor /
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


class SyncOllamaAdapter(SyncProvider):
    def __init__(self, base_url: str, default_model: str, timeout: float = 600.0):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.timeout = timeout
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=10.0, read=timeout, write=30.0, pool=10.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )

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
        model = model_override or self.default_model
        options = {"temperature": temperature_override if temperature_override is not None else 0.0}
        if max_tokens_override is not None:
            options["num_predict"] = max_tokens_override

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "format": "json",
            "stream": False,
            "options": options,
        }

        call_start = time.time()
        try:
            resp = self._client.post("/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("message", {}).get("content", "")
            input_tokens = int(data.get("prompt_eval_count", 0) or 0)
            output_tokens = int(data.get("eval_count", 0) or 0)
            duration_ms = int((time.time() - call_start) * 1000)
            parsed = None
            parse_error = None
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                parse_error = f"json_decode: {e}"
            self._last_call_stats = {
                "provider": "ollama",
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "parse_success": parsed is not None,
                "error": parse_error,
            }
            return parsed
        except httpx.HTTPError as e:
            logger.warning("SyncOllamaAdapter call failed: %s", e)
            self._last_call_stats = {
                "provider": "ollama", "model": model,
                "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": int((time.time() - call_start) * 1000),
                "parse_success": False, "error": str(e),
            }
            return None
