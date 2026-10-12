"""Sync router helper — bridges adapter dispatch + owner-class stats forwarding.

The 3 sync classes (LLMCorrector / PunctuationRefiner / MemoirExtractor) have
their own _prompt_log and _last_call_stats infrastructure populated via each
class's _record_call_stats() method. When the model router dispatches to a
SyncProvider adapter, those stats land on the adapter — not on the owner —
which would silently break ai_prompt_log persistence and cost accounting.

`routed_sync_call` resolves the adapter, executes the call, then forwards the
adapter's stats back to the owner via owner._record_call_stats(...) so the
existing pipeline-level cost + prompt-log machinery sees the call.

When LANGCHAIN_RESPECT_HUB_MODEL=false or the bundle has no model field, the
helper short-circuits and delegates straight to the owner's in-class
dispatcher — no router roundtrip, identical behavior to pre-Phase-7a.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from .model_router import resolve_sync_provider

logger = logging.getLogger(__name__)


def _forward_stats_to_owner(owner, adapter_stats: dict) -> None:
    """Translate adapter stats → owner._record_call_stats kwargs and invoke
    the owner's method. Mapping: adapter uses cache_read_tokens /
    cache_creation_tokens; owner's _record_call_stats uses cache_read /
    cache_creation. All other keys line up.
    """
    if not adapter_stats:
        return
    record = getattr(owner, "_record_call_stats", None)
    if not callable(record):
        # Owner doesn't have the helper — best-effort fallback: set
        # _last_call_stats directly so at least the per-call read works.
        try:
            owner._last_call_stats = adapter_stats
        except Exception:
            pass
        return
    try:
        record(
            provider=adapter_stats.get("provider", "unknown"),
            model=adapter_stats.get("model", "unknown"),
            input_tokens=int(adapter_stats.get("input_tokens", 0) or 0),
            output_tokens=int(adapter_stats.get("output_tokens", 0) or 0),
            cache_read=int(adapter_stats.get("cache_read_tokens", 0) or 0),
            cache_creation=int(adapter_stats.get("cache_creation_tokens", 0) or 0),
            cost_usd=float(adapter_stats.get("cost_usd", 0.0) or 0.0),
            duration_ms=int(adapter_stats.get("duration_ms", 0) or 0),
            parse_success=bool(adapter_stats.get("parse_success", False)),
            error=adapter_stats.get("error"),
        )
    except Exception as e:
        logger.warning("Stats forward to %s failed: %s", type(owner).__name__, e)
        # Last-resort fallback so at least _last_call_stats is set
        try:
            owner._last_call_stats = adapter_stats
        except Exception:
            pass


def routed_sync_call(
    owner,
    default_dispatcher: Callable[[str, str], Optional[dict]],
    bundle,
    system_prompt: str,
    user_prompt: str,
    env_default_key: Optional[str] = None,
) -> Optional[dict]:
    """Owner-aware sync dispatch.

    Args:
        owner: instance whose _record_call_stats + _prompt_log should receive
               adapter stats when an adapter is used.
        default_dispatcher: callable(system, user) -> dict|None. Called when
               routing isn't active or the router returns no adapter. Typically
               a lambda over owner._call_llm(...).
        bundle: PromptBundle from get_prompt_sync(...).
        system_prompt: usually bundle.system, passed explicitly so the caller
               has the option of feeding owner-specific text.
        user_prompt: same as above.
        env_default_key: provider key for fallback; defaults to owner._provider.

    Returns whatever the underlying call returned (provider-parsed JSON dict
    or None on failure).
    """
    from app.audio_preprocess.config import preprocess_config as cfg

    # Default path — no cross-provider routing requested → owner's dispatcher.
    if not cfg.LANGCHAIN_RESPECT_HUB_MODEL or not bundle.model:
        return default_dispatcher(system_prompt, user_prompt)

    env_default = env_default_key or getattr(owner, "_provider", "ollama")
    adapter, overrides = resolve_sync_provider(bundle, env_default_key=env_default)
    if adapter is None:
        return default_dispatcher(system_prompt, user_prompt)

    result = adapter.call(system_prompt, user_prompt, **overrides.as_kwargs())

    # Forward adapter stats back to the owner so existing _prompt_log + cost
    # paths see this call.
    try:
        adapter_stats = adapter.get_last_call_stats()
        if adapter_stats is not None:
            _forward_stats_to_owner(owner, adapter_stats)
    except Exception as e:
        logger.debug("Adapter stats fetch failed (non-fatal): %s", e)

    return result
