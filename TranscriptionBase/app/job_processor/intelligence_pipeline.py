"""
Multi-provider intelligence pipeline (Phase B).

Runs a 6-prompt pipeline against call transcripts using Claude (production)
or Gemini (free dev). Saves results as intelligence.json alongside other
pipeline outputs. The existing Ollama extractor is completely untouched.

Providers:
  INTELLIGENCE_PROVIDER=ollama  → existing flow, this file not used
  INTELLIGENCE_PROVIDER=gemini  → free dev/testing (Google)
  INTELLIGENCE_PROVIDER=groq    → free dev/testing (Llama 3.3 70B on Groq)
  INTELLIGENCE_PROVIDER=claude  → paid production (best quality)
"""

import asyncio
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any

# LangSmith tracing + LangChain Hub prompt loader (plan/langchain_langsmith_integration.md).
# All no-ops when LANGSMITH_ENABLED/LANGCHAIN_ENABLED=false (default).
from app.llm_observability import get_prompt, prompt_name_context, resolve_async_provider


def _provider_env_default_key() -> str:
    """Read INTELLIGENCE_PROVIDER from env at call time so a runtime flip
    is honored without re-import."""
    from app.audio_preprocess.config import preprocess_config as _cfg
    return (_cfg.INTELLIGENCE_PROVIDER or "ollama").lower()


def _accumulate_into_provider(provider, stats: dict) -> None:
    """Increment the provider's `total_*` accumulators by the per-call values
    in `stats`. Best-effort — only touches fields that already exist on the
    instance, so accumulating a Claude-provider's cache_* stats into a
    Gemini/Ollama provider just skips the cache fields.

    Why: when cross-provider routing executes a call on a different instance,
    the call's tokens/cost would otherwise be invisible to the pipeline's
    `provider.get_usage()` at the end. Folding them into the pipeline's own
    provider keeps the final result['usage'] dict accurate as a TOTAL across
    all backends used during the run, even though the dict's "provider" field
    still names the env-default provider (cosmetic only; per-call attribution
    lives in the prompt_log entries written by _collect_prompt_log).
    """
    if not stats or provider is None:
        return
    field_map = [
        ("total_input_tokens",          "input_tokens"),
        ("total_output_tokens",         "output_tokens"),
        ("total_cache_read_tokens",     "cache_read_tokens"),
        ("total_cache_creation_tokens", "cache_creation_tokens"),
        ("total_cost_usd",              "cost_usd"),
    ]
    for attr, stat_key in field_map:
        if not hasattr(provider, attr):
            continue
        try:
            delta = stats.get(stat_key, 0) or 0
            setattr(provider, attr, getattr(provider, attr) + delta)
        except Exception:
            pass


async def _routed_call(provider, bundle, system, user, model_tier):
    """Phase 7a wiring: consult the model router, fall back to the supplied
    `provider` (env default) when LANGCHAIN_RESPECT_HUB_MODEL=false / bundle
    has no model field / route resolution fails.

    IMPORTANT — stats forwarding: the pipeline reads per-call stats via
    _collect_prompt_log(provider, ...) and aggregate usage via
    provider.get_usage(). Both read from `provider._last_call_stats` and
    `provider.total_*`. If we executed the call on a different instance
    (e.g., registry singleton) those reads would return empty/stale data.

    Behavior:
    - Routing disabled OR Hub didn't set a model → call `provider` directly
      (bypass the registry entirely so _last_call_stats lands on `provider`).
    - Routing enabled + Hub model resolves to the SAME provider instance
      → call `provider` directly with the overrides.
    - Routing enabled + Hub model resolves to a DIFFERENT instance
      → execute on the routed instance, then BOTH mirror its
      _last_call_stats onto `provider` so _collect_prompt_log still works,
      AND fold token/cost contributions into provider.total_* accumulators
      so provider.get_usage() at the pipeline end reports an accurate TOTAL
      across whichever backends served the run.

    Known cosmetic limitation: provider.get_usage()'s "provider" field still
    names the env-default backend even when some calls routed to another
    backend. Per-call backend attribution is in the prompt_log entries.
    """
    from app.audio_preprocess.config import preprocess_config as _cfg

    # Default path — no routing requested or no Hub model → use supplied provider.
    if not _cfg.LANGCHAIN_RESPECT_HUB_MODEL or not bundle.model:
        if provider is None:
            return None
        return await provider.call(system, user, model_tier)

    resolved, overrides = resolve_async_provider(bundle, env_default_key=_provider_env_default_key())

    # Resolved is None → router returned no match → fall back to supplied.
    if resolved is None:
        if provider is None:
            return None
        return await provider.call(system, user, model_tier, **overrides.as_kwargs())

    # Same instance → call directly so stats land on `provider` as usual.
    if resolved is provider:
        return await provider.call(system, user, model_tier, **overrides.as_kwargs())

    # Cross-provider route — execute on `resolved`, then forward:
    #   (a) _last_call_stats onto `provider` for _collect_prompt_log
    #   (b) per-call token/cost deltas into `provider.total_*` accumulators
    #       so provider.get_usage() reports the run TOTAL not just env-default
    #       backend's calls.
    result = await resolved.call(system, user, model_tier, **overrides.as_kwargs())
    try:
        last = resolved.get_last_call_stats()
        if last is not None and provider is not None:
            provider._last_call_stats = last
            _accumulate_into_provider(provider, last)
    except Exception:
        pass
    return result

logger = logging.getLogger("job_processor.intelligence_pipeline")


# ---------------------------------------------------------------------------
# Provider abstraction
# ---------------------------------------------------------------------------

class IntelligenceProvider(ABC):
    """Base class for LLM providers.

    Two-method pattern (see plan §5.4):
    - Public concrete `call()` — wraps with @traceable_llm so a single
      decoration point covers all 5 subclasses. Delegates to `_call_impl()`.
    - Abstract `_call_impl()` — subclass-specific HTTP request. Subclasses
      MUST populate self._last_call_stats before returning so the existing
      `_collect_prompt_log()` machinery still works.
    """

    def __init__(self):
        self._last_call_stats: dict | None = None

    async def call(
        self,
        system_prompt: str,
        user_prompt: str,
        model_tier: str,
        *,
        model_override: str | None = None,
        temperature_override: float | None = None,
        max_tokens_override: int | None = None,
    ) -> dict[str, Any] | None:
        """Public entry point. Traced (when LANGSMITH_ENABLED). Delegates to
        provider-specific _call_impl().

        Optional override kwargs (plan §7a / Option C):
            model_override        — replace the model id chosen by model_tier
            temperature_override  — replace provider default temperature
            max_tokens_override   — replace provider default max output tokens

        When all overrides are None (the common case), behavior is identical
        to pre-Phase-7a — each subclass picks its model via model_tier.

        Trace name comes from current_prompt_name ContextVar.
        """
        from app.llm_observability.tracing import traceable_llm

        # Args passed EXPLICITLY (not via closure) so LangSmith captures them
        # as trace inputs. Previously `_traced()` took no args and pulled
        # everything from the enclosing scope → empty `inputs` dict on every
        # trace. Witnessed May 17 2026 on Michigan box: every memoir-* trace
        # had inputs={} in LangSmith UI while memoir-extractor (which uses
        # the sync routed_sync_call path) showed full system+user prompts.
        @traceable_llm("intelligence_provider_call")
        async def _traced(
            system_prompt: str,
            user_prompt: str,
            model_tier: str,
            model_override: str | None = None,
            temperature_override: float | None = None,
            max_tokens_override: int | None = None,
        ):
            return await self._call_impl(
                system_prompt, user_prompt, model_tier,
                model_override=model_override,
                temperature_override=temperature_override,
                max_tokens_override=max_tokens_override,
            )

        return await _traced(
            system_prompt,
            user_prompt,
            model_tier,
            model_override=model_override,
            temperature_override=temperature_override,
            max_tokens_override=max_tokens_override,
        )

    @abstractmethod
    async def _call_impl(
        self,
        system_prompt: str,
        user_prompt: str,
        model_tier: str,
        *,
        model_override: str | None = None,
        temperature_override: float | None = None,
        max_tokens_override: int | None = None,
    ) -> dict[str, Any] | None:
        """Subclass-specific HTTP request. Implementations must populate
        self._last_call_stats before returning.

        Args:
            system_prompt: The system instructions.
            user_prompt: The user message with transcript + context.
            model_tier: "fast" (Haiku/Flash) or "smart" (Sonnet/Flash).
            model_override: When set, overrides the model_tier-based selection.
            temperature_override: When set, overrides the provider's default temperature.
            max_tokens_override: When set, overrides the provider's default max tokens.

        Returns:
            Parsed JSON dict, or None on failure.
        """
        ...

    @abstractmethod
    def get_usage(self) -> dict:
        """Return cumulative token usage and cost."""
        ...

    def get_last_call_stats(self) -> dict | None:
        """Return stats from the most recent call() invocation."""
        return self._last_call_stats


# Tier 4 — Claude pricing constants (Sonnet 4.6 / Haiku 4.5, May 2026 rates).
# input/output rates are $/Mtok base. Cache write multiplier depends on TTL:
# 5-min = 1.25×, 1-hour = 2×. Cache read = 0.1× of base regardless of TTL.
_CLAUDE_PRICING = {
    "sonnet": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    "haiku":  {"input_per_mtok": 0.80, "output_per_mtok": 4.00},
}


def _claude_cost_usd(model_tier: str, input_tokens: int, cache_creation: int,
                     cache_read: int, output_tokens: int, ttl: str) -> float:
    """Compute Claude per-call cost with the right multipliers.

    Anthropic's response.usage:
      - input_tokens: uncached prefix + variable content (charged 1×)
      - cache_creation_input_tokens: written to cache this call (1.25× or 2×)
      - cache_read_input_tokens: reused from cache this call (0.1×)
      - output_tokens: generated (charged at output rate)

    These are MUTUALLY EXCLUSIVE counts — the previous implementation
    subtracted cache_read from input_tokens, which double-counted.
    """
    rates = _CLAUDE_PRICING["haiku" if model_tier == "fast" else "sonnet"]
    base = rates["input_per_mtok"]
    write_mult = 2.0 if ttl == "1h" else 1.25
    cost = (
        input_tokens * base
        + cache_creation * base * write_mult
        + cache_read * base * 0.1
        + output_tokens * rates["output_per_mtok"]
    ) / 1_000_000
    return round(cost, 6)


