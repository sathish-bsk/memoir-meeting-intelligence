"""
LLM-based context correction for Phase 5b.

Fully self-contained module — zero imports from pipeline, transcribe, or audio
modules. Takes pure data (segments as list[dict], vocabulary as list[dict]),
returns pure data (corrected segments, correction log).

Can be extracted to a microservice later by wrapping correct_segments() with
a FastAPI POST route.

Dependencies: httpx, jellyfish, rapidfuzz, json, logging (all stdlib or already installed).
"""
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple, Optional

import httpx
import jellyfish
from rapidfuzz.distance import Levenshtein

# LangSmith tracing + LangChain Hub prompt loader + cross-provider router
# (no-op when LANGSMITH_ENABLED / LANGCHAIN_ENABLED /
#  LANGCHAIN_RESPECT_HUB_MODEL are false — default).
from app.llm_observability import (
    traceable_llm, get_prompt_sync, routed_sync_call,
)

logger = logging.getLogger("audio_preprocess")

# Common English words that should never be phonetically corrected.
# Duplicated from vocabulary.py to keep this module self-contained.
_STOPWORDS = frozenset({
    "i", "me", "my", "we", "us", "our", "you", "your", "he", "him", "his",
    "she", "her", "it", "its", "they", "them", "their", "this", "that",
    "these", "those", "who", "what", "which", "all", "each", "some", "any",
    "am", "is", "are", "was", "were", "be", "been", "being", "have", "has",
    "had", "do", "does", "did", "will", "would", "can", "could", "shall",
    "should", "may", "might", "must", "go", "going", "gone", "get", "got",
    "come", "came", "make", "made", "take", "took", "give", "gave", "say",
    "said", "see", "saw", "know", "knew", "think", "want", "need", "use",
    "find", "tell", "ask", "work", "call", "try", "keep", "let", "put",
    "run", "set", "move", "play", "pay", "hear", "help", "talk", "turn",
    "start", "show", "feel", "hold", "bring", "happen", "write", "provide",
    "sit", "stand", "lose", "meet", "send", "read", "spend", "grow", "win",
    "teach", "buy", "watch", "follow", "die", "lead",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "up", "out",
    "if", "or", "and", "but", "not", "no", "so", "as", "an", "a", "the",
    "than", "too", "very", "just", "about", "also", "then", "more", "here",
    "there", "when", "how", "now", "new", "old", "well", "way", "back",
    "into", "over", "after", "before", "between", "under", "again", "still",
    "even", "only", "like", "most", "other", "much", "many", "long", "great",
    "ban", "band", "bar", "base", "mark", "park", "point", "port", "sign",
    "man", "men", "one", "two", "three", "four", "five", "first", "last",
    "good", "bad", "big", "small", "real", "right", "left", "high", "low",
    "best", "same", "own", "part", "kind", "sure", "both", "end", "why",
    "says", "saying", "others", "courses", "course", "bands", "bent",
    "medals", "clarity", "phantom", "turns", "team", "teams", "yes",
    "spins", "spin", "notion", "aroma", "fireplace", "motion",
})


def check_ollama_available(base_url: str) -> bool:
    """Check if Ollama is running and reachable."""
    try:
        resp = httpx.get(f"{base_url}/v1/models", timeout=3.0)
        return resp.status_code == 200
    except Exception:
        return False


