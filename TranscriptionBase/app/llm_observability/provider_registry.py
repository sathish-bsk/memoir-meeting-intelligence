"""Provider registry — one instance per configured LLM backend.

Built at service startup from credentials present in env. The router consults
this registry to route Hub-specified models to the right provider.

Two registries:
- _ASYNC_REGISTRY: holds IntelligenceProvider subclass instances for use by
  intelligence_pipeline.py runner functions.
- _SYNC_REGISTRY: holds SyncProvider adapter instances for use by
  LLMCorrector / PunctuationRefiner / MemoirExtractor.

Both registries are keyed by provider name: "claude", "gemini", "groq",
"ollama", "vllm".

See plan §9.3.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# Singletons populated by init_registry()
_ASYNC_REGISTRY: dict[str, object] = {}
_SYNC_REGISTRY: dict[str, object] = {}
_INITIALIZED = False


def _ollama_reachable(timeout_s: float = 1.0) -> bool:
    """Probe local Ollama with a fast HEAD on /api/tags."""
    base = os.getenv("LLM_BASE_URL", "http://localhost:11434").rstrip("/")
    try:
        # Ollama doesn't reliably support HEAD; use GET on the tags endpoint
        resp = httpx.get(f"{base}/api/tags", timeout=timeout_s)
        return resp.status_code < 500
    except Exception:
        return False


def _vllm_reachable(timeout_s: float = 1.0) -> bool:
    """Probe local vLLM with a GET on /v1/models."""
    base = os.getenv("VLLM_BASE_URL", "http://localhost:8011/v1").rstrip("/")
    try:
        resp = httpx.get(f"{base}/models", timeout=timeout_s)
        return resp.status_code < 500
    except Exception:
        return False


def init_registry() -> None:
    """Build the registries once at service startup. Idempotent.

    Async providers (IntelligenceProvider subclasses) are constructed from
    intelligence_pipeline.py's existing get_provider() factory — but we
    construct ALL configured providers, not just the env-default one.

    Sync providers are thin adapter classes from sync_adapters/.
    """
    global _INITIALIZED, _ASYNC_REGISTRY, _SYNC_REGISTRY

    if _INITIALIZED:
        return

    from app.audio_preprocess.config import preprocess_config as cfg

    # --- Async providers ---
    # Lazy import to avoid circular import at module load time
    from app.job_processor.intelligence_pipeline import (
        ClaudeProvider, GeminiProvider, GroqProvider, OllamaProvider, VLLMProvider,
    )

    if cfg.ANTHROPIC_API_KEY:
        try:
            _ASYNC_REGISTRY["claude"] = ClaudeProvider(
                api_key=cfg.ANTHROPIC_API_KEY,
                haiku_model=cfg.CLAUDE_HAIKU_MODEL,
                sonnet_model=cfg.CLAUDE_SONNET_MODEL,
                max_retries=cfg.CLAUDE_MAX_RETRIES,
                timeout=cfg.CLAUDE_TIMEOUT,
            )
            logger.info("Registry: registered ClaudeProvider")
        except Exception as e:
            logger.warning("Registry: ClaudeProvider init failed: %s", e)

    if cfg.GEMINI_API_KEY:
        try:
            _ASYNC_REGISTRY["gemini"] = GeminiProvider(
                api_key=cfg.GEMINI_API_KEY,
                model=cfg.GEMINI_MODEL,
                max_retries=cfg.GEMINI_MAX_RETRIES,
                timeout=cfg.GEMINI_TIMEOUT,
            )
            logger.info("Registry: registered GeminiProvider")
        except Exception as e:
            logger.warning("Registry: GeminiProvider init failed: %s", e)

    if cfg.GROQ_API_KEY:
        try:
            _ASYNC_REGISTRY["groq"] = GroqProvider(
                api_key=cfg.GROQ_API_KEY,
                fast_model=cfg.GROQ_FAST_MODEL,
                smart_model=cfg.GROQ_SMART_MODEL,
                max_retries=cfg.GROQ_MAX_RETRIES,
                timeout=cfg.GROQ_TIMEOUT,
            )
            logger.info("Registry: registered GroqProvider")
        except Exception as e:
            logger.warning("Registry: GroqProvider init failed: %s", e)

    if _ollama_reachable():
        try:
            _ASYNC_REGISTRY["ollama"] = OllamaProvider(
                base_url=cfg.LLM_BASE_URL if hasattr(cfg, "LLM_BASE_URL") else "http://localhost:11434",
                model=cfg.INTELLIGENCE_LLM_MODEL,
            )
            logger.info("Registry: registered OllamaProvider")
        except Exception as e:
            logger.warning("Registry: OllamaProvider init failed: %s", e)

    if _vllm_reachable():
        try:
            _ASYNC_REGISTRY["vllm"] = VLLMProvider(
                base_url=cfg.VLLM_BASE_URL if hasattr(cfg, "VLLM_BASE_URL") else "http://localhost:8011/v1",
                model=cfg.VLLM_MODEL,
            )
            logger.info("Registry: registered VLLMProvider")
        except Exception as e:
            logger.warning("Registry: VLLMProvider init failed: %s", e)

    # --- Sync providers ---
    # Sync adapters reuse the same httpx logic that LLMCorrector / Punctuation /
    # MemoirExtractor have inline today, plus Gemini + Groq from Phase 9.
    from app.llm_observability.sync_adapters.claude import SyncClaudeAdapter
    from app.llm_observability.sync_adapters.ollama import SyncOllamaAdapter
    from app.llm_observability.sync_adapters.vllm import SyncVLLMAdapter
    from app.llm_observability.sync_adapters.gemini import SyncGeminiAdapter
    from app.llm_observability.sync_adapters.groq import SyncGroqAdapter

    if cfg.ANTHROPIC_API_KEY:
        try:
            _SYNC_REGISTRY["claude"] = SyncClaudeAdapter(
                api_key=cfg.ANTHROPIC_API_KEY,
                default_model=cfg.CLAUDE_HAIKU_MODEL,
                timeout=cfg.CLAUDE_TIMEOUT,
            )
        except Exception as e:
            logger.warning("Registry: SyncClaudeAdapter init failed: %s", e)

    if cfg.GEMINI_API_KEY:
        try:
            _SYNC_REGISTRY["gemini"] = SyncGeminiAdapter(
                api_key=cfg.GEMINI_API_KEY,
                default_model=cfg.GEMINI_MODEL,
                timeout=cfg.GEMINI_TIMEOUT,
            )
            logger.info("Registry: registered SyncGeminiAdapter (Phase 9)")
        except Exception as e:
            logger.warning("Registry: SyncGeminiAdapter init failed: %s", e)

    if cfg.GROQ_API_KEY:
        try:
            _SYNC_REGISTRY["groq"] = SyncGroqAdapter(
                api_key=cfg.GROQ_API_KEY,
                default_model=cfg.GROQ_FAST_MODEL,
                timeout=cfg.GROQ_TIMEOUT,
            )
            logger.info("Registry: registered SyncGroqAdapter (Phase 9)")
        except Exception as e:
            logger.warning("Registry: SyncGroqAdapter init failed: %s", e)

    if _ollama_reachable():
        try:
            _SYNC_REGISTRY["ollama"] = SyncOllamaAdapter(
                base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434"),
                default_model=cfg.INTELLIGENCE_LLM_MODEL,
            )
        except Exception as e:
            logger.warning("Registry: SyncOllamaAdapter init failed: %s", e)

    if _vllm_reachable():
        try:
            _SYNC_REGISTRY["vllm"] = SyncVLLMAdapter(
                base_url=os.getenv("VLLM_BASE_URL", "http://localhost:8011/v1"),
                default_model=cfg.VLLM_MODEL,
            )
        except Exception as e:
            logger.warning("Registry: SyncVLLMAdapter init failed: %s", e)

    _INITIALIZED = True
    logger.info(
        "Registry initialized: %d async providers (%s), %d sync providers (%s)",
        len(_ASYNC_REGISTRY), ",".join(sorted(_ASYNC_REGISTRY.keys())),
        len(_SYNC_REGISTRY), ",".join(sorted(_SYNC_REGISTRY.keys())),
    )


def get_async_provider(key: str):
    """Return registered IntelligenceProvider for `key`, or None if unregistered."""
    if not _INITIALIZED:
        init_registry()
    return _ASYNC_REGISTRY.get(key)


def get_sync_provider(key: str):
    """Return registered SyncProvider adapter for `key`, or None if unregistered."""
    if not _INITIALIZED:
        init_registry()
    return _SYNC_REGISTRY.get(key)


def registry_snapshot() -> dict:
    """Diagnostic — exposed via /internal/llm-registry endpoint.

    Returns a dict describing which providers are registered + their basic
    config (no secrets), for verification after Phase 8 deploy.
    """
    if not _INITIALIZED:
        init_registry()
    return {
        "initialized": _INITIALIZED,
        "async_providers": sorted(_ASYNC_REGISTRY.keys()),
        "sync_providers": sorted(_SYNC_REGISTRY.keys()),
    }
