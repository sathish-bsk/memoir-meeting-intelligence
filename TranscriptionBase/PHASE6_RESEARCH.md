# Phase 6 Research: Domain Vocabulary & Jargon Handling

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [What Competitors Do](#2-what-competitors-do)
3. [Technical Approaches](#3-technical-approaches)
4. [Abbreviation Handling](#4-abbreviation-handling)
5. [Fuzzy Matching Approaches](#5-fuzzy-matching-approaches)
6. [Vocabulary Management Design](#6-vocabulary-management-design)
7. [Recommended Approach for TranscriptionBase](#7-recommended-approach-for-transcriptionbase)
8. [Implementation Priority](#8-implementation-priority)

---

## 1. Executive Summary

**The Problem in Plain English:** When Whisper transcribes a sales call, it does a great job with common English words. But sales calls are full of uncommon terms -- product names like "Salesforce" or "HubSpot," acronyms like "ARR" (Annual Recurring Revenue) or "MEDDIC" (a sales qualification framework), company-specific jargon, and people's names. Whisper has never heard your company's product name before, so it guesses the closest common word it knows. "BANT" becomes "band," "CSAT" becomes "see sat," and your CEO's unusual name becomes gibberish. This is the domain vocabulary problem, and every commercial transcription service has built features to solve it.

**How the industry solves it:** There are two fundamental strategies. The first is **biasing at recognition time** -- you tell the ASR model "these words are likely to appear" before it starts transcribing, so it favors those words when the audio is ambiguous. Think of it like telling a court reporter "today's witness is named Kowalczyk" before testimony begins -- the reporter is now primed to hear that name. The second strategy is **post-ASR correction** -- after transcription, you scan the output for likely errors and fix them using rules, fuzzy matching against a known vocabulary, or even a large language model (LLM) that understands context. Most production systems use both strategies together: bias during recognition to catch as many terms as possible, then clean up whatever was missed afterward.

**What this means for TranscriptionBase:** Our platform already uses faster-whisper (CTranslate2 engine), which supports both the `hotwords` parameter (prompt-based biasing) and the lower-level `sequence_bias` parameter (logit-level biasing). This gives us a strong foundation. The recommended approach is a three-layer system: (1) prompt-based hotword injection during ASR, (2) regex-based abbreviation normalization immediately after ASR, and (3) fuzzy vocabulary matching with phonetic verification as a final correction pass. Each layer catches what the previous one missed, and all three are lightweight enough to run on CPU without adding meaningful latency.

---

## 2. What Competitors Do

### 2.1 Competitor Comparison Table

| Provider | Feature Name | Mechanism | Limits | Pronunciation Help | Retroactive | Runtime vs Compile-time | Accuracy Claims |
|----------|-------------|-----------|--------|-------------------|-------------|------------------------|-----------------|
| **Deepgram** | Keywords (Nova-2) / Keyterms (Nova-3) | Logit biasing during beam search | 100 keyterms (Nova-3), unlimited keywords (Nova-2) with intensifier values | No explicit pronunciation -- model infers | No | Runtime (per-request) | Up to 90% higher keyword recall rate (KRR); 6x lift on domain vocabulary |
| **AssemblyAI** | Word Boost + Custom Spelling | Decoder biasing + post-ASR find-and-replace | 200 terms (Universal-2), 1000 terms (Universal-3-Pro); max 6 words per phrase | No explicit -- model handles | No | Runtime (per-request) | Not publicly stated; boost_param: low/default/high |
| **Google Cloud STT** | Speech Adaptation (PhraseSet + Class Tokens) | Decoder biasing with boost values; built-in class tokens ($ADDRESSNUM, $MONEY, etc.) + custom classes | No hard limit on phrases; boost range 0-20 | Custom classes group related terms | No | Runtime (per-request, reusable PhraseSet resources) | "Higher likelihood" -- no specific % claimed |
| **Amazon Transcribe** | Custom Vocabulary + Custom Language Models | Vocabulary table (Phrase, SoundsLike, IPA, DisplayAs) + LM trained on up to 2GB text | Up to 50,000 entries per vocabulary; 2GB training text for CLM | SoundsLike and IPA columns (now deprecated -- ignored) | No | Compile-time (vocabulary created, then referenced) | "Significant accuracy improvements" with CLM + vocab together |
| **Rev.ai** | Custom Vocabulary API | Pre-compiled vocabulary submitted before transcription | 6,000 phrases (English), 1,000 (other languages); max 12 words/phrase, 34 chars/word | Pronunciation auto-inferred from spelling | No | Hybrid (pre-compile, then attach per-job) | 95% accuracy on specialized content; 40% fewer errors in medical |
| **Microsoft Azure** | Phrase List + Custom Speech | Just-in-time phrase hints + full custom model training with audio/text data | 500 phrases for phrase lists; custom models need training data | No explicit pronunciation for phrase lists | No | Runtime (phrase lists) / Compile-time (custom models) | Not publicly stated |
| **Speechmatics** | Custom Dictionary + Output Transformation | `additional_vocab` with `sounds_like` hints + regex-based output transformation | 1,000 words/phrases per job; max 6 words per entry | `sounds_like` array with phonetic hints (e.g., "gnocchi": ["nyohki", "nokey"]) | No | Runtime (per-request) | Not publicly stated |
| **Gong.io** | Custom Vocabulary + AI Transcriber | Post-ASR correction with classification + alternative transcriptions | Not publicly stated | Alternative transcriptions (e.g., "air be and be" -> "Airbnb") | **Yes** -- retroactively updates past transcripts | Post-processing (runs after ASR) | 85-90% overall accuracy reported by users |
| **Fireflies.ai** | Custom Vocabulary | Pre-meeting term injection + feedback loop from transcript edits | Not publicly stated | No explicit pronunciation | Learns from user edits over time | Runtime + auto-learning | Not publicly stated |
| **Otter.ai** | Manage Vocabulary (Names + Other) | Vocabulary categories for names vs general terms | Free: 5 terms; Pro: 100+100; Business: 800+800 per team + 200+200 per user | No explicit pronunciation | No | Runtime | Not publicly stated |

### 2.2 Key Observations

**Two dominant patterns emerge:**

1. **Runtime biasing** (Deepgram, AssemblyAI, Google, Azure, Speechmatics): Terms are submitted with each transcription request. The decoder is biased toward those terms during beam search. This is instant -- no training needed. But it only affects new transcriptions.

2. **Post-ASR correction** (Gong, Fireflies): Terms are defined once, and a post-processing step finds and replaces incorrect transcriptions. Gong's approach is particularly noteworthy -- they use "alternative transcriptions" where you tell the system "when you hear something that sounds like 'air be and be', the correct word is 'Airbnb'." This is essentially a sounds-like mapping applied after recognition. The massive advantage is **retroactive correction** -- past transcripts are updated automatically.

**Speechmatics has the most elegant API design:** Their `additional_vocab` with `sounds_like` arrays is clean, intuitive, and powerful. You specify how you want the word written (`content`) and how it might sound (`sounds_like`). This is the design pattern we should study most closely.

**Amazon's approach is the most heavyweight:** Custom Language Models trained on up to 2GB of domain text. Overkill for most use cases, but shows the upper bound of what's possible. Their custom vocabulary table format (Phrase/SoundsLike/IPA/DisplayAs) was ahead of its time, though they've since deprecated the SoundsLike and IPA columns.

**Gong's retroactive correction is a killer feature for sales platforms:** When a sales team adds "Salesforce" to their vocabulary, every past call transcript gets updated. This matters because sales reps often don't set up vocabulary before their first calls -- they discover the need after seeing bad transcriptions.

---

## 3. Technical Approaches

### 3.1 Hotword Boosting via Prompt (What faster-whisper Already Supports)

**How it works (beginner explanation):** Whisper uses an "autoregressive" decoder, meaning it generates one token at a time and uses what it has already generated to predict the next token. Before it starts generating, you can give it a "prompt" -- text that it treats as if it already generated it. This primes the model: if the prompt contains "MEDDIC," the model has already "seen" that token sequence and is more likely to predict it again.

**faster-whisper's `hotwords` parameter:** When you pass `hotwords="ARR BANT MEDDIC Salesforce"` to `model.transcribe()`, faster-whisper tokenizes these words and appends them to the prompt tokens. The decoder then treats them as recent context, making these words more likely to appear in the output.

```python
# Current faster-whisper usage (what we'd add to our transcribe.py)
segments, info = model.transcribe(
    audio_path,
    language="en",
    beam_size=5,
    hotwords="ARR BANT MEDDIC CSAT NPS Salesforce HubSpot",
)
```

**Critical limitations:**
- **224-token limit:** The entire prompt (initial_prompt + hotwords + any context carry-over) is limited to 224 tokens. With an average of ~3 tokens per word, that is roughly 75 words maximum. For a vocabulary of 200+ terms, you cannot fit them all.
- **First segment only:** The `initial_prompt` is only used for the first 30-second segment. Subsequent segments use the previous segment's output as the prompt. The `hotwords` parameter in faster-whisper works around this by appending to every segment's prompt, but this still competes with the 224-token budget.
- **Unreliable steering:** Research from June 2024 ("Do Prompts Really Prompt?") found that Whisper exhibits limited understanding of textual prompts. Regression analysis showed no positive correlation between prompt relevance and recognition performance. Prompting helps with proper nouns and formatting but is not a reliable primary mechanism.
- **No negative biasing:** You cannot tell the model "do NOT produce this word" via prompts.

**Verdict:** Useful as a first layer, but not sufficient alone. Best for a curated shortlist of 20-50 highest-priority terms.

### 3.2 Sequence Bias (Logit-Level Biasing in CTranslate2)

**How it works (beginner explanation):** Every time the Whisper decoder is about to pick the next token, it calculates a probability score (called a "logit") for every possible token. The token with the highest score wins. Sequence biasing lets you add or subtract from these scores for specific token sequences. If you boost the tokens for "MEDDIC," the decoder is more likely to choose those tokens when the audio is ambiguous.

**CTranslate2's `sequence_bias` parameter:** This operates at the token-ID level, not the word level. You need to tokenize your vocabulary words into token IDs first, then pass them as `(token_sequence, multiplier)` tuples.

```python
# Lower-level CTranslate2 sequence_bias (conceptual example)
from faster_whisper import WhisperModel

model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
tokenizer = model.hf_tokenizer  # Access the tokenizer

# Tokenize custom vocabulary
def get_token_ids(word):
    return tokenizer.encode(word, add_special_tokens=False)

# Build sequence bias list
bias_list = [
    (get_token_ids("MEDDIC"), 1.5),   # Boost MEDDIC recognition
    (get_token_ids("ARR"), 1.3),       # Boost ARR
    (get_token_ids("Salesforce"), 1.2), # Boost Salesforce
]

# Note: This is passed to the CTranslate2 generate() method internally.
# As of early 2026, faster-whisper does not directly expose sequence_bias
# in its high-level transcribe() API -- this would require patching.
```

**Advantages over prompt-based hotwords:**
- No 224-token limit -- you can bias hundreds of terms
- Works across all segments, not just the first
- Can do negative biasing (suppress unwanted words)
- More precise control with multiplier values

**Disadvantages:**
- Requires tokenization of vocabulary (token IDs, not words)
- Not directly exposed in faster-whisper's high-level API (may need patching or using CTranslate2 directly)
- Choosing the right multiplier is trial-and-error -- too high causes hallucination (the model outputs "MEDDIC" when nobody said it), too low has no effect
- Multi-token words need sub-sequence biasing (all prefixes must also be biased, or the model may never reach the later tokens)

**Verdict:** The most powerful ASR-time biasing available to us. Worth implementing, but needs careful tuning per vocabulary list.

### 3.3 Contextual Biasing with TCPGen (Research-Grade)

**How it works (beginner explanation):** TCPGen (Tree-Constrained Pointer Generator) is a small neural network that sits alongside the Whisper decoder. It organizes your custom vocabulary into a "prefix tree" (imagine a decision tree where each branch is a letter/token). As the decoder generates tokens, TCPGen checks whether the current sequence matches any path in the tree. If it does, TCPGen boosts the probability of continuing down that path. Unlike simple logit biasing, TCPGen learns which audio features correspond to which vocabulary words.

**Key research results:**
- WhisperBiasing (GitHub: BriansIDP/WhisperBiasing): Achieved 20% relative R-WER reduction on LibriSpeech, 28% on SLURP, and 42% on DSTC2 with a biasing list of 1,000 words.
- October 2024 paper (arXiv:2410.18363): "Contextual Biasing to Improve Domain-specific Custom Vocabulary Audio Transcription without Explicit Fine-Tuning of Whisper Model" -- uses prefix tree + neural pointer generator, does NOT modify Whisper parameters, requires only a small domain-specific training dataset.
- Interspeech 2025: Novel loss terms achieved up to 16.6% relative WER reduction specifically on biased words.

**Why we should NOT implement this now:**
- Requires training a separate TCPGen module on domain-specific audio + text pairs
- Adds model inference overhead (though small)
- The WhisperBiasing implementation requires PyTorch training infrastructure
- Our Phase 6 goal is a configurable vocabulary system, not a trained biasing model
- This is a Phase 8 (Technical Gotchas) item if we need maximum accuracy later

**Verdict:** Best possible accuracy for domain vocabulary, but too complex for Phase 6. File as a future optimization.

### 3.4 Whisper Fine-Tuning with LoRA

**How it works (beginner explanation):** LoRA (Low-Rank Adaptation) is a technique for fine-tuning a large model by only training a tiny fraction of its parameters. Instead of modifying Whisper's 1.5 billion parameters, you add small "adapter" matrices (0.8-1.6% of total parameters) that learn your domain-specific patterns. The base Whisper model stays frozen -- you just swap different LoRA adapters for different domains.

**Recent results (Nov 2025):** Fine-tuning Whisper large-v3 with LoRA for fintech domain showed a 31.64 WER point improvement over the base model on domain-specific terms. The key advantage is that LoRA adapters are small (typically 10-50MB) and can be hot-swapped per organization.

**Relevant research (Interspeech 2025):**
- CBA-Whisper: Curriculum Learning-Based AdaLoRA fine-tuning on Whisper
- Hierarchical LoRA-MoE: Multi-language adaptation with shared + language-specific LoRA experts

**Why this is deferred to Phase 8:**
- Requires domain-specific audio + transcription pairs for training
- Each organization would need its own LoRA adapter
- Training infrastructure (GPU, training pipeline) not yet in place
- Returns are diminishing when combined with simpler approaches (hotwords + post-correction may be sufficient)

**Verdict:** Maximum accuracy per-organization, but requires training data and infrastructure. Phase 8 item.

### 3.5 LLM-Based Post-ASR Error Correction

**How it works (beginner explanation):** After Whisper produces a transcript, you feed it to a Large Language Model (like Qwen2.5 or Llama 3.1) along with a list of expected vocabulary terms. The LLM reads the transcript in context and identifies words that are likely ASR errors. For example, if the transcript says "our band score is very high," the LLM can infer from context that "band" should be "BANT" (a sales qualification framework), because it understands what makes sense in a sales conversation.

**Recent research highlights:**
- **RLLM-CF (2025):** A three-stage framework (error pre-detection, chain-of-thought correction, reasoning verification) achieved 21% relative CER reduction on major benchmarks.
- **Phonetic context integration:** Incorporating ASR N-best hypotheses with phonetic information helps prevent over-correction -- the LLM checks that its correction sounds similar to the original before applying it.
- **Key warning:** Zero-shot/few-shot LLMs (no fine-tuning) often INCREASE error rates due to over-correction. The LLM may "fix" words that were actually correct, or hallucinate domain terms where none were spoken.

**This overlaps with Phase 5 (Post-Processing):** Phase 5's LLM context correction is essentially this technique. Phase 6 adds the domain vocabulary component -- the LLM gets a list of expected terms and is more targeted in its corrections.

**Verdict:** Powerful but dangerous without guardrails. Best combined with phonetic verification (see Section 5). This is a Phase 5+6 bridge item.

### 3.6 Named Entity Recognition (NER) for Proper Nouns

**How it works:** After transcription, run spaCy's NER pipeline to identify entities (companies, products, people). Compare detected entities against a known vocabulary. If spaCy detects "Hub Spot" as an ORG entity but your vocabulary has "HubSpot," you can correct the spacing/casing.

**spaCy's built-in entity types relevant to sales:**
- `ORG` -- Companies, agencies, institutions
- `PERSON` -- People's names
- `PRODUCT` -- Objects, vehicles, foods, etc.
- `MONEY` -- Monetary values
- `GPE` -- Countries, cities, states

**Custom NER training:** spaCy supports training custom entity types. For sales, you could add:
- `COMPETITOR` -- Competitor company names
- `INTERNAL_PRODUCT` -- Your company's product names
- `SALES_TERM` -- Domain jargon (ARR, BANT, etc.)

**EntityRuler (rule-based NER):** For simpler cases, spaCy's EntityRuler lets you define pattern-based entity detection without training:

```python
import spacy
from spacy.pipeline import EntityRuler

nlp = spacy.load("en_core_web_sm")
ruler = nlp.add_pipe("entity_ruler", before="ner")

patterns = [
    {"label": "COMPETITOR", "pattern": "Salesforce"},
    {"label": "COMPETITOR", "pattern": "HubSpot"},
    {"label": "SALES_TERM", "pattern": "MEDDIC"},
    {"label": "SALES_TERM", "pattern": [{"LOWER": "annual"}, {"LOWER": "recurring"}, {"LOWER": "revenue"}]},
]
ruler.add_patterns(patterns)
```

**Verdict:** Useful for entity detection and case normalization, but not a primary correction mechanism. Good as an enrichment layer that tags entities in the transcript for Phase 7 (Intelligence Layer).

### 3.7 Inverse Text Normalization (ITN)

**What it is:** ITN converts spoken-form text to written-form text. For example: "one hundred and twenty three dollars" becomes "$123", "double-u double-u double-u dot example dot com" becomes "www.example.com", and "h t t p s colon slash slash" becomes "https://".

**NVIDIA NeMo Text Processing:** The most mature open-source ITN system. Uses Weighted Finite-State Transducers (WFST) -- think of them as very efficient pattern-matching state machines. Supports English, Russian, German, and more. The `nemo_text_processing` package can be installed standalone (separate from the full NeMo framework).

```python
# NeMo text processing (standalone)
from nemo_text_processing.inverse_text_normalization.inverse_normalize import InverseNormalizer
itn = InverseNormalizer(lang="en")
result = itn.normalize("one hundred and twenty three dollars")
# -> "$123"
```

**Caveat:** Depends on `pynini`, which is a C++ library that can be painful to install. Not a lightweight dependency.

**For our use case:** Whisper already handles most ITN internally (numbers, dates, etc.). The main gap is abbreviation normalization (covered in Section 4), which is better handled by our custom system than by generic ITN.

**Verdict:** Not needed as a separate system. Our abbreviation handler (Section 4) covers the sales-specific ITN needs.

### 3.8 Open-Source Tools Summary

| Tool | What It Does | Relevance to Phase 6 |
|------|-------------|---------------------|
| **FunASR (SeACo-Paraformer)** | Hotword-customizable ASR with contextual biasing | Mandarin-focused, not directly usable with Whisper |
| **Vosk** | Runtime vocabulary modification via custom language models | Different ASR engine, not compatible with our stack |
| **WhisperBiasing** | TCPGen contextual biasing for Whisper | Research-grade, requires training (Phase 8) |
| **NeMo Text Processing** | Inverse text normalization (WFST-based) | Overkill -- Whisper handles most ITN internally |
| **spaCy** | NER for entity detection in transcripts | Useful for entity tagging, not primary correction |
| **rapidfuzz** | Fast fuzzy string matching (C++ backed) | Core library for our fuzzy vocabulary matching |
| **jellyfish** | Phonetic encoding (Soundex, Metaphone, NYSIIS) | Core library for phonetic distance calculation |

---

## 4. Abbreviation Handling

### 4.1 The Problem

When someone says "ARR" in a sales call, Whisper may transcribe it as:
- `"a r r"` -- spelled out as individual letters with spaces
- `"arr"` -- lowercase, like the pirate exclamation
- `"A.R.R."` -- with periods between letters
- `"A R R"` -- uppercase with spaces
- `"air"` -- misheard entirely

Each of these needs different handling. The first four are formatting issues (the model heard the right sounds but formatted them wrong). The last is a recognition error (the model heard the wrong thing entirely).

### 4.2 Detecting Spelled-Out Abbreviations

**The regex approach:** Detect sequences of single letters separated by spaces. This catches "a r r", "c s a t", "m e d d i c", etc.

```python
import re

def detect_spelled_abbreviations(text: str, known_abbreviations: set) -> str:
    """
    Detect sequences of single letters separated by spaces
    and collapse them into abbreviations if they match known terms.

    Example: "our a r r is growing" -> "our ARR is growing"
    """
    # Pattern: 2+ single letters separated by spaces
    # Word boundaries ensure we don't match inside words
    pattern = r'\b([a-zA-Z])\s+([a-zA-Z])(?:\s+([a-zA-Z]))*\b'

    def replace_match(match):
        # Extract all the individual letters from the match
        full_match = match.group(0)
        letters = full_match.replace(' ', '').upper()

        # Only replace if it matches a known abbreviation
        if letters in known_abbreviations:
            return letters
        return full_match  # Leave unchanged if not in our dictionary

    # More robust approach: scan for all possible letter sequences
    result = text
    # Find all runs of spaced single letters
    spaced_pattern = r'(?<!\w)([a-zA-Z](?:\s+[a-zA-Z]){1,9})(?!\w)'

    for match in re.finditer(spaced_pattern, result):
        candidate = match.group(1).replace(' ', '').upper()
        if candidate in known_abbreviations:
            result = result[:match.start()] + candidate + result[match.end():]

    return result

# Usage
KNOWN_ABBREVS = {"ARR", "CSAT", "NPS", "BANT", "MEDDIC", "API", "CRM", "ROI", "KPI", "SLA"}
text = "our a r r is growing and the c s a t score is above ninety"
fixed = detect_spelled_abbreviations(text, KNOWN_ABBREVS)
# -> "our ARR is growing and the CSAT score is above ninety"
```

**Edge cases to handle:**
- **Partial matches:** "a r" might be the start of "are" spoken slowly, not an abbreviation
- **Ambiguous sequences:** "N P S" could be "NPS" (Net Promoter Score) or three separate letters in context
- **Mixed case:** Whisper might output "A r r" or "a R R"
- **Periods:** "A.R.R." should also be normalized to "ARR"
- **Whisper formatting quirks:** Whisper sometimes adds commas between letters: "a, r, r"

### 4.3 Case Normalization for Known Terms

After abbreviation detection, apply case normalization for all known vocabulary terms:

```python
def normalize_case_for_known_terms(text: str, vocabulary: dict) -> str:
    """
    Fix casing for known terms.
    vocabulary: {"salesforce": "Salesforce", "hubspot": "HubSpot", "arr": "ARR"}
    """
    # Build a case-insensitive pattern for each term
    for lower_form, correct_form in vocabulary.items():
        # Use word boundary matching to avoid partial replacements
        pattern = re.compile(r'\b' + re.escape(lower_form) + r'\b', re.IGNORECASE)
        text = pattern.sub(correct_form, text)
    return text
```

### 4.4 Period-Separated Abbreviation Detection

```python
def normalize_period_abbreviations(text: str, known_abbreviations: set) -> str:
    """
    Detect period-separated abbreviations: "A.R.R." -> "ARR"
    """
    # Pattern: letters separated by periods, optionally ending with period
    pattern = r'\b([A-Za-z]\.){2,}[A-Za-z]?\.?\b'

    def replace_match(match):
        candidate = match.group(0).replace('.', '').upper()
        if candidate in known_abbreviations:
            return candidate
        return match.group(0)

    return re.sub(pattern, replace_match, text)
```

### 4.5 Comprehensive Abbreviation Normalizer

Combining all the above into a single pass:

```python
class AbbreviationNormalizer:
    """
    Detects and normalizes abbreviations in ASR output.

    Handles:
    - Spelled-out: "a r r" -> "ARR"
    - Period-separated: "A.R.R." -> "ARR"
    - Comma-separated: "a, r, r" -> "ARR" (Whisper quirk)
    - Lowercase: "arr" -> "ARR" (when in known list)
    - Mixed case: "Arr" -> "ARR"
    """

    def __init__(self, known_abbreviations: set):
        self.known = {abbr.upper() for abbr in known_abbreviations}
        # Pre-build patterns for efficiency
        self._build_patterns()

    def _build_patterns(self):
        """Pre-compile regex patterns for each known abbreviation."""
        self.patterns = []
        for abbr in self.known:
            letters = list(abbr)
            # Spelled out with spaces: "a r r" or "A R R"
            spaced = r'(?<!\w)' + r'\s+'.join(f'[{c.lower()}{c.upper()}]' for c in letters) + r'(?!\w)'
            # Period-separated: "A.R.R." or "a.r.r."
            dotted = r'(?<!\w)' + r'\.'.join(f'[{c.lower()}{c.upper()}]' for c in letters) + r'\.?' + r'(?!\w)'
            # Comma-separated: "a, r, r" (Whisper quirk)
            comma = r'(?<!\w)' + r',\s*'.join(f'[{c.lower()}{c.upper()}]' for c in letters) + r'(?!\w)'

            self.patterns.append((abbr, re.compile(spaced)))
            self.patterns.append((abbr, re.compile(dotted)))
            self.patterns.append((abbr, re.compile(comma)))

    def normalize(self, text: str) -> str:
        """Apply all abbreviation normalizations to the text."""
        for abbr, pattern in self.patterns:
            text = pattern.sub(abbr, text)
        return text
```

### 4.6 Acronyms Spoken as Words vs. Spelled Out

Some acronyms are spoken as words ("NASA," "BANT") while others are spelled out ("ARR," "CRM"). The distinction matters because:
- **Spoken as words:** Whisper usually gets these right as they sound like normal words. The main issue is casing ("nasa" vs "NASA").
- **Spelled out:** Whisper often inserts spaces between letters or mishears individual letters.

The normalizer above handles both cases -- the case normalization catches "nasa" -> "NASA," and the spaced-letter detection catches "a r r" -> "ARR."

---

## 5. Fuzzy Matching Approaches

### 5.1 Why Fuzzy Matching Is Needed

Even with hotword boosting and abbreviation normalization, some words will still be wrong. Whisper might transcribe "medal" instead of "MEDDIC," "hub spot" instead of "HubSpot," or "sales farce" instead of "Salesforce." These errors are phonetically similar to the correct word -- they sound alike but are spelled differently. Fuzzy matching finds these near-misses by comparing the transcribed word against a custom vocabulary using various distance metrics.

### 5.2 Edit Distance (Levenshtein Distance)

**What it is (beginner explanation):** Count the minimum number of single-character edits (insertions, deletions, substitutions) needed to transform one string into another. "medal" -> "meddic" requires 3 edits (add 'd', change 'a' to 'i', change 'l' to 'c'), so the edit distance is 3.

**Normalized edit distance:** Divide by the length of the longer string to get a 0-1 ratio. Lower = more similar. A threshold of 0.3 (70% similar) is a common starting point for ASR correction.

```python
from rapidfuzz import fuzz, distance

# Simple ratio (0-100, higher = more similar)
fuzz.ratio("medal", "meddic")       # ~55
fuzz.ratio("hub spot", "hubspot")   # ~93
fuzz.ratio("salesfarce", "salesforce")  # ~91

# Levenshtein distance (raw count)
distance.Levenshtein.distance("medal", "meddic")  # 3
```

### 5.3 Phonetic Distance

**What it is:** Convert words to a phonetic encoding (a code that represents how the word sounds) and compare the codes. Two words that sound alike will have similar or identical phonetic codes, even if they are spelled differently.

**Algorithms available in Python (via jellyfish):**

| Algorithm | How It Works | Output Example ("Salesforce") | Strengths | Weaknesses |
|-----------|-------------|-------------------------------|-----------|------------|
| **Soundex** | First letter + 3-digit code based on consonant groups | S421 | Fast, simple | Very coarse -- many false positives |
| **Metaphone** | More sophisticated consonant mapping, handles silent letters | SLSFRS | Better than Soundex for English | Still fairly coarse |
| **NYSIIS** | New York State algorithm, handles vowels better | SALSFR | Good for names | Less common, English-only |
| **Match Rating** | 6-char code + comparison rules | SLSFRC | Built-in comparison | Unusual output format |
| **Double Metaphone** | Two codes (primary + alternate) per word, handles multiple pronunciations | (SLSF, SLSF) | Most accurate phonetic encoding | Slightly slower, separate library needed |

```python
import jellyfish

# Soundex
jellyfish.soundex("Salesforce")    # "S421"
jellyfish.soundex("salesfarce")    # "S421"  -- identical! Good match.
jellyfish.soundex("medal")         # "M340"
jellyfish.soundex("MEDDIC")        # "M320"  -- close but not identical

# Metaphone
jellyfish.metaphone("Salesforce")  # "SLSFRS"
jellyfish.metaphone("salesfarce")  # "SLSFRS"  -- identical!
jellyfish.metaphone("medal")       # "MTL"
jellyfish.metaphone("MEDDIC")      # "MTK"  -- different, but first two chars match
```

### 5.4 Combined Scoring: Edit Distance + Phonetic Distance

The most robust approach combines both metrics. A word might be an edit distance match but not a phonetic match (e.g., "read" and "reed" -- different spelling, same sound), or a phonetic match but not an edit distance match (e.g., "knight" and "nite" -- sound alike, very different spelling). Combining both reduces false positives.

```python
from rapidfuzz import fuzz
import jellyfish

def combined_similarity(candidate: str, target: str) -> float:
    """
    Compute combined similarity score (0-1) using edit distance
    and phonetic encoding.

    Higher = more similar.
    """
    # Edit distance similarity (0-1)
    edit_sim = fuzz.ratio(candidate.lower(), target.lower()) / 100.0

    # Phonetic similarity (binary: 0 or 1 for exact phonetic match)
    # Using metaphone for better accuracy than soundex
    cand_phonetic = jellyfish.metaphone(candidate)
    targ_phonetic = jellyfish.metaphone(target)
    phonetic_match = 1.0 if cand_phonetic == targ_phonetic else 0.0

    # If phonetic codes are different, compute edit similarity between them
    if not phonetic_match:
        phonetic_sim = fuzz.ratio(cand_phonetic, targ_phonetic) / 100.0
    else:
        phonetic_sim = 1.0

    # Weighted combination: 60% edit distance, 40% phonetic
    combined = 0.6 * edit_sim + 0.4 * phonetic_sim
    return combined

# Examples
combined_similarity("salesfarce", "Salesforce")  # ~0.95 (high match)
combined_similarity("medal", "MEDDIC")           # ~0.50 (borderline)
combined_similarity("revenue", "MEDDIC")         # ~0.15 (no match)
```

### 5.5 Context-Aware Matching

Pure string/phonetic matching produces false positives. "Band" and "BANT" are phonetically similar, but "band" is a common English word that Whisper might transcribe correctly. Context helps: if the sentence is "we use the band framework for qualifying leads," the word "band" in a sales qualification context almost certainly means "BANT."

**Approach:** Use a sliding window around each word and check for contextual signals:

```python
SALES_CONTEXT_WORDS = {
    "BANT": {"budget", "authority", "need", "timeline", "qualify", "qualification", "framework"},
    "MEDDIC": {"metrics", "economic", "buyer", "decision", "criteria", "champion", "process"},
    "ARR": {"annual", "recurring", "revenue", "growth", "million", "dollar"},
    "CSAT": {"customer", "satisfaction", "score", "survey", "feedback"},
    "NPS": {"net", "promoter", "score", "detractor", "passive"},
}

def context_boosted_match(
    word: str,
    vocabulary: dict,
    surrounding_words: list,
    base_threshold: float = 0.7,
    context_bonus: float = 0.15,
) -> Optional[str]:
    """
    Match a word against vocabulary with context-aware boosting.
    If surrounding words include context clues, lower the matching threshold.

    vocabulary: {"salesforce": "Salesforce", "bant": "BANT", ...}
    surrounding_words: words within a 5-word window
    """
    best_match = None
    best_score = 0.0

    for vocab_lower, vocab_correct in vocabulary.items():
        score = combined_similarity(word, vocab_correct)

        # Check if surrounding context supports this match
        context_words = SALES_CONTEXT_WORDS.get(vocab_correct.upper(), set())
        surrounding_lower = {w.lower() for w in surrounding_words}
        context_overlap = len(context_words & surrounding_lower)

        if context_overlap > 0:
            score += context_bonus * min(context_overlap, 3)  # Cap at 3 context matches

        if score > best_score:
            best_score = score
            best_match = vocab_correct

    if best_score >= base_threshold:
        return best_match
    return None
```

### 5.6 Python Library Comparison

| Library | Speed | Features | Install Size | Best For |
|---------|-------|----------|-------------|----------|
| **rapidfuzz** | 40% faster than alternatives, 2500 pairs/sec | Levenshtein, Jaro-Winkler, partial ratio, token sort/set ratio, process.extract for bulk matching | ~2MB (C++ backed) | Primary fuzzy matching -- fast bulk comparisons |
| **jellyfish** | 1600 pairs/sec | Soundex, Metaphone, NYSIIS, Match Rating Codex, Levenshtein, Damerau-Levenshtein, Jaro-Winkler | ~1MB (Rust backed) | Phonetic encoding -- complementary to rapidfuzz |
| **thefuzz** (fuzzywuzzy) | Slowest (pure Python fallback) | Same API as rapidfuzz but slower | ~50KB | Legacy compatibility only -- use rapidfuzz instead |
| **metaphone** (oubiwann) | Fast | Double Metaphone only | ~50KB | Double Metaphone if jellyfish's single Metaphone is insufficient |
| **phonetics** | Moderate | Metaphone, Double Metaphone, Soundex, NYSIIS, Caverphone | ~100KB | Alternative to jellyfish if needed |

**Recommendation:** Use `rapidfuzz` for edit distance matching and `jellyfish` for phonetic encoding. Together they cover all needs with minimal dependencies and high performance.

---

## 6. Vocabulary Management Design

### 6.1 Data Model

A custom vocabulary entry needs more than just the word itself. Based on competitor analysis, here is a comprehensive schema:

```python
from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum

class VocabCategory(str, Enum):
    PRODUCT = "product"           # Your company's products
    COMPETITOR = "competitor"     # Competitor companies/products
    PERSON = "person"             # People's names (reps, contacts)
    JARGON = "jargon"             # Industry/sales terminology
    ABBREVIATION = "abbreviation" # Acronyms and abbreviations
    COMPANY = "company"           # Company names (yours + others)
    OTHER = "other"               # Catch-all

@dataclass
class VocabEntry:
    # Required fields
    term: str                              # Canonical form: "Salesforce", "ARR", "MEDDIC"
    category: VocabCategory                # Classification for UI grouping

    # Optional fields
    sounds_like: List[str] = field(default_factory=list)
    # Alternative transcriptions: ["sales farce", "sales force", "sail's force"]
    # Following Gong's pattern -- what does Whisper actually produce?

    context_words: List[str] = field(default_factory=list)
    # Words that often appear nearby: ["annual", "recurring", "revenue"]
    # Used for context-aware matching

    abbreviation_of: Optional[str] = None
    # Full form: "Annual Recurring Revenue" for "ARR"

    boost_priority: int = 1
    # 1 = normal, 2 = high priority (always in hotwords), 3 = critical
    # Determines whether this term makes the 224-token hotword cut

    enabled: bool = True
    # Can be toggled without deleting

    # Auto-learning fields
    times_corrected: int = 0
    # How many times a user manually corrected this term
    common_mistranscriptions: List[str] = field(default_factory=list)
    # Auto-collected: what Whisper actually produces for this term
```

### 6.2 Storage Format

For a self-hosted platform, use a simple JSON file per organization (no database needed initially):

```json
{
    "organization": "Acme Corp",
    "version": 2,
    "updated_at": "2026-02-08T10:30:00Z",
    "entries": [
        {
            "term": "ARR",
            "category": "abbreviation",
            "sounds_like": ["a r r", "air", "arr"],
            "context_words": ["annual", "recurring", "revenue", "growth"],
            "abbreviation_of": "Annual Recurring Revenue",
            "boost_priority": 2,
            "enabled": true,
            "times_corrected": 0,
            "common_mistranscriptions": []
        },
        {
            "term": "Salesforce",
            "category": "competitor",
            "sounds_like": ["sales farce", "sales force", "sail's force"],
            "context_words": ["CRM", "platform", "competitor", "migration"],
            "abbreviation_of": null,
            "boost_priority": 2,
            "enabled": true,
            "times_corrected": 3,
            "common_mistranscriptions": ["sales farce"]
        },
        {
            "term": "MEDDIC",
            "category": "jargon",
            "sounds_like": ["medal", "medic", "med dick"],
            "context_words": ["metrics", "economic", "buyer", "decision", "champion"],
            "abbreviation_of": "Metrics, Economic buyer, Decision criteria, Decision process, Identify pain, Champion",
            "boost_priority": 1,
            "enabled": true,
            "times_corrected": 0,
            "common_mistranscriptions": []
        }
    ]
}
```

### 6.3 Import/Export Formats

**CSV (for bulk import):**
```
term,category,sounds_like,context_words,abbreviation_of,boost_priority
ARR,abbreviation,"a r r|air|arr","annual|recurring|revenue",Annual Recurring Revenue,2
Salesforce,competitor,"sales farce|sales force","CRM|platform|competitor",,2
MEDDIC,jargon,"medal|medic","metrics|economic|buyer",,1
```

**JSON:** The native format shown above.

**Starter packs:** Pre-built vocabulary lists for common sales domains:
- **SaaS Sales:** ARR, MRR, CSAT, NPS, BANT, MEDDIC, SPICED, CAC, LTV, churn, upsell, cross-sell
- **Enterprise Sales:** RFP, RFI, POC, pilot, procurement, stakeholder, champion, economic buyer
- **Real Estate:** MLS, escrow, appraisal, amortization, contingency
- **Medical Sales:** HIPAA, EHR, EMR, FDA, clinical trial

### 6.4 UI Design for Vocabulary Management

Based on competitor patterns (especially Gong and Otter.ai), the UI should have:

1. **Vocabulary List View:** Sortable table with columns: Term, Category, Sounds Like, Priority, Enabled (toggle). Filter by category. Search bar.

2. **Add/Edit Term Dialog:**
   - Term field (required)
   - Category dropdown (Product, Competitor, Person, Jargon, Abbreviation, Company, Other)
   - Sounds Like field (comma-separated, with helper text: "How does Whisper usually mis-transcribe this?")
   - Context Words field (comma-separated, with helper text: "Words that often appear near this term")
   - Abbreviation Of field (optional, for acronyms)
   - Priority radio buttons (Normal / High / Critical)

3. **Bulk Import:** Upload CSV or JSON file. Preview before applying. Merge/overwrite options.

4. **Starter Packs:** Checkboxes to include pre-built vocabulary packs (SaaS Sales, Enterprise Sales, etc.)

5. **Auto-Suggested Terms:** After each transcription, scan the output for potential new vocabulary terms (words with low confidence that appear repeatedly across calls). Suggest them to the user for addition.

### 6.5 Auto-Learning from User Corrections

When a user edits a transcript (correcting "sales farce" to "Salesforce"):

1. **Record the correction:** Store (original_text, corrected_text, surrounding_context) in a corrections log.

2. **Update vocabulary:** If "Salesforce" is already in the vocabulary, add "sales farce" to its `sounds_like` list and increment `times_corrected`. If "Salesforce" is NOT in the vocabulary, suggest adding it (with the correction as the first `sounds_like` entry).

3. **Retroactive application (like Gong):** Optionally scan past transcripts for the same error pattern and offer bulk correction.

4. **Correction confidence:** Only auto-add to `sounds_like` after N corrections (e.g., 3) from different calls. This prevents one-off typos from polluting the vocabulary.

---

## 7. Recommended Approach for TranscriptionBase

### 7.1 Three-Layer Architecture

The recommendation is a three-layer system where each layer catches what the previous one missed:

```
Audio Input
    |
    v
[Layer 1: ASR with Hotword Boosting]  <-- During transcription
    |-- faster-whisper `hotwords` parameter
    |-- Top ~50 terms (by boost_priority) formatted as prompt text
    |-- Applied per-segment during beam search
    |
    v
[Layer 2: Abbreviation Normalization]  <-- Immediately after ASR
    |-- Regex-based detection of spelled-out abbreviations
    |-- Period/comma-separated normalization
    |-- Case normalization for known terms
    |-- Zero latency, pure regex
    |
    v
[Layer 3: Fuzzy Vocabulary Matching]   <-- Post-processing pass
    |-- For each word: compute combined similarity against vocabulary
    |-- Phonetic encoding (jellyfish Metaphone) + edit distance (rapidfuzz)
    |-- Context-aware threshold adjustment
    |-- Only replace if score > threshold (default 0.75)
    |
    v
Corrected Transcript
```

### 7.2 Why This Architecture

**Layer 1 (Hotword Boosting)** is the cheapest intervention -- it biases the decoder at recognition time with zero post-processing cost. The 224-token limit means we can only use ~50 terms, so we use the `boost_priority` field to pick the most important ones. This catches the most common domain terms.

**Layer 2 (Abbreviation Normalization)** is essentially free -- regex runs in microseconds. It catches the formatting issues that hotword boosting misses (spaces between letters, wrong casing, periods). This is a deterministic, zero-risk layer.

**Layer 3 (Fuzzy Vocabulary Matching)** is the safety net. It catches recognition errors where Whisper heard the wrong word entirely ("medal" instead of "MEDDIC"). The phonetic + edit distance combined scoring with context-awareness keeps false positives low. This is the only layer that carries risk of incorrect corrections, so the threshold should be conservative (0.75+).

### 7.3 Integration with Existing Pipeline

In the current codebase (`app/audio_preprocess/services/transcribe.py`), the integration points are:

1. **Layer 1:** Modify `_transcribe_chunk_fw()` and related functions to accept and pass `hotwords` parameter to `model.transcribe()`. The vocabulary system generates the hotwords string from the top-priority entries.

2. **Layer 2:** New function `normalize_abbreviations(text, abbreviation_set)` called immediately after `transcribe()` returns, applied to each segment's text.

3. **Layer 3:** New function `apply_vocabulary_correction(text, vocabulary, context_window=5)` called after abbreviation normalization. Also applied per-segment.

4. **Pipeline integration:** In `app/audio_preprocess/services/pipeline.py`, add vocabulary loading and the post-processing calls after the existing transcription + alignment steps.

### 7.4 Configuration Additions

Add to `app/audio_preprocess/config.py`:

```python
# Phase 6: Domain Vocabulary
VOCAB_FILE_PATH: str = os.getenv("VOCAB_FILE_PATH", "")  # Path to vocabulary JSON
VOCAB_HOTWORD_MAX_TOKENS: int = int(os.getenv("VOCAB_HOTWORD_MAX_TOKENS", "200"))
VOCAB_FUZZY_THRESHOLD: float = float(os.getenv("VOCAB_FUZZY_THRESHOLD", "0.75"))
VOCAB_FUZZY_ENABLED: bool = os.getenv("VOCAB_FUZZY_ENABLED", "true").lower() == "true"
VOCAB_ABBREVIATION_ENABLED: bool = os.getenv("VOCAB_ABBREVIATION_ENABLED", "true").lower() == "true"
```

### 7.5 New Dependencies

```
rapidfuzz>=3.0.0     # Fuzzy string matching (C++ backed, fast)
jellyfish>=1.0.0     # Phonetic encoding (Rust backed)
```

Both are lightweight (total ~3MB), have no transitive dependencies on heavy frameworks, and are compatible with our existing Python/torch environment.

---

## 8. Implementation Priority

### Phase 6a: Foundation (Build First)

| # | Task | Effort | Impact | Dependencies |
|---|------|--------|--------|-------------|
| 1 | **Vocabulary data model + JSON storage** | 1 day | Foundation for everything else | None |
| 2 | **Abbreviation normalizer** (regex-based, Section 4.5) | 1 day | High -- catches most common ASR formatting errors | Vocabulary data model |
| 3 | **Hotword injection** (pass vocabulary to faster-whisper `hotwords` param) | 0.5 day | Medium -- helps ASR get terms right the first time | Vocabulary data model |
| 4 | **Case normalizer** for known terms | 0.5 day | Medium -- "salesforce" -> "Salesforce", "meddic" -> "MEDDIC" | Vocabulary data model |
| 5 | **Pipeline integration** (load vocabulary, apply layers in order) | 1 day | Required -- connects everything to the pipeline | Tasks 1-4 |
| 6 | **UI: Vocabulary management page** (list, add, edit, delete, toggle) | 2 days | Required -- users need to manage their vocabulary | Vocabulary data model |

**Total Phase 6a: ~6 days**

### Phase 6b: Fuzzy Matching (Build Second)

| # | Task | Effort | Impact | Dependencies |
|---|------|--------|--------|-------------|
| 7 | **Fuzzy matching engine** (rapidfuzz + jellyfish combined scoring) | 1.5 days | High -- catches recognition errors, not just formatting | rapidfuzz, jellyfish deps |
| 8 | **Context-aware matching** (surrounding word analysis) | 1 day | Medium -- reduces false positives | Fuzzy matching engine |
| 9 | **Vocabulary correction post-processor** (apply fuzzy matches per-segment) | 1 day | Required -- integrates fuzzy matching into pipeline | Tasks 7-8 |
| 10 | **UI: Correction highlighting** (show what was changed + original) | 1 day | Medium -- transparency for users, builds trust | Task 9 |

**Total Phase 6b: ~4.5 days**

### Phase 6c: Auto-Learning & Polish (Build Third)

| # | Task | Effort | Impact | Dependencies |
|---|------|--------|--------|-------------|
| 11 | **Starter vocabulary packs** (SaaS Sales, Enterprise Sales, etc.) | 1 day | Medium -- great onboarding experience | Vocabulary data model |
| 12 | **CSV/JSON import/export** | 0.5 day | Medium -- bulk vocabulary management | Vocabulary data model |
| 13 | **Auto-learning from transcript edits** | 2 days | High -- vocabulary improves over time without manual work | Transcript editing UI |
| 14 | **Retroactive correction** (apply new vocabulary to past transcripts) | 1.5 days | High -- matches Gong's killer feature | Vocabulary + stored transcripts |
| 15 | **Auto-suggested terms** (flag low-confidence repeated words) | 1.5 days | Medium -- proactive vocabulary building | Segment confidence data |

**Total Phase 6c: ~6.5 days**

### Deferred to Phase 8

| Task | Why Deferred |
|------|-------------|
| **CTranslate2 `sequence_bias`** (logit-level biasing) | Requires patching faster-whisper or using CTranslate2 directly; hotwords parameter is sufficient for now |
| **TCPGen contextual biasing** (WhisperBiasing) | Requires training infrastructure and domain-specific audio data |
| **LoRA fine-tuning** per organization | Requires GPU training pipeline and per-org audio datasets |
| **LLM post-correction** (overlaps with Phase 5) | Depends on Phase 5 implementation; risk of over-correction |
| **Custom NER training** for sales entities | spaCy's EntityRuler (rule-based) is sufficient for Phase 6; ML-based NER is Phase 7 territory |

### Summary

**Phase 6a (Foundation)** gives us 80% of the value: hotword boosting catches terms during ASR, abbreviation normalization fixes formatting, and case normalization cleans up the rest. This is the minimum viable vocabulary system.

**Phase 6b (Fuzzy Matching)** catches the remaining recognition errors where Whisper heard the wrong word entirely. This is the "magic" layer that handles "sales farce" -> "Salesforce" type corrections.

**Phase 6c (Auto-Learning)** makes the system self-improving. Each user correction makes future transcriptions better. Retroactive correction updates past transcripts when new vocabulary is added. This is what turns a good feature into a great product.

---

## Sources

### Competitor Documentation
- [Deepgram Keywords Documentation](https://developers.deepgram.com/docs/keywords)
- [Deepgram Keyterm Prompting (Nova-3)](https://developers.deepgram.com/docs/keyterm)
- [AssemblyAI Word Boost Documentation](https://www.assemblyai.com/docs/faq/how-can-i-make-certain-words-more-likely-to-be-transcribed)
- [AssemblyAI Custom Spelling](https://www.assemblyai.com/docs/speech-to-text/pre-recorded-audio/custom-spelling)
- [Google Cloud Speech Adaptation](https://cloud.google.com/speech-to-text/docs/adaptation-model)
- [Google Class Tokens](https://docs.cloud.google.com/speech-to-text/docs/class-tokens)
- [Amazon Transcribe Custom Vocabulary](https://docs.aws.amazon.com/transcribe/latest/dg/custom-vocabulary.html)
- [Amazon Transcribe Custom Language Models](https://docs.aws.amazon.com/transcribe/latest/dg/custom-language-models.html)
- [Rev.ai Custom Vocabulary API](https://docs.rev.ai/api/custom-vocabulary/)
- [Rev.ai Custom Vocabulary Best Practices](https://docs.rev.ai/api/custom-vocabulary/best-practices/)
- [Microsoft Azure Phrase List](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/improve-accuracy-phrase-list)
- [Microsoft Azure Custom Speech](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/custom-speech-overview)
- [Speechmatics Custom Dictionary](https://docs.speechmatics.com/speech-to-text/features/custom-dictionary)
- [Gong Custom Vocabulary](https://help.gong.io/docs/using-custom-vocabulary)
- [Gong AI Transcriber](https://help.gong.io/docs/understanding-ai-transcriber)
- [Gong Custom Vocabulary FAQs](https://help.gong.io/docs/faqs-for-custom-vocabulary)
- [Otter.ai Manage Vocabulary](https://help.otter.ai/hc/en-us/articles/360048571373-Manage-vocabulary)
- [Otter.ai Custom Vocabulary Blog](https://otter.ai/blog/custom-vocabulary)

### Research Papers
- [Contextual Biasing for Whisper without Fine-Tuning (arXiv:2410.18363)](https://arxiv.org/abs/2410.18363)
- [Can Contextual Biasing Remain Effective with Whisper and GPT-2? (Interspeech 2023)](https://arxiv.org/abs/2306.01942)
- [WhisperBiasing GitHub Repository](https://github.com/BriansIDP/WhisperBiasing)
- [CTranslate2 Shallow Contextual Biasing PR](https://github.com/OpenNMT/CTranslate2/pull/1789/files)
- [ASR Error Correction using LLMs (arXiv:2409.09554)](https://arxiv.org/abs/2409.09554)
- [Three-Stage LLM Framework for ASR Error Correction (arXiv:2505.24347)](https://arxiv.org/abs/2505.24347)
- [LLM-based Generative Error Correction for Rare Words (arXiv:2505.17410)](https://arxiv.org/html/2505.17410v1)
- [Do Prompts Really Prompt? Whisper Prompt Understanding (arXiv:2406.05806)](https://arxiv.org/html/2406.05806v1)
- [Fine-tuning Whisper Large-v3 with LoRA (Medium, Nov 2025)](https://medium.com/@sushanttwayana1/fine-tuning-whisper-large-v3-for-domain-specific-speech-recognition-47bfd9c4a0bf)
- [CBA-Whisper: AdaLoRA Fine-Tuning (Interspeech 2025)](https://www.isca-archive.org/interspeech_2025/tan25b_interspeech.pdf)
- [SeACo-Paraformer: Hotword Customization in FunASR](https://www.semanticscholar.org/paper/SeACo-Paraformer:-A-Non-Autoregressive-ASR-System-Shi-Yang/b690c69206031f73dccc81d22015c47154161147)
- [Improving Rare-Word Recognition of Whisper in Zero-Shot Settings (arXiv:2502.11572)](https://arxiv.org/html/2502.11572v1)
- [Lightweight Prompt Biasing for Contextualized ASR (arXiv:2506.06252)](https://arxiv.org/html/2506.06252v2)

### Technical Tools & Libraries
- [faster-whisper (SYSTRAN)](https://github.com/SYSTRAN/faster-whisper)
- [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz)
- [Jellyfish](https://github.com/jamesturk/jellyfish)
- [NVIDIA NeMo Text Processing](https://github.com/NVIDIA/NeMo-text-processing)
- [Whisper Prompting Guide (OpenAI Cookbook)](https://cookbook.openai.com/examples/whisper_prompting_guide)
- [FunASR Toolkit](https://github.com/modelscope/FunASR)
- [Vosk Language Model Adaptation](https://alphacephei.com/vosk/lm)
- [spaCy NER Documentation](https://spacy.io/usage/linguistic-features)
- [Deepgram Keywords vs Search Comparison](https://deepgram.com/learn/keywords-vs-search)
- [Generative Error Correction for Domain-Specific Vocabulary (RevComm)](https://tech.revcomm.co.jp/boosting-dsv-recognition-with-gec)
