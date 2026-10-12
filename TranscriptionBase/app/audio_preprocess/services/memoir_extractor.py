"""
Phase 10: Memoir call summary extraction via LLM.

Self-contained module — zero imports from pipeline/transcribe/audio.
Designed for future extraction into post-processing microservice (Phase 9).

Takes pure dicts (segments with speaker labels + timestamps), calls Ollama/vLLM,
returns validated MemoirSummary dict.

Follows the same patterns as intelligence.py (Ollama native /api/chat,
dynamic num_ctx, map-reduce for long transcripts, JSON repair).
"""

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

import httpx

from .memoir_schemas import MemoirSummary, ConfidenceLevel, get_memoir_schema

# LangSmith tracing + LangChain Hub prompt loader + cross-provider router
# (no-op when LangChain flags are false — default).
from app.llm_observability import (
    traceable_llm, get_prompt_sync, routed_sync_call,
)


def _memoir_dispatch(
    extractor,
    default_system: str,
    default_user_template: str,
    **variables,
) -> Optional[dict]:
    """Hub-aware dispatch for the MemoirExtractor.

    Two call shapes supported:
    - WITH variables (single-pass path): default_system + default_user_template
      are templates with {var} placeholders. get_prompt_sync renders them via
      .format(**variables) when Hub is disabled, or fetches Hub equivalents
      and renders THOSE with the same variables.
    - WITHOUT variables (chunk path): default_system + default_user_template
      are pre-rendered strings. get_prompt_sync passes them through verbatim.

    routed_sync_call forwards adapter stats back to extractor._prompt_log
    via extractor._record_call_stats so ai_prompt_log keeps working
    regardless of which backend executes the call.
    """
    bundle = get_prompt_sync(
        "memoir-extractor",
        default_system=default_system,
        default_user_template=default_user_template,
        **variables,
    )
    return routed_sync_call(
        owner=extractor,
        default_dispatcher=lambda s, u: extractor._call_llm(s, u),
        bundle=bundle,
        system_prompt=bundle.system,
        user_prompt=bundle.user,
    )

logger = logging.getLogger(__name__)


# --- Off-the-record keywords ---

OFF_THE_RECORD_KEYWORDS = [
    "off the record",
    "between us",
    "don't quote me",
    "don't put this in writing",
    "this stays here",
    "i shouldn't tell you this",
    "i shouldn't be telling you this",
    "confidentially",
    "this is confidential",
    "keep this between",
]


# --- System prompt (from spec Section 5) ---

MEMOIR_SYSTEM_PROMPT = """You are Memoir's call analysis engine. Your job is to extract structured sales intelligence from call transcripts and produce a summary that a sales professional would find immediately useful for updating their CRM and preparing for next steps.

## YOUR PRINCIPLES

1. ACCURACY OVER COMPLETENESS. If you're not confident about a data point, do NOT fabricate or stretch. Mark it as ambiguous or exclude it. A CRM with no data is better than a CRM with wrong data.

2. ATTRIBUTION IS MANDATORY. Every extracted data point must include the speaker name and a supporting quote (or paraphrase if the quote is very long). This allows the user to verify and builds trust.

3. CONFIDENCE TAGGING. Every discovery field must be tagged:
   - DEFINITIVE: Explicitly stated in clear language
   - INDICATIVE: Strongly implied, reasonable inference from direct statements
   - INFERRED: AI interpretation from indirect signals — useful but less reliable
   - AMBIGUOUS: Mentioned but unclear, contradictory, or impossible to parse — EXCLUDE from fields, list in disclaimer

4. THE "RETURNING SALESPERSON" TEST. Write the summary as if you're a senior sales rep who took perfect notes. When the user reads this summary 3 weeks from now before their next call, they should immediately recall the context, know what was promised, and feel prepared. No fluff, no filler, no AI-speak.

5. RESPECT CONFIDENTIALITY. If a speaker uses language suggesting off-the-record intent ("between us", "don't quote me", "off the record", "this stays here", "I shouldn't be telling you this"), EXCLUDE that content from the summary entirely. Add a flag: confidential_content_detected = true.

6. HANDLE MULTIPLE SPEAKERS. Always attribute insights to the speaker using the labels provided in the transcript (e.g., SPEAKER_00, SPEAKER_01, or resolved names). Do NOT attempt to infer or guess speaker identities from names mentioned in conversation — a speaker saying "Good call, Satish" does NOT mean the speaker IS Satish, they are ADDRESSING Satish.

7. SPEAKER NAMES. Use the speaker labels exactly as they appear at the start of each transcript line. If the transcript uses SPEAKER_XX labels, keep those labels in your output — do NOT replace them with inferred names. If the transcript uses real names (already resolved), use those names. For call_metadata.participants, only include names that are used as speaker labels in the transcript, not names mentioned in dialogue.

## OUTPUT FORMAT

You must respond with valid JSON matching the schema provided in the user message. Do not include any text outside the JSON object.

## WHAT NOT TO DO

- Do NOT invent information that wasn't in the transcript
- Do NOT assign names to speakers — use only the labels from the transcript (SPEAKER_XX or pre-resolved names)
- Do NOT infer speaker identity from names mentioned in dialogue (e.g., "Thanks Vivek" does NOT mean the speaker is Vivek)
- Do NOT fabricate participant names, company names, or titles
- Do NOT assign DEFINITIVE confidence to something that was implied
- Do NOT include pleasantries, small talk, or filler in the narrative
- Do NOT use phrases like "The AI detected" or "Based on our analysis" — write as if a human took these notes
- Do NOT push inferred insights into the discovery fields — notes only
- Do NOT include content flagged as confidential/off-the-record
- Do NOT include exact lengthy quotes (>30 words) — paraphrase and note the timestamp range instead

## EDGE CASE RULES

1. If a discovery field was not discussed at all, set value to null and confidence to "ambiguous" with source_quote: "Not discussed in this call"
2. If the call is mostly small talk with minimal discovery, still extract what you can. Set overall_confidence_score low.
3. If multiple prospect speakers disagree, capture the MOST SENIOR person's answer and note the discrepancy in narrative.
4. If the prospect corrects themselves, use the CORRECTED version.
5. For budget ranges, capture as-is. Do not average or pick a midpoint.
6. If the rep states pricing, this is NOT the prospect's budget. Only capture budget from what the PROSPECT says.
7. If transcript has quality issues ([inaudible] markers), note in disclaimer and lower confidence.
8. For voicemails (single speaker, <3 min), produce minimal summary with null fields.

CRITICAL: Never hallucinate or invent information. Use ONLY the speaker labels that appear at the start of each transcript line (SPEAKER_00, SPEAKER_01, or pre-resolved names). Do NOT infer who a speaker is from names mentioned in the conversation text. A speaker addressing someone by name does not reveal the speaker's own identity.

CRITICAL — source_quote field format:
- source_quote must be a VERBATIM quote of what the speaker said. ≤ 250 characters.
- Do NOT prefix with the speaker's name (use source_speaker for that — separate field).
- Do NOT include multi-turn dialogue. ONE speaker, ONE statement.
- If the quote would exceed 250 chars, pick the most informative single sentence.
- WRONG: source_quote: "Vivekanandhan Sivasubramaniyam: Our budget is around $50K and we're hoping to deploy by Q3 once the security team finishes the review."
- RIGHT: source_quote: "Our budget is around $50K"  +  source_speaker: "Vivekanandhan Sivasubramaniyam"

{schema_json}"""