class ClaudeProvider(IntelligenceProvider):
    """Anthropic Claude API provider with prompt caching.

    Tier 4 enhancements (May 2026):
      - Tracks cache_creation_input_tokens separately (was lumped into input)
      - 1-hour TTL with extended-cache-ttl-2025-04-11 beta header (configurable
        via CLAUDE_CACHE_TTL env var)
      - Correct cost calculation distinguishing base / cache-write / cache-read
      - Adds call_layered() for 3-layer caching (Layer 1 system + Layer 2
        team-static user prefix + Layer 3 variable user content)
    """

    def __init__(self, api_key: str, haiku_model: str, sonnet_model: str,
                 max_retries: int = 2, timeout: float = 60.0,
                 cache_ttl: str = "1h"):
        super().__init__()
        import anthropic
        # 1h TTL is gated on a beta header at time of writing (April 2025
        # beta). If it has graduated to GA by deploy time, the header is a
        # no-op. Set CLAUDE_CACHE_TTL=5m to use the GA 5-min TTL without
        # the beta header.
        default_headers = {}
        if cache_ttl == "1h":
            default_headers["anthropic-beta"] = "extended-cache-ttl-2025-04-11"
        self.client = anthropic.AsyncAnthropic(
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout,
            default_headers=default_headers or None,
        )
        self.haiku_model = haiku_model
        self.sonnet_model = sonnet_model
        self.cache_ttl = cache_ttl  # "1h" or "5m"
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_cache_creation_tokens = 0
        self.total_cost_usd = 0.0

    def _cache_control(self) -> dict:
        """Build the cache_control marker honoring the configured TTL."""
        if self.cache_ttl == "1h":
            return {"type": "ephemeral", "ttl": "1h"}
        return {"type": "ephemeral"}

    async def _call_impl(self, system_prompt: str, user_prompt: str,
                         model_tier: str,
                         *,
                         model_override: str | None = None,
                         temperature_override: float | None = None,
                         max_tokens_override: int | None = None) -> dict[str, Any] | None:
        """Single-layer call: system cached as Layer 1; user is variable.

        Renamed from `call()` in plan §5.4 refactor — base class now provides
        a concrete `call()` wrapper with @traceable_llm.

        Phase 7a overrides: model_override replaces model_tier-based selection;
        temperature_override / max_tokens_override applied when set.
        """
        model = model_override or (self.haiku_model if model_tier == "fast" else self.sonnet_model)
        self._last_call_stats = None
        call_start = time.time()
        try:
            response = await self.client.messages.create(
                model=model,
                max_tokens=4096,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": self._cache_control(),
                }],
                messages=[{"role": "user", "content": user_prompt}],
            )
            return self._record_response(response, model, model_tier, call_start)
        except Exception as e:
            duration_ms = int((time.time() - call_start) * 1000)
            self._last_call_stats = {
                "provider": "claude",
                "model": model,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "parse_success": False,
                "error": str(e),
            }
            logger.warning(f"Claude API call failed ({model_tier}): {e}")
            return None

    async def call_layered(
        self,
        system_prompt: str,
        team_static_block: str | None,
        variable_user_content: str,
        model_tier: str,
    ) -> dict[str, Any] | None:
        """3-layer caching call:

        Layer 1 — system_prompt (org-wide static, cached forever within TTL)
        Layer 2 — team_static_block (per-team vocab + CRM contacts, sorted
                  deterministically; cached per team). Pass None to skip.
        Layer 3 — variable_user_content (transcript chunk; never cached)

        Returns parsed JSON or None on failure.
        """
        model = self.haiku_model if model_tier == "fast" else self.sonnet_model
        self._last_call_stats = None
        call_start = time.time()
        try:
            user_blocks: list = []
            if team_static_block:
                user_blocks.append({
                    "type": "text",
                    "text": team_static_block,
                    "cache_control": self._cache_control(),
                })
            user_blocks.append({"type": "text", "text": variable_user_content})

            response = await self.client.messages.create(
                model=model,
                max_tokens=4096,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": self._cache_control(),
                }],
                messages=[{"role": "user", "content": user_blocks}],
            )
            return self._record_response(response, model, model_tier, call_start)
        except Exception as e:
            duration_ms = int((time.time() - call_start) * 1000)
            self._last_call_stats = {
                "provider": "claude",
                "model": model,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "parse_success": False,
                "error": str(e),
            }
            logger.warning(f"Claude layered API call failed ({model_tier}): {e}")
            return None

    def _record_response(self, response, model: str, model_tier: str,
                          call_start: float) -> dict[str, Any] | None:
        """Shared response handling for call() and call_layered()."""
        text = response.content[0].text if response.content else ""
        duration_ms = int((time.time() - call_start) * 1000)

        usage = response.usage
        input_tokens = getattr(usage, "input_tokens", 0)
        output_tokens = getattr(usage, "output_tokens", 0)
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cache_read_tokens += cache_read
        self.total_cache_creation_tokens += cache_creation

        cost = _claude_cost_usd(
            model_tier, input_tokens, cache_creation, cache_read,
            output_tokens, self.cache_ttl,
        )
        self.total_cost_usd += cost

        parsed = _parse_json(text)

        self._last_call_stats = {
            "provider": "claude",
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "cost_usd": cost,
            "duration_ms": duration_ms,
            "parse_success": parsed is not None,
        }

        return parsed

    async def prewarm_cache(self, system_prompt: str,
                             team_static_block: str | None) -> None:
        """Fire a max_tokens=0 request to populate cache before real calls.

        Costs a single cache-write at the configured TTL × multiplier.
        Call once at the top of Type 5 / Type 3 to absorb the write cost
        before any real LLM batch hits — saves first-call latency.
        Failures are non-fatal; if pre-warm errors out the real calls
        will write cache themselves.
        """
        try:
            user_blocks: list = []
            if team_static_block:
                user_blocks.append({
                    "type": "text",
                    "text": team_static_block,
                    "cache_control": self._cache_control(),
                })
            user_blocks.append({"type": "text", "text": "warmup"})

            await self.client.messages.create(
                model=self.haiku_model,  # cheaper model for warmup
                max_tokens=1,  # max_tokens=0 not supported in batch; 1 is minimum
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": self._cache_control(),
                }],
                messages=[{"role": "user", "content": user_blocks}],
            )
            logger.info(f"Claude cache pre-warmed (ttl={self.cache_ttl})")
        except Exception as e:
            logger.warning(f"Claude cache pre-warm failed (non-fatal): {e}")

    def get_usage(self) -> dict:
        return {
            "provider": "claude",
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cache_read_tokens": self.total_cache_read_tokens,
            "total_cache_creation_tokens": self.total_cache_creation_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "cost_usd": round(self.total_cost_usd, 6),
        }


class GeminiProvider(IntelligenceProvider):
    """Google Gemini API provider (free tier for dev)."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash",
                 max_retries: int = 2, timeout: float = 60.0):
        super().__init__()
        from google import genai
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.max_retries = max_retries
        self.timeout = timeout
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    async def _call_impl(self, system_prompt: str, user_prompt: str,
                         model_tier: str,
                         *,
                         model_override: str | None = None,
                         temperature_override: float | None = None,
                         max_tokens_override: int | None = None) -> dict[str, Any] | None:
        # Renamed from `call()` per plan §5.4 — base class wraps with @traceable_llm.
        # Phase 7a: model_override flows into the GenerateContentConfig below
        # via the local `model` variable; temp/max_tokens override the config
        # defaults when set.
        from google.genai import types
        model = model_override or self.model
        self._last_call_stats = None
        call_start = time.time()
        try:
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            text = response.text if response.text else ""
            duration_ms = int((time.time() - call_start) * 1000)

            # Track usage (approximate)
            input_tokens = 0
            output_tokens = 0
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                meta = response.usage_metadata
                input_tokens = getattr(meta, "prompt_token_count", 0)
                output_tokens = getattr(meta, "candidates_token_count", 0)
                self.total_input_tokens += input_tokens
                self.total_output_tokens += output_tokens

            parsed = _parse_json(text)

            self._last_call_stats = {
                "provider": "gemini",
                "model": self.model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "parse_success": parsed is not None,
            }

            return parsed
        except Exception as e:
            duration_ms = int((time.time() - call_start) * 1000)
            self._last_call_stats = {
                "provider": "gemini",
                "model": self.model,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "parse_success": False,
                "error": str(e),
            }
            logger.warning(f"Gemini API call failed ({model_tier}): {e}")
            return None

    def get_usage(self) -> dict:
        return {
            "provider": "gemini",
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "cost_usd": 0.0,  # Free tier
        }


class GroqProvider(IntelligenceProvider):
    """Groq API provider (free tier for dev — Llama models on custom hardware)."""

    def __init__(self, api_key: str, fast_model: str = "llama-3.1-8b-instant",
                 smart_model: str = "llama-3.3-70b-versatile",
                 max_retries: int = 2, timeout: float = 60.0):
        super().__init__()
        from groq import AsyncGroq
        self.client = AsyncGroq(
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout,
        )
        self.fast_model = fast_model
        self.smart_model = smart_model
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    async def _call_impl(self, system_prompt: str, user_prompt: str,
                         model_tier: str,
                         *,
                         model_override: str | None = None,
                         temperature_override: float | None = None,
                         max_tokens_override: int | None = None) -> dict[str, Any] | None:
        # Renamed from `call()` per plan §5.4 — base class wraps with @traceable_llm.
        # Phase 7a: model_override replaces tier-based selection.
        model = model_override or (self.fast_model if model_tier == "fast" else self.smart_model)
        self._last_call_stats = None
        call_start = time.time()
        try:
            response = await self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=4096,
            )
            text = response.choices[0].message.content if response.choices else ""
            duration_ms = int((time.time() - call_start) * 1000)

            # Track usage
            input_tokens = 0
            output_tokens = 0
            if response.usage:
                input_tokens = response.usage.prompt_tokens or 0
                output_tokens = response.usage.completion_tokens or 0
                self.total_input_tokens += input_tokens
                self.total_output_tokens += output_tokens

            parsed = _parse_json(text)

            self._last_call_stats = {
                "provider": "groq",
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "parse_success": parsed is not None,
            }

            return parsed
        except Exception as e:
            duration_ms = int((time.time() - call_start) * 1000)
            self._last_call_stats = {
                "provider": "groq",
                "model": model,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "parse_success": False,
                "error": str(e),
            }
            logger.warning(f"Groq API call failed ({model_tier}/{model}): {e}")
            return None

    def get_usage(self) -> dict:
        return {
            "provider": "groq",
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "cost_usd": 0.0,  # Free tier
        }


class OllamaProvider(IntelligenceProvider):
    """Local Ollama provider — runs on same machine (GPU). Free, no API key."""

    def __init__(self, base_url: str = "http://localhost:11434",
                 model: str = "qwen2.5:3b-instruct",
                 timeout: float = 600.0):
        super().__init__()
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._client = None  # Reuse single httpx client

    async def _get_client(self):
        """Get or create a reusable httpx client."""
        import httpx
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(
                    max_connections=5,
                    max_keepalive_connections=2,
                    keepalive_expiry=30,
                ),
            )
        return self._client

    async def close(self):
        """Close the httpx client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _call_impl(self, system_prompt: str, user_prompt: str,
                         model_tier: str,
                         *,
                         model_override: str | None = None,
                         temperature_override: float | None = None,
                         max_tokens_override: int | None = None) -> dict[str, Any] | None:
        # Renamed from `call()` per plan §5.4 — base class wraps with @traceable_llm.
        # Phase 7a: model_override replaces self.model.
        effective_model = model_override or self.model
        self._last_call_stats = None
        call_start = time.time()
        try:
            payload = {
                "model": effective_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1, "num_ctx": 16384},
            }

            client = await self._get_client()
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()

            data = resp.json()
            content = data.get("message", {}).get("content", "")
            duration_ms = int((time.time() - call_start) * 1000)

            # Track tokens (Ollama returns these in response)
            input_tokens = data.get("prompt_eval_count", 0)
            output_tokens = data.get("eval_count", 0)
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens

            parsed = _parse_json(content)

            self._last_call_stats = {
                "provider": "ollama",
                "model": self.model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "parse_success": parsed is not None,
            }

            return parsed
        except Exception as e:
            duration_ms = int((time.time() - call_start) * 1000)
            self._last_call_stats = {
                "provider": "ollama",
                "model": self.model,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "parse_success": False,
                "error": str(e),
            }
            logger.warning(f"Ollama API call failed ({self.model}): {e}")
            return None

    def get_usage(self) -> dict:
        return {
            "provider": "ollama",
            "model": self.model,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "cost_usd": 0.0,
        }


async def prewarm_claude_for_stage(system_prompt: str,
                                    team_static_block: str | None = None) -> None:
    """Tier 4.2 — pre-populate Anthropic prompt cache before a stage's
    first real LLM call.

    Fire-and-forget. Costs a single cache-write at the configured TTL ×
    multiplier — pays for itself iff at least one subsequent call hits
    the cache. Most useful at the start of Type 5 / Type 3 when a fresh
    cache TTL window is likely (e.g., first meeting of the hour for
    that org).

    Silently no-ops when:
      - INTELLIGENCE_PROVIDER != "claude"
      - Provider construction fails (e.g., ANTHROPIC_API_KEY missing)
      - Pre-warm call itself raises (logged inside ClaudeProvider.prewarm_cache)
    """
    from app.audio_preprocess.config import preprocess_config as config
    if config.INTELLIGENCE_PROVIDER != "claude":
        return
    provider = get_provider()
    if provider is None or not isinstance(provider, ClaudeProvider):
        return
    await provider.prewarm_cache(system_prompt, team_static_block)