class LLMCorrector:
    """
    LLM-based context correction using Ollama.

    Takes pure data (segments as list[dict], vocabulary as list[dict]).
    Returns pure data (corrected segments, correction log).
    No imports from pipeline, transcribe, or audio modules.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        vocab_entries: List[dict],
        confidence_threshold: float = 0.5,
        timeout: float = 120.0,
        temperature: float = 0.1,
        max_batch: int = 20,
    ):
        self.base_url = base_url
        self.model = model
        self.confidence_threshold = confidence_threshold
        self.timeout = timeout
        self.temperature = temperature
        self.max_batch = max_batch
        self.call_count: int = 0  # number of batch calls made to Ollama

        # Tier 5.6 — singleton httpx client with explicit timeouts + keep-alive
        # pool. Avoids ~50-200 ms TCP handshake per call across the ~50-100 LLM
        # calls per meeting. Read timeout matches self.timeout to preserve
        # existing long-prompt behavior.
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
        # emission. Caller drains via get_prompt_log() after correct_segments.
        self._last_call_stats: Optional[dict] = None
        self._prompt_log: List[dict] = []

        # Tier 4.1 — provider selection. Defaults to Ollama (today's behavior).
        # Set INTELLIGENCE_PROVIDER=claude to route through Anthropic.
        self._provider = os.getenv("INTELLIGENCE_PROVIDER", "ollama").lower()
        self._claude_client = None  # lazy-init on first Claude call
        self._claude_model = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
        self._cache_ttl = os.getenv("CLAUDE_CACHE_TTL", "1h")

        # Tier 5.2 — when correct_and_refine() is running, this overrides the
        # default "llm_corrector" prompt_name on per-call ai_prompt_log entries
        # so the merged path is distinguishable in cost analytics. Set on entry,
        # cleared on exit.
        self._current_prompt_name: Optional[str] = None

        # Tier 5.1.b — protect call_count and other small accumulators when
        # batches dispatch concurrently via ThreadPoolExecutor. _prompt_log
        # uses list.append which is GIL-atomic, so it doesn't need this lock.
        self._stats_lock = threading.Lock()

        # Build internal indexes from vocab_entries (pure dicts)
        self._phonetic_index: dict = {}  # metaphone → list[dict]
        self._lower_index: dict = {}     # lowercase term → dict
        self._sounds_like_map: dict = {} # metaphone of sounds_like → dict
        self._all_entries: list = []     # flat list of enabled entries for edit distance

        for entry in vocab_entries:
            if not entry.get("enabled", True):
                continue
            term = entry["term"]
            self._lower_index[term.lower()] = entry
            self._all_entries.append(entry)

            # Index by metaphone of term
            try:
                meta = jellyfish.metaphone(term.lower())
                if meta:
                    self._phonetic_index.setdefault(meta, []).append(entry)
            except Exception:
                pass

            # Index by metaphone of sounds_like aliases
            for alias in entry.get("sounds_like", []):
                try:
                    meta = jellyfish.metaphone(alias.lower())
                    if meta:
                        self._sounds_like_map.setdefault(meta, []).append(entry)
                except Exception:
                    pass

    def _find_phonetic_candidates(self, word: str) -> List[dict]:
        """Find vocabulary entries that phonetically or orthographically match the given word."""
        lower = word.lower().strip()
        if not lower:
            return []

        lower_bare = lower.strip(".,!?;:'\"-()[]")
        if lower_bare in _STOPWORDS:
            return []

        # Skip if word IS an exact vocab term
        if lower in self._lower_index:
            return []

        try:
            word_meta = jellyfish.metaphone(lower)
        except Exception:
            word_meta = ""

        candidates = []
        seen_terms = set()

        # Check 1: Phonetic index (Metaphone match)
        if word_meta:
            for entry in self._phonetic_index.get(word_meta, []):
                term = entry["term"]
                if term not in seen_terms:
                    jw = jellyfish.jaro_winkler_similarity(lower, term.lower())
                    if jw >= 0.78:
                        candidates.append(entry)
                        seen_terms.add(term)

            # Check sounds_like index
            for entry in self._sounds_like_map.get(word_meta, []):
                term = entry["term"]
                if term not in seen_terms:
                    candidates.append(entry)
                    seen_terms.add(term)

        # Check 2: Edit distance (spelling similarity)
        if len(lower_bare) >= 5:  # skip short words — too many false Levenshtein neighbors
            max_dist = 2 if len(lower_bare) > 5 else 1
            for entry in self._all_entries:
                term = entry["term"]
                if term in seen_terms:
                    continue
                dist = Levenshtein.distance(lower_bare, term.lower(), score_cutoff=max_dist)
                if dist <= max_dist:
                    candidates.append(entry)
                    seen_terms.add(term)

        return candidates

    def _get_context(self, segments: List[dict], seg_idx: int) -> str:
        """Get context string from current + adjacent segments."""
        parts = []
        for i in range(max(0, seg_idx - 1), min(len(segments), seg_idx + 2)):
            text = segments[i].get("text", "")
            if text:
                parts.append(text)
        return " ".join(parts)

    def collect_candidates(
        self,
        segments: List[dict],
        already_corrected: set,
        vocab_candidates: Optional[List[dict]] = None,
    ) -> List[dict]:
        """
        Collect words that are candidates for vocabulary correction.

        Merges two sources:
        1. vocab_candidates from Phase 5a (exact + sounds_like matches)
        2. Own phonetic + edit-distance candidates from _find_phonetic_candidates()

        Candidates from both sources are merged per word (deduped by word_id).

        Args:
            segments: list of segment dicts with text, words, translation
            already_corrected: set of (seg_idx, word_idx) already fixed by abbreviation normalization
            vocab_candidates: list of candidate dicts from Phase 5a vocabulary matching

        Returns:
            list of candidate dicts with word_id, word, score, phonetic_candidates,
            context, field, segment_index, word_index
        """
        # Start with vocab candidates (exact + sounds_like from Phase 5a)
        candidates_by_id = {}
        if vocab_candidates:
            for vc in vocab_candidates:
                word_id = vc["word_id"]
                candidates_by_id[word_id] = {
                    "word_id": word_id,
                    "word": vc["word"],
                    "score": vc["score"],
                    "phonetic_candidates": list(vc["phonetic_candidates"]),
                    "context": vc["context"],
                    "field": vc["field"],
                    "segment_index": vc["segment_index"],
                    "word_index": vc["word_index"],
                    "match_type": vc.get("match_type", ""),
                }

        # Add own phonetic + edit-distance candidates
        for seg_idx, seg in enumerate(segments):
            context = self._get_context(segments, seg_idx)

            # Check words in seg["text"] (with alignment scores)
            words = seg.get("words", [])
            if words:
                for w_idx, w in enumerate(words):
                    if (seg_idx, w_idx) in already_corrected:
                        continue

                    word_text = w.get("word", "").strip()
                    if not word_text:
                        continue

                    score = w.get("score", 1.0)
                    if score >= self.confidence_threshold:
                        continue

                    phonetic_matches = self._find_phonetic_candidates(word_text)
                    if not phonetic_matches:
                        continue

                    word_id = f"seg{seg_idx}_w{w_idx}"
                    new_terms = [e["term"] for e in phonetic_matches]

                    if word_id in candidates_by_id:
                        # Merge: add new phonetic candidates to existing vocab candidate
                        existing = candidates_by_id[word_id]["phonetic_candidates"]
                        for term in new_terms:
                            if term not in existing:
                                existing.append(term)
                    else:
                        candidates_by_id[word_id] = {
                            "word_id": word_id,
                            "word": word_text,
                            "score": round(score, 4),
                            "phonetic_candidates": new_terms,
                            "context": context,
                            "field": "text",
                            "segment_index": seg_idx,
                            "word_index": w_idx,
                        }

            # Check words in seg["translation"]
            translation = seg.get("translation", "")
            if translation:
                trans_words = translation.split()
                for tw_idx, tw in enumerate(trans_words):
                    tw_clean = tw.strip(".,!?;:'\"-()[]")
                    if not tw_clean:
                        continue

                    phonetic_matches = self._find_phonetic_candidates(tw_clean)
                    if not phonetic_matches:
                        continue

                    word_id = f"seg{seg_idx}_t{tw_idx}"
                    new_terms = [e["term"] for e in phonetic_matches]

                    if word_id in candidates_by_id:
                        existing = candidates_by_id[word_id]["phonetic_candidates"]
                        for term in new_terms:
                            if term not in existing:
                                existing.append(term)
                    else:
                        candidates_by_id[word_id] = {
                            "word_id": word_id,
                            "word": tw_clean,
                            "score": 0.0,
                            "phonetic_candidates": new_terms,
                            "context": context,
                            "field": "translation",
                            "segment_index": seg_idx,
                            "word_index": tw_idx,
                        }

        candidates = list(candidates_by_id.values())
        vocab_count = len(vocab_candidates) if vocab_candidates else 0
        pre_filter = len(candidates)

        # Context-words filter: phonetic/edit-distance candidates must have
        # at least 1 context_word in surrounding text. Vocab matches (exact/
        # sounds_like) and email candidates are always kept.
        filtered = []
        for c in candidates:
            match_type = c.get("match_type", "")
            if match_type in ("exact_match", "sounds_like_match") or match_type.startswith("email_"):
                filtered.append(c)
                continue
            context_lower = c.get("context", "").lower()
            has_context = False
            for term in c.get("phonetic_candidates", []):
                entry = self._lower_index.get(term.lower(), {})
                for cw in (entry.get("context_words", []) if isinstance(entry, dict) else []):
                    if cw.lower() in context_lower:
                        has_context = True
                        break
                if has_context:
                    break
            if has_context:
                filtered.append(c)
            else:
                logger.debug(f"LLM corrector: dropped '{c['word']}' -> {c.get('phonetic_candidates', [])} (no context words)")
        candidates = filtered

        logger.info(f"LLM corrector: collected {pre_filter} candidates ({vocab_count} from vocab), {pre_filter - len(candidates)} dropped by context filter, {len(candidates)} remaining")
        return candidates

    def _build_prompt(self, candidates: List[dict]) -> Tuple[str, str]:
        """Build system and user prompts for the LLM."""
        # Build vocabulary summary with context words
        vocab_terms = set()
        for c in candidates:
            for term in c.get("phonetic_candidates", []):
                entry = self._lower_index.get(term.lower(), {})
                category = entry.get("category", "other") if isinstance(entry, dict) else "other"
                ctx = entry.get("context_words", []) if isinstance(entry, dict) else []
                ctx_str = f" [context: {', '.join(ctx)}]" if ctx else ""
                vocab_terms.add(f"- {term} ({category}){ctx_str}")

        vocab_summary = "\n".join(sorted(vocab_terms))

        system_prompt = f"""You are a specialized ASR post-correction assistant for sales call transcriptions.

