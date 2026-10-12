# Phase 5: Transcription Post-Processing -- Research

## 1. Executive Summary

**What is Phase 5?** After Whisper (or any ASR engine) transcribes audio into text, the result is never perfect. Some words will be wrong -- especially names, jargon, quiet speech, and similar-sounding words like "fifteen" vs "thirteen" or "vomit" vs "warm it." Phase 5 is about building a post-processing layer that catches and fixes these errors *after* the transcription is done, before the text goes to the intelligence extraction layer (Phase 7).

**Why does this matter for sales calls?** In a sales call transcription platform, getting product names, competitor names, customer names, and dollar amounts right is critical. If "Salesforce" is transcribed as "sales force" or a customer name "Priya" becomes "priya" or "pre-ya," the downstream intelligence extraction (competitors mentioned, next steps, budget discussed) will produce garbage. Every 1% improvement in transcription accuracy directly improves the quality of business insights.

**The big picture from research:** The industry is converging on a three-pronged approach: (1) flag low-confidence words using ASR confidence scores (logprob), (2) use a Large Language Model (LLM) to correct those flagged words using surrounding context, and (3) verify that corrections are phonetically plausible (the corrected word should *sound like* what was actually spoken). This prevents the LLM from "hallucinating" -- inventing words that were never said. The leading academic framework for this is called Generative Error Correction (GER), with multiple papers from NVIDIA, IBM, Amazon, and Apple published in 2024-2025. However, practical experiments (notably from the Vosk team) warn that naive LLM correction is unstable -- 8B models at 4-bit quantization hallucinate in ~25% of cases, and simple ensemble methods (ROVER) can be equally effective with far more stability. The key lesson: **LLM correction must be constrained and verified, not blindly applied.**

---

## 2. What Competitors Do

### 2.1 Competitor Comparison Table

| Company | Claimed Accuracy | Post-Processing Approach | Custom Vocabulary | Correction Features | Notable Details |
|---------|-----------------|-------------------------|-------------------|--------------------|--------------------|
| **Gong.io** | 85-90% | AI Transcriber agent corrects domain terms; ML identifies topics independent of exact keywords | Yes -- custom vocabulary with alternative transcriptions; retroactive correction of past calls | Automatic replacement of misrecognized terms; pronunciation-aware matching | Adds alternative spellings (e.g., "air be and be" -> "Airbnb"); corrections applied retroactively within hours |
| **Rev.ai** | Not published (WER) | ITN (Inverse Text Normalization), casing, punctuation as post-processing steps; `skip_postprocessing` API param | Yes -- up to 500 phrases; per-phrase pronunciation hints | Post-ASR formatting pipeline; optional skip for speed | Trained on 3M+ hours of noisy data; custom vocabulary limited to 500 phrases, 12 words per phrase |
| **Deepgram** | Nova-2: 8.4% WER (median); Nova-3: lower | Keyword boosting (5-15 point accuracy gains); custom trained models (4-8 weeks); domain fine-tuning (6-12 weeks) | Yes -- keyword boosting (phrase lists); custom language models (up to 2GB training text) | Smart formatting, punctuation, capitalization, filler word tracking | Custom model training can cut WER 10-20%; fine-tuning with labeled audio yields 10-30% relative WER reduction |
| **AssemblyAI** | Universal-2: 21% better alphanumerics, 24% better rare words vs U-1 | Multi-objective tagging model trained on 23M samples for punctuation, casing, ITN | Entity detection (people, orgs, locations, dates, phone numbers, medical, banking) | Auto-highlights (key phrase extraction); sentiment analysis | 15% formatting improvement; 600M param Conformer on 12.5M hours |
| **Fireflies.ai** | 90-95% (optimal conditions) | AI-generated notes, action items, bookmarking | Not documented | Post-meeting workflows; no explicit error correction | Accuracy drops with background noise, accents, overlapping speakers |
| **Otter.ai** | ~85% | Custom vocabulary learning over time; user-editable transcripts | Yes -- learns custom vocabulary, names, jargon, acronyms | Manual editing interface; accuracy improves over time with custom terms | Struggles with non-native English speakers; sentence segmentation issues |
| **Microsoft Teams** | Not published | Custom Dictionary (up to 10,000 terms, released Aug 2025); Copilot post-call summarization | Yes -- admin-uploadable term lists for abbreviations, acronyms, project names, industry jargon | Speaker attribution (Cloud IntelliFrame); Copilot-powered summaries, insights, next steps | Copilot uses Microsoft Graph data for context; non-persisted transcripts for privacy |
| **Google Meet** | ~85% (built-in); 95-96% (with AI tools) | Post-transcription editing interface with timestamp references | Not documented | Manual editing; third-party tools recommended for higher accuracy | English-only; paid Workspace plans only |
| **Zoom AI Companion** | Low WER; strong on rare words | Real-time translated captions (46 languages); federated model approach | Not documented (rare word detection appears built-in) | 28% more accurate English-to-French, 14% more accurate English-to-Spanish translations | AI Companion 3.0 (Dec 2025) with agentic workflows |
| **Amazon Transcribe** | Custom vocab: WER from 9.8% to 6.1% | Custom vocabularies (pronunciation hints) + custom language models (context learning, up to 2GB text) | Yes -- custom vocabulary with pronunciation; custom language models with training + tuning data | Combining custom vocab + custom language model for maximum accuracy | Language models learn word context and relationships; custom vocab alone gave ~4% WER improvement |

### 2.2 Key Insights from Competitor Analysis

