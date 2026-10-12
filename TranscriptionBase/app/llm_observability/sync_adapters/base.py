"""Sync provider adapter base class.

Provides a uniform .call(system, user, **overrides) surface so the model_router
can dispatch sync prompts (LLMCorrector / PunctuationRefiner / MemoirExtractor)
to any configured backend.

Each concrete adapter wraps an httpx.Client. The existing _call_ollama /
_call_claude / _call_vllm methods inlined in the 3 sync classes stay in place
for backwards compat; this adapter layer provides the cross-routing surface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class SyncProvider(ABC):
    """Uniform sync-LLM interface used by the model router.

    Provider implementations must populate self._last_call_stats after each
    call so cost tracking continues to work (mirrors IntelligenceProvider's
    contract for the async side).
    """

    def __init__(self):
        self._last_call_stats: Optional[dict] = None

    @abstractmethod
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
        """Send a single chat-completion request. Returns parsed JSON dict
        or None on failure. Implementations should set self._last_call_stats
        before returning so existing cost-aggregation code keeps working.
        """
        ...

    def get_last_call_stats(self) -> Optional[dict]:
        return self._last_call_stats