Your task: review words that may have been misheard by the speech recognition system, and decide whether each word should be corrected to a domain-specific term from the provided vocabulary.

RULES:
1. Only correct a word if you are CONFIDENT the speaker intended the vocabulary term based on the surrounding context.
2. If the word makes perfect sense as-is in context, output "KEEP".
3. Common English words should almost NEVER be corrected unless the surrounding context strongly indicates a domain term was intended.
4. Each correction must be phonetically plausible — the word must sound similar to the correction.
5. Provide a brief reasoning (1 sentence) for each decision.
6. Fix mid-sentence capitalization errors: words like "By", "The", "With" that appear capitalized mid-sentence without being proper nouns should be lowercased.
7. NEVER strip, remove, or modify numbers, dollar amounts, percentages, or dates.
8. NEVER duplicate or repeat text. Output each word_id exactly ONCE.
9. NEVER correct a common English word to a vocabulary term if it makes grammatical sense. Examples: "says" is NOT "SaaS", "gone" is NOT "Gong", "other" is NOT "Otter", "course" is NOT "Chorus".
10. When in doubt, output "KEEP". False corrections are worse than missed corrections.
11. For EMAIL candidates: reconstruct the spoken email address. Use the known contact emails if provided. Output the full email address (e.g., "john@company.io").

VOCABULARY REFERENCE:
{vocab_summary}