class VLLMProvider(IntelligenceProvider):
    """Tier 5.1.a — self-hosted vLLM runtime via OpenAI-compatible HTTP API.

    Same Qwen 2.5 weights as Ollama, different inference engine. PagedAttention
    + continuous batching means concurrent requests pack into the same GPU
    forward pass — only beneficial when callers actually issue concurrent
    calls (Type 3 map-reduce today; LLMCorrector / PunctuationRefiner after
    Tier 5.1.b ships).

    Mirrors the OllamaProvider / ClaudeProvider contract:
      - call() returns parsed JSON dict (or None on failure)
      - _last_call_stats set as a side effect with the same key set
      - get_usage() returns aggregate counters
    """

    def __init__(self, base_url: str = "http://localhost:8011/v1",
                 model: str = "Qwen/Qwen2.5-7B-Instruct-AWQ",
                 fast_model: str | None = None,
                 max_retries: int = 2, timeout: float = 300.0):
        super().__init__()
        # Strip trailing slashes; the path "/chat/completions" is appended
        # at call time. base_url ends with "/v1" by convention.
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.fast_model = fast_model or model
        self.max_retries = max_retries
        self.timeout = timeout
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._client = None  # async httpx client, lazy-init

    async def _get_client(self):
        import httpx
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=self.timeout, write=30.0, pool=10.0),
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=120.0,
                ),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _call_impl(self, system_prompt: str, user_prompt: str,
                         model_tier: str,
                         *,
                         model_override: str | None = None,
                         temperature_override: float | None = None,
                         max_tokens_override: int | None = None) -> dict[str, Any] | None:
        # Renamed from `call()` per plan §5.4 — base class wraps with @traceable_llm.
        self._last_call_stats = None
        call_start = time.time()
        # Phase 7a: model_override replaces tier-based selection.
        model = model_override or (self.fast_model if model_tier == "fast" else self.model)
        try:
            client = await self._get_client()
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 4096,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices") or []
            content = choices[0].get("message", {}).get("content", "") if choices else ""
            duration_ms = int((time.time() - call_start) * 1000)

            usage = data.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens", 0) or 0)
            output_tokens = int(usage.get("completion_tokens", 0) or 0)
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens

            parsed = _parse_json(content)

            self._last_call_stats = {
                "provider": "vllm",
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "parse_success": parsed is not None,
            }
            return parsed
        except Exception as e:
            duration_ms = int((time.time() - call_start) * 1000)
            self._last_call_stats = {
                "provider": "vllm",
                "model": model,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "parse_success": False,
                "error": str(e),
            }
            logger.warning(f"vLLM API call failed ({model_tier}/{model}): {e}")
            return None

    def get_usage(self) -> dict:
        return {
            "provider": "vllm",
            "model": self.model,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "cost_usd": 0.0,
        }


def get_provider() -> IntelligenceProvider | None:
    """Create the appropriate provider based on config."""
    from app.audio_preprocess.config import preprocess_config as config

    if config.INTELLIGENCE_PROVIDER == "claude":
        if not config.ANTHROPIC_API_KEY:
            logger.error("ANTHROPIC_API_KEY not set but INTELLIGENCE_PROVIDER=claude")
            return None
        # Tier 4 — read cache TTL from env (defaults to 1h, the recommended
        # setting per plan). 1h gates on the extended-cache-ttl-2025-04-11
        # beta header, set automatically by ClaudeProvider when ttl="1h".
        cache_ttl = os.getenv("CLAUDE_CACHE_TTL", "1h")
        if cache_ttl not in ("1h", "5m"):
            logger.warning(f"Unknown CLAUDE_CACHE_TTL={cache_ttl!r}, defaulting to 1h")
            cache_ttl = "1h"
        return ClaudeProvider(
            api_key=config.ANTHROPIC_API_KEY,
            haiku_model=config.CLAUDE_HAIKU_MODEL,
            sonnet_model=config.CLAUDE_SONNET_MODEL,
            max_retries=config.CLAUDE_MAX_RETRIES,
            timeout=config.CLAUDE_TIMEOUT,
            cache_ttl=cache_ttl,
        )
    elif config.INTELLIGENCE_PROVIDER == "gemini":
        if not config.GEMINI_API_KEY:
            logger.error("GEMINI_API_KEY not set but INTELLIGENCE_PROVIDER=gemini")
            return None
        return GeminiProvider(
            api_key=config.GEMINI_API_KEY,
            model=config.GEMINI_MODEL,
            max_retries=config.GEMINI_MAX_RETRIES,
            timeout=config.GEMINI_TIMEOUT,
        )
    elif config.INTELLIGENCE_PROVIDER == "groq":
        if not config.GROQ_API_KEY:
            logger.error("GROQ_API_KEY not set but INTELLIGENCE_PROVIDER=groq")
            return None
        return GroqProvider(
            api_key=config.GROQ_API_KEY,
            fast_model=config.GROQ_FAST_MODEL,
            smart_model=config.GROQ_SMART_MODEL,
            max_retries=config.GROQ_MAX_RETRIES,
            timeout=config.GROQ_TIMEOUT,
        )
    elif config.INTELLIGENCE_PROVIDER == "ollama":
        return OllamaProvider(
            base_url=config.LLM_BASE_URL or "http://localhost:11434",
            model=config.LLM_MODEL or "qwen2.5:3b-instruct",
        )
    elif config.INTELLIGENCE_PROVIDER == "vllm":
        # Tier 5.1.a — self-hosted vLLM runtime, OpenAI-compatible API.
        # Same GPU instance as Ollama; runs side-by-side during shadow A/B.
        return VLLMProvider(
            base_url=config.VLLM_BASE_URL,
            model=config.VLLM_MODEL,
            fast_model=config.VLLM_FAST_MODEL or None,
            max_retries=config.VLLM_MAX_RETRIES,
            timeout=config.VLLM_TIMEOUT,
        )
    else:
        return None


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _parse_json(text: str) -> dict | None:
    """Parse JSON from LLM response, handling markdown code blocks."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # remove opening ```json
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse JSON from LLM response: {text[:200]}...")
        return None


def format_transcript_for_llm(segments: list[dict]) -> str:
    """Convert segment dicts to readable [HH:MM] SPEAKER: text format."""
    lines = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        start = seg.get("start", 0)
        speaker = seg.get("speaker", "UNKNOWN")
        text = seg.get("text", "").strip()
        if not text:
            continue
        minutes = int(start // 60)
        seconds = int(start % 60)
        lines.append(f"[{minutes:02d}:{seconds:02d}] {speaker}: {text}")
    return "\n".join(lines)


def extract_metadata(segments: list[dict]) -> dict:
    """Extract call metadata from segments."""
    if not segments:
        return {"call_duration_minutes": 0, "speaker_count": 0, "word_count": 0}

    speakers = set()
    max_end = 0.0
    word_count = 0
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        speaker = seg.get("speaker")
        if speaker:
            speakers.add(speaker)
        end = seg.get("end", 0)
        if end > max_end:
            max_end = end
        text = seg.get("text", "")
        word_count += len(text.split())

    return {
        "call_duration_minutes": round(max_end / 60, 1),
        "speaker_count": len(speakers),
        "word_count": word_count,
    }


def is_internal_call(participants: list[dict]) -> bool | None:
    """Domain-matching pre-check for internal calls.

    Args:
        participants: List of {name, email} dicts from calendar attendees.

    Returns:
        True if internal (all same domain), False if external,
        None if uncertain (no email data).
    """
    if not participants:
        return None

    emails = [p.get("email", "") for p in participants if p.get("email")]
    if not emails:
        return None

    domains = set()
    for email in emails:
        parts = email.split("@")
        if len(parts) == 2:
            domains.add(parts[1].lower())

    if len(domains) == 1:
        return True  # All same domain = internal
    return False  # Mixed domains = external


# ---------------------------------------------------------------------------
# Prompt system/user templates
# ---------------------------------------------------------------------------

CLASSIFICATION_SYSTEM = """You are a call classifier for a B2B sales intelligence platform.
Your job is to determine whether a recorded call is an external sales call with a prospect or customer, or an internal team call.

Return ONLY valid JSON. No explanation, no markdown, no extra text."""

CLASSIFICATION_USER = """Analyse this call transcript and classify it.

TRANSCRIPT:
{formatted_transcript}

METADATA:
- Duration: {call_duration_minutes} minutes
- Speaker count: {speaker_count}

Return this exact JSON:
{{
  "call_type": "external_sales" | "internal" | "uncertain",
  "confidence": "high" | "medium" | "low",
  "reasoning": "one sentence explanation",
  "prospect_name": "name of prospect/customer if external, null if internal",
  "prospect_company": "company name if identifiable, null if not"
}}

Classification rules:
- "external_sales": call involves someone being sold to, discovery questions asked, product demonstrated, pricing discussed, or participants clearly from different organisations
- "internal": team standup, planning session, internal review, all participants appear to be from same organisation
- "uncertain": cannot determine with confidence

If uncertain, default to "external_sales" — it is better to over-include than to miss a genuine sales call."""


CRM_EXTRACTION_SYSTEM = """You are a CRM data extractor for a B2B sales intelligence platform.
Extract structured sales data from call transcripts.
Be precise and conservative — only extract what is explicitly stated or strongly implied. Do not invent or infer beyond what is said.
Return ONLY valid JSON. No explanation, no markdown, no extra text."""

CRM_EXTRACTION_USER = """Extract CRM data from this sales call transcript.

TRANSCRIPT:
{formatted_transcript}

KNOWN CONTEXT:
- Rep name: {rep_name}
- Prospect name: {prospect_name}
- Prospect company: {prospect_company}
- Call date: {call_date}
- Call duration: {call_duration_minutes} minutes

Return this exact JSON:
{{
  "contact": {{
    "name": "full name of prospect",
    "email": "email if mentioned in conversation, null if not",
    "company": "company name",
    "role": "job title if mentioned, null if not",
    "phone": "phone number if mentioned, null if not"
  }},
  "deal": {{
    "name": "prospect company — product/service discussed",
    "stage": "awareness|interest|consideration|intent|evaluation|purchase",
    "budget_range": "budget if mentioned, null if not mentioned",
    "timeline": "when they want to implement/buy, null if not mentioned",
    "deal_size_confidence": "high|medium|low|unknown"
  }},
  "call_details": {{
    "call_outcome": "progressed|stalled|lost|won|follow_up_needed|unclear",
    "sentiment": "positive|neutral|negative|mixed",
    "next_meeting_date": "if scheduled during call, null if not"
  }},
  "action_items": [
    {{
      "task": "specific task description",
      "owner": "rep|prospect|both",
      "due_date": "specific date if mentioned, null if not",
      "priority": "high|medium|low"
    }}
  ],
  "next_steps": [
    "specific next step 1",
    "specific next step 2"
  ],
  "key_people_mentioned": [
    {{
      "name": "person name",
      "role": "their role",
      "relevance": "decision_maker|influencer|end_user|other"
    }}
  ]
}}

If a field cannot be determined from the transcript, use null.
Do not guess. Conservative extraction is better than wrong data."""


INTELLIGENCE_SYSTEM = """You are a sales intelligence analyst for a B2B SaaS company.
Your job is to extract deep intelligence signals from sales call transcripts that help founders and sales teams understand their market, customers, and product-market fit.

Be specific and quote directly from the transcript where possible.
Rank items by importance and frequency.
Distinguish between what was explicitly said vs strongly implied.
Return ONLY valid JSON. No explanation, no markdown, no extra text."""

INTELLIGENCE_USER = """Analyse this sales call transcript and extract intelligence signals.

TRANSCRIPT:
{formatted_transcript}

CONTEXT:
- Prospect: {prospect_name} at {prospect_company}
- Prospect role: {prospect_role}
- Prospect industry: {prospect_industry}
- Rep: {rep_name}
- Call date: {call_date}

Return this exact JSON:

