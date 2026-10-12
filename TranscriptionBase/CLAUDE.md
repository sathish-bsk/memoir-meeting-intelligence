# TranscriptionBase — Project Architecture & Session Context

## Project Vision

Sales call transcription & intelligence platform. Self-hosted models (not cloud APIs) wherever possible. Takes audio from file uploads, media URLs, or YouTube, produces accurate transcriptions with speaker identification, and eventually extracts structured intelligence (competitors, objections, next steps) for CRM integration.

## User Preferences

- Always discuss before implementing — no coding without explicit approval
- Self-hosted models preferred over cloud APIs
- English + Tamil/Hindi mixed-language sales calls (primary use case)
- FFmpeg + Python libs approach
- UI toggle for optional features (not always-on)

---

## 9-Phase Roadmap

| Phase | Name | Goal | Status |
|-------|------|------|--------|
| **1** | The "Clean Audio" Foundation | Smart audio routing — detect what each file needs, apply minimum effective processing | **IMPLEMENTED** |
| **2** | Who is Who? | Speaker diarization with Pyannote 3.1.1, run before ASR | **IMPLEMENTED** |
| **3** | The "World-Class" ASR | ctc-forced-aligner + VAD remapping + tail re-transcription (all working) | **IMPLEMENTING** |
| **4** | Multilingual Mastery | IndicConformer/AI4Bharat + Dolphin for Hindi & Tamil code-switching | **DEFERRED** |
| **5** | Transcription Post-Processing | Context-based correction of low-confidence words using LLM (Qwen 2.5 3B dev / 14B prod) + phonetic similarity | **5a+5b IMPLEMENTED** (vocabulary matching + LLM context correction via Ollama), 5c Pending |
| **6** | Domain Vocabulary & Jargon | Custom vocabulary, abbreviation normalization, auto-learning | **6a+6b IMPLEMENTED** (abbreviation normalization + edit distance merged into 5b — hotword boosting DISABLED), 6c Pending |
| **7** | The "Gong" Intelligence Layer | Coaching metrics + LLM extraction (competitors, objections, sentiment — single pass) + scoring | **7A+7B IMPLEMENTED**, 7C Pending |
| **8** | Technical Gotchas | Cross-talk/TSE, GPU memory (24GB VRAM), serverless GPU providers, parallel chunk transcription | Pending |
| **9** | Post-Processing Microservice | Extract Phases 5-7 into separate microservice (text-only, no audio deps) | Future |
| **10** | Memoir Post-Processing | CRM-ready call summary with confidence tagging, source attribution, 7 discovery fields | **10A+10B+10C IMPLEMENTED**, 10D-F Future |

## Core Components (mapped to phases)

| # | Component | Tool | Phase |
|---|-----------|------|-------|
| 1 | Enhancement | DeepFilterNet (conditional) + ClearerVoice-Studio (bandwidth ext.) | 1 |
| 2 | VAD | Silero VAD v6.x (v5 fallback) | 1, 2 |
| 3 | Diarization | pyannote speaker-diarization-3.1 | 2 |
| 4 | ASR (English) | faster-whisper large-v3-turbo (primary), openai-whisper (fallback) | 3 |
| 5 | Alignment | ctc-forced-aligner (word-level timestamps, 1130 languages) | 3 |
| 6 | ASR (Indic) | IndicConformer / AI4Bharat + Dolphin ASR | 4 |
| 7 | Post-Processing | Vocabulary matching (exact+sounds_like) + LLM context correction (Qwen 2.5 3B dev / 14B prod via Ollama) + phonetic verification | 5 |
| 8 | Vocabulary | Abbreviation normalization (regex) + vocabulary management UI (hotword boosting DISABLED) | 6 |
| 9 | Coaching Metrics | Direct computation from diarization data (talk ratio, monologue, questions, fillers, interruptions) | 7 |
| 10 | Intelligence Extraction | Qwen 2.5 3B (dev) / 14B (prod) via Ollama/vLLM — competitors, objections, sentiment, next steps, budget, scoring (single LLM pass) | 7 |
| 11 | Integration | Zoho CRM APIs + OAuth + Sync Service | 7C |
| 12 | Memoir Summary | Separate LLM extraction — 7 discovery fields with confidence + attribution, CRM-ready output | 10 |

---

## Phase 1: The "Clean Audio" Foundation — DETAILED

### Key Research Finding: Less Preprocessing = Better ASR

Industry consensus (2025-2026): aggressive audio enhancement HURTS transcription accuracy.

**Evidence:**
- Dec 2025 paper: denoising degraded WER in ALL 40 test configurations (4 ASR models x 10 noise conditions), 1-47% worse
- Speech enhancement artifacts are MORE harmful to ASR than original noise
- Deepgram: "spectral subtraction improves SNR by 8dB yet drives WER up 15%"
- Generative models (Resemble Enhance) can hallucinate phonemes — words never spoken appear in enhanced audio

