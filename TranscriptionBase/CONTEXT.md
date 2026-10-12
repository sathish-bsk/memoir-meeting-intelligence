# TranscriptionBase — Full Implementation Context

## Project Overview

Sales call transcription & intelligence platform. Self-hosted models (not cloud APIs). Takes audio from file uploads, media URLs, or YouTube, produces accurate transcriptions with speaker identification, and eventually extracts structured intelligence for CRM integration.

**Primary use case:** English + Tamil/Hindi mixed-language sales calls.

**Stack:** FastAPI + Whisper (openai-whisper / faster-whisper) + Silero VAD + DeepFilterNet3 + Resemble Enhance + Pyannote 3.1.1 (speaker diarization, implemented)

**Entry point:** `uvicorn app.main:app --host 0.0.0.0 --port 8000`

**URLs:**
- Main transcription UI: `http://localhost:8000/`
- Audio preprocessing A/B comparison: `http://localhost:8000/audio-preprocess/`
- Post-processing test page: `http://localhost:8000/audio-preprocess/post-process`
- Vocabulary management: `http://localhost:8000/audio-preprocess/vocabulary`
- API docs: `http://localhost:8000/docs`

---

## 9-Phase Roadmap

| Phase | Name | Status |
|-------|------|--------|
| **1** | The "Clean Audio" Foundation — smart audio routing, detection-first preprocessing, multilingual mode | **IMPLEMENTED** |
| **2** | Who is Who? — Pyannote 3.1.1 speaker diarization | **IMPLEMENTED** |
| **3** | World-Class ASR — ctc-forced-aligner + VAD remapping + tail re-transcription + multilingual survey + Whisper translate | **IMPLEMENTING** |
| **4** | Multilingual Mastery — specialist ASR models (IndicWhisper, IndicConformer) + dedicated translation | **DEFERRED** (code in `phase4_multilingual` branch) |
| **5** | Transcription Post-Processing — vocabulary matching + LLM context correction (Qwen 2.5 7B via Ollama) | **5a+5b IMPLEMENTED**, 5c Pending |
| **6** | Domain Vocabulary & Jargon — abbreviation normalization + fuzzy matching (hotword boosting DISABLED) | **6a+6b IMPLEMENTED**, 6c Pending |
| **7** | The "Gong" Intelligence Layer — coaching metrics + LLM extraction (incl. sentiment) + scoring | **7A+7B IMPLEMENTED**, 7C Pending |
| **8** | Technical Gotchas — cross-talk, GPU memory, Canary-Qwen 2.5B (requires PyTorch 2.6+) | Pending |
| **9** | Post-Processing Microservice — extract Phases 5-7 into separate microservice | Future |
| **10** | Memoir Post-Processing — CRM-ready call summary with confidence tagging + source attribution | **10A+10B+10C IMPLEMENTED**, 10D-F Future |

---

## Directory Structure

```
TranscriptionBase/
├── CLAUDE.md                          # Project instructions for Claude
├── CONTEXT.md                         # This file — full implementation context
├── requirements.txt                   # All Python dependencies (no resemble-enhance)
├── docker-compose.yml                 # Microservice orchestration (resemble-enhance build context → ../resemble_enhance)
# Note: resemble-enhance service moved to top-level resemble_enhance/ directory
├── app/
│   ├── __init__.py                    # OpenMP fix (KMP_DUPLICATE_LIB_OK + ctranslate2 import)
│   ├── main.py                        # FastAPI app — mounts both UIs
│   ├── core/
│   │   ├── config.py                  # Original env-based settings
│   │   └── logging.py                 # Request-scoped logging
│   ├── services/
│   │   ├── asr.py                     # Original Whisper ASR (openai-whisper only)
│   │   ├── asr_pingala.py             # Pingala V1 Universal ASR
│   │   ├── asr_ai4bharat.py           # AI4Bharat Tamil ASR
│   │   ├── audio_preprocessor.py      # Original noisereduce/DeepFilterNet + VAD
│   │   ├── ffmpeg.py                  # Original format conversion
│   │   ├── ingest.py                  # File upload, URL download, YouTube (yt-dlp)
│   │   └── cleanup.py                 # Temp directory cleanup
│   └── audio_preprocess/              # Phase 1 module
│       ├── __init__.py                # Defers to app/__init__.py for OpenMP fix
│       ├── config.py                  # All thresholds, ASR settings, multilingual settings, timeouts
│       ├── router.py                  # FastAPI router at /audio-preprocess
│       ├── ui.py                      # Full HTML UI (dark theme, drag-drop, PDF export, multilingual toggle)
│       ├── static/
│       │   └── fonts/
│       │       ├── NotoSansTamil-Regular.ttf       # 78KB, Latin + Tamil glyphs
│       │       └── NotoSansDevanagari-Regular.ttf   # 219KB, Latin + Devanagari glyphs
│       ├── postprocess_ui.py          # Phase 7: Standalone post-processing test page
│       └── services/
│           ├── analyzer.py            # Audio analysis (SNR, clipping, sample rate)
│           ├── core_processing.py     # Always-on: resample, highpass, VAD (returns boundaries), loudnorm
│           ├── conditional.py         # If-needed: denoise, bandwidth ext, declipping
│           ├── diarize.py             # Phase 2: Pyannote 3.1.1 speaker diarization + speaker alignment
│           ├── aligner.py             # Phase 3: ctc-forced-aligner word-level timestamps (ONNX, per-segment, multilingual)
│           ├── enhance_playback.py    # Resemble Enhance (Path B only)
│           ├── transcribe.py          # Dual-engine ASR with smart chunking, multilingual, tail re-transcription
│           ├── schemas.py             # Phase 7: Pydantic models for CallIntelligence JSON
│           ├── coaching_metrics.py    # Phase 7A: Talk ratio, monologue, questions, fillers, interruptions
│           ├── intelligence.py        # Phase 7A+7B: LLM extraction + sentiment + scoring
│           ├── memoir_schemas.py      # Phase 10: Pydantic models for Memoir call summary (independent from schemas.py)
│           ├── memoir_extractor.py    # Phase 10: Memoir LLM extraction (confidence, attribution, off-the-record redaction)
│           ├── vocabulary.py          # Phase 5a+6a: Vocabulary matching + abbreviation normalization
│           ├── llm_corrector.py       # Phase 5b: LLM context correction via Ollama
│           └── pipeline.py            # Orchestrator — A/B flow + diarization + alignment + VAD remapping + intelligence/memoir
```

---

## Critical: OpenMP Fix (macOS)

**Problem:** `torch 2.1.1` and `ctranslate2` (faster-whisper backend) both bundle `libiomp5.dylib` on macOS. Loading both causes a segfault, especially with INT8 quantized compute types.

**Fix location:** `app/__init__.py` — MUST run before any other module imports torch.

```python
import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

try:
    import ctranslate2
except ImportError:
    pass
```

**Why `app/__init__.py` and not `app/audio_preprocess/__init__.py`:** In `app/main.py`, `app.services.asr` (which imports torch) is imported on line 24, BEFORE `app.audio_preprocess` on line 28. The fix must be in the root `app/__init__.py` which Python executes before any submodule.

**Version constraints:**
- `resemble-enhance 0.0.1` pins `torch==2.1.1` — cannot upgrade torch
- `transformers>=4.46` is incompatible with `torch 2.1.1` (AttributeError on `register_pytree_node`) — pinned to `<4.46`

---

## Dual ASR Engine Architecture

### Engine Selection

Two ASR engines available, selected via UI dropdown:

| Engine | Backend | Speed (CPU) | Quantization | Package |
|--------|---------|-------------|--------------|---------|
| **faster-whisper** (default) | CTranslate2 (C++) | ~3.1x faster | INT8 | `faster-whisper>=1.0.0` |
| **openai-whisper** | PyTorch | Baseline | float32 only | `openai-whisper>=20231117` |

Both use identical Whisper model weights. Output is identical with float32, <0.1% WER difference with INT8.

### Model Size Selection

UI dropdown with options: `tiny`, `base`, `small`, `medium` (default), `large-v3`

### Model Caching

Separate singletons for each engine with size tracking:

```
openai-whisper: _ow_model / _ow_model_size + _ow_lock
faster-whisper: _fw_model / _fw_model_size + _fw_lock
```

When model size changes between requests, the old model is deleted (`del` + `gc.collect()`) and the new size is loaded. Both engines can coexist in memory but typically only one is active.

### Parameter Tuning

Key optimization: `best_of` changed from 5 to 1 for both engines. With `temperature=0`, decoding is deterministic — `best_of=5` ran 5 identical samples. This alone gives ~1.5-2x speedup with zero quality impact.

```
beam_size: 5 (kept — meaningful for search)
best_of: 1 (was 5 — redundant with temperature=0)
temperature: 0.0 (deterministic)
vad_filter: False (faster-whisper only — preprocessing already handles VAD)
```

### Benchmark Results

faster-whisper medium INT8 = 11.78s vs openai-whisper medium = 36.31s (**3.1x speedup**)

---

## Phase 1 Pipeline Architecture

### The Core Problem

Raw sales call audio has issues — noise, silence, wrong sample rates, clipping — that hurt transcription accuracy. And Whisper has known failure modes: it hallucinates on silence, picks one language for the entire file, and generates repetition loops. The pipeline solves this by: detect what each audio file needs → apply minimum processing → smart chunking → transcription with hallucination filtering.

### Detection-First, Not Always-On

Instead of enhancing everything, detect what each audio file needs and apply minimum processing. Research shows aggressive enhancement HURTS ASR accuracy (Dec 2025 paper: denoising degraded WER in ALL 40 test configurations). Production services like Rev.ai, Deepgram, AssemblyAI do zero preprocessing — they train on noisy data instead.

### Pipeline Flow

```
Input audio
    │
    ▼
[1. Audio Analysis] — SNR, clipping %, sample rate, duration, channels
    │
    ▼
[2. Core Processing — Always Applied]
    ├── 2a. Resample to 16kHz mono (required by Silero VAD + Whisper)
    ├── 2b. Highpass 80Hz (remove rumble — NO lowpass, preserve consonants)
    ├── 2c. Silero VAD silence trimming (prevent Whisper hallucinations on silence)
    └── 2d. FFmpeg loudnorm dual-pass (-23 LUFS, EBU R128)
    │
    ▼
[3. Conditional Processing — Only When Detected]
    ├── IF sample_rate ≤ 8kHz → bandwidth extension (FFmpeg soxr)
    ├── IF clipping > 0.1% → declipping (tanh soft saturation)
    └── IF SNR < 15dB → DeepFilterNet3 + Observation Adding (70/30 blend)
    │
    ▼
[4. Path Selection — user chooses in UI]
    ├── Path A: Direct ASR (recommended, faster)
    ├── Path B: Resemble Enhance → ASR (perceptual quality testing only)
    └── Both: A/B comparison
    │
    ▼
[5. Transcription (Whisper ASR)]
    ├── Short audio (≤30s): single-pass transcription
    ├── Long audio (>30s): VAD-based smart chunking (≤28s chunks)
    ├── Multilingual mode (optional): language survey + chunk-level dual-pass
    └── Hallucination filtering on ALL paths
    │
    ▼
Results (transcription + segments + timestamps + quality metrics)
```

---

## Phase 1: Input Analysis (Step 1)

**What:** Read the raw audio file and measure its properties using soundfile + numpy.

**Metrics measured:**
- **Sample rate** (e.g., 48000 Hz) — how many audio samples per second. Higher = more detail.
- **Channels** (mono/stereo) — most speech is mono; stereo doubles processing for no ASR benefit.
- **SNR (Signal-to-Noise Ratio)** — how much louder speech is vs background noise, in dB. 60dB = clean, 15dB = noisy, <10dB = very noisy. Uses energy-based estimation with a coefficient of variation check (CV < 0.3 means steady signal like a tone, switches to RMS-based estimate).
- **Peak/RMS amplitude** — peak is maximum loudness; RMS is average loudness.
- **Clipping percentage** — what % of samples are at maximum amplitude (flat-topped waveforms).

**Why:** Every subsequent step is conditional on these measurements. Clean audio skips denoising. High sample rate skips bandwidth extension. No clipping skips declipping. The philosophy is **less processing = better ASR**.

**Output:** `AudioAnalysis` dataclass with three boolean flags: `needs_bandwidth_extension`, `needs_declipping`, `needs_denoising`.

---

## Phase 2: Core Processing — Always Applied (Step 2)

These 4 steps always run because they're required by downstream tools (VAD, Whisper) or universally improve quality.

### Step 2a: Resample to 16kHz Mono

**What:** Convert audio to 16000 Hz sample rate, single channel, using FFmpeg.

**Why:**
- Silero VAD **only accepts 8000 or 16000 Hz**. It crashes on other rates.
- Whisper internally converts to 16kHz anyway — doing it upfront avoids redundant conversions.
- Mono because speech is the same in both channels; stereo wastes computation.
- Done **first** so all subsequent steps operate on consistent 16kHz mono audio.

**Command:** `ffmpeg -i input -ar 16000 -ac 1 -c:a pcm_s16le output.wav`

### Step 2b: Highpass Filter at 80 Hz

**What:** Remove all frequencies below 80 Hz using FFmpeg.

**Why:**
- Below 80 Hz is rumble, AC hum (50/60 Hz), handling noise, HVAC — none of it is speech.
- These low frequencies add energy that confuses SNR estimation and loudness normalization.
- **No lowpass filter applied** — high frequencies (4kHz-8kHz) contain consonant information (/s/, /f/, /th/) that Whisper needs. Amazon Transcribe warns removing high frequencies "turns fifteen into thirteen."

**Command:** `ffmpeg -af "highpass=f=80"`

### Step 2c: Silero VAD Silence Trimming

**What:** Use Silero VAD (Voice Activity Detection) neural network to detect which parts contain speech, then keep only speech segments with 200ms padding.

**Why:**
- Whisper **hallucinates on silence and background noise**. Given 10 seconds of silence, Whisper generates phantom text — words never spoken. This is Whisper's most well-known failure mode.
- A 48-minute sales call with 50% silence → 24 minutes of silence for Whisper to hallucinate on.
- By removing non-speech, we eliminate the primary hallucination trigger.
- 200ms padding preserves natural word boundaries so speech isn't cut mid-syllable.

**How Silero VAD works:** ~1MB PyTorch neural network trained for speech detection. Outputs per-frame speech probabilities. Parameters: threshold=0.5, min_speech_duration=250ms, speech_pad=200ms.

**Model:** Loaded once as a singleton (thread-safe with lock). CPU-only.

**Example:** test.mp3 (48 min) — removed 277s of non-speech from 2884s → 2607s kept.

### Step 2d: Dual-Pass Loudness Normalization

**What:** Normalize audio loudness to -23 LUFS (EBU R128 broadcast standard) using FFmpeg's `loudnorm` filter in two passes.

**Why:**
- Audio recordings vary wildly in volume. Consistent loudness gives Whisper consistent input.
- Deepgram's research shows 5-10% WER improvement from loudness normalization.
- -23 LUFS is the broadcast standard — likely what Whisper was trained on.

**Why dual-pass (not single-pass):**
- **Pass 1:** Measures actual loudness properties (integrated loudness, loudness range, true peak, threshold). No modification.
- **Pass 2:** Uses measured values to apply precise correction with `linear=true` for transparent normalization.
- Single-pass guesses input properties and can over/under-correct. Dual-pass is exact.

