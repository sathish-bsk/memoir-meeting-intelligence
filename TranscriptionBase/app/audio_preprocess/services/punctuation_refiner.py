"""
LLM-based punctuation and capitalization refinement using Qwen 2.5 via Ollama
(default) or Anthropic Claude (set INTELLIGENCE_PROVIDER=claude).

Replaces the token-classification approach (oliverguhr) with a context-aware
LLM pass that fixes punctuation, capitalization, and contractions while
preserving all original words.
"""
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import httpx

# LangSmith tracing + LangChain Hub prompt loader + cross-provider router
# (all no-ops when LANGSMITH_ENABLED / LANGCHAIN_ENABLED /
#  LANGCHAIN_RESPECT_HUB_MODEL are false — default).
from app.llm_observability import (
    traceable_llm, get_prompt_sync, routed_sync_call,
)

logger = logging.getLogger("audio_preprocess")

_SYSTEM_PROMPT = """You are a transcript formatter. Your ONLY job is to fix punctuation, capitalization, and contractions in meeting transcript segments. You must NOT change any words.

Rules:
1. Add missing periods at sentence boundaries
2. Add commas at natural pauses and before conjunctions
3. Capitalize the first word of each sentence
4. Capitalize proper nouns (company names, person names, product names)
5. Fix contractions (its → it's, dont → don't, wont → won't, cant → can't, youre → you're, were → we're when appropriate, thats → that's, hes → he's, shes → she's, theyre → they're, doesnt → doesn't, didnt → didn't, isnt → isn't, wasnt → wasn't, weve → we've, youve → you've, whats → what's, lets → let's)
6. Preserve ALL words exactly — do NOT add, remove, or rephrase any words
7. NEVER change the value of any number. You may add comma separators (8500→8,500) but NEVER change digits or add decimal points (2317 must stay 2317 or 2,317, NOT 231.70)
8. Preserve technical terms and acronyms exactly as written
9. Do NOT merge or split segments — return exactly the same number of segments

Respond with JSON: {"segments": [{"id": 0, "text": "refined text"}, ...]}"""


