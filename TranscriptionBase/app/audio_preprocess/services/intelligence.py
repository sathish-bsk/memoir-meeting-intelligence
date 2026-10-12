"""
Phase 7A+7B: LLM intelligence extraction + sentiment + scoring.

Self-contained module — zero imports from pipeline/transcribe/audio.
Designed for future extraction into post-processing microservice (Phase 9).

Takes pure dicts (segments with speaker labels + timestamps), calls Ollama/vLLM,
returns validated CallIntelligence dict.

7A: LLM extraction (competitors, objections, sentiment, etc.) in a single pass
7B: Methodology scoring (MEDDIC/BANT), deal health, coaching scorecard (post-extraction logic)
"""

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import httpx

from .schemas import CallIntelligence, get_extraction_schema

logger = logging.getLogger(__name__)


# --- Ollama/vLLM availability check ---

def check_intelligence_llm_available(base_url: str) -> dict:
    """Check if the intelligence LLM is available via Ollama/vLLM."""
    try:
        resp = httpx.get(f"{base_url}/v1/models", timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            models = [m.get("id", "") for m in data.get("data", [])]
            return {"available": True, "models": models}
    except Exception:
        pass
    return {"available": False, "models": []}


# --- System prompt ---

SYSTEM_PROMPT = """You are a sales call intelligence analyst. Your job is to extract structured business intelligence from a sales call transcript.

RULES:
1. Only extract information EXPLICITLY stated in the transcript. If a data point was not discussed, set it to null or use empty arrays.
2. NEVER fabricate or infer information that is not clearly present in the conversation.
3. Include exact quotes from the transcript as evidence for each extraction where applicable.
4. The transcript uses speaker labels like "SPEAKER_00:" and "SPEAKER_01:" (or pre-resolved names). Determine which speaker is the sales rep and which is the customer based on context (the rep typically introduces the product, asks discovery questions, and presents solutions). IMPORTANT: Do NOT infer speaker identities from names mentioned in dialogue — a speaker saying "Good call, Satish" does NOT mean the speaker IS Satish, they are ADDRESSING Satish. Use only the speaker labels from the transcript lines.
5. For competitor mentions, include indirect references like "the other tool" or "what we're currently using" if you can identify the specific product.
6. For objections, classify the type accurately and note whether the rep addressed the objection.

SENTIMENT RULES (sales-specific context):
- Price objections ("that's expensive", "over our budget") are NEUTRAL, not negative — they are buying signals indicating the customer is seriously considering the purchase.
- Asking about competitors ("we're also looking at X") is NEUTRAL — indicates active evaluation, which is a positive deal signal.
- "Let me think about it" or "we'll get back to you" is NEGATIVE — classic stall tactic, signals deal risk.
- "Send over the contract" or "what's the next step?" is POSITIVE — closing signal.
- "Can you do a discount?" is POSITIVE — indicates readiness to buy, just negotiating terms.
- Classify each segment's sentiment considering the surrounding conversation context, not just the isolated words.

OUTPUT FORMAT:
Return a single JSON object matching the schema below. Do not include any text outside the JSON object.

JSON SCHEMA:
{schema}"""


# --- Transcript formatting ---

def _format_transcript(segments: list[dict]) -> str:
    """Convert segments into LLM-readable format."""
    lines = []
    for seg in segments:
        speaker = seg.get("speaker", "UNKNOWN")
        start = seg.get("start", 0)
        end = seg.get("end", 0)

        # Format timestamps as MM:SS
        start_fmt = f"{int(start // 60):02d}:{int(start % 60):02d}"
        end_fmt = f"{int(end // 60):02d}:{int(end % 60):02d}"

        text = seg.get("text", "").strip()
        if not text:
            continue

        line = f'{speaker} [{start_fmt}-{end_fmt}]: "{text}"'

        # Add translation if present
        translation = seg.get("translation", "")
        if translation and translation.strip():
            line += f" (Translation: {translation.strip()})"

        lines.append(line)

    return "\n".join(lines)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~1 token per 3.5 chars (works for English + JSON)."""
    return max(1, int(len(text) / 3.5))


def _estimate_num_ctx(
    system_prompt: str,
    user_prompt: str,
    output_buffer: int = 3000,
    safety_margin: int = 512,
    min_ctx: int = 4096,
    max_ctx: int = 16384,
) -> int:
    """
    Calculate minimum num_ctx needed for an LLM call.

    Smaller num_ctx = smaller KV cache = faster inference on CPU.
    Formula: prompt_tokens + output_buffer + safety_margin, clamped to [min_ctx, max_ctx].
    """
    prompt_tokens = _estimate_tokens(system_prompt) + _estimate_tokens(user_prompt)
    needed = prompt_tokens + output_buffer + safety_margin
    return max(min_ctx, min(needed, max_ctx))


def _normalize_llm_output(raw: dict) -> dict:
    """
    Normalize LLM field names to match our Pydantic schema.

    LLMs often use alternative field names (e.g., 'action' instead of
    'description', 'text' instead of 'question'). This function remaps
    known variations before Pydantic validation so validation passes.
    """
    # Field name aliases: LLM may use any of these → remap to schema name
    DESC_ALIASES = ("text", "action", "content", "issue", "summary", "detail")
    QUESTION_ALIASES = ("text", "content", "asked", "query")

    # --- pain_points: need 'description' ---
    for item in raw.get("pain_points", []) or []:
        if isinstance(item, dict) and "description" not in item:
            for alias in DESC_ALIASES:
                if alias in item:
                    item["description"] = item.pop(alias)
                    break

    # --- objections: need 'description' ---
    for item in raw.get("objections", []) or []:
        if isinstance(item, dict) and "description" not in item:
            for alias in DESC_ALIASES:
                if alias in item:
                    item["description"] = item.pop(alias)
                    break

    # --- next_steps: need 'description' ---
    for item in raw.get("next_steps", []) or []:
        if isinstance(item, dict) and "description" not in item:
            for alias in DESC_ALIASES:
                if alias in item:
                    item["description"] = item.pop(alias)
                    break

    # --- questions_asked: need 'question' ---
    for item in raw.get("questions_asked", []) or []:
        if isinstance(item, dict) and "question" not in item:
            for alias in QUESTION_ALIASES:
                if alias in item:
                    item["question"] = item.pop(alias)
                    break

    # --- features: must be dict, not list ---
    features = raw.get("features")
    if isinstance(features, list):
        # LLM returned a flat list of features — wrap into expected dict
        raw["features"] = {
            "features_discussed": [
                f.get("feature", f.get("name", str(f)))
                if isinstance(f, dict) else str(f)
                for f in features
            ],
            "feature_requests": [],
            "use_cases": [],
            "technical_requirements": [],
        }

    # --- segment_sentiments: need 'segment_id', not 'segment_index' ---
    for item in raw.get("segment_sentiments", []) or []:
        if isinstance(item, dict) and "segment_id" not in item:
            if "segment_index" in item:
                item["segment_id"] = item.pop("segment_index")

    return raw


def _repair_json(text: str) -> Optional[dict]:
    """
    Attempt to repair malformed JSON from LLM output.

    Common LLM JSON errors:
    1. Trailing commas: {"a": 1,} → {"a": 1}
    2. Missing closing brackets (truncated output)
    3. Unescaped newlines/tabs in strings
    """
    if not text or not text.strip():
        return None

    original = text.strip()

    # 1. Remove trailing commas before } or ]
    cleaned = re.sub(r",\s*([}\]])", r"\1", original)

    # 2. Try parsing after comma fix
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3. Try closing truncated output
    # Walk backwards to find the last complete JSON value, then close brackets
    repaired = cleaned

    # Close any unclosed string (odd number of unescaped quotes)
    in_string = False
    for i, ch in enumerate(repaired):
        if ch == '"' and (i == 0 or repaired[i - 1] != "\\"):
            in_string = not in_string
    if in_string:
        repaired += '"'

    # Remove any trailing partial key-value (e.g., `,"key": ` or `,"key":`)
    repaired = repaired.rstrip()
    # Strip dangling key-colon patterns: `, "key":` or `, "key": `
    repaired = re.sub(r',\s*"[^"]*"\s*:\s*$', "", repaired)
    # Strip dangling incomplete string values: `, "key": ""`
    repaired = re.sub(r',\s*"[^"]*"\s*:\s*""$', "", repaired)

    # Remove trailing comma
    repaired = repaired.rstrip().rstrip(",")

    # Close all unclosed brackets/braces using a stack for correct order
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

    # Remove trailing commas that may have appeared before the new closers
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)

    try:
        result = json.loads(repaired)
        logger.info("JSON repair successful (closed %d bracket(s))", len(stack))
        return result
    except json.JSONDecodeError:
        pass

    # 4. Last resort: find the largest parseable JSON object from the start
    # Progressively trim from the end until we find valid JSON
    for end in range(len(cleaned) - 1, 0, -1):
        if cleaned[end] not in ("}", "]"):
            continue
        candidate = cleaned[: end + 1]
        try:
            result = json.loads(candidate)
            logger.info("JSON repair: extracted valid object (first %d chars)", end + 1)
            return result
        except json.JSONDecodeError:
            continue

    logger.warning("JSON repair failed — could not recover valid JSON")
    return None


# --- Map-Reduce for long transcripts ---

def _chunk_transcript(
    segments: list[dict],
    max_tokens: int = 4000,
    overlap_tokens: int = 500,
) -> list[list[dict]]:
    """
    Split segments into chunks for Map-Reduce processing.
    Each chunk targets max_tokens with overlap_tokens of overlap.
    """
    chunks = []
    current_chunk = []
    current_tokens = 0
    overlap_buffer = []

    for seg in segments:
        text = seg.get("text", "")
        seg_tokens = _estimate_tokens(text)

        if current_tokens + seg_tokens > max_tokens and current_chunk:
            chunks.append(current_chunk)
            # Start new chunk with overlap from end of previous
            current_chunk = list(overlap_buffer)
            current_tokens = sum(
                _estimate_tokens(s.get("text", "")) for s in current_chunk
            )

        current_chunk.append(seg)
        current_tokens += seg_tokens

        # Keep overlap buffer
        overlap_buffer.append(seg)
        while sum(_estimate_tokens(s.get("text", "")) for s in overlap_buffer) > overlap_tokens:
            overlap_buffer.pop(0)

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _merge_extractions(partials: list[dict]) -> dict:
    """Merge multiple partial extraction results into one."""
    merged = {
        "summary": {"one_line": None, "detailed": None, "key_topics": []},
        "competitors": [],
        "pain_points": [],
        "objections": [],
        "next_steps": [],
        "budget": {"discussed": False},
        "decision_making": {
            "buying_committee": [],
            "champion_identified": False,
        },
        "deal_stage": {"current_stage": "unknown", "confidence": 0.0},
        "features": {
            "features_discussed": [],
            "feature_requests": [],
            "use_cases": [],
            "technical_requirements": [],
        },
        "segment_sentiments": [],
        "overall_sentiment": {},
        "questions_asked": [],
    }

    seen_competitors = set()
    seen_pain_points = set()

    for partial in partials:
        # Summary: take the last one (most context)
        if partial.get("summary", {}).get("one_line"):
            merged["summary"] = partial["summary"]

        # Competitors: deduplicate by name
        for comp in partial.get("competitors", []):
            name = comp.get("name", "").lower()
            if name and name not in seen_competitors:
                seen_competitors.add(name)
                merged["competitors"].append(comp)

        # Pain points: deduplicate by description
        for pp in partial.get("pain_points", []):
            desc = pp.get("description", "").lower()[:50]
            if desc and desc not in seen_pain_points:
                seen_pain_points.add(desc)
                merged["pain_points"].append(pp)

        # Objections, next steps, questions: collect all
        merged["objections"].extend(partial.get("objections", []))
        merged["next_steps"].extend(partial.get("next_steps", []))
        merged["questions_asked"].extend(partial.get("questions_asked", []))

        # Budget: any partial that discussed it wins
        p_budget = partial.get("budget", {})
        if p_budget.get("discussed"):
            merged["budget"] = p_budget

        # Decision making: merge
        p_dm = partial.get("decision_making", {})
        if p_dm.get("decision_maker"):
            merged["decision_making"]["decision_maker"] = p_dm["decision_maker"]
        if p_dm.get("decision_timeline"):
            merged["decision_making"]["decision_timeline"] = p_dm["decision_timeline"]
        if p_dm.get("decision_process"):
            merged["decision_making"]["decision_process"] = p_dm["decision_process"]
        if p_dm.get("champion_identified"):
            merged["decision_making"]["champion_identified"] = True
            merged["decision_making"]["champion_name"] = p_dm.get("champion_name")
        merged["decision_making"]["buying_committee"].extend(
            p_dm.get("buying_committee", [])
        )

        # Deal stage: take highest confidence
        p_stage = partial.get("deal_stage", {})
        if p_stage.get("confidence", 0) > merged["deal_stage"].get("confidence", 0):
            merged["deal_stage"] = p_stage

        # Features: collect all
        p_feat = partial.get("features", {})
        for key in ["features_discussed", "feature_requests", "use_cases", "technical_requirements"]:
            merged["features"][key].extend(p_feat.get(key, []))

        # Sentiments: collect all
        merged["segment_sentiments"].extend(partial.get("segment_sentiments", []))

        # Overall sentiment: take the last one
        if partial.get("overall_sentiment"):
            merged["overall_sentiment"] = partial["overall_sentiment"]

    # Deduplicate lists
    merged["decision_making"]["buying_committee"] = list(
        set(merged["decision_making"]["buying_committee"])
    )
    for key in ["features_discussed", "feature_requests", "use_cases", "technical_requirements"]:
        merged["features"][key] = list(set(merged["features"][key]))

    # Deduplicate key topics
    if merged["summary"].get("key_topics"):
        merged["summary"]["key_topics"] = list(set(merged["summary"]["key_topics"]))

    return merged


# --- Main extractor class ---

class IntelligenceExtractor:
    """
    Extracts structured intelligence from call transcripts via LLM.

    Self-contained — takes pure data, returns pure data.
    Compatible with Ollama and vLLM (OpenAI-compatible API).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b-instruct",
        temperature: float = 0.1,
        timeout: float = 18000.0,
        max_single_pass_tokens: int = 12000,
        num_ctx: int = 8192,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.max_single_pass_tokens = max_single_pass_tokens
        self.num_ctx = num_ctx

    def _call_llm(self, system_prompt: str, user_prompt: str, max_retries: int = 2) -> Optional[dict]:
        """Call Ollama native API and return parsed JSON response.

        Uses /api/chat (not /v1/chat/completions) because the OpenAI-compatible
        endpoint silently ignores options.num_ctx, defaulting to 4096 context.

        Dynamic num_ctx: calculates minimum needed per call instead of fixed value.
        On JSON parse failure: try repair → retry with 1.5x num_ctx → give up.
        """
        dynamic_ctx = _estimate_num_ctx(
            system_prompt, user_prompt,
            output_buffer=3000,
            safety_margin=512,
            min_ctx=4096,
            max_ctx=self.num_ctx,
        )
        logger.info("Intelligence LLM: dynamic num_ctx=%d (max=%d)", dynamic_ctx, self.num_ctx)

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
            try:
                resp = httpx.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=self.timeout,
                )
                resp.raise_for_status()

                data = resp.json()
                content = data["message"]["content"]
                return json.loads(content)

            except httpx.TimeoutException:
                logger.error("Intelligence LLM call timed out after %.0fs (attempt %d/%d)", self.timeout, attempt, max_retries)
                return None
            except httpx.HTTPStatusError as e:
                logger.error("Intelligence LLM HTTP error: %s (attempt %d/%d)", e, attempt, max_retries)
                return None
            except json.JSONDecodeError as e:
                logger.warning("Intelligence LLM JSON parse failed (attempt %d/%d): %s", attempt, max_retries, e)
                # Try repair first (cheap)
                if content:
                    repaired = _repair_json(content)
                    if repaired:
                        return repaired
                # Repair failed — retry with more context (1.5x) if attempts remain
                if attempt < max_retries:
                    expanded_ctx = min(int(dynamic_ctx * 1.5), self.num_ctx)
                    logger.warning(
                        "JSON repair failed — retrying with num_ctx=%d (was %d)",
                        expanded_ctx, dynamic_ctx,
                    )
                    payload["options"]["num_ctx"] = expanded_ctx
                    continue
                logger.error("Intelligence LLM JSON failed after %d attempts + repair", max_retries)
                return None
            except (KeyError, IndexError) as e:
                logger.error("Intelligence LLM response structure error: %s (attempt %d/%d)", e, attempt, max_retries)
                return None
            except Exception as e:
                logger.error("Intelligence LLM unexpected error: %s (attempt %d/%d)", e, attempt, max_retries)
                return None

        return None

    def _extract_single_pass(
        self,
        segments: list[dict],
        metadata: dict,
    ) -> Optional[dict]:
        """Single-pass extraction for shorter transcripts."""
        schema_str = get_extraction_schema()
        system_prompt = SYSTEM_PROMPT.replace("{schema}", schema_str)

        transcript_text = _format_transcript(segments)

        user_prompt = (
            f"Call duration: {metadata.get('duration_seconds', 0)} seconds\n"
            f"Number of speakers: {metadata.get('num_speakers', 2)}\n"
            f"Language: {metadata.get('language', 'en')}\n"
            f"Number of segments: {len(segments)}\n\n"
            f"TRANSCRIPT:\n{transcript_text}"
        )

        return self._call_llm(system_prompt, user_prompt)

    def _extract_map_reduce(
        self,
        segments: list[dict],
        metadata: dict,
    ) -> Optional[dict]:
        """Map-Reduce extraction for longer transcripts.

        Chunks are independent — process them in parallel using ThreadPoolExecutor.
        Ollama queues concurrent requests internally, but starts processing the next
        while the current is generating, giving better throughput than sequential.
        """
        chunks = _chunk_transcript(segments, max_tokens=4000, overlap_tokens=500)
        num_chunks = len(chunks)
        logger.info(
            "Map-Reduce extraction: %d chunks from %d segments",
            num_chunks, len(segments),
        )

        schema_str = get_extraction_schema()
        system_prompt = SYSTEM_PROMPT.replace("{schema}", schema_str)

        def _process_chunk(i: int, chunk: list[dict]) -> Optional[dict]:
            transcript_text = _format_transcript(chunk)
            user_prompt = (
                f"This is chunk {i + 1} of {num_chunks} from a longer call.\n"
                f"Call duration: {metadata.get('duration_seconds', 0)} seconds\n"
                f"Language: {metadata.get('language', 'en')}\n\n"
                f"TRANSCRIPT CHUNK:\n{transcript_text}"
            )
            return self._call_llm(system_prompt, user_prompt)

        # Process chunks in parallel (cap workers to avoid overwhelming Ollama)
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
                    logger.error("Map-reduce chunk %d failed: %s", chunk_idx, e)

        if not partials:
            return None

        # Sort by chunk index to maintain order for merge
        partials.sort(key=lambda x: x[0])
        return _merge_extractions([p[1] for p in partials])

    def extract_intelligence(
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
            Validated CallIntelligence dict, or None on failure
        """
        if not segments:
            logger.warning("No segments provided for intelligence extraction")
            return None

        start_time = time.time()

        metadata = {
            "duration_seconds": int(total_duration),
            "num_speakers": num_speakers,
            "language": language,
        }

        # Estimate tokens to decide single-pass vs map-reduce
        transcript_text = _format_transcript(segments)
        estimated_tokens = _estimate_tokens(transcript_text)

        logger.info(
            "Intelligence extraction: %d segments, ~%d tokens (threshold: %d)",
            len(segments), estimated_tokens, self.max_single_pass_tokens,
        )

        if estimated_tokens <= self.max_single_pass_tokens:
            raw_result = self._extract_single_pass(segments, metadata)
        else:
            raw_result = self._extract_map_reduce(segments, metadata)

        if raw_result is None:
            logger.error("Intelligence extraction returned no results")
            return None

        # Normalize LLM field names to match our Pydantic schema
        raw_result = _normalize_llm_output(raw_result)

        # Validate through Pydantic
        try:
            validated = CallIntelligence.model_validate(raw_result)
            result_dict = validated.model_dump()
        except Exception as e:
            logger.warning(
                "Pydantic validation failed, using raw result: %s", e
            )
            result_dict = raw_result

        # Add metadata
        result_dict.setdefault("metadata", {})
        result_dict["metadata"]["duration_seconds"] = int(total_duration)
        result_dict["metadata"]["language"] = language
        result_dict["metadata"]["extraction_model"] = self.model
        result_dict["metadata"]["extraction_timestamp"] = (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        )

        elapsed = time.time() - start_time
        logger.info("Intelligence extraction completed in %.1fs", elapsed)

        return result_dict


# --- Phase 7B: Post-extraction scoring ---

def compute_methodology_scores(intelligence: dict) -> dict:
    """
    Compute MEDDIC (0-6) and BANT (0-4) scores from extracted fields.
    Pure boolean logic — no LLM call.
    """
    # MEDDIC — defensive against unexpected LLM output shapes
    pain_points = intelligence.get("pain_points", [])
    if not isinstance(pain_points, list):
        pain_points = []
    budget_info = intelligence.get("budget", {})
    if not isinstance(budget_info, dict):
        budget_info = {}
    decision = intelligence.get("decision_making", {})
    if not isinstance(decision, dict):
        decision = {}
    features = intelligence.get("features", {})
    if isinstance(features, list):
        features = {"features_discussed": features, "technical_requirements": [], "feature_requests": [], "use_cases": []}
    elif not isinstance(features, dict):
        features = {}
    objections = intelligence.get("objections", [])
    if not isinstance(objections, list):
        objections = []

    has_metrics = any(pp.get("business_impact") for pp in pain_points if isinstance(pp, dict))
    has_economic_buyer = bool(decision.get("decision_maker"))
    has_decision_criteria = (
        bool(features.get("technical_requirements"))
        or any(o.get("type") == "feature_gap" for o in objections if isinstance(o, dict))
    )
    has_decision_process = bool(decision.get("decision_process"))
    has_pain = len(pain_points) > 0
    has_champion = decision.get("champion_identified", False)

    meddic_total = sum([
        has_metrics, has_economic_buyer, has_decision_criteria,
        has_decision_process, has_pain, has_champion,
    ])

    first_pain = pain_points[0] if pain_points and isinstance(pain_points[0], dict) else {}
    tech_reqs = features.get("technical_requirements", [])
    if not isinstance(tech_reqs, list):
        tech_reqs = []

    meddic_details = {
        "metrics": _get_evidence(pain_points, "business_impact") if has_metrics else None,
        "economic_buyer": decision.get("decision_maker") if has_economic_buyer else None,
        "decision_criteria": (
            ", ".join(str(r) for r in tech_reqs[:3])
            if has_decision_criteria else None
        ),
        "decision_process": decision.get("decision_process") if has_decision_process else None,
        "identify_pain": first_pain.get("description") if has_pain else None,
        "champion": decision.get("champion_name") if has_champion else None,
    }

    # BANT
    has_budget = budget_info.get("discussed", False)
    has_authority = bool(decision.get("decision_maker"))
    has_need = len(pain_points) > 0
    has_timeline = bool(decision.get("decision_timeline"))

    bant_total = sum([has_budget, has_authority, has_need, has_timeline])

    bant_details = {
        "budget": budget_info.get("quote") if has_budget else None,
        "authority": decision.get("decision_maker") if has_authority else None,
        "need": first_pain.get("description") if has_need else None,
        "timeline": decision.get("decision_timeline") if has_timeline else None,
    }

    return {
        "meddic": {
            "total_score": meddic_total,
            "metrics": has_metrics,
            "economic_buyer": has_economic_buyer,
            "decision_criteria": has_decision_criteria,
            "decision_process": has_decision_process,
            "identify_pain": has_pain,
            "champion": has_champion,
            "details": meddic_details,
        },
        "bant": {
            "total_score": bant_total,
            "budget": has_budget,
            "authority": has_authority,
            "need": has_need,
            "timeline": has_timeline,
            "details": bant_details,
        },
    }


def _get_evidence(items: list[dict], field: str) -> Optional[str]:
    """Get first non-None value of field from a list of dicts."""
    for item in items:
        val = item.get(field)
        if val:
            return val
    return None


def validate_deal_stage(intelligence: dict) -> dict:
    """
    Cross-check LLM's deal stage against rule-based classification.
    Lowers confidence on disagreement.
    """
    deal_stage = intelligence.get("deal_stage", {})
    if not isinstance(deal_stage, dict):
        deal_stage = {}
    llm_stage = deal_stage.get("current_stage", "unknown")

    next_steps = intelligence.get("next_steps", [])
    if not isinstance(next_steps, list):
        next_steps = []
    budget_info = intelligence.get("budget", {})
    if not isinstance(budget_info, dict):
        budget_info = {}
    competitors = intelligence.get("competitors", [])
    if not isinstance(competitors, list):
        competitors = []
    pain_points = intelligence.get("pain_points", [])
    if not isinstance(pain_points, list):
        pain_points = []

    has_contract_talk = any(
        "contract" in (ns.get("description", "") or ns.get("action", "") or "").lower()
        for ns in next_steps if isinstance(ns, dict)
    )
    has_pricing = budget_info.get("pricing_presented", False)
    has_competitors = len(competitors) > 0
    has_pain = len(pain_points) > 0

    # Rule-based stage
    if has_contract_talk:
        rule_stage = "closing"
    elif has_pricing and has_competitors:
        rule_stage = "negotiation"
    elif has_competitors:
        rule_stage = "evaluation"
    elif has_pain:
        rule_stage = "qualification"
    else:
        rule_stage = "discovery"

    # If LLM and rules disagree, lower confidence
    if llm_stage != rule_stage and llm_stage != "unknown":
        current_conf = deal_stage.get("confidence", 0.5)
        deal_stage["confidence"] = round(current_conf * 0.7, 2)
        signals = deal_stage.get("stage_signals", [])
        signals.append(
            f"Rule-based check suggests '{rule_stage}' (LLM said '{llm_stage}')"
        )
        deal_stage["stage_signals"] = signals

    intelligence["deal_stage"] = deal_stage
    return intelligence


def compute_deal_health(
    intelligence: dict,
    coaching_metrics: Optional[dict] = None,
) -> dict:
    """
    Compute deal health score (0-100) from all available signals.
    """
    score = 50  # baseline

    # Positive signals — defensive against unexpected types
    budget_info = intelligence.get("budget", {})
    if not isinstance(budget_info, dict):
        budget_info = {}
    decision = intelligence.get("decision_making", {})
    if not isinstance(decision, dict):
        decision = {}
    next_steps = intelligence.get("next_steps", [])
    if not isinstance(next_steps, list):
        next_steps = []
    sentiment = intelligence.get("overall_sentiment", {})
    if not isinstance(sentiment, dict):
        sentiment = {}

    if budget_info.get("discussed"):
        score += 8
    if decision.get("champion_identified"):
        score += 10
    if decision.get("decision_timeline"):
        score += 5
    if len(next_steps) > 0:
        score += 8
    if sentiment.get("customer_net") == "positive":
        score += 8

    # Coaching metrics signals
    if coaching_metrics:
        talk_ratio = coaching_metrics.get("talk_ratio", {})
        if talk_ratio.get("assessment") == "optimal":
            score += 5
        elif talk_ratio.get("assessment") == "rep_dominant":
            score -= 5

    # Negative signals
    objections = intelligence.get("objections", [])
    if not isinstance(objections, list):
        objections = []
    unhandled = sum(1 for o in objections if isinstance(o, dict) and not o.get("handled", False))
    score -= unhandled * 5

    deal_stage_info = intelligence.get("deal_stage", {})
    if not isinstance(deal_stage_info, dict):
        deal_stage_info = {}
    risk_signals = deal_stage_info.get("risk_signals", [])
    if not isinstance(risk_signals, list):
        risk_signals = []
    score -= len(risk_signals) * 5

    if sentiment.get("trajectory") == "declining":
        score -= 8

    # Methodology bonus
    methodology = intelligence.get("methodology_scores", {})
    meddic_total = methodology.get("meddic", {}).get("total_score", 0)
    score += meddic_total * 3  # Up to +18 for perfect MEDDIC

    score = max(0, min(100, score))

    if score >= 70:
        label = "healthy"
    elif score >= 40:
        label = "at_risk"
    else:
        label = "critical"

    return {"score": score, "label": label}


def compute_coaching_scorecard(
    intelligence: dict,
    coaching_metrics: Optional[dict] = None,
) -> dict:
    """
    Compute coaching grade (A/B/C/D) from metrics + extraction quality.

    Components (20% each):
    1. Talk ratio
    2. Question quality
    3. Objection handling
    4. Methodology coverage
    5. Call outcome
    """
    components = {}

    # 1. Talk ratio (20%)
    if coaching_metrics:
        assessment = coaching_metrics.get("talk_ratio", {}).get("assessment", "optimal")
        if assessment == "optimal":
            components["talk_ratio"] = 100.0
        elif assessment in ("rep_dominant", "customer_dominant"):
            # Check how far off — mild deviation = 60, extreme = 30
            rep_pct = coaching_metrics.get("talk_ratio", {}).get("rep_percentage", 50)
            if 30 <= rep_pct <= 65:
                components["talk_ratio"] = 60.0
            else:
                components["talk_ratio"] = 30.0
    else:
        components["talk_ratio"] = 50.0  # no data

    # 2. Question quality (20%)
    if coaching_metrics:
        rep_questions = coaching_metrics.get("question_count", {}).get("rep", 0)
        # Scale: 10+ questions = 100, linear below
        components["question_quality"] = min(100.0, rep_questions * 10.0)
    else:
        components["question_quality"] = 50.0

    # 3. Objection handling (20%)
    objections = intelligence.get("objections", [])
    if not isinstance(objections, list):
        objections = []
    if objections:
        handled = sum(1 for o in objections if isinstance(o, dict) and o.get("handled", False))
        components["objection_handling"] = round((handled / len(objections)) * 100, 1)
    else:
        components["objection_handling"] = 50.0  # no objections = neutral

    # 4. Methodology coverage (20%)
    methodology = intelligence.get("methodology_scores", {})
    if not isinstance(methodology, dict):
        methodology = {}
    meddic_data = methodology.get("meddic", {})
    if not isinstance(meddic_data, dict):
        meddic_data = {}
    meddic_total = meddic_data.get("total_score", 0)
    components["methodology_coverage"] = round((meddic_total / 6) * 100, 1)

    # 5. Call outcome (20%)
    next_steps = intelligence.get("next_steps", [])
    if not isinstance(next_steps, list):
        next_steps = []
    has_followup = any(
        ns.get("type") in ("follow_up_call", "demo", "contract")
        for ns in next_steps if isinstance(ns, dict)
    )
    has_any_steps = len(next_steps) > 0

    if has_followup:
        components["call_outcome"] = 100.0
    elif has_any_steps:
        components["call_outcome"] = 50.0
    else:
        components["call_outcome"] = 0.0

    # Weighted average (equal 20% each)
    weighted = sum(v * 0.2 for v in components.values())

    if weighted >= 85:
        grade = "A"
    elif weighted >= 70:
        grade = "B"
    elif weighted >= 55:
        grade = "C"
    else:
        grade = "D"

    return {
        "grade": grade,
        "score": round(weighted, 1),
        "components": components,
    }


def run_full_intelligence(
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
    num_ctx: int = 8192,
) -> Optional[dict]:
    """
    Run the complete intelligence pipeline:
    1. LLM extraction (7A) — competitors, objections, sentiment, etc.
    2. Methodology scoring (7B) — MEDDIC/BANT
    3. Deal stage validation (7B) — rule-based sanity check
    4. Deal health scoring (7B) — weighted composite
    5. Coaching scorecard (7B) — letter grade

    Args:
        segments: List of segment dicts
        total_duration: Call duration in seconds
        num_speakers: Number of speakers
        language: Primary language
        coaching_metrics: Pre-computed coaching metrics dict (from coaching_metrics.py)
        base_url: Ollama/vLLM base URL
        model: Model name
        temperature: LLM temperature
        timeout: LLM timeout in seconds
        max_single_pass_tokens: Threshold for Map-Reduce
        num_ctx: Context window size for Ollama (default 8192)

    Returns:
        Complete CallIntelligence dict with all fields populated, or None on failure
    """
    # Step 1: LLM extraction
    extractor = IntelligenceExtractor(
        base_url=base_url,
        model=model,
        temperature=temperature,
        timeout=timeout,
        max_single_pass_tokens=max_single_pass_tokens,
        num_ctx=num_ctx,
    )

    intelligence = extractor.extract_intelligence(
        segments=segments,
        total_duration=total_duration,
        num_speakers=num_speakers,
        language=language,
    )

    if intelligence is None:
        return None

    # Step 2: Methodology scoring (7B)
    intelligence["methodology_scores"] = compute_methodology_scores(intelligence)

    # Step 3: Deal stage validation (7B)
    intelligence = validate_deal_stage(intelligence)

    # Step 4: Deal health scoring (7B)
    intelligence["deal_health"] = compute_deal_health(intelligence, coaching_metrics)

    # Step 5: Coaching scorecard (7B)
    intelligence["coaching_scorecard"] = compute_coaching_scorecard(
        intelligence, coaching_metrics,
    )

    # Attach coaching metrics if provided
    if coaching_metrics:
        intelligence["coaching_metrics"] = coaching_metrics

    return intelligence