{{
  "pain_points": [
    {{
      "pain": "specific pain point description",
      "severity": "critical|high|medium|low",
      "exact_quote": "direct quote from transcript if available, null if inferred",
      "timestamp": "approximate timestamp e.g. 00:04:22, null if not determinable",
      "speaker_name": "name of the person who mentioned this",
      "explicitly_stated": true
    }}
  ],
  "desired_outcomes": [
    {{
      "outcome": "what the prospect wants to achieve",
      "importance": "critical|high|medium|low",
      "exact_quote": "direct quote if available, null if inferred",
      "timestamp": "approximate timestamp, null if not determinable",
      "speaker_name": "name of the person who mentioned this",
      "explicitly_stated": true
    }}
  ],
  "feature_requests": [
    {{
      "feature": "specific feature or capability requested",
      "use_case": "why they need it",
      "exact_quote": "direct quote if available, null if inferred",
      "timestamp": "approximate timestamp, null if not determinable",
      "speaker_name": "name of the person who mentioned this",
      "urgency": "blocking|important|nice_to_have",
      "explicitly_stated": true
    }}
  ],
  "competitors_mentioned": [
    {{
      "competitor": "competitor name",
      "context": "evaluating|currently_using|previously_used|just_mentioned",
      "sentiment_toward_competitor": "positive|negative|neutral",
      "exact_quote": "direct quote if available, null if inferred",
      "timestamp": "approximate timestamp, null if not determinable",
      "speaker_name": "name of the person who mentioned this",
      "what_they_like": "what prospect likes about competitor, null if not mentioned",
      "what_they_dislike": "what prospect dislikes about competitor, null if not mentioned"
    }}
  ],
  "objections": [
    {{
      "objection": "specific objection raised",
      "category": "pricing|timing|technical|trust|competitor|internal|other",
      "how_handled": "resolved|partially_resolved|unresolved|not_addressed",
      "exact_quote": "direct quote if available, null if inferred",
      "timestamp": "approximate timestamp, null if not determinable",
      "speaker_name": "name of the person who raised this"
    }}
  ],
  "buying_signals": [
    {{
      "signal": "specific buying signal observed",
      "strength": "strong|moderate|weak",
      "exact_quote": "direct quote if available, null if inferred",
      "timestamp": "approximate timestamp, null if not determinable"
    }}
  ],
  "buy_signal": {{
    "has_committed_next_step": false,
    "next_step_type": "demo_booked|trial_started|pricing_requested|follow_up_confirmed|none",
    "evidence": "exact quote showing the commitment, or null if no committed next step"
  }},
  "red_flags": [
    {{
      "flag": "specific concern or warning sign",
      "severity": "critical|high|medium|low",
      "exact_quote": "direct quote if available, null if inferred"
    }}
  ],
  "tech_stack_mentioned": [
    {{
      "tool": "tool or technology name",
      "category": "crm|communication|productivity|dev|analytics|other",
      "usage": "currently_using|evaluating|previously_used|just_mentioned"
    }}
  ],
  "budget_signals": {{
    "budget_mentioned": false,
    "amount": null,
    "currency": null,
    "budget_holder": null,
    "budget_timeline": null,
    "budget_constraint_mentioned": false
  }},
  "decision_process": {{
    "decision_makers": [],
    "decision_timeline": null,
    "evaluation_criteria": [],
    "approval_process": null
  }},
  "product_perception": {{
    "positive_signals": [
      {{
        "aspect": "what they reacted positively to",
        "exact_quote": "direct quote if available, null if inferred"
      }}
    ],
    "negative_reactions": [],
    "confusion_points": []
  }}
}}

Rules:
1. Only include items actually present in the transcript
2. Use null for missing fields, empty arrays [] for no items
3. Quotes must be verbatim from transcript
4. Timestamps are approximate — use segment start times
5. If a field is uncertain, use explicitly_stated: false
6. speaker_name should be the name of the person who spoke — use the speaker labels from the transcript (these may be real names or SPEAKER_XX labels)"""


PMF_SYSTEM = """You are a product-market fit analyst advising early-stage B2B SaaS founders. You specialise in detecting subtle signals of product-market fit (or lack thereof) from sales conversations.

Your analysis goes beyond what was explicitly said — you look for patterns, emotional cues, language choices, and behavioural signals that indicate whether a product truly resonates with the market.

Be honest and specific. Do not be falsely positive.
Founders need accurate signals, not flattery.
Return ONLY valid JSON. No explanation, no markdown, no extra text."""

PMF_USER = """Analyse this sales call for product-market fit signals.

TRANSCRIPT:
{formatted_transcript}

CONTEXT:
- Prospect: {prospect_name} at {prospect_company}
- Prospect role: {prospect_role}
- Prospect industry: {prospect_industry}
- Rep: {rep_name}
- Call date: {call_date}
- Product being sold: Memoir — AI sales call intelligence platform that records calls, transcribes them, extracts intelligence, and auto-fills CRM

INTELLIGENCE CONTEXT (from prior analysis):
{intelligence_summary}

Return this exact JSON:

{{
  "pull_score": {{
    "score": 5,
    "evidence": "specific evidence from transcript supporting this score",
    "signals": ["specific pull signal observed"]
  }},
  "urgency_level": {{
    "level": "medium",
    "evidence": "what in the conversation indicates this urgency level",
    "urgency_drivers": ["specific thing driving urgency"]
  }},
  "language_ownership": {{
    "detected": false,
    "evidence": "is prospect using product language naturally or still generic language",
    "examples": ["specific example"]
  }},
  "genuine_pain_fit": {{
    "score": 5,
    "assessment": "does this prospect have the exact pain the product solves",
    "evidence": "specific evidence from transcript"
  }},
  "hesitation_signals": [
    {{
      "signal": "specific hesitation observed",
      "likely_cause": "what is probably causing this hesitation",
      "severity": "blocking|significant|minor",
      "exact_quote": null
    }}
  ],
  "icp_fit": {{
    "is_icp": true,
    "icp_score": 5,
    "fit_assessment": "brief assessment of how well this prospect matches ideal customer",
    "icp_gaps": []
  }},
  "unprompted_advocacy": {{
    "detected": false,
    "examples": ["specific moment where prospect mentioned telling others about the product or recommending it"]
  }},
  "unexpected_insights": [
    {{
      "insight": "something surprising observed in the call",
      "implication": "what this might mean for product or go-to-market strategy"
    }}
  ],
  "overall_pmf_signal": {{
    "signal": "neutral",
    "confidence": "medium",
    "one_line_summary": "single sentence capturing the most important PMF signal from this call",
    "key_evidence": ["top 3 pieces of evidence"]
  }},
  "deal_momentum": {{
    "direction": "accelerating|steady|decelerating|stalled",
    "vs_previous_call": "better|same|worse|first_call",
    "evidence": "what changed since previous call, or N/A for first call"
  }},
  "coaching_insight": {{
    "for_rep": "one specific actionable thing the rep could do better",
    "missed_opportunity": null
  }}
}}

Scoring guide:
- pull_score: 1-3 = passive/polite. 4-6 = genuine interest. 7-8 = leaning in, asking when/how. 9-10 = already selling internally.
- genuine_pain_fit: 1-3 = nice to have. 4-6 = real pain not critical. 7-8 = significant, actively seeking. 9-10 = critical blocker.
- icp_score: 1-3 = wrong segment. 4-6 = partial fit. 7-8 = good fit minor gaps. 9-10 = exactly our target.
- deal_momentum: If this is a follow-up call, compare engagement level vs previous call.

CRITICAL RULES:
1. Every score MUST have specific evidence from the transcript.
2. If you cannot find evidence, score it low and say why.
3. Quotes must be VERBATIM — do not paraphrase and present as quotes.
4. Use the full range. Not everything is 5-7. Use 1-3 for weak signals.

Analysis guidelines:
- Pull score: Are they asking WHEN not IF? Are they leaning in?
- Language ownership: Do they say "we need this" or "we need a tool like this"?
- Genuine pain fit: Would this prospect's life be measurably better with this product?
- Unprompted advocacy: Did the prospect mention sharing or recommending without being asked?
- Unexpected insights: Anything surprising that the founder should know about
- Be specific — vague assessments are not useful to founders"""


SUMMARY_SYSTEM = """You are Memoir's meeting intelligence engine. Your job is to read a meeting transcript and produce a structured summary that helps people understand what happened, what was decided, and what needs to happen next — without them having to re-read the transcript or re-attend the meeting.

You are not a transcription tool. You are not compressing the transcript. You are extracting signal from noise.

Your output must be a single valid JSON object that matches the schema provided. No preamble. No explanation. No markdown. No backticks. Just the JSON.

## Your job for each section:

### tldr (30-Second Summary)
The TL;DR must answer these four questions in order:
1. What is the situation or risk? (include churn risk, deal stage, tension if present)
2. What is the most important thing that was decided or proposed?
3. What are the concrete next steps — named owner + date?
4. What is the open dependency that could change the outcome?

If the call involves churn risk, cancellation intent, or a disputed commitment — surface it explicitly. Do NOT neutralise stakes.
WRONG: "The meeting addressed concerns about integration issues."
RIGHT: "Customer stated they will not renew unless two conditions are met by [deadline]: technical issue fixed and integration beta delivered."

IMPORTANT: ONLY use information from the transcript provided below. Do NOT reference any other meetings, calls, contexts, or examples from these instructions. Every name, company, number, and fact in your output must come from the transcript.

A TL;DR that omits the stakes has failed its purpose. Write 3–4 sentences in past tense for someone who missed the meeting and has 10 seconds to get up to speed. No filler phrases like "the team discussed" or "it was noted that." Be direct.

### decisions
Extract only things that were actually agreed upon — not discussed, not proposed, but decided. If two people agreed on something and the conversation moved forward treating it as settled, that's a decision. Include a short title (5–8 words) and a detail sentence that captures the specifics precisely. Numbers, names, and dates belong in the detail — they matter.

A decision is ONLY firm if both parties treated it as settled and moved on.

Test before placing in Decisions:
"Could this still not happen?" → If YES, it goes to open_questions, not decisions.

WRONG (listing as Decision):
"Customer agreed to 60-day extension"
← WRONG if customer said "I'm not saying yes or no yet"

CORRECT (listing as open_questions):
"Extension pending — customer presenting proposal to CFO this week. Decision expected by [date]. Customer stated: if either condition is missed by the deadline, they will not renew."

Words that signal a conditional (not a decision):
"if", "assuming", "provided that", "once X happens", "pending approval", "subject to", "contingent on"

If any of these words qualify the agreement, it belongs in open_questions with the specific condition stated.

### open_questions
The test for open_questions is: "After the call ended, does this still need something to happen?"

Raised AND answered on the call → NOT unresolved. Put it in the relevant discussion thread instead.
Raised AND answered with "I'll check / get back to you / TBD" → IS unresolved.
Decision made but requires 3rd party approval → IS unresolved. State the specific approval needed and expected date.

WRONG: listing "GDPR compliance check" as unresolved when the consent banner mechanism was fully explained on the call.
CORRECT: listing "GDPR compliance check" as unresolved when the customer said "I'll check with our lawyer" and no date was given.

The question is never "was this discussed?" The question is always "is this closed?"

Also include: unassigned tasks, questions nobody answered, topics that ended with "we'll figure that out later", and things that have no owner and no date. If it's unresolved, it belongs here — not buried in a discussion thread where it looks handled.

### action_items
An action item requires three things: a clear task, a named owner, and (implicitly or explicitly) a timeframe. Extract only real commitments — not vague intentions.

For urgency, use these definitions:
- today: must happen today or tomorrow (explicit deadline, blocks something starting soon)
- this_week: needs to happen within the next 5 business days
- next_week: mentioned for next week or the following week
- upcoming: no specific date but clearly not urgent
- async: assigned to someone who wasn't on the call, or explicitly flagged as non-urgent

For the context field: only populate this if there's a dependency or a "why this matters" that isn't obvious from the task itself. Example: "Blocks campaign launch" or "Client billing starts tomorrow." If the task is self-explanatory, leave context null.

For the blocking field: only populate if someone in the meeting explicitly stated that this task blocks another. Use a short phrase.

Action items MUST include people who were NOT on the call if they were assigned a task or are a dependency for a decision. "I need to take this to my CFO for approval" →
Owner: CFO name if mentioned, otherwise "CFO (via [person on call])"
Task: Approve [specific proposal]
Urgency: based on stated deadline
Context: "Not on this call — assigned by [person on call]"

Never omit a stakeholder because they were absent. Their absence is irrelevant. Their role in what happens next is what matters. If a third party made a past commitment that is now disputed (e.g. a sales rep who sent an email) — that person must appear in the relevant discussion thread with the specific claim attributed to them.

### discussion_threads
A discussion thread is a topic the meeting spent meaningful time on. It's not every agenda item — it's the conversations that had substance, conflict, nuance, or consequence.

Priority rules:
- high: a decision was made, or an action item with Today/This Week urgency came out of it, or it was the longest conversation
- medium: important but not urgent, or produced upcoming action items
- low: mentioned but brief, or informational only

For the summary field: write what actually happened in the discussion, not what was on the agenda. Include the core tension or question, what was explored, and how it landed. 2–4 sentences. No hedging.

For attributions: only include when knowing who said it matters — key proposals, significant objections, named commitments. Don't attribute routine participation. Speaker action examples: "proposed", "raised concern", "flagged", "confirmed", "challenged", "committed to", "objected to."

If a thread has no meaningful attributions (e.g. it was a purely informational update with no debate), omit the attributions array entirely or return an empty array.

For calls longer than 20 minutes: extract a minimum of 3 discussion threads. If you have fewer than 3, you have under-extracted — go back and look for distinct topics.

Each thread represents ONE specific topic. Do not merge topics. Topics that always get their own thread if present:
- A specific technical bug or product failure
- A disputed commitment or misrepresentation
- A commercial negotiation (pricing, seats, contract terms)
- A named competitor threat
- An adoption or usage problem
- A renewal or cancellation discussion