**Pattern 1: Custom Vocabulary is Universal.** Every serious competitor offers some form of custom vocabulary -- from Gong's alternative transcription matching to Amazon's pronunciation-hinted vocabulary tables to Microsoft's 10,000-term custom dictionaries. This is the most impactful and lowest-risk post-processing technique.

**Pattern 2: Nobody Publicly Uses LLM Post-Correction on ASR Output.** Despite the academic excitement around Generative Error Correction, none of the major competitors publicly describe using an LLM to fix ASR errors word-by-word. Instead, they invest in better ASR models (trained on more data, with noise robustness) and vocabulary boosting. The LLM is used *downstream* for summarization and insight extraction, not for fixing individual transcription errors.

**Pattern 3: Retroactive Correction is Powerful.** Gong's approach of retroactively correcting historical transcripts when new vocabulary terms are added is particularly clever -- it means the vocabulary system improves *all* past transcripts, not just future ones. This is essentially a find-and-replace on phonetically similar terms.

**Pattern 4: Entity Detection is Separate from Error Correction.** AssemblyAI and others detect entities (names, numbers, dates, addresses) as a separate post-processing step, not as part of error correction. This is useful for Phase 7 (intelligence extraction) but distinct from fixing wrong words.

**Pattern 5: The Accuracy Ceiling Without Custom Vocabulary is ~85-90%.** Across all competitors, raw ASR accuracy clusters around 85-90% for general speech. Custom vocabulary and domain training push this to 90-95%+. The last 5% requires domain-specific models or human review.

---

## 3. Technical Approaches

### 3.1 Confidence-Based Word Flagging

**What it is:** Every word produced by Whisper has a confidence score -- a number indicating how sure the model is about that word. Words with low confidence are likely wrong and should be flagged for correction.

**How it works with Whisper:**

- **Segment-level:** Whisper provides `avg_logprob` per segment (a group of words). The default threshold is -1.0 -- below this, the segment is treated as failed. The value `exp(avg_logprob)` roughly corresponds to confidence (e.g., exp(-0.5) = 0.61 = 61% confident).

- **Word-level (faster-whisper):** When `word_timestamps=True`, each word gets a `probability` value. You can compute `log(probability)` for the logprob. Words with probability < 0.5 (logprob < -0.69) are likely candidates for correction.

- **Word-level (whisper-timestamped):** The library `whisper-timestamped` provides per-word confidence scores directly, derived from the last token of each word.

**Important caveat:** Word-level probabilities from faster-whisper are alignment-based (from Dynamic Time Warping) and may not exactly match the token-level logprobs from the generation process. They are still useful as a relative ranking of confidence.

**Recommended thresholds (from literature):**
- `probability < 0.3` (logprob < -1.2): High-confidence flag -- very likely wrong
- `probability < 0.5` (logprob < -0.69): Medium-confidence flag -- worth checking
- `probability < 0.7` (logprob < -0.36): Low-confidence flag -- probably fine but might benefit from context check

**For our system:** Since we already have word-level timestamps and probabilities from Phase 3 (ctc-forced-aligner), we can use those probability values directly. The aligner provides per-word confidence that correlates with transcription accuracy.

### 3.2 LLM-Based Generative Error Correction (GER)

**What it is:** Using a Large Language Model to read the transcription and fix errors using contextual understanding. Think of it as having a smart proofreader who knows what words *should* be there based on the surrounding conversation.

**Key research papers:**

| Paper | Year | Key Finding |
|-------|------|-------------|
| HyPoradise (NeurIPS 2023, IBM/NVIDIA) | 2023 | First open benchmark for LLM-based ASR error correction. 316K hypothesis-transcription pairs. LLMs can correct tokens missing from N-best lists. |
| "Large Language Models are Efficient Learners of Noise-Robust Speech Recognition" (ICLR 2024, NVIDIA) | 2024 | Up to 53.9% WER improvement with limited training data. Fine-tuned LLMs outperform zero-shot. |
| PROGRES: Prompted Generative Rescoring (2024) | 2024 | Extract N-best from ASR, prompt LLM for diverse hypotheses, rescore. Better than simple re-ranking. |
| Evolutionary Prompt Design for Post-ASR Error Correction (2024) | 2024 | Automated prompt optimization for GER. Better prompts found through evolutionary search. |
| RLLM-CF: Fewer Hallucinations, More Verification (2025) | 2025 | Three-stage framework: (1) error pre-detection, (2) chain-of-thought correction, (3) reasoning verification. GPT-4o achieves 21% CER reduction, 9-11% WER reduction. |
| LLM-based GER for Rare Words with Synthetic Data + Phonetic Context (Interspeech 2025) | 2025 | Introduces LLM-based Simplified Phoneme (LSP) representation. Synthetic error pairs from rare words. Improves rare word correction in English and Japanese. |
| GenSEC Challenge (NVIDIA, Dec 2024) | 2024 | Competition for post-ASR correction, speaker tagging, emotion recognition using LLMs. |
| GEC-RAG (2025) | 2025 | RAG-enhanced GER for low-resource languages (Persian). Retrieves error patterns from knowledge base. |
| Apple: Retrieval-Augmented Correction of Named Entities (2024) | 2024 | Vector database of entity names. Query with ASR hypotheses, retrieve similar entities, feed to LLM. 33-39% relative WER reduction on rare music entities. |
| Confidence-Guided Error Correction for Disordered Speech (2025) | 2025 | Entropy-based confidence scores guide LLM corrections for dysarthric speech. |
| Game-Oriented ASR Error Correction via RAG (2025) | 2025 | Domain-specific knowledge base for gaming terminology. RAG dynamically retrieves relevant entries. |

