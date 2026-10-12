# Phase 3 & Phase 4: Complete ASR Research — Beginner-Friendly Guide

> **Updated: February 2026** — Torch constraint removed (resemble-enhance moved to microservice).
> This document covers ALL research findings for Phase 3 (World-Class ASR) and Phase 4 (Multilingual Mastery) in plain, beginner-friendly language.
> For the older research that was written when we were stuck on torch 2.1.1, see `ASR_RESEARCH.md` and `PHASE3_RESEARCH.md`.

---

## Table of Contents

1. [What Changed? The Torch Breakthrough](#1-what-changed-the-torch-breakthrough)
2. [Understanding the Key Terms](#2-understanding-the-key-terms)
3. [Phase 3: World-Class ASR](#3-phase-3-world-class-asr)
   - [3.1 The ASR Leaderboard — Who's Who in 2026](#31-the-asr-leaderboard--whos-who-in-2026)
   - [3.2 NVIDIA Canary-Qwen 2.5B — The Ferrari (Now Available!)](#32-nvidia-canary-qwen-25b--the-ferrari-now-available)
   - [3.3 Whisper large-v3-turbo via faster-whisper — The Reliable Upgrade](#33-whisper-large-v3-turbo-via-faster-whisper--the-reliable-upgrade)
   - [3.4 IBM Granite Speech 3.3 — The Tank (New!)](#34-ibm-granite-speech-33--the-tank-new)
   - [3.5 Other Notable Models](#35-other-notable-models)
   - [3.6 What is "Forced Alignment"? (Word-Level Timestamps)](#36-what-is-forced-alignment-word-level-timestamps)
   - [3.7 WhisperX — Why We're NOT Using It](#37-whisperx--why-were-not-using-it)
   - [3.8 faster-whisper vs openai-whisper — Why Switch?](#38-faster-whisper-vs-openai-whisper--why-switch)
   - [3.9 Sales Call Specific Findings](#39-sales-call-specific-findings)
   - [3.10 Phase 3 Recommendation](#310-phase-3-recommendation)
4. [Phase 4: Multilingual Mastery](#4-phase-4-multilingual-mastery)
   - [4.1 The Code-Switching Problem](#41-the-code-switching-problem)
   - [4.2 AI4Bharat IndicConformer — The Indian Language Champion](#42-ai4bharat-indicconformer--the-indian-language-champion)
   - [4.3 Dolphin ASR — The Eastern Language Specialist](#43-dolphin-asr--the-eastern-language-specialist)
   - [4.4 Meta Omnilingual ASR — The New Giant](#44-meta-omnilingual-asr--the-new-giant)
   - [4.5 Meta SeamlessM4T v2 — Great But License Problem](#45-meta-seamlessm4t-v2--great-but-license-problem)
   - [4.6 Shunya Labs Pingala / Zero STT — Hinglish Specialist](#46-shunya-labs-pingala--zero-stt--hinglish-specialist)
   - [4.7 Other Indian-Focused Models](#47-other-indian-focused-models)
   - [4.8 Models We Eliminated](#48-models-we-eliminated)
   - [4.9 What Do Companies Like Gong Actually Do?](#49-what-do-companies-like-gong-actually-do)
   - [4.10 The Language-Routing Pipeline — Our Recommended Approach](#410-the-language-routing-pipeline--our-recommended-approach)
   - [4.11 Phase 4 Recommendation](#411-phase-4-recommendation)
5. [The Combined Architecture — Phases 3 + 4 Together](#5-the-combined-architecture--phases-3--4-together)
6. [Hardware & Memory Budget](#6-hardware--memory-budget)
7. [Expected Improvements — Before vs After](#7-expected-improvements--before-vs-after)
8. [Which Phase First?](#8-which-phase-first)
9. [Complete Model Comparison Tables](#9-complete-model-comparison-tables)
10. [Sources](#10-sources)

---

## 1. What Changed? The Torch Breakthrough

### The Problem We Had Before

Imagine you own a workshop with a very specific power supply (PyTorch 2.1.1). Your audio enhancer machine (resemble-enhance) was hardwired to ONLY work with that exact voltage. But all the fancy new tools you wanted — Canary-Qwen, WhisperX — needed a different, higher voltage (PyTorch 2.6+).

You were stuck. You couldn't upgrade the power supply without breaking the enhancer.

### What We Did

We moved the enhancer to its own separate workshop (a microservice) with its own dedicated power supply. Now:

- The **enhancer** runs independently in `microservices/resemble-enhance/` with its own PyTorch 2.1.1
- The **main application** is free to use ANY PyTorch version
- All the previously-blocked models are now available to us

### What This Unlocked

| Model | Before (torch 2.1.1) | After (torch free) |
|-------|----------------------|---------------------|
| **Canary-Qwen 2.5B** | BLOCKED (needs torch 2.6+) | **Available** |
| **WhisperX** | BLOCKED (needs torch 2.5.1+) | Available (but not recommended — see below) |
| **AI4Bharat IndicConformer** | BLOCKED (NeMo fork needs newer torch) | **Available** |
| **Meta Omnilingual ASR** | BLOCKED | **Available** |
| **ctc-forced-aligner** | Already worked | Still works |
| **faster-whisper** | Already worked | Still works |

This is a game-changer. Let's explore what's now possible.

---

## 2. Understanding the Key Terms

If you've already read the old `PHASE3_RESEARCH.md`, you know these. Here's a refresher plus new terms:

### WER (Word Error Rate)

The standard way to measure how good a speech-to-text model is.

**How it works:** If you say 100 words, and the model gets 7 wrong (substitutions, missing words, or extra words), that's **7% WER**. Lower is better.

- **2-3% WER** = Excellent (like a professional human transcriber)
- **5-7% WER** = Very good (production quality)
- **10-15% WER** = Okay (useful but noticeable errors)
- **20%+ WER** = Poor (many errors, especially names and numbers)
- **35%+ WER** = Bad (typical for Tamil/Hindi on Whisper — every 3rd word is wrong)

### CER (Character Error Rate)

Like WER but for individual characters instead of words. Used for languages where "word" boundaries are unclear (like Chinese or Tamil script). A model with 10% CER means it gets about 1 in 10 characters wrong.

### RTFx (Real-Time Factor multiplied)

How fast the model processes audio compared to real-time.

- **RTFx = 1** means a 60-minute recording takes 60 minutes to transcribe
- **RTFx = 100** means a 60-minute recording takes only 36 seconds
- **RTFx = 418** (Canary-Qwen on GPU) means 60 minutes in about 8.6 seconds
- **RTFx = 3,332** (Parakeet on GPU) means 60 minutes in about 1.1 seconds!

On CPU (what we currently use), Whisper Medium runs at roughly **RTFx 0.3-1** — a 60-minute call can take 60-180 minutes.

### Code-Switching

When a speaker switches between languages mid-conversation. Example from an Indian sales call:

> "So the pricing structure, naan onga kitta solla vendiyathu..." (switching from English to Tamil)
> "Aap ko ek discount milega, basically twenty percent off" (switching from Hindi to English)

This is extremely common in Indian business conversations and very hard for AI models.

### Forced Alignment

After a model transcribes audio, forced alignment goes back and figures out **exactly when each word was spoken** — down to the millisecond. Think of it like:

1. **Without alignment:** "The meeting is at three" → spoken sometime between 0:05 and 0:08
2. **With alignment:** "The" (0:05.100) "meeting" (0:05.300) "is" (0:05.650) "at" (0:05.800) "three" (0:05.950)

This enables karaoke-style highlighting and precise subtitle generation.

### Language Identification (LID)

A small AI model that listens to a clip of audio and tells you what language it's in. Like Shazam for languages. We use this to route different parts of a conversation to the right ASR model.

### CTranslate2

A specialized C++ engine for running AI models fast. While PyTorch is flexible (good for training), CTranslate2 is optimized purely for speed. It's like the difference between a Swiss Army knife (PyTorch) and a laser cutter (CTranslate2). This is what `faster-whisper` uses under the hood.

### INT8 Quantization

A technique to make AI models smaller and faster by reducing the precision of their internal numbers. Think of it like rounding prices from $14.73829 to $15 — you lose a tiny bit of precision but save a lot of space. INT8 models use ~4x less memory with barely any accuracy loss.

### VRAM (Video RAM)

Memory on a GPU (graphics card). AI models need to be loaded into VRAM to run on a GPU. A typical consumer GPU has 8-24 GB of VRAM. The more models you want to run simultaneously, the more VRAM you need.

### NeMo

NVIDIA's toolkit for building and running speech AI models. Think of it as NVIDIA's "recipe book" for speech recognition. Many top models (Canary-Qwen, Parakeet, IndicConformer) are built with NeMo. It requires newer PyTorch versions, which is why we couldn't use it before.

---

## 3. Phase 3: World-Class ASR

### What We're Trying to Achieve

Phase 3 upgrades our transcription engine. Right now we use **Whisper Medium** via the `openai-whisper` library. It's decent but:

- **Slow** on CPU (takes 1-3x the audio length to process)
- **7.9% WER** on clean English (good, not great)
- **No word-level timestamps** (only sentence-level)
- **~17.7% WER on phone calls** (much worse than clean audio)
- **~35-40% WER on Tamil/Hindi** (roughly every 3rd word is wrong)

Phase 3 aims to:
1. **4x faster** transcription speed
2. **Better accuracy** (5.6-7.0% WER depending on model)
3. **Word-level timestamps** for each spoken word
4. **Foundation** for Phase 4 multilingual improvements

---

### 3.1 The ASR Leaderboard — Who's Who in 2026

The [Open ASR Leaderboard](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard) is like the Olympics for speech-to-text models. Here's the current scoreboard:

| Rank | Model | Creator | Avg WER | Params | CPU OK? | Languages | License |
|------|-------|---------|---------|--------|---------|-----------|---------|
| #1 | **Canary-Qwen 2.5B** | NVIDIA | **5.63%** | 2.5B | No | English only | CC-BY-4.0 |
| #2 | **Granite Speech 8B** | IBM | 5.85% | 8B | No | 5 European | Apache 2.0 |
| #3 | **Phi-4 Multimodal** | Microsoft | 6.14% | 5.6B | No | Multilingual | MIT |
| #4 | **Parakeet TDT 0.6B v3** | NVIDIA | 6.32% | 600M | No | 25 European | CC-BY-4.0 |
| #5 | **Whisper large-v3** | OpenAI | 6.43% | 1.55B | Slow | 99 langs | MIT |
| — | **Whisper large-v3-turbo** | OpenAI | ~7.0% | 809M | **Yes** | 99 langs | MIT |
| — | **Whisper medium (ours)** | OpenAI | ~7.9% | 769M | Yes | 99 langs | MIT |

**Key takeaway:** The top 4 models are all GPU-only and mostly English-focused. Whisper remains the only top model that works on CPU and supports 99 languages.

---

### 3.2 NVIDIA Canary-Qwen 2.5B — The Ferrari (Now Available!)

**The most accurate open-source ASR model in the world.**

Previously blocked by our torch 2.1.1 constraint — now available since we moved resemble-enhance to a microservice.

#### What Makes It Special

Think of most ASR models as a translator who listens and writes down what they hear. Canary-Qwen is different — it's a translator who also UNDERSTANDS what they hear.

**The architecture (in simple terms):**
1. **Ears** (FastConformer encoder): Listens to audio, converts sound waves into a representation the AI can understand
2. **Brain** (Qwen3-1.7B LLM): A full language model that processes what the ears heard. This is the same type of AI as ChatGPT, but built into the speech model

This "ears + brain" design means Canary-Qwen:
- Understands context — knows "Zoho" is a company name, not "so ho"
- Has fewer hallucinations — 1.3x fewer repeated phrases than Whisper
- Can also summarize calls and extract action items (Phase 7 potential!)
- Produces natural punctuation and capitalization

#### The Numbers

| Metric | Value |
|--------|-------|
| **Average WER** | **5.63%** (#1 globally) |
| Clean speech (LibriSpeech) | 1.60% |
| Financial calls (SPGISpeech) | 1.90% |
| Meetings (AMI) | 10.18% |
| Podcasts (GigaSpeech) | 9.41% |
| Speed on GPU | 418 RTFx (60-min call in ~8.6 seconds) |
| GPU memory needed | ~8 GB |
| Training data | 234,000 hours of English speech |

#### Noise Robustness

One of the most impressive things — Canary-Qwen barely degrades on noisy audio:

| Condition | WER |
|-----------|-----|
| Clean | 1.60% |
| SNR 10dB (moderate noise) | 2.41% |
| SNR 5dB (noisy) | 4.08% |
| SNR 0dB (very noisy) | 9.83% |

Compare this to Whisper, which goes from ~2.7% to ~17.7% on telephone audio — a 556% degradation!

#### The Critical Limitation

**English only.** Zero Tamil. Zero Hindi. The encoder saw some German/French/Spanish during training, but no Indian languages at all.

For our project (English + Tamil/Hindi sales calls), Canary-Qwen can only handle the English portions. We need a different model for Tamil and Hindi (that's Phase 4).

#### When to Use It

- When you have a GPU with at least 8 GB VRAM
- For English-only or English-dominant sales calls
- Alongside pyannote diarization (both fit in a 24 GB GPU)

---

### 3.3 Whisper large-v3-turbo via faster-whisper — The Reliable Upgrade

**The best "bang for buck" upgrade we can make right now, even without a GPU.**

#### What Is It?

Two separate upgrades combined:

1. **Whisper large-v3-turbo** = A newer, smarter Whisper model (809M params vs our current 769M medium)
2. **faster-whisper** = A faster engine to run Whisper models (uses CTranslate2 instead of PyTorch)

Think of it like upgrading both the car engine (turbo model) AND the transmission (faster-whisper engine).

#### Why "Turbo"?

OpenAI took their best model (large-v3, 1.55B parameters) and compressed it: they reduced the decoder from 32 layers to just 4 layers. The result:

- **3x faster** than large-v3 with only ~0.6% accuracy loss
- **809 million parameters** (vs 769M for medium — similar size, better accuracy)
- Works on **CPU** (unlike Canary-Qwen)

#### Why "faster-whisper"?

The `openai-whisper` library runs on PyTorch. `faster-whisper` converts the model to CTranslate2 format and runs it 4x faster with less memory:

| Feature | openai-whisper (current) | faster-whisper |
|---------|-------------------------|----------------|
| Speed | 1x baseline | **4x faster** |
| Memory | Higher | Lower (~1.5 GB for turbo on CPU) |
| INT8 quantization | No | **Yes** (even faster, tiny accuracy loss) |
| Batched inference | No | **Yes** (12.5x speedup on GPU) |
| PyTorch needed? | Yes | **No** (CTranslate2 is standalone) |
| Accuracy | Same | **Same** |

#### The Numbers

| Metric | Whisper Medium (current) | large-v3-turbo (faster-whisper) |
|--------|--------------------------|--------------------------------|
| Average WER | ~7.9% | **~7.0%** |
| Clean speech | ~4.5% | ~2.7% |
| Telephony | ~20%+ | ~17.7% |
| Tamil | ~35-40% | ~30-37% |
| Hindi | ~25-30% | ~20-25% |
| Languages | 99 | 99 |
| CPU speed | 0.3-1 RTFx | **1.2-4 RTFx** |
| Memory (CPU) | ~1.5 GB | **~1.5 GB (INT8)** |

**Bottom line:** Same memory, same languages, but faster and more accurate. Zero new dependencies needed.

---

### 3.4 IBM Granite Speech 3.3 — The Tank (New!)

A brand new model from IBM, released in 2025. Available in two sizes: 8B and 2B.

#### Why "The Tank"?

Because it has the **best noise robustness** of any model tested. While other models crumble on noisy audio, Granite barely flinches:

- Only **7.54% degradation** from clean to noisy conditions
- Compare: Whisper degrades by **556%** on telephone audio
- This is extremely relevant for sales calls recorded over phone lines

#### The Two Sizes

| Feature | Granite 8B | Granite 2B |
|---------|-----------|-----------|
| Accuracy (OpenASR) | 5.85% (#2!) | ~6-7% (estimated) |
| GPU needed? | Yes (16+ GB VRAM) | **CPU possible** |
| Languages | English, French, German, Spanish, Portuguese | Same |
| Tamil/Hindi | **No** | **No** |
| License | Apache 2.0 | Apache 2.0 |

#### Why We're Not Using It

Same problem as Canary-Qwen: **no Tamil or Hindi support**. Also, the 8B model is huge (needs 16+ GB VRAM). The 2B model is interesting for CPU deployment but still doesn't help with our multilingual needs.

**Worth watching** for a future English-only upgrade, especially if noise robustness matters more than raw WER.

---

### 3.5 Other Notable Models

#### NVIDIA Parakeet TDT 0.6B v3 — The Speed Demon

- **3,332 RTFx** — processes 1 hour of audio in about 1 second on GPU
- 600M params, small and efficient
- 25 European languages (v3), but **no Tamil/Hindi**
- Great accuracy (6.32% WER)
- If we ever need blazing-fast English transcription, this is it

#### Microsoft Phi-4 Multimodal — The Overachiever

- 5.6B params, 6.14% WER (#3 on leaderboard)
- Does speech + vision + text (multimodal)
- But it's like buying a Swiss Army knife when you only need scissors — loads vision capabilities you don't need, wasting GPU memory
- 12-16 GB VRAM estimated

#### Whisper large-v3 (not turbo)

- More accurate than turbo (~6.43% vs ~7.0%), but 3x slower
- On GPU: worth considering (the speed penalty is acceptable for batch processing)
- On CPU: too slow for production use
- MLCommons chose large-v3 (not turbo) for their official benchmark, citing superior accuracy

---

### 3.6 What is "Forced Alignment"? (Word-Level Timestamps)

Right now, our transcription gives timestamps for each **sentence segment** (e.g., "this segment runs from 0:05 to 0:12"). But we want timestamps for each **word**.

#### Why Does This Matter?

1. **Karaoke-style highlighting** — highlight each word as it's spoken in the UI
2. **Precise speaker attribution** — know exactly which words each speaker said (even mid-sentence)
3. **Subtitle generation** — create subtitles that appear at exactly the right time
4. **Search & navigation** — click a word in the transcript, jump to that exact moment in the audio

#### How It Works

Think of it like lip-syncing in reverse:

1. First, the ASR model transcribes the audio: "the meeting is at three o'clock"
2. Then the aligner takes the text and the audio, and figures out WHERE each word appears:
   - It uses a pronunciation model: "meeting" = /m/ /ee/ /t/ /ih/ /ng/
   - It matches each sound to the audio waveform
   - Result: "meeting" starts at 5.300s, ends at 5.620s

#### Our Choice: ctc-forced-aligner

We evaluated three alignment tools:

| Tool | Languages | Accuracy | Memory | Works With Our Stack? |
|------|-----------|----------|--------|----------------------|
| **ctc-forced-aligner** | **1,130** (incl. Tamil, Hindi) | ~50-100ms | Low (~300MB) | **Yes** |
| WhisperX alignment | ~30 | ~50-100ms | Higher | Yes (but see 3.7) |
| Montreal Forced Aligner (MFA) | Many | **10-20ms (best!)** | Medium | Difficult (needs Conda) |
| CrisperWhisper | Whisper langs | Best for Whisper | Medium | Requires Whisper variant |

**We chose ctc-forced-aligner because:**
- **1,130 languages** — native Tamil (`tam`) and Hindi (`hin`) support out of the box
- **Lightweight** — 5x less memory than WhisperX's alignment module
- **Simple** — standalone tool, no complex framework needed
- **Accuracy is good enough** — 50-100ms precision is fine for subtitles and word highlighting

The alignment model is Facebook's MMS (Massively Multilingual Speech) — a small model trained on 1,130 languages.

---

### 3.7 WhisperX — Why We're NOT Using It

WhisperX is a popular "all-in-one" package that bundles:
1. faster-whisper (ASR)
2. Wav2Vec2 alignment (word-level timestamps)
3. pyannote diarization (speaker identification)

**Sounds perfect, right?** But for our project, it creates redundancy:

| What WhisperX Does | What We Already Have |
|--------------------|--------------------|
| ASR via faster-whisper | We're adding faster-whisper directly |
| Speaker diarization | We already have pyannote (Phase 2) |
| Word alignment via Wav2Vec2 | We're using ctc-forced-aligner instead |

**The problems with WhisperX for us:**

1. **Redundant:** Duplicates two components we already have (faster-whisper + pyannote)
2. **Fewer languages for alignment:** WhisperX supports ~30 languages for alignment vs ctc-forced-aligner's 1,130
3. **No Tamil alignment model:** WhisperX has Hindi alignment but NOT Tamil — we'd need a custom model
4. **Timestamp accuracy:** Research shows WhisperX timestamps are "significantly off" compared to Montreal Forced Aligner
5. **Dependency conflicts:** Historical issues between WhisperX's faster-whisper and pyannote-audio versions

**Bottom line:** WhisperX is great for someone starting from scratch. For us, it would duplicate existing work and limit our Tamil support.

---

### 3.8 faster-whisper vs openai-whisper — Why Switch?

This is a no-brainer upgrade:

| Feature | openai-whisper (current) | faster-whisper (upgrade) |
|---------|--------------------------|--------------------------|
| **Speed** | 1x | **4x faster** |
| **Memory** | Higher | **Lower** |
| **INT8 quantization** | No | **Yes** |
| **Batched inference** | No | **Yes** (12.5x on GPU) |
| **PyTorch at runtime?** | Yes (loads entire PyTorch) | **No** (CTranslate2 is standalone) |
| **Accuracy** | Baseline | **Identical** |
| **Active development** | Minimal (OpenAI rarely updates) | Active community |
| **Model compatibility** | All Whisper models | All Whisper models |

**There is zero reason to keep using openai-whisper for inference.** The only use case for openai-whisper is fine-tuning (training), which CTranslate2 doesn't support — but we're not training models.

---

### 3.9 Sales Call Specific Findings

#### The Telephone Audio Problem

This is the most important finding for our use case. Whisper was trained primarily on podcasts, YouTube, and audiobooks — clean audio with good microphones. Phone calls are very different:

- **Narrowband audio** (8kHz vs 16kHz+ for podcasts)
- **Compression artifacts** from phone codecs
- **Background noise** (office, car, street)
- **Cross-talk** (two people talking simultaneously)
- **Echo and reverb** from speakerphone

**The impact is dramatic:**

| Model | Clean Audio WER | Telephone WER | Degradation |
|-------|----------------|---------------|-------------|
| Whisper large-v3-turbo | ~2.7% | ~17.7% | **556%** |
| Canary-Qwen 2.5B | 1.60% | ~4-10% | ~150-500% |
| IBM Granite 8B | ~2% | ~2.15% | **7.5%** |
| Deepgram Nova-3 (commercial) | ~3% | 5.3-6.8% | ~80-130% |

Our Phase 1 preprocessing (highpass filter, VAD, loudnorm) helps close the gap, but can't fully eliminate it.

#### What Commercial Services Do Better

The top commercial services (Gong, Rev.ai, Deepgram) don't use better models — they use **better training data**:

- **Rev.ai:** Trained on 3M+ hours of real-world noisy audio. No preprocessing. Model handles noise.
- **Deepgram:** Trains specifically on call center recordings. Their "Noise Reduction Paradox" finding: training on noise > cleaning noise.
- **Gong:** Proprietary models trained on millions of actual sales call hours.

**The lesson:** The competitive advantage isn't the model architecture — it's the training data. Off-the-shelf models trained on clean audio will always underperform on phone calls.

#### OpenAI's Tamil/Hindi Numbers Are Misleading

OpenAI reports ~16.9% WER for both Tamil and Hindi on the FLEURS benchmark. This sounds reasonable, but it's artificially deflated:

- The text normalization for Brahmic scripts breaks words into consonant sequences before measuring WER
- This makes errors look smaller than they really are
- **Real-world Tamil WER is ~35-40%** (every 3rd word is wrong)
- **Real-world Hindi WER is ~25-30%**

---

### 3.10 Phase 3 Recommendation

#### What To Build

**Step 1 — Immediate (CPU, no new hardware):**

| Change | What | Why |
|--------|------|-----|
| Replace ASR engine | `openai-whisper` → `faster-whisper` | 4x speed, same accuracy, less memory |
| Upgrade model | `medium` → `large-v3-turbo` | Better accuracy (~7.0% vs ~7.9% WER) |
| Add word timestamps | Install `ctc-forced-aligner` | Word-level timestamps for 1,130 languages |

**Step 2 — When GPU is available:**

| Change | What | Why |
|--------|------|-----|
| Add English ASR | Install Canary-Qwen 2.5B via NeMo | Best English accuracy (5.63% WER) |
| Upgrade Whisper | `large-v3-turbo` → `large-v3` on GPU | ~0.6% better WER, acceptable speed |
| Use Canary dual-mode | LLM mode for Phase 7 intelligence | Same model for transcription + summarization |

#### Why This Order

1. **Step 1 is zero-risk** — faster-whisper produces identical output to openai-whisper, just faster
2. **Step 1 needs no new hardware** — works on our current CPU setup
3. **Step 2 adds Canary-Qwen only for English** — Tamil/Hindi stay on faster-whisper (or upgrade in Phase 4)
4. **ctc-forced-aligner is independent** — works with any ASR engine, so it benefits both steps

---

## 4. Phase 4: Multilingual Mastery

### What We're Trying to Achieve

Our primary use case is Indian sales calls where speakers mix English with Tamil or Hindi. Phase 4 aims to:

1. **Dramatically improve Tamil WER** — from ~35-40% to ~18-25% (nearly 2x better)
2. **Dramatically improve Hindi WER** — from ~25-30% to ~13-15% (nearly 2x better)
3. **Handle code-switching** — when speakers switch between English and Tamil/Hindi mid-conversation
4. **Auto-detect language** — know which language each segment is in

---

### 4.1 The Code-Switching Problem

Code-switching is when speakers switch languages during a conversation. In Indian business contexts, this is the norm, not the exception:

> **English→Tamil:** "So the quarterly targets, naan check pannuven tomorrow and I'll send you the numbers"
> **English→Hindi:** "We need to finalize the deal, aap ko ek discount milega, basically twenty percent off"

#### Why It's So Hard for AI

1. **Most models expect ONE language.** Whisper detects the language from the first 30 seconds and applies it to everything. If it picks "English," all the Tamil words get garbled.

2. **Training data is scarce.** There are very few code-switched audio datasets for Tamil-English or Hindi-English. Models can't learn what they've never seen.

3. **Grammar changes.** Code-switching isn't random — it follows rules. A bilingual speaker doesn't just sprinkle in random words. The AI needs to understand these patterns.

4. **No off-the-shelf solution exists for Tamil-English.** This is still an active research problem in 2026. Hindi-English has more attention (Hinglish is a massive market), but Tamil-English is underserved.

#### The Practical Reality

Even companies like Gong don't truly handle code-switching. If a call is 51% Polish and 49% English, Gong transcribes the entire call in Polish. They identify the majority language and stick with it.

Our approach is more sophisticated: we detect the language of each segment and route it to the best model for that language.

---

### 4.2 AI4Bharat IndicConformer — The Indian Language Champion

**The clear winner for Tamil and Hindi ASR.**

#### What Is AI4Bharat?

AI4Bharat is a research project from IIT Madras (Indian Institute of Technology, one of India's top universities) focused on building AI tools for Indian languages. They built dedicated speech models for all 22 officially recognized Indian languages.

#### The Model

**Full name:** `ai4bharat/indic-conformer-600m-multilingual`

**Architecture (in simple terms):**
- **Conformer encoder** — A type of "ears" that's especially good at understanding the acoustic patterns of Indian languages
- **Hybrid CTC + RNNT decoder** — Two different "brains" that work together. CTC is fast but less accurate; RNNT is slower but better. The hybrid uses both and picks the best output.
- Built on NVIDIA's NeMo framework

#### Per-Language Models

This is the killer feature. Instead of one model trying to handle all languages, AI4Bharat trained **separate specialized models** for each language:

| Language | Model | Estimated WER |
|----------|-------|---------------|
| **Tamil** | `indicconformer_stt_ta_hybrid_ctc_rnnt_large` | **~18-25%** |
| **Hindi** | `indicconformer_stt_hi_hybrid_ctc_rnnt_large` | **~13-15%** |
| All 22 Indian languages | `indic-conformer-600m-multilingual` | Varies |

Compare this to Whisper's Tamil WER of 35-40% — IndicConformer is roughly **twice as accurate**.

#### Strengths

- **Best open-source Tamil and Hindi accuracy** — purpose-built for Indian languages
- **MIT license** — fully permissive, commercial use OK
- **600M parameters** — small enough to run on modest hardware (~2-3 GB VRAM)
- **Can run on CPU** (slower, but feasible)
- **Active maintenance** by IIT Madras

#### Limitations

- **No native code-switching.** The model requires you to specify the language at inference time (`model(wav, "ta", "ctc")`). It expects monolingual input.
- **Requires AI4Bharat's NeMo fork** — not standard NeMo, need to clone their repo
- **Published WER numbers are hard to pin down** — their Vistaar benchmark paper focuses on IndicWhisper, not IndicConformer directly

#### What This Means for Us

We route Tamil segments to the Tamil model, Hindi segments to the Hindi model, and English segments to faster-whisper or Canary-Qwen. Each language gets its best possible model.

---

### 4.3 Dolphin ASR — The Eastern Language Specialist

#### What Is It?

Built by Dataocean AI (a Chinese AI company) and Tsinghua University. Dolphin focuses on **Eastern languages** — 40 languages including Tamil, Hindi, Chinese, Vietnamese, Thai, and more.

#### The Architecture

Think of it as "Whisper's smarter cousin for Asian languages":
- **E-Branchformer encoder** — an improved version of the architecture many ASR models use
- **Transformer decoder** — similar to Whisper's approach
- Trained on **210,000+ hours** of multilingual audio (massive!)

#### Available Sizes

| Size | Parameters | Use Case |
|------|-----------|----------|
| dolphin-base | 140M | CPU, quick experiments |
| dolphin-small | 372M | CPU, reasonable accuracy |
| dolphin-medium | 910M | GPU recommended |
| dolphin-large | 1,679M | GPU required, best accuracy |

#### Performance Comparison (vs Whisper large-v3)

| Language | Whisper large-v3 | Dolphin large | Improvement |
|----------|-----------------|---------------|-------------|
| Tamil | 85.6% | **43.0%** | 50% better |
| Hindi | 45.4% | **24.9%** | 45% better |

**Important caveat:** These numbers are from Dolphin's own proprietary test set, not standard benchmarks like FLEURS. The absolute numbers (43% Tamil WER) are still quite high, and the test set may be particularly challenging.

#### Strengths

- **Huge improvement over Whisper** for Eastern languages (40-70% relative improvement claimed)
- **Apache 2.0 license** — fully permissive
- **Multiple sizes** — choose speed vs accuracy
- **210K hours of training data** — massive

#### Limitations

- **Tamil WER is still high** (~43% even with the large model)
- **No proven code-switching support** — uses language tokens for dialect disambiguation, not mid-sentence switching
- **Requires ESPnet framework** — yet another framework to install and manage
- **Benchmarks are on proprietary data** — hard to compare directly with other models

#### Our Verdict

Good as a **fallback** for languages that neither IndicConformer nor faster-whisper handle well. But for Tamil and Hindi specifically, IndicConformer appears to be better.

---

### 4.4 Meta Omnilingual ASR — The New Giant

#### What Is It?

Released by Meta (Facebook) in November 2025. The most ambitious multilingual ASR project ever attempted:

- **1,600+ languages** (yes, one thousand six hundred)
- Trained on **4.3 million hours** of audio
- The 7B variant has the largest speech encoder ever built

#### Why It Matters

Think of it as the "Google Translate of speech recognition." While most models support 25-100 languages, Omnilingual covers 1,600+ — including rare Indian languages like Kui, Chhattisgarhi, and Maithili that no other model touches.

#### Available Sizes

| Size | Parameters | GPU Needed? |
|------|-----------|-------------|
| 300M | 300 million | Small GPU or CPU |
| 1B | 1 billion | Yes |
| 3B | 3 billion | Yes (8+ GB) |
| 7B | 7 billion | Yes (large GPU) |

#### Performance

- CER below 10% for 78% of its 1,600+ languages
- Outperforms Whisper large-v3 and MMS on low-resource languages
- Hindi is included; Tamil is supported but specific numbers need verification

#### Strengths

- **Apache 2.0 license** — fully permissive
- **State-of-the-art for rare languages** — if you ever need Odia, Maithili, or Chhattisgarhi
- **Massive scale** — Meta's most advanced multilingual model

#### Limitations

- **Very new** (Nov 2025) — less battle-tested
- **No documented code-switching** — uses per-language decoding
- **7B model needs serious hardware**
- Uses CER (not WER) for evaluation, making comparison harder

#### Our Verdict

Interesting as a **future option**, especially if we expand beyond Tamil/Hindi. But for now, IndicConformer is more proven for our specific languages. Worth watching.

---

### 4.5 Meta SeamlessM4T v2 — Great But License Problem

#### What Is It?

Meta's "seamless" translation model that handles speech-to-text in 96 languages. A 2025 research study found that **fine-tuned SeamlessM4T outperformed everything else on Tamil-English code-switching**.

#### Why We Can't Use It

**License: CC-BY-NC-4.0** — "Non-Commercial" use only.

This means:
- You can use it for research or personal projects
- You **cannot** use it in a commercial product
- Since our platform is for sales call transcription (commercial), this is a dealbreaker

It's frustrating because SeamlessM4T is arguably the best model for our exact use case (code-switched Indian language sales calls). But the license makes it off-limits.

---

### 4.6 Shunya Labs Pingala / Zero STT — Hinglish Specialist

#### What Is It?

An Indian startup (Shunya Labs) built two notable models:

1. **Pingala V1** — General ASR supporting 204 languages, 2.94% WER on English (competitive with Canary-Qwen!)
2. **Zero STT Hinglish** — The world's first dedicated **Hindi-English code-switching** model

#### Why Zero STT Hinglish Is Interesting

This is the only model we found that natively handles Hindi-English code-switching. It was specifically designed for "Hinglish" — the natural mix of Hindi and English that millions of Indians speak daily.

Example: "Aap ko ek discount milega, basically twenty percent off" — the model handles this natively, producing mixed Hindi-English text from a single forward pass.

#### The Problem

**License: RAIL-M (Responsible AI License - Modified)**

This is restrictive:
- **No redistribution** — you can't share the model with others
- **No derivative works** — you can't fine-tune or modify it
- The PyPI package is MIT, but the **model weights** are RAIL-M

Also: **No Tamil-English model exists** from Shunya Labs. Only Hindi-English.

#### Our Verdict

Worth evaluating for Hindi-English sales calls if the license is acceptable for our use. But it doesn't solve the Tamil-English problem, and the restrictive license is concerning for a self-hosted platform.

---

### 4.7 Other Indian-Focused Models

#### Sarvam AI (Saarika / Shuka) — Indian Language Startup

- Claims "higher accuracy across 10 Indian languages"
- Supports code-mixed speech
- **But: API-only, not self-hostable.** Since we prefer self-hosted models, this doesn't work for us.

#### Krutrim Dhwani-1 (by Ola, the ride-hailing company)

- Supports Bengali, Gujarati, Hindi, Kannada, Malayalam, Marathi, Tamil, Telugu
- Primarily a speech **translation** model, not pure ASR
- Limited benchmark data available

#### Meta MMS (Massively Multilingual Speech)

- 1,107 languages for ASR
- **Superseded** by Meta's newer Omnilingual ASR (Nov 2025)
- **CC-BY-NC license** (non-commercial) — same problem as SeamlessM4T

---

### 4.8 Models We Eliminated

| Model | Reason for Elimination |
|-------|----------------------|
| **Google USM / Chirp** | Not self-hostable — API only via Google Cloud |
| **Meta SeamlessM4T v2** | Non-commercial license (CC-BY-NC-4.0) |
| **Meta MMS** | Superseded by Omnilingual; non-commercial license |
| **Sarvam AI Saarika** | API only, not self-hostable |
| **ElevenLabs Scribe** | Closed-source commercial service |
| **Deepgram Nova-3** | Closed-source commercial service |

---

### 4.9 What Do Companies Like Gong Actually Do?

We researched how enterprise sales intelligence platforms handle multilingual transcription:

#### Gong

- Supports 70+ languages with proprietary in-house models
- Models are "specifically optimized for revenue conversations" (trained on sales call data)
- **Does NOT handle code-switching** — if multiple languages are spoken, the transcript is in the majority language (e.g., 51% Hindi = Hindi transcript)
- Competitive advantage: millions of hours of proprietary sales call training data

#### Deepgram

- Telephony WER of 5.3-6.8% (dramatically better than Whisper's 17.7%)
- Trains specifically on call center recordings
- Their finding: training on noisy data > cleaning noisy audio

#### Speechmatics

- Offers "advanced bilingual models purpose-built for code-switched conversations"
- One of the few commercial services that claims real code-switching support

#### Reverie (India-specific)

- API "tuned for regional pronunciation and code-switching"
- Supports 11 Indian languages
- Cloud or on-premises deployment available

**The key insight:** Enterprise players train custom models on massive proprietary datasets of actual sales calls. The competitive advantage is the training data, not the model architecture. No off-the-shelf open-source model matches their performance on real sales calls.

---

### 4.10 The Language-Routing Pipeline — Our Recommended Approach

Since no single model handles English + Tamil + Hindi code-switching well, we use a **routing approach**: detect what language each segment is in, then send it to the best model for that language.

#### How It Works (Step by Step)

```
Step 1: Audio comes in
    |
    v
Step 2: Phase 1 preprocessing (clean audio — already built)
    |
    v
Step 3: Phase 2 diarization (identify speakers — already built)
    |
    Output: Audio split into segments, each labeled with a speaker
    |
    v
Step 4: [NEW] Language Identification
    |
    For each segment, a small LID model listens and says:
    "This is English" / "This is Tamil" / "This is Hindi" / "Not sure"
    |
    v
Step 5: [NEW] Route to Best ASR Model
    |
    ├── English segments → Canary-Qwen 2.5B (GPU) or faster-whisper (CPU)
    ├── Tamil segments → IndicConformer Tamil large model
    ├── Hindi segments → IndicConformer Hindi large model
    └── Mixed/unclear → Dolphin or IndicConformer multilingual
    |
    v
Step 6: Phase 3 forced alignment (word timestamps — ctc-forced-aligner)
    |
    v
Step 7: Merge everything back together
    |
    Output: Complete transcript with Speaker + Language + Word Timestamps
```

#### The Language Detection Model

We'll use one of these lightweight models to identify the language of each segment:

| Model | Languages | Size | Notes |
|-------|-----------|------|-------|
| **MMS LID** (Meta) | 4,000+ | ~1 GB | Most comprehensive |
| **Silero LID** | ~100 | Very small | Fastest, but fewer languages |
| **SpeechBrain LID** | ~100 | Small | Good accuracy |

#### Advantages of This Approach

1. **Each language gets its best possible model** — Tamil gets a Tamil specialist, not a general-purpose model
2. **Modular** — upgrade the Tamil model without touching the English pipeline
3. **No single model needs to handle code-switching** — we handle it at the pipeline level
4. **Incrementally improvable** — add better models as they become available

#### Limitations

1. **Language detection errors cascade** — if the detector thinks a Tamil segment is English, it goes to the wrong model and produces garbage
2. **Mid-sentence switching not handled** — if a single segment contains "so the pricing, naan check pannuven," the whole segment goes to one model
3. **More models = more memory** — loading Tamil, Hindi, and English models simultaneously
4. **More complex pipeline** — more things that can break

#### How We Handle the Limitations

- **Short diarized segments** reduce mid-sentence switching — most segments are 2-10 seconds, shorter segments are more likely to be monolingual
- **Confidence thresholds** — if the language detector isn't sure, send to the multilingual fallback model
- **Model loading on demand** — load Tamil model only when Tamil is detected, don't keep all models in memory permanently

---

### 4.11 Phase 4 Recommendation

#### Tier 1 — Implement First (Biggest Impact)

| Component | Model | Purpose | License | Memory |
|-----------|-------|---------|---------|--------|
| **Language Detection** | MMS LID or Silero LID | Identify language per segment | Apache 2.0 | ~1 GB |
| **Tamil ASR** | IndicConformer Tamil large | Best Tamil accuracy | MIT | ~2-3 GB |
| **Hindi ASR** | IndicConformer Hindi large | Best Hindi accuracy | MIT | ~2-3 GB |
| **English ASR** | faster-whisper large-v3-turbo (or Canary-Qwen on GPU) | Best English accuracy | MIT / CC-BY-4.0 | ~1.5-8 GB |

#### Tier 2 — Add Later

| Component | Model | Purpose | License |
|-----------|-------|---------|---------|
| **Hindi-English code-switch** | Shunya Zero STT Hinglish | Native Hinglish for mixed segments | RAIL-M (evaluate) |
| **Multilingual fallback** | Dolphin large | Eastern language fallback | Apache 2.0 |
| **Rare languages** | Meta Omnilingual 1B | Low-resource language coverage | Apache 2.0 |

#### Tier 3 — Future / Research

| Component | Model | Purpose |
|-----------|-------|---------|
| **Fine-tuned CS model** | Whisper large-v3 fine-tuned on synthetic Tamil-English CS data | True mid-sentence code-switching |
| **Tamil-English CS** | Wait for dedicated model or fine-tune IndicConformer | Currently no good off-the-shelf option |

---

## 5. The Combined Architecture — Phases 3 + 4 Together

Here's how everything fits together when both phases are implemented:

```
┌─────────────────────────────────────────────────────────────┐
│                     AUDIO INPUT                             │
│         (file upload, URL, or YouTube)                      │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           v
┌─────────────────────────────────────────────────────────────┐
│              PHASE 1: Audio Preprocessing                   │
│  Resample → Highpass → VAD → Loudnorm → Conditional steps  │
│                    (Already built)                          │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           v
┌─────────────────────────────────────────────────────────────┐
│           PHASE 2: Speaker Diarization                      │
│  pyannote speaker-diarization-3.1 → Speaker-labeled segments│
│                    (Already built)                          │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           v
┌─────────────────────────────────────────────────────────────┐
│      PHASE 4: Language Detection (per segment)              │
│  MMS LID or Silero LID → Language tag per segment           │
│          (en / ta / hi / mixed / unknown)                   │
└──────────────────────────┬──────────────────────────────────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
              v            v            v
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  English     │ │    Tamil     │ │    Hindi     │
│ segments     │ │  segments    │ │  segments    │
│              │ │              │ │              │
│ Canary-Qwen  │ │IndicConformer│ │IndicConformer│
│ 2.5B (GPU)   │ │ Tamil large  │ │ Hindi large  │
│    — or —    │ │              │ │              │
│faster-whisper│ │              │ │              │
│ turbo (CPU)  │ │              │ │              │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       └────────────────┼────────────────┘
                        │
                        v
┌─────────────────────────────────────────────────────────────┐
│       PHASE 3: Word-Level Alignment                         │
│  ctc-forced-aligner (MMS model, 1130 languages)             │
│  Produces word-level timestamps for each word               │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           v
┌─────────────────────────────────────────────────────────────┐
│              MERGED TRANSCRIPT                              │
│                                                             │
│  Speaker A (English): "So the quarterly targets..."         │
│  Speaker B (Tamil): "நான் check பண்ணுவேன் tomorrow..."       │
│  Speaker A (English): "Perfect, let's finalize..."          │
│                                                             │
│  Each word has: speaker + language + start_time + end_time  │
└─────────────────────────────────────────────────────────────┘
```

---

## 6. Hardware & Memory Budget

### CPU-Only Setup (What We Have Now)

| Component | RAM Usage | Notes |
|-----------|----------|-------|
| faster-whisper large-v3-turbo (INT8) | ~1.5 GB | Main English ASR |
| ctc-forced-aligner (MMS) | ~300 MB | Word timestamps |
| Silero VAD | ~50 MB | Already loaded |
| pyannote diarization | ~2 GB | Already loaded |
| IndicConformer Tamil | ~2-3 GB | Load on demand |
| IndicConformer Hindi | ~2-3 GB | Load on demand |
| MMS LID | ~1 GB | Language detection |
| **Total (all loaded)** | **~10-13 GB RAM** | **Feasible on 16 GB machine** |

Note: IndicConformer models can be loaded/unloaded on demand — no need to keep both in memory if RAM is tight.

### GPU Setup (Future — RTX 3090/4090 with 24 GB VRAM)

| Component | VRAM | Notes |
|-----------|------|-------|
| Canary-Qwen 2.5B (English) | ~8 GB | Primary English ASR |
| pyannote diarization | ~6-8 GB | Speaker identification |
| faster-whisper large-v3-turbo | ~2-3 GB | Multilingual fallback |
| IndicConformer (Tamil or Hindi) | ~2-3 GB | Load the needed one |
| ctc-forced-aligner | ~300 MB | Word timestamps |
| **Total** | **~18-22 GB** | **Fits in 24 GB** |

This leaves 2-6 GB headroom for Phase 7 intelligence extraction (Canary-Qwen's built-in LLM mode reuses the same 8 GB — no additional VRAM needed).

---

## 7. Expected Improvements — Before vs After

### Phase 3 Improvements (ASR Engine Upgrade)

| Metric | Before (Whisper Medium) | After (faster-whisper turbo) | Improvement |
|--------|------------------------|------------------------------|-------------|
| English WER (clean) | ~7.9% | ~7.0% | ~11% better |
| English WER (phone) | ~20%+ | ~17.7% | ~12% better |
| Transcription speed (CPU) | 0.3-1 RTFx | 1.2-4 RTFx | **4x faster** |
| Word timestamps | No (sentence only) | **Yes** (per word) | New capability |
| Memory usage | ~1.5 GB | ~1.5 GB (INT8) | Same |

With Canary-Qwen on GPU:

| Metric | Value |
|--------|-------|
| English WER (clean) | **5.63%** (29% better than medium) |
| English WER (noisy) | ~4-10% |
| Speed | 418 RTFx (60 min in 8.6 sec) |
| Bonus | Can also summarize calls (Phase 7) |

### Phase 4 Improvements (Multilingual)

| Language | Before (Whisper Medium) | After (IndicConformer) | Improvement |
|----------|------------------------|------------------------|-------------|
| Tamil | ~35-40% WER | ~18-25% WER | **~50% better** |
| Hindi | ~25-30% WER | ~13-15% WER | **~50% better** |
| Code-switch detection | None | Per-segment routing | **New capability** |
| Language identification | None (auto-detect per file) | Per-segment detection | **New capability** |

### Combined Impact

A typical English-Tamil sales call (60% English, 40% Tamil) would see:

| Metric | Before | After Both Phases |
|--------|--------|-------------------|
| Overall WER estimate | ~25-30% | ~12-18% |
| English portions | ~7.9% | ~5.6-7.0% |
| Tamil portions | ~35-40% | ~18-25% |
| Speed (CPU) | 60-180 min for 60-min call | **15-50 min** |
| Speed (GPU) | N/A | **~30 seconds** |
| Word timestamps | No | Yes |
| Speaker + Language labels | Speaker only | Speaker + Language |

---

## 8. Which Phase First?

**Phase 3 first, then Phase 4.**

### Why Phase 3 Comes First

Think of it like upgrading a factory:

1. **Phase 3 is upgrading the main assembly line** — every product benefits
   - faster-whisper replaces the slow engine → everything transcribes faster
   - large-v3-turbo is more accurate → every language improves slightly
   - ctc-forced-aligner adds word timestamps → works for all languages

2. **Phase 4 is adding specialized workstations** — only certain products benefit
   - IndicConformer only helps Tamil and Hindi
   - Language routing only matters for multilingual calls
   - These specialized stations need the main line (Phase 3) to be working first

### The Implementation Order

| Step | Phase | What | Risk | Effort |
|------|-------|------|------|--------|
| 1 | 3 | Replace `openai-whisper` → `faster-whisper` | Low (identical output) | Medium |
| 2 | 3 | Upgrade model `medium` → `large-v3-turbo` | Low (just a model swap) | Small |
| 3 | 3 | Add `ctc-forced-aligner` for word timestamps | Low (additive feature) | Medium |
| 4 | 4 | Add language detection (MMS LID) | Medium (new component) | Medium |
| 5 | 4 | Add IndicConformer Tamil + Hindi | Medium (new framework) | Large |
| 6 | 4 | Build language-routing pipeline | High (complex integration) | Large |
| 7 | 3 | Add Canary-Qwen 2.5B (when GPU available) | Medium (NeMo setup) | Large |

Steps 1-3 can be done independently and give immediate value. Steps 4-6 build on top of that foundation.

---

## 9. Complete Model Comparison Tables

### Phase 3: English ASR Models

| Model | Avg WER | RTFx (GPU) | CPU Viable? | VRAM | Params | License | Our Pick? |
|-------|---------|------------|-------------|------|--------|---------|-----------|
| **Canary-Qwen 2.5B** | **5.63%** | 418 | No | ~8 GB | 2.5B | CC-BY-4.0 | **GPU primary** |
| IBM Granite 8B | 5.85% | ~31 | No | ~16 GB | 8B | Apache 2.0 | Too large |
| Microsoft Phi-4 | 6.14% | — | No | ~14 GB | 5.6B | MIT | Overkill (multimodal) |
| Parakeet TDT v3 | 6.32% | 3,332 | No | ~2 GB | 600M | CC-BY-4.0 | Speed champion |
| Whisper large-v3 | 6.43% | 68.56 | Slow | ~4 GB | 1.55B | MIT | GPU alternative |
| **Whisper large-v3-turbo** | ~7.0% | 216 | **Yes** | ~2 GB | 809M | MIT | **CPU primary** |
| Whisper medium (current) | ~7.9% | ~30 | Yes | ~1.5 GB | 769M | MIT | Replacing |
| IBM Granite 2B | ~6-7% | ~100 | Yes | ~4 GB | 2B | Apache 2.0 | English-only alt |

### Phase 4: Multilingual / Indian Language ASR Models

| Model | Tamil WER | Hindi WER | Code-Switch | Size | License | Self-Host | Our Pick? |
|-------|-----------|-----------|-------------|------|---------|-----------|-----------|
| **IndicConformer 600M** | **~18-25%** | **~13-15%** | No (per-lang) | 600M | **MIT** | Yes | **Primary** |
| Dolphin large | ~43% | ~25% | No | 1.6B | Apache 2.0 | Yes | Fallback |
| Whisper large-v3-turbo | ~30-37% | ~20-25% | Per-chunk | 809M | MIT | Yes | Current |
| Whisper large-v3 | ~28-35% | ~18-22% | Per-chunk | 1.55B | MIT | Yes | Marginal |
| Meta Omnilingual 1B | TBD | CER<10% | No | 1B | Apache 2.0 | Yes | Future |
| Meta Omnilingual 7B | TBD | CER<10% | No | 7B | Apache 2.0 | Yes | Future (big) |
| SeamlessM4T v2 | Best CS | Good | **Best** | 2.3B | **CC-BY-NC** | Yes | **License blocker** |
| Shunya Zero STT | No Tamil | Hinglish | Hindi-En only | ? | **RAIL-M** | Yes | Hindi only |
| Meta MMS 1B | ~20-30% | ~15-20% | No | 1B | CC-BY-NC | Yes | Superseded |
| Sarvam Saarika | Supported | Supported | Code-mixed | ? | **API only** | **No** | Not self-hosted |
| Google USM/Chirp | ? | ? | ? | 2B | **Not released** | **No** | Not self-hosted |

### Alignment Tools

| Tool | Languages | Accuracy | Memory | Tamil | Hindi | Our Pick? |
|------|-----------|----------|--------|-------|-------|-----------|
| **ctc-forced-aligner** | **1,130** | ~50-100ms | ~300MB | **Yes** | **Yes** | **Yes** |
| WhisperX alignment | ~30 | ~50-100ms | Higher | No | Yes | No (see 3.7) |
| Montreal Forced Aligner | Many | **10-20ms** | Medium | Yes | Yes | Too complex |
| CrisperWhisper | Whisper langs | Best (Whisper) | Medium | ? | ? | Interesting |

---

## 10. Sources

### Phase 3 Sources

- [Open ASR Leaderboard (HuggingFace)](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard)
- [NVIDIA Canary-Qwen 2.5B Model Card](https://huggingface.co/nvidia/canary-qwen-2.5b)
- [NVIDIA Speech AI Models Blog](https://developer.nvidia.com/blog/nvidia-speech-ai-models-deliver-industry-leading-accuracy-and-performance/)
- [faster-whisper GitHub](https://github.com/SYSTRAN/faster-whisper)
- [faster-whisper vs Canary-Qwen Comparison (HuggingFace Blog)](https://huggingface.co/blog/norwooodsystems/faster-whisper-vs-canary-qwen-2-5b)
- [IBM Granite Speech 3.3 8B](https://huggingface.co/ibm-granite/granite-speech-3.3-8b)
- [IBM Granite Speech 3.3 2B](https://huggingface.co/ibm-granite/granite-speech-3.3-2b)
- [Microsoft Phi-4 Multimodal](https://huggingface.co/microsoft/Phi-4-multimodal-instruct)
- [NVIDIA Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
- [OpenAI Whisper large-v3](https://huggingface.co/openai/whisper-large-v3)
- [WhisperX GitHub](https://github.com/m-bain/whisperX)
- [CrisperWhisper (Verbatim Timestamps)](https://github.com/nyrahealth/CrisperWhisper)
- [ctc-forced-aligner](https://github.com/MahmoudAshraf97/ctc-forced-aligner)
- [Whisper MLPerf Inference Benchmark](https://mlcommons.org/2025/09/whisper-inferencev5-1/)
- [NVIDIA NeMo Installation Guide](https://docs.nvidia.com/nemo-framework/user-guide/latest/installation.html)
- [Choosing Between Whisper Variants (Modal)](https://modal.com/blog/choosing-whisper-variants)
- [Top Open Source STT Models 2025 (Modal)](https://modal.com/blog/open-source-stt)
- [Best Open Source STT Model 2026 (Northflank)](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks)
- [Open ASR Leaderboard Paper (arXiv)](https://arxiv.org/abs/2510.06961)
- [Canary-1B-v2 & Parakeet Paper (arXiv)](https://arxiv.org/abs/2509.14128)

### Phase 4 Sources

- [AI4Bharat IndicConformer on HuggingFace](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual)
- [AI4Bharat IndicConformerASR GitHub](https://github.com/AI4Bharat/IndicConformerASR)
- [Dolphin ASR Paper (arXiv 2503.20212)](https://arxiv.org/abs/2503.20212)
- [Dolphin ASR GitHub](https://github.com/DataoceanAI/Dolphin)
- [Meta Omnilingual ASR GitHub](https://github.com/facebookresearch/omnilingual-asr)
- [Meta Omnilingual ASR Blog](https://ai.meta.com/blog/omnilingual-asr-advancing-automatic-speech-recognition/)
- [Meta SeamlessM4T v2](https://huggingface.co/facebook/seamless-m4t-v2-large)
- [Meta MMS Documentation](https://huggingface.co/docs/transformers/en/model_doc/mms)
- [Shunya Labs Pingala V1](https://huggingface.co/shunyalabs/pingala-v1-universal)
- [Shunya Labs Zero STT Hinglish](https://huggingface.co/shunyalabs/zero-stt-hinglish)
- [Vistaar Benchmark Paper (arXiv 2305.15386)](https://arxiv.org/abs/2305.15386)
- [Vistaar GitHub](https://github.com/AI4Bharat/vistaar)
- [Enhancing Whisper for Indian Languages (arXiv 2412.19785)](https://arxiv.org/abs/2412.19785)
- [Code-Switching ASR Without Real CS Data (Interspeech 2025)](https://www.isca-archive.org/interspeech_2025/nguyen25_interspeech.pdf)
- [NVIDIA Multilingual and Code-Switched ASR Blog](https://developer.nvidia.com/blog/multilingual-and-code-switched-automatic-speech-recognition-with-nvidia-nemo/)
- [Sarvam AI Models](https://www.sarvam.ai/blogs/sarvam-1)
- [Gong Language Support](https://help.gong.io/docs/what-languages-are-supported-in-gong)
- [Shunyalabs ASR Benchmark Blog](https://www.shunyalabs.ai/blog/benchmarking-top-open-source-speech-recognition-models)
- [Code-Switching ASR Literature Review (2025)](https://arxiv.org/html/2507.07741v1)
- [Benchmarking ASR for Indian Languages in Agriculture (2025)](https://arxiv.org/html/2602.03868)