Do NOT use generic thread titles:
WRONG: "Contract Issues"
RIGHT: "Disputed Gong Integration Commitment — Ritu Sharma Email, Sept 2025"
WRONG: "Zoho Webhook Issue and Gong Integration" (two topics merged)
RIGHT: Two separate threads.

### participants
For each participant, mark:
- present: true/false
- absence_reason: if known from context (e.g. "on holiday", "joined late"), include it. Otherwise null.

## High-Value Signals — Always Extract If Present

CHURN / RISK SIGNALS (always surface in TL;DR if present):
- Intent to cancel, not renew, or switch to a competitor
- Named competitor being evaluated or actively in contact
- NPS score, satisfaction rating, or explicit dissatisfaction
- Missed commitment or broken promise from previous interaction

COMMERCIAL SIGNALS:
- Annual contract value, MRR, or ARR figure mentioned
- Any pricing figure — flag if decimal seems implausible vs context
- Any discount, credit, or special term offered
- Any approval gate — person whose sign-off is needed

DISPUTED FACTS:
- Any claim that a commitment was made in a prior conversation
- Include: who made the claim, what was claimed, what evidence exists (e.g. "email dated September 4, 2025"), and current status

If ANY of the above are present → they must appear in the TL;DR AND the relevant section (decisions, open_questions, action_items, or discussion_threads). They are never optional context.

## Principles to apply throughout:

1. Specificity over vagueness. "6 ATS seats, 12-month cycle, starting tomorrow" is infinitely more useful than "billing discussed." Include numbers, names, dates, and exact terms when they appear in the transcript.

2. Separation of decided vs. open. Never put an unresolved item in the decisions section. Never bury a decision inside a discussion thread. Keep these cleanly separated.

3. Ownership on commitments. Every action item must have a named owner. If something was discussed with no named owner, it belongs in open_questions, not action_items.

4. Attribution only where it adds value. On decisions and action items, knowing who said it rarely matters. On key proposals, objections, and stakeholder positions — especially in sales calls — it matters a lot.

5. Urgency is inferred, not invented. Only assign "today" or "this_week" if there's a real signal in the transcript (an explicit deadline, a dependency on something time-sensitive, language like "first thing tomorrow"). If you're not sure, default to "upcoming."

6. Threads are prioritised by consequence, not by time spent. A 2-minute conversation that produced a $50K billing decision is high priority. A 20-minute status update with no decisions or actions is low.

7. The tldr stands alone. Someone who reads only the tldr should have a complete, accurate mental model of the meeting outcome. It should not tease — it should inform.

8. Never hallucinate. If something wasn't said in the transcript, don't include it. If an owner wasn't named, don't guess. If a decision wasn't made, don't imply it was. The reader will be comparing this summary against their memory of the call — accuracy is non-negotiable."""

SUMMARY_USER = """Meeting metadata:
- Title: {meeting_title}
- Date: {meeting_date}
- Duration: {duration_minutes} minutes
- Platform: {platform}
- Recorded by: {recorder_name}

Participants:
{participants_list}
(Format: Name | Email | Internal: yes/no | Present: yes/no | Absence reason if known)

Transcript:
{formatted_transcript}

Return only a valid JSON object matching this exact schema. No preamble. No markdown. Just JSON.

{{
  "meeting": {{
    "title": "string",
    "date": "ISO 8601",
    "duration_minutes": number,
    "platform": "google_meet | zoom | teams | other",
    "recorded_by": "string",
    "participants": [
      {{
        "name": "string",
        "email": "string",
        "is_internal": boolean,
        "is_recorder": boolean,
        "present": boolean,
        "absence_reason": "string | null"
      }}
    ]
  }},
  "summary": {{
    "tldr": "string",
    "decisions": [
      {{
        "title": "string",
        "detail": "string"
      }}
    ],
    "open_questions": [
      {{
        "text": "string"
      }}
    ],
    "action_items": [
      {{
        "owner": "string",
        "owner_email": "string",
        "text": "string",
        "context": "string | null",
        "urgency": "today | this_week | next_week | upcoming | async",
        "blocking": "string | null"
      }}
    ],
    "discussion_threads": [
      {{
        "title": "string",
        "priority": "high | medium | low",
        "summary": "string",
        "attributions": [
          {{
            "speaker": "string",
            "action": "string"
          }}
        ]
      }}
    ]
  }}
}}"""


FAQ_SYSTEM = """You are a FAQ analyst for a B2B sales team.
Extract questions asked by prospects in sales calls.
Focus on questions that reveal what prospects need to know before buying — these become your FAQ database.
Return ONLY valid JSON. No explanation, no markdown, no extra text."""

FAQ_USER = """Extract all questions asked by the prospect in this sales call.

TRANSCRIPT:
{formatted_transcript}

CONTEXT:
- Rep name: {rep_name}
- Prospect: {prospect_name} at {prospect_company}

Identify which speaker is the prospect (not the rep).

Return this exact JSON:
{{
  "questions": [
    {{
      "question": "exact or paraphrased question asked by prospect",
      "exact_quote": "verbatim quote from transcript if possible",
      "timestamp": "approximate timestamp",
      "speaker_name": "name of the person who asked this question",
      "category": "pricing|technical|security|integration|support|competitor|timeline|process|feature|other",
      "was_answered": true,
      "answer_quality": "complete|partial|unclear|not_answered",
      "rep_answer_summary": "brief summary of how rep answered, null if not answered"
    }}
  ],
  "unanswered_questions": [],
  "most_important_question": null
}}

