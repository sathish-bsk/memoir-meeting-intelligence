# Phase 3: "World-Class" ASR — Research Report

> Compiled from 4 parallel research agents covering: Canary-Qwen 2.5B deep dive, WhisperX forced alignment, SOTA ASR landscape, and production edge cases.

---

## Table of Contents

1. [Executive Summary & Recommendations](#1-executive-summary--recommendations)
2. [The ASR Landscape (2025-2026)](#2-the-asr-landscape-2025-2026)
3. [NVIDIA Canary-Qwen 2.5B — Deep Dive](#3-nvidia-canary-qwen-25b--deep-dive)
4. [Forced Alignment (Word-Level Timestamps)](#4-forced-alignment-word-level-timestamps)
5. [Sales Call Edge Cases & Best Practices](#5-sales-call-edge-cases--best-practices)
6. [Compatibility Analysis with Our Stack](#6-compatibility-analysis-with-our-stack)
7. [Recommended Phase 3 Architecture](#7-recommended-phase-3-architecture)
8. [Sources](#8-sources)

---

## 1. Executive Summary & Recommendations

### The Big Picture

Phase 3's original plan was: **Canary-Qwen 2.5B + WhisperX forced alignment**. After deep research, here's what we found:

| Component | Status | Issue |
|-----------|--------|-------|
| **Canary-Qwen 2.5B** | Requires PyTorch 2.6+ (NeMo) | Our stack pins `torch==2.1.1` (resemble-enhance) |
| **WhisperX 3.7.6** | Requires `torch>=2.5.1` | Same torch conflict |
| **ctc-forced-aligner** | Compatible | Light deps, uses MMS model, 1130 languages |
| **faster-whisper** | Already installed | 4x faster than openai-whisper, same accuracy |

### Recommended Phase 3 Plan

**Option A (Minimal — stay on torch 2.1.1):**
1. Switch from `openai-whisper` to `faster-whisper` for ASR (already installed, 4x speedup)
2. Upgrade model from `medium` to `large-v3-turbo` (809M params, better accuracy, still feasible on CPU)
3. Add `ctc-forced-aligner` for word-level timestamps (works with torch 2.1.1, supports Tamil+Hindi)
4. Add post-processing: ITN (number formatting), hotword boosting, hallucination filtering

**Option B (Microservice — separate PyTorch environment):**
1. Run Canary-Qwen 2.5B in a Docker container with PyTorch 2.6+ / NeMo
2. Your FastAPI app sends audio to the Canary service via HTTP
3. Best English accuracy (5.63% WER) but GPU required, English only
4. Keep faster-whisper as fallback for CPU / multilingual

**Option C (Deferred — wait for GPU):**
1. Do Option A now (immediate improvement, no GPU needed)
2. When GPU hardware is available (Phase 6), add Canary-Qwen as primary English ASR
3. Keeps the upgrade path clean

### Quick Comparison

| Approach | English WER | Tamil/Hindi | GPU Needed | Complexity | Immediate? |
|----------|-------------|-------------|------------|------------|------------|
| Current (Whisper medium, openai-whisper) | ~7.9% | ~35-40% | No | Low | Yes |
| **Option A** (faster-whisper large-v3-turbo) | ~6.8% | ~28-35% | No | Low | **Yes** |
| **Option B** (Canary-Qwen microservice) | **5.63%** | Not supported | **Yes** | High | No |
| Option C (A now, B later) | ~6.8% now → 5.63% later | ~28-35% | Later | Medium | Yes |

---

## 2. The ASR Landscape (2025-2026)

### Open-Source Models — Full Comparison

| Model | Org | Params | WER (OpenASR) | RTFx (GPU) | Languages | License | CPU Feasible? |
|-------|-----|--------|---------------|------------|-----------|---------|---------------|
| **Canary-Qwen 2.5B** | NVIDIA | 2.5B | **5.63%** | 418 | English only | CC-BY-4.0 | No (too slow) |
| IBM Granite Speech 8B | IBM | 8B | 5.85% | — | 5 languages | Apache 2.0 | No |
| Phi-4 Multimodal | Microsoft | 5.6B | 6.14% | — | Multilingual | MIT | No |
| Parakeet TDT 0.6B v3 | NVIDIA | 600M | 6.32% | **3,332** | 25 European | CC-BY-4.0 | No |
| Whisper Large-v3 | OpenAI | 1.55B | 6.43% | 68.56 | **99 languages** | MIT | Yes (via faster-whisper) |
| Parakeet CTC 1.1B | NVIDIA | 1.1B | 6.68% | 2,793 | English only | CC-BY-4.0 | No |
| **Whisper Large-v3-turbo** | OpenAI | 809M | ~7.0% | 216 | **99 languages** | MIT | **Yes** |
| Whisper Medium | OpenAI | 769M | ~7.9% | ~100 | 99 languages | MIT | Yes (current) |

### Key Insight: LLM-Decoder Models Are Overtaking Whisper

The trend is clear: hybrid ASR+LLM models (Canary-Qwen, Granite, Phi-4) achieve lower WER because the language model component provides:
- Better contextual understanding (proper nouns, numbers)
- Native punctuation and capitalization
- Dual-mode capability (transcription + summarization in one model)

However, **all of them require GPU**. For CPU inference, Whisper (via faster-whisper) remains the only viable option.

### Commercial Services (for reference)

| Provider | Model | WER (Real-world) | Telephony WER | Price per 1000 min | Diarization |
|----------|-------|------------------|---------------|---------------------|-------------|
| ElevenLabs | Scribe v2 | Best on benchmarks | — | API pricing | Yes |
| Deepgram | Nova-3 | 5.3-6.8% | **5.3-6.8%** | $4.30 | Yes |
| AssemblyAI | Universal-2 | 6.88% | ~8% | $4.50 | Yes |
| OpenAI | Whisper API | ~7.9% | ~17.7% | $6.00 | No |
| Google | Chirp 3 | 11.6% batch | — | $16.00 | Yes |

**Deepgram Nova-3 crushes Whisper on telephony audio** (5.3% vs 17.7%). This is because Deepgram trains specifically on call center data. Open-source models trained on podcasts/audiobooks degrade significantly on telephone-quality audio.

### Telephony/Sales Call Specific Performance

This is the most relevant comparison for your use case:

| Model | Clean Audio WER | Telephony WER | Degradation |
|-------|----------------|---------------|-------------|
| IBM Granite 8B | 1.5% | ~1.6% | **7.5% (best)** |
| Canary-Qwen 2.5B | 1.6% | Not benchmarked | Likely good (trained on Switchboard) |
| Deepgram Nova-3 | ~5% | 5.3-6.8% | ~30% |
| AssemblyAI Universal-2 | ~5% | 6.88% | ~38% |
| Whisper Large-v3 | 2.7% | **~17.7%** | **556% (worst)** |

**Critical finding**: Whisper degrades dramatically on telephony audio. Your Phase 1 preprocessing pipeline (denoising, loudnorm, bandwidth extension) partially mitigates this, but Whisper was trained primarily on podcasts and audiobooks, not phone calls.

### Indian Language Support

| Model | Tamil WER | Hindi WER | Code-Switching | Notes |
|-------|-----------|-----------|----------------|-------|
| Whisper Large-v3 | ~28-35% (actual) | ~37% (actual) | Per-chunk only | OpenAI's text normalization hides true WER |
| AI4Bharat IndicConformer | ~15-20% | ~15-20% | **Native** | Best open-source for Indian langs |
| Dolphin | Supported | Supported | **Native** | 40 Eastern languages, code-switching |
| Canary-Qwen 2.5B | **Not supported** | **Not supported** | N/A | English only |

**Important warning**: OpenAI's reported WER for Tamil (~16.9%) and Hindi (~16.9%) is misleading. Their text normalization for Brahmic scripts breaks words into consonant sequences before WER measurement, artificially deflating error rates. Real-world Tamil WER is ~35-40% with Whisper.

---

## 3. NVIDIA Canary-Qwen 2.5B — Deep Dive

### Architecture

| Component | Details |
|-----------|---------|
| **Encoder** | FastConformer (from `nvidia/canary-1b-flash`) — 80ms frame rate (12.5 audio tokens/second) |
| **Decoder** | Qwen3-1.7B LLM (frozen pretrained weights) |
| **Bridge** | Linear projection layer mapping audio embeddings to LLM embedding space |
| **Adaptation** | LoRA (r=128, alpha=256) on q_proj, v_proj of LLM decoder |
| **Total parameters** | 2.5 billion |
| **Training data** | 234,500 hours of English speech |
| **License** | CC-BY-4.0 (commercial use OK) |

### Dual Operating Modes

1. **ASR Mode**: Prompt = `"Transcribe the following: <audio>"` — pure transcription with native punctuation/capitalization
2. **LLM Mode**: Disables LoRA adapters, uses base Qwen3 for summarization, QA, action item extraction on transcripts

This dual-mode is uniquely valuable for Phase 5 (Intelligence Layer) — the same model could transcribe AND extract structured intelligence.

### Per-Dataset WER Benchmarks

| Dataset | WER (%) | Relevance to Sales Calls |
|---------|---------|--------------------------|
| LibriSpeech Clean | 1.60 | Low (audiobooks) |
| LibriSpeech Other | 3.10 | Low (noisy audiobooks) |
| SPGISpeech | 1.90 | **High** (financial earnings calls) |
| TedLium | 2.72 | Medium (presentations) |
| VoxPopuli | 5.66 | Low (parliament speeches) |
| GigaSpeech | 9.41 | Medium (podcasts, YouTube) |
| AMI | 10.18 | **High** (meeting recordings) |
| Earnings22 | 10.42 | **High** (corporate earnings calls) |

### Noise Robustness

| SNR Level | WER (%) |
|-----------|---------|
| Clean | 1.60 |
| SNR 10 dB | 2.41 |
| SNR 5 dB | 4.08 |
| SNR 0 dB | 9.83 |
| SNR -5 dB | 30.60 |

### Hardware Requirements

| Metric | Value |
|--------|-------|
| GPU VRAM | ~8 GB (bfloat16) |
| Tested GPUs | A6000, A100, RTX 5090 |
| Max audio chunk | **40 seconds** (vs Whisper's 30s) |
| Streaming | **Not supported** (batch only) |
| CPU inference | Impractically slow (~50-100x slower than GPU) |

### Why It Won't Work With Our Current Stack

| Dependency | Our Stack | Canary-Qwen Requires |
|------------|-----------|---------------------|
| **PyTorch** | 2.1.1 (pinned by resemble-enhance) | **2.6+** (hard requirement for NeMo FSDP2) |
| **NeMo** | Not installed | 2.5.0+ |
| **Python** | 3.x | >= 3.10 |
| **GPU** | CPU currently | **NVIDIA GPU required** (8+ GB VRAM) |

There is no workaround for the PyTorch requirement within the same environment. The only path is a separate service/container.

---

## 4. Forced Alignment (Word-Level Timestamps)

### Why We Need This

Current problem: After VAD silence trimming in Phase 1, all timestamps are relative to the trimmed audio, not the original file. For subtitle generation synced to original video, timestamps need remapping.

Forced alignment solves this by aligning ASR text against the **original untrimmed audio**, producing accurate word-level timestamps in the original timeline.

### Tool Comparison

| Tool | Accuracy | Languages | Install Weight | Integration | Tamil/Hindi |
|------|----------|-----------|---------------|-------------|-------------|
| **ctc-forced-aligner** | Good (50-100ms) | **1,130** | Light (~300MB) | **Easy** | **Yes, both** |
| WhisperX | Good (50-100ms) | 30+ | Heavy (~2GB) | Medium | Hindi yes, Tamil custom |
| stable-ts | Moderate | All Whisper langs | Light | Easy | Yes (via Whisper) |
| MFA (Montreal) | **Best** (10-20ms) | 14+ | Heavy (Conda) | Hard (CLI) | Limited |
| NeMo NFA | Good | 14+ | Very heavy | Hard | Limited |
| torchaudio | Good | 30+ | Already installed | Medium | **Deprecated** |
| Whisper char-align | **Very good** | All Whisper langs | Light | Medium | Yes |

### Recommended: ctc-forced-aligner

**Why it's the best fit for our project:**

1. **1,130 languages** including Tamil (`tam`) and Hindi (`hin`) — via Facebook's MMS model
2. **5x less memory** than torchaudio's implementation
3. **Light dependencies** — no faster-whisper, no pyannote needed
4. **Works with torch 2.1.1** (doesn't require torch upgrade)
5. **Simple Python API** — integrate directly into our pipeline

### How CTC Forced Alignment Works (Beginner Explanation)

Imagine you have a recording of someone saying "Hello, how are you?" and Whisper gave you the text. Forced alignment figures out *exactly when* each word was spoken:

1. A wav2vec2 model listens to the audio and produces a probability grid: for each tiny moment in time (~20ms), what's the probability of each character being spoken?
2. Given the known text, a dynamic programming algorithm (Viterbi) finds the optimal path through this grid
3. This path tells you: "H" was at 0.00-0.05s, "e" at 0.05-0.08s, etc.
4. Characters are grouped into words: "Hello" = 0.00-0.18s, "how" = 0.35-0.48s, etc.

The result is word-level timestamps accurate to ~50ms (compared to Whisper's segment-level timestamps which can be off by seconds).

### Integration Strategy: Align Against Original Audio

```
[Original Audio] --> [Phase 1: Resample + Highpass] --> [Save pre-VAD copy]
                                                              |
                                                     [VAD trimming]
                                                              |
                                                     [ASR: Whisper → text + rough timestamps]
                                                              |
[Pre-VAD Audio] --> [ctc-forced-aligner] --> [Word timestamps in ORIGINAL timeline]
                           ^
                           |
                     [Text from ASR]
```

Key: Run alignment against the **pre-VAD-trimmed audio** (16kHz mono, highpass-filtered). The aligner finds where each word occurs acoustically, completely bypassing the timestamp remapping problem.

### ctc-forced-aligner Usage Example

```python
from ctc_forced_aligner import (
    load_audio, load_alignment_model, generate_emissions,
    preprocess_text, get_alignments, get_spans, postprocess_results
)

# Load model once (singleton)
alignment_model, alignment_tokenizer = load_alignment_model(device)

# Load pre-VAD audio (original timeline)
audio_waveform = load_audio("pre_vad_audio.wav", alignment_model.dtype, device)

# Align ASR text against original audio
emissions, stride = generate_emissions(alignment_model, audio_waveform, batch_size=16)

# For Tamil: language="tam", for Hindi: language="hin", for English: language="eng"
tokens_starred, text_starred = preprocess_text(
    asr_text, romanize=True, language="eng"
)

segments, scores, blank_token = get_alignments(emissions, tokens_starred, alignment_tokenizer)
spans = get_spans(tokens_starred, segments, blank_token)
word_timestamps = postprocess_results(text_starred, spans, stride, scores)
```

### WhisperX — Why We're Not Using It

WhisperX 3.7.6 requires `torch>=2.5.1` — same incompatibility as Canary-Qwen. It would also pull in `faster-whisper`, `pyannote-audio`, and other heavy dependencies even if we only need alignment. The `ctc-forced-aligner` provides equivalent alignment quality with much lighter dependencies.

---

## 5. Sales Call Edge Cases & Best Practices

### Proper Nouns (Company Names, Person Names)

- **Error rates**: 15-40% on proper nouns not in the model's vocabulary, compared to 2-5% WER for common words
- **Indian names** are particularly challenging: "Balasubramanian" might be split or mangled
- **Mitigation strategies**:
  - **Hotword boosting** via `initial_prompt` (Whisper) — include expected names: `"The following is a sales call discussing Zoho CRM, Freshdesk, Balasubramanian"`
  - **Post-processing NER** — cross-reference against CRM contact list
  - **Custom vocabulary correction** — edit distance + phonetic similarity (Soundex/Metaphone)
  - With boosting: 85-95% accuracy on domain terms (vs 60-70% without)

### Number Transcription

- Whisper achieves 85-92% accuracy on numbers in conversational speech
- Common errors: "fifteen" vs "fifty", "thirteen" vs "thirty"
- Phone numbers are particularly error-prone: grouping/missing digits
- **Indian English specifics**: "one lakh fifty thousand" = 1,50,000 — most ITN libraries get this wrong
- **Recommendation**: Add Inverse Text Normalization (ITN) post-processing for number formatting
  - Best library: `nemo_text_processing` (NVIDIA, 20+ languages)

### Hallucination at Chunk Boundaries

- Well-documented Whisper issue: silence at chunk end → phantom text ("Thank you for watching", "Please subscribe")
- **Detection**: `no_speech_probability > 0.6` and `compression_ratio > 2.4`
- **Prevention**:
  - Trim trailing silence from each chunk before transcription
  - Set `temperature=0` (greedy decoding)
  - De-duplicate repeated text across chunk boundaries (fuzzy matching last N words of chunk K vs first N words of chunk K+1)
  - Forced alignment can flag words without acoustic evidence

### Cross-Talk / Overlapping Speech

- 10-20% of typical sales call speaking time involves some overlap
- During overlap, WER jumps to 40-80%
- **Current best approach** (Phase 2-3): Pyannote detects overlapping regions → mark as "[cross-talk]" → still attempt transcription but flag low confidence
- **Future** (Phase 6): Target Speaker Extraction (TSE) to actually separate and transcribe overlapping speakers

### Hold Music and IVR Detection

- Whisper hallucates during hold music (generates phantom text)
- Silero VAD sometimes classifies music as speech
- **Recommendation**: Tag hold music segments and exclude from intelligence layer (Phase 5)
- Can use simple spectral analysis: sustained tonal energy + low spectral variation = music

### Chunk Boundary Best Practices

Your current VAD-based + energy-based fallback chunking is solid. Additional improvements:

| Technique | Benefit | Cost |
|-----------|---------|------|
| **De-duplication** | Removes repeated text at boundaries | Minimal (regex/fuzzy match) |
| **Overlap-and-merge** | Eliminates word-splitting | 10-20% more compute |
| **Context carry-over** (already implemented) | Consistent formatting, proper nouns | None |
| **compression_ratio filtering** | Catches hallucination/repetition | Minimal |

### Confidence Scoring Thresholds (Production Standards)

| Confidence Level | Threshold | Display |
|-----------------|-----------|---------|
| High | > 0.9 | Normal text |
| Medium | 0.7 - 0.9 | Light highlight |
| Low | 0.4 - 0.7 | Strong highlight |
| Very low | < 0.4 | Replace with "[inaudible]" |

### Evaluation: Beyond WER

For sales calls, standard WER is insufficient. Domain-specific metrics:

1. **Named Entity Accuracy**: % of company/person/product names correct (target: >85% F1)
2. **Number Accuracy**: % of prices, dates, phone numbers correct (target: >90%)
3. **Action Item Preservation**: Do key business facts survive transcription? (target: >95%)
4. **Speaker Attribution Accuracy (WDER)**: Combined WER + diarization error (target: <15%)

**Recommendation**: Build a 20-50 call gold-standard evaluation set with manual transcriptions, entity annotations, and key information markers. Use for regression testing across model upgrades.

---

## 6. Compatibility Analysis with Our Stack

### Current Constraints

| Package | Version | Pins |
|---------|---------|------|
| `torch` | 2.1.1 | Pinned by `resemble-enhance==0.0.1` |
| `torchaudio` | 2.1.1 | Must match torch |
| `transformers` | <4.46 | Incompatible with torch 2.1.1 above this |
| `numpy` | <2 | Compatibility pin |
| `pyannote.audio` | 3.1.1 | Phase 2, works with torch 2.1.1 |

### What's Compatible

| Tool | Requires | Compatible? | Notes |
|------|----------|-------------|-------|
| `faster-whisper` >=1.0.0 | CTranslate2, no specific torch pin | **Yes** (already installed) | Uses own C++ inference engine |
| `ctc-forced-aligner` | torch (flexible), transformers | **Likely yes** | Uses MMS model, needs testing |
| Whisper large-v3-turbo | Works via faster-whisper | **Yes** | Just download the model |
| `stable-ts` | Same as whisper | **Yes** | Lightweight |
| `nemo_text_processing` | Pynini, no torch requirement | **Yes** | For ITN (number formatting) |

### What's NOT Compatible

| Tool | Requires | Issue |
|------|----------|-------|
| Canary-Qwen 2.5B | NeMo + PyTorch 2.6+ | Hard torch conflict |
| WhisperX 3.7.6 | torch>=2.5.1 | Hard torch conflict |
| AI4Bharat IndicConformer | NeMo | Same as Canary-Qwen |
| Dolphin ASR | ESPnet | Heavy dependencies, needs testing |

### The torch 2.1.1 Bottleneck

`resemble-enhance==0.0.1` pins `torch==2.1.1`. This single dependency blocks every modern ASR model except Whisper variants. Options:

1. **Keep resemble-enhance**: Stay on torch 2.1.1, use faster-whisper (recommended for Phase 3)
2. **Drop resemble-enhance**: Upgrade torch, unlock Canary-Qwen/WhisperX (but lose Path B enhancement)
3. **Microservice**: Run new models in separate containers with their own torch versions
4. **Wait for resemble-enhance update**: Unknown timeline

---

## 7. Recommended Phase 3 Architecture

### What We Can Do NOW (Option A)

These changes are all compatible with our current torch 2.1.1 stack:

#### Step 1: Switch to faster-whisper (4x speedup, zero accuracy loss)

You already have `faster-whisper>=1.0.0` in requirements.txt but `transcribe.py` uses `openai-whisper`. Switching the default engine to faster-whisper gives:
- **4x faster** transcription on CPU (CTranslate2 C++ backend)
- **Less memory** (~50% less than PyTorch Whisper)
- **INT8 quantization** available for even more speed on CPU
- **Identical accuracy** — it runs the same Whisper model weights

#### Step 2: Upgrade to large-v3-turbo model (better accuracy)

- 809M params (vs medium's 769M)
- ~7.0% WER (vs medium's ~7.9%)
- 4x faster than large-v3 (only 4 decoder layers vs 32)
- **Feasible on CPU** with faster-whisper + int8

#### Step 3: Add ctc-forced-aligner (word-level timestamps)

- Install: `pip install ctc-forced-aligner`
- Save pre-VAD audio path in pipeline
- After ASR, run alignment against pre-VAD audio
- Result: word-level timestamps in original audio timeline
- Supports English, Tamil, Hindi (1,130 languages total)

#### Step 4: Post-processing improvements

- **Hallucination filtering**: Drop segments with `no_speech_prob > 0.6` or `compression_ratio > 2.4`
- **Chunk boundary de-duplication**: Fuzzy match last N words of chunk K vs first N words of chunk K+1
- **Hotword boosting**: Pass domain vocabulary in `initial_prompt` / `initial_prompt` for each chunk
- **ITN (optional)**: Inverse Text Normalization for number/date formatting

### Pipeline Flow (After Phase 3)

```
Input audio
    |
    v
[1. Audio Analysis] — SNR, clipping, sample rate, channels
    |
    v
[2. Core Processing] — resample 16kHz, highpass 80Hz, loudnorm
    |
    v  ──── [Save pre-VAD audio path] ────┐
    |                                       |
[2c. VAD silence trimming]                  |
    |                                       |
    v                                       |
[3. Conditional Processing] — denoise, BWE, declipping
    |                                       |
    v                                       |
[3.5. Speaker Diarization] (Phase 2)        |
    |                                       |
    v                                       |
[4. ASR: faster-whisper large-v3-turbo]     |
    |  with hotword boosting,               |
    |  hallucination filtering,             |
    |  chunk de-duplication                 |
    v                                       |
[5. Forced Alignment: ctc-forced-aligner] <─┘
    |  Align text against pre-VAD audio
    |  Word-level timestamps in original timeline
    v
[6. Post-processing]
    |  ITN (number formatting)
    |  Confidence tagging
    v
Results: text + word timestamps + speaker labels + confidence
```

### What We Defer to Later Phases

| Capability | Phase | Why Defer |
|-----------|-------|-----------|
| Canary-Qwen 2.5B | Phase 6 (GPU) | Requires PyTorch 2.6+ and NVIDIA GPU |
| AI4Bharat IndicConformer | Phase 4 | Requires NeMo + GPU, Tamil/Hindi specific |
| Dolphin ASR | Phase 4 | Eastern language specialist, code-switching |
| Batch GPU inference | Phase 6 | Requires GPU hardware |
| Target Speaker Extraction | Phase 6 | Cross-talk handling, research stage |

---

## 8. Sources

### Canary-Qwen & NVIDIA Models
- [NVIDIA Canary-Qwen-2.5B (HuggingFace)](https://huggingface.co/nvidia/canary-qwen-2.5b)
- [MarkTechPost: Canary-Qwen-2.5B Release](https://www.marktechpost.com/2025/07/17/nvidia-ai-releases-canary-qwen-2-5b-a-state-of-the-art-asr-llm-hybrid-model-with-sota-performance-on-openasr-leaderboard/)
- [NVIDIA Developer Blog: Parakeet TDT](https://developer.nvidia.com/blog/turbocharge-asr-accuracy-and-speed-with-nvidia-nemo-parakeet-tdt/)
- [Faster-Whisper vs Canary-Qwen-2.5B (HuggingFace Blog)](https://huggingface.co/blog/norwooodsystems/faster-whisper-vs-canary-qwen-2-5b)
- [NVIDIA NeMo GitHub](https://github.com/NVIDIA-NeMo/NeMo)

### ASR Benchmarks & Leaderboards
- [HuggingFace Open ASR Leaderboard](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard)
- [Northflank: Best Open Source STT Model 2026](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks)
- [Modal: Top Open Source STT Models 2025](https://modal.com/blog/open-source-stt)
- [Artificial Analysis: Speech to Text Leaderboard](https://artificialanalysis.ai/speech-to-text)
- [OpenASR Leaderboard Paper (arXiv)](https://arxiv.org/html/2510.06961v2)

### Indian Language ASR
- [Deepgram: How OpenAI's Text Normalization Hides Whisper's True WER for South Asian Languages](https://deepgram.com/learn/how-openai-s-text-normalization-hides-whisper-s-true-word-error-rate-for-south-asian-and-southeast-asian-languages)
- [AI4Bharat IndicConformer (GitHub)](https://github.com/AI4Bharat/IndicConformerASR)
- [AI4Bharat IndicConformer 600M (HuggingFace)](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual)
- [Dolphin ASR (GitHub)](https://github.com/DataoceanAI/Dolphin)
- [Dolphin Paper (arXiv)](https://arxiv.org/abs/2503.20212)

### Forced Alignment
- [WhisperX GitHub](https://github.com/m-bain/whisperX)
- [WhisperX Paper (Interspeech 2023)](https://arxiv.org/abs/2303.00747)
- [ctc-forced-aligner GitHub](https://github.com/MahmoudAshraf97/ctc-forced-aligner)
- [ctc-forced-aligner PyPI](https://pypi.org/project/ctc-forced-aligner/)
- [MMS-300M Forced Aligner (HuggingFace)](https://huggingface.co/MahmoudAshraf/mms-300m-1130-forced-aligner)
- [Tradition or Innovation: Comparison of ASR Methods for Forced Alignment (Interspeech 2024)](https://arxiv.org/html/2406.19363v1)
- [Whisper Has an Internal Word Aligner (Sep 2025)](https://arxiv.org/abs/2509.09987)
- [CrisperWhisper (Interspeech 2024)](https://arxiv.org/abs/2408.16589)
- [stable-ts GitHub](https://github.com/jianfch/stable-ts)

### Whisper Ecosystem
- [faster-whisper GitHub](https://github.com/SYSTRAN/faster-whisper)
- [Whisper Large-v3-turbo (HuggingFace)](https://huggingface.co/openai/whisper-large-v3-turbo)
- [Modal: Choosing Between Whisper Variants](https://modal.com/blog/choosing-whisper-variants)

### Commercial ASR
- [Deepgram: Introducing Nova-3](https://deepgram.com/learn/introducing-nova-3-speech-to-text-api)
- [ElevenLabs: Introducing Scribe v2](https://elevenlabs.io/blog/introducing-scribe-v2)
- [AssemblyAI: Universal-2](https://www.assemblyai.com/universal-2)

### Edge Cases & Production Practices
- [AWS Blog: Custom Vocabulary for Call Transcription](https://aws.amazon.com/blogs/machine-learning/improve-transcription-accuracy-of-customer-agent-calls-with-custom-vocabulary-in-amazon-transcribe/)
- [NVIDIA Blog: Multilingual and Code-Switched ASR with NeMo](https://developer.nvidia.com/blog/multilingual-and-code-switched-automatic-speech-recognition-with-nvidia-nemo/)
- [2025 Edge Speech-to-Text Benchmark (Ionio)](https://www.ionio.ai/blog/2025-edge-speech-to-text-model-benchmark-whisper-vs-competitors)