# Single-pass user prompt template — placeholders match _extract_single_pass kwargs.
# voicemail_note must include its own leading newline when set; empty string otherwise.
# This pattern matches the other Hub-migrated prompts.
MEMOIR_USER_TEMPLATE = """Analyze the following sales call transcript and produce a structured Memoir summary.

## CONTEXT

Call Duration: {call_duration_minutes} minutes
Number of Speakers: {num_speakers}
Language: {language}
Number of Segments: {num_segments}{voicemail_note}

## TRANSCRIPT

\"\"\"
{transcript_text}
\"\"\"

## REQUIRED OUTPUT

Respond with a JSON object matching the Memoir schema from the system prompt."""


# --- Transcript formatting (reused from intelligence.py pattern) ---

def _format_transcript(segments: list[dict]) -> str:
    """Convert segments into LLM-readable format."""
    lines = []
    for seg in segments:
        speaker = seg.get("speaker", "UNKNOWN")
        start = seg.get("start", 0)
        end = seg.get("end", 0)

        start_fmt = f"{int(start // 60):02d}:{int(start % 60):02d}"
        end_fmt = f"{int(end // 60):02d}:{int(end % 60):02d}"

        text = seg.get("text", "").strip()
        if not text:
            continue

        line = f'{speaker} [{start_fmt}-{end_fmt}]: "{text}"'

        translation = seg.get("translation", "")
        if translation and translation.strip():
            line += f" (Translation: {translation.strip()})"

        lines.append(line)

    return "\n".join(lines)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~1 token per 3.5 chars."""
    return max(1, int(len(text) / 3.5))


def _estimate_num_ctx(
    system_prompt: str,
    user_prompt: str,
    output_buffer: int = 4000,
    safety_margin: int = 512,
    min_ctx: int = 4096,
    max_ctx: int = 16384,
) -> int:
    """Calculate minimum num_ctx needed for a Memoir LLM call."""
    prompt_tokens = _estimate_tokens(system_prompt) + _estimate_tokens(user_prompt)
    needed = prompt_tokens + output_buffer + safety_margin
    return max(min_ctx, min(needed, max_ctx))


def _detect_off_the_record(segments: list[dict]) -> bool:
    """Check if any segment contains off-the-record language."""
    for seg in segments:
        text = seg.get("text", "").lower()
        for keyword in OFF_THE_RECORD_KEYWORDS:
            if keyword in text:
                return True
    return False


def _redact_off_the_record(segments: list[dict]) -> list[dict]:
    """Remove segments containing off-the-record language + the segment after.

    The confidential info usually follows the marker phrase, so we strip
    both the trigger segment and the next one. Returns a new list.
    """
    skip_indices = set()
    for i, seg in enumerate(segments):
        text = seg.get("text", "").lower()
        for keyword in OFF_THE_RECORD_KEYWORDS:
            if keyword in text:
                skip_indices.add(i)
                skip_indices.add(i + 1)  # next segment (may be out of range — filtered below)
                break

    redacted = [seg for i, seg in enumerate(segments) if i not in skip_indices]
    removed = len(segments) - len(redacted)
    if removed:
        logger.info("Memoir: redacted %d segment(s) with off-the-record content", removed)
    return redacted


def _detect_voicemail(segments: list[dict], total_duration: float) -> bool:
    """Detect if this is a voicemail (single speaker, short duration)."""
    speakers = set(s.get("speaker") for s in segments if s.get("speaker"))
    return len(speakers) <= 1 and total_duration < 180  # <3 minutes


# --- JSON repair (same logic as intelligence.py) ---

def _repair_json(text: str) -> Optional[dict]:
    """Attempt to repair malformed JSON from LLM output."""
    if not text or not text.strip():
        return None

    original = text.strip()

    # 1. Remove trailing commas before } or ]
    cleaned = re.sub(r",\s*([}\]])", r"\1", original)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 2. Close truncated output
    repaired = cleaned

    # Close any unclosed string
    in_string = False
    for i, ch in enumerate(repaired):
        if ch == '"' and (i == 0 or repaired[i - 1] != "\\"):
            in_string = not in_string
    if in_string:
        repaired += '"'

    # Remove trailing partial key-value
    repaired = repaired.rstrip()
    repaired = re.sub(r',\s*"[^"]*"\s*:\s*$', "", repaired)
    repaired = re.sub(r',\s*"[^"]*"\s*:\s*""$', "", repaired)
    repaired = repaired.rstrip().rstrip(",")

    # Close all unclosed brackets/braces
    stack = []
    in_str = False
    for i, ch in enumerate(repaired):
        if ch == '"' and (i == 0 or repaired[i - 1] != "\\"):
            in_str = not in_str
        if in_str:
            continue
        if ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in ("}", "]") and stack and stack[-1] == ch:
            stack.pop()

    if stack:
        repaired += "".join(reversed(stack))

    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)

    try:
        result = json.loads(repaired)
        logger.info("Memoir JSON repair successful (closed %d bracket(s))", len(stack))
        return result
    except json.JSONDecodeError:
        pass

    # 3. Last resort: find largest parseable JSON object
    for end in range(len(cleaned) - 1, 0, -1):
        if cleaned[end] not in ("}", "]"):
            continue
        candidate = cleaned[: end + 1]
        try:
            result = json.loads(candidate)
            logger.info("Memoir JSON repair: extracted valid object (first %d chars)", end + 1)
            return result
        except json.JSONDecodeError:
            continue

    logger.warning("Memoir JSON repair failed")
    return None


# --- Map-Reduce chunking ---

def _chunk_transcript(
    segments: list[dict],
    max_tokens: int = 4000,
    overlap_tokens: int = 500,
) -> list[list[dict]]:
    """Split segments into chunks for Map-Reduce processing."""
    chunks = []
    current_chunk = []
    current_tokens = 0
    overlap_buffer = []

    for seg in segments:
        text = seg.get("text", "")
        seg_tokens = _estimate_tokens(text)

        if current_tokens + seg_tokens > max_tokens and current_chunk:
            chunks.append(current_chunk)
            current_chunk = list(overlap_buffer)
            current_tokens = sum(
                _estimate_tokens(s.get("text", "")) for s in current_chunk
            )

        current_chunk.append(seg)
        current_tokens += seg_tokens

        overlap_buffer.append(seg)
        while sum(_estimate_tokens(s.get("text", "")) for s in overlap_buffer) > overlap_tokens:
            overlap_buffer.pop(0)

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _merge_memoir_extractions(partials: list[dict]) -> dict:
    """Merge multiple partial Memoir extraction results into one."""
    merged = {
        "call_metadata": {},
        "discovery_fields": {
            "budget": {"confidence": "ambiguous"},
            "authority": {"confidence": "ambiguous"},
            "need": {"confidence": "ambiguous"},
            "timeline": {"confidence": "ambiguous"},
            "tech_stack": {"confidence": "ambiguous"},
            "team": {"confidence": "ambiguous"},
            "competition": {"confidence": "ambiguous"},
        },
        "narrative_summary": "",
        "action_items": {"rep_actions": [], "prospect_commitments": []},
        "next_steps": {},
        "signals": {"buying_signals": [], "risk_signals": []},
        "disclaimer": {"ambiguous_topics": [], "confidential_content_detected": False},
        "detected_introductions": [],
        "overall_confidence_score": 0,
    }

    confidence_rank = {"definitive": 4, "indicative": 3, "inferred": 2, "ambiguous": 1}
    narratives = []

    for partial in partials:
        # Call metadata: take last (most context)
        if partial.get("call_metadata"):
            merged["call_metadata"] = partial["call_metadata"]

        # Discovery fields: take highest confidence per field
        p_fields = partial.get("discovery_fields", {})
        for field_name in ("budget", "authority", "need", "timeline", "tech_stack", "team", "competition"):
            p_field = p_fields.get(field_name, {})
            m_field = merged["discovery_fields"][field_name]
            p_conf = confidence_rank.get(p_field.get("confidence", "ambiguous"), 1)
            m_conf = confidence_rank.get(m_field.get("confidence", "ambiguous"), 1)
            if p_conf > m_conf:
                merged["discovery_fields"][field_name] = p_field

        # Narrative: collect all, merge later
        if partial.get("narrative_summary"):
            narratives.append(partial["narrative_summary"])

        # Action items: collect all
        p_actions = partial.get("action_items", {})
        merged["action_items"]["rep_actions"].extend(p_actions.get("rep_actions", []))
        merged["action_items"]["prospect_commitments"].extend(p_actions.get("prospect_commitments", []))

        # Next steps: take last (most context)
        if partial.get("next_steps"):
            merged["next_steps"] = partial["next_steps"]

        # Signals: collect all
        p_signals = partial.get("signals", {})
        merged["signals"]["buying_signals"].extend(p_signals.get("buying_signals", []))
        merged["signals"]["risk_signals"].extend(p_signals.get("risk_signals", []))

        # Disclaimer: merge
        p_disc = partial.get("disclaimer", {})
        merged["disclaimer"]["ambiguous_topics"].extend(p_disc.get("ambiguous_topics", []))
        if p_disc.get("confidential_content_detected"):
            merged["disclaimer"]["confidential_content_detected"] = True
        if p_disc.get("transcript_quality_issues"):
            merged["disclaimer"]["transcript_quality_issues"] = p_disc["transcript_quality_issues"]

        # Detected introductions: collect all
        merged["detected_introductions"].extend(
            partial.get("detected_introductions", [])
        )

        # Confidence: take highest from any chunk
        if partial.get("overall_confidence_score"):
            merged["overall_confidence_score"] = max(
                merged["overall_confidence_score"],
                partial["overall_confidence_score"],
            )

    # Merge narratives
    if narratives:
        merged["narrative_summary"] = "\n\n".join(narratives)

    # Deduplicate signals by signal text
    seen_buying = set()
    unique_buying = []
    for s in merged["signals"]["buying_signals"]:
        key = (s.get("signal", "") or "").lower()[:50]
        if key and key not in seen_buying:
            seen_buying.add(key)
            unique_buying.append(s)
    merged["signals"]["buying_signals"] = unique_buying

    seen_risk = set()
    unique_risk = []
    for s in merged["signals"]["risk_signals"]:
        key = (s.get("signal", "") or "").lower()[:50]
        if key and key not in seen_risk:
            seen_risk.add(key)
            unique_risk.append(s)
    merged["signals"]["risk_signals"] = unique_risk

    # Deduplicate introductions by speaker_label (keep first occurrence)
    seen_labels = set()
    unique_intros = []
    for intro in merged["detected_introductions"]:
        label = intro.get("speaker_label", "")
        if label and label not in seen_labels:
            seen_labels.add(label)
            unique_intros.append(intro)
    merged["detected_introductions"] = unique_intros

    return merged


# --- Confidence scoring ---

CONFIDENCE_WEIGHTS = {
    "definitive": 100,
    "indicative": 70,
    "inferred": 40,
    "ambiguous": 0,
}

FIELD_NAMES = ["budget", "authority", "need", "timeline", "tech_stack", "team", "competition"]


def _compute_confidence_score(result: dict) -> int:
    """Compute weighted average confidence score (0-100) from discovery fields."""
    fields = result.get("discovery_fields", {})
    if not fields:
        return 0

    total_weight = 0
    total_score = 0

    for field_name in FIELD_NAMES:
        field = fields.get(field_name) or {}
        conf = field.get("confidence", "ambiguous") if isinstance(field, dict) else "ambiguous"
        score = CONFIDENCE_WEIGHTS.get(conf, 0)
        total_score += score
        total_weight += 1

    if total_weight == 0:
        return 0

    return round(total_score / total_weight)


def _validate_confidence(result: dict) -> dict:
    """
    Validate confidence tags against source quotes.

    If a field is tagged "definitive" but the quote is weak/vague,
    downgrade to "indicative". Prevents hallucinated high confidence.
    """
    fields = result.get("discovery_fields", {})
    if not fields:
        return result

    weak_indicators = [
        "not discussed",
        "not mentioned",
        "no information",
        "unclear",
        "unknown",
        "n/a",
    ]

    for field_name in FIELD_NAMES:
        field = fields.get(field_name, {})
        if not isinstance(field, dict):
            continue

        conf = field.get("confidence", "ambiguous")
        quote = (field.get("source_quote") or "").lower().strip()

        # If definitive but weak quote, downgrade
        if conf == "definitive" and quote:
            if any(indicator in quote for indicator in weak_indicators):
                field["confidence"] = "indicative"
                logger.info(
                    "Memoir: downgraded %s from definitive to indicative (weak quote: '%s')",
                    field_name, quote[:50],
                )

        # If definitive but no quote at all, downgrade
        if conf == "definitive" and not quote:
            field["confidence"] = "indicative"
            logger.info("Memoir: downgraded %s from definitive to indicative (no quote)", field_name)

    return result


# --- Main extractor class ---

class MemoirExtractor:
    """
    Extracts structured Memoir call summary from transcripts via LLM.

    Self-contained — takes pure data, returns pure data.
    Compatible with Ollama and vLLM (Ollama native API).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b-instruct",
        temperature: float = 0.1,
        timeout: float = 18000.0,
        max_single_pass_tokens: int = 12000,
        num_ctx: int = 16384,
        team_static_block: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.max_single_pass_tokens = max_single_pass_tokens
        self.num_ctx = num_ctx

        # Tier 5.6 — singleton httpx client with explicit timeouts + keep-alive
        # pool. Memoir extractor map-reduces over chunks via ThreadPoolExecutor;
        # the singleton must be thread-safe (httpx.Client is — POST/GET only,
        # no per-call cookies). Read timeout matches self.timeout.
        self._http_client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=10.0, read=timeout, write=30.0, pool=10.0),
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=40,
                keepalive_expiry=120.0,
            ),
        )

        # Tier 4.2 — optional team-static prefix for 3-layer Claude caching.
        # Pass team-specific data (CRM contacts, vocabulary, company profile)
        # sorted deterministically. When set, Claude calls use call_layered
        # so this prefix gets its own cache entry per team. Ignored by
        # Ollama. Pass None / empty to fall back to single-layer caching.
        self.team_static_block = team_static_block or None

        # Tier 4.0.b — per-call cost data accumulated for ai_prompt_log
        # persistence. Caller drains via get_prompt_log() after extraction.
        self._prompt_log: List[dict] = []

        # Tier 4.1 — provider selection. Defaults to Ollama (today's behavior).
        self._provider = os.getenv("INTELLIGENCE_PROVIDER", "ollama").lower()
        self._claude_client = None
        self._claude_model = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
        self._cache_ttl = os.getenv("CLAUDE_CACHE_TTL", "1h")

    def _record_call_stats(self, *, provider: str, model: str,
                            input_tokens: int, output_tokens: int,
                            cache_read: int, cache_creation: int,
                            cost_usd: float, duration_ms: int,
                            parse_success: bool, error: Optional[str]) -> None:
        """Append a per-call entry to self._prompt_log for ai_prompt_log persistence."""
        entry = {
            "prompt_number": 10,  # Phase 10 (Memoir extraction)
            "prompt_name": "P10_memoir",
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
            entry["error"] = error
        self._prompt_log.append(entry)

    def _call_ollama_internal(self, system_prompt: str, user_prompt: str, max_retries: int = 2) -> Optional[dict]:
        """Existing Ollama call path — unchanged behavior, with prompt_log emission."""
        dynamic_ctx = _estimate_num_ctx(
            system_prompt, user_prompt,
            output_buffer=4000,
            safety_margin=512,
            min_ctx=4096,
            max_ctx=self.num_ctx,
        )
        logger.info("Memoir LLM: dynamic num_ctx=%d (max=%d)", dynamic_ctx, self.num_ctx)

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
            content = None
            call_start = time.time()
            try:
                resp = self._http_client.post("/api/chat", json=payload)
                resp.raise_for_status()

                data = resp.json()
                content = data["message"]["content"]
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
                    repaired = _repair_json(content) if content else None
                    if repaired:
                        self._record_call_stats(
                            provider="ollama", model=self.model,
                            input_tokens=input_tokens, output_tokens=output_tokens,
                            cache_read=0, cache_creation=0,
                            cost_usd=0.0, duration_ms=duration_ms,
                            parse_success=True, error="json_repaired",
                        )
                        return repaired
                    if attempt < max_retries:
                        expanded_ctx = min(int(dynamic_ctx * 1.5), self.num_ctx)
                        logger.warning(
                            "Memoir JSON repair failed — retrying with num_ctx=%d (was %d)",
                            expanded_ctx, dynamic_ctx,
                        )
                        payload["options"]["num_ctx"] = expanded_ctx
                        continue
                    logger.error("Memoir LLM JSON failed after %d attempts + repair", max_retries)
                    self._record_call_stats(
                        provider="ollama", model=self.model,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                        cache_read=0, cache_creation=0,
                        cost_usd=0.0, duration_ms=duration_ms,
                        parse_success=False, error=f"json_decode: {e}",
                    )
                    return None

            except httpx.TimeoutException:
                logger.error("Memoir LLM call timed out (attempt %d/%d)", attempt, max_retries)
                self._record_call_stats(
                    provider="ollama", model=self.model,
                    input_tokens=0, output_tokens=0,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=int((time.time() - call_start) * 1000),
                    parse_success=False, error="timeout",
                )
                return None
            except httpx.HTTPStatusError as e:
                logger.error("Memoir LLM HTTP error: %s (attempt %d/%d)", e, attempt, max_retries)
                self._record_call_stats(
                    provider="ollama", model=self.model,
                    input_tokens=0, output_tokens=0,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=int((time.time() - call_start) * 1000),
                    parse_success=False, error=str(e),
                )
                return None
            except (KeyError, IndexError) as e:
                logger.error("Memoir LLM response structure error: %s (attempt %d/%d)", e, attempt, max_retries)
                self._record_call_stats(
                    provider="ollama", model=self.model,
                    input_tokens=0, output_tokens=0,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=int((time.time() - call_start) * 1000),
                    parse_success=False, error=str(e),
                )
                return None
            except Exception as e:
                logger.error("Memoir LLM unexpected error: %s (attempt %d/%d)", e, attempt, max_retries)
                self._record_call_stats(
                    provider="ollama", model=self.model,
                    input_tokens=0, output_tokens=0,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=int((time.time() - call_start) * 1000),
                    parse_success=False, error=str(e),
                )
                return None

        return None

    def _call_claude(self, system_prompt: str, user_prompt: str,
                     max_retries: int = 2) -> Optional[dict]:
        """Anthropic Claude call with system-prompt caching.

        Layer 1 = system_prompt + JSON schema (org-static, cached forever within TTL)
        Layer 3 = user_prompt (transcript content, never cached)
        No Layer 2 — Memoir extraction doesn't have a clean per-team static
        prefix. The cache benefit comes from system-prompt reuse.
        """
        if self._claude_client is None:
            try:
                import anthropic
                api_key = os.getenv("ANTHROPIC_API_KEY", "")
                if not api_key:
                    logger.error("MemoirExtractor: ANTHROPIC_API_KEY not set; falling back to None")
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
                logger.error(f"MemoirExtractor: anthropic SDK not installed: {e}")
                return None

        cache_control = (
            {"type": "ephemeral", "ttl": "1h"} if self._cache_ttl == "1h"
            else {"type": "ephemeral"}
        )

        for attempt in range(1, max_retries + 1):
            call_start = time.time()
            try:
                # Tier 4.2 — 3-layer caching when team_static_block is set,
                # otherwise fall back to single-layer (system only).
                if self.team_static_block:
                    user_blocks = [
                        {
                            "type": "text",
                            "text": self.team_static_block,
                            "cache_control": cache_control,
                        },
                        {"type": "text", "text": user_prompt},
                    ]
                    user_content = user_blocks
                else:
                    user_content = user_prompt

                response = self._claude_client.messages.create(
                    model=self._claude_model,
                    # 4096 was truncating the full Memoir schema mid-JSON for
                    # calls with rich discovery_fields, leaving narrative_
                    # summary / action_items / next_steps as nulls after
                    # bracket-repair. The full schema serializes to ~6 KB
                    # at p99, so give 8192 output headroom.
                    max_tokens=8192,
                    system=[{
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": cache_control,
                    }],
                    messages=[{"role": "user", "content": user_content}],
                )
                duration_ms = int((time.time() - call_start) * 1000)

                usage = response.usage
                input_tokens = getattr(usage, "input_tokens", 0)
                output_tokens = getattr(usage, "output_tokens", 0)
                cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
                cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0

                base_rate = 3.0  # Sonnet 4.6
                output_rate = 15.0
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
                    repaired = _repair_json(content) if content else None
                    if repaired:
                        self._record_call_stats(
                            provider="anthropic", model=self._claude_model,
                            input_tokens=input_tokens, output_tokens=output_tokens,
                            cache_read=cache_read, cache_creation=cache_creation,
                            cost_usd=round(cost, 6), duration_ms=duration_ms,
                            parse_success=True, error="json_repaired",
                        )
                        return repaired
                    if attempt < max_retries:
                        continue
                    self._record_call_stats(
                        provider="anthropic", model=self._claude_model,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                        cache_read=cache_read, cache_creation=cache_creation,
                        cost_usd=round(cost, 6), duration_ms=duration_ms,
                        parse_success=False, error=f"json_decode: {e}",
                    )
                    return None
            except Exception as e:
                logger.error(f"MemoirExtractor Claude call failed (attempt %d/%d): %s", attempt, max_retries, e)
                self._record_call_stats(
                    provider="anthropic", model=self._claude_model,
                    input_tokens=0, output_tokens=0,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=int((time.time() - call_start) * 1000),
                    parse_success=False, error=str(e),
                )
                return None

        return None

    def _call_vllm(self, system_prompt: str, user_prompt: str, max_retries: int = 2) -> Optional[dict]:
        """Tier 5.1.a — vLLM via OpenAI-compat /chat/completions.

        Mirrors _call_ollama_internal but without Ollama-specific options
        (num_ctx is irrelevant; vLLM has its own --max-model-len).
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
            # See max_tokens note on the Claude path — 4096 was truncating
            # the full Memoir schema mid-JSON, leaving narrative_summary /
            # action_items / next_steps null after bracket-repair.
            "max_tokens": 8192,
            "response_format": {"type": "json_object"},
        }

        for attempt in range(1, max_retries + 1):
            content = None
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
                    repaired = _repair_json(content) if content else None
                    if repaired:
                        self._record_call_stats(
                            provider="vllm", model=vllm_model,
                            input_tokens=input_tokens, output_tokens=output_tokens,
                            cache_read=0, cache_creation=0,
                            cost_usd=0.0, duration_ms=duration_ms,
                            parse_success=True, error="repaired",
                        )
                        return repaired
                    if attempt < max_retries:
                        continue
                    self._record_call_stats(
                        provider="vllm", model=vllm_model,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                        cache_read=0, cache_creation=0,
                        cost_usd=0.0, duration_ms=duration_ms,
                        parse_success=False, error=f"json_decode: {e}",
                    )
                    return None
            except Exception as e:
                logger.warning(f"MemoirExtractor vLLM call failed (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    continue
                self._record_call_stats(
                    provider="vllm", model=vllm_model,
                    input_tokens=0, output_tokens=0,
                    cache_read=0, cache_creation=0,
                    cost_usd=0.0, duration_ms=int((time.time() - call_start) * 1000),
                    parse_success=False, error=str(e),
                )
                return None
        return None

    @traceable_llm("memoir-extractor")
    def _call_llm(self, system_prompt: str, user_prompt: str, max_retries: int = 2) -> Optional[dict]:
        """Tier 4.1 + 5.1.a — provider dispatcher.

        INTELLIGENCE_PROVIDER:
          claude → _call_claude (Anthropic + 3-layer cache when team_static_block set)
          vllm   → _call_vllm
          ollama (default) → _call_ollama_internal

        @traceable_llm — single decoration covers all three backends with one
        LangSmith trace per call. Identity no-op when LANGSMITH_ENABLED=false.
        """
        if self._provider == "claude":
            return self._call_claude(system_prompt, user_prompt, max_retries)
        if self._provider == "vllm":
            return self._call_vllm(system_prompt, user_prompt, max_retries)
        return self._call_ollama_internal(system_prompt, user_prompt, max_retries)

    def get_prompt_log(self) -> List[dict]:
        """Drain accumulated prompt_log entries (one per LLM call)."""
        log = list(self._prompt_log)
        self._prompt_log.clear()
        return log

    def _build_system_prompt(self) -> str:
        """Pre-render the Memoir system prompt with JSON schema injected.

        Used by the chunk path (`_extract_map_reduce`) which passes a
        pre-rendered system string to _memoir_dispatch (no Hub variables).
        Single-pass path uses MEMOIR_SYSTEM_PROMPT directly as a template
        with {schema_json} placeholder and lets get_prompt_sync substitute.
        """
        schema_str = get_memoir_schema()
        return MEMOIR_SYSTEM_PROMPT.format(schema_json=f"\nJSON SCHEMA:\n{schema_str}")

    def _build_user_prompt(self, segments: list[dict], metadata: dict) -> str:
        """Build the user prompt with transcript and context."""
        transcript_text = _format_transcript(segments)

        context_lines = [
            f"Call Duration: {metadata.get('duration_minutes', 0):.0f} minutes",
            f"Number of Speakers: {metadata.get('num_speakers', 2)}",
            f"Language: {metadata.get('language', 'en')}",
            f"Number of Segments: {len(segments)}",
        ]
        if metadata.get("is_voicemail"):
            context_lines.append("NOTE: This appears to be a voicemail (single speaker, short duration). Produce minimal summary with null discovery fields.")

        prompt = (
            f"Analyze the following sales call transcript and produce a structured Memoir summary.\n\n"
            f"## CONTEXT\n\n"
            + "\n".join(context_lines) + "\n\n"
            f"## TRANSCRIPT\n\n"
            f'"""\n{transcript_text}\n"""\n\n'
            f"## REQUIRED OUTPUT\n\n"
            f"Respond with a JSON object matching the Memoir schema from the system prompt."
        )
        return prompt

    def _extract_single_pass(self, segments: list[dict], metadata: dict) -> Optional[dict]:
        """Single-pass extraction for shorter transcripts.

        Passes both system and user as templates with variables — Hub can
        override either when LANGCHAIN_ENABLED=true. When Hub is disabled,
        get_prompt_sync renders the in-code defaults via .format(**vars)
        producing byte-identical output to the legacy _build_user_prompt path.
        """
        is_voicemail = metadata.get("is_voicemail", False)
        voicemail_note = (
            "\nNOTE: This appears to be a voicemail (single speaker, short duration). "
            "Produce minimal summary with null discovery fields."
            if is_voicemail else ""
        )
        return _memoir_dispatch(
            self,
            MEMOIR_SYSTEM_PROMPT,           # template form ({schema_json} placeholder)
            MEMOIR_USER_TEMPLATE,           # template form (6 placeholders)
            # System vars
            schema_json=f"\nJSON SCHEMA:\n{get_memoir_schema()}",
            # User vars (formatted to match the old f-string output exactly)
            call_duration_minutes=f"{metadata.get('duration_minutes', 0):.0f}",
            num_speakers=metadata.get("num_speakers", 2),
            language=metadata.get("language", "en"),
            num_segments=len(segments),
            voicemail_note=voicemail_note,
            transcript_text=_format_transcript(segments),
        )

    def _extract_map_reduce(self, segments: list[dict], metadata: dict) -> Optional[dict]:
        """Map-Reduce extraction for longer transcripts."""
        chunks = _chunk_transcript(segments, max_tokens=4000, overlap_tokens=500)
        num_chunks = len(chunks)
        logger.info("Memoir Map-Reduce: %d chunks from %d segments", num_chunks, len(segments))

        system_prompt = self._build_system_prompt()

        def _process_chunk(i: int, chunk: list[dict]) -> Optional[dict]:
            transcript_text = _format_transcript(chunk)
            user_prompt = (
                f"This is chunk {i + 1} of {num_chunks} from a longer call.\n"
                f"Call Duration: {metadata.get('duration_minutes', 0):.0f} minutes\n"
                f"Language: {metadata.get('language', 'en')}\n\n"
                f"## TRANSCRIPT CHUNK\n\n"
                f'"""\n{transcript_text}\n"""\n\n'
                f"## REQUIRED OUTPUT\n\n"
                f"Respond with a JSON object matching the Memoir schema from the system prompt. "
                f"Extract all information visible in this chunk."
            )
            # Plan §6.2 + §9.4 — same Hub+router pattern as _extract_single_pass.
            # Note: system_prompt is the same across chunks (schema-augmented
            # by _build_system_prompt); user_prompt varies per chunk.
            return _memoir_dispatch(self, system_prompt, user_prompt)

        max_workers = min(num_chunks, 4)
        partials = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_process_chunk, i, chunk): i
                for i, chunk in enumerate(chunks)
            }
            for future in as_completed(futures):
                chunk_idx = futures[future]
                try:
                    result = future.result()
                    if result:
                        partials.append((chunk_idx, result))
                except Exception as e:
                    logger.error("Memoir map-reduce chunk %d failed: %s", chunk_idx, e)

        if not partials:
            return None

        partials.sort(key=lambda x: x[0])
        return _merge_memoir_extractions([p[1] for p in partials])

    def extract_memoir_summary(
        self,
        segments: list[dict],
        total_duration: float = 0.0,
        num_speakers: int = 2,
        language: str = "en",
    ) -> Optional[dict]:
        """
        Main extraction entry point.

        Args:
            segments: List of segment dicts (speaker, start, end, text, translation)
            total_duration: Call duration in seconds
            num_speakers: Number of speakers detected
            language: Primary language of the call

        Returns:
            Validated MemoirSummary dict, or None on failure
        """
        if not segments:
            logger.warning("No segments provided for Memoir extraction")
            return None

        start_time = time.time()

        metadata = {
            "duration_minutes": round(total_duration / 60, 1),
            "num_speakers": num_speakers,
            "language": language,
        }

        # Check for off-the-record content and redact before LLM sees it
        has_confidential = _detect_off_the_record(segments)
        if has_confidential:
            logger.info("Memoir: off-the-record language detected — redacting before LLM")
            segments = _redact_off_the_record(segments)

        # Check for voicemail
        is_voicemail = _detect_voicemail(segments, total_duration)
        if is_voicemail:
            logger.info("Memoir: voicemail detected (single speaker, <3 min)")
            metadata["is_voicemail"] = True

        # Estimate tokens to decide single-pass vs map-reduce
        transcript_text = _format_transcript(segments)
        estimated_tokens = _estimate_tokens(transcript_text)

        logger.info(
            "Memoir extraction: %d segments, ~%d tokens (threshold: %d)",
            len(segments), estimated_tokens, self.max_single_pass_tokens,
        )

        if estimated_tokens <= self.max_single_pass_tokens:
            raw_result = self._extract_single_pass(segments, metadata)
        else:
            raw_result = self._extract_map_reduce(segments, metadata)

        if raw_result is None:
            logger.error("Memoir extraction returned no results")
            return None

        # Validate confidence tags
        raw_result = _validate_confidence(raw_result)

        # Compute overall confidence score from fields
        computed_score = _compute_confidence_score(raw_result)
        # Use LLM's score if reasonable, otherwise use computed
        llm_score = raw_result.get("overall_confidence_score", 0)
        if isinstance(llm_score, (int, float)) and 0 <= llm_score <= 100:
            # Average LLM and computed for balance
            raw_result["overall_confidence_score"] = round((llm_score + computed_score) / 2)
        else:
            raw_result["overall_confidence_score"] = computed_score

        # Set confidential flag if detected
        if has_confidential:
            disc = raw_result.setdefault("disclaimer", {})
            disc["confidential_content_detected"] = True

        # Defense-in-depth before Pydantic: trim any source_quote that would
        # exceed the schema's 300-char limit (LLM sometimes prepends speaker
        # name or returns multi-sentence quotes despite the prompt). Falling
        # back to raw_result on a length-only error throws away an otherwise-
        # valid extraction, so we pre-trim then validate.
        for field_name, field_value in (raw_result.get("discovery_fields") or {}).items():
            if not isinstance(field_value, dict):
                continue
            sq = field_value.get("source_quote")
            if isinstance(sq, str) and len(sq) > 300:
                # Strip a leading "Speaker Name:" prefix if present
                # (matches prompt's WRONG-example failure mode)
                if ":" in sq[:80]:
                    sq = sq.split(":", 1)[1].strip()
                if len(sq) > 300:
                    sq = sq[:297].rstrip() + "..."
                field_value["source_quote"] = sq

        # Validate through Pydantic
        try:
            validated = MemoirSummary.model_validate(raw_result)
            result_dict = validated.model_dump()
        except Exception as e:
            logger.warning("Memoir Pydantic validation failed, using raw result: %s", e)
            result_dict = raw_result

        # Add extraction metadata
        result_dict["extraction_model"] = self.model
        result_dict["extraction_timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        elapsed = time.time() - start_time
        logger.info("Memoir extraction completed in %.1fs", elapsed)

        return result_dict


# --- Convenience function (matches run_full_intelligence pattern) ---

def run_memoir_extraction(
    segments: list[dict],
    total_duration: float,
    num_speakers: int = 2,
    language: str = "en",
    coaching_metrics: Optional[dict] = None,
    base_url: str = "http://localhost:11434",
    model: str = "qwen2.5:7b-instruct",
    temperature: float = 0.1,
    timeout: float = 18000.0,
    max_single_pass_tokens: int = 12000,
    num_ctx: int = 16384,
    team_static_block: Optional[str] = None,
) -> Optional[dict]:
    """
    Run Memoir extraction pipeline.

    Args match run_full_intelligence() for easy swapping in pipeline.py.
    coaching_metrics is accepted but not used by Memoir (accepted for API compatibility).

    Returns:
        MemoirSummary dict, or None on failure.
    """
    extractor = MemoirExtractor(
        base_url=base_url,
        model=model,
        temperature=temperature,
        timeout=timeout,
        max_single_pass_tokens=max_single_pass_tokens,
        num_ctx=num_ctx,
        team_static_block=team_static_block,
    )

    result = extractor.extract_memoir_summary(
        segments=segments,
        total_duration=total_duration,
        num_speakers=num_speakers,
        language=language,
    )

    if result is None:
        return None

    # Tier 4.0.b — embed per-call prompt_log so the caller can forward to
    # core_api. Use a sentinel key (`_prompt_log`) prefixed with underscore
    # to avoid colliding with Memoir's own JSON schema fields.
    log = extractor.get_prompt_log()
    if log:
        result["_prompt_log"] = log

    return result
