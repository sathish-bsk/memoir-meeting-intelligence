"""
Vocabulary correction engine for Phase 5a + 6a.

Provides:
  - VocabEntry / VocabularyStore — load, save, CRUD for custom vocabulary JSON
  - Hotword generation — top terms by priority for faster-whisper hotwords param
  - Abbreviation normalization — "a r r" → "ARR" (regex, post-ASR)
  - Confidence flagging — flag words with low alignment score
  - Vocabulary matching — exact + sounds_like match (Phase 5a)
  - Phonetic candidate lookup — find_phonetic_candidates() for Phase 5b LLM
  - Translation correction — apply corrections to seg["translation"] too
  - Correction log — track what changed and why

Phonetic matching (Metaphone) is NOT applied automatically in Phase 5a.
Instead, phonetic candidates are collected and sent to the LLM in Phase 5b
for context-aware decision making.

Uses jellyfish for phonetic encoding (Metaphone) and string distance (Jaro-Winkler).
"""
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Tuple

import jellyfish

logger = logging.getLogger("audio_preprocess")

# Common English words that should NEVER be phonetically corrected.
# These match vocabulary terms by Metaphone but are real words:
#   "are" → AR == "ARR" → AR, "more" → MR == "MRR" → MR,
#   "going" → KNK == "Gong" → KNK, etc.
_STOPWORDS = frozenset({
    # Pronouns / determiners
    "i", "me", "my", "we", "us", "our", "you", "your", "he", "him", "his",
    "she", "her", "it", "its", "they", "them", "their", "this", "that",
    "these", "those", "who", "what", "which", "all", "each", "some", "any",
    # Verbs / auxiliaries
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
    # Prepositions / conjunctions / adverbs
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "up", "out",
    "if", "or", "and", "but", "not", "no", "so", "as", "an", "a", "the",
    "than", "too", "very", "just", "about", "also", "then", "more", "here",
    "there", "when", "how", "now", "new", "old", "well", "way", "back",
    "into", "over", "after", "before", "between", "under", "again", "still",
    "even", "only", "like", "most", "other", "much", "many", "long", "great",
    # Common nouns/adjectives that collide with jargon phonetics
    "ban", "band", "bar", "base", "mark", "park", "point", "port", "sign",
    "man", "men", "one", "two", "three", "four", "five", "first", "last",
    "good", "bad", "big", "small", "real", "right", "left", "high", "low",
    "best", "same", "own", "part", "kind", "sure", "both", "end", "why",
    # Anti-collision: common words that phonetically match vocabulary terms
    "says", "saying", "others", "courses", "course", "bands", "bent",
    "medals", "clarity", "phantom", "turns", "team", "teams", "yes",
    "spins", "spin", "notion", "aroma", "fireplace", "motion",
})

# ═══════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════

@dataclass
class VocabEntry:
    term: str                           # "Salesforce", "ARR", "MEDDIC"
    category: str = "other"             # product, competitor, person, jargon, abbreviation, company, other
    sounds_like: List[str] = field(default_factory=list)   # ["sales farce", "sales force"]
    context_words: List[str] = field(default_factory=list) # ["CRM", "platform"]
    boost_priority: int = 1             # 1=normal, 2=high, 3=critical
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "VocabEntry":
        return cls(
            term=d["term"],
            category=d.get("category", "other"),
            sounds_like=d.get("sounds_like", []),
            context_words=d.get("context_words", []),
            boost_priority=d.get("boost_priority", 1),
            enabled=d.get("enabled", True),
        )


# ═══════════════════════════════════════════════════════════════════
# Vocabulary Store
# ═══════════════════════════════════════════════════════════════════