**The hallucination problem:** This is the single biggest risk with LLM-based correction. When you ask an LLM to "fix" a transcription, it can:
- Add words that were never spoken
- Change correct words to incorrect ones (over-correction)
- Alter the meaning of what was said
- "Fix" grammar that was intentionally informal (spoken language vs written)
- Replace rare proper names with common words (the LLM has never seen the name before)

**Practical findings from the Vosk/Alphacephei team (March 2025):**
- Tested multiple models that fit on an 8GB GPU card, plus Gemini Flash Lite 2.0
- **Most 8B models at 4-bit quantization are not very stable, with hallucinations in ~25% of cases**
- Prompt formatting matters hugely -- changing prompt format reduced WER from 26% to 16%
- Overcorrection of proper names is a persistent problem
- Overcorrection of grammar is common (LLM "improves" spoken language)
- **Simple ROVER (Recognizer Output Voting Error Reduction -- an ensemble method) is equally effective and much more stable**
- Conclusion: GER "doesn't seem like a breakthrough technology" for practical use

**The OpenAI Cookbook approach (official recommendation):**
- Provide a list of correct spellings (product names, company names) as context
- System prompt: "You are a helpful assistant for the company [X]. Your task is to correct misspellings based on the provided list."
- GPT-4 processes the transcript with the vocabulary list
- More scalable than Whisper's prompt parameter (244 token limit)
- Works well for known vocabulary, but cannot fix words not in the list

### 3.3 N-Best Hypothesis Re-Ranking

**What it is:** Instead of taking Whisper's single best guess (1-best), generate multiple hypotheses (N-best) and use an LLM to pick the best one. Like getting multiple answers to a question and choosing the most sensible one.

**How it works:**
1. Run Whisper with `temperature > 0` and `best_of > 1` to get multiple transcription candidates
2. Feed all candidates to an LLM along with conversation context
3. LLM selects the most contextually appropriate candidate
4. Optionally, LLM generates an entirely new hypothesis that combines the best parts of each candidate

**Key finding from HyPoradise:** LLMs can "surpass the upper bound of traditional re-ranking" -- they can generate corrections that are better than any individual hypothesis in the N-best list. This is a genuine advantage over simple voting.

**Practical considerations:**
- Generating N-best hypotheses multiplies ASR compute by N (5-best = 5x slower)
- On CPU, this is very expensive for long audio
- Most useful for short segments with low confidence, not for entire transcripts

### 3.4 RAG-Based Correction (Retrieval-Augmented Generation)

**What it is:** Store a database of known correct terms (company names, product names, people names, industry jargon). When correcting a transcript, retrieve relevant terms and give them to the LLM as context.

**Apple's approach (2024):**
1. Index entity names in a vector database
2. Generate queries from (potentially wrong) ASR text
3. Retrieve phonetically similar entity names
4. Feed ASR hypotheses + retrieved entities to an adapted LLM
5. Achieved 33-39% relative WER reduction on rare music entity names

**Why this matters for sales calls:** Sales calls mention specific companies, people, products, and deals. A RAG database of CRM entities (customer names, deal names, product SKUs) would dramatically improve correction accuracy for these high-value terms.

**GEC-RAG approach (2025):**
- Stores common ASR error patterns alongside corrections
- At correction time, retrieves relevant error patterns
- Feeds patterns + transcript to LLM for targeted correction
- Particularly effective for domain-specific and low-resource languages

### 3.5 Phonetic Similarity Verification

**What it is:** Before accepting an LLM's correction, verify that the corrected word *sounds like* the original (incorrect) word. If someone said "Priya" and Whisper transcribed "pre-ya," a correction to "Priya" is phonetically valid. But if the LLM tries to change "pre-ya" to "pricing" (semantically plausible in a sales context), phonetic verification would reject it because those words don't sound alike.