**What production services do:**
- Rev.ai: No preprocessing. Train on 3M+ hours of noisy data. Model handles noise.
- Deepgram: No preprocessing. "Noise Reduction Paradox" — multi-condition training > post-hoc filtering.
- AssemblyAI: No external preprocessing. 600M param Conformer on 12.5M hours.
- Amazon Transcribe: Warns denoising "removes consonant sounds, turns fifteen into thirteen."
- OpenAI Whisper: Raw audio → Mel spectrogram → model. No enhancement.
- Google Chirp 3: Only provider with a built-in co-optimized denoiser (not external).
- Microsoft Azure: Offers MAS but warns "distorted audio adversely affects ML ASR accuracy."

### Phase 1 Architecture: Detection-First, Not Always-On

Instead of enhancing everything, detect what each audio file needs and apply minimum processing.

```
Input audio
    |
    v
[Audio Analysis Module]
    |-- Measure SNR (energy-based with CV check)
    |-- Detect input sample rate
    |-- Detect clipping (peak analysis)
    |-- Measure duration, channels, bit depth
    |
    v
[Always Apply]
    |-- Resample to 16kHz mono (required by Silero VAD + Whisper)
    |-- Highpass 80Hz (remove rumble — NO lowpass, preserve high-freq info for ASR)
    |-- Silero VAD v6 silence trimming
    |-- FFmpeg loudnorm dual-pass (-23 LUFS)
    |
    v
[Conditional — only when detected]
    |-- IF sample_rate <= 8kHz (telephone) --> bandwidth extension (FFmpeg soxr)
    |-- IF clipping > 0.1% --> declipping (tanh soft saturation)
    |-- IF SNR < 15dB --> DeepFilterNet3 + Observation Adding (70/30 blend)
    |
    v
[Path Selection — user chooses in UI]
    |-- Path A: Direct ASR (recommended, faster)
    |-- Path B: Resemble Enhance → ASR (for perceptual quality testing)
    |-- Both: A/B comparison (disabled by default — slow)
    |
    v
[Smart Chunking + Whisper ASR]
    |-- Audio > 30s: VAD-based chunking with energy-based fallback
    |-- Per-chunk language detection (when auto-detect)
    |-- Context carry-over between chunks
    |
    v
Results (transcription + segments + timestamps + metadata)
```

### Processing Steps — Implementation Details

Each step in the pipeline uses a specific tool. Here's exactly how each one works:

| # | Step | Tool | Type | How It Works |
|---|------|------|------|-------------|
| 1 | **Audio Analysis** | `soundfile` + `numpy` | Python in-memory | Reads raw audio, computes SNR (energy-based with CV check), peak/RMS amplitude, clipping %, sample rate, channels. All CPU, no model. |
| 2 | **Resample to 16kHz mono** | FFmpeg | CLI subprocess (`asyncio`) | `ffmpeg -i input -ar 16000 -ac 1 -c:a pcm_s16le output.wav`. Required before VAD (Silero only accepts 8k/16k). |
| 3 | **Highpass 80Hz** | FFmpeg | CLI subprocess (`asyncio`) | `ffmpeg -af "highpass=f=80"`. Removes sub-80Hz rumble/hum. No lowpass — preserves high-freq consonants (/s/, /f/, /th/). |
| 4 | **VAD silence trimming** | Silero VAD v6 | PyTorch model (~1MB) | Loaded via `torch.hub.load("snakers4/silero-vad")`. Returns speech timestamps, `collect_chunks` concatenates speech segments. Threshold=0.5, min_speech=250ms, speech_pad=200ms. CPU-only singleton. |
| 5 | **Dual-pass loudnorm** | FFmpeg | CLI subprocess (`asyncio`) x2 | **Pass 1:** `loudnorm=print_format=json` → measures input_I, input_LRA, input_TP, input_thresh. **Pass 2:** applies with measured values + `linear=true` for precise -23 LUFS normalization. Falls back to single-pass if measure fails. |
| 6 | **Bandwidth extension** | FFmpeg | CLI subprocess (`subprocess.run`) | Only if sample rate ≤ 8kHz. `aresample=resampler=soxr:precision=33:dither_method=triangular` — high-quality SoX resampler to 16kHz. |
| 7 | **Declipping** | NumPy | Python in-memory | Only if clipping > 0.1%. Applies `np.tanh(audio * 0.8) / np.tanh(0.8)` soft saturation to smooth clipped peaks. No model needed. |
| 8 | **Denoising + OA** | DeepFilterNet3 | PyTorch model (~10MB) | Only if SNR < 15dB. Loaded via `df.enhance.init_df()`. Enhances at 48kHz, resamples back, blends: `0.7 * enhanced + 0.3 * original`. Singleton with thread lock. |
| 9 | **Resemble Enhance** | resemble-enhance | PyTorch model (diffusion, ~300MB) | Generative denoiser. `resemble_enhance.enhancer.inference.denoise()`. Outputs at 44.1kHz, resampled back to 16kHz for ASR. Falls back to file copy if not installed. |
| 10 | **Transcription (ASR)** | OpenAI Whisper | PyTorch model (medium=~1.5GB) | `whisper.load_model("medium")`. beam_size=5, best_of=5, temperature=0, with initial prompt for punctuation. Smart chunking for audio >30s (VAD + energy-based splits, per-chunk language detection, context carry-over). Returns text + segment timestamps with logprob/no_speech/compression metadata. |

