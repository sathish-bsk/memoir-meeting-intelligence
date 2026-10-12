"""Prompt loader — pulls from LangChain Hub when enabled, else returns in-code defaults.

Public surface:
    PromptBundle(system, user, source, model, temperature, max_tokens)
    get_prompt(name, default_system, default_user_template, **variables) -> PromptBundle  # async
    get_prompt_sync(name, default_system, default_user_template, **variables) -> PromptBundle

Cache strategy (see plan §7):
- One in-process dict, TTL per LANGCHAIN_HUB_CACHE_TTL
- threading.RLock per cache key — works for both sync direct callers and async
  callers (which arrive here via asyncio.to_thread)
- Known-absent prompts are cached too so we don't refetch every call

Hub failure NEVER blocks the pipeline — every error path falls back to default.

Option C (LANGCHAIN_RESPECT_HUB_MODEL=true): Hub templates may carry `metadata`
with model/temperature/max_tokens. These flow through PromptBundle and are
consumed by model_router.py in Phase 7a.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class PromptBundle:
    """Resolved prompt + optional model/generation params.

    `source="default"` → from in-code default (Hub disabled, prompt absent,
                         or fetch error).
    `source="hub"` → from LangChain Hub.

    `model`, `temperature`, `max_tokens` are populated only when the Hub
    template specified them AND LANGCHAIN_RESPECT_HUB_MODEL=true. Phase 7a
    consumes them via model_router.resolve_*_provider().
    """

    system: str
    user: str
    source: str = "default"
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


# Cache: key=f"{name}:{tag}" → (template_or_None, expiry_epoch_seconds)
# None template = "known absent in Hub" (so we don't re-fetch every call within TTL)
_cache: dict[str, tuple[Any, float]] = {}
_locks: defaultdict[str, threading.RLock] = defaultdict(threading.RLock)


def _render_default(default_system: str, default_user_template: str, **variables) -> PromptBundle:
    """Build the fallback bundle. Skip str.format when no variables are passed —
    that matches the sync-caller pattern where _build_prompt() already produced
    the final user prompt and is being passed in as the default_user_template.
    """
    if variables:
        try:
            user = default_user_template.format(**variables)
        except (KeyError, IndexError) as e:
            logger.warning(
                "default user template format failed (%s); returning template verbatim", e
            )
            user = default_user_template
    else:
        user = default_user_template
    return PromptBundle(system=default_system, user=user, source="default")


def _pull_template_robust(pull_id: str):
    """Pull a Hub prompt with fallback for public prompts in foreign namespaces.

    LangSmith routes prompt access via the API key's workspace. A service key
    scoped to workspace A gets 403 trying to read a prompt owned by workspace B,
    EVEN IF the prompt is marked public. The SDK's pull_prompt() always sends
    auth headers → 403. Public prompts are reachable unauthenticated, so we
    try that path first for any owner/name lookup, then fall back to the
    authenticated SDK call if needed (private prompts in our own workspace).
    """
    import json
    import requests
    from langchain_core.load.load import loads

    # Path A: unauthenticated GET (works for public prompts regardless of namespace)
    try:
        r = requests.get(
            f"https://api.smith.langchain.com/commits/{pull_id}/latest",
            timeout=10,
        )
        if r.status_code == 200:
            payload = r.json()
            manifest = payload.get("manifest")
            if manifest:
                # Deserialize the LangChain manifest into a ChatPromptTemplate.
                template = loads(json.dumps(manifest))
                # Attach Hub's model_provider + model_config as instance attrs
                # so _extract_from_template can read them for Option C routing.
                # API returns these at the top level of the commit response,
                # SIBLINGS of the manifest. The SDK doesn't expose them through
                # the ChatPromptTemplate object so we side-channel here.
                try:
                    object.__setattr__(template, "_memoir_model_provider", payload.get("model_provider"))
                    object.__setattr__(template, "_memoir_model_config", payload.get("model_config") or {})
                except Exception:
                    # If the template object's class doesn't allow setattr,
                    # routing falls back to env default — graceful.
                    pass
                return template
        logger.debug("Unauth pull returned %s; trying authenticated SDK", r.status_code)
    except Exception as e:
        logger.debug("Unauth pull failed (%s); trying authenticated SDK", e)

    # Path B: authenticated SDK pull (private prompts in our own workspace)
    from langsmith import Client
    client = Client()
    return client.pull_prompt(
        pull_id,
        include_model=False,
        dangerously_pull_public_prompt=True,
    )


# Map LangChain provider class name (from Hub API model_provider field) to
# our internal provider registry key. ChatOpenAI is intentionally absent —
# our registry doesn't have OpenAI, so prompts left at the Hub UI's default
# (ChatOpenAI + gpt-5.5) fall through to env default at runtime.
# Add entries here as we add more SyncProvider/IntelligenceProvider adapters.
_PROVIDER_CLASS_TO_KEY = {
    "ChatAnthropic": "claude",
    "ChatGroq": "groq",
    "ChatGoogleGenerativeAI": "gemini",
    "ChatOllama": "ollama",
    # ChatVLLM doesn't have a standard class name — LangChain uses ChatOpenAI
    # with a custom base_url for vLLM (OpenAI-compat). If we ever want
    # Hub-driven vLLM routing, that would need a custom convention here.
}


def _get_template_cached(name: str, tag: str, ttl_seconds: int, owner: str = ""):
    """Returns the Hub ChatPromptTemplate or None ("known absent").

    The pull identifier is constructed as `<owner>/<name>` when LANGCHAIN_HUB_OWNER
    is set, else bare `<name>` (only works for public prompts in the marketplace).
    Cache key uses the full identifier so an owner change correctly invalidates.
    """
    pull_id = f"{owner}/{name}" if owner else name
    cache_key = f"{pull_id}:{tag}"
    now = time.monotonic()

    # Fast path — read without lock
    entry = _cache.get(cache_key)
    if entry is not None:
        template, expiry = entry
        if now < expiry:
            return template

    # Slow path — lock, double-check, fetch
    with _locks[cache_key]:
        entry = _cache.get(cache_key)
        if entry is not None and now < entry[1]:
            return entry[0]
        try:
            template = _pull_template_robust(pull_id)
            logger.debug("pull(%s): OK", pull_id)
        except ImportError:
            logger.warning(
                "langsmith not installed but LANGCHAIN_ENABLED=true. "
                "Run `pip install langsmith langchain-core` on this service."
            )
            template = None
        except Exception as e:
            # Hub miss, network error, auth error → cache as "absent" so we don't
            # spam pulls during the TTL window. Pipeline falls back gracefully.
            logger.debug("hub.pull(%s) failed: %s — falling back to default", cache_key, e)
            template = None
        _cache[cache_key] = (template, now + ttl_seconds)
        return template


def _extract_model_metadata(template) -> dict:
    """Build the metadata dict from Hub's model_provider + model_config (which we
    side-channeled onto the template during _pull_template_robust).

    Returns keys for PromptBundle: model (as "<our-key>:<model-id>"),
    temperature, max_tokens.

    When model_provider doesn't map to a key in _PROVIDER_CLASS_TO_KEY (e.g.,
    ChatOpenAI — we don't have OpenAI in our registry), model is omitted.
    With model=None, resolve_*_provider falls back to env default at runtime.
    """
    metadata: dict = {}
    provider_class = getattr(template, "_memoir_model_provider", None)
    config = getattr(template, "_memoir_model_config", {}) or {}

    if provider_class and config.get("model"):
        registry_key = _PROVIDER_CLASS_TO_KEY.get(provider_class)
        if registry_key:
            metadata["model"] = f"{registry_key}:{config['model']}"
        # else: provider not in registry → leave bundle.model None → env default

    # Honor temperature and max_tokens regardless of provider mapping, since
    # those work on the env-default provider too. Skip when not set in Hub.
    if "temperature" in config and config["temperature"] is not None:
        metadata["temperature"] = float(config["temperature"])
    if "max_tokens" in config and config["max_tokens"] is not None:
        metadata["max_tokens"] = int(config["max_tokens"])
    elif "max_output_tokens" in config and config["max_output_tokens"] is not None:
        # Some providers use max_output_tokens (Gemini)
        metadata["max_tokens"] = int(config["max_output_tokens"])

    return metadata


def _extract_from_template(template, variables: dict) -> tuple[str, str, dict]:
    """Render template.format_messages and pull metadata.

    Returns (system_str, user_str, metadata_dict).
    metadata_dict may contain model/temperature/max_tokens keys.

    Raises if the template can't be formatted with the provided variables —
    caller catches and falls back to default.
    """
    if variables:
        formatted = template.format_messages(**variables)
    else:
        # Sync-caller pattern: _build_prompt already inlined the variables.
        # Hub template should have no placeholders; format_messages() handles
        # that fine (returns the messages as-is).
        formatted = template.format_messages()

    # ChatPromptTemplate yields a list of messages. We expect:
    # - SystemMessage (first) — extract .content
    # - HumanMessage (second) — extract .content
    # Anything else (e.g., AIMessage few-shot examples) gets concatenated into user.
    system_str = ""
    user_parts = []
    for msg in formatted:
        # langchain_core.messages: SystemMessage, HumanMessage, AIMessage, etc.
        msg_type = msg.__class__.__name__
        if msg_type == "SystemMessage" and not system_str:
            system_str = msg.content
        else:
            user_parts.append(msg.content)
    user_str = "\n\n".join(user_parts) if user_parts else ""

    # Metadata (model, temperature, max_tokens) — pulled from Hub's model_provider
    # + model_config fields (side-channeled onto the template during fetch).
    # See _extract_model_metadata for the provider-class → registry-key mapping.
    metadata = _extract_model_metadata(template)

    return system_str, user_str, metadata


def get_prompt_sync(
    name: str,
    default_system: str,
    default_user_template: str,
    **variables,
) -> PromptBundle:
    """Sync API for LLMCorrector / PunctuationRefiner / MemoirExtractor.

    Behavior matrix:
    - LANGCHAIN_ENABLED=false → return default immediately.
    - LANGCHAIN_ENABLED=true, prompt in Hub → use Hub system+user; populate
      model/temperature/max_tokens iff LANGCHAIN_RESPECT_HUB_MODEL=true.
    - LANGCHAIN_ENABLED=true, prompt NOT in Hub or fetch fails → return default
      with a debug log.
    """
    from app.audio_preprocess.config import preprocess_config as cfg

    if not cfg.LANGCHAIN_ENABLED:
        return _render_default(default_system, default_user_template, **variables)

    template = _get_template_cached(name, cfg.LANGCHAIN_HUB_TAG, cfg.LANGCHAIN_HUB_CACHE_TTL, owner=cfg.LANGCHAIN_HUB_OWNER)
    if template is None:
        return _render_default(default_system, default_user_template, **variables)

    try:
        system_str, user_str, metadata = _extract_from_template(template, variables)
    except Exception as e:
        # Variable mismatch, malformed template, etc. — fall back, warn loudly
        # so the cofounder sees their broken edit.
        logger.warning(
            "Hub template %s render failed (%s) — using in-code default. "
            "Likely cause: variable name drift between Hub and code.",
            name, e,
        )
        return _render_default(default_system, default_user_template, **variables)

    # Only honor model/temp/max_tokens when Option C is on
    if cfg.LANGCHAIN_RESPECT_HUB_MODEL:
        model = metadata.get("model")
        temperature = metadata.get("temperature")
        max_tokens = metadata.get("max_tokens")
    else:
        model = temperature = max_tokens = None

    return PromptBundle(
        system=system_str or default_system,  # belt + braces — empty system = fall back
        user=user_str if user_str else (default_user_template.format(**variables) if variables else default_user_template),
        source="hub",
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )


async def get_prompt(
    name: str,
    default_system: str,
    default_user_template: str,
    **variables,
) -> PromptBundle:
    """Async wrapper — runs sync logic in a thread to avoid blocking the event
    loop on Hub I/O. Cache hits return immediately without an OS thread hop
    (asyncio.to_thread overhead is small; thread re-use via default ThreadPool).
    """
    return await asyncio.to_thread(
        get_prompt_sync, name, default_system, default_user_template, **variables
    )


def prewarm_all(prompt_names: list[str]) -> int:
    """Pre-fetch every prompt name in `prompt_names` at service startup.
    Returns count of prompts successfully cached (hits + known-absent). Safe to
    call multiple times — idempotent.

    Triggered when LANGCHAIN_PREWARM_ON_STARTUP=true. Failures don't raise.
    """
    from app.audio_preprocess.config import preprocess_config as cfg

    if not cfg.LANGCHAIN_ENABLED:
        return 0
    count = 0
    for name in prompt_names:
        try:
            _get_template_cached(name, cfg.LANGCHAIN_HUB_TAG, cfg.LANGCHAIN_HUB_CACHE_TTL, owner=cfg.LANGCHAIN_HUB_OWNER)
            count += 1
        except Exception as e:
            logger.warning("prewarm of %s failed: %s", name, e)
    logger.info("LangChain Hub prewarm complete: %d/%d entries cached", count, len(prompt_names))
    return count