Only include questions asked by the PROSPECT, not the rep.
Exclude small talk questions.
Include rhetorical questions only if they reveal a concern."""


# ---------------------------------------------------------------------------
# Individual prompt runners
# ---------------------------------------------------------------------------

async def run_classification(
    provider: IntelligenceProvider,
    formatted_transcript: str,
    metadata: dict,
) -> dict | None:
    """Prompt 1: Call Classification (fast tier)."""
    bundle = await get_prompt(
        "memoir-classification",
        default_system=CLASSIFICATION_SYSTEM,
        default_user_template=CLASSIFICATION_USER,
        formatted_transcript=formatted_transcript,
        call_duration_minutes=metadata.get("call_duration_minutes", 0),
        speaker_count=metadata.get("speaker_count", 0),
    )
    with prompt_name_context("memoir-classification"):
        return await _routed_call(provider, bundle, bundle.system, bundle.user, "fast")


async def run_crm_extraction(
    provider: IntelligenceProvider,
    formatted_transcript: str,
    metadata: dict,
    prospect_name: str | None,
    prospect_company: str | None,
    call_date: str | None,
    rep_name: str | None = None,
) -> dict | None:
    """Prompt 2: CRM Data Extraction (fast tier)."""
    bundle = await get_prompt(
        "memoir-crm-extraction",
        default_system=CRM_EXTRACTION_SYSTEM,
        default_user_template=CRM_EXTRACTION_USER,
        formatted_transcript=formatted_transcript,
        rep_name=rep_name or "Unknown",
        prospect_name=prospect_name or "Unknown",
        prospect_company=prospect_company or "Unknown",
        call_date=call_date or "Unknown",
        call_duration_minutes=metadata.get("call_duration_minutes", 0),
    )
    with prompt_name_context("memoir-crm-extraction"):
        return await _routed_call(provider, bundle, bundle.system, bundle.user, "fast")


async def run_intelligence_extraction(
    provider: IntelligenceProvider,
    formatted_transcript: str,
    prospect_name: str | None,
    prospect_company: str | None,
    call_date: str | None,
    prospect_role: str | None = None,
    prospect_industry: str | None = None,
    rep_name: str | None = None,
    previous_calls_context: str | None = None,
    call_number_in_deal: int | None = None,
) -> dict | None:
    """Prompt 3: Intelligence Extraction (smart tier)."""
    bundle = await get_prompt(
        "memoir-intelligence",
        default_system=INTELLIGENCE_SYSTEM,
        default_user_template=INTELLIGENCE_USER,
        formatted_transcript=formatted_transcript,
        prospect_name=prospect_name or "Unknown",
        prospect_company=prospect_company or "Unknown",
        prospect_role=prospect_role or "Unknown",
        prospect_industry=prospect_industry or "Unknown",
        rep_name=rep_name or "Unknown",
        call_date=call_date or "Unknown",
    )
    user = bundle.user
    # Inject deal context if available (post-template suffix, applies whether
    # prompt came from Hub or in-code default — both share this convention)
    if previous_calls_context:
        deal_context = (
            f"\n\n## Previous Calls in This Deal (Call #{call_number_in_deal or '?'} in sequence)\n"
            f"{previous_calls_context}\n"
            "Consider how this call builds on or diverges from previous interactions."
        )
        user += deal_context
    elif call_number_in_deal and call_number_in_deal == 1:
        user += "\n\nNote: This is the FIRST call with this prospect — no prior deal history."
    with prompt_name_context("memoir-intelligence"):
        return await _routed_call(provider, bundle, bundle.system, user, "smart")


async def run_pmf_analysis(
    provider: IntelligenceProvider,
    formatted_transcript: str,
    prospect_name: str | None,
    prospect_company: str | None,
    call_date: str | None,
    intelligence_result: dict | None,
    prospect_role: str | None = None,
    prospect_industry: str | None = None,
    rep_name: str | None = None,
    previous_calls_context: str | None = None,
    call_number_in_deal: int | None = None,
) -> dict | None:
    """Prompt 4: PMF Signals (smart tier). Uses Prompt 3 output as context."""
    # Build intelligence summary from Prompt 3 output
    intel_summary = "No prior intelligence available."
    if intelligence_result:
        parts = []
        pain_points = intelligence_result.get("pain_points", [])
        if pain_points:
            pains = [p.get("pain", "") for p in pain_points[:3]]
            parts.append(f"Pain points: {', '.join(pains)}")
        objections = intelligence_result.get("objections", [])
        if objections:
            objs = [o.get("objection", "") for o in objections[:3]]
            parts.append(f"Objections: {', '.join(objs)}")
        buying = intelligence_result.get("buying_signals", [])
        if buying:
            sigs = [b.get("signal", "") for b in buying[:3]]
            parts.append(f"Buying signals: {', '.join(sigs)}")
        buy_signal = intelligence_result.get("buy_signal", {})
        if isinstance(buy_signal, dict) and buy_signal.get("has_committed_next_step"):
            parts.append(f"Committed next step: {buy_signal.get('next_step_type', 'unknown')}")
        if parts:
            intel_summary = "\n".join(parts)

    bundle = await get_prompt(
        "memoir-pmf",
        default_system=PMF_SYSTEM,
        default_user_template=PMF_USER,
        formatted_transcript=formatted_transcript,
        prospect_name=prospect_name or "Unknown",
        prospect_company=prospect_company or "Unknown",
        prospect_role=prospect_role or "Unknown",
        prospect_industry=prospect_industry or "Unknown",
        rep_name=rep_name or "Unknown",
        call_date=call_date or "Unknown",
        intelligence_summary=intel_summary,
    )
    user = bundle.user
    # Inject deal context if available (post-template suffix, same in Hub or default)
    if previous_calls_context:
        deal_context = (
            f"\n\n## Previous Calls in This Deal (Call #{call_number_in_deal or '?'} in sequence)\n"
            f"{previous_calls_context}\n"
            "Compare engagement level with previous calls for deal_momentum assessment."
        )
        user += deal_context
    elif call_number_in_deal and call_number_in_deal == 1:
        user += "\n\nNote: This is the FIRST call with this prospect. Set deal_momentum.vs_previous_call to 'first_call'."
    with prompt_name_context("memoir-pmf"):
        return await _routed_call(provider, bundle, bundle.system, user, "smart")


async def run_summary_generation(
    provider: IntelligenceProvider,
    formatted_transcript: str,
    metadata: dict,
    prospect_name: str | None,
    prospect_company: str | None,
    call_date: str | None,
    rep_name: str | None = None,
    participants: list[dict] | None = None,
    meeting_title: str | None = None,
    meeting_date: str | None = None,
    duration_minutes: float | None = None,
    platform: str | None = None,
    recorder_name: str | None = None,
    closed_vocab_directive: str = "",
) -> dict | None:
    """Prompt 5: Summary Generation (smart tier — Sonnet).

    `closed_vocab_directive` (2026-05-13 fast-email speaker attribution):
        When non-empty, prepended to the transcript so the LLM is told the
        known participant list + rules for mapping SPEAKER_XX labels to real
        names. Used by Type 7 when `speaker_events.json` is missing but
        `participant_names` is populated. See plan §10.2.
    """
    # Build participants list string
    participants_lines = []
    for p in (participants or []):
        name = p.get("name", "Unknown")
        email = p.get("email", "")
        is_internal = "yes" if p.get("is_internal", False) else "no"
        present = "yes" if p.get("present", True) else "no"
        absence_reason = p.get("absence_reason") or ""
        participants_lines.append(f"{name} | {email} | Internal: {is_internal} | Present: {present} | {absence_reason}")
    participants_list = "\n".join(participants_lines) if participants_lines else "No participant data available"

    effective_transcript = (
        f"{closed_vocab_directive}\n\n{formatted_transcript}"
        if closed_vocab_directive
        else formatted_transcript
    )

    bundle = await get_prompt(
        "memoir-p5-summary",
        default_system=SUMMARY_SYSTEM,
        default_user_template=SUMMARY_USER,
        meeting_title=meeting_title or "Unknown",
        meeting_date=meeting_date or call_date or "Unknown",
        duration_minutes=duration_minutes or metadata.get("call_duration_minutes", 0),
        platform=platform or "other",
        recorder_name=recorder_name or rep_name or "Unknown",
        participants_list=participants_list,
        formatted_transcript=effective_transcript,
    )
    with prompt_name_context("memoir-p5-summary"):
        return await _routed_call(provider, bundle, bundle.system, bundle.user, "smart")


async def run_faq_extraction(
    provider: IntelligenceProvider,
    formatted_transcript: str,
    prospect_name: str | None,
    prospect_company: str | None,
    rep_name: str | None = None,
) -> dict | None:
    """Prompt 6: FAQ Extraction (fast tier)."""
    bundle = await get_prompt(
        "memoir-faq",
        default_system=FAQ_SYSTEM,
        default_user_template=FAQ_USER,
        formatted_transcript=formatted_transcript,
        rep_name=rep_name or "Unknown",
        prospect_name=prospect_name or "Unknown",
        prospect_company=prospect_company or "Unknown",
    )
    with prompt_name_context("memoir-faq"):
        return await _routed_call(provider, bundle, bundle.system, bundle.user, "fast")


# ---------------------------------------------------------------------------
# Validation functions (Steps 6, 7)
# ---------------------------------------------------------------------------

def validate_quotes(intelligence_result: dict, transcript_text: str) -> tuple[dict, list]:
    """Fuzzy match AI-generated quotes against transcript. Mark unverified quotes.

    Args:
        intelligence_result: P3 intelligence extraction result.
        transcript_text: Raw transcript text (all segments joined).

    Returns:
        (validated result, list of validation issues).
    """
    from difflib import SequenceMatcher

    issues = []
    for section in ["pain_points", "feature_requests", "competitors_mentioned", "objections", "buying_signals"]:
        items = intelligence_result.get(section, [])
        if not items or not isinstance(items, list):
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            quote = item.get("exact_quote", "")
            if not quote:
                continue
            # Sliding window matching
            best_ratio = 0.0
            quote_lower = quote.lower()
            words = transcript_text.lower().split()
            quote_words = quote_lower.split()
            window_size = len(quote_words)
            if not quote_words or not words:
                continue
            for i in range(max(0, len(words) - window_size + 1)):
                window = " ".join(words[i:i + window_size + 2])
                ratio = SequenceMatcher(None, quote_lower, window).ratio()
                best_ratio = max(best_ratio, ratio)
                if best_ratio >= 0.6:
                    break
            item["quote_verified"] = best_ratio >= 0.5
            if not item["quote_verified"]:
                issues.append({
                    "field": f"{section}[{idx}].exact_quote",
                    "issue": "quote_not_found_in_transcript",
                    "match_ratio": round(best_ratio, 2),
                })
    return intelligence_result, issues


STAGE_ORDER = ["awareness", "interest", "consideration", "intent", "evaluation", "purchase"]


def validate_stage_classification(ai_stage: str, previous_stage: str | None, call_number: int) -> dict:
    """Validate and constrain AI stage classification.

    Args:
        ai_stage: Stage from P2 CRM extraction.
        previous_stage: Previous deal stage (from deal_match), or None.
        call_number: Call number in the deal (1 = first call).

    Returns:
        Dict with validated_stage, original_stage, adjusted flag, and reason.
    """
    original = ai_stage
    if not ai_stage or ai_stage not in STAGE_ORDER:
        return {
            "original_stage": original or None,
            "validated_stage": ai_stage or "awareness",
            "adjusted": not ai_stage,
            "reason": "unknown_stage_defaulted" if not ai_stage else None,
        }

    ai_idx = STAGE_ORDER.index(ai_stage)

    # First call: cap at "interest"
    if call_number <= 1:
        if ai_idx > 1:
            return {
                "original_stage": original,
                "validated_stage": STAGE_ORDER[min(ai_idx, 1)],
                "adjusted": True,
                "reason": "First call cap",
            }

    if previous_stage and previous_stage in STAGE_ORDER:
        prev_idx = STAGE_ORDER.index(previous_stage)
        # No regression > 1 step
        if ai_idx < prev_idx - 1:
            return {
                "original_stage": original,
                "validated_stage": STAGE_ORDER[prev_idx - 1],
                "adjusted": True,
                "reason": "Stage regression prevented",
            }
        # No jump > 2 steps
        if ai_idx > prev_idx + 2:
            return {
                "original_stage": original,
                "validated_stage": STAGE_ORDER[prev_idx + 2],
                "adjusted": True,
                "reason": "Large jump capped",
            }

    return {
        "original_stage": original,
        "validated_stage": ai_stage,
        "adjusted": False,
        "reason": None,
    }


def validate_pmf_output(pmf_data: dict, call_number: int) -> tuple[dict, list]:
    """Validate PMF output -- downgrade scores without evidence.

    Args:
        pmf_data: P4 PMF analysis result.
        call_number: Call number in deal.

    Returns:
        (validated pmf_data, list of validation issues).
    """
    issues = []
    if not pmf_data or not isinstance(pmf_data, dict):
        return pmf_data or {}, issues

    # Safely extract nested dicts
    pull_score = pmf_data.get("pull_score", {})
    if not isinstance(pull_score, dict):
        pull_score = {}
    pain_fit = pmf_data.get("genuine_pain_fit", {})
    if not isinstance(pain_fit, dict):
        pain_fit = {}
    icp_fit = pmf_data.get("icp_fit", {})
    if not isinstance(icp_fit, dict):
        icp_fit = {}
    overall = pmf_data.get("overall_pmf_signal", {})
    if not isinstance(overall, dict):
        overall = {}

    # Rule 1: pull_score > 7 with no signals -> cap at 6
    ps_score = pull_score.get("score")
    if isinstance(ps_score, (int, float)) and ps_score > 7:
        if not pull_score.get("signals"):
            pull_score["score"] = min(int(ps_score), 6)
            pull_score["evidence"] = (pull_score.get("evidence") or "") + " [Adjusted: insufficient signal evidence]"
            issues.append({"field": "pull_score", "issue": "high_score_no_signals", "adjusted_to": 6})

    # Rule 2: overall strong_positive but pull_score < 5 -> downgrade to positive
    ps_score = pull_score.get("score")
    if overall.get("signal") == "strong_positive" and isinstance(ps_score, (int, float)) and ps_score < 5:
        overall["signal"] = "positive"
        overall["confidence"] = "low"
        issues.append({"field": "overall_pmf_signal", "issue": "strong_positive_low_pull", "adjusted_to": "positive"})

    # Rule 3: First call with pull_score 9-10 -> cap at 8
    ps_score = pull_score.get("score")
    if call_number == 1 and isinstance(ps_score, (int, float)) and ps_score >= 9:
        pull_score["score"] = 8
        pull_score["evidence"] = (pull_score.get("evidence") or "") + " [Adjusted: first call cap]"
        issues.append({"field": "pull_score", "issue": "first_call_cap", "adjusted_to": 8})

    # Rule 4: Clamp all scores to 1-10
    for d, key in [(pull_score, "score"), (pain_fit, "score"), (icp_fit, "icp_score")]:
        val = d.get(key)
        if isinstance(val, (int, float)):
            d[key] = max(1, min(10, int(val)))

    # Write back
    pmf_data["pull_score"] = pull_score
    pmf_data["genuine_pain_fit"] = pain_fit
    pmf_data["icp_fit"] = icp_fit
    pmf_data["overall_pmf_signal"] = overall

    return pmf_data, issues


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _collect_prompt_log(
    provider: IntelligenceProvider,
    prompt_name: str,
    prompt_number: int,
    validation_issues: list | None = None,
) -> dict:
    """Collect per-prompt stats from the provider's last call."""
    stats = provider.get_last_call_stats() or {}
    entry = {
        "prompt_number": prompt_number,
        "prompt_name": prompt_name,
        "provider": stats.get("provider", "unknown"),
        "model": stats.get("model", "unknown"),
        "input_tokens": stats.get("input_tokens", 0),
        "output_tokens": stats.get("output_tokens", 0),
        "cache_read_tokens": stats.get("cache_read_tokens", 0),
        # Tier 4.0.a — emit cache_creation_tokens so cost analytics can
        # split cache writes (1.25× / 2× base for Claude) from base input
        # (1×) and cache reads (0.1×). Ollama/OpenAI providers populate 0.
        "cache_creation_tokens": stats.get("cache_creation_tokens", 0),
        "cost_usd": stats.get("cost_usd", 0.0),
        "duration_ms": stats.get("duration_ms", 0),
        "parse_success": stats.get("parse_success", False),
        "validation_issues": validation_issues,
    }
    return entry