Respond with JSON:
{{"corrections": [{{"word_id": "...", "original": "...", "decision": "KEEP or the corrected term", "reasoning": "..."}}]}}"""

        # Build user prompt with candidates
        user_parts = ["Review these flagged words from a sales call:\n"]
        for c in candidates:
            matches_str = ", ".join(c["phonetic_candidates"])
            score_str = f" (confidence: {c['score']})" if c["score"] > 0 else ""
            field_str = f" [from {c['field']}]" if c["field"] != "text" else ""
            match_type = c.get("match_type", "")
            match_hint = ""
            if match_type == "exact_match":
                match_hint = " [exact vocabulary match]"
            elif match_type == "sounds_like_match":
                match_hint = " [sounds-like vocabulary match]"
            user_parts.append(
                f"--- Word ID: {c['word_id']} ---\n"
                f"Word: \"{c['word']}\"{score_str}{field_str}{match_hint}\n"
                f"Phonetic matches: {matches_str}\n"
                f"Context: \"...{c['context']}...\"\n"
            )

        user_prompt = "\n".join(user_parts)
        return system_prompt, user_prompt

    def _estimate_num_ctx(self, system_prompt: str, user_prompt: str) -> int:
        """Calculate minimum num_ctx for this LLM call.

        Smaller num_ctx = smaller KV cache = faster inference on CPU.
        """
        prompt_chars = len(system_prompt) + len(user_prompt)
        prompt_tokens = max(1, int(prompt_chars / 3.5))
        output_buffer = 1500  # correction JSON is compact
        safety_margin = 512
        return max(4096, prompt_tokens + output_buffer + safety_margin)

    def _call_ollama(self, system_prompt: str, user_prompt: str, max_retries: int = 2) -> Optional[dict]:
        """Call Ollama native API and parse JSON response.

        Uses /api/chat (not /v1/chat/completions) because the OpenAI-compatible
        endpoint silently ignores options.num_ctx, defaulting to 4096 context.
        Dynamic num_ctx: calculates minimum needed per call instead of using default.
        On JSON parse failure: retry the LLM call (up to max_retries).
        """
        dynamic_ctx = self._estimate_num_ctx(system_prompt, user_prompt)
        logger.info("LLM corrector: dynamic num_ctx=%d", dynamic_ctx)

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "format": "json",
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": dynamic_ctx,
            },
        }

        for attempt in range(1, max_retries + 1):
            call_start = time.time()
            try:
                resp = self._http_client.post("/api/chat", json=payload)

                if resp.status_code != 200:
                    logger.warning("LLM corrector: Ollama returned status %d (attempt %d/%d)", resp.status_code, attempt, max_retries)
                    self._record_call_stats(
                        provider="ollama", model=self.model,
                        input_tokens=0, output_tokens=0,
                        cache_read=0, cache_creation=0,
                        cost_usd=0.0, duration_ms=int((time.time() - call_start) * 1000),
                        parse_success=False, error=f"status_{resp.status_code}",
                    )
                    return None

                data = resp.json()
                content = data.get("message", {}).get("content", "")
                input_tokens = int(data.get("prompt_eval_count", 0) or 0)
                output_tokens = int(data.get("eval_count", 0) or 0)
                duration_ms = int((time.time() - call_start) * 1000)

                if not content:
                    logger.warning("LLM corrector: empty response from Ollama (attempt %d/%d)", attempt, max_retries)
                    self._record_call_stats(
                        provider="ollama", model=self.model,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                        cache_read=0, cache_creation=0,
                        cost_usd=0.0, duration_ms=duration_ms,
                        parse_success=False, error="empty_content",
                    )
                    return None

                parsed = json.loads(content)
                self._record_call_stats(
                    provider="ollama", model=self.model,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=duration_ms,
                    parse_success=True, error=None,
                )
                return parsed

            except httpx.TimeoutException:
                logger.warning("LLM corrector: Ollama timeout after %.0fs (attempt %d/%d)", self.timeout, attempt, max_retries)
                self._record_call_stats(
                    provider="ollama", model=self.model,
                    input_tokens=0, output_tokens=0,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=int((time.time() - call_start) * 1000),
                    parse_success=False, error="timeout",
                )
                return None
            except json.JSONDecodeError as e:
                logger.warning("LLM corrector: invalid JSON (attempt %d/%d): %s", attempt, max_retries, e)
                if attempt < max_retries:
                    logger.warning("LLM corrector: retrying LLM call (attempt %d/%d)", attempt + 1, max_retries)
                    continue
                logger.error("LLM corrector: JSON failed after %d attempts", max_retries)
                self._record_call_stats(
                    provider="ollama", model=self.model,
                    input_tokens=0, output_tokens=0,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=int((time.time() - call_start) * 1000),
                    parse_success=False, error=f"json_decode: {e}",
                )
                return None
            except Exception as e:
                logger.warning("LLM corrector: Ollama call failed: %s (attempt %d/%d)", e, attempt, max_retries)
                self._record_call_stats(
                    provider="ollama", model=self.model,
                    input_tokens=0, output_tokens=0,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=int((time.time() - call_start) * 1000),
                    parse_success=False, error=str(e),
                )
                return None

        return None

    def _build_team_static_block(self) -> str:
        """Build the team-specific static block (Layer 2 for Claude caching).

        Sorted deterministically so the cache key is stable across batches +
        across meetings. Contains the FULL team vocabulary (not the per-batch
        candidate subset) so the prefix is identical for every call site that
        uses this corrector.
        """
        terms_sorted = []
        for entry in sorted(self._all_entries, key=lambda e: (e.get("term") or "").lower()):
            term = entry.get("term", "")
            category = entry.get("category", "other")
            ctx = entry.get("context_words", []) or []
            ctx_str = f" [context: {', '.join(sorted(ctx))}]" if ctx else ""
            terms_sorted.append(f"- {term} ({category}){ctx_str}")
        return "Team vocabulary reference (full list):\n" + "\n".join(terms_sorted)

    def _call_claude(self, system_prompt: str, user_prompt: str,
                     max_retries: int = 2) -> Optional[dict]:
        """Call Anthropic Claude API with 3-layer prompt caching.

        Layer 1 — system_prompt (instructions + JSON schema) → cached
        Layer 2 — full team vocabulary (sorted) → cached per team
        Layer 3 — user_prompt (per-batch candidates) → never cached
        """
        if self._claude_client is None:
            try:
                import anthropic
                api_key = os.getenv("ANTHROPIC_API_KEY", "")
                if not api_key:
                    logger.error("LLM corrector: ANTHROPIC_API_KEY not set; falling back to Ollama")
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
                logger.error(f"LLM corrector: anthropic SDK not installed: {e}")
                return None

        cache_control = (
            {"type": "ephemeral", "ttl": "1h"} if self._cache_ttl == "1h"
            else {"type": "ephemeral"}
        )
        team_static = self._build_team_static_block()

        for attempt in range(1, max_retries + 1):
            call_start = time.time()
            try:
                response = self._claude_client.messages.create(
                    model=self._claude_model,
                    max_tokens=4096,
                    system=[{
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": cache_control,
                    }],
                    messages=[{
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": team_static,
                                "cache_control": cache_control,
                            },
                            {"type": "text", "text": user_prompt},
                        ],
                    }],
                )
                duration_ms = int((time.time() - call_start) * 1000)

                usage = response.usage
                input_tokens = getattr(usage, "input_tokens", 0)
                output_tokens = getattr(usage, "output_tokens", 0)
                cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
                cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0

                # Sonnet 4.6 pricing — same constants as ClaudeProvider in
                # intelligence_pipeline.py kept in sync. If you flip
                # CLAUDE_SONNET_MODEL to a different rate, update both.
                base_rate = 3.0  # $/Mtok
                output_rate = 15.0  # $/Mtok
                write_mult = 2.0 if self._cache_ttl == "1h" else 1.25
                cost = (
                    input_tokens * base_rate
                    + cache_creation * base_rate * write_mult
                    + cache_read * base_rate * 0.1
                    + output_tokens * output_rate
                ) / 1_000_000

                content = response.content[0].text if response.content else ""
                if not content:
                    self._record_call_stats(
                        provider="anthropic", model=self._claude_model,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                        cache_read=cache_read, cache_creation=cache_creation,
                        cost_usd=round(cost, 6), duration_ms=duration_ms,
                        parse_success=False, error="empty_content",
                    )
                    return None

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
                logger.warning("LLM corrector: Claude invalid JSON (attempt %d/%d): %s", attempt, max_retries, e)
                if attempt < max_retries:
                    continue
                self._record_call_stats(
                    provider="anthropic", model=self._claude_model,
                    input_tokens=0, output_tokens=0,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=int((time.time() - call_start) * 1000),
                    parse_success=False, error=f"json_decode: {e}",
                )
                return None
            except Exception as e:
                logger.warning("LLM corrector: Claude call failed (attempt %d/%d): %s", attempt, max_retries, e)
                self._record_call_stats(
                    provider="anthropic", model=self._claude_model,
                    input_tokens=0, output_tokens=0,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=int((time.time() - call_start) * 1000),
                    parse_success=False, error=str(e),
                )
                return None

        return None

    def _call_vllm(self, system_prompt: str, user_prompt: str,
                   max_retries: int = 2) -> Optional[dict]:
        """Tier 5.1.a — vLLM via OpenAI-compatible /chat/completions.

        Mirrors _call_ollama: returns parsed JSON dict (or None), records
        per-call stats via _record_call_stats. No prompt caching (vLLM
        has no equivalent of Anthropic prompt cache).
        """
        vllm_base_url = os.getenv("VLLM_BASE_URL", "http://localhost:8011/v1").rstrip("/")
        vllm_model = os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-7B-Instruct-AWQ")

        payload = {
            "model": vllm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
        }

        for attempt in range(1, max_retries + 1):
            call_start = time.time()
            try:
                # Use a one-off httpx call (the _http_client singleton is
                # bound to self.base_url which is the Ollama URL); a fresh
                # httpx.post here is fine — vLLM is on the same loopback.
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

                if not content:
                    self._record_call_stats(
                        provider="vllm", model=vllm_model,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                        cache_read=0, cache_creation=0,
                        cost_usd=0.0, duration_ms=duration_ms,
                        parse_success=False, error="empty_content",
                    )
                    return None

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
                logger.warning("LLM corrector vLLM: invalid JSON (attempt %d/%d): %s", attempt, max_retries, e)
                if attempt < max_retries:
                    continue
                self._record_call_stats(
                    provider="vllm", model=vllm_model,
                    input_tokens=0, output_tokens=0,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=int((time.time() - call_start) * 1000),
                    parse_success=False, error=f"json_decode: {e}",
                )
                return None
            except Exception as e:
                logger.warning("LLM corrector vLLM call failed: %s (attempt %d/%d)", e, attempt, max_retries)
                self._record_call_stats(
                    provider="vllm", model=vllm_model,
                    input_tokens=0, output_tokens=0,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=int((time.time() - call_start) * 1000),
                    parse_success=False, error=str(e),
                )
                return None
        return None

    @traceable_llm("memoir-corrector")
    def _call_llm(self, system_prompt: str, user_prompt: str, max_retries: int = 2) -> Optional[dict]:
        """Provider-agnostic dispatcher.

        Tier 4.1 + 5.1.a — INTELLIGENCE_PROVIDER routes:
          - claude → _call_claude (Anthropic API + 3-layer cache)
          - vllm   → _call_vllm   (self-hosted OpenAI-compat)
          - ollama (default) → _call_ollama (today's path)

        @traceable_llm — produces one LangSmith trace per batched call
        when LANGSMITH_ENABLED=true; identity no-op when disabled.
        """
        if self._provider == "claude":
            return self._call_claude(system_prompt, user_prompt, max_retries)
        if self._provider == "vllm":
            return self._call_vllm(system_prompt, user_prompt, max_retries)
        return self._call_ollama(system_prompt, user_prompt, max_retries)

    def _record_call_stats(self, *, provider: str, model: str,
                            input_tokens: int, output_tokens: int,
                            cache_read: int, cache_creation: int,
                            cost_usd: float, duration_ms: int,
                            parse_success: bool, error: Optional[str]) -> None:
        """Persist last-call usage stats AND append a prompt_log entry.

        The entry shape matches intelligence_pipeline._collect_prompt_log()
        so the existing core_api persister (intelligence_store.store_all_costs)
        accepts it without changes.
        """
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
            "prompt_name": self._current_prompt_name or "llm_corrector",
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
        """Drain accumulated prompt_log entries (one per LLM call).

        Caller should call this after correct_segments() to forward the
        per-call cost data to core_api via the Type 5 result dict.
        """
        log = list(self._prompt_log)
        self._prompt_log.clear()
        return log

    def _verify_phonetically(self, original: str, suggestion: str) -> bool:
        """
        Verify that a correction is phonetically plausible.

        Three checks (any passing = verified):
        1. Metaphone codes match
        2. Jaro-Winkler similarity >= 0.80
        3. Metaphone of original matches any sounds_like of the suggested term
        """
        if suggestion.upper() == "KEEP":
            return True

        if "@" in suggestion:
            return True

        orig_lower = original.lower().strip(".,!?;:'\"-()[]")
        sugg_lower = suggestion.lower()

        try:
            # Check 1: Metaphone match
            orig_meta = jellyfish.metaphone(orig_lower)
            sugg_meta = jellyfish.metaphone(sugg_lower)
            if orig_meta and sugg_meta and orig_meta == sugg_meta:
                return True

            # Check 2: Jaro-Winkler similarity
            jw = jellyfish.jaro_winkler_similarity(orig_lower, sugg_lower)
            if jw >= 0.80:
                return True

            # Check 3: Original matches sounds_like of the suggested term
            entry = self._lower_index.get(sugg_lower, {})
            for alias in entry.get("sounds_like", []) if entry else []:
                try:
                    alias_meta = jellyfish.metaphone(alias.lower())
                    if orig_meta and alias_meta and orig_meta == alias_meta:
                        return True
                except Exception:
                    pass

        except Exception:
            pass

        return False

    def correct_segments(
        self,
        segments: List[dict],
        already_corrected: set,
        vocab_candidates: Optional[List[dict]] = None,
    ) -> Tuple[List[dict], List[dict]]:
        """
        Main entry point: collect candidates → prompt LLM → verify → apply.

        Args:
            segments: list of segment dicts (modified in place)
            already_corrected: set of (seg_idx, word_idx) from abbreviation normalization
            vocab_candidates: list of exact/sounds_like candidates from Phase 5a

        Returns:
            (segments, correction_log)
        """
        correction_log = []

        # Step 1: Collect all candidates (vocab + phonetic + edit-distance)
        candidates = self.collect_candidates(segments, already_corrected, vocab_candidates)
        if not candidates:
            logger.info("LLM corrector: no candidates to review")
            return segments, correction_log

        # Step 2: Process in batches.
        # Tier 5.1.b — when LLM_CONCURRENT_BATCHES=true, dispatch via
        # ThreadPoolExecutor; only beneficial with vLLM (Ollama serializes
        # server-side). Public API stays sync. Order-preserving via
        # [f.result() for f in futures] so all_decisions composition is
        # deterministic.
        batches: List[List[dict]] = []
        for batch_start in range(0, len(candidates), self.max_batch):
            batches.append(candidates[batch_start:batch_start + self.max_batch])

        concurrent = os.getenv("LLM_CONCURRENT_BATCHES", "false").lower() == "true"
        max_workers = max(1, int(os.getenv("LLM_BATCH_CONCURRENCY", "4")))

        def _run_batch(batch: List[dict]) -> Tuple[List[dict], Optional[dict]]:
            default_system, default_user = self._build_prompt(batch)
            # Plan §6.2 + §9.4 — Hub text override + cross-provider router.
            # routed_sync_call short-circuits to self._call_llm when routing
            # is off, and forwards adapter stats back to self._prompt_log /
            # self._last_call_stats (via self._record_call_stats) when routing
            # dispatches to a SyncProvider adapter — preserving the existing
            # ai_prompt_log cost path on every code branch.
            bundle = get_prompt_sync(
                "memoir-corrector",
                default_system=default_system,
                default_user_template=default_user,
            )
            with self._stats_lock:
                self.call_count += 1
            result = routed_sync_call(
                owner=self,
                default_dispatcher=lambda s, u: self._call_llm(s, u),
                bundle=bundle,
                system_prompt=bundle.system,
                user_prompt=bundle.user,
            )
            return batch, result

        if concurrent and len(batches) > 1:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(batches))) as pool:
                futures = [pool.submit(_run_batch, b) for b in batches]
                batch_results = [f.result() for f in futures]
        else:
            batch_results = [_run_batch(b) for b in batches]

        all_decisions = {}
        for batch, result in batch_results:
            if not result:
                # Log timeout/error for all candidates in this batch
                for c in batch:
                    correction_log.append({
                        "segment_id": c["segment_index"],
                        "word_index": c["word_index"],
                        "original_text": c["word"],
                        "corrected_text": c["word"],
                        "correction_type": "llm_timeout",
                        "confidence_before": c["score"],
                        "match_score": 0.0,
                        "llm_reasoning": "LLM did not respond",
                        "phonetic_verified": False,
                        "field": c["field"],
                    })
                continue

            # Parse decisions
            corrections = result.get("corrections", [])
            for corr in corrections:
                word_id = corr.get("word_id", "")
                all_decisions[word_id] = corr

        # Step 3: Verify and apply
        for c in candidates:
            decision = all_decisions.get(c["word_id"])

            if not decision:
                continue

            suggestion = decision.get("decision", "KEEP")
            reasoning = decision.get("reasoning", "")

            # "KEEP" means no correction needed
            if suggestion.upper() == "KEEP":
                correction_log.append({
                    "segment_id": c["segment_index"],
                    "word_index": c["word_index"],
                    "original_text": c["word"],
                    "corrected_text": c["word"],
                    "correction_type": "llm_keep",
                    "confidence_before": c["score"],
                    "match_score": 0.0,
                    "llm_reasoning": reasoning,
                    "phonetic_verified": True,
                    "field": c["field"],
                })
                continue

            # Verify suggestion is a known vocabulary term
            if suggestion.lower() not in self._lower_index:
                correction_log.append({
                    "segment_id": c["segment_index"],
                    "word_index": c["word_index"],
                    "original_text": c["word"],
                    "corrected_text": suggestion,
                    "correction_type": "llm_invalid",
                    "confidence_before": c["score"],
                    "match_score": 0.0,
                    "llm_reasoning": reasoning,
                    "phonetic_verified": False,
                    "field": c["field"],
                })
                continue

            # Phonetic verification gate
            if not self._verify_phonetically(c["word"], suggestion):
                correction_log.append({
                    "segment_id": c["segment_index"],
                    "word_index": c["word_index"],
                    "original_text": c["word"],
                    "corrected_text": suggestion,
                    "correction_type": "llm_rejected",
                    "confidence_before": c["score"],
                    "match_score": jellyfish.jaro_winkler_similarity(
                        c["word"].lower(), suggestion.lower()
                    ),
                    "llm_reasoning": reasoning,
                    "phonetic_verified": False,
                    "field": c["field"],
                })
                continue

            # Apply the correction
            seg = segments[c["segment_index"]]
            jw_score = jellyfish.jaro_winkler_similarity(
                c["word"].lower(), suggestion.lower()
            )

            if c["field"] == "text":
                span = c.get("span_length", 1)
                # Apply to word-level alignment if available
                words = seg.get("words", [])
                if words and c["word_index"] < len(words):
                    w = words[c["word_index"]]
                    w["corrected_from"] = w.get("word", c["word"])
                    w["word"] = suggestion
                    # Blank out remaining words in multi-word span (e.g. email "at domain.tld")
                    for j in range(1, span):
                        if c["word_index"] + j < len(words):
                            words[c["word_index"] + j]["word"] = ""
                    # Rebuild segment text
                    seg["text"] = " ".join(
                        w2.get("word", "") for w2 in words if w2.get("word", "").strip()
                    )
                else:
                    # No word alignment — replace in text directly
                    text_words = seg.get("text", "").split()
                    if c["word_index"] < len(text_words):
                        text_words[c["word_index"]] = suggestion
                        for j in range(1, span):
                            if c["word_index"] + j < len(text_words):
                                text_words[c["word_index"] + j] = ""
                        seg["text"] = " ".join(w for w in text_words if w)

            elif c["field"] == "translation":
                trans_words = seg.get("translation", "").split()
                if c["word_index"] < len(trans_words):
                    trans_words[c["word_index"]] = suggestion
                    seg["translation"] = " ".join(trans_words)

            correction_log.append({
                "segment_id": c["segment_index"],
                "word_index": c["word_index"],
                "original_text": c["word"],
                "corrected_text": suggestion,
                "correction_type": "llm_context",
                "confidence_before": c["score"],
                "match_score": round(jw_score, 4),
                "llm_reasoning": reasoning,
                "phonetic_verified": True,
                "field": c["field"],
            })

        applied = sum(1 for c in correction_log if c["correction_type"] == "llm_context")
        rejected = sum(1 for c in correction_log if c["correction_type"] == "llm_rejected")
        kept = sum(1 for c in correction_log if c["correction_type"] == "llm_keep")
        logger.info(
            f"LLM corrector: {applied} applied, {rejected} rejected, {kept} kept, "
            f"{len(candidates)} total candidates"
        )

        return segments, correction_log

    # ------------------------------------------------------------------
    # Tier 5.2 — merged correction + punctuation refinement
    # ------------------------------------------------------------------

    def _validate_refinement(self, original: str, refined: str) -> bool:
        """Tier 5.2 — port of PunctuationRefiner._validate_refinement.

        Reject the LLM's refined text if it (a) added/removed words beyond
        a small tolerance for contraction splits, or (b) altered any number
        value (commas allowed; digits not).
        """
        def normalize(text: str) -> list:
            return re.findall(r"[a-zA-Z0-9]+", text.lower())

        orig_words = normalize(original)
        ref_words = normalize(refined)

        if len(orig_words) == 0:
            return len(ref_words) == 0

        # Number preservation — strip commas/dots, compare digit-only values
        orig_numbers = sorted(re.sub(r"[,.]", "", n) for n in re.findall(r"\d[\d,.]*\d|\d", original))
        ref_numbers = sorted(re.sub(r"[,.]", "", n) for n in re.findall(r"\d[\d,.]*\d|\d", refined))
        if orig_numbers != ref_numbers:
            return False

        # Allow small symmetric difference for contraction splits (it's → it + s)
        # AND for vocabulary corrections that may legitimately swap words.
        orig_set = set(orig_words)
        ref_set = set(ref_words)
        diff = orig_set.symmetric_difference(ref_set)
        return len(diff) <= max(8, len(orig_words) // 6)

    def _build_merged_prompt(
        self,
        segments_batch: List[dict],
        batch_offset: int,
        batch_candidates: List[dict],
        vocab_summary: str,
    ) -> Tuple[str, str]:
        """Build the dual-task system+user prompts for correct_and_refine.

        Same vocab summary structure as _build_prompt so cache key is
        consistent within a meeting (both paths sort by lowercase term).
        """
        system_prompt = f"""You are a transcription post-processor for sales call transcripts.

For each batch you process TWO tasks together:

TASK 1 — VOCABULARY CORRECTION:
Review flagged words ("FLAGGED WORDS TO REVIEW"). For each, decide whether to correct it to a domain term from VOCABULARY REFERENCE or KEEP as-is.
- Only correct if you are CONFIDENT based on the surrounding context.
- The correction must be phonetically plausible (sounds similar to the spoken word).
- Common English words (says, gone, other, course, were, them, from, this) almost NEVER become vocabulary terms.
- For EMAIL candidates: reconstruct the spoken email address using known contacts where possible.
- NEVER modify numbers, dollar amounts, percentages, or dates.
- When in doubt: KEEP. False corrections are worse than missed corrections.

TASK 2 — PUNCTUATION & CAPITALIZATION:
For each segment in "SEGMENTS TO REFINE", produce a refined version with proper punctuation, capitalization, and contractions.
- Apply TASK 1 corrections to the segment text BEFORE refining punctuation.
- Add periods at sentence boundaries; commas at natural pauses.
- Capitalize the first word of each sentence; capitalize proper nouns.
- Fix contractions (dont→don't, wont→won't, youre→you're, thats→that's, hes→he's, shes→she's, theyre→they're, doesnt→doesn't, didnt→didn't, isnt→isn't, wasnt→wasn't, weve→we've, youve→you've, whats→what's, lets→let's, cant→can't, its→it's when possessive vs contraction).
- Do NOT add, remove, or rephrase words other than the TASK 1 corrections.
- NEVER change number values. You may add comma separators (8500→8,500) but NEVER change digits or insert decimal points (2317 must stay 2317 or 2,317, NOT 231.70).
- Preserve technical terms and acronyms exactly.
- Return exactly one entry per input segment, keyed by its segment id.

VOCABULARY REFERENCE:
{vocab_summary}

OUTPUT JSON:
{{"corrections": [{{"word_id": "...", "decision": "KEEP or the corrected term", "reasoning": "..."}}],
  "segments": [{{"id": <int>, "text": "<refined text with corrections applied>"}}]}}"""

        user_parts = []
        if batch_candidates:
            user_parts.append("FLAGGED WORDS TO REVIEW:")
            for c in batch_candidates:
                matches_str = ", ".join(c.get("phonetic_candidates", []) or [])
                score_str = f" (confidence: {c['score']})" if c.get("score", 0) > 0 else ""
                field_str = f" [from {c['field']}]" if c.get("field") and c["field"] != "text" else ""
                match_type = c.get("match_type", "")
                match_hint = ""
                if match_type == "exact_match":
                    match_hint = " [exact vocabulary match]"
                elif match_type == "sounds_like_match":
                    match_hint = " [sounds-like vocabulary match]"
                user_parts.append(
                    f"  word_id={c['word_id']}: \"{c['word']}\"{score_str}{field_str}{match_hint}; "
                    f"in segment {c['segment_index']}; phonetic matches: {matches_str}; "
                    f"context: \"...{c.get('context', '')}...\""
                )
        else:
            user_parts.append("FLAGGED WORDS TO REVIEW: (none in this batch)")

        user_parts.append("\nSEGMENTS TO REFINE:")
        for offset, seg in enumerate(segments_batch):
            seg_id = batch_offset + offset
            text = (seg.get("text") or "").strip()
            if text:
                user_parts.append(f'  Segment {seg_id}: "{text}"')

        return system_prompt, "\n".join(user_parts)

    def correct_and_refine(
        self,
        segments: List[dict],
        already_corrected: set,
        vocab_candidates: Optional[List[dict]] = None,
    ) -> Tuple[List[dict], List[dict]]:
        """Tier 5.2 — single dual-task LLM pass replacing correct_segments +
        PunctuationRefiner.refine_segments.

        Caller decides when to use this vs the separate two-pass flow via
        the MERGE_PUNCT_CORRECTION env var (gated in processor.py).

        The LLM is asked to apply vocab corrections AND punctuation/caps
        refinement in a single output. Per segment we prefer the LLM's
        refined text (which already includes corrections); we fall back
        to the original segment text if number-validation rejects the
        refined version.

        Returns (segments, correction_log) — same shape as correct_segments.
        """
        correction_log: List[dict] = []

        # Step 1 — collect candidates, same as correct_segments
        candidates = self.collect_candidates(segments, already_corrected, vocab_candidates)

        # Step 2 — group candidates by segment_index for fast batch lookup
        candidates_by_seg: dict = {}
        for c in candidates:
            candidates_by_seg.setdefault(c["segment_index"], []).append(c)

        # Step 3 — pre-build the full vocab summary (sorted, deterministic)
        vocab_terms_full: List[str] = []
        for entry in sorted(self._all_entries, key=lambda e: (e.get("term") or "").lower()):
            term = entry.get("term", "")
            category = entry.get("category", "other")
            ctx = entry.get("context_words", []) or []
            ctx_str = f" [context: {', '.join(sorted(ctx))}]" if ctx else ""
            vocab_terms_full.append(f"- {term} ({category}){ctx_str}")
        vocab_summary = "\n".join(vocab_terms_full)

        # Step 4 — iterate segments in batches; emit ai_prompt_log rows under
        # the merged prompt_name for cost-analytics distinguishability.
        merge_batch_size = max(1, int(os.getenv("MERGE_BATCH_SIZE", "5")))
        all_decisions: dict = {}
        refined_texts: dict = {}  # segment_index → refined text (if validated)

        # Tier 5.1.b — concurrent batch dispatch when LLM_CONCURRENT_BATCHES=true.
        concurrent = os.getenv("LLM_CONCURRENT_BATCHES", "false").lower() == "true"
        max_workers = max(1, int(os.getenv("LLM_BATCH_CONCURRENCY", "4")))

        # Build batch list: (batch_start, batch_segments, batch_candidates)
        merged_batches: List[tuple] = []
        for batch_start in range(0, len(segments), merge_batch_size):
            batch_segments = segments[batch_start:batch_start + merge_batch_size]
            # Skip empty batches (no text in any segment)
            if not any((s.get("text") or "").strip() for s in batch_segments):
                continue
            batch_candidates: List[dict] = []
            for offset, _seg in enumerate(batch_segments):
                seg_idx = batch_start + offset
                batch_candidates.extend(candidates_by_seg.get(seg_idx, []))
            merged_batches.append((batch_start, batch_segments, batch_candidates))

        def _run_merged_batch(item: tuple) -> tuple:
            batch_start, batch_segments, batch_candidates = item
            system_prompt, user_prompt = self._build_merged_prompt(
                batch_segments, batch_start, batch_candidates, vocab_summary,
            )
            with self._stats_lock:
                self.call_count += 1
            result = self._call_llm(system_prompt, user_prompt)
            return batch_start, batch_segments, batch_candidates, result

        self._current_prompt_name = "llm_correction_punct"
        try:
            if concurrent and len(merged_batches) > 1:
                with ThreadPoolExecutor(max_workers=min(max_workers, len(merged_batches))) as pool:
                    futures = [pool.submit(_run_merged_batch, b) for b in merged_batches]
                    batch_results = [f.result() for f in futures]
            else:
                batch_results = [_run_merged_batch(b) for b in merged_batches]
        finally:
            self._current_prompt_name = None

        for batch_start, batch_segments, batch_candidates, result in batch_results:
            if not result:
                # Log timeout entries for any candidates in this batch.
                # Segments stay as-is (no refinement applied).
                for c in batch_candidates:
                    correction_log.append({
                        "segment_id": c["segment_index"],
                        "word_index": c["word_index"],
                        "original_text": c["word"],
                        "corrected_text": c["word"],
                        "correction_type": "llm_timeout",
                        "confidence_before": c.get("score", 0),
                        "match_score": 0.0,
                        "llm_reasoning": "Merged LLM did not respond",
                        "phonetic_verified": False,
                        "field": c.get("field", "text"),
                    })
                continue

            # Parse correction decisions
            for corr in (result.get("corrections") or []):
                word_id = corr.get("word_id", "")
                if word_id:
                    all_decisions[word_id] = corr

            # Parse refined segments + validate each against the original
            for item in (result.get("segments") or []):
                seg_id = item.get("id")
                new_text = (item.get("text") or "").strip()
                if seg_id is None or not new_text or not isinstance(seg_id, int):
                    continue
                if seg_id < batch_start or seg_id >= batch_start + len(batch_segments):
                    continue  # out-of-batch id → ignore
                if seg_id < 0 or seg_id >= len(segments):
                    continue
                original = (segments[seg_id].get("text") or "").strip()
                if self._validate_refinement(original, new_text):
                    refined_texts[seg_id] = new_text
                else:
                    logger.debug(
                        "Tier 5.2 merged: refinement rejected for segment %d "
                        "(number/word mismatch)", seg_id,
                    )

        # Step 5 — apply refined text per segment, where validated.
        # The LLM's refined text already includes its TASK 1 corrections,
        # so applying it covers both correction + punctuation in one shot.
        applied_count = 0
        for seg_id, refined_text in refined_texts.items():
            if 0 <= seg_id < len(segments):
                segments[seg_id]["text"] = refined_text
                applied_count += 1

        # Step 6 — populate correction_log from decisions for cost auditing.
        # Note: in the merged path we trust the LLM's correction calls because
        # the segment refined_text already reflects them. correction_log here
        # is informational; the verify-and-apply word-by-word pipeline used
        # by correct_segments is intentionally skipped.
        for c in candidates:
            decision = all_decisions.get(c["word_id"])
            if not decision:
                # No decision in any batch (e.g., parse failure) — log as keep
                correction_log.append({
                    "segment_id": c["segment_index"],
                    "word_index": c["word_index"],
                    "original_text": c["word"],
                    "corrected_text": c["word"],
                    "correction_type": "llm_keep",
                    "confidence_before": c.get("score", 0),
                    "match_score": 0.0,
                    "llm_reasoning": "no decision returned (merged path)",
                    "phonetic_verified": False,
                    "field": c.get("field", "text"),
                })
                continue
            suggestion = (decision.get("decision") or "KEEP").strip()
            reasoning = decision.get("reasoning", "")

            if suggestion.upper() == "KEEP":
                correction_log.append({
                    "segment_id": c["segment_index"],
                    "word_index": c["word_index"],
                    "original_text": c["word"],
                    "corrected_text": c["word"],
                    "correction_type": "llm_keep",
                    "confidence_before": c.get("score", 0),
                    "match_score": 0.0,
                    "llm_reasoning": reasoning,
                    "phonetic_verified": True,
                    "field": c.get("field", "text"),
                })
                continue

            # Determine if the refined text actually contains the suggestion.
            refined_text = refined_texts.get(c["segment_index"], "")
            applied = False
            if refined_text and re.search(rf"\b{re.escape(suggestion)}\b", refined_text, re.IGNORECASE):
                applied = True

            if applied:
                correction_log.append({
                    "segment_id": c["segment_index"],
                    "word_index": c["word_index"],
                    "original_text": c["word"],
                    "corrected_text": suggestion,
                    "correction_type": "llm_context",
                    "confidence_before": c.get("score", 0),
                    "match_score": 1.0,
                    "llm_reasoning": reasoning,
                    "phonetic_verified": True,
                    "field": c.get("field", "text"),
                })
            else:
                # LLM proposed a correction but the refined text didn't apply
                # it (either rejected by validator or LLM dropped it). Log as
                # rejected so the audit trail still records the proposal.
                correction_log.append({
                    "segment_id": c["segment_index"],
                    "word_index": c["word_index"],
                    "original_text": c["word"],
                    "corrected_text": c["word"],
                    "correction_type": "llm_rejected",
                    "confidence_before": c.get("score", 0),
                    "match_score": 0.0,
                    "llm_reasoning": reasoning,
                    "phonetic_verified": False,
                    "field": c.get("field", "text"),
                })

        applied_in_log = sum(1 for c in correction_log if c["correction_type"] == "llm_context")
        kept = sum(1 for c in correction_log if c["correction_type"] == "llm_keep")
        rejected = sum(1 for c in correction_log if c["correction_type"] == "llm_rejected")
        timeouts = sum(1 for c in correction_log if c["correction_type"] == "llm_timeout")
        logger.info(
            f"Tier 5.2 merged: refined {applied_count} segments; "
            f"corrections {applied_in_log} applied, {kept} kept, {rejected} rejected, {timeouts} timeouts"
        )

        return segments, correction_log