**Execution model:** FFmpeg steps use `asyncio.create_subprocess_exec` (non-blocking). Model-based steps run in `loop.run_in_executor(None, ...)` to avoid blocking the event loop. All models are loaded as singletons with thread locks.

### ASR: Whisper Medium + Smart Chunking

**Model:** `whisper-medium` (769M params, ~1.5GB). Chosen over `base` for significantly better multilingual accuracy and language detection. English WER ~2.7%, Tamil WER ~35%. Configurable via `ASR_MODEL_SIZE` env var. Upgrade to `large-v3-turbo` when GPU is available.

**Language selection:** UI dropdown with auto-detect (default) + 17 languages (en, ta, hi, te, kn, ml, mr, bn, gu, pa, ur, fr, de, es, ja, zh, ar). Auto-detect lets Whisper identify the language. For mixed-language audio (e.g., English + Tamil sales calls), auto-detect with smart chunking gives per-chunk language detection.

**Important Whisper language behavior:**
- `language=None` (auto-detect): Whisper detects language ONCE from the first 30s and applies to the ENTIRE file. This is why smart chunking is critical — each chunk gets its own detection.
- `language="en"` (forced): All audio decoded as English. Tamil/Hindi segments produce garbage. Only use if audio is 100% English.
- For mixed English + Tamil, use auto-detect + smart chunking.