**Fallback:** If pass 1 measurement fails (e.g., silent audio), falls back to single-pass.

---

## Phase 3: Conditional Processing — Only When Needed (Step 3)

These steps only run when analysis detects a specific problem. **Don't fix what isn't broken.**

### Step 3a: Bandwidth Extension (IF sample_rate ≤ 8000 Hz)

**What:** High-quality upsampling from telephone-quality (8kHz) to 16kHz using FFmpeg's SoX resampler.

**Why:**
- Telephone audio (8kHz) captures only frequencies up to 4kHz. Speech consonants (/s/, /f/, /th/) exist at 4-8kHz. Without them, "fifteen" and "thirteen" sound identical to Whisper.
- SoX resampler with precision=33 and triangular dithering is the highest quality resampler in FFmpeg.

**Skipped when:** Sample rate > 8kHz.

### Step 3b: Declipping (IF clipping > 0.1%)

**What:** Apply soft saturation using `tanh` compression to smooth clipped peaks.

**Formula:** `output = tanh(audio * 0.8) / tanh(0.8)` — compresses peaks smoothly instead of cutting them flat.

**Why:**
- Clipped audio has flat-topped waveforms — harsh distortion that confuses ASR.
- 0.1% threshold because some clipping is normal (transients); only systematic clipping hurts.

**Skipped when:** Clipping < 0.1%.

### Step 3c: Denoising with Observation Adding (IF SNR < 15 dB)

**What:** Run DeepFilterNet3 neural denoiser, then blend enhanced + original: `0.7 × enhanced + 0.3 × original`.

**Why denoising is conditional:**
- Research (Dec 2025): denoising degraded WER in ALL 40 test configurations (4 ASR models × 10 noise conditions).
- Enhancement artifacts are worse for ASR than original noise.
- Only applied when SNR < 15dB (genuinely noisy audio).

**Why Observation Adding (OA):**
- Pure denoising introduces metallic/robotic artifacts that confuse ASR more than moderate noise.
- OA preserves natural speech characteristics from the original while gaining noise reduction from the enhanced version.
- Research shows OA improves signal-to-artifact ratio and downstream ASR accuracy vs pure denoising.

**DeepFilterNet3:** ~10MB neural network for speech enhancement. Runs at 48kHz internally, resampled back to 16kHz. Singleton with thread lock.

**Skipped when:** SNR ≥ 15dB.

---

## Phase 4: Path Selection (User Choice)

### Path A: Direct ASR (Recommended)

Preprocessed audio → Whisper transcription directly.

**Why recommended:** Preprocessing already handled what was needed. Adding more processing risks artifacts. This is what production services do.

### Path B: Resemble Enhance → ASR

Preprocessed audio → Resemble Enhance (generative diffusion model, ~300MB) → Whisper transcription.

**WARNING: Dangerous for ASR.** Being generative, it can **fabricate phonemes** — create speech sounds not in the original audio. In testing, it created massive repetition loops ("He is feeling shy" ×13, "ive seen at work" ×100+) because the diffusion model generated repeating patterns that Whisper faithfully transcribed.

**When to use Path B:** Only for human listening quality (demos, archive restoration). **Never for ASR accuracy on clean audio.** On a 60dB SNR recording, Resemble Enhance makes things worse, not better.

---

## Phase 5: Transcription (Whisper ASR)

### The Model

**faster-whisper (default):** CTranslate2 implementation. 4-8× faster than OpenAI's PyTorch version on CPU, same accuracy. INT8 quantization.

**whisper medium (769M params, ~1.5GB):** English WER ~2.7%, Tamil WER ~35%. Configurable — can switch to `large-v3` for better accuracy (slower).

Both engines loaded as singletons (one model in memory, reused across requests, thread-safe with locks).

### Language Detection

- **Auto-detect (default):** Whisper detects language from audio. Problem: detects once from first 30s, applies to entire file.
- **Forced language:** User selects specific language. Only works if entire call is one language.
- **Multilingual mode:** See dedicated section below.

### Short Audio Path (≤ 30s)

Single-pass transcription. Whole audio fits in Whisper's 30-second context window.

Options:
- `beam_size=5` — explores 5 candidate sequences at each step
- `temperature=0` — deterministic decoding
- `condition_on_previous_text=True` — context from previous segments
- `initial_prompt` — biases toward proper punctuation
- `compression_ratio_threshold=2.4` — Whisper's internal repetition check
- `no_speech_threshold=0.6` — Whisper's internal silence detection

### Long Audio Path: Smart Chunking (> 30s)

Whisper's context window is 30 seconds. Longer audio is split into chunks.

