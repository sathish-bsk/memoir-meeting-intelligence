"""Tracing decorators — wrap LLM call sites with LangSmith @traceable when enabled.

Public surface (stable across phases):
    @traceable_llm(default_name)       # per-LLM-call span (run_type="llm")
    @traceable_chain(name)             # pipeline-stage parent (run_type="chain")
    current_prompt_name                 # ContextVar — set by runner functions
                                       # before invoking provider.call() so the
                                       # trace name reflects which Hub prompt fired

Granularity contract (see plan §8): decorators are applied to actual provider
HTTP-call methods (IntelligenceProvider.call, LLMCorrector._call_llm, etc.) NOT
to orchestrator methods. One span per actual LLM request — matches existing
_collect_prompt_log() row-per-call semantics.
"""

from __future__ import annotations

import functools
import logging
import os
from contextvars import ContextVar
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# Only these prompts emit LangSmith traces. The 7 prompts published to
# LangSmith Hub at memoir-ai/<name>. Pattern-B prompts (memoir-corrector,
# memoir-punctuation, memoir-p5-reduce) are NOT in Hub yet, so tracing them
# burns the 5k/month free-tier quota without enabling the Hub iteration
# workflow (cofounder can't edit them from Hub UI yet). Skip until they're
# migrated.
#
# Note: memoir-p5-summary covers BOTH the single-pass call AND the chunked
# map-reduce path (both invoke with prompt_name_context("memoir-p5-summary")),
# so chunked p5 chunks ARE traced — that's intentional, the Hub template is
# the same.
HUB_MIGRATED_PROMPTS = frozenset({
    "memoir-classification",
    "memoir-crm-extraction",
    "memoir-intelligence",
    "memoir-pmf",
    "memoir-p5-summary",
    "memoir-faq",
    "memoir-extractor",
})


# Set by intelligence-pipeline runner functions before invoking provider.call().
# Read by the traceable_llm wrapper to compute the trace name per call. When
# None the decorator's default_name is used. ContextVar means concurrent calls
# (asyncio.gather over chunks etc.) keep their own values.
current_prompt_name: ContextVar[Optional[str]] = ContextVar(
    "current_prompt_name", default=None
)


def _langsmith_enabled() -> bool:
    """Lazy check so import order is irrelevant + env reloads honored."""
    try:
        from app.audio_preprocess.config import preprocess_config as cfg
        return bool(cfg.LANGSMITH_ENABLED)
    except Exception:
        return False


def _ensure_langsmith_env() -> None:
    """Propagate cfg → os.environ so the langsmith SDK picks up our values."""
    try:
        from app.audio_preprocess.config import preprocess_config as cfg
        # langsmith SDK reads these directly from os.environ
        if cfg.LANGSMITH_API_KEY:
            os.environ.setdefault("LANGSMITH_API_KEY", cfg.LANGSMITH_API_KEY)
            # Backwards-compat: older SDK versions look at LANGCHAIN_API_KEY
            os.environ.setdefault("LANGCHAIN_API_KEY", cfg.LANGSMITH_API_KEY)
        if cfg.LANGSMITH_PROJECT:
            os.environ.setdefault("LANGSMITH_PROJECT", cfg.LANGSMITH_PROJECT)
            os.environ.setdefault("LANGCHAIN_PROJECT", cfg.LANGSMITH_PROJECT)
        # Tracing flag the SDK reads
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    except Exception as e:
        logger.warning("LangSmith env setup failed: %s", e)


def _traceable_or_identity(default_name: str, run_type: str) -> Callable[[Callable], Callable]:
    """Return langsmith.traceable decorator, or an identity decorator when
    tracing is disabled / langsmith not installed. The check runs at CALL time
    (inside the wrapper), so a runtime env flip can enable/disable without
    re-importing the module.
    """

    def decorator(fn: Callable) -> Callable:
        # Cache per (enabled, resolved_name) so we don't rebuild the traceable
        # wrapper on every call when the same prompt_name is in play. Key by
        # resolved name so each Hub prompt gets its own LangSmith trace name.
        # When enabled=False the inner fn is cached under None.
        _wrapped_cache: dict[tuple, Callable] = {}

        def _resolve_target() -> Callable:
            enabled = _langsmith_enabled()
            if not enabled:
                key = (False, None)
                if key not in _wrapped_cache:
                    _wrapped_cache[key] = fn
                return _wrapped_cache[key]
            # Resolve trace name at CALL time. LangSmith's traceable validates
            # name as str (pydantic RunTree.name: str), so a lambda passed to
            # `name=` blows up with: "Input should be a valid string" — was
            # observed on May 17 2026 during the Michigan test run, with EVERY
            # traced call falling through to graceful fallback.
            name = current_prompt_name.get() or default_name
            # Allowlist gate: only emit traces for prompts actually in Hub.
            # Pattern-B prompts (memoir-corrector, memoir-punctuation,
            # memoir-p5-reduce) would otherwise burn ~14 of the ~22 traces
            # we'd post per meeting against the 5k/month free tier without
            # offering the Hub iteration workflow.
            if name not in HUB_MIGRATED_PROMPTS:
                key = (False, None)
                if key not in _wrapped_cache:
                    _wrapped_cache[key] = fn
                return _wrapped_cache[key]
            key = (True, name)
            cached = _wrapped_cache.get(key)
            if cached is not None:
                return cached
            try:
                from langsmith import traceable as _ls_traceable
            except ImportError:
                logger.warning(
                    "LANGSMITH_ENABLED=true but langsmith package not installed. "
                    "Run `pip install langsmith` on this service."
                )
                _wrapped_cache[(True, None)] = fn
                return fn
            _ensure_langsmith_env()
            wrapped = _ls_traceable(
                name=name,
                run_type=run_type,
            )(fn)
            _wrapped_cache[key] = wrapped
            return wrapped

        # Need to handle both sync and async functions transparently.
        import asyncio
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                target = _resolve_target()
                return await target(*args, **kwargs)
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                target = _resolve_target()
                return target(*args, **kwargs)
            return sync_wrapper

    return decorator


def traceable_llm(default_name: str) -> Callable[[Callable], Callable]:
    """LLM-level trace span (run_type="llm"). Applied to provider dispatch methods.

    When current_prompt_name ContextVar is set (by intelligence-pipeline runner
    functions), the trace gets that name instead of default_name. This is how
    a single decoration on IntelligenceProvider.call() can produce per-prompt
    trace names like "memoir-classification", "memoir-p5-summary", etc.
    """
    return _traceable_or_identity(default_name, "llm")


def traceable_chain(name: str) -> Callable[[Callable], Callable]:
    """Pipeline-stage trace (run_type="chain"). Groups multiple LLM call spans
    under one parent — for `_run_asr_postprocess` / `_run_asr_p5_fast` etc.
    The wrapped function may contain non-LLM work (R2 I/O, ASR, alignment, etc.);
    run_type="chain" labels the span as a compositional grouping, not as a
    pure LLM call. See plan §8.
    """
    return _traceable_or_identity(name, "chain")


# ---- ContextVar helper for runner functions ----

class _PromptNameContext:
    """Context manager for setting current_prompt_name. Use:

        with prompt_name_context("memoir-classification"):
            result = await provider.call(...)
    """
    def __init__(self, name: str):
        self.name = name
        self.token = None

    def __enter__(self):
        self.token = current_prompt_name.set(self.name)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.token is not None:
            current_prompt_name.reset(self.token)
        return False


def prompt_name_context(name: str) -> _PromptNameContext:
    """Convenience: ergonomic ContextVar setter for runner functions."""
    return _PromptNameContext(name)