**How it works:**
1. Convert both the original word and the proposed correction to phonetic codes
2. Compare the codes -- if they are similar, the correction is phonetically plausible
3. If the codes are very different, reject the correction (it's likely a hallucination)

**Algorithms:**
- **Soundex (1918):** Oldest algorithm. Converts words to a code: first letter + 3 digits. "Robert" and "Rupert" both become R163. Simple but crude -- designed for English surnames.
- **Metaphone (1990):** Improved Soundex. Uses pronunciation rules for groups of letters. More accurate than Soundex.
- **Double Metaphone (2000):** Generates two codes per word -- a primary (English pronunciation) and alternate (foreign pronunciation). Handles non-English words better.
- **Beider-Morse Phonetic Matching (BMPM):** Most sophisticated. Language-aware (detects the origin language of a name). Handles Eastern European, Hebrew, Russian names. **Does NOT currently support Indian languages** (Hindi, Tamil, etc.).
- **NYSIIS:** New York State algorithm, better than Soundex for English names.
- **Levenshtein distance:** Not phonetic, but measures edit distance (how many character changes). Useful as a complementary check.

**Critical limitation for Indian names:** All standard phonetic algorithms (Soundex, Metaphone, Double Metaphone, Beider-Morse) were designed for English and European languages. They handle Hindi, Tamil, Telugu names poorly. Research papers have proposed "Indian Soundex" variants, but none are widely available as libraries. For our use case (English + Tamil/Hindi sales calls), phonetic verification will work well for English terms but may need a custom approach for Indian names.

### 3.6 The Three-Stage Framework (RLLM-CF, 2025)

The most promising academic framework, designed specifically to prevent hallucination:

**Stage 1 -- Error Pre-Detection:**
- Identify which words/regions in the transcript are likely wrong
- Uses confidence scores, entropy, or a separate error detection model
- Only flagged regions are sent to the LLM for correction (saves compute, reduces hallucination risk)

**Stage 2 -- Chain-of-Thought Iterative Correction:**
- Break the correction task into subtasks:
  1. **Localization:** Identify the exact error position
  2. **Pronunciation Assessment:** What did the speaker likely say? (phonetic analysis)
  3. **Candidate Generation:** Generate possible corrections
  4. **Candidate Selection:** Choose the best correction based on context + phonetics
- This step-by-step reasoning prevents the LLM from making impulsive (wrong) corrections

**Stage 3 -- Reasoning Process Verification:**
- Verify that the LLM's correction is consistent with its own reasoning
- Check phonetic plausibility
- Reject corrections that don't pass verification
- This is the "safety net" against hallucination

**Results:** GPT-4o with RLLM-CF achieves 21% relative CER reduction and 9-11% relative WER reduction on standard benchmarks (AISHELL-1, AISHELL-2, LibriSpeech).

---

## 4. Self-Hosted LLM Comparison

### 4.1 Model Comparison Table

| Model | Parameters | GPU VRAM (FP16) | GPU VRAM (4-bit) | CPU RAM (4-bit) | GPU Speed (tok/s) | CPU Speed (tok/s) | Quality for Text Correction | Notes |
|-------|-----------|----------------|-----------------|----------------|-------------------|-------------------|---------------------------|-------|
| **Qwen 2.5 7B Instruct** | 7B | ~17 GB | ~4.5 GB | ~4 GB | ~40+ | ~9-16 | Very good; strong multilingual; good instruction following | Best quality-to-size for multilingual; strong on Asian languages |
| **Qwen 2.5 14B Instruct** | 14B | ~28 GB | ~9 GB | ~8 GB | ~25+ | ~5-8 | Excellent; near-frontier quality for size | Needs 24GB GPU; too slow on CPU for production |
| **Llama 3.1 8B Instruct** | 8B | ~16 GB | ~5 GB | ~5 GB | ~40+ | ~8-12 | Good; proven for ASR correction in research papers | 10% relative WER reduction in research (LoRA fine-tuned); larger = better |
| **Llama 3.2 3B Instruct** | 3B | ~6 GB | ~2 GB | ~2 GB | ~50-100+ | ~10-30 | Moderate; good instruction following but limited knowledge | As strong as 8B on instruction following (IFEval); weaker on knowledge tasks |
| **Llama 3.3 70B Instruct** | 70B | ~140 GB | ~35 GB | ~35 GB | ~15+ (multi-GPU) | Impractical | Excellent; best open-source quality | Too large for single GPU; needs multi-GPU setup |
| **Mistral 7B Instruct** | 7B | ~14 GB | ~4 GB | ~4 GB | ~40+ | ~9-15 | Good; strong on coding and translation | Older model; Mistral NeMo 12B is better |
| **Mistral NeMo 12B** | 12B | ~24 GB | ~7 GB | ~7 GB | ~30+ | ~6-10 | Very good; 128K context window; strong reasoning | Drop-in replacement for Mistral 7B; better quality |
| **Phi-4 Mini Instruct** | 3.8B | ~8 GB | ~2.5 GB | ~2.5 GB | ~60+ | ~15-25 | Good; comparable to 7-9B models on reasoning | Excellent speed; 128K context; but newer with less community testing |
| **Gemma 2 9B** | 9B | ~18 GB | ~5 GB | ~5 GB | ~35+ | ~8-12 | Very good; top-ranked for grammatical error correction across languages | Knowledge-distilled; strong multilingual GEC per 2025 study |
| **Gemma 2 2B** | 2B | ~4 GB | ~1.5 GB | ~1.5 GB | ~80+ | ~20-40 | Moderate; ranked 4th overall for GEC despite small size | Best tiny model for text correction; knowledge-distilled |

### 4.2 Practical Recommendations for ASR Correction

**For CPU-only deployment (our current setup):**
- **Best choice: Qwen 2.5 7B (4-bit quantized via Ollama/llama.cpp)**
  - ~4 GB RAM, ~9-16 tokens/second on modern CPU
  - Strong multilingual support (important for Tamil/Hindi names in transcripts)
  - Good instruction following (critical for constrained correction tasks)
- **Budget/speed choice: Llama 3.2 3B or Phi-4 Mini 3.8B (4-bit)**
  - ~2 GB RAM, ~15-30 tokens/second on CPU
  - Faster but lower quality; may hallucinate more
  - Phi-4 Mini has 128K context window (useful for long transcripts)
- **Minimum viable: Gemma 2 2B (4-bit)**
  - ~1.5 GB RAM, ~20-40 tokens/second
  - Surprisingly good for grammatical correction
  - Weakest on domain-specific corrections

**For GPU deployment (future, with 24GB VRAM):**
- **Best choice: Qwen 2.5 14B or Mistral NeMo 12B (4-bit quantized)**
  - Fits in 24GB VRAM; best quality
  - Fast enough for batch processing (~25-30 tok/s)
- **Best quality: Llama 3.1 8B (FP16, full precision)**
  - ~16 GB VRAM; no quantization artifacts
  - Proven in ASR correction research papers

**Critical finding from Vosk experiments:** Most 8B models at 4-bit quantization hallucinate in ~25% of cases. This means:
- 4-bit quantization is risky for correction tasks
- Use 8-bit (GPTQ/AWQ) or FP16 if GPU memory allows
- Always implement phonetic verification as a safety net
- Consider fine-tuning with LoRA on ASR correction pairs (reduces hallucination significantly)

### 4.3 Prompt Engineering for ASR Correction

**Based on the RLLM-CF paper and OpenAI Cookbook, here is an effective prompt structure:**

```
System: You are an ASR error correction assistant. Your task is to fix
transcription errors in speech-to-text output. Rules:
1. ONLY fix words that are clearly wrong based on context
2. DO NOT change grammar or sentence structure (this is spoken language)
3. DO NOT add or remove words
4. Preserve the original meaning exactly
5. If a word might be a proper noun you don't recognize, leave it unchanged
6. Use the provided vocabulary list to correct known terms

User:
Vocabulary: [list of correct terms]
Transcript with confidence scores:
"The customer mentioned sales force [0.45] and they want to
integrate with hub spot [0.32] for their see are em [0.28] needs."

Correct ONLY the low-confidence words if you can identify what they
should be. Return the corrected transcript only, no explanations.
```

**Key insight:** Including confidence scores in the prompt helps the LLM focus on genuinely uncertain words. Without confidence scores, LLMs tend to "improve" perfectly correct words.

---

## 5. Phonetic Libraries Comparison

### 5.1 Python Library Comparison Table

| Library | Algorithms Included | Indian Language Support | Speed | Maintenance | License | Size | Best For |
|---------|-------------------|----------------------|-------|-------------|---------|------|----------|
| **jellyfish** | Soundex, Metaphone, NYSIIS, Match Rating Codex, Levenshtein, Damerau-Levenshtein, Jaro-Winkler | None (English-focused) | Very fast (C extension) | Actively maintained (2025) | BSD-2 | Tiny (~50KB) | Fast phonetic + string distance in one package |
| **abydos** | ~40 phonetic algorithms, ~35 distance metrics, stemmers, fingerprinters. Includes Soundex, Double Metaphone, NYSIIS, Beider-Morse, Alpha-SIS, Caverphone, and many more | Partial (Beider-Morse handles some transliteration; no dedicated Indian algorithms) | Moderate (pure Python) | Less active (last major release ~2020) | GPL-3.0 | Large (~2MB) | Most comprehensive; research/experimentation |
| **pyphonetics** | Soundex, Metaphone, NYSIIS, Caverphone, Lein, phonex | None (English-focused) | Moderate (pure Python) | Minimally maintained | MIT | Small (~30KB) | Simple phonetic coding |
| **phonetics** | Metaphone, Double Metaphone, Soundex, NYSIIS, Caverphone | None (English-focused) | Moderate (pure Python) | Minimally maintained | MIT | Small (~20KB) | Basic phonetic encoding |
| **fuzzy** | Soundex, NYSIIS, Double Metaphone | None (English-focused) | Fast (C extension) | Minimally maintained | GPL-2.0 | Small (~40KB) | Speed-focused phonetic matching |
| **python-Levenshtein** | Levenshtein, Jaro, Jaro-Winkler (edit distance only, not phonetic) | Language-agnostic (character-level) | Very fast (C extension) | Actively maintained | GPL-2.0 | Tiny (~30KB) | Pure distance metrics; complements phonetic libs |

### 5.2 Recommendation for Our Use Case

**Primary: jellyfish** -- The best overall package. Fast C extension, actively maintained, combines phonetic algorithms (Metaphone, Soundex, NYSIIS) with string distance metrics (Levenshtein, Jaro-Winkler) in one library. BSD-2 license is permissive.

**Secondary: abydos** -- Use for Beider-Morse phonetic matching and as a reference for advanced algorithms. GPL-3.0 license is restrictive but acceptable for internal use. Not actively maintained, so pin the version.

**For Indian names:** No existing Python library handles Hindi/Tamil phonetic matching well. Two pragmatic options:
1. **Transliterate to Latin script** (using `unidecode` or `indic-transliteration`) and then apply Double Metaphone. This is imperfect but provides a baseline.
2. **Use edit distance** (Levenshtein/Jaro-Winkler) on both the original script and transliterated versions. Character-level distance is language-agnostic and works across scripts.
3. **Future: Build a custom Indian phonetic matcher** based on the "Indian Soundex" research papers. This would map Devanagari/Tamil characters to phonetic groups (e.g., ka/kha/ga/gha all map to the same group, like Soundex maps B/F/P/V together).

---

## 6. Recommended Approach

### 6.1 Architecture Overview

```
ASR Output (Whisper transcription with word-level timestamps + confidence)
    |
    v
[Step 1: Confidence-Based Flagging]
    |-- Flag words with probability < 0.5 as "uncertain"
    |-- Flag words with probability < 0.3 as "likely wrong"
    |-- Pass through words with probability >= 0.7 unchanged
    |
    v
[Step 2: Custom Vocabulary Matching]
    |-- Compare flagged words against custom vocabulary (company names,
    |   product names, people names, industry terms)
    |-- Use phonetic similarity (Double Metaphone) + edit distance
    |   (Levenshtein) to find close matches
    |-- If match found with high confidence (phonetic match + edit
    |   distance < 3): auto-correct
    |-- If match found with medium confidence: mark as suggestion
    |
    v
[Step 3: LLM Context Correction] (optional, for remaining flagged words)
    |-- Group flagged words with surrounding context (sentence or segment)
    |-- Send to self-hosted LLM with constrained prompt
    |-- Include N-best hypotheses if available
    |-- Include custom vocabulary as context (RAG-style)
    |
    v
[Step 4: Phonetic Verification]
    |-- For each LLM-proposed correction, verify phonetic similarity
    |   to original word
    |-- Reject corrections where phonetic distance is too high
    |-- Accept corrections where phonetic match is strong
    |
    v
[Step 5: Output]
    |-- Corrected transcript with change markers
    |-- Confidence-annotated words (original confidence + correction
    |   confidence)
    |-- Correction log (what was changed, why, original vs corrected)
```

### 6.2 Why This Order Matters

1. **Flagging first** prevents wasting LLM compute on words that are already correct. In a typical transcript, only 5-15% of words are genuinely uncertain.

2. **Custom vocabulary before LLM** handles the highest-value corrections (company/product names) with the simplest, most reliable technique. No hallucination risk. Gong, Amazon, Microsoft, and Deepgram all use this as their primary correction method.

3. **LLM correction only for remaining uncertain words** limits the blast radius of hallucination. The LLM only sees words that failed both the confidence check and vocabulary matching.

4. **Phonetic verification after LLM** is the safety net. It catches cases where the LLM generates a semantically plausible but phonetically impossible correction. If "Priya" was transcribed as "pre-ya" and the LLM suggests "pricing," the phonetic check rejects it because "pre-ya" and "pricing" don't sound alike.

### 6.3 Key Design Decisions

**Decision 1: Correct only flagged words, not the entire transcript.**
- Rationale: The Vosk experiments showed that feeding entire transcripts to LLMs leads to grammar over-correction and proper name destruction. Targeted correction is safer.

**Decision 2: Custom vocabulary is the primary correction mechanism, not LLM.**
- Rationale: Every successful competitor (Gong, Amazon, Deepgram, Microsoft) relies on vocabulary matching. LLM correction is supplementary, not primary.

**Decision 3: Use phonetic verification as a hard gate, not a soft signal.**
- Rationale: If a correction fails the phonetic check, it is rejected entirely. This prevents hallucination from reaching the output. The RLLM-CF paper's "verification stage" confirms this approach.

**Decision 4: Make LLM correction optional (UI toggle).**
- Rationale: LLM correction adds latency (especially on CPU), risk of hallucination, and complexity. Some users may prefer the simpler vocabulary-only correction. Match our existing pattern of UI toggles for optional features.

**Decision 5: Start with Qwen 2.5 7B (4-bit via Ollama) for the LLM.**
- Rationale: Best quality-to-size ratio for multilingual text; strong instruction following; fits in 4GB RAM on CPU. Can be upgraded to 14B on GPU later.

---

## 7. Implementation Priority

### 7.1 Phase 5a: Custom Vocabulary + Confidence Flagging (Build First)

**What:** Build the confidence-based flagging system and custom vocabulary matching engine.

**Components:**
1. **Word confidence extractor:** Read probability values from faster-whisper/openai-whisper word timestamps. Map to confidence levels (high/medium/low).
2. **Custom vocabulary store:** JSON/YAML file per organization with terms, alternatives, and phonetic codes. Admin UI for adding/editing terms.
3. **Phonetic matcher:** Use jellyfish (Double Metaphone + Jaro-Winkler distance) to find vocabulary matches for flagged words.
4. **Auto-correction engine:** Replace flagged words with vocabulary matches when confidence exceeds threshold.
5. **Correction log:** Track what was changed, why, and the confidence scores involved.

**Why first:** Lowest risk, highest impact. Every competitor does this. No model to load, no hallucination risk, fast execution. Amazon showed ~4% absolute WER improvement from custom vocabulary alone. Gong's entire "AI Transcriber" is essentially this.

**Estimated complexity:** Moderate. Mostly string matching and data structure design. The phonetic matching is well-supported by jellyfish.

### 7.2 Phase 5b: LLM Context Correction (Build Second)

**What:** Add optional LLM-based correction for words that remain uncertain after vocabulary matching.

**Components:**
1. **LLM integration:** Ollama client for local Qwen 2.5 7B inference. Fallback to smaller models (Phi-4 Mini, Gemma 2B).
2. **Prompt builder:** Construct correction prompts with flagged words, surrounding context, confidence scores, and relevant vocabulary terms.
3. **Phonetic verifier:** Validate LLM corrections against original words using Double Metaphone + Levenshtein.
4. **Correction reviewer:** Accept/reject corrections based on phonetic verification + confidence threshold.
5. **UI toggle:** "Enable AI-assisted correction" checkbox, disabled by default.

**Why second:** Higher risk (hallucination), higher compute cost, requires Ollama installation. But potentially catches errors that vocabulary matching misses -- especially for unknown proper nouns and context-dependent corrections.

**Estimated complexity:** High. LLM prompt engineering requires iteration. Phonetic verification needs threshold tuning. Hallucination mitigation needs testing.

### 7.3 Phase 5c: Advanced Features (Build Later / Optional)

**What:** N-best hypothesis re-ranking, RAG-enhanced correction, auto-learning vocabulary.

**Components:**
1. **N-best generation:** Run Whisper with temperature > 0, collect multiple hypotheses per segment.
2. **RAG knowledge base:** Index CRM entities (from Phase 7 Zoho integration) in a vector store. Retrieve relevant entities during correction.
3. **Auto-learning:** Track user manual corrections. When the same correction is made 3+ times, automatically add to custom vocabulary (with admin approval).
4. **Retroactive correction:** When new vocabulary terms are added, queue re-correction of historical transcripts (like Gong).

**Why later:** These are optimizations that build on the foundation of 5a and 5b. N-best requires more ASR compute. RAG requires Phase 7 integration. Auto-learning requires a correction tracking system.

### 7.4 Implementation Timeline Estimate

| Sub-Phase | Estimated Effort | Dependencies | Risk Level |
|-----------|-----------------|--------------|------------|
| **5a: Vocabulary + Flagging** | 1-2 weeks | jellyfish library; custom vocabulary file format | Low |
| **5b: LLM Correction** | 2-3 weeks | Ollama installed; Qwen 2.5 7B model; prompt iteration | Medium-High |
| **5c: Advanced Features** | 3-4 weeks | Phase 7 (Zoho CRM); vector store; user correction tracking | Medium |

### 7.5 Files to Create/Modify

| File | Purpose | Phase |
|------|---------|-------|
| `app/audio_preprocess/services/post_processor.py` | Main post-processing orchestrator (NEW) | 5a |
| `app/audio_preprocess/services/vocabulary.py` | Custom vocabulary store + phonetic matcher (NEW) | 5a |
| `app/audio_preprocess/services/llm_corrector.py` | LLM-based correction via Ollama (NEW) | 5b |
| `app/audio_preprocess/services/phonetic_verifier.py` | Phonetic verification gate (NEW) | 5a/5b |
| `app/audio_preprocess/services/pipeline.py` | Add post-processing step after ASR + alignment | 5a |
| `app/audio_preprocess/router.py` | Add vocabulary management endpoints + correction toggle | 5a |
| `app/audio_preprocess/ui.py` | Correction UI: vocabulary editor, correction log display | 5a |
| `app/audio_preprocess/config.py` | Post-processing thresholds, LLM settings, vocabulary path | 5a |
| `vocabularies/` | Directory for per-organization vocabulary JSON files (NEW) | 5a |

---

## 8. Key Risks and Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| LLM hallucination adds words never spoken | High | Phonetic verification gate; reject corrections that fail phonetic check; make LLM correction optional |
| LLM over-corrects spoken grammar | Medium | Explicit prompt instruction "DO NOT change grammar"; only send flagged words, not full transcript |
| LLM destroys proper names | High | Custom vocabulary checked BEFORE LLM; phonetic verification; prompt instruction to preserve unknown proper nouns |
| CPU inference too slow for production | Medium | Use 3B/4B models on CPU; batch corrections across segments; make LLM correction async/optional |
| Phonetic matching fails for Indian names | Medium | Use edit distance (Levenshtein) as fallback; transliterate to Latin first; custom phonetic rules for Indic scripts (future) |
| 4-bit quantized models unstable (~25% hallucination) | High | Use 8-bit quantization or FP16 if GPU available; always verify corrections; consider fine-tuning with LoRA |
| Custom vocabulary grows too large | Low | Limit to 500-1000 terms (like Rev.ai); use phonetic indexing for efficient lookup |

---

## 9. References

### Competitor Documentation
- [Gong AI Transcriber](https://help.gong.io/docs/understanding-ai-transcriber) -- Custom vocabulary with alternative transcriptions and retroactive correction
- [Gong Custom Vocabulary](https://help.gong.io/docs/using-the-custom-vocabulary) -- How Gong handles pronunciation-aware matching
- [Rev.ai Custom Vocabulary API](https://docs.rev.ai/api/custom-vocabulary/) -- Up to 500 phrases with pronunciation hints
- [Deepgram Nova-2](https://deepgram.com/learn/nova-2-speech-to-text-api) -- 8.4% median WER; keyword boosting for 5-15 point gains
- [Deepgram Speech Recognition Accuracy](https://deepgram.com/learn/speech-recognition-accuracy-production-metrics) -- Custom language models cut WER 10-20%
- [AssemblyAI Universal-2](https://www.assemblyai.com/universal-2) -- 21% better alphanumerics, 24% better rare words
- [AssemblyAI Text Formatting Research](https://www.assemblyai.com/research/universal-2) -- Multi-objective tagging model on 23M samples
- [Amazon Transcribe Custom Vocabularies](https://docs.aws.amazon.com/transcribe/latest/dg/custom-vocabulary.html) -- Pronunciation hints; WER from 9.8% to 6.1%
- [Amazon Transcribe Custom Language Models](https://docs.aws.amazon.com/transcribe/latest/dg/custom-language-models.html) -- Up to 2GB training data; context-aware
- [Microsoft Teams Custom Dictionary](https://learn.microsoft.com/en-us/microsoftteams/copilot-custom-dictionary) -- Up to 10,000 terms (Aug 2025)
- [Deepgram Noise Reduction Paradox](https://deepgram.com/learn/the-noise-reduction-paradox-why-it-may-hurt-speech-to-text-accuracy) -- Why preprocessing hurts ASR

### Academic Papers
- [HyPoradise: An Open Baseline for Generative Speech Recognition with LLMs (NeurIPS 2023)](https://arxiv.org/abs/2309.15701) -- 316K hypothesis-transcription pairs; IBM/NVIDIA
- [Large Language Models are Efficient Learners of Noise-Robust Speech Recognition (ICLR 2024)](https://openreview.net/pdf?id=ceATjGPTUD) -- 53.9% WER improvement with limited data
- [ASR Error Correction using Large Language Models (2024)](https://arxiv.org/abs/2409.09554) -- Zero-shot and few-shot LLM correction
- [Evolutionary Prompt Design for LLM-Based Post-ASR Error Correction (2024)](https://arxiv.org/abs/2407.16370) -- Automated prompt optimization
- [Fewer Hallucinations, More Verification: RLLM-CF (2025)](https://arxiv.org/abs/2505.24347) -- Three-stage framework; 21% CER reduction
- [LLM-based GER for Rare Words with Synthetic Data and Phonetic Context (Interspeech 2025)](https://arxiv.org/abs/2505.17410) -- LLM-based Simplified Phoneme (LSP)
- [GenSEC Challenge (NVIDIA, Dec 2024)](https://research.nvidia.com/publication/2024-12_large-language-model-based-generative-error-correction-challenge-and-baselines) -- Post-ASR correction competition
- [Apple: Retrieval-Augmented Correction of Named Entity ASR Errors (2024)](https://arxiv.org/abs/2409.06062) -- 33-39% WER reduction on rare entities
- [GEC-RAG: Improving GER via RAG for ASR (2025)](https://arxiv.org/html/2501.10734) -- RAG-enhanced correction for low-resource languages
- [Confidence-Guided Error Correction for Disordered Speech (2025)](https://arxiv.org/html/2509.25048) -- Entropy-based confidence guiding LLM corrections
- [Adopting Whisper for Confidence Estimation (2025)](https://arxiv.org/html/2502.13446v1) -- Fine-tuning Whisper for word-level confidence
- [Evaluating ASR Confidence Scores for Error Detection (2025)](https://arxiv.org/html/2503.15124v1) -- Confidence scores in user-assisted correction
- [Indian Soundex Algorithm Improvement (2014)](https://www.airccse.org/journal/ijcsea/papers/4314ijcsea03.pdf) -- Adapting Soundex for Hindi/Gujarati
- [Need for Customized Soundex for Indian Names (2017)](https://www.researchgate.net/publication/316891126_Need_for_Customized_Soundex_based_Algorithm_on_Indian_Names_for_Phonetic_Matching) -- Indian-specific phonetic matching

### Practical Resources
- [OpenAI Cookbook: Correcting Whisper Misspellings](https://cookbook.openai.com/examples/whisper_correct_misspelling) -- GPT-4 post-processing technique
- [OpenAI Cookbook: Whisper Processing Guide](https://cookbook.openai.com/examples/whisper_processing_guide) -- Pre and post-processing techniques
- [Vosk Blog: Experiments with GER using LLMs (March 2025)](https://alphacephei.com/nsh/2025/03/15/generative-error-correction.html) -- Practical findings; 25% hallucination rate in 8B models
- [whisper-timestamped](https://github.com/linto-ai/whisper-timestamped) -- Word-level confidence scores for Whisper
- [jellyfish Python library](https://github.com/jamesturk/jellyfish) -- Phonetic matching + string distance
- [abydos Python library](https://github.com/chrislit/abydos) -- 40+ phonetic algorithms including Beider-Morse
- [pyphonetics Python library](https://github.com/Lilykos/pyphonetics) -- Simple phonetic coding
- [Qwen 2.5 Speed Benchmark](https://qwen.readthedocs.io/en/latest/getting_started/speed_benchmark.html) -- Official inference speed data
- [Qwen 2.5 System Requirements](https://www.oneclickitsolution.com/centerofexcellence/aiml/qwen-2-5-minimum-requirements-hardware-software) -- Hardware requirements guide
- [Beider-Morse Phonetic Matching](https://stevemorse.org/phoneticinfo.htm) -- Official BMPM documentation

### LLM Model Cards
- [Qwen 2.5 7B](https://huggingface.co/Qwen/Qwen2.5-7B) -- Multilingual, 128K context
- [Phi-4 Mini Instruct (3.8B)](https://huggingface.co/microsoft/Phi-4-mini-instruct) -- Compact, 128K context, comparable to 7-9B
- [Gemma 2 9B](https://huggingface.co/google/gemma-2-9b) -- Knowledge-distilled, strong on GEC
- [Llama 3.2 3B](https://huggingface.co/blog/llama32) -- Lightweight, strong instruction following
- [Mistral NeMo 12B](https://mistral.ai/news/mistral-nemo) -- 128K context, successor to Mistral 7B

---

## 10. Glossary

| Term | Definition |
|------|-----------|
| **ASR** | Automatic Speech Recognition -- converting audio to text |
| **WER** | Word Error Rate -- percentage of words transcribed incorrectly. Lower is better. 5% WER = 95% accuracy. |
| **CER** | Character Error Rate -- like WER but at character level. More granular for languages without clear word boundaries. |
| **GER** | Generative Error Correction -- using an LLM to generate a corrected version of ASR output |
| **ITN** | Inverse Text Normalization -- converting spoken forms to written forms ("twenty three" to "23", "at" to "@") |
| **Logprob** | Log probability -- the logarithm of how probable the model thinks a word is. More negative = less confident. |
| **N-best** | The N most likely transcription hypotheses. 1-best is the single best guess; 5-best gives five alternatives. |
| **ROVER** | Recognizer Output Voting Error Reduction -- an ensemble method that combines multiple ASR outputs by voting on each word position |
| **RAG** | Retrieval-Augmented Generation -- enriching LLM prompts with retrieved relevant information from a knowledge base |
| **Phonetic code** | A standardized representation of how a word sounds, independent of spelling. "Robert" and "Rupert" have similar codes. |
| **Double Metaphone** | A phonetic algorithm that generates two codes per word (primary and alternate pronunciation) |
| **Beider-Morse** | An advanced phonetic matching algorithm that detects the language origin of a name before coding |
| **Levenshtein distance** | The minimum number of single-character edits (insertions, deletions, substitutions) to transform one word into another |
| **Jaro-Winkler** | A string similarity metric (0 to 1) that gives extra weight to matching prefixes. Good for name matching. |
| **LoRA** | Low-Rank Adaptation -- a technique for fine-tuning LLMs using very few parameters (e.g., 42M instead of 8B) |
| **Quantization** | Reducing model precision (e.g., 16-bit to 4-bit) to save memory, at some quality cost |
| **Hallucination** | When an LLM generates plausible-sounding but factually incorrect content -- in ASR correction, this means "correcting" words to something that was never said |
| **Chain-of-Thought (CoT)** | Prompting an LLM to reason step-by-step before giving an answer, which improves accuracy |