async def run_full_pipeline(
    segments: list[dict],
    participants: list[dict] | None = None,
    rep_name: str | None = None,
    call_date: str | None = None,
    team_id: str | None = None,
    meeting_id: str | None = None,
    skip_summary: bool = False,
    job_id: str | None = None,
) -> dict | None:
    """Run the full 6-prompt intelligence pipeline.

    Args:
        segments: Transcript segments from ASR + diarization.
        participants: Calendar attendees [{name, email}] for internal detection.
        rep_name: Name of the sales rep (from calendar organizer).
        call_date: ISO date string of the call.
        team_id: Team UUID (for deal matching).
        meeting_id: Meeting UUID (for deal context queries).

    Returns:
        Combined dict with all 6 prompt outputs + usage stats,
        or None if provider not configured.
    """
    provider = get_provider()
    if provider is None:
        return None

    start_time = time.time()
    prompt_log = []

    # Pre-process transcript
    formatted_transcript = format_transcript_for_llm(segments)
    metadata = extract_metadata(segments)
    # Build raw transcript text for quote validation
    transcript_text = " ".join(
        seg.get("text", "") for seg in segments if isinstance(seg, dict)
    )

    if not formatted_transcript or metadata["word_count"] < 200:
        logger.info("Transcript too short for intelligence pipeline (%d words, need 200), skipping", metadata["word_count"])
        return {
            "call_type": "skipped",
            "reason": "transcript_too_short",
            "word_count": metadata["word_count"],
        }

    # Step 0: Internal call pre-check (code, not AI)
    internal = is_internal_call(participants or [])
    if internal is True:
        logger.info("Internal call detected via domain matching, skipping pipeline")
        return {"call_type": "internal", "detection_method": "domain_match"}

    # Helper for progress updates (meeting-level for split pipeline)
    async def _progress(pct: int, label: str) -> None:
        if meeting_id:
            from app.job_processor.progress import update_meeting_progress
            await update_meeting_progress(meeting_id, 3, pct, label)
        elif job_id:
            from app.job_processor.progress import update_progress
            await update_progress(job_id, pct, label)

    # Step 1: Classification (only if uncertain — no email metadata)
    await _progress(15, "Classifying call (P1)...")
    prospect_name = None
    prospect_company = None

    if internal is None:
        logger.info("[Pipeline] Starting P1 classification...")
        classification = await run_classification(provider, formatted_transcript, metadata)
        logger.info("[Pipeline] P1 classification done")
        prompt_log.append(_collect_prompt_log(provider, "P1_classification", 1))
        if classification:
            if classification.get("call_type") == "internal":
                logger.info("Internal call detected via AI classification, skipping pipeline")
                return {
                    "call_type": "internal",
                    "detection_method": "ai_classification",
                    "classification": classification,
                    "prompt_log": prompt_log,
                    **provider.get_usage(),
                }
            prospect_name = classification.get("prospect_name")
            prospect_company = classification.get("prospect_company")
    else:
        # External call confirmed by domain matching, still run classification for names
        logger.info("[Pipeline] Starting P1 classification...")
        classification = await run_classification(provider, formatted_transcript, metadata)
        logger.info("[Pipeline] P1 classification done")
        prompt_log.append(_collect_prompt_log(provider, "P1_classification", 1))
        if classification:
            prospect_name = classification.get("prospect_name")
            prospect_company = classification.get("prospect_company")

    call_type = "external_sales"

    # Step 2: Run P2 (CRM extraction) first — extracts prospect_role/industry
    await _progress(25, "Extracting CRM data (P2)...")
    logger.info("[Pipeline] Starting P2 CRM extraction...")
    crm_result = await run_crm_extraction(
        provider, formatted_transcript, metadata,
        prospect_name, prospect_company, call_date, rep_name,
    )
    logger.info("[Pipeline] P2 CRM extraction done")
    prompt_log.append(_collect_prompt_log(provider, "P2_crm_extraction", 2))

    # Extract prospect_role and prospect_industry from CRM result
    prospect_role = None
    prospect_industry = None
    prospect_email = None
    if crm_result:
        contact = crm_result.get("contact") or {}
        if isinstance(contact, dict):
            prospect_role = contact.get("role")
            prospect_email = contact.get("email")
            # Update prospect_name/company from P2 if P1 didn't get them
            if not prospect_name:
                prospect_name = contact.get("name")
            if not prospect_company:
                prospect_company = contact.get("company")
        # Industry may be inferred from company context
        prospect_industry = crm_result.get("industry")

    # ---------------------------------------------------------------
    await _progress(35, "Matching deals...")
    # Step 3: Deal matching (after P2, before P3/P5)
    # ---------------------------------------------------------------
    deal_match = None
    previous_calls_context = None
    call_number_in_deal = 1
    previous_stage = None

    # Threading gate — when DEAL_THREADING_ENABLED=false (default), skip the
    # matcher entirely. Defaults above flow through: deal_match=None → P3/P4
    # treat as first call, intelligence.json gets "deal_match": null, and
    # core_api.process_deal_match short-circuits on the webhook side. No DB
    # writes to memoir_deals / deal_match_log. Re-enable via env flip.
    from app.audio_preprocess.config import preprocess_config as _cfg_threading
    threading_enabled = _cfg_threading.DEAL_THREADING_ENABLED

    if threading_enabled and team_id and (prospect_company or prospect_email):
        try:
            from .deal_matcher import match_call_to_deal
            deal_match = await match_call_to_deal(
                team_id=team_id,
                extracted_data=crm_result or {},
                current_meeting_id=meeting_id,
            )
            if deal_match:
                previous_calls_context = deal_match.get("previous_calls_context")
                call_number_in_deal = deal_match.get("call_number_in_deal", 1)
                # Get previous stage for stage validation later
                if deal_match.get("matched_deal_id"):
                    # Query previous stage from the matched deal
                    try:
                        from .db import get_pool
                        pool = get_pool()
                        row = await pool.fetchval(
                            "SELECT current_stage FROM memoir_deals WHERE id = $1::uuid",
                            deal_match["matched_deal_id"],
                        )
                        previous_stage = row
                    except Exception:
                        pass
                logger.info(
                    "Deal match: method=%s deal=%s call#=%d",
                    deal_match.get("match_method"),
                    deal_match.get("matched_deal_id"),
                    call_number_in_deal,
                )
        except Exception as e:
            logger.warning(f"Deal matching failed: {e}")

    # ---------------------------------------------------------------
    # P3 (Intelligence) — sequential
    # ---------------------------------------------------------------
    await _progress(40, "Extracting intelligence (P3)...")
    logger.info("[Pipeline] Starting P3 intelligence extraction...")
    intelligence_result = await run_intelligence_extraction(
        provider, formatted_transcript,
        prospect_name, prospect_company, call_date,
        prospect_role=prospect_role,
        prospect_industry=prospect_industry,
        rep_name=rep_name,
        previous_calls_context=previous_calls_context,
        call_number_in_deal=call_number_in_deal,
    )
    logger.info("[Pipeline] P3 intelligence extraction done")
    prompt_log.append(_collect_prompt_log(provider, "P3_intelligence", 3))

    # ---------------------------------------------------------------
    # Step 6: Quote validation (after P3, before P4)
    # ---------------------------------------------------------------
    quote_validation_issues = []
    if intelligence_result and isinstance(intelligence_result, dict):
        intelligence_result, quote_validation_issues = validate_quotes(
            intelligence_result, transcript_text,
        )
        if quote_validation_issues:
            logger.info(
                "Quote validation: %d issues found",
                len(quote_validation_issues),
            )

    # ---------------------------------------------------------------
    # P4 (PMF) — uses P2 data (prospect_role) + P3 data (intelligence_summary)
    # ---------------------------------------------------------------
    await _progress(55, "Analyzing PMF signals (P4)...")
    logger.info("[Pipeline] Starting P4 PMF analysis...")
    pmf_result = await run_pmf_analysis(
        provider, formatted_transcript,
        prospect_name, prospect_company, call_date,
        intelligence_result,
        prospect_role=prospect_role,
        prospect_industry=prospect_industry,
        rep_name=rep_name,
        previous_calls_context=previous_calls_context,
        call_number_in_deal=call_number_in_deal,
    )
    logger.info("[Pipeline] P4 PMF analysis done")
    pmf_validation_issues = []
    if pmf_result and isinstance(pmf_result, dict):
        pmf_result, pmf_validation_issues = validate_pmf_output(
            pmf_result, call_number_in_deal,
        )
    prompt_log.append(_collect_prompt_log(
        provider, "P4_pmf", 4,
        validation_issues=pmf_validation_issues or None,
    ))

    # ---------------------------------------------------------------
    # Step 7: Stage classification validation
    # ---------------------------------------------------------------
    stage_validation = None
    ai_stage = None
    if crm_result:
        deal_data = crm_result.get("deal") or {}
        if isinstance(deal_data, dict):
            ai_stage = deal_data.get("stage")
    if ai_stage:
        stage_validation = validate_stage_classification(
            ai_stage, previous_stage, call_number_in_deal,
        )
        if stage_validation.get("adjusted"):
            logger.info(
                "Stage validation: %s -> %s (%s)",
                stage_validation["original_stage"],
                stage_validation["validated_stage"],
                stage_validation.get("reason"),
            )

    # P6 (FAQ)
    await _progress(70, "Extracting FAQs (P6)...")
    logger.info("[Pipeline] Starting P6 FAQ extraction...")
    faq_result = await run_faq_extraction(
        provider, formatted_transcript,
        prospect_name, prospect_company, rep_name,
    )
    logger.info("[Pipeline] P6 FAQ extraction done")
    prompt_log.append(_collect_prompt_log(provider, "P6_faq", 6))

    # ---------------------------------------------------------------
    # P5 (Summary) — runs last to avoid starving P6 with large context
    # ---------------------------------------------------------------
    summary_result = None
    if not skip_summary:
        logger.info("[Pipeline] Starting P5 summary generation...")
        summary_result = await run_summary_generation(
            provider, formatted_transcript, metadata,
            prospect_name, prospect_company, call_date, rep_name,
            participants=participants,
            meeting_title=prospect_company or prospect_name or None,
            meeting_date=call_date,
            duration_minutes=metadata.get("call_duration_minutes"),
            platform=None,
            recorder_name=rep_name,
        )
        logger.info("[Pipeline] P5 summary generation done")
        prompt_log.append(_collect_prompt_log(provider, "P5_summary", 5))

    elapsed = round(time.time() - start_time, 2)
    usage = provider.get_usage()

    # Extract key scores for top-level access
    pull_score = None
    icp_fit_score = None
    pain_fit_score = None
    overall_pmf_signal = None
    call_health = None
    overall_confidence = None
    deal_momentum = None

    if pmf_result:
        ps = pmf_result.get("pull_score", {})
        pull_score = ps.get("score") if isinstance(ps, dict) else None
        gp = pmf_result.get("genuine_pain_fit", {})
        pain_fit_score = gp.get("score") if isinstance(gp, dict) else None
        icp = pmf_result.get("icp_fit", {})
        icp_fit_score = icp.get("icp_score") if isinstance(icp, dict) else None
        opsig = pmf_result.get("overall_pmf_signal", {})
        overall_pmf_signal = opsig.get("signal") if isinstance(opsig, dict) else None
        overall_confidence = opsig.get("confidence") if isinstance(opsig, dict) else None
        dm = pmf_result.get("deal_momentum", {})
        deal_momentum = dm.get("direction") if isinstance(dm, dict) else None

    if summary_result:
        call_health = summary_result.get("call_health")

    validated_stage = None
    if stage_validation:
        validated_stage = stage_validation.get("validated_stage")

    result = {
        "call_type": call_type,
        "classification": classification if internal is None else None,
        "crm": crm_result,
        "intelligence": intelligence_result,
        "pmf": pmf_result,
        "summary": summary_result,
        "faq": faq_result,
        # Deal matching
        "deal_match": deal_match,
        "previous_calls_context": previous_calls_context,
        # Stage validation
        "stage_validation": stage_validation,
        # Quote validation
        "quote_validation_issues": quote_validation_issues or None,
        # Top-level scores for easy access
        "pull_score": pull_score,
        "icp_fit_score": icp_fit_score,
        "pain_fit_score": pain_fit_score,
        "overall_pmf_signal": overall_pmf_signal,
        "overall_confidence": overall_confidence,
        "call_health": call_health,
        "deal_momentum": deal_momentum,
        "validated_stage": validated_stage,
        "prospect_name": prospect_name,
        "prospect_company": prospect_company,
        # Prompt logging
        "prompt_log": prompt_log,
        # Usage & timing
        "pipeline_duration_seconds": elapsed,
        **usage,
    }

    # Close provider client if applicable
    if hasattr(provider, 'close'):
        await provider.close()

    logger.info(
        f"[Pipeline] COMPLETED in {elapsed}s "
        f"(provider={usage.get('provider')}, "
        f"tokens={usage.get('total_tokens', 0)}, "
        f"cost=${usage.get('cost_usd', 0):.4f}, "
        f"deal={deal_match.get('match_method') if deal_match else 'none'}, "
        f"stage={validated_stage})"
    )

    return result


def _build_prompt_log_entry(
    stats: dict | None, prompt_name: str, prompt_number: int = 5,
) -> dict:
    """Build a prompt_log row directly from a captured _last_call_stats dict.

    Used by _run_summary_mapreduce where each chunk's stats must be captured
    immediately after that chunk's await provider.call() resolves — provider
    state is shared, so we cannot rely on _collect_prompt_log() reading
    get_last_call_stats() at the end.
    """
    s = stats or {}
    return {
        "prompt_number": prompt_number,
        "prompt_name": prompt_name,
        "provider": s.get("provider", "unknown"),
        "model": s.get("model", "unknown"),
        "input_tokens": s.get("input_tokens", 0),
        "output_tokens": s.get("output_tokens", 0),
        "cache_read_tokens": s.get("cache_read_tokens", 0),
        "cache_creation_tokens": s.get("cache_creation_tokens", 0),
        "cost_usd": s.get("cost_usd", 0.0),
        "duration_ms": s.get("duration_ms", 0),
        "parse_success": s.get("parse_success", False),
        "validation_issues": None,
    }