**Smart chunking** (for audio > 30 seconds):
1. Silero VAD detects speech segments — chunking VAD always uses `min_silence=300ms` to find natural split points (separate from trim VAD which uses 500ms in phone mode)
2. Consecutive segments grouped into chunks ≤ 28 seconds (Whisper's context window is 30s). **Phone mode uses 18s max chunk size** — shorter chunks reduce volume contrast within chunks, improving accuracy for muffled/quiet speakers.
3. **Energy-based fallback:** If any chunk exceeds the max (28s or 18s in phone mode), it's subdivided by finding lowest-energy 50ms windows (breath pauses, micro-silences). Recursive splitting until all chunks fit.
4. Each chunk transcribed independently with per-chunk language detection (when language=auto)
5. Last 200 chars of previous chunk fed as `initial_prompt` to next chunk for context continuity
6. Segment timestamps remapped to original audio position (chunk offset added)
7. Language reported as `"mixed (en:4, ta:2)"` when multiple languages detected across chunks
8. **Gap rescue pass:** After transcription, detects gaps ≥ 1.0s between segments where Whisper may have skipped speech. Re-transcribes each gap with relaxed thresholds to recover missed content.

**Why energy-based fallback is needed:** After VAD silence trimming in the core processing step, all pauses ≥ 300ms have been removed. When the transcriber runs VAD again for chunking, it sees one continuous speech block. The energy-based splitter finds natural breath pauses (even < 500ms) to split at, rather than cutting mid-syllable.

**Note on timestamps after VAD trimming:** Segment timestamps are relative to the VAD-trimmed audio, not the original file. For subtitle generation synced to original video, timestamps would need remapping using saved VAD boundaries. Phase 3 (WhisperX forced alignment) will solve this properly by aligning text against the original untrimmed audio.

### Resemble Enhance — When and How to Use

**NEVER use in ASR pipeline.** Generative diffusion model that can fabricate speech content.

**Safe uses:**
- After transcription: enhance audio clips for playback in the product UI
- Demo/showcase: make sample audio sound professional
- Archive restoration: old recordings for human listening

**Architecture:** Run as a parallel output path (Path B in the UI).

### Observation Adding (OA) Technique

When denoising is applied (conditional, SNR < 15dB), blend enhanced + original:
```python
output = alpha * enhanced + (1 - alpha) * original  # alpha = 0.7
```
This preserves natural speech characteristics while reducing noise. Research shows OA improves signal-to-artifact ratio and downstream ASR performance.

### UI Features

- **Dark theme** with drag-and-drop file upload
- **Path selection** radio buttons: Path A (recommended, default), Path B, Both (disabled with hover warning about speed)
- **Language dropdown**: Auto-detect (default) + 17 languages
- **Progress bar** with percentage and step names
- **Input Analysis table** with metric definitions (italic text under each metric)
- **Processing Steps table** with Step / Status (Applied|Skipped) / Reason columns
- **Results table**: single-column for one path, side-by-side comparison for both
- **Segment Timestamps table** with color-coded quality metrics (avg_logprob, no_speech_prob, compression_ratio)
- **PDF download** button (jsPDF + autoTable from CDN) — generates full report with all tables, transcription, and segments
- **5-hour fetch timeout** via AbortController to match server-side timeouts

### Timeouts

All FFmpeg and subprocess operations use **5-hour timeouts** (18000s). Frontend fetch also uses 5-hour AbortController timeout. Configurable via env vars `FFMPEG_TIMEOUT` and `SUBPROCESS_TIMEOUT`.

### Configuration (config.py)

```python
SNR_THRESHOLD_DB = 15.0          # Below this → denoise
CLIPPING_THRESHOLD = 0.99        # Sample threshold for clipping detection
LOW_SAMPLE_RATE_HZ = 8000        # Below this → bandwidth extension
TARGET_LUFS = -23.0              # EBU R128 loudness target
TARGET_TP = -1.5                 # True peak target
TARGET_LRA = 11.0                # Loudness range target
OA_ALPHA = 0.7                   # Observation Adding blend ratio
ASR_MODEL_SIZE = "medium"        # Whisper model (was "base", upgraded for multilingual)
ASR_DEVICE = "cpu"               # CPU for now, GPU in Phase 6
RESEMBLE_ENHANCE_URL = "http://localhost:8001"  # Microservice URL
FFMPEG_TIMEOUT = 18000           # 5 hours
SUBPROCESS_TIMEOUT = 18000       # 5 hours
TEMP_DIR = "/tmp/audio_preprocess"
```

All configurable via environment variables.

---

## Current Implementation State

### Phase 1 Files (inside app/audio_preprocess/)

| File | Description |
|------|-------------|
| `__init__.py` | Empty |
| `config.py` | All thresholds, ASR settings, timeouts, device config |
| `router.py` | FastAPI router at `/audio-preprocess`. Endpoints: `GET /` (UI), `POST /process` (pipeline, accepts file + processing_mode + language), `GET /audio/{id}/{file}` (serve audio), `DELETE /cleanup/{id}` |
| `ui.py` | Full HTML UI — upload, path selection, language dropdown, progress bar, results tables, segment timestamps, PDF download |
| `services/analyzer.py` | `AudioAnalysis` dataclass, `estimate_snr()` (energy-based with CV check), `detect_clipping()`, `analyze_audio()`. NumPy types converted to native Python in `to_dict()` for JSON serialization. |
| `services/core_processing.py` | `resample_to_16k()`, `apply_highpass()` (80Hz, no lowpass), `trim_silence_vad()` (Silero VAD singleton), `apply_loudnorm_dual_pass()` (with single-pass fallback) |
| `services/conditional.py` | `denoise_with_oa()` (DeepFilterNet3 + alpha blending), `apply_bandwidth_extension()` (FFmpeg soxr), `apply_declipping()` (tanh soft saturation) |
| `services/enhance_playback.py` | `apply_resemble_enhance()` with fallback to file copy if not installed |
| `services/transcribe.py` | Whisper ASR with smart chunking: VAD-based + energy-based fallback splitting, per-chunk language detection, context carry-over, timestamp remapping |
| `services/pipeline.py` | `PipelineResult` class, `run_pipeline()` orchestrator accepting `processing_mode` and `language` params |

### Pre-existing Files (not part of Phase 1, but still in codebase)

| File | Description |
|------|-------------|
| `app/main.py` | FastAPI app — includes `audio_preprocess_router`. Also has original Whisper/Pingala/AI4Bharat routes |
| `app/core/config.py` | Original env-based settings |
| `app/services/asr.py` | Original Whisper ASR with smart chunking (reference implementation) |
| `app/services/audio_preprocessor.py` | Original noisereduce/DeepFilterNet + VAD + loudnorm |
| `app/services/ffmpeg.py` | Original format conversion + bandpass 80Hz-8kHz |
| `app/services/ingest.py` | File upload, URL download, YouTube (yt-dlp) |

### Entry Point

Router is mounted in `app/main.py` via `app.include_router(audio_preprocess_router)`.
Single `requirements.txt` at project root (resemble-enhance is a separate top-level service at `resemble_enhance/`).
Browser URL: `http://localhost:8000/audio-preprocess/`
Start: `uvicorn app.main:app --host 0.0.0.0 --port 8000`

---

## Bugs Fixed During Implementation

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| Silero VAD crash: "only supports 8000 and 16000 sample rates" | Pipeline ran highpass at original sample rate (e.g., 44.1kHz) then passed to VAD | Resample to 16kHz FIRST, before highpass and VAD |
| `'PreprocessConfig' has no attribute 'RESEMBLE_ENHANCE_DEVICE'` | Field missing when config was recreated inside `app/` | Added the field to config.py |
| `Object of type bool_ is not JSON serializable` | `AudioAnalysis.to_dict()` returned numpy `bool_` types | Added type conversion for `np.bool_`, `np.integer`, `np.floating` |
| SNR estimator false negative on clean sine wave (0.2 dB) | Energy-based method assumes speech-like energy variation | Added coefficient of variation check — if CV < 0.3 (steady signal), use RMS-based estimate |
| `git: 'lfs' is not a git command` | git-lfs not installed, Resemble Enhance model download failed | Installed git-lfs via homebrew, ran `git lfs install` |
| `asyncio.new_event_loop()` inside sync function called from async | `apply_bandwidth_extension()` used async loop in sync context | Replaced with `subprocess.run()` |
| Smart chunking produces 1 chunk for entire 43-min audio | After VAD silence trimming, all pauses removed — VAD finds no boundaries | Added energy-based fallback: finds lowest-energy 50ms windows (breath pauses) and recursively splits oversized chunks |
| Whisper detects Tamil for entire mixed English+Tamil audio | `language="en"` was hardcoded; auto-detect picks one language for whole file | Changed to `language=None` (auto-detect) as default, added UI language dropdown, smart chunking gives per-chunk detection |

---

## Notes for Future Phases

### Phase 2: Diarization — IMPLEMENTED (see CONTEXT.md + DIARIZATION_RESEARCH.md)
- **Model:** pyannote/speaker-diarization-3.1 (pyannote.audio 3.1.1, works with torch 2.1.1)
- **Architecture:** Segmentation → Embedding → Agglomerative Clustering
- **Pipeline:** Diarize BEFORE ASR → per-segment Whisper transcription → temporal overlap alignment
- **Speaker assignment:** `assign_speakers_to_segments()` splits Whisper segments at speaker-change boundaries (not just picking the dominant speaker). If a segment spans multiple speaker turns, it gets split into separate segments — text distributed by word timestamps (if available) or proportionally by duration.
- **Speaker count:** Auto-detected. Hints: min_speakers=2, max_speakers=10
- **Multichannel shortcut:** Auto-detect stereo channels → channel-based separation (skip diarization)
- **Speaker Embedding Verification** (optional, `DIARIZATION_VERIFY_SPEAKERS=true`): Post-diarization cleanup using `wespeaker-voxceleb-resnet34-LM` embeddings — cluster merging (cosine similarity), outlier reassignment, short segment inheritance. Corrects pyannote over-segmentation and label flipping. 7 config params (`DIARIZATION_MERGE_THRESHOLD`, etc.). Known limitation: cannot fix errors from pyannote's upstream segmentation model (undetected brief speaker turns).
- **Phase 7 future:** Voice enrollment for rep identification (like Gong), requires GDPR opt-in consent
- Do NOT use Whisper's internal "fake" diarization

### Phase 3: World-Class ASR — IMPLEMENTING
- **ctc-forced-aligner** for word-level timestamps — IMPLEMENTED (`services/aligner.py`)
  - ONNX-based MMS model, 1130 languages (Tamil+Hindi), ~50ms accuracy
  - Per-segment alignment with auto-detected language
  - Script detection via `unicodedata.name()` (15+ scripts auto-detected)
  - Pre-cleaning prevents assertion crashes on numbers/symbols
- **VAD timestamp remapping** — IMPLEMENTED (`core_processing.py` + `pipeline.py`)
  - `trim_silence_vad()` returns `(Path, vad_boundaries)` tuple
  - `_remap_to_original_time()` + `_remap_segments()` add `original_start`/`original_end` to segments + words
  - Separate "Original Audio Timestamps" UI table + PDF section
- **Tail re-transcription** — IMPLEMENTED (`transcribe.py`)
  - Detects when Whisper stops early within a chunk (gap > 1.5s)
  - Re-transcribes uncovered tail with `no_speech_threshold=None`
  - Integrated into both `_transcribe_chunked` and `_transcribe_multilingual`
  - Chunk edge extension: ensures first/last chunk covers full audio
- **Translate to English** — IMPLEMENTED (`transcribe.py`)
  - Indian languages: IndicTrans2 text-to-text (15/15 Tamil accuracy vs Whisper's 2/8)
  - Other languages: Whisper translate mode (text + word timestamps)
  - Script-based routing via Unicode ranges (not Whisper's detected language)
  - 14 Indian languages supported, batch translation per language
- **Hallucination filter** — `_is_hallucination()` detects and removes Whisper hallucination segments:
  - Compression ratio > 2.4 (Whisper's own default threshold)
  - Avg logprob < -1.0 (very low confidence)
  - N-gram repetition (3-gram through 5-gram): any n-gram >5 total occurrences OR 3+ consecutive identical n-grams (back-to-back loop). Catches real hallucination loops while ignoring rhetorical patterns.
- All core Phase 3 features tested and working on mixed English+Tamil audio
- Canary-Qwen 2.5B deferred to Phase 8 (needs torch 2.6+ / GPU)
- Remaining: faster-whisper large-v3-turbo model upgrade

### Phase 4: Multilingual — DEFERRED
- First-level testing done with SpeechBrain LID + IndicWhisper + IndicTrans2 — results not satisfying
- Code preserved in `phase4_multilingual` branch (not merged)
- Basic multilingual features (language survey + translation) moved to Phase 3
- Advanced features (specialist ASR models, dedicated LID) deferred for later
- See `PHASE4_RESEARCH.md` for full research (12 models evaluated)

### Phase 5a + 6a: Vocabulary Correction + Hotword Boosting — IMPLEMENTED

Combined Phase 5a (confidence flagging + vocabulary matching) and Phase 6a (hotword boosting + abbreviation normalization) because they share the same vocabulary store.

**Architecture:** In-process (not microservice) — hotwords must be passed INTO Whisper during ASR. JSON storage with atomic writes. Separate vocabulary management page.

**New files:**
- `app/audio_preprocess/services/vocabulary.py` — VocabEntry, VocabularyStore, phonetic matching, abbreviation normalization, apply_vocabulary_corrections
- `app/audio_preprocess/vocabulary_ui.py` — Management page at `/audio-preprocess/vocabulary`
- `vocabularies/saas_starter.json` (42 terms), `vocabularies/enterprise_starter.json` (30 terms)

**Modified files:** config.py (3 new settings), transcribe.py (hotwords param), pipeline.py (vocabulary_files + correction step), router.py (10 CRUD endpoints), ui.py (checkboxes multi-select + corrections display)

**Pipeline execution order:**
1. Load vocabulary → generate hotwords string
2. ASR with hotwords (faster-whisper `hotwords` param biases beam search)
3. Abbreviation normalization (regex: "a r r" → "ARR", case fixes)
4. Word-level alignment (ctc-forced-aligner)
5. VAD timestamp remapping
6. Confidence flagging + vocabulary matching (phonetic, on aligned words with score < threshold)
7. Translation

**Key dependency:** `jellyfish>=1.0.0` (Metaphone + Jaro-Winkler, BSD-2, ~1MB)

### Phase 5b: LLM Context Correction — IMPLEMENTED
- Qwen 2.5 3B (dev/CPU) / 14B (prod/GPU) via Ollama native `/api/chat`, phonetic (Metaphone) + edit distance (rapidfuzz) candidate matching
- Phonetic verification gate (Metaphone match OR Jaro-Winkler ≥ 0.75 OR sounds_like match)
- Retry logic (max_retries=2) on JSON parse failure
- **Dynamic num_ctx:** Context window calculated per-call based on prompt size (`prompt_tokens + output_buffer + safety_margin`), clamped to configured max cap. Avoids wasting memory on short prompts while allowing full context for large batches.
- **Key deps:** `jellyfish>=1.0.0`, `rapidfuzz>=3.0.0`

### Phase 5c + 6c: N-best + Auto-learning (Pending, merged)
- N-best hypothesis re-ranking
- Auto-learning from user corrections (RAG context + vocabulary sounds_like expansion)

### Phase 6b: Fuzzy Matching — IMPLEMENTED (merged into 5b)
- Edit distance (rapidfuzz Levenshtein) added to 5b candidate collection
- Standalone step not needed — LLM handles decision making

### Phase 7A+7B: Intelligence Layer — IMPLEMENTED

**New files:**
- `app/audio_preprocess/services/schemas.py` — Pydantic models for CallIntelligence JSON (nullable fields, evidence quotes, compact LLM schema). ObjectionType/NextStepType/QuestionType are free-text strings (not enums).
- `app/audio_preprocess/services/coaching_metrics.py` — Talk ratio, monologue, questions, fillers, interruptions, response time, silence % (pure computation, no LLM)
- `app/audio_preprocess/services/intelligence.py` — LLM extraction via Ollama native `/api/chat` + sentiment + scoring + `_repair_json` + retry logic (max_retries=2)
- `app/audio_preprocess/postprocess_ui.py` — Standalone post-processing test page

**Modified files:** config.py (INTELLIGENCE_ENABLED, INTELLIGENCE_LLM_MODEL, INTELLIGENCE_NUM_CTX=16384), pipeline.py (Steps 5.1-5.3 + AAC pre-conversion + Resemble Enhance at Step 3.4), router.py (enable_intelligence param, /intelligence-status, /post-process endpoints), ui.py (intelligence dashboard + diarized transcript column + post-process nav link)

**7A — Coaching Metrics** (no LLM, direct computation):
- `compute_talk_ratio()`: sum durations per speaker, works with or without speaker labels
- `compute_longest_monologue()`: consecutive same-speaker segment tracking
- `count_questions()`: regex "?" per speaker (works without speakers too)
- `count_filler_words()`: word-boundary regex (um, uh, like, you know, etc.) per speaker with breakdown
- `count_interruptions()`: speaker B start < speaker A end by >0.5s
- `compute_response_time()`: average gap between speakers (exclude >10s)
- `compute_silence_percentage()`: uncovered time / total duration
- Speaker role: SPEAKER_00 = rep (default), UI override later

**7A — LLM Intelligence Extraction + Sentiment** (single pass):
- `IntelligenceExtractor` class (self-contained, same pattern as `llm_corrector.py`)
- **Dev model:** Qwen 2.5 3B (CPU), **Prod model:** Qwen 2.5 14B (GPU) — configurable via `INTELLIGENCE_LLM_MODEL`
- Extracts: competitors, pain points, objections, next steps, budget, decision-making, deal stage, features, questions
- **Sentiment in same pass** — sales-specific prompt rules ("price objections are neutral")
- **Map-Reduce** for calls >12K tokens: split into ~4K token chunks with 500-token overlap, merge results. **Parallel execution with ThreadPoolExecutor (max 4 workers)** — chunks are independent, significant speedup on multi-core.
- **Dynamic num_ctx:** Context window calculated per-call (`prompt_tokens + output_buffer(3000) + safety_margin`), clamped to configured max cap (`INTELLIGENCE_NUM_CTX`). Avoids allocating 16K context for short transcripts.
- `_normalize_llm_output()`: remaps LLM field name variations before Pydantic validation
- `_repair_json()`: fixes truncated/malformed LLM JSON (trailing commas, unclosed brackets, dangling keys)
- Ollama native `/api/chat` with dynamic `num_ctx` (OpenAI-compatible endpoint silently ignores `num_ctx`)
- Retry logic: parse → repair (cheap) → retry LLM call (with 1.5x expanded context on truncation) → parse → repair → give up

**7B — Scoring** (pure computation, no LLM):
- `compute_methodology_scores()`: MEDDIC (0-6) + BANT (0-4) from extracted fields
- `validate_deal_stage()`: cross-check LLM stage vs rule-based classification
- `compute_deal_health()`: weighted composite 0-100 from all signals
- `compute_coaching_scorecard()`: letter grade A/B/C/D from metrics + methodology + outcome

**Post-Processing Test Page** (`/audio-preprocess/post-process`):
- Standalone UI for testing vocab correction, LLM correction, and intelligence on pasted text
- Auto-detects input format: `[SPEAKER_XX] text` (with speakers) or plain text (sentence splitting)
- "Load Sample" button with 8-segment sales call
- Same options as main pipeline: vocab checkboxes (multi-select), LLM checkbox, intelligence checkbox
- Results: correction tables, intelligence dashboard, coaching metrics, scoring cards

**Diarized Transcript Column** (main pipeline UI):
- When diarization enabled, shows color-coded `[SPEAKER_XX] text` in results table
- Easy to copy-paste into post-processing page with full speaker info

**Phase 7C (CRM — Pending):**
- Zoho CRM OAuth 2.0 self-client flow
- Custom fields on Deals module + Note attachment
- Deal/Contact auto-matching by email/phone

**Key decisions:**
- **Sentiment**: LLM-integrated (NOT separate RoBERTa model) — no off-the-shelf model fits sales domain, no Tamil support
- **Model config**: Separate fields — `LLM_MODEL` (Phase 5, 3B dev / 14B prod) + `INTELLIGENCE_LLM_MODEL` (Phase 7, 3B dev / 14B prod)
- **Ollama native API** (`/api/chat`) — OpenAI-compatible endpoint silently ignores `num_ctx`
- **AAC support**: FFmpeg pre-conversion to WAV before analysis (soundfile/libsndfile doesn't support AAC)
- **No new dependencies** — httpx + pydantic already installed
- See `PHASE7_RESEARCH.md` for full research

### Phase 10: Memoir Post-Processing — IMPLEMENTED (10A+10B+10C)

Completely separate post-processing flow alongside Phase 7. UI dropdown selects between "Phase 7 Intelligence" and "Memoir Summary". Both share vocabulary/LLM correction steps but produce different outputs.

**Phase 7 vs Memoir:**
- Phase 7: Coaching analytics for sales managers (MEDDIC/BANT, talk ratio, deal health)
- Memoir: CRM auto-fill for sales reps (discovery fields with confidence + attribution, narrative summary)

**New files:**
- `services/memoir_schemas.py` — Pydantic models: ConfidenceLevel (4-tier), 7 DiscoveryField subclasses (Budget, Authority, Need, Timeline, TechStack, Team, Competition), MemoirSummary top-level. NullListMixin handles LLMs returning null for list fields.
- `services/memoir_extractor.py` — MemoirExtractor class: Ollama native `/api/chat`, single-pass or map-reduce, confidence validation (downgrades hallucinated "definitive"), off-the-record redaction, voicemail detection, overall confidence scoring.

**Modified files:** ui.py (ppType dropdown + renderMemoirSummary), postprocess_ui.py (same), router.py (post_processing_type param), pipeline.py (branches Step 6 based on type)

**Key features:**
- 4-tier confidence: definitive (100) / indicative (70) / inferred (40) / ambiguous (0)
- Source attribution on every discovery field (speaker + quote + timestamp)
- Off-the-record redaction: segments with confidential keywords stripped before LLM sees them
- Voicemail detection: single speaker + <3 min → minimal summary hint to LLM
- Confidence validation: auto-downgrade "definitive" when quote is weak/missing
- NullListMixin: Pydantic model_validator coerces LLM null→[] for list fields
- Map-reduce for long transcripts with ThreadPoolExecutor (max 4 workers)
- Same Ollama config as Phase 7 (INTELLIGENCE_LLM_MODEL, LLM_BASE_URL)

**10D-F (Future):** Call threading (link multiple calls), CRM push (by confidence level), Email summary

### Known Issues

- **Diarization segmentation**: Pyannote's segmentation model can miss brief (1-2s) speaker turns inside long monologues. Embedding verification (`DIARIZATION_VERIFY_SPEAKERS=true`) fixes clustering errors but cannot recover undetected speaker changes (upstream limitation).
- **CPU performance**: LLM extraction on CPU is very slow (~750s for short transcript with Qwen 2.5 7B). Needs GPU for production use.

### Phase 8: Technical Gotchas
- Cross-talk / overlapping speech — Target Speaker Extraction (TSE)
- GPU memory: Resemble Enhance + Canary + Pyannote needs 24GB VRAM (RTX 3090/4090)
- Consider serverless GPU: RunPod / Lambda
- Canary-Qwen 2.5B as microservice (separate Docker with torch 2.6+)
- **Parallel chunk transcription** — GPU batched inference for smart chunking. On CPU, chunks are sequential (model not thread-safe, context carry-over is sequential). On GPU, batch multiple chunks in a single forward pass for significant speedup.

### Phase 9: Post-Processing Microservice — Future

Extract Phases 5-7 (all post-ASR text processing) into a separate microservice. Clean separation because post-processing never touches audio.

**ASR Microservice (GPU-heavy):**
- Audio analysis, core processing, conditional processing
- Whisper ASR, pyannote diarization, ctc-forced-aligner
- VAD remapping, translation
- Deps: torch, whisper, pyannote, ffmpeg (~4-8 GB)
- Output: segments JSON

**Post-Processing Microservice (LLM-bound):**
- Vocabulary correction (5a/6a), LLM correction (5b)
- Coaching metrics (7A), intelligence extraction (7A), scoring (7B)
- Deps: httpx, jellyfish, rapidfuzz, pydantic (~50 MB)
- Connects to Ollama/vLLM via HTTP
- Input: segments JSON → Output: corrected segments + CallIntelligence JSON

**Benefits:** Independent scaling, re-runnable without re-transcribing, lighter deployment, fault isolation, different hardware (ASR=GPU, post-processing=CPU+LLM).

**Design principle:** All post-processing modules already designed as self-contained (zero imports from pipeline/transcribe/audio). Microservice extraction = wrap with FastAPI + move files. No architectural refactoring needed.

**API contract:**
```
POST /process
{
  "segments": [...],
  "diarization_result": {...},
  "call_duration": 1800.0,
  "vocabulary_files": ["saas_starter", "enterprise_starter"],
  "enable_vocab_correction": true,
  "enable_llm_correction": true,
  "enable_intelligence": true
}
→ { "corrected_segments": [...], "intelligence": {...}, "coaching_metrics": {...} }
```
