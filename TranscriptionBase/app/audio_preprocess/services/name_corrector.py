"""Post-ASR fuzzy name correction against known contacts.

Scans transcript segments for tokens that phonetically match known names
(from participants, CRM contacts, voice profiles). Uses Double Metaphone
for phonetic matching and Jaro-Winkler for similarity verification.

Dependencies: jellyfish, rapidfuzz (both already installed).
"""

import logging
from typing import Optional

import jellyfish
from rapidfuzz.distance import JaroWinkler

logger = logging.getLogger(__name__)

_SKIP_WORDS = frozenset({
    "the", "and", "for", "but", "not", "with", "this", "that", "from",
    "have", "will", "are", "was", "were", "been", "being", "has", "had",
    "does", "did", "can", "could", "would", "should", "may", "might",
    "shall", "must", "need", "about", "into", "over", "after", "before",
    "between", "under", "above", "below", "just", "also", "very", "really",
    "well", "here", "there", "then", "than", "when", "where", "what",
    "which", "who", "whom", "how", "why", "all", "each", "every", "both",
    "few", "more", "most", "other", "some", "any", "many", "much",
    "like", "know", "think", "want", "going", "come", "make", "take",
    "see", "get", "give", "say", "said", "tell", "told", "ask",
})


def build_name_index(names: list[str]) -> dict:
    """Build phonetic lookup index from a list of full names.

    Args:
        names: List of full names, e.g. ["Wanjiku Kamau", "Søren Andersen"]

    Returns:
        dict with:
          - "metaphone": {code: [(token, full_name), ...]}
          - "exact_lower": set of lowercase name tokens
    """
    index = {"metaphone": {}, "exact_lower": set()}
    for full_name in names:
        if not full_name:
            continue
        for token in full_name.split():
            if len(token) < 3:
                continue
            lower = token.lower()
            index["exact_lower"].add(lower)
            try:
                code = jellyfish.metaphone(lower)
                if code:
                    index["metaphone"].setdefault(code, []).append(token)
            except Exception:
                pass
    return index


def correct_names_in_segments(
    segments: list[dict],
    name_index: dict,
    jw_threshold: float = 0.85,
) -> tuple[list[dict], list[dict]]:
    """Scan transcript segments for tokens that fuzzy-match known names.

    Only checks capitalized tokens >= 3 chars that aren't already exact
    matches. Uses Metaphone for candidate lookup, Jaro-Winkler for
    verification.

    Args:
        segments: List of ASR segment dicts (modified in place).
        name_index: Index from build_name_index().
        jw_threshold: Minimum Jaro-Winkler similarity to accept a match.

    Returns:
        (segments, correction_log) where correction_log contains
        {segment, original, corrected, score} for each replacement.
    """
    log = []
    exact_lower = name_index["exact_lower"]
    metaphone_idx = name_index["metaphone"]

    if not metaphone_idx:
        return segments, log

    for seg_i, seg in enumerate(segments):
        text = seg.get("text", "")
        if not text:
            continue
        words = text.split()
        changed = False

        for w_i, word in enumerate(words):
            bare = word.strip(".,!?;:'\"-()")
            if len(bare) < 3:
                continue
            if bare.lower() in _SKIP_WORDS:
                continue
            if bare.lower() in exact_lower:
                continue
            if not bare[0].isupper():
                continue

            try:
                code = jellyfish.metaphone(bare.lower())
            except Exception:
                continue
            if not code:
                continue

            candidates = metaphone_idx.get(code, [])
            for cand in candidates:
                if bare.lower() == cand.lower():
                    continue
                score = JaroWinkler.similarity(bare.lower(), cand.lower())
                if score >= jw_threshold:
                    prefix = word[: word.index(bare[0])] if bare != word else ""
                    suffix_start = word.rindex(bare[-1]) + 1 if bare != word else len(word)
                    suffix = word[suffix_start:]
                    words[w_i] = prefix + cand + suffix
                    log.append({
                        "segment": seg_i,
                        "original": bare,
                        "corrected": cand,
                        "score": round(score, 3),
                    })
                    changed = True
                    break

        if changed:
            seg["text"] = " ".join(words)

    if log:
        logger.info(f"Name corrector: {len(log)} correction(s)")
    return segments, log