class PunctuationRefiner:

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b-instruct",
        timeout: float = 120.0,
        temperature: float = 0.1,
        batch_size: int = 5,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.batch_size = batch_size

        # Tier 5.6 — singleton httpx client with explicit timeouts + keep-alive
        # pool. PunctuationRefiner makes 40-80 batch calls per meeting; the
        # per-call TCP handshake savings dominate.
        self._http_client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=10.0, read=timeout, write=30.0, pool=10.0),
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=40,
                keepalive_expiry=120.0,
            ),
        )

        # Tier 4.0.b — per-call cost/usage stats accumulated for prompt_log
        # emission. Caller drains via get_prompt_log() after refine_segments.
        self._last_call_stats: Optional[dict] = None
        self._prompt_log: List[dict] = []

        # Tier 4.1 — provider selection. Defaults to Ollama. Set
        # INTELLIGENCE_PROVIDER=claude to route through Anthropic Haiku
        # (punctuation refinement is high-volume + simple → Haiku-tier).
        self._provider = os.getenv("INTELLIGENCE_PROVIDER", "ollama").lower()
        self._claude_client = None  # lazy-init
        self._claude_model = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
        self._cache_ttl = os.getenv("CLAUDE_CACHE_TTL", "1h")

    def _estimate_num_ctx(self, system_prompt: str, user_prompt: str) -> int:
        prompt_chars = len(system_prompt) + len(user_prompt)
        prompt_tokens = max(1, int(prompt_chars / 3.5))
        output_buffer = max(2000, prompt_tokens)
        safety_margin = 512
        return max(4096, prompt_tokens + output_buffer + safety_margin)

    def _call_ollama(self, user_prompt: str, system_prompt: Optional[str] = None) -> Optional[dict]:
        # Plan §6.2 — accept optional system_prompt override; default preserves
        # existing behavior so untouched callers see no change.
        sys_p = system_prompt if system_prompt is not None else _SYSTEM_PROMPT
        num_ctx = self._estimate_num_ctx(sys_p, user_prompt)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": sys_p},
                {"role": "user", "content": user_prompt},
            ],
            "format": "json",
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": num_ctx,
            },
        }

        call_start = time.time()
        try:
            resp = self._http_client.post("/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("message", {}).get("content", "")
            input_tokens = int(data.get("prompt_eval_count", 0) or 0)
            output_tokens = int(data.get("eval_count", 0) or 0)
            duration_ms = int((time.time() - call_start) * 1000)

            try:
                parsed = json.loads(content)
                self._record_call_stats(
                    provider="ollama", model=self.model,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=duration_ms,
                    parse_success=True, error=None,
                )
                return parsed
            except json.JSONDecodeError as e:
                self._record_call_stats(
                    provider="ollama", model=self.model,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=duration_ms,
                    parse_success=False, error=f"json_decode: {e}",
                )
                return None
        except httpx.HTTPError as e:
            logger.warning(f"Punctuation refiner Ollama call failed: {e}")
            self._record_call_stats(
                provider="ollama", model=self.model,
                input_tokens=0, output_tokens=0,
                cache_read=0, cache_creation=0,
                cost_usd=0.0, duration_ms=int((time.time() - call_start) * 1000),
                parse_success=False, error=str(e),
            )
            return None

    def _call_claude(self, user_prompt: str, system_prompt: Optional[str] = None) -> Optional[dict]:
        """Anthropic Claude Haiku call with 2-layer caching.

        Layer 1 — _SYSTEM_PROMPT (formatter rules) → cached
        Layer 3 — user_prompt (per-batch segments) → never cached

        No Layer 2 — punctuation refinement doesn't need team-specific
        context (vocabulary doesn't change punctuation rules).

        Plan §6.2 — system_prompt arg overrides _SYSTEM_PROMPT when set
        (e.g., Hub template). Default preserves existing behavior.
        """
        if self._claude_client is None:
            try:
                import anthropic
                api_key = os.getenv("ANTHROPIC_API_KEY", "")
                if not api_key:
                    logger.error("Punctuation refiner: ANTHROPIC_API_KEY not set; falling back to None")
                    return None
                default_headers = {}
                if self._cache_ttl == "1h":
                    default_headers["anthropic-beta"] = "extended-cache-ttl-2025-04-11"
                self._claude_client = anthropic.Anthropic(
                    api_key=api_key,
                    timeout=self.timeout,
                    default_headers=default_headers or None,
                )
            except ImportError as e:
                logger.error(f"Punctuation refiner: anthropic SDK not installed: {e}")
                return None

        cache_control = (
            {"type": "ephemeral", "ttl": "1h"} if self._cache_ttl == "1h"
            else {"type": "ephemeral"}
        )

        # Plan §6.2 — system_prompt may have been overridden by caller (Hub
        # template) but default falls back to the module-level _SYSTEM_PROMPT.
        sys_p = system_prompt if system_prompt is not None else _SYSTEM_PROMPT
        call_start = time.time()
        try:
            response = self._claude_client.messages.create(
                model=self._claude_model,
                max_tokens=4096,
                system=[{
                    "type": "text",
                    "text": sys_p,
                    "cache_control": cache_control,
                }],
                messages=[{"role": "user", "content": user_prompt}],
            )
            duration_ms = int((time.time() - call_start) * 1000)

            usage = response.usage
            input_tokens = getattr(usage, "input_tokens", 0)
            output_tokens = getattr(usage, "output_tokens", 0)
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0

            # Haiku 4.5 pricing
            base_rate = 0.80
            output_rate = 4.00
            write_mult = 2.0 if self._cache_ttl == "1h" else 1.25
            cost = (
                input_tokens * base_rate
                + cache_creation * base_rate * write_mult
                + cache_read * base_rate * 0.1
                + output_tokens * output_rate
            ) / 1_000_000

            content = response.content[0].text if response.content else ""
            try:
                parsed = json.loads(content)
                self._record_call_stats(
                    provider="anthropic", model=self._claude_model,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    cache_read=cache_read, cache_creation=cache_creation,
                    cost_usd=round(cost, 6), duration_ms=duration_ms,
                    parse_success=True, error=None,
                )
                return parsed
            except json.JSONDecodeError as e:
                self._record_call_stats(
                    provider="anthropic", model=self._claude_model,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    cache_read=cache_read, cache_creation=cache_creation,
                    cost_usd=round(cost, 6), duration_ms=duration_ms,
                    parse_success=False, error=f"json_decode: {e}",
                )
                return None

        except Exception as e:
            logger.warning(f"Punctuation refiner Claude call failed: {e}")
            self._record_call_stats(
                provider="anthropic", model=self._claude_model,
                input_tokens=0, output_tokens=0,
                cache_read=0, cache_creation=0,
                cost_usd=0.0, duration_ms=int((time.time() - call_start) * 1000),
                parse_success=False, error=str(e),
            )
            return None

    def _call_vllm(self, user_prompt: str, system_prompt: Optional[str] = None) -> Optional[dict]:
        """Tier 5.1.a — vLLM via OpenAI-compat /chat/completions.

        Plan §6.2 — system_prompt arg overrides _SYSTEM_PROMPT when set.
        """
        sys_p = system_prompt if system_prompt is not None else _SYSTEM_PROMPT
        vllm_base_url = os.getenv("VLLM_BASE_URL", "http://localhost:8011/v1").rstrip("/")
        vllm_model = os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-7B-Instruct-AWQ")

        payload = {
            "model": vllm_model,
            "messages": [
                {"role": "system", "content": sys_p},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
        }
        call_start = time.time()
        try:
            resp = httpx.post(
                f"{vllm_base_url}/chat/completions",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices") or []
            content = (choices[0].get("message", {}).get("content", "") or "") if choices else ""
            usage = data.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens", 0) or 0)
            output_tokens = int(usage.get("completion_tokens", 0) or 0)
            duration_ms = int((time.time() - call_start) * 1000)
            try:
                parsed = json.loads(content)
                self._record_call_stats(
                    provider="vllm", model=vllm_model,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=duration_ms,
                    parse_success=True, error=None,
                )
                return parsed
            except json.JSONDecodeError as e:
                self._record_call_stats(
                    provider="vllm", model=vllm_model,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=duration_ms,
                    parse_success=False, error=f"json_decode: {e}",
                )
                return None
        except httpx.HTTPError as e:
            logger.warning(f"Punctuation refiner vLLM call failed: {e}")
            self._record_call_stats(
                provider="vllm", model=vllm_model,
                input_tokens=0, output_tokens=0,
                cache_read=0, cache_creation=0,
                cost_usd=0.0, duration_ms=int((time.time() - call_start) * 1000),
                parse_success=False, error=str(e),
            )
            return None

    @traceable_llm("memoir-punctuation")
    def _call_llm(self, user_prompt: str, system_prompt: Optional[str] = None) -> Optional[dict]:
        """Tier 4.1 + 5.1.a — provider dispatcher.

        INTELLIGENCE_PROVIDER:
          claude → _call_claude (Haiku-tier)
          vllm   → _call_vllm
          ollama (default) → _call_ollama

        Plan §6.2 — passes optional system_prompt through to the chosen
        backend. Default preserves existing behavior (each backend falls
        back to the module-level _SYSTEM_PROMPT). Phase 2.4 wires this
        from get_prompt_sync at the call site.

        @traceable_llm — produces one LangSmith trace per call when
        LANGSMITH_ENABLED=true. ContextVar `current_prompt_name` (set by
        higher-level code) can override the trace name; this decorator
        provides "memoir-punctuation" as the fallback name.
        """
        if self._provider == "claude":
            return self._call_claude(user_prompt, system_prompt=system_prompt)
        if self._provider == "vllm":
            return self._call_vllm(user_prompt, system_prompt=system_prompt)
        return self._call_ollama(user_prompt, system_prompt=system_prompt)

    def _record_call_stats(self, *, provider: str, model: str,
                            input_tokens: int, output_tokens: int,
                            cache_read: int, cache_creation: int,
                            cost_usd: float, duration_ms: int,
                            parse_success: bool, error: Optional[str]) -> None:
        """Persist last-call usage stats AND append a prompt_log entry."""
        stats = {
            "provider": provider,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "cost_usd": cost_usd,
            "duration_ms": duration_ms,
            "parse_success": parse_success,
        }
        if error:
            stats["error"] = error
        self._last_call_stats = stats

        entry = {
            "prompt_number": None,
            "prompt_name": "punctuation_refiner",
            "provider": provider,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "cost_usd": cost_usd,
            "duration_ms": duration_ms,
            "parse_success": parse_success,
        }
        self._prompt_log.append(entry)

    def get_prompt_log(self) -> List[dict]:
        """Drain accumulated prompt_log entries (one per LLM call)."""
        log = list(self._prompt_log)
        self._prompt_log.clear()
        return log

    def _validate_refinement(self, original: str, refined: str) -> bool:
        """Check that the LLM didn't add/remove words or change numbers."""
        def normalize(text: str) -> list:
            return re.findall(r"[a-zA-Z0-9]+", text.lower())

        orig_words = normalize(original)
        ref_words = normalize(refined)

        if len(orig_words) == 0:
            return len(ref_words) == 0

        # Number preservation: extract digit-only value from each number
        # Allow comma formatting (2317→2,317) but reject value changes (2317→231.70)
        orig_numbers = sorted(re.sub(r"[,.]", "", n) for n in re.findall(r"\d[\d,.]*\d|\d", original))
        ref_numbers = sorted(re.sub(r"[,.]", "", n) for n in re.findall(r"\d[\d,.]*\d|\d", refined))
        if orig_numbers != ref_numbers:
            return False

        # Allow small differences from contraction splits (it's → it + s)
        orig_set = set(orig_words)
        ref_set = set(ref_words)
        diff = orig_set.symmetric_difference(ref_set)
        return len(diff) <= max(4, len(orig_words) // 10)

    def _build_batch_prompt(self, batch: list) -> Optional[str]:
        """Build the user prompt for a single batch. Returns None for empty batches."""
        prompt_lines = ["Fix the punctuation, capitalization, and contractions in these transcript segments:\n"]
        for i, seg in enumerate(batch):
            text = (seg.get("text") or "").strip()
            if text:
                prompt_lines.append(f'Segment {i}: "{text}"')
        if len(prompt_lines) <= 1:
            return None
        return "\n".join(prompt_lines)

    def refine_segments(self, segments: list) -> tuple:
        """Refine punctuation and capitalization for all segments.

        Returns (segments, refinement_count).

        Tier 5.1.b — when LLM_CONCURRENT_BATCHES=true, batches dispatch via
        ThreadPoolExecutor (only beneficial with vLLM; Ollama serializes
        server-side regardless). Public API stays sync; concurrency is
        internal. Order-preserving via [f.result() for f in futures] so
        segment-id mapping per batch is unaffected.
        """
        if not segments:
            return segments, 0

        # Build batch list with their segment-offset for id mapping
        batches: List[tuple] = []  # (batch_start, batch_segs, user_prompt)
        for batch_start in range(0, len(segments), self.batch_size):
            batch_segs = segments[batch_start:batch_start + self.batch_size]
            user_prompt = self._build_batch_prompt(batch_segs)
            if user_prompt is None:
                continue
            batches.append((batch_start, batch_segs, user_prompt))

        if not batches:
            return segments, 0

        concurrent = os.getenv("LLM_CONCURRENT_BATCHES", "false").lower() == "true"
        max_workers = max(1, int(os.getenv("LLM_BATCH_CONCURRENCY", "4")))

        def _run_batch(item: tuple) -> tuple:
            batch_start, _batch_segs, user_prompt = item
            # Plan §6.2 + §9.4 — Hub prompt fetch (text override) + router
            # dispatch via routed_sync_call. When LangChain flags are off
            # the helper short-circuits to default_dispatcher = self._call_llm,
            # preserving existing behavior. When routing dispatches to an
            # adapter, the helper forwards stats back into self._prompt_log /
            # self._last_call_stats via self._record_call_stats so ai_prompt_log
            # persistence stays correct.
            bundle = get_prompt_sync(
                "memoir-punctuation",
                default_system=_SYSTEM_PROMPT,
                default_user_template=user_prompt,
            )
            result = routed_sync_call(
                owner=self,
                # _call_llm signature here is (user, system_prompt=...) — the
                # default_dispatcher contract is (system, user), so adapt:
                default_dispatcher=lambda s, u: self._call_llm(u, system_prompt=s),
                bundle=bundle,
                system_prompt=bundle.system,
                user_prompt=bundle.user,
            )
            return batch_start, result

        # Step 1 — dispatch (concurrent or sequential)
        if concurrent and len(batches) > 1:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(batches))) as pool:
                futures = [pool.submit(_run_batch, b) for b in batches]
                # Order-preserving: f.result() blocks in submission order so
                # results align with batches[]
                results = [f.result() for f in futures]
        else:
            results = [_run_batch(b) for b in batches]

        # Step 2 — apply results (sequential; mutates segments in-place)
        refined_count = 0
        for batch_start, result in results:
            if not result or "segments" not in result:
                continue
            refined_list = result["segments"]
            if not isinstance(refined_list, list):
                continue

            for item in refined_list:
                seg_id = item.get("id")
                new_text = (item.get("text") or "").strip()

                if seg_id is None or not new_text:
                    continue
                if not isinstance(seg_id, int) or seg_id < 0:
                    continue

                actual_idx = batch_start + seg_id
                if actual_idx >= len(segments):
                    continue

                original_text = segments[actual_idx].get("text", "")

                if not self._validate_refinement(original_text, new_text):
                    logger.debug(f"Refinement rejected (word mismatch): \"{original_text[:50]}\" → \"{new_text[:50]}\"")
                    continue

                if new_text != original_text:
                    segments[actual_idx]["text"] = new_text
                    refined_count += 1

        logger.info(f"Punctuation refinement: {refined_count}/{len(segments)} segments refined")
        return segments, refined_count