**Step 1 — VAD-based chunking:**
- Silero VAD finds speech segments with pauses ≥ 500ms as boundaries.
- Groups consecutive segments into chunks ≤ 28 seconds (2s margin from Whisper's 30s limit).

**Step 2 — Energy-based fallback:**
- After VAD silence trimming (Step 2c), all pauses ≥ 300ms were removed. The chunking VAD often sees one continuous speech block with no boundaries.
- For any chunk > 28s, an energy-based splitter scans for lowest-energy 50ms windows — natural breath pauses, micro-silences between words.
- Searches in the middle 60% of the chunk (avoids cutting at very start/end).
- Recursively splits until all chunks ≤ 28s.

**Step 3 — Per-chunk transcription:**
- Each chunk transcribed independently.
- **Per-chunk language detection:** When language=auto, each chunk gets its own detection.
- **Context carry-over:** Last 200 characters of previous chunk fed as `initial_prompt` to next chunk for continuity.

**Step 4 — Hallucination filtering:**
- Each chunk's segments checked for hallucination indicators (see Hallucination Filtering section).
- Hallucinated segments removed. Chunk text rebuilt from survivors.

**Step 5 — Timestamp remapping:**
- Each chunk's segment timestamps (relative to chunk start) have the chunk offset added for absolute timestamps.

**Step 6 — Assembly:**
- All chunk transcripts joined. Language: single if uniform, or `"mixed (en:4, ta:2)"` if multiple detected. Segments renumbered sequentially.

---

## Hallucination Filtering

### The Problem

Whisper has a well-known failure mode: repetition loops. It generates the same text over and over, producing segments with hundreds of repeated words. This happens especially on:
- Silence or low-energy audio (despite VAD trimming, some gets through)
- Audio processed by Resemble Enhance (generative model creates repeating patterns)
- Segment boundaries where Whisper loses context

### The Filter

Applied on **ALL transcription paths** (short audio, long audio chunked, and multilingual). Runs after each chunk/segment is transcribed, before assembly.

A segment is classified as hallucination if ANY of these trigger:

| Check | Threshold | Rationale |
|-------|-----------|-----------|
| `compression_ratio > 2.4` | Whisper's own default | Measures text repetitiveness. Normal speech: 0.5-2.0. Repetition loops: 5-20+. |
| `avg_logprob < -1.0` | Whisper's own default | Whisper's confidence. Normal: -0.1 to -0.5. Very uncertain: below -1.0. |
| **N-gram repetition** | 3-5 gram match | Same 3-5 word sequence appears consecutively. Definitive proof of looping. |

### What is NOT used as a trigger

**`no_speech_prob` is deliberately NOT a standalone trigger.** Conversational audio frequently has high no_speech_prob (0.5-0.9) on legitimate speech — at turn boundaries, quiet responses, or when one speaker is dominant. In testing on a 48-minute sales call, using `no_speech_prob > 0.5` produced **43 false positives** (real speech deleted), including sentences like:
- "I am speaking in English" (nsp=0.54)
- "How is it going?" (nsp=0.54)
- "Could I ask one of the managers to join the call?" (nsp=0.81)

### Threshold Evolution

| Version | compression_ratio | avg_logprob | no_speech_prob | n-gram | Result |
|---------|------------------|-------------|----------------|--------|--------|
| Initial | > 2.0 | < -0.7 | > 0.5 | ≥10 words | 98 filtered, **43 false positives** |
| **Current** | > 2.4 | < -1.0 | **removed** | ≥6 words | 42 filtered, **3 borderline** |

The current thresholds match Whisper's own internal defaults. The 3 borderline cases are:
- "I will say hi. I will say hi." — real speech (person repeating to shy child), caught by 4-gram match
- 2 segments with cr=2.68 — real speech with formulaic structure

### Real-World Results (test.mp3, 48 min sales call)

True hallucinations caught:
- 32 "ive seen at work" loop segments (cr=13-19)
- 4 "Send it to 100 people" loop segments (cr=12-19)
- 3 "Automated warm-up" loop segments (cr=4.4 + n-gram)
- Various other repetition loops

False positives: 0 legitimate speech segments incorrectly deleted (was 43 with old thresholds).

---

## Multilingual Mode (Optional — UI Checkbox)

### The Problem

A sales call in English + Tamil. Standard Whisper detects English from the first 30s and transcribes everything as English. Tamil portions become garbage. Even with smart chunking giving per-chunk language detection, if a chunk has mixed languages, the minority language is lost.

### How It's Activated

- UI: "Multilingual Mode" checkbox (after language dropdown)
- Checkbox auto-disabled when language is not "Auto-detect" (multilingual requires auto-detection)
- Form parameter: `multilingual=true/false`
- Router parses: `is_multilingual = multilingual.lower() in ("true", "1", "yes", "on")`
- Auto-disabled if language != "auto": `if is_multilingual and language != "auto": is_multilingual = False`

### Architecture: Language Survey + Chunk-Level Dual-Pass

**Stage 1 — Language Survey:**
- Samples 5 evenly-spaced 15-second segments across the entire audio.
- For a 48-minute file: samples at roughly positions 8min, 16min, 24min, 32min, 40min.
- Runs Whisper's `detect_language()` on each sample — encoder-only pass (fast, no transcription).
- Returns top 1-3 candidate languages sorted by vote frequency.

**Why 15s segments:** The original implementation used 1-5s micro-segments. This failed catastrophically — Whisper detected Korean on Tamil audio, Hindi on English audio. Whisper needs ~10-15s for reliable language detection.

**If 1 language detected:** Falls through to standard path with that language forced. No dual-pass overhead. This is the common case for monolingual calls.

**Stage 2 — Chunk-level dual-pass (if 2+ languages):**
- Uses the same 28s smart chunks from the standard path.
- For each chunk, transcribes with **every** candidate language forced.
  - Example: candidates = ["en", "ta"]. Chunk transcribed once with `language="en"`, once with `language="ta"`.
- After each pass, hallucinated segments are filtered out.
- Winner: the language with the highest mean `avg_logprob` (Whisper's confidence).
- **No initial_prompt on first chunk** — the default English prompt leaked into Tamil/Hindi output in the previous implementation, causing hallucinations.
- Context carry-over: previous chunk's transcribed text (not the English prompt) used for subsequent chunks.

**Stage 3 — Assembly:**
- Each segment tagged with `detected_language` (shown in UI "Lang" column).
- Overall language: `"en"` if uniform, or `"mixed (en:4, ta:2)"` if multiple detected.

### LID Method: Whisper's Built-In `detect_language()`

No new dependencies. Both engines support it:

| Engine | API | Returns |
|--------|-----|---------|
| **faster-whisper** | `model.detect_language(audio=np_array)` | `(lang_code, confidence, [(lang, prob), ...])` |
| **openai-whisper** | `model.detect_language(mel_tensor)` | `(probs_tensor, probs_dict)` |

Both use the same Whisper encoder — fast (encoder pass only, no decoder).

### Data Flow

```
UI: [x] Multilingual Mode  →  FormData: multilingual=true
        ↓
Router: process_audio(multilingual="true")  →  is_multilingual=True
        ↓
Pipeline: run_pipeline(multilingual=True)  →  transcribe(multilingual=True)
        ↓
Transcribe: multilingual branch
        ↓
    _survey_languages()         →  ["en", "ta"]  (N × 15s samples, N based on duration)
        ↓
    If 1 language  →  fall through to standard path with forced language
    If 2+ languages →  _transcribe_multilingual()
        ↓
    For each 28s chunk:
        ├── Transcribe with language="en"  →  mean_logprob=-0.25
        ├── Transcribe with language="ta"  →  mean_logprob=-0.85
        └── Winner: "en" (higher logprob)
        ↓
    Filter hallucinations, remap timestamps, tag detected_language
        ↓
    Return: {text, language:"mixed (en:4, ta:2)", segments: [..., detected_language: "en", ...]}
```

### Edge Cases

| Case | Handling |
|------|----------|
| All survey samples same language | Single candidate → standard path with forced language (no overhead) |
| Audio < 15s | Single survey sample using full audio |
| Chunk > 28s in dual-pass | Sub-chunked via existing `_get_speech_chunks()` within the group |
| Multilingual ON but language forced | Auto-disabled (checkbox grayed out in UI, router sets False) |
| No speech detected by VAD | Fall back to energy-based splitting |

### Implementation History

The multilingual feature went through two implementations:

**v1 (Micro-segment approach — FAILED):**
- Split audio into 1-5s micro-segments via fine-grained VAD
- Ran LID on each micro-segment
- Grouped consecutive same-language segments
- Transcribed each group separately

**Why it failed:** 1-5s is too short for reliable LID. Whisper detected Korean on Tamil audio, Hindi on English audio. Short segments also destroyed Whisper's context window, producing terrible transcription. The English initial_prompt leaked into all segments, causing "This is a clear, well-structured transcription..." to appear 3 times in the output.

**v2 (Language Survey + Chunk-Level Dual-Pass — CURRENT):**
- Longer LID samples (15s) for reliable detection
- Keeps existing 28s smart chunks (preserves Whisper context)
- Dual-pass at chunk level (try each candidate language, pick best)
- Hallucination filtering as safety net
- No English-biased initial_prompt for forced-language passes

---

## Processing Steps Detail

| # | Step | Tool | When | Why |
|---|------|------|------|-----|
| 1 | Audio Analysis | soundfile + numpy | Always | Measure SNR, clipping, sample rate to decide what processing is needed |
| 2a | Resample 16kHz mono | FFmpeg subprocess | Always | Required by Silero VAD (only 8k/16k) and Whisper |
| 2b | Highpass 80Hz | FFmpeg subprocess | Always | Remove sub-80Hz rumble/hum. No lowpass — preserve consonant frequencies |
| 2c | Silero VAD trimming | PyTorch model (~1MB) | Always | Remove silence/noise to prevent Whisper hallucinations |
| 2d | Dual-pass loudnorm | FFmpeg subprocess ×2 | Always | Normalize to -23 LUFS for consistent Whisper input. 5-10% WER improvement |
| 3a | Bandwidth extension | FFmpeg soxr subprocess | IF sample_rate ≤ 8kHz | Recover high-freq consonants from telephone-quality audio |
| 3b | Declipping | NumPy tanh | IF clipping > 0.1% | Smooth clipped peaks to reduce distortion |
| 3c | DeepFilterNet3 + OA | PyTorch model (~10MB) | IF SNR < 15dB | Denoise with 70/30 blend to preserve speech characteristics |
| 4 | Resemble Enhance | PyTorch diffusion (~300MB) | Path B only | Generative enhancement — dangerous for ASR, use for playback only |
| 5 | Transcription | Whisper (medium ~1.5GB) | Always | ASR with smart chunking, hallucination filtering, optional multilingual |

**Execution model:** FFmpeg steps use `asyncio.create_subprocess_exec` (non-blocking). Model-based steps run in `loop.run_in_executor(None, ...)` to avoid blocking the event loop. All models loaded as singletons with thread locks.

---

## Whisper Quality Metrics

The segment timestamps table shows three quality metrics per segment:

### avg_logprob (Average Log Probability)
- **What:** Average log probability of each token. Higher (closer to 0) = more confident.
- **Good:** > -0.5 (confident transcription)
- **Concerning:** -0.5 to -1.0 (uncertain)
- **Bad:** < -1.0 (likely errors, hallucination — filtered by hallucination detector)

### no_speech_prob (No Speech Probability)
- **What:** Model's estimate that segment contains no actual speech.
- **Good:** < 0.3 (probably real speech)
- **Note:** High values (0.5-0.9) are common on legitimate conversational speech (turn boundaries, quiet responses). NOT used as a hallucination filter trigger.

### compression_ratio
- **What:** Ratio of token count to text length. Measures repetitiveness.
- **Good:** 0.5 - 2.0 (normal speech)
- **Concerning:** 2.0 - 2.4 (somewhat repetitive but may be real)
- **Bad:** > 2.4 (repetition loop — filtered by hallucination detector)

---

## Whisper Language Behavior

- `language=None` (auto-detect): Whisper detects language ONCE from the first 30s and applies to the ENTIRE file. Smart chunking gives per-chunk detection.
- `language="en"` (forced): All audio decoded as English. Tamil/Hindi segments produce garbage.
- For mixed English + Tamil: use Multilingual Mode (language survey + dual-pass).

---

## Configuration

All values configurable via environment variables.

```python
# Analysis thresholds
SNR_THRESHOLD_DB = 15.0          # Below this → denoise
CLIPPING_THRESHOLD = 0.99        # Sample threshold for clipping detection
LOW_SAMPLE_RATE_HZ = 8000        # Below this → bandwidth extension

# Loudness normalization (EBU R128)
TARGET_LUFS = -23.0
TARGET_TP = -1.5
TARGET_LRA = 11.0

# Observation Adding blend ratio
OA_ALPHA = 0.7                   # 70% enhanced + 30% original

# ASR
ASR_ENGINE = "faster-whisper"    # "faster-whisper" or "openai-whisper"
ASR_MODEL_SIZE = "medium"        # "tiny", "base", "small", "medium", "large-v3"
ASR_DEVICE = "cpu"               # "cpu" or "cuda"
ASR_COMPUTE_TYPE = "int8"        # faster-whisper only

# Multilingual mode
LID_CONFIDENCE_THRESHOLD = 0.8   # For language survey confidence
LID_MIN_SPEECH_DURATION_MS = 100 # Minimum speech duration for LID segments

# Speaker Diarization (Phase 2)
DIARIZATION_MODEL = "pyannote/speaker-diarization-3.1"
DIARIZATION_DEVICE = "cpu"       # "cpu" or "cuda"
DIARIZATION_MIN_SPEAKERS = 2     # Lower bound for sales calls
DIARIZATION_MAX_SPEAKERS = 10    # Upper bound
DIARIZATION_CLUSTERING_THRESHOLD = 0.0  # 0.0 = use pyannote default (0.7046)
DIARIZATION_MIN_CLUSTER_SIZE = 0        # 0 = use pyannote default (12)
HF_AUTH_TOKEN = ""               # HuggingFace token (required, gated model)

# Speaker Embedding Verification (post-diarization cleanup)
DIARIZATION_VERIFY_SPEAKERS = false     # Enable/disable verification
DIARIZATION_EMBEDDING_MODEL = "pyannote/wespeaker-voxceleb-resnet34-LM"
DIARIZATION_MERGE_THRESHOLD = 0.75      # Cosine similarity above this → merge clusters
DIARIZATION_OUTLIER_THRESHOLD = 0.55    # Below this → segment is outlier in its cluster
DIARIZATION_REASSIGN_THRESHOLD = 0.65   # Must exceed this to reassign to new cluster
DIARIZATION_REASSIGN_MARGIN = 0.10      # New cluster must beat old by this margin
DIARIZATION_MIN_EMBED_DURATION = 1.5    # Segments shorter than this inherit from nearest

# Resemble Enhance Microservice
RESEMBLE_ENHANCE_URL = "http://localhost:8001"  # Microservice URL

# Timeouts
FFMPEG_TIMEOUT = 18000           # 5 hours
SUBPROCESS_TIMEOUT = 18000       # 5 hours

# Temp directory
TEMP_DIR = "/tmp/audio_preprocess"
```

---

## API Endpoints

### Audio Preprocessing (Phase 1)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/audio-preprocess/` | Serve the A/B comparison UI |
| POST | `/audio-preprocess/process` | Process audio through pipeline |
| GET | `/audio-preprocess/audio/{request_id}/{filename}` | Serve processed audio for playback |
| GET | `/audio-preprocess/fonts/{filename}` | Serve bundled font files for PDF |
| DELETE | `/audio-preprocess/cleanup/{request_id}` | Clean up temp files |

**POST /audio-preprocess/process** accepts multipart form:
- `file`: Audio file (required)
- `processing_mode`: `"path_a"` | `"path_b"` | `"both"` (default: `"both"`)
- `language`: ISO 639-1 code or `"auto"` (default: `"auto"`)
- `engine`: `"faster-whisper"` | `"openai-whisper"` (default: `"faster-whisper"`)
- `model_size`: `"tiny"` | `"base"` | `"small"` | `"medium"` | `"large-v3"` (default: `"medium"`)
- `multilingual`: `"true"` | `"false"` (default: `"false"`)
- `diarization`: `"true"` | `"false"` (default: `"false"`) — Phase 2: enable speaker identification
- `num_speakers`: `"auto"` | `"2"` | `"3"` | `"4"` | `"5"` (default: `"auto"`) — expected speaker count
- `align`: `"true"` | `"false"` (default: `"false"`) — Phase 3: enable word-level forced alignment

### Original Transcription Service

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Original transcription UI |
| GET | `/health` | Health check |
| POST | `/transcribe` | Transcribe via file upload, media URL, or YouTube URL |

---

## UI Features

- **Dark theme** with drag-and-drop file upload
- **Engine dropdown**: faster-whisper (default) / openai-whisper
- **Model size dropdown**: tiny / base / small / medium (default) / large-v3
- **Path selection** radio buttons: Path A (recommended, default), Path B, Both (with speed warning)
- **Language dropdown**: Auto-detect (default) + 17 languages (en, ta, hi, te, kn, ml, mr, bn, gu, pa, ur, fr, de, es, ja, zh, ar)
- **Multilingual Mode checkbox**: enabled only when language is Auto-detect, disabled otherwise via JS event listener
- **Speaker Diarization checkbox**: enables Pyannote speaker identification (Phase 2)
- **Expected Speakers dropdown**: Auto-detect / 2 / 3 / 4 / 5+ (shown only when diarization enabled)
- **Progress bar** with percentage and step names showing engine + model
- **Input Analysis table** with metric definitions
- **Processing Steps table** with Step / Status (Applied|Skipped) / Reason
- **Results table**: single-column for one path, side-by-side for both
- **Segment Timestamps table** with color-coded quality metrics; "Lang" column shown when multilingual mode produces `detected_language` per segment; color-coded "Speaker" column shown when diarization enabled
- **Word-Level Alignment checkbox**: enables ctc-forced-aligner (Phase 3)
- **Expandable word timestamps**: click segment row to show per-word timing with confidence scores
- **Original Audio Timestamps table**: separate table showing timestamps remapped to pre-VAD-trimming positions, with expandable word sub-rows
- **Diarization summary** in results section: number of speakers detected, method used, processing time
- **PDF download** button (jsPDF + autoTable) with Tamil/Devanagari font support, includes speaker labels, diarization summary, word timestamps, and original audio timestamps
- **5-hour fetch timeout** via AbortController

---

## PDF Export with Unicode Support

### Architecture

The UI generates PDF reports using jsPDF + autoTable (loaded from cdnjs CDN). Custom fonts needed for Tamil/Hindi/Devanagari text.

### Self-Hosted Fonts

```
/audio-preprocess/fonts/NotoSansTamil-Regular.ttf      → 78KB (Latin + Tamil)
/audio-preprocess/fonts/NotoSansDevanagari-Regular.ttf  → 219KB (Latin + Devanagari)
```

Full (non-subsetted) Noto Sans fonts from Google Fonts. Include both Latin AND Indic scripts for mixed-language text.

**Important:** Subsetted fonts (from Fontsource CDN) only contain ~83 Indic codepoints with zero Latin letters. Mixed text loses all English characters. Always use full fonts.

### Font Loading Flow

1. `downloadPDF()` creates jsPDF document
2. `registerFonts(doc)` fetches both TTF files from `/audio-preprocess/fonts/...`
3. Each font converted to base64, registered via `addFileToVFS()` + `addFont()`
4. `setFontForText(doc, text)` detects Tamil/Devanagari Unicode ranges and switches font
5. `indicFontHook(data)` does per-cell font switching in autoTable
6. Graceful fallback to Helvetica if font loading fails

---

## File-by-File Implementation Details

### `app/__init__.py`
OpenMP fix for macOS. Sets `KMP_DUPLICATE_LIB_OK=TRUE` and imports `ctranslate2` before any other module can import torch. Prevents segfaults with faster-whisper INT8.

### `app/audio_preprocess/config.py`
Single `PreprocessConfig` class with all settings including multilingual config (`LID_CONFIDENCE_THRESHOLD`, `LID_MIN_SPEECH_DURATION_MS`). Instantiated as `preprocess_config` module-level singleton. All values configurable via environment variables.

### `app/audio_preprocess/router.py`
FastAPI APIRouter with prefix `/audio-preprocess`. Endpoints:
- `GET /` — serves HTML UI from `ui.py`
- `POST /process` — accepts file + form params (processing_mode, language, engine, model_size, multilingual, diarization, num_speakers), runs pipeline, returns JSON
- `GET /audio/{request_id}/{filename}` — serves processed audio WAV files for playback
- `GET /fonts/{filename}` — serves bundled TTF font files (whitelisted to 2 files)
- `DELETE /cleanup/{request_id}` — cleans up temp files

The `multilingual` form parameter is parsed from string to boolean, auto-disabled if language != "auto". The `diarization` and `num_speakers` params are parsed similarly (Phase 2).

### `app/audio_preprocess/services/analyzer.py`
`AudioAnalysis` dataclass with metrics. `analyze_audio()` reads raw audio and computes SNR (energy-based with CV check), clipping percentage, sample rate. Sets three boolean flags: `needs_bandwidth_extension`, `needs_declipping`, `needs_denoising`. `estimate_snr()` uses frame energy distribution for speech-like signals and RMS-based estimate for steady signals. `to_dict()` converts numpy types to native Python for JSON serialization.

### `app/audio_preprocess/services/core_processing.py`
Four always-on steps:
1. `resample_to_16k()` — FFmpeg async subprocess, skips if already 16kHz mono
2. `apply_highpass()` — 80Hz highpass via FFmpeg, no lowpass
3. `trim_silence_vad()` — Silero VAD singleton, concatenates speech segments with 200ms padding. **Returns `(Path, vad_boundaries)` tuple** where boundaries are `[{start, end}, ...]` in seconds for timestamp remapping.
4. `apply_loudnorm_dual_pass()` — two FFmpeg passes: measure then apply. Falls back to single-pass if measure fails.

### `app/audio_preprocess/services/conditional.py`
Three conditional steps:
1. `denoise_with_oa()` — DeepFilterNet3 singleton, enhances at 48kHz, resamples back, blends 70/30
2. `apply_bandwidth_extension()` — FFmpeg soxr resampler for ≤8kHz audio
3. `apply_declipping()` — NumPy tanh soft saturation

### `app/audio_preprocess/services/enhance_playback.py`
`apply_resemble_enhance()` — calls the resemble-enhance microservice via HTTP. Falls back to file copy if microservice is unavailable. `check_enhance_available()` pings microservice health endpoint. Path B only.

### `app/audio_preprocess/services/aligner.py` (Phase 3)
Word-level forced alignment using ctc-forced-aligner ONNX model. Key components:
- **Singleton loading** — `_load_alignment_model()` returns `(onnx_session, tokenizer)` with thread lock
- **`LANG_MAP_1_TO_3`** — ISO 639-1 (Whisper) → ISO 639-3 (MMS) mapping for 27 languages
- **`_detect_script_lang(text)`** — Unicode script detection via `unicodedata.name()`, maps 15+ scripts to ISO 639-3
- **`_clean_text_for_alignment()`** — removes numbers/symbols that crash ctc-forced-aligner assertions
- **`_filter_empty_tokens()`** — safety net for empty/bad tokens after preprocessing
- **`_normalize_score()`** — converts cumulative log-probs to 0-1 per-frame probability
- **`align_segments(audio_path, segments)`** — main function: per-segment alignment with auto language detection, script override for misidentified text, word timestamp offsetting

### `app/audio_preprocess/services/transcribe.py`
Dual-engine ASR with smart chunking, multilingual mode, hallucination filtering, and tail re-transcription. Largest file (~880 lines).

**Model loading:** Three singletons with locks — openai-whisper, faster-whisper, Silero VAD (shared with chunking).

**Engine-specific functions:**
- `_get_options_ow/fw(language)` — parameter dicts
- `_transcribe_chunk_ow/fw(model, path, options)` — single chunk transcription
- `_detect_language_ow/fw(model, audio_path)` — LID via Whisper's detect_language()

**Shared functions (engine-agnostic):**
- `_load_vad()` — Silero VAD singleton
- `_get_speech_chunks()` — VAD boundary detection + grouping into ≤28s chunks + **chunk edge extension** (extends first/last chunk to cover full audio)
- `_find_lowest_energy_point()` — finds breath pauses for fallback splitting
- `_split_oversized_chunk()` — recursive splitting at low-energy points
- **`_retranscribe_tail()`** — detects when Whisper stops early within a chunk (gap > 1.5s), re-transcribes uncovered tail with `no_speech_threshold=None`, filters hallucinations, keeps segments extending past overlap zone
- `_transcribe_chunked()` — chunk loop with context carry-over, hallucination filtering, **tail re-transcription**, timestamp remapping
- `_transcribe_multilingual()` — chunk-level dual-pass with **tail re-transcription**: try each candidate language per chunk, pick best by logprob

**Multilingual functions:**
- `_survey_languages()` — N × 15s evenly-spaced LID samples (N auto-scaled: <5min→5, 5-30min→10, >30min→15)
- `_transcribe_multilingual()` — chunk-level dual-pass: try each candidate language per chunk, pick best by logprob

**Hallucination functions:**
- `_is_hallucination()` — checks compression_ratio > 2.4, avg_logprob < -1.0, n-gram repetition
- `_filter_hallucinations()` — removes hallucinated segments with logging

**Main `transcribe()` function:** Routes to multilingual branch (if enabled + auto-detect) → standard short path (≤30s) → chunked path (>30s). All paths apply hallucination filtering + tail re-transcription.

### `app/audio_preprocess/services/diarize.py` (Phase 2)
Speaker diarization service (~776 lines). Key components:
- **Singleton model loading** — `_load_diarization_pipeline()` with thread-safe double-check pattern
- **`_detect_multichannel()`** — checks stereo cross-correlation < 0.9 for channel-based separation
- **`_diarize_multichannel()`** — energy-based segmentation per channel
- **`diarize_audio(audio_path, config, num_speakers)`** — main function, returns `{speakers, num_speakers, method, verification}`
- **`assign_speakers_to_segments(diarization_result, whisper_segments)`** — temporal overlap algorithm with segment splitting
- **Speaker Embedding Verification** (post-diarization cleanup, optional):
  - `_load_embedding_model()` — singleton for `pyannote/wespeaker-voxceleb-resnet34-LM`
  - `_extract_cluster_embeddings()` — per-segment embeddings via `inference.crop()`, duration-weighted L2-normalized centroids
  - `_merge_similar_clusters()` — greedy pairwise cosine similarity merging above threshold
  - `_reassign_outlier_segments()` — reassign low-similarity segments + short segment inheritance from nearest neighbor
  - `verify_speaker_clusters()` — orchestrator: extract → merge → reassign → relabel sequentially

### `app/audio_preprocess/services/pipeline.py`
`PipelineResult` class with fields for both paths + diarization + vad_boundaries. `run_pipeline()` orchestrator that:
1. Analyzes raw audio
2. Runs core processing (resample → highpass → VAD trim **→ saves vad_boundaries** → loudnorm)
3. Runs conditional processing (bandwidth ext, declipping, denoising)
4. Adds multilingual step to processing steps log
5. **Step 3.5: Speaker diarization** (if enabled) — runs Pyannote on processed audio
6. Path A: copies processed audio, runs transcription, assigns speakers, **runs alignment (if enabled)**, **remaps timestamps to original audio**
7. Path B: runs Resemble Enhance, resamples to 16kHz, runs transcription, assigns speakers, **runs alignment**, **remaps timestamps**
8. Returns `PipelineResult` with `to_dict()` for JSON serialization (includes diarization + vad_boundaries)

**VAD remapping functions:**
- `_remap_to_original_time(trimmed_time, vad_boundaries)` — walks VAD segments to map trimmed-audio time → original-audio time
- `_remap_segments(segments, vad_boundaries)` — adds `original_start`/`original_end` to each segment and word

### `app/audio_preprocess/ui.py`
~1100 lines of inline HTML/CSS/JS. Dark theme with:
- Drag-and-drop upload area
- Engine + model size + language + path selection dropdowns
- Multilingual Mode checkbox (auto-disabled when language != Auto-detect)
- FormData construction with all parameters including `multilingual`
- 5-hour AbortController timeout
- Progress bar with step names
- Results rendering (single or side-by-side)
- Segment timestamps table with color-coded metrics and optional "Lang" column for multilingual
- `downloadPDF()` with Unicode font support via self-hosted Noto Sans fonts
- Script detection (`hasTamil()`, `hasDevanagari()`) for per-cell font switching

---

## Dependencies

```
# Web framework
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
python-multipart>=0.0.6

# HTTP client
httpx>=0.25.0

# ASR engines
openai-whisper>=20231117           # PyTorch-based Whisper
faster-whisper>=1.0.0              # CTranslate2-based Whisper (3x faster)

# ASR alternatives (original routes)
pingala-shunya>=0.1.0
aksharamukha>=2.0

# Audio preprocessing
noisereduce>=3.0
deepfilternet>=0.5
# resemble-enhance moved to top-level resemble_enhance/ (isolated torch 2.1.1)

# Speaker diarization (Phase 2)
pyannote.audio>=3.1,<4.0          # Works with torch 2.1.1 (3.1.1 verified)

# Word-level forced alignment (Phase 3)
ctc-forced-aligner>=0.3.0         # ONNX-based MMS model, 1130 languages
unidecode>=1.3.0                   # Required by ctc-forced-aligner for non-Latin romanization

# Core
numpy<2                            # Pinned for Whisper compatibility
soundfile                          # Audio I/O
torch                              # ML framework (pinned to 2.1.1 by resemble-enhance)

# YouTube
yt-dlp>=2023.11.0

# Testing
pytest>=7.4.0
pytest-asyncio>=0.21.0
pydantic>=2.5.0
```

---

## Bugs Fixed During Implementation

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| Silero VAD crash: "only supports 8000 and 16000 sample rates" | Pipeline ran highpass at original sample rate then passed to VAD | Resample to 16kHz FIRST, before highpass and VAD |
| `'PreprocessConfig' has no attribute 'RESEMBLE_ENHANCE_DEVICE'` | Field missing when config was recreated | Added the field to config.py |
| `Object of type bool_ is not JSON serializable` | `AudioAnalysis.to_dict()` returned numpy types | Added type conversion for `np.bool_`, `np.integer`, `np.floating` |
| SNR estimator false negative on clean sine wave | Energy-based method assumes speech-like energy variation | Added coefficient of variation check — if CV < 0.3, use RMS-based estimate |
| `git: 'lfs' is not a git command` | git-lfs not installed | Installed git-lfs via homebrew |
| `asyncio.new_event_loop()` inside sync function called from async | `apply_bandwidth_extension()` used async loop in sync context | Replaced with `subprocess.run()` |
| Smart chunking produces 1 chunk for entire 43-min audio | After VAD silence trimming, all pauses removed | Added energy-based fallback splitting |
| Whisper detects Tamil for entire mixed English+Tamil audio | `language="en"` was hardcoded; auto-detect picks one language for whole file | Changed to `language=None` (auto-detect), added UI dropdown, smart chunking gives per-chunk detection |
| faster-whisper segfault (exit code 139) | torch + ctranslate2 OpenMP library conflict on macOS | Set `KMP_DUPLICATE_LIB_OK=TRUE` + import ctranslate2 before torch in `app/__init__.py` |
| INT8 segfault specifically | Quantized compute types crash when torch loads OpenMP first | Same OpenMP fix — must be in `app/__init__.py` |
| `transformers 4.57.6` incompatible with `torch 2.1.1` | AttributeError `register_pytree_node` | Downgraded: `pip install "transformers>=4.30,<4.46"` |
| torch upgrade broke resemble-enhance | `resemble-enhance 0.0.1` pins `torch==2.1.1` | Reverted to torch 2.1.1 |
| PDF Tamil characters missing | jsPDF default Helvetica doesn't support Tamil Unicode | Added Noto Sans Tamil/Devanagari font loading |
| PDF font CDN 403 error | jsDelivr GitHub proxy blocking font downloads | Switched to self-hosted fonts via FastAPI endpoint |
| PDF Tamil font missing Latin glyphs | Fontsource CDN serves subsetted fonts (Tamil-only, 83 codepoints) | Used full Google Fonts TTF files (428+ codepoints, Latin + Indic) |
| Multilingual v1: Korean/Hindi detected on Tamil audio | 1-5s micro-segments too short for reliable Whisper LID | Rewrote to use 15s survey segments |
| Multilingual v1: initial prompt leaked 3× into output | English-biased prompt echoed by forced non-English transcription on short segments | Removed initial_prompt for forced-language passes; use previous chunk context only |
| Multilingual v1: massive "ya ya ya" hallucination loops | Short segments destroyed Whisper's context window | Rewrote to use existing 28s smart chunks |
| Hallucination filter: 43 false positives on real speech | `no_speech_prob > 0.5` too aggressive — conversational audio has high nsp on legitimate speech | Removed nsp as standalone trigger; raised thresholds to match Whisper defaults (cr>2.4, lp<-1.0) |
| Hallucination filter only ran in multilingual path | `_filter_hallucinations()` was only called in `_transcribe_multilingual()` | Added filtering to `_transcribe_chunked()` and single-pass path |
| `transcribe()` calling deleted functions after rewrite | Multilingual branch still referenced `_get_micro_segments`, `_run_lid_on_segments` | Updated to call `_survey_languages()` + `_transcribe_multilingual()` with new signatures |
| Pipeline step text described old approach | Still said "1-5s micro-segments" after rewrite | Updated to describe "language survey + chunk-level dual-pass + hallucination filtering" |
| **Phase 3 bugs** | | |
| `No module named 'unidecode'` — alignment fails for all Tamil segments | ctc-forced-aligner needs `unidecode` for non-Latin romanization, wasn't in requirements.txt | `pip install unidecode` + added to requirements.txt |
| 4 Tamil segments missing word timestamps | Whisper detected Tamil text as `lang=en`, Latin whitelist `[a-z']` stripped all Tamil characters → empty → skipped | Added `_detect_script_lang()` using `unicodedata.name()` to override language when non-Latin chars detected |
| Pipeline import crash after aligner rewrite | `pipeline.py` imported removed functions `align_words` + `assign_words_to_segments` | Changed import to `align_segments`, rewrote Step 4.5a/4.5b |
| PDF missing word timestamps in Original Audio Timestamps | Word sub-rows not included in PDF autoTable for original timestamps section | Added word sub-rows with `w.original_start`/`w.original_end` to PDF |
| Whisper chunk gap — last ~5s of speech not transcribed | Chunking VAD's speech timestamps don't always extend to audio edges; Whisper stops early due to 30s window zero-padding | Chunk edge extension (extend first/last chunk to cover full audio) + tail re-transcription (`_retranscribe_tail`) |
| Tail re-transcription filtering too aggressive | Overlap filter checked `seg["start"] >= boundary` — discarded segments starting in overlap zone even if they contained new content | Changed to `seg["end"] > boundary` — keeps segments extending past overlap |
| Tail fix only in `_transcribe_chunked`, not multilingual | `_transcribe_multilingual` had same chunk gap issue but no tail fix | Added `_retranscribe_tail` call to multilingual path too |

---

## Key Design Decisions

1. **Detection-first preprocessing** — measure audio properties, only process what's needed. Less processing = better ASR.

2. **Preprocessing is engine-agnostic** — resample, highpass, VAD trimming, loudnorm, conditional processing all run identically regardless of ASR engine.

3. **No redundant VAD** — preprocessing VAD trims silence. Smart chunking reuses the VAD singleton. faster-whisper's `vad_filter=False`.

4. **Smart chunking with energy fallback** — VAD boundaries for natural splits, energy-based for when VAD sees continuous speech post-trimming.

5. **Hallucination filtering on all paths** — not just multilingual. Uses Whisper's own thresholds (cr>2.4, lp<-1.0) plus n-gram repetition detection. No no_speech_prob trigger.

6. **Multilingual via survey + dual-pass** — long LID samples (15s) for reliability, existing 28s chunks for transcription quality, per-chunk language competition by logprob.

7. **Resemble Enhance is Path B only** — generative model that can fabricate phonemes. Safe for human listening, dangerous for ASR.

8. **Self-hosted fonts** — no CDN dependency for PDF generation. Full fonts with Latin + Indic glyphs.

9. **Model singletons** — all ML models (Whisper, VAD, DeepFilterNet) loaded once, cached with thread locks, reused across requests. Size changes trigger reload.

10. **5-hour timeouts** — both server (FFmpeg, subprocess) and client (AbortController) to handle long audio on CPU.

---

## Phase 2: Speaker Diarization — "Who is Who?" (IMPLEMENTED)

> Full research with beginner-friendly explanations: see `DIARIZATION_RESEARCH.md`

### What Phase 2 Adds

Phase 1 output: "Here's what was said"
Phase 2 output: "Here's what was said **and who said it**"

This is the foundation for Phase 7 (intelligence layer) — you need speaker attribution to extract "what did the customer object to?" vs "what did the sales rep promise?"

### Model Choice: `pyannote/speaker-diarization-3.1`

Pyannote speaker-diarization-3.1 (pyannote.audio 3.1.1) was selected because pyannote.audio 4.x (community-1) requires torch>=2.8.0 which is incompatible with our torch 2.1.1 constraint. pyannote.audio 3.1.1 works with torch 2.1.1.

| Factor | Detail |
|--------|--------|
| **Model** | `pyannote/speaker-diarization-3.1` |
| **Library** | `pyannote.audio==3.1.1` (compatible with torch 2.1.1) |
| **Architecture** | Segmentation → Embedding → Agglomerative Clustering |
| **Speaker count** | Auto-detected via min_speakers/max_speakers constraints |
| **License** | Gated model — requires HuggingFace token + license acceptance |
| **CPU viable** | Yes but slow |
| **HuggingFace models to accept** | `pyannote/speaker-diarization-3.1`, `pyannote/segmentation-3.0`, `pyannote/embedding` |

### Implementation Details

**File:** `app/audio_preprocess/services/diarize.py` (~776 lines)

**Key components:**
1. **Singleton model loading** — thread-safe double-check pattern (same as VAD/Whisper singletons)
2. **Multichannel detection** — checks if stereo audio has different content per channel (cross-correlation < 0.9). If so, uses channel-based separation instead of model diarization
3. **`diarize_audio()`** — main function: multichannel check → pyannote diarization → optional embedding verification → structured result
4. **`assign_speakers_to_segments()`** — splits Whisper segments at speaker-change boundaries (word-level if timestamps available, proportional otherwise)
5. **Speaker Embedding Verification** (optional, `DIARIZATION_VERIFY_SPEAKERS=true`):
   - Uses `pyannote/wespeaker-voxceleb-resnet34-LM` to extract per-segment speaker embeddings
   - Computes duration-weighted L2-normalized centroids per cluster
   - Greedy pairwise merging: finds highest cosine similarity pair above `MERGE_THRESHOLD`, merges smaller cluster into larger, repeats
   - Outlier reassignment: segments with low own-cluster similarity reassigned if better match found (must exceed `REASSIGN_THRESHOLD` and beat old by `REASSIGN_MARGIN`)
   - Short segment inheritance: segments below `MIN_EMBED_DURATION` inherit speaker from temporally nearest embedded segment
   - Returns verification stats: original_clusters, final_clusters, merged_clusters, segments_reassigned, short_segments_inherited, processing_time_seconds

### Pipeline Integration

Diarization runs as **Step 3.5** — after conditional processing, before ASR:

```
[3. Conditional Processing] → [3.5. Speaker Diarization (if enabled)] → [4. ASR]
```

**After ASR**, speaker labels are assigned to Whisper segments via temporal overlap alignment. This approach preserves ALL existing transcription quality — the Whisper pipeline is completely unchanged.

### UI Controls

- **"Speaker Diarization" checkbox** — toggles diarization on/off (off = zero changes to Phase 1 flow)
- **"Expected Speakers" dropdown** — Auto-detect (recommended), 2, 3, 4, 5+ speakers (hidden until checkbox checked)
- **Color-coded speaker column** in segments table (SPEAKER_00 = blue, SPEAKER_01 = green, etc.)
- **Diarization summary** — number of speakers, method, processing time
- **PDF export** includes speaker labels and diarization summary

### Response Format

```json
{
  "diarization": {
    "enabled": true,
    "num_speakers": 2,
    "method": "pyannote",
    "speakers": [
      {"speaker": "SPEAKER_00", "start": 0.0, "end": 5.2},
      {"speaker": "SPEAKER_01", "start": 5.2, "end": 12.8}
    ],
    "processing_time_seconds": 45.3,
    "verification": {
      "original_clusters": 4,
      "final_clusters": 2,
      "merged_clusters": 2,
      "segments_reassigned": 3,
      "short_segments_inherited": 5,
      "processing_time_seconds": 12.1
    }
  },
  "path_a": {
    "segments": [
      {"id": 0, "start": 0.0, "end": 5.2, "text": "...", "speaker": "SPEAKER_00"},
      {"id": 1, "start": 5.2, "end": 12.8, "text": "...", "speaker": "SPEAKER_01"}
    ]
  }
}
```

### What Competitors Do (Research Summary)

| Competitor | Approach | Key Takeaway for Us |
|-----------|----------|---------------------|
| **Gong.io** | Audio diarization + voice enrollment (5 samples) + CRM metadata + video analysis (patented) | Gold standard for sales — voice enrollment identifies rep vs customer. Plan for Phase 5 |
| **Rev.ai** | Open-sourced "Reverb" — pyannote fine-tuned on 26K hours of human-labeled data | Domain-specific fine-tuning on pyannote gives 22% WDER improvement |
| **Deepgram** | Cascade with embeddings trained on 100K+ speakers. Per-word attribution | Multichannel audio is the "cheat code" — separate channels = near-perfect results |
| **AssemblyAI** | Custom embeddings at 16kHz. Advanced augmentation for failure modes | 85% reduction in speaker count errors via better embedding training |

---

## Phase 3: World-Class ASR (IMPLEMENTING — core features working)

> Full research: see `ASR_RESEARCH.md` and `PHASE3_RESEARCH.md` (beginner-friendly)

### What's Implemented (all tested and working)

1. **ctc-forced-aligner** — word-level timestamps via ONNX-based MMS model (per-segment, multilingual, script auto-detection)
2. **VAD timestamp remapping** — all timestamps mapped back to original audio time (UI table + PDF)
3. **Tail re-transcription** — recovers speech Whisper misses at chunk boundaries (both chunked + multilingual paths)
4. **Chunk edge extension** — ensures chunks cover full audio (extends first/last chunk to audio edges)
5. **Multilingual language survey** — split audio into N survey chunks, detect languages present → build language array. Per-chunk transcription tries each detected language, picks best by logprob. (Moved from Phase 4 into Phase 3)
6. **Translate to English** — IndicTrans2 for Indian languages (text-to-text, 15/15 Tamil accuracy), Whisper translate for other languages, with UI + PDF export
7. **IndicTrans2 integration** — script-based routing via Unicode ranges (not Whisper's detected language), singleton model loading, batch translation per language

### What's Pending

- **Language survey chunk count**: hardcoded at 5 (auto-scaling by duration deferred — revisit if needed: <5min→5, 5-30min→10, >30min→15)
- **Model upgrade: medium → large-v3-turbo** (809M params, ~7.0% WER)

### 3a. Word-Level Forced Alignment (`services/aligner.py`)

**Tool:** ctc-forced-aligner v0.3+ with ONNX inference (no torch dependency for alignment itself).

**How it works:**
1. Loads ONNX MMS model once (singleton with thread lock, ~50MB downloaded on first use)
2. For each ASR segment independently:
   - Detects the segment's language (from Whisper's `detected_language`)
   - Auto-detects script via `unicodedata.name()` when Whisper misidentifies (e.g., Tamil text labeled as English)
   - Pre-cleans text: removes numbers/symbols that crash ctc-forced-aligner assertions
   - Extracts the segment's audio portion from the full processed audio
   - Runs CTC forced alignment: `preprocess_text` → `generate_emissions` → `get_alignments` → `get_spans` → `postprocess_results`
   - Offsets word timestamps back to full-audio time
3. Normalizes scores from cumulative log-probs to 0-1 per-frame probability

**Script detection:** Uses `unicodedata.name(ch)` to extract script name from first non-Latin character. Maps 15+ scripts: Tamil, Devanagari, Bengali, Gujarati, Gurmukhi, Telugu, Kannada, Malayalam, Arabic, Thai, CJK, Hangul, Hiragana, Katakana, Cyrillic. Overrides Whisper's language when non-Latin text is detected but Whisper reported a Latin-script language.

**Dependencies:** `ctc-forced-aligner>=0.3.0`, `unidecode>=1.3.0` (for non-Latin romanization), `onnxruntime`

**Word format:**
```json
{"word": "hello", "start": 1.23, "end": 1.45, "score": 0.92}
```

### 3b. VAD Timestamp Remapping

**Problem:** After VAD silence trimming, all timestamps are relative to the trimmed audio. If 30s of silence was removed, everything after that is off by 30s compared to the original file.

**Solution:** Save VAD speech boundaries during trimming, remap all timestamps back.

**Changes:**
- `core_processing.py`: `trim_silence_vad()` returns `(Path, vad_boundaries)` where boundaries are `[{start, end}, ...]` in seconds relative to original audio
- `pipeline.py`: New functions `_remap_to_original_time()` and `_remap_segments()`:
  - Walk through VAD segments accumulating trimmed durations
  - Find which segment a trimmed timestamp falls in
  - Compute offset within that segment → original time
  - Adds `original_start`/`original_end` to each segment and word
- `ui.py`: Separate "Original Audio Timestamps" table with expandable word sub-rows, plus PDF section

**Segment format after remapping:**
```json
{
  "start": 5.0, "end": 8.2,
  "original_start": 10.3, "original_end": 13.5,
  "text": "the meeting is at three",
  "words": [
    {"word": "the", "start": 5.1, "end": 5.25, "original_start": 10.4, "original_end": 10.55, "score": 0.95}
  ]
}
```

### 3c. Tail Re-Transcription (`_retranscribe_tail()`)

**Problem:** Whisper processes audio in 30s windows. When a chunk is shorter (e.g., 15s), it gets zero-padded to 30s. The model's attention sees upcoming silence and may stop generating text early, leaving speech untranscribed at chunk ends.

**Solution:** After each chunk is transcribed, check if the last segment's end is significantly before the chunk's end (gap > 1.5s). If so:
1. Extract tail audio with 1s overlap for decoder context
2. Re-transcribe with `no_speech_threshold=None` (prevents the same premature-stop issue)
3. Filter hallucinations on tail segments
4. Keep only segments extending past the overlap zone (`seg["end"] > overlap_boundary`)
5. Append recovered segments to the chunk

**Constants:** `TAIL_MIN_GAP_SECONDS = 1.5`, `TAIL_OVERLAP_SECONDS = 1.0`

**Integrated into both** `_transcribe_chunked()` and `_transcribe_multilingual()`.

### 3d. Chunk Edge Extension

**Problem:** Smart chunking's second VAD pass may not start at sample 0 or end at the audio's last sample. The edges are excluded from all chunks → never transcribed.

**Solution:** In `_get_speech_chunks()`, after building chunks:
- If first chunk starts after 0 → extend to 0
- If last chunk ends before audio end (gap > 100ms) → extend to audio end

Safe because the input is already VAD-trimmed (only contains speech).

### Pipeline Flow (Current)

```
Input audio
    │
    ▼
[1. Analysis] → [2. Core Processing] → [3. Conditional Processing]
    │                                          │
    ▼                                          ▼
[2c. VAD trimming] ──→ vad_boundaries    [3.5. Diarization (if enabled)]
    │                       │                  │
    ▼                       │                  ▼
[4. ASR: Whisper] ←─────────┘            [Speaker alignment]
    │   (multilingual: language survey → per-chunk language-aware transcription)
    │
    ▼
[4.5. Alignment: ctc-forced-aligner (if enabled)]
    │
    ▼
[5. VAD Remapping] ── adds original_start/original_end using vad_boundaries
    │
    ▼
[6. Translate to English (if checkbox enabled + non-English detected)]
    │   Uses Whisper built-in task="translate" — zero new deps
    │
    ▼
Results: text + segments + words + original timestamps + speaker labels + translation
```

### 3e. Multilingual Language Survey + Language-Aware Transcription (moved from Phase 4)

**Problem:** Sales calls mix English + Tamil/Hindi. Standard Whisper detects one language for the entire file. Even with smart chunking, if a chunk has mixed languages the minority language is lost.

**Solution (already implemented in multilingual mode):**
1. **Language survey** — split audio into N evenly-spaced survey chunks (currently hardcoded at 5), run Whisper's `detect_language()` on each → build array of languages present in the audio
2. **Per-chunk language-aware transcription** — for each transcribed chunk, try each detected language, pick the one with best `avg_logprob` (Whisper's confidence)
3. Each segment tagged with `detected_language`

**Pending decisions:**
- **Survey chunk count**: hardcoded at 5 (auto-scaling deferred — revisit if needed: <5min→5, 5-30min→10, >30min→15)

### 3f. Translate to English (IMPLEMENTED)

**Problem:** When non-English segments are detected, users need an English translation to understand the content. Whisper's built-in `task="translate"` hallucinates for Tamil (~2/8 segments correct), though it works fine for high-resource languages (French, German, Spanish).

**Solution:** Hybrid approach with script-based routing:
- **Indian languages** (14 supported): IndicTrans2 text-to-text translation (15/15 Tamil accuracy). Original-language word timestamps from ASR are preserved.
- **Non-Indian languages**: Whisper translate mode with word-level timestamps.
- **Routing**: Based on actual text script (Unicode ranges), NOT Whisper's detected language. This handles cases like Whisper detecting "en" for Tamil text or "ta" for English text.

**How it works:**
1. All segments are tagged with `detected_language` (in all 3 paths: short audio, chunked, multilingual)
2. After ASR completes, `_translate_segments()` iterates over segments
3. `_is_text_actually_english()` checks script — Latin text is skipped regardless of detected language
4. `_detect_indic_lang_from_text()` checks Unicode ranges to identify Indian scripts:
   - Tamil (U+0B80–0BFF), Devanagari (U+0900–097F), Bengali (U+0980–09FF), Gujarati, Kannada, Malayalam, Gurmukhi, Telugu, Odia, Sinhala, Arabic/Urdu — 11 script ranges covering 14 languages
5. **Indian language segments**: grouped by script → batch translated via `_translate_text_indictrans2()` → sets `seg["translation"]`. Word timestamps come from original ASR `seg["words"]`.
6. **Non-Indian segments**: Whisper translate per segment (audio extraction → temp WAV → `task="translate"` + `word_timestamps=True`) → sets `seg["translation"]` + `seg["translation_words"]`
7. In `pipeline.py`, `_remap_segments()` applies VAD remapping to `translation_words` (adds `original_start`/`original_end`)

**IndicTrans2 details:**
- Model: `ai4bharat/indictrans2-indic-en-dist-200M` (211M params, MIT license)
- Package: `indictranstoolkit 1.1.1` (already installed)
- Singleton loading with thread lock (double-checked locking pattern)
- Batch translation per language for efficiency (~400ms/sentence batch vs ~1s/sentence individual)
- Memory: ~808MB
- 14 Indian languages: Tamil, Hindi, Bengali, Gujarati, Kannada, Malayalam, Marathi, Punjabi, Telugu, Urdu, Odia, Assamese, Nepali, Sinhala
- Fallback: if IndicTrans2 fails, segments fall through to Whisper translate

**UI:**
- "Translate to English" checkbox (always visible, below alignment section)
- Description: "Indian languages via IndicTrans2, other languages via Whisper translate"
- "Translation (English)" column in segment timestamps table (blue text `#a8d5ff`)
- "Translation Words (English)" expandable sub-rows for non-Indian segments
- Translation column + translation word sub-rows in Original Audio Timestamps table
- PDF export includes all translation columns + translation word sections

**Files changed:**
- `transcribe.py`: `INDIC_LANG_MAP`, `_SCRIPT_TO_INDIC`, `_detect_indic_lang_from_text()`, `_get_indictrans2()`, `_translate_text_indictrans2()`, `_translate_segment_fw()`, `_translate_segment_ow()`, `_translate_segments()`, `translate_to_english` param
- `config.py`: `INDICTRANS2_MODEL`, `INDICTRANS2_DEVICE`
- `pipeline.py`: `translation_words` VAD remapping, `translate_to_english` param passthrough, updated step description
- `router.py`: `translate_to_english` Form param
- `ui.py`: Checkbox, FormData, progress steps, segment tables, original timestamps table, PDF export, updated description text

### Compatibility Table

| Tool | torch 2.1.1 Compatible? | Status |
|------|------------------------|--------|
| faster-whisper | Yes (already installed) | **In use** |
| Whisper large-v3-turbo | Yes (via faster-whisper) | Pending upgrade |
| ctc-forced-aligner | Yes (ONNX) | **Implemented** |
| Canary-Qwen 2.5B | **No** (needs torch 2.6+) | Deferred to Phase 8 |
| WhisperX | **No** (needs torch 2.5.1+) | Replaced by ctc-forced-aligner |
| AI4Bharat IndicConformer | **No** (needs NeMo) | Deferred to Phase 4 |

---

## Notes for Future Phases

### Phase 4: Multilingual Mastery (DEFERRED — results unsatisfying)
- First-level testing done with SpeechBrain LID + IndicWhisper + IndicTrans2 — results not satisfying
- Code preserved in `phase4_multilingual` branch (not merged)
- Basic multilingual features (language survey + Whisper translate) moved to Phase 3
- Advanced features (specialist ASR models, dedicated translation models) deferred for later
- See `PHASE4_RESEARCH.md` for full research (12 models evaluated)

### Phase 5a: Confidence Flagging + Vocabulary Matching — IMPLEMENTED

**The Problem:** Whisper sometimes produces wrong words, especially for similar-sounding words. Example: "sales farce" instead of "Salesforce", "medal" instead of "MEDDIC". Low-confidence words (alignment score < threshold) are especially prone to errors that persist even with excellent preprocessing.

**Implementation — Combined Phase 5a + 6a:**

Phase 5a (confidence flagging + vocabulary matching) and Phase 6a (hotword boosting + abbreviation normalization) share the same vocabulary store, so they were implemented as a single unit.

**New dependency:** `jellyfish>=1.0.0` (phonetic encoding — Metaphone + Jaro-Winkler similarity, BSD-2 license, ~1MB)

#### Vocabulary Store (`services/vocabulary.py`)

**VocabEntry dataclass:**
```python
@dataclass
class VocabEntry:
    term: str                    # "Salesforce", "ARR", "MEDDIC"
    category: str                # "product", "competitor", "jargon", "abbreviation", "person"
    sounds_like: list[str]       # ["sales farce", "sales force"]
    context_words: list[str]     # ["CRM", "platform"]
    boost_priority: int          # 1=normal, 2=high, 3=critical
    enabled: bool                # True
```

**VocabularyStore class:**
- `load(path)` / `save(path)` — JSON file storage with atomic writes (temp → `os.replace()`)
- `add_entry()` / `update_entry()` / `delete_entry()` — CRUD operations
- `get_hotwords(max_tokens)` — space-separated priority-sorted terms for faster-whisper
- `get_abbreviations()` — set of abbreviation terms for normalizer
- `match_word(word, context)` — safe rule-based matching only (phonetic matching moved to Phase 5b LLM):
  1. Exact match (lowercase) → score 1.0
  2. Sounds-like match (exact phrase) → score 0.95
- `find_phonetic_candidates(word)` — returns vocab entries matching by Metaphone (for Phase 5b)
- `to_entries_list()` — pure dict export for LLMCorrector (no class dependency crossing)
- Three internal indexes: `_phonetic_index`, `_lower_index`, `_sounds_like_index`

**normalize_abbreviations(text, known_abbreviations):**
- Regex patterns: `"a r r"` → `"ARR"`, `"A.R.R."` → `"ARR"`, `"a, r, r"` → `"ARR"`
- Case normalization: `"salesforce"` → `"Salesforce"` for known terms

**apply_vocabulary_corrections(segments, vocab, confidence_threshold):**
- Phase 1: Abbreviation normalization on all segment text
- Phase 2: Word-level matching with multi-word sliding window (2-3 word windows)
  - With alignment: only flag words with `score < confidence_threshold` (default 0.5)
  - Without alignment: match all words against vocabulary
  - Multi-word matching: "sales farce" (2 words) → "Salesforce" via sliding window
- Returns (corrected_segments, correction_log)

**Correction log entry schema:**
```python
{
    "segment_id": int,          # Which segment
    "word_index": int,          # Index in words[] array (-1 if no alignment)
    "original_text": str,       # "sales farce"
    "corrected_text": str,      # "Salesforce"
    "correction_type": str,     # "abbreviation", "phonetic_match", "exact_match", "case_fix"
    "confidence_before": float, # 0.32 (word alignment score)
    "match_score": float,       # 0.95 (phonetic + context similarity)
}
```

#### Hotword Boosting (`services/transcribe.py`)

- `_get_options_fw(language, hotwords)` — adds `options["hotwords"] = hotwords` when non-empty
- `transcribe(audio_path, ..., hotwords="")` — passes hotwords through to all faster-whisper calls
- Lambda wrapper pattern: `get_options = lambda lang, _hw=hotwords: _get_options_fw(lang, hotwords=_hw)`
- Biases Whisper beam search decoder toward known terms DURING ASR

#### Pipeline Integration (`services/pipeline.py`)

- `run_pipeline(..., vocabulary_files=[])` — loads and merges vocabularies, generates hotwords, passes to transcribe
- Vocabulary correction step runs AFTER alignment + VAD remapping (needs per-word confidence scores)
- Results include vocabulary corrections section with processing time and correction count
- Step summary includes vocabulary correction status

#### Vocabulary Management Page (`vocabulary_ui.py`)

Dedicated page at `/audio-preprocess/vocabulary` (dark theme matching main UI):
- Vocabulary selector dropdown (starters + custom)
- CRUD table with inline editing: Term | Category | Sounds Like | Context Words | Priority | Enabled | Actions
- Search/filter bar
- Import/Export JSON buttons
- Load Starter Pack button (SaaS or Enterprise)
- Test Match tool: type a word → shows which vocabulary entry it matches + phonetic score
- All operations via `fetch()` to CRUD API endpoints

#### Starter Packs

- `vocabularies/saas_starter.json` — 42 common SaaS sales terms (ARR, MRR, MEDDIC, BANT, Salesforce, HubSpot, Gong, etc.)
- `vocabularies/enterprise_starter.json` — 30 enterprise terms (RFP, RFI, MSA, SOW, NDA, GDPR, SOC2, procurement, etc.)

#### Router Endpoints (`router.py`)

- `GET /vocabulary` — HTML management page
- `GET /vocabulary/list` — list all vocabularies with metadata
- `GET /vocabulary/{name}/data` — get full vocabulary data
- `POST /vocabulary/{name}/entry` — add new term
- `PUT /vocabulary/{name}/entry/{term}` — update term
- `DELETE /vocabulary/{name}/entry/{term}` — delete term
- `POST /vocabulary/upload` — upload complete JSON file
- `GET /vocabulary/{name}/export` — download as JSON
- `POST /vocabulary/{name}/load-starter` — load starter pack
- `POST /vocabulary/test-match` — test word matching

#### Main UI Changes (`ui.py`)

- Vocabulary checkboxes (multi-select) added after "Translate to English" checkbox
- "Manage Vocabularies →" link to management page
- Vocabulary corrections summary in results (Original | Corrected To | Type | Match Score)
- Vocabulary corrections section in PDF export

#### Config Settings (`config.py`)

```python
VOCABULARY_DIR: str              # Path to vocabularies/ directory
VOCABULARY_CONFIDENCE_THRESHOLD: float  # 0.5 — word alignment score below this triggers matching
VOCABULARY_HOTWORD_MAX_TOKENS: int      # 200 — max tokens for hotword string
```

#### Pipeline Execution Order (with vocabulary)

```
1. Audio Analysis
2. Resample + Highpass + VAD Trim + Loudnorm (Phase 1)
3. Conditional processing (denoise, bandwidth, declip)
4. Load vocabulary → generate hotwords string
5. ASR with hotwords (Phase 3 + 6a hotwords)
6. Word-level alignment (Phase 3 — ctc-forced-aligner)
7. VAD timestamp remapping
8. Abbreviation normalization + vocabulary matching (Phase 5a + 6a)
9. Translation (Phase 3 — Whisper translate / IndicTrans2)
```

#### Files Created/Modified

| File | Change |
|------|--------|
| `app/audio_preprocess/services/vocabulary.py` | NEW — core vocabulary engine |
| `app/audio_preprocess/vocabulary_ui.py` | NEW — management page HTML |
| `vocabularies/saas_starter.json` | NEW — 42 SaaS sales terms |
| `vocabularies/enterprise_starter.json` | NEW — 30 enterprise terms |
| `app/audio_preprocess/config.py` | Added 3 vocabulary settings |
| `app/audio_preprocess/services/transcribe.py` | Added hotwords param to _get_options_fw() and transcribe() |
| `app/audio_preprocess/services/pipeline.py` | Added vocabulary_files param (list), loading via load_and_merge, hotwords passing, correction step |
| `app/audio_preprocess/router.py` | Added vocabulary Form param (comma-separated) + 10 CRUD endpoints |
| `app/audio_preprocess/ui.py` | Added vocabulary checkboxes (multi-select), corrections display, PDF section |
| `requirements.txt` | Added jellyfish>=1.0.0, rapidfuzz>=3.0.0 |

### Phase 5b: LLM Context Correction — IMPLEMENTED

**The Problem:** Phase 5a's phonetic matching (Metaphone) produced false positives — common English words got incorrectly replaced ("are" → "ARR", "more" → "MRR", "going" → "Gong") because Metaphone codes collide. Stopword lists help but are a blunt instrument. Context understanding is needed.

**The Solution:** Move ALL phonetic corrections to an LLM (Qwen 2.5 7B via Ollama). Phase 5a now only does safe rule-based corrections (exact match, sounds_like, abbreviation). Phase 5b collects phonetic candidates and sends them to the LLM with surrounding context — the LLM decides if a word should be corrected or kept.

**Architecture:**
```
Phase 5a (no LLM): exact + sounds_like + abbreviation → safe, always works
Phase 5b (LLM):    phonetic + edit distance candidates → LLM context review → phonetic verification → apply
```

**Key design principle:** `llm_corrector.py` is fully self-contained — zero imports from pipeline, transcribe, or audio modules. Takes pure data (list[dict]), returns pure data. Can be extracted to a microservice by wrapping `correct_segments()` with a FastAPI route.

**Implementation:**
- `llm_corrector.py` (NEW): `check_ollama_available()`, `LLMCorrector` class
  - `collect_candidates()`: finds words matching vocab phonetically (Metaphone) or by spelling (edit distance via rapidfuzz) in both text + translation
  - `_build_prompt()`: system prompt (rules + vocab summary) + user prompt (candidates + context)
  - `_call_ollama()`: httpx POST to Ollama native `/api/chat`, `format: "json"`, retry logic (max_retries=2)
  - `_verify_phonetically()`: Metaphone match OR JW≥0.75 OR sounds_like match
  - `correct_segments()`: collect → prompt → LLM → verify → apply
- `vocabulary.py` changes:
  - `match_word()`: phonetic matching REMOVED — only exact + sounds_like
  - `find_phonetic_candidates()`: NEW — returns vocab entries matching by Metaphone
  - `to_entries_list()`: NEW — pure dict export for LLMCorrector
  - `apply_vocabulary_corrections()`: now corrects both text + translation, returns corrected_indices
- `config.py`: 5 new LLM settings (LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT, etc.)
- `pipeline.py`: `vocab_correction` + `llm_correction` params, Step 4.8a/4.8b
- `router.py`: `vocab_correction` + `llm_correction` Form params, `/llm-status` endpoint
- `ui.py`: "Enable Vocabulary Correction" master checkbox → vocab checkboxes (multi-select) + LLM checkbox

**Also fixed in this phase:**
- **Hotword boosting DISABLED** — testing showed it causes hallucination loops ("QBR SLA SPICED..."), 98% content loss on non-English chunks, and ALL CAPS garbled output
- **Stopword protection added** to vocabulary.py (~150 common English words blocked from phonetic matching)

| File | Change |
|------|--------|
| `app/audio_preprocess/services/llm_corrector.py` | NEW — self-contained LLM correction module (Metaphone + edit distance candidate matching) |
| `app/audio_preprocess/services/vocabulary.py` | Removed phonetic from match_word, added find_phonetic_candidates, to_entries_list, translation corrections |
| `app/audio_preprocess/config.py` | Added 5 LLM settings |
| `app/audio_preprocess/services/pipeline.py` | Added vocab_correction + llm_correction params, LLM step, corrected_indices |
| `app/audio_preprocess/router.py` | Added vocab_correction + llm_correction Form params, /llm-status endpoint |
| `app/audio_preprocess/ui.py` | Checkbox-gated vocab section, LLM status check, LLM results table, PDF export |

### Multi-Vocabulary Selection — IMPLEMENTED

Users can select multiple vocabulary JSON files. Entries from all selected files are merged (later file wins on duplicate terms).

**Changes:**
- `vocabulary.py`: Added `VocabularyStore.load_and_merge(paths)` class method — loads each file, combines entries, deduplicates by term
- `ui.py`: Changed `<select>` dropdown to checkboxes — each vocabulary file shown as a checkbox with entry count
- `postprocess_ui.py`: Same checkbox change for the post-processing test page
- `router.py` `/process`: Accepts comma-separated vocabulary names (e.g. `saas_starter,enterprise_starter`), resolves multiple paths
- `router.py` `/post-process/run`: Same comma-separated handling with `load_and_merge`
- `pipeline.py`: Changed `vocabulary_file: str` parameter to `vocabulary_files: list`, uses `load_and_merge` for multiple files

### Ollama Native API — CRITICAL FIX

**Problem:** Ollama's OpenAI-compatible endpoint (`/v1/chat/completions`) silently ignores `options.num_ctx`, always using default 4096 context. With ~6500 tokens input from a 185-segment transcript, output was truncated → malformed JSON → persistent parse failures.

**Fix:** Switched both `intelligence.py` and `llm_corrector.py` from `/v1/chat/completions` to `/api/chat` (Ollama native API).

**Native API differences:**
- `format: "json"` (not `response_format: {"type": "json_object"}`)
- `stream: false`
- `options: {temperature, num_ctx}` (properly applied)
- Response path: `data["message"]["content"]` (not `data["choices"][0]["message"]["content"]`)

**Context window:** `INTELLIGENCE_NUM_CTX` settled at 16384 (was 8192 → 32768 → 16384). Sufficient for most transcripts, 4x faster than 32768 on CPU.

**Retry logic:** Both modules now retry LLM calls on JSON parse failure (max_retries=2). `intelligence.py` attempts `_repair_json()` before retrying.

### Pipeline Order Change

Resemble Enhance moved from Step 5 (after diarization) to Step 3.4 (before diarization at Step 3.5). This allows the enhanced audio to be used for diarization if desired.

### Phase 5c: N-best + RAG + Auto-learning — Pending

**Remaining Phase 5 work:**
- **5c:** N-best hypothesis re-ranking, RAG context, auto-learning from user corrections
- See `PHASE5_RESEARCH.md` for full details

### Phase 6b: Fuzzy Matching — IMPLEMENTED (merged into 5b)

**Edit distance matching** (rapidfuzz Levenshtein) was merged into Phase 5b's candidate collection rather than implemented as a standalone step. The LLM already provides context-aware decision making, so a separate fuzzy matching step with its own threshold logic was unnecessary.

**Implementation:** Added to `llm_corrector.py` `_find_phonetic_candidates()`:
- After Metaphone check, loops through all vocab entries comparing edit distance
- Threshold: ≤2 edits for words >5 chars, ≤1 edit for shorter words
- Uses `rapidfuzz.distance.Levenshtein` with `score_cutoff` for early termination
- Candidates from both Metaphone and edit distance feed into the same LLM → verify → apply pipeline

**Examples caught by edit distance (not Metaphone):**
- "Salesfroce" → Salesforce (2 edits, letter swap)
- "HubSopt" → HubSpot (2 edits, letter swap)

**New dep:** `rapidfuzz>=3.0.0` (~2MB, MIT license)

### Phase 6c: Auto-learning — Pending

**Remaining Phase 6 work:**
- Auto-learning from user corrections, vocabulary suggestions, organization-specific correction rules
- See `PHASE6_RESEARCH.md` for full details

### Phase 7A+7B: Intelligence Layer — IMPLEMENTED

**Three sub-phases:** 7A (Core Intelligence), 7B (Advanced Scoring), 7C (CRM Integration)

#### Phase 7A: Core Intelligence — IMPLEMENTED

**New files:**
- `app/audio_preprocess/services/schemas.py` — Pydantic models for CallIntelligence JSON output, compact LLM schema via `get_extraction_schema()`. ObjectionType/NextStepType/QuestionType changed from enums to free-text strings (7B model doesn't reliably follow enum constraints).
- `app/audio_preprocess/services/coaching_metrics.py` — Talk ratio, monologue, questions, fillers, interruptions (pure computation, no LLM). Works with or without speaker labels.
- `app/audio_preprocess/services/intelligence.py` — LLM extraction via Ollama native `/api/chat` + sentiment + normalization + JSON repair + scoring + retry logic (max_retries=2)
- `app/audio_preprocess/postprocess_ui.py` — Standalone post-processing test page at `/audio-preprocess/post-process`

**Modified files:** config.py (Phase 7 settings + INTELLIGENCE_NUM_CTX=16384), pipeline.py (Steps 5.1-5.3 + AAC pre-conversion + Resemble Enhance moved to Step 3.4), router.py (enable_intelligence param + /intelligence-status + /post-process endpoints), ui.py (intelligence dashboard + diarized transcript column + nav link)

**Coaching Metrics** (no LLM needed — direct computation):
- `compute_talk_ratio()`: sum durations per speaker, classify optimal/rep_dominant/customer_dominant/no_speakers. Works without speaker labels (all attributed to "rep").
- `compute_longest_monologue()`: consecutive same-speaker segment tracking
- `count_questions()`: regex sentences ending in "?" per speaker. Works without speaker labels.
- `count_filler_words()`: word-boundary regex (um, uh, like, you know, basically, etc.) per speaker with breakdown. Works without speaker labels.
- `count_interruptions()`: speaker B start < speaker A end by >0.5s
- `compute_response_time()`: average gap between speakers (exclude >10s silences)
- `compute_silence_percentage()`: uncovered time / total duration
- Speaker role: SPEAKER_00 = rep (default), None → "rep" (single speaker fallback), UI override later

**LLM Intelligence Extraction + Sentiment** (single pass):
- `IntelligenceExtractor` class (self-contained, same pattern as `llm_corrector.py`)
- `_format_transcript()`: segments → `SPEAKER_00 [00:15-00:32]: "text..." (Translation: ...)`
- `SYSTEM_PROMPT`: role + extraction rules + sales-specific sentiment rules + JSON schema
- `_call_llm()`: httpx POST to Ollama native `/api/chat`, `format: "json"`, `options: {num_ctx: 16384}`, retry logic (max_retries=2, repair → retry → repair → give up)
- `_normalize_llm_output()`: remaps LLM field name variations (action/text/content → description, segment_index → segment_id, features list → features dict) before Pydantic validation
- `_repair_json()`: fixes truncated/malformed LLM JSON — trailing commas, unclosed brackets, dangling keys, last-resort substring extraction
- Pydantic `CallIntelligence` validation with raw dict fallback
- Map-Reduce for calls >12K tokens: split into ~4K token chunks with 500-token overlap, extract per chunk, merge with deduplication

**Sentiment — LLM-integrated (NOT separate model):**
- Per-segment sentiment + overall trajectory in same JSON extraction
- Sales-specific prompt rules: "price objections are neutral", "competitor evaluation is neutral", etc.
- For Tamil/Hindi: sentiment analyzed on English translations (IndicTrans2 already in pipeline)
- Rationale: no off-the-shelf model fits sales domain, no Tamil support in any classifier, LLM has context awareness

**Schemas** (Pydantic):
```
CallIntelligence
├── metadata (call_id, date, duration, participants, language, extraction_model)
├── summary (one_line, detailed, key_topics)
├── competitors[] (name, sentiment, context, quote, timestamp)
├── pain_points[] (description, business_impact, quote)
├── objections[] (type free-text, description, handled, rep_response, quote)
├── next_steps[] (description, owner enum, due_date, type free-text)
├── budget (discussed, amount, currency, pricing_presented, discount_requested, roi_discussed)
├── decision_making (decision_maker, buying_committee[], process, timeline, champion)
├── deal_stage (current_stage enum, confidence, stage_signals[], risk_signals[])
├── features (discussed[], requests[], use_cases[], technical_requirements[])
├── segment_sentiments[] (segment_id, sentiment enum, confidence)
├── overall_sentiment (overall, customer_net, rep_net, trajectory enum, notable_shifts[])
├── coaching_metrics (talk_ratio, monologue, questions, fillers, interruptions, response_time, silence%)
└── questions_asked[] (question, asked_by, type free-text, answered, timestamp)
```

**Config additions:**
```python
INTELLIGENCE_ENABLED = False                              # Master toggle (env var override)
INTELLIGENCE_LLM_MODEL = "qwen2.5:7b-instruct"           # Separate from LLM_MODEL (Phase 5)
INTELLIGENCE_MAX_SINGLE_PASS_TOKENS = 12000               # Above this → Map-Reduce
INTELLIGENCE_NUM_CTX = 16384                              # Ollama context window (was 8192, then 32768, settled on 16384 for CPU speed)
```
Note: `LLM_BASE_URL`, `LLM_TIMEOUT`, `LLM_TEMPERATURE` shared with Phase 5b.

**Model configuration strategy:**
- Development (CPU): Both Phase 5 + Phase 7 use `qwen2.5:7b-instruct` via Ollama
- Production (GPU): Phase 5 stays `qwen2.5:7b-instruct`, Phase 7 upgrades to `qwen2.5:14b-instruct`
- Same base URL, OpenAI-compatible API — switching is config-only

**Pipeline integration:**
- Step 5.1: Coaching Metrics (after Step 4.8b LLM correction) — pure computation, <1s
- Step 5.2: Intelligence Extraction (after 5.1) — LLM call, ~23 min on CPU (7B model), ~1-2 min on GPU
- Step 5.3: Scoring (after 5.2) — methodology + deal health + coaching scorecard, <100ms
- Gating: Steps 5.1-5.3 only run if `enable_intelligence=True`
- Coaching metrics work without speaker labels (questions/fillers still count); per-speaker breakdown requires diarization

**Post-Processing Test Page** (`/audio-preprocess/post-process`):
- Standalone UI for testing vocab correction, LLM correction, and intelligence on pasted transcript text
- Auto-detects input format: `[SPEAKER_XX] text` (with speakers) or plain text (sentence splitting with auto-generated timestamps)
- "Load Sample" button pre-fills 8-segment sales call with competitors, objections, budget, next steps
- Options: vocab correction → vocabulary checkboxes (multi-select) → LLM correction; intelligence; language
- LLM/Intelligence status indicators via `/llm-status` and `/intelligence-status`
- Results: vocab corrections table, LLM corrections table, intelligence dashboard, corrected transcript

**Diarized Transcript Column** (main pipeline UI):
- When diarization enabled, shows color-coded `[SPEAKER_XX] text` in a new "Diarized Transcript" row
- Appears in both comparison and single-path result tables
- Easy to copy-paste into post-processing page with full speaker info

**AAC File Support:**
- `.aac` added to file input accept attribute
- FFmpeg pre-conversion to WAV in pipeline.py before `analyze_audio()` (soundfile/libsndfile doesn't support AAC)

**Bugs fixed during implementation:**
- Pydantic validation fails with 9 errors: LLM field naming mismatches → `_normalize_llm_output()` remaps variations
- Truncated LLM JSON (Expecting ',' delimiter): Ollama default 4096 context too small → increased `num_ctx=8192` + `_repair_json()` fallback
- AAC "Format not recognised": soundfile can't read AAC → FFmpeg pre-conversion before analysis
- Coaching metrics all zeros without speakers: functions skipped segments with `speaker=None` → removed `not speaker` guard, handle no-speaker case
- **Ollama `/v1/chat/completions` ignores `num_ctx`**: Root cause of ALL JSON parse failures — Ollama's OpenAI-compatible endpoint silently ignores `options.num_ctx`, defaulting to 4096 context. `api/ps` showed `context_length: 4096` despite requesting 32768. Switched to native `/api/chat` which properly applies `options.num_ctx`.
- **32768 context too slow on CPU**: 20% RAM, extremely slow token generation. Reduced default to 16384 (sufficient for most transcripts, 4x faster).
- **Pydantic enum validation failure**: LLM returned `next_steps.type="setup"` not in ObjectionType/NextStepType/QuestionType enums. Changed from enums to free-text strings.
- **Ollama stuck after kill**: After killing test script, Ollama runner continued at 795% CPU generating tokens with no client. Fixed with `brew services restart ollama`.

#### Phase 7B: Advanced Scoring — IMPLEMENTED

Functions in `intelligence.py` (no new files):

- `compute_methodology_scores()`: MEDDIC (0-6) + BANT (0-4) from extracted fields (boolean logic, defensive against unexpected LLM output types)
  - MEDDIC: Metrics, Economic Buyer, Decision Criteria, Decision Process, Identify Pain, Champion
  - BANT: Budget, Authority, Need, Timeline
- `validate_deal_stage()`: Cross-check LLM's stage against rule-based classification (discovery → qualification → evaluation → negotiation → closing), lower confidence on disagreement
- `compute_deal_health()`: Weighted composite 0-100 (positive: budget discussed, champion, timeline, next steps, positive sentiment; negative: unhandled objections, risk signals, declining sentiment; bonus: MEDDIC score)
- `compute_coaching_scorecard()`: Letter grade A/B/C/D from 5 components at 20% each (talk ratio, question quality, objection handling, methodology coverage, call outcome)

**Pipeline:** Step 5.3 (Scoring) runs after Step 5.2, pure computation <100ms

#### Phase 7C: CRM Integration — Pending
- Zoho CRM OAuth 2.0 self-client flow, refresh token persistence
- Custom fields on Deals module (competitors, budget, MEDDIC score, sentiment, etc.)
- Note attachment with formatted intelligence summary
- Deal/Contact auto-matching by email/phone (future)
- See `PHASE7_RESEARCH.md` Section 6 for full Zoho API details

### Phase 8: Technical Gotchas
- **Canary-Qwen 2.5B** as microservice (separate Docker with PyTorch 2.6+, communicate via HTTP)
- Cross-talk / overlapping speech — Target Speaker Extraction (TSE)
- GPU memory: 24GB VRAM budget for all models
- Parallel chunk transcription with GPU batched inference via faster-whisper BatchedInferencePipeline
- Serverless GPU options: Modal (fastest cold starts), RunPod (cheapest A100)

### Phase 9: Post-Processing Microservice — Future

Extract Phases 5-7 (all post-ASR text processing) into a separate microservice.

**Why it separates cleanly:** Post-processing never touches audio. It takes segments JSON (text + speaker + timestamps) and produces corrected text + intelligence JSON. Zero dependency on torch, Whisper, pyannote, FFmpeg, or any audio library.

**Architecture:**
```
┌─────────────────────────┐     segments JSON      ┌──────────────────────────┐
│  ASR Microservice       │ ─────────────────────→  │  Post-Processing         │
│  (GPU Server)           │                         │  Microservice            │
│                         │                         │                          │
│  • Audio analysis       │                         │  • Vocab correction (5a) │
│  • Core processing      │                         │  • LLM correction (5b)   │
│  • Conditional proc.    │                         │  • Coaching metrics (7A)  │
│  • Whisper ASR          │                         │  • Intelligence (7A)     │
│  • Pyannote diarization │                         │  • Scoring (7B)          │
│  • ctc-forced-aligner   │                         │                          │
│  • VAD remapping        │                         │  Deps: httpx, jellyfish, │
│  • Translation          │                         │  rapidfuzz, pydantic     │
│                         │                         │  (~50 MB)               │
│  Deps: torch, whisper,  │     ┌──────────────┐    │         │                │
│  pyannote, ffmpeg       │     │ Ollama/vLLM  │◄───┤─────────┘                │
│  (~4-8 GB)              │     │ (GPU or CPU)  │    │                          │
└─────────────────────────┘     └──────────────┘    └──────────────────────────┘
```

**Benefits:**
- Independent scaling (ASR = GPU-bound, post-processing = LLM-bound)
- Re-runnable: change vocab/prompts without re-transcribing
- Lighter deployment (~50MB vs multi-GB audio deps)
- Fault isolation (LLM timeout doesn't block ASR)
- Different hardware (ASR needs GPU, post-processing just needs LLM access)

**Design principle already in place:** All post-processing modules (`vocabulary.py`, `llm_corrector.py`, `coaching_metrics.py`, `intelligence.py`) are self-contained — take pure dicts, zero imports from pipeline/transcribe/audio. Microservice extraction = wrap with FastAPI route + move files.

**API contract:**
```json
POST /process
Input: { "segments": [...], "diarization_result": {...}, "call_duration": 1800.0,
         "vocabulary_files": ["saas_starter", "enterprise_starter"], "enable_intelligence": true }
Output: { "corrected_segments": [...], "coaching_metrics": {...},
          "intelligence": {...}, "processing_time": 45.2 }
```

---

## Critical Constraint: PyTorch 2.1.1 Version Lock (RESOLVED)

### Root Cause (Resolved)

`resemble-enhance==0.0.1` previously pinned `torch==2.1.1` in the main process. This has been resolved by moving resemble-enhance to an isolated microservice (`top-level resemble_enhance/`). The main process no longer depends on resemble-enhance, so torch can be upgraded when needed. Currently torch 2.1.1 remains installed (no immediate need to upgrade — Phase 3 tools work on 2.1.1).

### Cross-Phase Impact

| Phase | Blocked Tool | Requires | Why Blocked |
|-------|-------------|----------|-------------|
| **Phase 3** | Canary-Qwen 2.5B | torch>=2.6 + NeMo + NVIDIA GPU | NeMo framework needs modern torch; SALM architecture uses features not in 2.1.1 |
| **Phase 3** | WhisperX | torch>=2.5.1 | Explicitly pinned in WhisperX 3.7.6 requirements |
| **Phase 4** | AI4Bharat IndicConformer | NeMo + torch>=2.6 | Same NeMo dependency as Canary-Qwen |
| **Phase 4** | Dolphin ASR | ESPnet + likely torch>=2.4 | ESPnet framework typically requires recent torch |
| **Phase 8** | Canary-Qwen microservice | torch>=2.6 (in separate container) | Only blocked if running in same process; microservice architecture bypasses this |

### What Still Works on torch 2.1.1

| Tool | Status | Notes |
|------|--------|-------|
| faster-whisper (CTranslate2) | **Works** | Already installed and tested |
| Whisper large-v3-turbo | **Works** | Via faster-whisper, not native torch |
| ctc-forced-aligner | **Works** | Uses Facebook MMS model, verified compatible |
| pyannote.audio 3.1.1 | **Works** | Already installed and tested (Phase 2) |
| Silero VAD | **Works** | Lightweight, no modern torch features needed |
| DeepFilterNet3 | **Works** | Already installed and tested (Phase 1) |

### Resolution Options

| # | Option | Pros | Cons |
|---|--------|------|------|
| **1** | **Drop resemble-enhance** (Recommended) | Unblocks ALL phases; torch upgradeable to latest; resemble-enhance is Path B only (perceptual quality, not ASR) | Lose Path B audio enhancement comparison in UI |
| **2** | **Microservice architecture** | Keep resemble-enhance in main process; run Canary-Qwen/IndicConformer in separate Docker container with its own torch | Added infrastructure complexity; HTTP overhead for ASR calls; need Docker/orchestration |
| **3** | **Stay within torch 2.1.1 limits** | Zero risk; faster-whisper + large-v3-turbo + ctc-aligner is solid | Can never use Canary-Qwen, IndicConformer, or Dolphin ASR; permanently locked out of best models |
| **4** | **Move resemble-enhance to its own microservice** | Best of both worlds — keep resemble-enhance AND upgrade torch in main process | Same infrastructure complexity as Option 2; resemble-enhance rarely used |

### Recommendation

**Option 1 (drop resemble-enhance)** is the cleanest path forward. Resemble-enhance serves only Path B (perceptual audio quality comparison) which is a testing/demo feature, not part of the production ASR pipeline. Dropping it allows upgrading torch to latest, unlocking Canary-Qwen 2.5B, IndicConformer, Dolphin ASR, and all future models.

If Path B comparison is valued, **Option 4** (move resemble-enhance to its own microservice) preserves the feature while unblocking the main process.

**Decision: Option 4 (move resemble-enhance to microservice) — IMPLEMENTED.**

Resemble-enhance moved to `top-level resemble_enhance/` as an isolated Docker service with its own torch 2.1.1. Main process no longer depends on resemble-enhance — the `requirements.txt` pin is removed. Path B calls the microservice via HTTP (`RESEMBLE_ENHANCE_URL` config). If the microservice is down, Path B gracefully degrades (disabled in UI, fallback copy in pipeline). The main process torch can be upgraded when needed for Phase 4/8.

---

## LLM Performance Optimization (Feb 2026)

### Problem
LLM calls via Ollama (Qwen 2.5 7B) on CPU were extremely slow — 30+ minutes for a ~4748 token transcript intelligence extraction. Both Phase 5b (vocab correction) and Phase 7 (intelligence) affected.

### Root Causes
1. `INTELLIGENCE_NUM_CTX=16384` — Ollama allocates full KV cache regardless of actual input size. On CPU, larger context = slower per-token generation.
2. Phase 5b sent candidates in batches of 20 — multiple sequential LLM calls.
3. Phase 7 map-reduce chunks processed sequentially.

### Fix 1: Dynamic num_ctx (Biggest Impact)
Instead of fixed `num_ctx`, calculate minimum needed per call and pass to Ollama. Smaller context = smaller KV cache = faster inference.

**Formula:** `num_ctx = max(4096, prompt_chars/3.5 + output_buffer + 512)`, capped at `INTELLIGENCE_NUM_CTX`.

- `intelligence.py`: `_estimate_num_ctx()` helper, output_buffer=3000, retry with 1.5x num_ctx on truncated JSON
- `llm_corrector.py`: `_estimate_num_ctx()` method on LLMCorrector, output_buffer=1500

**Example savings:** 4748-token transcript needs ~8760 num_ctx instead of 16384 — nearly 2x reduction.

### Fix 2: Increased Batch Size
`LLM_MAX_CANDIDATES_PER_BATCH`: 20 → 50. Fewer LLM round-trips = less overhead per call.

### Fix 3: Parallel Map-Reduce
`_extract_map_reduce()` now uses `concurrent.futures.ThreadPoolExecutor` (max 4 workers) for parallel chunk processing. Chunks are independent — Ollama queues concurrent requests internally.

### Fix 4: Dev/Prod Model Config
Both `LLM_MODEL` and `INTELLIGENCE_LLM_MODEL` defaults changed to `qwen2.5:3b-instruct` (fast dev iteration on CPU). For production (GPU), override via env vars to `qwen2.5:14b-instruct`.

### Files Modified
- `app/audio_preprocess/services/intelligence.py` — dynamic num_ctx, parallel map-reduce, ThreadPoolExecutor import
- `app/audio_preprocess/services/llm_corrector.py` — dynamic num_ctx via `_estimate_num_ctx()` method
- `app/audio_preprocess/config.py` — batch size 50, 3B model defaults, comments for prod override

---

## Hallucination Filter Fix (Feb 2026)

### Problem
N-gram repetition detector in `_is_hallucination()` was too aggressive — triggered on 2 occurrences of any 3-word phrase, filtering real conversational speech like "a lot of our, a lot of our stuff" and "how to be X, how to be Y, how to be Z".

### Fix
Two-tier detection:
1. **Total count**: Same n-gram appears **>5 times** total (even non-consecutive)
2. **Consecutive loop**: Same n-gram appears **3+ times back-to-back** (catches real hallucination loops like "you know you know you know")

Natural speech with rhetorical patterns ("how to be X, how to be Y, how to be Z") passes both checks — only 3 total occurrences (not >5) and not consecutive.

### Files Modified
- `app/audio_preprocess/services/transcribe.py` — `_is_hallucination()` rewritten with Counter-based n-gram counting + consecutive detection

---

## Phone Mode Chunking Fix (Feb 2026)

### Problem
With phone mode enabled ("Sensitive to Quiet" checkbox), the 53-55s section of a test audio ("About an hour north of Nashville") was consistently missing from transcription despite being present in normal mode.

### Root Cause
Phone mode's `min_silence_duration_ms=500` (in both VAD trimming AND chunking) merged too many segments into massive blocks. A 300-400ms pause that normal mode (300ms) used as a natural chunk boundary was ignored by phone mode, creating 54-second monster segments. The energy-based splitter then cut these at sub-optimal points, creating chunks where the quiet 53-55s section (-34dB, 10dB quieter than surroundings) was buried in a 25.8s chunk. Whisper skipped the quiet section.

### Fix 1: Separate Chunking VAD Threshold
Chunking VAD (`_get_speech_chunks()`) now always uses `min_silence_duration_ms=300` regardless of phone mode. Only the trim VAD (`trim_silence_vad()`) uses the phone mode 500ms threshold.

### Fix 2: Shorter Chunks in Phone Mode
New constant `MAX_CHUNK_SECONDS_PHONE = 18.0` (vs 28.0 normal). Smaller chunks = less volume contrast within each chunk = Whisper less likely to skip quiet sections.

### Fix 3: Gap Rescue Pass
After all chunks are transcribed, `_rescue_gaps()` scans for timeline gaps ≥1s between consecutive segments that fall within a chunk boundary (meaning Whisper had the audio but produced nothing). Re-transcribes just those gaps with `no_speech_threshold=None` and merges recovered segments.

### Files Modified
- `app/audio_preprocess/services/transcribe.py` — `_get_speech_chunks()` min_silence=300, `MAX_CHUNK_SECONDS_PHONE`, `_rescue_gaps()`, `_split_oversized_chunk()` accepts max_seconds param

---

## Diarization Speaker Splitting (Feb 2026)

### Problem
`assign_speakers_to_segments()` assigned one speaker per Whisper segment. When a Whisper segment was 16-20s long and contained multiple speakers, it picked the speaker with most temporal overlap — the other speaker's content was mislabeled.

### Fix
Rewrote `assign_speakers_to_segments()` to **split Whisper segments at diarization speaker-change boundaries**:

1. For each Whisper segment, find all pyannote speaker turns that overlap
2. **Single speaker** → assign directly (same as before)
3. **Multiple speakers** → split the segment:
   - **With word timestamps**: assign each word to the speaker active at that word's midpoint, group by speaker
   - **Without word timestamps**: split text proportionally by each speaker turn's duration

Helper functions added:
- `_get_speaker_at()` — get speaker at a specific time point
- `_find_speaker_changes()` — find all speaker turns within a time range, merge adjacent same-speaker turns

### Files Modified
- `app/audio_preprocess/services/diarize.py` — `assign_speakers_to_segments()` rewritten with segment splitting

---

## Speaker Embedding Verification — Post-Diarization Cleanup (Feb 2026)

### Problem
Pyannote's agglomerative clustering sometimes produces errors: over-segmentation (splitting one speaker into 2-3 clusters) or label flipping (short segments assigned to wrong cluster). These errors propagate to all downstream features (transcription, coaching metrics, intelligence extraction).

### Solution
Optional post-diarization cleanup using speaker embeddings from `pyannote/wespeaker-voxceleb-resnet34-LM`. Enabled via `DIARIZATION_VERIFY_SPEAKERS=true` (default: false).

### How It Works

1. **Extract embeddings** — For each diarization segment ≥ 1.5s (`MIN_EMBED_DURATION`), extract a speaker embedding using `inference.crop()`. Compute duration-weighted, L2-normalized centroids per cluster. Segments shorter than threshold tracked separately for later inheritance.

2. **Merge similar clusters** — Compute pairwise cosine similarity between all cluster centroids. If highest similarity ≥ `MERGE_THRESHOLD` (0.75), merge the smaller cluster into the larger one. Repeat until no more merges possible. Uses transitive resolution for multi-step merges (A→B, B→C resolved to A→C).

3. **Reassign outliers** — For each embedded segment, compute cosine similarity to its own cluster centroid. If below `OUTLIER_THRESHOLD` (0.55), check all other clusters. Reassign if best alternative exceeds `REASSIGN_THRESHOLD` (0.65) AND beats the current cluster by `REASSIGN_MARGIN` (0.10).

4. **Short segment inheritance** — Segments below `MIN_EMBED_DURATION` (too short for reliable embedding) inherit the speaker label from the nearest embedded segment by temporal proximity.

5. **Sequential relabeling** — Final speaker labels reassigned as SPEAKER_00, SPEAKER_01, etc. by order of first appearance.

### Config Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `DIARIZATION_VERIFY_SPEAKERS` | `false` | Enable/disable verification |
| `DIARIZATION_EMBEDDING_MODEL` | `pyannote/wespeaker-voxceleb-resnet34-LM` | Speaker embedding model |
| `DIARIZATION_MERGE_THRESHOLD` | `0.75` | Cosine similarity to merge clusters |
| `DIARIZATION_OUTLIER_THRESHOLD` | `0.55` | Below this → segment is outlier |
| `DIARIZATION_REASSIGN_THRESHOLD` | `0.65` | Must exceed to reassign |
| `DIARIZATION_REASSIGN_MARGIN` | `0.10` | New cluster must beat old by this |
| `DIARIZATION_MIN_EMBED_DURATION` | `1.5` | Min seconds for reliable embedding |

### Files Modified

| File | Changes |
|------|---------|
| `services/diarize.py` | +342 lines: embedding singleton, 4 verification functions, integration into `diarize_audio()` |
| `services/pipeline.py` | Verification stats in `to_dict()`, step reason includes verification summary |
| `ui.py` | Verification rows in HTML + PDF diarization summary |
| `config.py` | 7 new `DIARIZATION_VERIFY_*` / `DIARIZATION_*` params |

### Known Limitation
Verification operates at the **cluster level** (post-clustering). It cannot fix errors from pyannote's upstream **segmentation model** — if the neural network doesn't detect a speaker change (e.g., brief 1-2s interjections inside a long monologue), the segment is never created, and verification has nothing to merge/reassign. This is a fundamental pyannote limitation, not a verification limitation.

---

## Phase 10: Memoir Post-Processing (Feb 2026)

### Overview

Completely separate post-processing flow alongside Phase 7. A UI dropdown lets users choose "Phase 7 Intelligence" or "Memoir Summary". Both flows share vocabulary/LLM correction (Phases 5a/5b), but produce different structured outputs.

**Why separate from Phase 7?** They're complementary, not competing:
- **Phase 7:** Coaching analytics for sales managers — talk ratio, MEDDIC/BANT scoring, deal health, coaching scorecard
- **Memoir:** CRM auto-fill for sales reps — 7 discovery fields with confidence levels + source attribution, narrative summary ("returning salesperson" test), action items split by rep/prospect

### Architecture

```
UI Dropdown: "Post-Processing Type"
    ├── "Phase 7 Intelligence" (default)  →  intelligence.py + schemas.py + coaching_metrics.py
    │                                         (UNTOUCHED — coaching, MEDDIC/BANT, sentiment)
    └── "Memoir Summary" (new)            →  memoir_extractor.py + memoir_schemas.py
                                              (confidence, attribution, CRM-ready summary)
```

### Memoir Schema (memoir_schemas.py)

**ConfidenceLevel** (4-tier enum): `definitive` (100) | `indicative` (70) | `inferred` (40) | `ambiguous` (0)

**DiscoveryFieldBase** — every field carries:
- `confidence`: ConfidenceLevel
- `source_speaker`: who said it
- `source_quote`: supporting text (max 200 chars)
- `source_timestamp_approx`: MM:SS

**7 Discovery Fields:**
1. **BudgetField** — value (e.g. "$50K", "Declined to share")
2. **AuthorityField** — decision_maker, title, champions, blockers, approval_process
3. **NeedField** — primary_pain, secondary_pains, current_workaround
4. **TimelineField** — target_date, driving_event, urgency (urgent/active/exploring/none)
5. **TechStackField** — current_tools, pain_with_current, integrations_needed
6. **TeamField** — size, roles, locations
7. **CompetitionField** — competitors_evaluating, competitor_sentiment

**MemoirSummary** (top-level): call_metadata, discovery_fields, narrative_summary (3-5 paragraphs), action_items (rep_actions + prospect_commitments), next_steps, signals (buying + risk), disclaimer, overall_confidence_score (0-100)

**NullListMixin**: Pydantic `model_validator(mode='before')` that coerces LLM `null` → `[]` for all list-typed fields. Applied to DiscoveryFieldBase, ActionItems, Signals, Disclaimer, MemoirCallMetadata. Critical fix — LLMs frequently return `null` instead of `[]`.

### Memoir Extractor (memoir_extractor.py)

**MemoirExtractor** class — same Ollama native `/api/chat` pattern as intelligence.py:
- Dynamic `num_ctx` (output_buffer=4000 for larger Memoir JSON)
- Single-pass for short transcripts, map-reduce (ThreadPoolExecutor, max 4 workers) for long ones
- JSON repair (trailing commas, unclosed brackets, truncation recovery)
- Retry with 1.5x expanded context on truncation

**System prompt:** 6 principles (accuracy over completeness, attribution mandatory, confidence tagging, "returning salesperson" test, respect confidentiality, handle multiple speakers) + 8 edge case rules

**Pre-extraction checks:**
- **Off-the-record redaction**: Scans for keywords ("off the record", "between us", "don't quote me", etc.). Removes matching segment + the next segment (confidential info usually follows). Sets `disclaimer.confidential_content_detected = true`. LLM never sees redacted content.
- **Voicemail detection**: Single speaker + <3 min → adds hint to LLM prompt for minimal summary

**Post-extraction validation:**
- **Confidence validation**: Auto-downgrades "definitive" to "indicative" when source quote is weak/missing (contains "not discussed", "unclear", etc.) or absent
- **Confidence scoring**: Weighted average of all 7 field confidences, averaged with LLM's own score

### Files Summary

| File | Action | Description |
|------|--------|-------------|
| `services/memoir_schemas.py` | **NEW** | Pydantic models (ConfidenceLevel, 7 discovery fields, NullListMixin, MemoirSummary, get_memoir_schema) |
| `services/memoir_extractor.py` | **NEW** | MemoirExtractor class (LLM extraction, off-the-record redaction, voicemail detection, confidence validation) |
| `ui.py` | MODIFIED | Post-processing type dropdown + `renderMemoirSummary()` JS with confidence badges + PDF sections |
| `postprocess_ui.py` | MODIFIED | Same dropdown + render function (kept in sync with ui.py) |
| `router.py` | MODIFIED | `post_processing_type` Form param on /process and /post-process/run |
| `pipeline.py` | MODIFIED | Branches Step 6: memoir → `run_memoir_extraction`, phase7 → `run_full_intelligence` |
| `config.py` | NO CHANGE | Reuses INTELLIGENCE_LLM_MODEL, LLM_BASE_URL |
| `intelligence.py` | NO CHANGE | Phase 7 untouched |
| `schemas.py` | NO CHANGE | Phase 7 schemas untouched |

### Known Issues (Reported)

- **Diarization segmentation**: Pyannote's segmentation model can miss brief (1-2s) speaker turns inside long monologues. Embedding verification (`DIARIZATION_VERIFY_SPEAKERS=true`) fixes clustering errors (over-segmentation, label flipping) but cannot recover undetected speaker changes — this is a pyannote segmentation model limitation. Tested with min_cluster_size=12/9/6 — no improvement in speaker detection.
- **CPU performance**: LLM extraction on CPU extremely slow (~750s for short transcript with Qwen 2.5 7B). Production requires GPU. Phase 8 will address this.

### Phase 10 Sub-phases

| Sub-phase | Status | Description |
|-----------|--------|-------------|
| **10A** | IMPLEMENTED | Memoir schemas + extractor (core extraction engine) |
| **10B** | IMPLEMENTED | UI dropdown + router + pipeline integration |
| **10C** | IMPLEMENTED | Off-the-record redaction (low-discovery + quality-issues dropped — LLM handles natively) |
| **10D** | Future | Call threading — link multiple calls for same deal, track field changes over time |
| **10E** | Future | CRM push — push Memoir summary to Zoho CRM by confidence level |
| **10F** | Future | Email summary — send formatted Memoir as email to rep |
