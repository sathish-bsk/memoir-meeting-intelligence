"""Model router — resolves a Hub-specified `model` string to the right provider.

Hub `model` field uses the format "<provider>:<model-id>". Examples:
    claude:claude-sonnet-4-5
    claude:claude-haiku-4-5
    gemini:gemini-2.0-flash
    groq:llama-3.3-70b-versatile
    ollama:qwen2.5:14b
    vllm:Qwen/Qwen2.5-14B-Instruct

See plan §9.4.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from .prompt_loader import PromptBundle
from .provider_registry import get_async_provider, get_sync_provider

logger = logging.getLogger(__name__)


# Runtime set of FQ model IDs (provider:model) that were lazy-pulled this
# process lifetime. Used by _model_available_locally to honor models that
# weren't in LANGCHAIN_AVAILABLE_MODELS at startup but were pulled on demand.
_runtime_available_models: set[str] = set()

# Per-model lock so concurrent calls for the same uncached model wait on one pull
# rather than firing N parallel `ollama pull` subprocesses.
_pull_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


@dataclass
class CallOverrides:
    """Per-call overrides extracted from a Hub PromptBundle. Passed to
    provider methods as kwargs (model_override, temperature_override,
    max_tokens_override). All Nones → provider uses its env defaults.
    """
    model_override: Optional[str] = None
    temperature_override: Optional[float] = None
    max_tokens_override: Optional[int] = None

    def as_kwargs(self) -> dict:
        """Compact dict of non-None overrides. Convenient for **kwargs splats."""
        out = {}
        if self.model_override is not None:
            out["model_override"] = self.model_override
        if self.temperature_override is not None:
            out["temperature_override"] = self.temperature_override
        if self.max_tokens_override is not None:
            out["max_tokens_override"] = self.max_tokens_override
        return out


class ModelUnavailableError(Exception):
    """Raised when Hub specifies a model that isn't available locally and
    LANGCHAIN_ON_MISSING_MODEL=error.
    """
    pass


def _parse_model_id(hub_model: str) -> tuple[Optional[str], Optional[str]]:
    """Split "provider:model-id" → (provider_key, model_id). Returns (None, None)
    if the format isn't recognized.

    Ollama tags contain colons (e.g., "ollama:qwen2.5:14b"). We split on the
    FIRST colon only — everything after the first ":" is the model_id verbatim.
    """
    if not hub_model or ":" not in hub_model:
        return None, None
    provider, model_id = hub_model.split(":", 1)
    provider = provider.strip().lower()
    if provider not in ("claude", "gemini", "groq", "ollama", "vllm"):
        return None, None
    return provider, model_id.strip()


def _model_available_locally(provider_key: str, model_id: str) -> bool:
    """Check if the resolved model is actually usable on this box.

    Cloud (claude/gemini/groq): assume yes if provider is in registry.
    Ollama: yes if listed in LANGCHAIN_AVAILABLE_MODELS env (pre-pulled by
            vast-start.sh), OR pulled at runtime via _attempt_ollama_pull
            (tracked in _runtime_available_models set).
    vLLM: check the model_id matches the active VLLM_MODEL exactly (one model
          per instance).
    """
    from app.audio_preprocess.config import preprocess_config as cfg

    if provider_key in ("claude", "gemini", "groq"):
        return True  # cloud — model availability is the provider's problem

    declared = [m.strip() for m in (cfg.LANGCHAIN_AVAILABLE_MODELS or "").split(",") if m.strip()]
    fqid = f"{provider_key}:{model_id}"

    if provider_key == "ollama":
        return fqid in declared or fqid in _runtime_available_models

    if provider_key == "vllm":
        # vLLM serves ONE model. Even if declared in AVAILABLE_MODELS, it can
        # only serve the one in VLLM_MODEL. Can't lazy-swap.
        return model_id == cfg.VLLM_MODEL

    return False


def _attempt_ollama_pull(hub_model: str) -> bool:
    """Synchronously run `ollama pull <model>` for the requested Ollama model.

    BLOCKING. A 14B-class model takes several minutes to pull on a fresh box.
    First pipeline call to a not-yet-pulled model will block for that time;
    subsequent calls to the same model are instant (process-lifetime cache).

    Concurrent calls for the same model serialize on _pull_locks so we
    don't spawn N parallel subprocesses.

    Returns True if model is now available; False otherwise. False causes the
    caller (_handle_missing) to fall back to env default for this call.

    Configurable via LANGCHAIN_PULL_TIMEOUT_SEC (default 1800 = 30 min).
    """
    if not hub_model.startswith("ollama:"):
        return False
    model_id = hub_model[len("ollama:"):]

    with _pull_locks[hub_model]:
        # Double-check inside lock — another thread may have just pulled it.
        if hub_model in _runtime_available_models:
            return True

        timeout_sec = int(os.getenv("LANGCHAIN_PULL_TIMEOUT_SEC", "1800"))
        logger.info(
            "Auto-pulling Ollama model %s (timeout %ds, may take several minutes)...",
            model_id, timeout_sec,
        )
        try:
            result = subprocess.run(
                ["ollama", "pull", model_id],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            if result.returncode == 0:
                _runtime_available_models.add(hub_model)
                logger.info("Auto-pull of %s succeeded (cached for process lifetime)", model_id)
                return True
            logger.warning(
                "Auto-pull of %s failed (returncode=%d): %s",
                model_id, result.returncode, (result.stderr or "")[:300],
            )
            return False
        except subprocess.TimeoutExpired:
            logger.warning(
                "Auto-pull of %s exceeded timeout %ds — aborted", model_id, timeout_sec,
            )
            return False
        except FileNotFoundError:
            logger.warning(
                "`ollama` binary not in PATH on this host — cannot auto-pull. "
                "Either pre-pull via vast-start.sh or change LANGCHAIN_ON_MISSING_MODEL=fallback."
            )
            return False
        except Exception as e:
            logger.warning("Auto-pull of %s raised unexpectedly: %s", model_id, e)
            return False


def _handle_missing(hub_model: str, env_default_key: str, async_surface: bool):
    """LANGCHAIN_ON_MISSING_MODEL behavior router.

    Three modes:
    - "error" (strict): raise ModelUnavailableError. Pipeline fails for this prompt.
    - "fallback" (default, safe): warn + use env default provider. Pipeline unaffected.
    - "pull" (Ollama only): synchronously `ollama pull <model>`. First call to a new
      model blocks for minutes; subsequent calls are instant. On success, route to
      the now-available model. On failure (timeout, disk full, etc.), fall back.
    """
    from app.audio_preprocess.config import preprocess_config as cfg

    behavior = (cfg.LANGCHAIN_ON_MISSING_MODEL or "pull").lower()
    if behavior == "error":
        raise ModelUnavailableError(
            f"Hub model {hub_model!r} unavailable locally and "
            f"LANGCHAIN_ON_MISSING_MODEL=error"
        )

    if behavior == "pull" and hub_model.startswith("ollama:"):
        if _attempt_ollama_pull(hub_model):
            # Pull succeeded — re-resolve using the now-available registry entry.
            # No re-call to resolve_*_provider; we know provider_key from hub_model
            # and the registry already has the OllamaProvider instance.
            provider_key, model_id = _parse_model_id(hub_model)
            provider = (
                get_async_provider(provider_key) if async_surface
                else get_sync_provider(provider_key)
            )
            if provider is not None:
                logger.info("Routing %s to %s after auto-pull", hub_model, provider_key)
                return provider, CallOverrides(model_override=model_id)
            # Provider not registered (no credentials / unreachable) — fall through

    logger.warning(
        "Hub model %s unavailable locally; falling back to env default provider %s",
        hub_model, env_default_key,
    )
    provider = get_async_provider(env_default_key) if async_surface else get_sync_provider(env_default_key)
    return provider, CallOverrides()


def resolve_async_provider(bundle: PromptBundle, env_default_key: str):
    """Resolve which IntelligenceProvider should run this prompt.

    Returns (provider_instance, CallOverrides). The overrides dict is passed
    to provider.call() / _call_impl() as **kwargs once Phase 7a-step-2 adds
    the override kwargs to all 5 IntelligenceProvider subclasses.

    Until that subclass change is shipped, callers should ignore the
    overrides and just use the provider — model continues to come from
    each provider's env config.
    """
    from app.audio_preprocess.config import preprocess_config as cfg

    # Fast path: routing disabled OR Hub didn't specify a model
    if not cfg.LANGCHAIN_RESPECT_HUB_MODEL or not bundle.model:
        return get_async_provider(env_default_key), CallOverrides()

    provider_key, model_id = _parse_model_id(bundle.model)
    if provider_key is None:
        logger.warning("Hub model %r unparseable; falling back to env default", bundle.model)
        return get_async_provider(env_default_key), CallOverrides()

    provider = get_async_provider(provider_key)
    if provider is None:
        return _handle_missing(bundle.model, env_default_key, async_surface=True)

    if not _model_available_locally(provider_key, model_id):
        return _handle_missing(bundle.model, env_default_key, async_surface=True)

    return provider, CallOverrides(
        model_override=model_id,
        temperature_override=bundle.temperature,
        max_tokens_override=bundle.max_tokens,
    )


def resolve_sync_provider(bundle: PromptBundle, env_default_key: str):
    """Sync-side analog of resolve_async_provider — returns a SyncProvider
    adapter instance + CallOverrides.

    Note (plan §9.6): sync registry today supports only claude/ollama/vllm.
    If Hub routes a sync prompt to gemini/groq, falls back to env default
    with warning. Closing the gap is Phase 9.
    """
    from app.audio_preprocess.config import preprocess_config as cfg

    if not cfg.LANGCHAIN_RESPECT_HUB_MODEL or not bundle.model:
        return get_sync_provider(env_default_key), CallOverrides()

    provider_key, model_id = _parse_model_id(bundle.model)
    if provider_key is None:
        logger.warning("Hub model %r unparseable; falling back to env default", bundle.model)
        return get_sync_provider(env_default_key), CallOverrides()

    provider = get_sync_provider(provider_key)
    if provider is None:
        return _handle_missing(bundle.model, env_default_key, async_surface=False)

    if not _model_available_locally(provider_key, model_id):
        return _handle_missing(bundle.model, env_default_key, async_surface=False)

    return provider, CallOverrides(
        model_override=model_id,
        temperature_override=bundle.temperature,
        max_tokens_override=bundle.max_tokens,
    )