async def _run_summary_mapreduce(
    provider: IntelligenceProvider,
    segments: list[dict],
    metadata: dict,
    *,
    participants: list[dict] | None,
    call_date: str | None,
    rep_name: str | None,
    meeting_title: str | None,
    platform: str | None,
    duration_minutes: float | None,
    recorder_name: str | None,
    closed_vocab_directive: str = "",
) -> tuple[dict | None, list[dict]]:
    """Tier 5.1.c — split transcript into chunks, summarize each in parallel
    via asyncio.gather, then run a final reduce pass to produce the unified
    summary.

    Falls through to run_summary_generation single-call when chunking yields
    only one chunk (no benefit from map-reduce).

    Returns (parsed_summary, prompt_log_entries) where prompt_log_entries has
    one row per actual provider.call() — N chunks (+ 1 reduce when applicable).
    Each row's stats reflect that specific call. Captured per-chunk via the
    asyncio property that "between an await resolving and the next await,
    no other task interleaves" — so reading provider.get_last_call_stats()
    immediately after `await provider.call(...)` reliably yields THIS call's
    stats even though provider state is shared across concurrent chunks.
    """
    # Reuse memoir_extractor's chunker — same algorithm, already battle-tested.
    from app.audio_preprocess.services.memoir_extractor import _chunk_transcript

    chunks = _chunk_transcript(segments, max_tokens=3500, overlap_tokens=300)

    # Build participants list — same format as run_summary_generation
    participants_lines = []
    for p in (participants or []):
        name = p.get("name", "Unknown")
        email = p.get("email", "")
        is_internal = "yes" if p.get("is_internal", False) else "no"
        present = "yes" if p.get("present", True) else "no"
        absence_reason = p.get("absence_reason") or ""
        participants_lines.append(
            f"{name} | {email} | Internal: {is_internal} | Present: {present} | {absence_reason}"
        )
    participants_list = "\n".join(participants_lines) if participants_lines else "No participant data available"

    # Single-chunk early return: no map-reduce overhead on shorter calls
    if len(chunks) <= 1:
        formatted = format_transcript_for_llm(segments)
        result = await run_summary_generation(
            provider, formatted, metadata,
            prospect_name=None, prospect_company=None,
            call_date=call_date, rep_name=rep_name,
            participants=participants,
            meeting_title=meeting_title, meeting_date=call_date,
            duration_minutes=duration_minutes, platform=platform,
            recorder_name=recorder_name,
            closed_vocab_directive=closed_vocab_directive,
        )
        single_log = _collect_prompt_log(provider, "P5_summary", 5)
        return result, [single_log] if result else []

    logger.info("Tier 5.1.c summary map-reduce: %d chunks from %d segments",
                len(chunks), len(segments))

    async def _summarize_chunk(idx: int, chunk_segments: list[dict]) -> tuple[int, dict | None, dict | None]:
        chunk_formatted = format_transcript_for_llm(chunk_segments)
        chunk_note = (
            f"[NOTE: This is chunk {idx + 1} of {len(chunks)} from a longer call. "
            f"Extract everything visible in this chunk. The final summary will be assembled "
            f"by merging chunk results — do not invent context not present in this chunk.]"
        )
        chunk_body = (
            f"{closed_vocab_directive}\n\n{chunk_note}\n\n{chunk_formatted}"
            if closed_vocab_directive
            else f"{chunk_note}\n\n{chunk_formatted}"
        )
        chunk_user = SUMMARY_USER.format(
            meeting_title=meeting_title or "Unknown",
            meeting_date=call_date or "Unknown",
            duration_minutes=duration_minutes or metadata.get("call_duration_minutes", 0),
            platform=platform or "other",
            recorder_name=recorder_name or rep_name or "Unknown",
            participants_list=participants_list,
            formatted_transcript=chunk_body,
        )
        # Trace name: memoir-p5-summary (chunk #idx). Hub user-prompt wiring is
        # deferred (per-chunk content doesn't slot into get_prompt format), but
        # the call still flows through the router so a Hub `model:` override
        # on memoir-p5-summary takes effect for chunk calls too.
        from app.llm_observability import PromptBundle as _PB
        chunk_bundle = _PB(system=SUMMARY_SYSTEM, user=chunk_user, source="default")
        with prompt_name_context("memoir-p5-summary"):
            result = await _routed_call(provider, chunk_bundle, SUMMARY_SYSTEM, chunk_user, "smart")
        # Capture stats SYNCHRONOUSLY immediately after the await resolves —
        # asyncio guarantees no task interleaves between await resolution and
        # the next statement, so provider._last_call_stats still reflects
        # THIS call. Copy to dict so subsequent overwrites don't mutate it.
        last = provider.get_last_call_stats()
        stats = dict(last) if last else None
        return idx, result, stats

    # Map step: parallel summarization of all chunks
    chunk_results = await asyncio.gather(
        *[_summarize_chunk(i, c) for i, c in enumerate(chunks)],
        return_exceptions=True,
    )

    prompt_log_entries: list[dict] = []
    valid: list[dict] = []
    for item in chunk_results:
        if isinstance(item, Exception):
            logger.warning("Tier 5.1.c chunk failed with exception: %s", item)
            continue
        idx, result, stats = item
        prompt_log_entries.append(
            _build_prompt_log_entry(stats, f"P5_summary_chunk_{idx}", 5)
        )
        if isinstance(result, dict):
            valid.append(result)

    # If everything failed, try the single-call path as a last resort.
    # The fallback's stats will be appended to prompt_log_entries so cost
    # accounting reflects every actual API call.
    if not valid:
        logger.warning("Tier 5.1.c: all chunk summaries failed; falling back to single-call")
        formatted = format_transcript_for_llm(segments)
        fallback = await run_summary_generation(
            provider, formatted, metadata,
            prospect_name=None, prospect_company=None,
            call_date=call_date, rep_name=rep_name,
            participants=participants,
            meeting_title=meeting_title, meeting_date=call_date,
            duration_minutes=duration_minutes, platform=platform,
            recorder_name=recorder_name,
            closed_vocab_directive=closed_vocab_directive,
        )
        prompt_log_entries.append(_collect_prompt_log(provider, "P5_summary_fallback", 5))
        return fallback, prompt_log_entries

    # If only one chunk succeeded, return it directly (no reduce needed)
    if len(valid) == 1:
        return valid[0], prompt_log_entries

    # Reduce step: combine partial summaries into one unified summary
    reduce_user = (
        "You are combining partial summaries from different chunks of one sales call.\n\n"
        f"Meeting Title: {meeting_title or 'Unknown'}\n"
        f"Meeting Date: {call_date or 'Unknown'}\n"
        f"Duration: {duration_minutes or metadata.get('call_duration_minutes', 0)} minutes\n"
        f"Recorder: {recorder_name or rep_name or 'Unknown'}\n"
        f"Number of Partial Summaries: {len(valid)}\n\n"
        f"## PARTICIPANTS\n\n{participants_list}\n\n"
        "## PARTIAL SUMMARIES (JSON)\n\n"
        f'"""\n{json.dumps(valid, indent=2)}\n"""\n\n'
        "## YOUR TASK\n\n"
        "Produce ONE unified summary matching the same JSON schema. Rules for merging:\n"
        "- tldr: synthesize a SINGLE 3-4 sentence overview covering the entire call. "
        "Surface churn risk, deal stage, named competitors, commercial signals if present in any chunk.\n"
        "- decisions: deduplicate near-identical entries; keep the most-specific wording. "
        "Only include items that were ACTUALLY decided (no conditional commitments).\n"
        "- open_questions: deduplicate; merge similar items. Include all unresolved items "
        "across chunks even if only one chunk surfaced them.\n"
        "- action_items: deduplicate by (owner + task) similarity. Preserve named owners and urgency.\n"
        "- discussion_threads: merge threads that cover the same specific topic; keep them "
        "separate if topics are distinct (e.g. pricing vs. integration vs. churn risk).\n"
        "- participants: union across all chunks; preserve absence_reason where present.\n\n"
        "Apply the same SUMMARY_SYSTEM principles. Output a single valid JSON object. "
        "No preamble. No markdown. No backticks. Just the JSON."
    )
    # Trace name: memoir-p5-reduce. User prompt is built inline above with
    # the merged partial summaries. Hub user-template wiring deferred, but
    # router still consulted so Hub `model:` override on memoir-p5-reduce
    # takes effect.
    from app.llm_observability import PromptBundle as _PB_reduce
    reduce_bundle = _PB_reduce(system=SUMMARY_SYSTEM, user=reduce_user, source="default")
    with prompt_name_context("memoir-p5-reduce"):
        reduce_result = await _routed_call(provider, reduce_bundle, SUMMARY_SYSTEM, reduce_user, "smart")
    # No concurrent chunks running anymore, so _collect_prompt_log is safe here
    prompt_log_entries.append(_collect_prompt_log(provider, "P5_summary_reduce", 5))
    return reduce_result, prompt_log_entries


async def run_summary_only(
    segments: list[dict],
    participants: list[dict] | None = None,
    call_date: str | None = None,
    rep_name: str | None = None,
    meeting_title: str | None = None,
    platform: str | None = None,
    duration_minutes: float | None = None,
    recorder_name: str | None = None,
    closed_vocab_mode: bool = False,
    participant_names_for_closed_vocab: list[str] | None = None,
    pyannote_speaker_count: int | None = None,
) -> dict | None:
    """Run only the P5 summary generation prompt.

    Lightweight alternative to run_full_pipeline() when only a summary
    is needed (e.g. regeneration after edits).

    Tier 5.1.c — when LLM_SUMMARY_MAPREDUCE=true, transcripts that exceed
    the chunk threshold are summarized via parallel chunk-summarize + reduce
    instead of one giant prompt. Default off → existing single-call path
    runs verbatim.

    Fast-email speaker attribution (2026-05-13):
        When `closed_vocab_mode=True`, prepends a disambiguation directive to
        the transcript so the LLM is told the known participants and rules for
        mapping SPEAKER_XX labels to real names. Used by Type 7 when
        `speaker_events.json` is missing but `participant_names` is populated.
        See plan §10.2.

    Returns:
        Dict with summary result + provider usage, or None.
    """
    provider = get_provider()
    if provider is None:
        return None

    formatted_transcript = format_transcript_for_llm(segments)
    metadata = extract_metadata(segments)

    if not formatted_transcript or metadata["word_count"] < 200:
        logger.info(
            "Transcript too short for summary (%d words, need 200), skipping",
            metadata["word_count"],
        )
        return None

    # Build closed-vocab directive (§10.2 prompt) when fast-path attribution
    # could not map speakers via speaker_events.json. The directive instructs
    # the LLM to attempt the mapping from transcript content while strictly
    # avoiding hallucination (no name invention, no double-mapping).
    closed_vocab_directive = ""
    if closed_vocab_mode and (participant_names_for_closed_vocab or []):
        names = [str(n).strip() for n in (participant_names_for_closed_vocab or []) if n]
        names_joined = ", ".join(names) if names else "(none)"
        n_pyannote = (
            int(pyannote_speaker_count) if pyannote_speaker_count is not None else 0
        )
        closed_vocab_directive = (
            "[SPEAKER ATTRIBUTION RULES]\n"
            f"The transcript has {n_pyannote} pyannote-labeled speakers "
            f"(SPEAKER_00 through SPEAKER_{max(n_pyannote - 1, 0)}).\n"
            f"The known participants in this meeting are: {names_joined}.\n\n"
            "Rules for mapping SPEAKER_XX labels in your summary and discussion_threads:\n"
            "- If you can confidently determine from transcript content which SPEAKER_XX maps "
            "to which name, use the real name when referring to that speaker.\n"
            "- If you are unsure for any speaker, leave that label as SPEAKER_XX. Do not "
            "guess. Do not invent names not in the participant list above.\n"
            "- If the participant count differs from the pyannote speaker count, map only "
            "the ones you can identify with high confidence. Extras remain SPEAKER_XX.\n"
            "- Never assign one participant name to multiple SPEAKER_XX labels."
        )

    use_mapreduce = os.getenv("LLM_SUMMARY_MAPREDUCE", "false").lower() == "true"

    if use_mapreduce:
        # Map-reduce returns (result, list_of_prompt_log_entries) so per-chunk
        # stats are captured at call time rather than via _last_call_stats at
        # the end (which would reflect only the LAST call in concurrent runs).
        result, prompt_log_entries = await _run_summary_mapreduce(
            provider, segments, metadata,
            participants=participants, call_date=call_date,
            rep_name=rep_name, meeting_title=meeting_title,
            platform=platform, duration_minutes=duration_minutes,
            recorder_name=recorder_name,
            closed_vocab_directive=closed_vocab_directive,
        )
    else:
        result = await run_summary_generation(
            provider,
            formatted_transcript,
            metadata,
            prospect_name=None,
            prospect_company=None,
            call_date=call_date,
            rep_name=rep_name,
            participants=participants,
            meeting_title=meeting_title,
            meeting_date=call_date,
            duration_minutes=duration_minutes,
            platform=platform,
            recorder_name=recorder_name,
            closed_vocab_directive=closed_vocab_directive,
        )
        # Tier 4.0.b/c — single-call path emits one P5_summary row. _collect_
        # prompt_log reads provider.get_last_call_stats() which is reliable
        # because no concurrency is involved here.
        prompt_log_entries = [_collect_prompt_log(provider, "P5_summary", 5)] if result else []

    if result:
        return {
            "summary": result,
            "prompt_log": prompt_log_entries,
            **provider.get_usage(),
        }
    return None
