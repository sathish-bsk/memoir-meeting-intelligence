"""SyncClaudeAdapter — Anthropic Claude via sync SDK call.

Phase 7a: thin adapter that allows the model_router to dispatch sync prompts
to Claude. Reuses Anthropic's sync `messages.create` API. For the 3 existing
sync classes (LLMCorrector / PunctuationRefiner / MemoirExtractor), this
duplicates some of the inline httpx logic those classes have — that's
acceptable because (a) the original methods stay in place as fallback,
(b) the adapter exists so router-routed calls work uniformly across providers.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from .base import SyncProvider

logger = logging.getLogger(__name__)


class SyncClaudeAdapter(SyncProvider):
    def __init__(self, api_key: str, default_model: str, timeout: float = 60.0,
                 cache_ttl: str = "1h"):
        super().__init__()
        self.api_key = api_key
        self.default_model = default_model
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._client = None  # lazy init

    def _ensure_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:
                logger.error("SyncClaudeAdapter: anthropic SDK not installed: %s", e)
                raise
            default_headers = {}
            if self.cache_ttl == "1h":
                default_headers["anthropic-beta"] = "extended-cache-ttl-2025-04-11"
            self._client = anthropic.Anthropic(
                api_key=self.api_key,
                timeout=self.timeout,
                default_headers=default_headers or None,
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
        try:
            self._ensure_client()
        except ImportError:
            return None

        model = model_override or self.default_model
        max_tokens = max_tokens_override or 4096
        cache_control = (
            {"type": "ephemeral", "ttl": "1h"} if self.cache_ttl == "1h"
            else {"type": "ephemeral"}
        )

        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system_prompt, "cache_control": cache_control}],
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if temperature_override is not None:
            kwargs["temperature"] = temperature_override

        call_start = time.time()
        try:
            response = self._client.messages.create(**kwargs)
            duration_ms = int((time.time() - call_start) * 1000)
            usage = response.usage
            input_tokens = getattr(usage, "input_tokens", 0)
            output_tokens = getattr(usage, "output_tokens", 0)
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0

            content = response.content[0].text if response.content else ""
            parsed = None
            parse_error = None
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                parse_error = f"json_decode: {e}"

            self._last_call_stats = {
                "provider": "anthropic",
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
                "cache_creation_tokens": cache_creation,
                "cost_usd": 0.0,  # adapter-level cost calc deferred; existing classes still do it
                "duration_ms": duration_ms,
                "parse_success": parsed is not None,
                "error": parse_error,
            }
            return parsed
        except Exception as e:
            logger.warning("SyncClaudeAdapter call failed: %s", e)
            self._last_call_stats = {
                "provider": "anthropic",
                "model": model,
                "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": int((time.time() - call_start) * 1000),
                "parse_success": False, "error": str(e),
            }
            return None