class VocabularyStore:
    """In-memory vocabulary loaded from a JSON file."""

    def __init__(self, name: str = "", entries: Optional[List[VocabEntry]] = None):
        self.name: str = name
        self.version: int = 1
        self.updated_at: str = ""
        self.entries: List[VocabEntry] = entries or []
        # Pre-built lookup caches (rebuilt on any mutation)
        self._phonetic_index: dict = {}   # metaphone → list[VocabEntry]
        self._lower_index: dict = {}      # lowercase term → VocabEntry
        self._sounds_like_index: dict = {}  # lowercase phrase → VocabEntry
        self._rebuild_indexes()

    # ── Load / Save ──

    @classmethod
    def load(cls, path: str) -> "VocabularyStore":
        """Load vocabulary from a JSON file."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Vocabulary file not found: {path}")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        store = cls(name=data.get("name", p.stem))
        store.version = data.get("version", 1)
        store.updated_at = data.get("updated_at", "")
        store.entries = [VocabEntry.from_dict(e) for e in data.get("entries", [])]
        store._rebuild_indexes()
        logger.info(f"Loaded vocabulary '{store.name}': {len(store.entries)} entries")
        return store

    @classmethod
    def load_and_merge(cls, paths: List[str]) -> "VocabularyStore":
        """Load multiple vocabulary files and merge into one store.

        Entries are combined. If duplicate terms exist across files,
        the entry from the later file wins.
        """
        merged = cls(name="merged")
        seen_terms: dict = {}  # lowercase term → index in merged.entries
        names = []

        for path in paths:
            store = cls.load(path)
            names.append(store.name)
            for entry in store.entries:
                key = entry.term.lower()
                if key in seen_terms:
                    # Later file overrides earlier
                    merged.entries[seen_terms[key]] = entry
                else:
                    seen_terms[key] = len(merged.entries)
                    merged.entries.append(entry)

        merged.name = " + ".join(names)
        merged._rebuild_indexes()
        logger.info(f"Merged vocabularies [{merged.name}]: {len(merged.entries)} total entries")
        return merged

    def save(self, path: str) -> None:
        """Atomic save — write to temp file then rename to prevent corruption."""
        self.updated_at = datetime.now(timezone.utc).isoformat()
        data = {
            "name": self.name,
            "version": self.version,
            "updated_at": self.updated_at,
            "entries": [e.to_dict() for e in self.entries],
        }
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: temp file in same directory → rename
        fd, tmp_path = tempfile.mkstemp(dir=str(p.parent), suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, str(p))
        except Exception:
            # Clean up temp file on failure
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        logger.info(f"Saved vocabulary '{self.name}' to {path}: {len(self.entries)} entries")

    def to_dict(self) -> dict:
        """Serialize to dict for JSON API responses."""
        return {
            "name": self.name,
            "version": self.version,
            "updated_at": self.updated_at,
            "entry_count": len(self.entries),
            "enabled_count": sum(1 for e in self.entries if e.enabled),
            "entries": [e.to_dict() for e in self.entries],
        }

    # ── CRUD ──

    def add_entry(self, entry: VocabEntry) -> None:
        """Add a new term. Raises ValueError if term already exists."""
        if entry.term.lower() in self._lower_index:
            raise ValueError(f"Term '{entry.term}' already exists")
        self.entries.append(entry)
        self._rebuild_indexes()

    def update_entry(self, term: str, entry: VocabEntry) -> None:
        """Update an existing term by its original name."""
        lower = term.lower()
        for i, e in enumerate(self.entries):
            if e.term.lower() == lower:
                self.entries[i] = entry
                self._rebuild_indexes()
                return
        raise ValueError(f"Term '{term}' not found")

    def delete_entry(self, term: str) -> None:
        """Delete a term by name."""
        lower = term.lower()
        for i, e in enumerate(self.entries):
            if e.term.lower() == lower:
                self.entries.pop(i)
                self._rebuild_indexes()
                return
        raise ValueError(f"Term '{term}' not found")

    def get_entry(self, term: str) -> Optional[VocabEntry]:
        """Look up a term by name (case-insensitive)."""
        return self._lower_index.get(term.lower())

    # ── Hotword Generation (Phase 6a) ──

    def get_hotwords(self, max_tokens: int = 200) -> str:
        """
        Generate space-separated hotword string for faster-whisper.
        Sorted by boost_priority (highest first), then alphabetically.
        Respects the 224-token limit of faster-whisper's hotwords param.
        """
        enabled = [e for e in self.entries if e.enabled]
        # Sort by priority descending, then alphabetically
        enabled.sort(key=lambda e: (-e.boost_priority, e.term.lower()))

        hotwords = []
        token_count = 0
        for e in enabled:
            # Rough token estimate: 1 word ≈ 1 token, multi-word terms count each word
            words_in_term = e.term.split()
            new_tokens = len(words_in_term)
            if token_count + new_tokens > max_tokens:
                break
            hotwords.append(e.term)
            token_count += new_tokens

        result = " ".join(hotwords)
        logger.info(f"Generated hotwords: {len(hotwords)} terms, ~{token_count} tokens")
        return result

    # ── Abbreviation Detection (Phase 6a) ──

    def get_abbreviations(self) -> set:
        """Return set of all abbreviation terms (for regex normalizer)."""
        return {e.term for e in self.entries if e.enabled and e.category == "abbreviation"}

    def get_all_terms_lower(self) -> dict:
        """Return dict of lowercase term → correct casing for case normalization."""
        return {e.term.lower(): e.term for e in self.entries if e.enabled}

    # ── Phonetic Matching (Phase 5a) ──

    def match_word(self, word: str, context: Optional[List[str]] = None) -> Optional[Tuple[str, float, str]]:
        """
        Try to match a word against vocabulary using exact + sounds_like matching.

        Phonetic matching is handled by Phase 5b (LLM context correction) — not here.

        Returns (corrected_term, score, match_type) or None.
        match_type: "exact_match", "sounds_like_match"
        """
        lower = word.lower().strip()
        if not lower:
            return None

        # 1. Exact match (case-insensitive)
        if lower in self._lower_index:
            entry = self._lower_index[lower]
            if entry.enabled:
                return (entry.term, 1.0, "exact_match")

        # 2. Sounds-like match (check multi-word phrases too)
        if lower in self._sounds_like_index:
            entry = self._sounds_like_index[lower]
            if entry.enabled:
                return (entry.term, 0.95, "sounds_like_match")

        return None

    def find_phonetic_candidates(self, word: str) -> List[VocabEntry]:
        """
        Find vocabulary entries that phonetically match the given word.

        Used by Phase 5b (LLM corrector) to collect candidates for LLM review.
        Skips stopwords and disabled entries.

        Returns list of matching VocabEntry objects.
        """
        lower = word.lower().strip()
        if not lower:
            return []

        # Skip stopwords
        lower_bare = lower.strip(".,!?;:'\"-()[]")
        if lower_bare in _STOPWORDS:
            return []

        try:
            word_metaphone = jellyfish.metaphone(lower)
        except Exception:
            return []

        if not word_metaphone:
            return []

        candidates = []
        seen_terms = set()

        for metaphone_key, entries in self._phonetic_index.items():
            if word_metaphone == metaphone_key:
                for entry in entries:
                    if entry.enabled and entry.term not in seen_terms:
                        # Skip if word IS the term (exact match, not a phonetic candidate)
                        if lower == entry.term.lower():
                            continue
                        jw_score = jellyfish.jaro_winkler_similarity(lower, entry.term.lower())
                        if jw_score >= 0.75:
                            candidates.append(entry)
                            seen_terms.add(entry.term)

        return candidates

    def to_entries_list(self) -> List[dict]:
        """
        Return vocabulary entries as plain list of dicts.

        Clean data handoff for LLMCorrector — no class dependency crossing
        the module boundary. Each dict has: term, category, sounds_like,
        context_words, enabled.
        """
        return [
            {
                "term": e.term,
                "category": e.category,
                "sounds_like": e.sounds_like,
                "context_words": e.context_words,
                "enabled": e.enabled,
            }
            for e in self.entries
            if e.enabled
        ]

    def test_match(self, word: str, context: Optional[List[str]] = None) -> dict:
        """Test matching for the vocabulary management UI."""
        result = self.match_word(word, context)
        if result:
            corrected, score, match_type = result
            return {
                "input": word,
                "matched": True,
                "corrected_to": corrected,
                "score": round(score, 3),
                "match_type": match_type,
            }
        return {"input": word, "matched": False}

    # ── Internal ──

    def _rebuild_indexes(self) -> None:
        """Rebuild all lookup indexes after any mutation."""
        self._phonetic_index = {}
        self._lower_index = {}
        self._sounds_like_index = {}

        for entry in self.entries:
            # Case-insensitive term lookup
            self._lower_index[entry.term.lower()] = entry

            # Phonetic index for the term itself
            try:
                meta = jellyfish.metaphone(entry.term.lower())
                if meta:
                    self._phonetic_index.setdefault(meta, []).append(entry)
            except Exception:
                pass

            # Sounds-like index
            for phrase in entry.sounds_like:
                self._sounds_like_index[phrase.lower()] = entry
                # Also index phonetic of sounds_like phrases
                try:
                    meta = jellyfish.metaphone(phrase.lower())
                    if meta:
                        self._phonetic_index.setdefault(meta, []).append(entry)
                except Exception:
                    pass


# ═══════════════════════════════════════════════════════════════════
# Abbreviation Normalization (Phase 6a)
# ═══════════════════════════════════════════════════════════════════

def normalize_abbreviations(text: str, abbreviations: set, all_terms: dict) -> Tuple[str, List[dict]]:
    """
    Normalize spelled-out abbreviations and fix casing of known terms.

    Patterns handled:
      - "a r r" → "ARR"
      - "A.R.R." → "ARR"
      - "a, r, r" → "ARR"
      - "salesforce" → "Salesforce" (case fix for known terms)

    Returns (corrected_text, list of correction log entries).
    """
    corrections = []

    # 1. Abbreviation normalization
    for abbr in abbreviations:
        if len(abbr) < 2:
            continue
        letters = list(abbr.lower())

        # Pattern: "a r r" or "a  r  r" (letters separated by spaces)
        spaced_pattern = r"\b" + r"\s+".join(re.escape(c) for c in letters) + r"\b"
        text, n = _replace_with_log(text, spaced_pattern, abbr, "abbreviation", corrections)

        # Pattern: "a.r.r." or "A.R.R." (letters separated by dots)
        dotted_pattern = r"\b" + r"\.".join(re.escape(c) for c in letters) + r"\.?" + r"\b"
        text, n = _replace_with_log(text, dotted_pattern, abbr, "abbreviation", corrections)

        # Pattern: "a, r, r" (letters separated by comma+space)
        comma_pattern = r"\b" + r",\s*".join(re.escape(c) for c in letters) + r"\b"
        text, n = _replace_with_log(text, comma_pattern, abbr, "abbreviation", corrections)

    # 2. Case normalization for known terms
    for lower_term, correct_case in all_terms.items():
        if lower_term == correct_case.lower() and lower_term != correct_case:
            # Only fix if there's actually a case difference to fix
            # Match whole words only, case-insensitive
            pattern = r"\b" + re.escape(lower_term) + r"\b"
            matches = list(re.finditer(pattern, text, re.IGNORECASE))
            for m in reversed(matches):
                original = m.group()
                if original != correct_case:
                    text = text[:m.start()] + correct_case + text[m.end():]
                    corrections.append({
                        "original_text": original,
                        "corrected_text": correct_case,
                        "correction_type": "case_fix",
                        "match_score": 1.0,
                    })

    return text, corrections


def _replace_with_log(text: str, pattern: str, replacement: str,
                      correction_type: str, corrections: list) -> Tuple[str, int]:
    """Regex replace with logging of each replacement."""
    count = 0
    matches = list(re.finditer(pattern, text, re.IGNORECASE))
    for m in reversed(matches):
        original = m.group()
        if original != replacement:
            text = text[:m.start()] + replacement + text[m.end():]
            corrections.append({
                "original_text": original,
                "corrected_text": replacement,
                "correction_type": correction_type,
                "match_score": 1.0,
            })
            count += 1
    return text, count


# ═══════════════════════════════════════════════════════════════════
# Vocabulary Correction (Phase 5a)
# ═══════════════════════════════════════════════════════════════════

def _get_segment_context(segments: List[dict], seg_idx: int) -> str:
    """Get context string from current + adjacent segments for LLM candidates."""
    parts = []
    for i in range(max(0, seg_idx - 1), min(len(segments), seg_idx + 2)):
        text = segments[i].get("text", "")
        if text:
            parts.append(text)
    return " ".join(parts)


def apply_vocabulary_corrections(
    segments: List[dict],
    vocab: VocabularyStore,
    confidence_threshold: float = 0.5,
    collect_only: bool = False,
) -> Tuple[List[dict], List[dict], set, List[dict]]:
    """
    Apply vocabulary corrections to transcribed segments.

    Phase 1 (always): Abbreviation normalization on segment text (regex, fast)
    Phase 2 (collect_only=True only): Collect word-level candidates for LLM review

    Word-level replacements (exact + sounds_like) are NEVER applied directly.
    They are only collected as candidates for Phase 5b (LLM context correction)
    when collect_only=True.

    Corrects abbreviations in BOTH seg["text"] and seg["translation"].

    Args:
        segments: list of segment dicts with text, words, translation
        vocab: VocabularyStore with loaded vocabulary entries
        confidence_threshold: alignment score below which words are flagged
        collect_only: if True, collect word-level matches as candidates for LLM

    Returns (corrected_segments, correction_log, corrected_indices, vocab_candidates).
    - correction_log: abbreviation/case corrections applied
    - corrected_indices: set of (seg_idx, word_idx) from abbreviation fixes
    - vocab_candidates: word matches collected for LLM review (empty if collect_only=False)
    """
    correction_log = []
    corrected_indices = set()
    vocab_candidates = []
    abbreviations = vocab.get_abbreviations()
    all_terms = vocab.get_all_terms_lower()

    for seg_idx, seg in enumerate(segments):
        # Phase 1: Abbreviation normalization on segment text
        if seg.get("text"):
            corrected_text, abbr_corrections = normalize_abbreviations(
                seg["text"], abbreviations, all_terms
            )
            if corrected_text != seg["text"]:
                seg["text"] = corrected_text
                for c in abbr_corrections:
                    c["segment_id"] = seg_idx
                    c["word_index"] = -1
                    c["confidence_before"] = seg.get("avg_logprob", 0.0)
                    c["field"] = "text"
                correction_log.extend(abbr_corrections)

        # Phase 1b: Abbreviation normalization on translation text
        if seg.get("translation"):
            corrected_trans, trans_abbr_corrections = normalize_abbreviations(
                seg["translation"], abbreviations, all_terms
            )
            if corrected_trans != seg["translation"]:
                seg["translation"] = corrected_trans
                for c in trans_abbr_corrections:
                    c["segment_id"] = seg_idx
                    c["word_index"] = -1
                    c["confidence_before"] = 0.0
                    c["field"] = "translation"
                correction_log.extend(trans_abbr_corrections)

        # Phase 2: Collect word-level vocabulary candidates (only when LLM will review)
        if not collect_only:
            continue

        context = _get_segment_context(segments, seg_idx)

        # Phase 2a: Word-level matching on aligned words
        words = seg.get("words", [])
        if words:
            skip_until = -1

            for w_idx, w in enumerate(words):
                if w_idx <= skip_until:
                    continue
                word_text = w.get("word", "").strip()
                if not word_text:
                    continue

                score = w.get("score", 1.0)

                # Only flag words below confidence threshold
                if score < confidence_threshold:
                    # Try multi-word phrases first (2-3 word windows)
                    multi_matched = False
                    for window in (3, 2):
                        if w_idx + window <= len(words):
                            phrase_words = [words[w_idx + j].get("word", "").strip() for j in range(window)]
                            phrase = " ".join(phrase_words)
                            result = vocab.match_word(phrase)
                            if result:
                                corrected, match_score, match_type = result
                                if corrected.lower() != phrase.lower():
                                    skip_until = w_idx + window - 1
                                    multi_matched = True
                                    vocab_candidates.append({
                                        "word_id": f"seg{seg_idx}_w{w_idx}",
                                        "word": phrase,
                                        "score": round(score, 4),
                                        "phonetic_candidates": [corrected],
                                        "context": context,
                                        "field": "text",
                                        "segment_index": seg_idx,
                                        "word_index": w_idx,
                                        "match_type": match_type,
                                    })
                                    break

                    # Single word match
                    if not multi_matched:
                        result = vocab.match_word(word_text)
                        if result:
                            corrected, match_score, match_type = result
                            if corrected.lower() != word_text.lower():
                                vocab_candidates.append({
                                    "word_id": f"seg{seg_idx}_w{w_idx}",
                                    "word": word_text,
                                    "score": round(score, 4),
                                    "phonetic_candidates": [corrected],
                                    "context": context,
                                    "field": "text",
                                    "segment_index": seg_idx,
                                    "word_index": w_idx,
                                    "match_type": match_type,
                                })

        elif seg.get("text"):
            # No word-level alignment — match words in segment text
            text_words = seg["text"].split()
            skip_until = -1

            for w_idx, word_text in enumerate(text_words):
                if w_idx <= skip_until:
                    continue

                # Try multi-word phrases first
                multi_matched = False
                for window in (3, 2):
                    if w_idx + window <= len(text_words):
                        phrase = " ".join(text_words[w_idx:w_idx + window])
                        result = vocab.match_word(phrase)
                        if result:
                            corrected, match_score, match_type = result
                            if corrected.lower() != phrase.lower():
                                skip_until = w_idx + window - 1
                                multi_matched = True
                                vocab_candidates.append({
                                    "word_id": f"seg{seg_idx}_w{w_idx}",
                                    "word": phrase,
                                    "score": 0.0,
                                    "phonetic_candidates": [corrected],
                                    "context": context,
                                    "field": "text",
                                    "segment_index": seg_idx,
                                    "word_index": w_idx,
                                    "match_type": match_type,
                                })
                                break

                # Single word match
                if not multi_matched:
                    result = vocab.match_word(word_text)
                    if result:
                        corrected, match_score, match_type = result
                        if corrected.lower() != word_text.lower():
                            vocab_candidates.append({
                                "word_id": f"seg{seg_idx}_w{w_idx}",
                                "word": word_text,
                                "score": 0.0,
                                "phonetic_candidates": [corrected],
                                "context": context,
                                "field": "text",
                                "segment_index": seg_idx,
                                "word_index": w_idx,
                                "match_type": match_type,
                            })

        # Phase 2b: Translation word matching
        if seg.get("translation"):
            trans_words = seg["translation"].split()
            skip_until = -1

            for w_idx, word_text in enumerate(trans_words):
                if w_idx <= skip_until:
                    continue

                # Try multi-word phrases first
                multi_matched = False
                for window in (3, 2):
                    if w_idx + window <= len(trans_words):
                        phrase = " ".join(trans_words[w_idx:w_idx + window])
                        result = vocab.match_word(phrase)
                        if result:
                            corrected, match_score, match_type = result
                            if corrected.lower() != phrase.lower():
                                skip_until = w_idx + window - 1
                                multi_matched = True
                                vocab_candidates.append({
                                    "word_id": f"seg{seg_idx}_t{w_idx}",
                                    "word": phrase,
                                    "score": 0.0,
                                    "phonetic_candidates": [corrected],
                                    "context": context,
                                    "field": "translation",
                                    "segment_index": seg_idx,
                                    "word_index": w_idx,
                                    "match_type": match_type,
                                })
                                break

                if not multi_matched:
                    result = vocab.match_word(word_text)
                    if result:
                        corrected, match_score, match_type = result
                        if corrected.lower() != word_text.lower():
                            vocab_candidates.append({
                                "word_id": f"seg{seg_idx}_t{w_idx}",
                                "word": word_text,
                                "score": 0.0,
                                "phonetic_candidates": [corrected],
                                "context": context,
                                "field": "translation",
                                "segment_index": seg_idx,
                                "word_index": w_idx,
                                "match_type": match_type,
                            })

    abbr_count = len(correction_log)
    candidate_count = len(vocab_candidates)
    logger.info(f"Vocabulary correction: {abbr_count} abbreviation/case fixes applied, {candidate_count} word candidates collected for LLM")
    return segments, correction_log, corrected_indices, vocab_candidates
