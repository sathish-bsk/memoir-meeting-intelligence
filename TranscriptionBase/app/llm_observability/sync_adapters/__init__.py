"""Sync provider adapters — for LLMCorrector / PunctuationRefiner / MemoirExtractor.

Each adapter exposes a uniform .call(system, user, **overrides) -> dict | None
surface so model_router.resolve_sync_provider() can dispatch sync prompts to
any configured backend.

Phase 7a: Claude, Ollama, vLLM
Phase 9: Gemini, Groq (closes the sync-surface gap from plan §9.6)
"""

from .base import SyncProvider
from .claude import SyncClaudeAdapter
from .ollama import SyncOllamaAdapter
from .vllm import SyncVLLMAdapter
from .gemini import SyncGeminiAdapter
from .groq import SyncGroqAdapter

__all__ = [
    "SyncProvider",
    "SyncClaudeAdapter",
    "SyncOllamaAdapter",
    "SyncVLLMAdapter",
    "SyncGeminiAdapter",
    "SyncGroqAdapter",
]
