# Phase 3: "World-Class" ASR — Beginner-Friendly Explanation

> This document explains all Phase 3 research findings in simple, plain language.
> For the technical reference with exact benchmarks, code samples, and source links, see `ASR_RESEARCH.md`.

---

## Table of Contents

1. [What is Phase 3 About?](#1-what-is-phase-3-about)
2. [Understanding the Key Terms](#2-understanding-the-key-terms)
3. [The ASR Landscape — Who's Who](#3-the-asr-landscape--whos-who)
4. [Why Canary-Qwen Won't Work For Us (Yet)](#4-why-canary-qwen-wont-work-for-us-yet)
5. [What is "Forced Alignment"?](#5-what-is-forced-alignment)
6. [What is "faster-whisper"?](#6-what-is-faster-whisper)
7. [What is "large-v3-turbo"?](#7-what-is-large-v3-turbo)
8. [Sales Call Specific Findings](#8-sales-call-specific-findings)
9. [The Three Options](#9-the-three-options)
10. [Indian Language Findings](#10-indian-language-findings)
11. [New Models We Discovered](#11-new-models-we-discovered)
12. [What Commercial Services Do Better](#12-what-commercial-services-do-better)

---

## 1. What is Phase 3 About?

Think of our project like building a car in stages:

- **Phase 1 (done)** = Built the engine — **clean the audio** before transcription (remove noise, fix volume, trim silence)
- **Phase 2 (done)** = Added GPS — **identify who's speaking** (speaker diarization with Pyannote)
- **Phase 3** = Upgrade the engine — **get a better transcription model** + **word-level timestamps**

Right now we use **Whisper Medium** — a decent model from OpenAI. Phase 3 is about:
1. Finding the **most accurate model** we can actually run
2. Adding the ability to know **exactly when each word was spoken** (not just each sentence)
3. Fixing **post-processing issues** like hallucinations and number formatting

---

## 2. Understanding the Key Terms

### WER (Word Error Rate)

The standard way to measure how good a speech-to-text model is.

**How it works:** If you say 100 words, and the model gets 7 wrong (substitutions, missing words, or extra words), that's **7% WER**. Lower is better.

- **2-3% WER** = Excellent (like a professional human transcriber)
- **5-7% WER** = Very good (production quality)
- **10-15% WER** = Okay (useful but noticeable errors)
- **20%+ WER** = Poor (many errors, especially on names and numbers)

### RTFx (Real-Time Factor multiplied)

How fast the model processes audio compared to real-time.

- **RTFx = 1** means a 60-minute recording takes 60 minutes to transcribe (as slow as listening to it)
- **RTFx = 100** means a 60-minute recording takes only 36 seconds
- **RTFx = 418** (Canary-Qwen on GPU) means 60 minutes in about 8.6 seconds

On CPU (what we use), Whisper Medium runs at roughly **RTFx 0.3-1** — meaning it takes 1-3x the audio length. A 60-minute call can take 60-180 minutes to transcribe.

### PyTorch (torch)

The AI framework that powers almost all speech models. Think of it like the "operating system" for AI models. Different model versions need different PyTorch versions — this is our main compatibility headache.

### CTranslate2

A specialized C++ inference engine. While PyTorch is flexible (good for training), CTranslate2 is optimized purely for running models fast. It's like the difference between a Swiss Army knife (PyTorch) and a dedicated tool (CTranslate2).

---

## 3. The ASR Landscape — Who's Who

Think of ASR models like car manufacturers. Each has different strengths:

### NVIDIA Canary-Qwen 2.5B — The Ferrari

The **most accurate** open-source speech-to-text model in the world right now.

**What makes it special:**
- Lowest error rate: 5.63% WER (rank #1 on the global leaderboard)
- It's actually **two AI models in one**: a speech listener (FastConformer) connected to a language brain (Qwen3 LLM)
- The language brain understands context — so it knows "Zoho" is a company name, not "so ho"
- Can also **summarize calls** and **extract action items** using the same model — no need for a second AI
- Native punctuation and capitalization (Whisper often drops these)

**Why we can't use it right now:**
- Needs PyTorch 2.6+ (we're stuck on 2.1.1)
- Needs a powerful NVIDIA GPU (8GB+ video memory)
- **English only** — zero Tamil or Hindi support

### OpenAI Whisper — The Reliable Toyota (What We Use)

**What makes it special:**
- Works in **99 languages** — no other open model comes close
- Huge ecosystem — hundreds of tools built around it
- **Works on CPU** — no GPU needed
- Several sizes available (tiny → medium → large-v3)

**The problem:**
- Accuracy drops dramatically on phone calls: ~2.7% WER on clean audio but **~17.7% on telephone audio**
- Whisper was trained on podcasts and YouTube — clean audio with good microphones
- Phone calls have compression, background noise, and lower quality

**Available sizes:**

| Size | Parameters | Error Rate | Speed | Memory |
|------|-----------|------------|-------|--------|
| tiny | 39 million | ~14% | Very fast | ~150MB |
| base | 74 million | ~11% | Fast | ~300MB |
| small | 244 million | ~9% | Moderate | ~500MB |
| **medium (ours)** | 769 million | ~7.9% | Slow | ~1.5GB |
| large-v3 | 1,550 million | ~6.4% | Very slow | ~3GB |
| **large-v3-turbo** | 809 million | ~7.0% | Moderate | ~1.6GB |

### NVIDIA Parakeet — The Speed Demon

- Processes audio **3,332x faster than real-time** (a 1-hour call transcribed in ~1 second!)
- English only, needs GPU
- Good accuracy but not the best

### IBM Granite Speech 8B — The Tank

- Very accurate (5.85% WER)
- **Best noise resilience** — barely degrades on noisy audio
- But needs a powerful GPU with 16-20GB memory

### Others We Found

- **Dolphin** (Dataocean AI): Specialist for Asian languages — Tamil, Hindi, Chinese, with native code-switching
- **AI4Bharat IndicConformer** (IIT Madras): Best for Tamil and Hindi specifically
- **ElevenLabs Scribe v2**: Commercial service, possibly the most accurate overall, but closed-source
- **Moonshine**: Tiny model for edge devices — fast but less accurate

---

## 4. Why Canary-Qwen Won't Work For Us (Yet)

### The torch Version Lock

Imagine you have a car with a special engine (resemble-enhance) that only runs on a specific type of fuel (PyTorch 2.1.1). You want to install a fancy new navigation system (Canary-Qwen), but it only works with a different fuel type (PyTorch 2.6+).

You can't use both fuels at the same time. That's our problem.

**The chain:**
1. `resemble-enhance` (our Path B audio enhancer) **hardcodes** `torch==2.1.1`
2. Canary-Qwen needs `torch>=2.6` via NVIDIA's NeMo framework
3. WhisperX (forced alignment tool we originally planned) needs `torch>=2.5.1`
4. AI4Bharat IndicConformer also needs NeMo → torch 2.6+

**Everything modern is blocked by this one dependency.**

### What CAN Work

| Tool | Works with our stack? | What it does |
|------|----------------------|-------------|
| **faster-whisper** | Yes (already installed!) | Runs Whisper 4x faster using C++ |
| **Whisper large-v3-turbo** | Yes (via faster-whisper) | Better, bigger model |
| **ctc-forced-aligner** | Yes | Word-level timestamps, 1130 languages |
| Canary-Qwen 2.5B | **No** | Best accuracy but needs newer PyTorch |
| WhisperX | **No** | Forced alignment but needs newer PyTorch |

### Possible Workarounds (For Later)

1. **Microservice approach**: Run Canary-Qwen in a separate Docker container with its own PyTorch version, communicate via HTTP
2. **Drop resemble-enhance**: Upgrade torch, unlock everything (but lose Path B enhancement)
3. **Wait**: Eventually resemble-enhance may update to support newer torch

---

## 5. What is "Forced Alignment"?

### The Problem We're Solving

Right now, our pipeline does this:
1. Takes audio → removes silence (VAD trimming) → transcribes

The timestamps Whisper gives us are relative to the **trimmed audio**, not the original recording. If the original had 10 seconds of silence at the start, all our timestamps are 10 seconds off.

This matters for:
- Syncing subtitles to video
- Clicking on a word to jump to that moment in the recording
- Matching speaker labels to exact words

### How Forced Alignment Works (Simple Explanation)

Imagine you have:
- The **original audio** (untrimmed, with all the silence)
- The **transcription text** (from Whisper)

Forced alignment is like a "Ctrl+F find" for audio. You give it text and audio, and it figures out **exactly when each word was spoken**.

**Step by step:**

1. A small AI model (wav2vec2) listens to the audio and creates a "probability grid" — for every 20 milliseconds of audio, it guesses what letter is being spoken

2. Given the text "Hello how are you", a clever algorithm traces through this grid to find the best match:

```
"Hello"  → 1.23s - 1.58s
"how"    → 1.62s - 1.78s
"are"    → 1.80s - 1.95s
"you"    → 1.98s - 2.15s
```

3. The result is timestamps accurate to about **50 milliseconds** — compared to Whisper's timestamps which can be off by **several seconds**

### Why ctc-forced-aligner (Not WhisperX)

WhisperX was our original plan, but it needs torch 2.5.1+ (blocked by our torch constraint).

**ctc-forced-aligner** does the same job but:
- Works with our torch 2.1.1
- Supports **1,130 languages** (including Tamil and Hindi!) via Facebook's MMS model
- Uses **5x less memory** than WhisperX
- Much lighter to install (doesn't drag in dozens of extra packages)

### The Clever Integration Trick

Instead of trying to remap timestamps after VAD trimming, we:
1. Save a copy of the audio **before** VAD trimming (the "pre-VAD audio")
2. Run normal processing + ASR on the trimmed audio (better accuracy)
3. After ASR, run the aligner on the **pre-VAD audio** with the transcription text
4. The aligner finds where each word is in the original timeline

This completely bypasses the timestamp problem!

---

## 6. What is "faster-whisper"?

### Two Ways to Run the Same Model

We have two Whisper implementations installed:

1. **openai-whisper** — OpenAI's original code. Written in Python, uses PyTorch directly.
2. **faster-whisper** — A reimplementation using CTranslate2 (optimized C++ engine).

Both run the **exact same model** with the **exact same accuracy**. The difference is HOW they run it.

**Analogy:** Imagine two people reading the same book aloud. One reads directly (openai-whisper). The other first translates the book into a speed-reading format, then reads it (faster-whisper). Same content, different delivery speed.

### Why faster-whisper is Better

| Aspect | openai-whisper | faster-whisper |
|--------|---------------|----------------|
| Speed | 1x (baseline) | **4x faster** |
| Memory usage | 100% | **~50% less** |
| Accuracy | Same | **Same** |
| INT8 quantization | No | **Yes** (even faster, less memory) |

**What's INT8 quantization?** Normally, AI models use 32-bit numbers for calculations (very precise but slow). INT8 uses 8-bit numbers (less precise but much faster). For speech-to-text, the tiny precision loss is unnoticeable — less than 0.1% WER difference — but you get another speed boost and use less memory.

There's literally no downside to switching.

### Current Situation

Our `transcribe.py` supports both engines (UI dropdown). But `openai-whisper` is sometimes still the default. Phase 3 will make `faster-whisper` the clear default.

---

## 7. What is "large-v3-turbo"?

OpenAI released a new Whisper model called **large-v3-turbo**. Think of it as a "greatest hits" version of their biggest model.

### How They Made It

OpenAI took their most accurate model (large-v3, with 32 decoder layers) and compressed it down to just **4 decoder layers**. This is called **distillation** — like making a concentrated version of a drink.

### What We Get

| Model | Parameters | Error Rate | Speed | Memory |
|-------|-----------|------------|-------|--------|
| **medium (current)** | 769 million | ~7.9% | 1x | ~1.5GB |
| large-v3 | 1,550 million | ~6.4% | 0.25x (4x slower) | ~3GB |
| **large-v3-turbo** | 809 million | **~7.0%** | **1x (same as medium)** | ~1.6GB |

**The sweet spot:** Almost as accurate as large-v3, but 4x faster. Only slightly larger than medium (809M vs 769M), with meaningful accuracy improvement (7.0% vs 7.9%).

Still supports all 99 languages — perfect for our English + Tamil/Hindi use case.

---

## 8. Sales Call Specific Findings

### Proper Nouns are Hard for AI

ASR models learn from millions of hours of general speech. They've heard "Microsoft" countless times but maybe never heard "Freshdesk" or "Balasubramanian".

**What happens:**
- "Freshdesk" → "fresh desk" or "fresh test"
- "Balasubramanian" → "bala subra manian" or worse
- "Zoho CRM" → "so ho see are em"

**The numbers:** 15-40% error rate on proper nouns vs 2-5% on common words.

**The fix — hotword boosting:** Before transcribing, tell the model what words to expect. Whisper has a parameter called `initial_prompt` — if you include "This is a sales call about Zoho CRM with Balasubramanian", the model is much more likely to recognize those words.

With boosting: **85-95% accuracy** on domain terms (vs 60-70% without).

### Numbers are Surprisingly Error-Prone

Whisper gets numbers right only **85-92% of the time** in conversation:

- "Fifteen" vs "fifty" (the most infamous ASR error — even Amazon warns about this)
- "Thirteen" vs "thirty"
- Phone numbers: digits get grouped wrong or missed entirely
- Prices: "$1,299" might become "twelve ninety nine" or "one thousand two hundred ninety nine" — which format do you want?
- **Indian numbers:** "one lakh fifty thousand" (1,50,000) — most number formatting tools don't understand Indian numbering

**The fix — Inverse Text Normalization (ITN):** A post-processing step that converts spoken form ("fifteen thousand rupees") to written form ("Rs. 15,000"). NVIDIA's `nemo_text_processing` library is the best option.

### Hallucination: The Model Makes Things Up

This is a real problem with Whisper. When it encounters silence, music, or low-quality audio, instead of outputting nothing, it sometimes generates **phantom text** that was never spoken:

Common hallucinations:
- "Thank you for watching"
- "Please subscribe and hit the bell icon"
- "Thanks for listening"

These come from Whisper's training data (YouTube videos often end with these phrases).

**How we detect it:** Our pipeline already tracks metrics like `no_speech_probability` and `compression_ratio`. We also check for repeated phrases (n-gram repetition). Phase 3 will strengthen this filtering.

### Cross-Talk (People Talking Over Each Other)

In sales calls, people interrupt each other 10-20% of the time. When two people talk simultaneously:

- **WER jumps to 40-80%** — the model hears a jumbled mix
- Neither speaker's words are transcribed correctly

**What we do now (Phase 2-3):** Pyannote detects when speakers overlap → mark as "[cross-talk]" in the transcript → still try to transcribe but flag as low confidence.

**What we'll do later (Phase 6):** Target Speaker Extraction (TSE) — AI that can separate two overlapping voices into clean individual streams, then transcribe each separately.

### Whisper Hates Phone Audio

This was one of our most important discoveries:

| Audio Type | Whisper WER |
|-----------|-------------|
| Audiobook (studio mic) | 2.7% |
| Podcast (decent mic) | ~6% |
| Meeting recording | ~10% |
| **Phone call** | **~17.7%** |

**Why?** Whisper was trained mainly on YouTube, podcasts, and audiobooks — all relatively clean audio. Phone calls have:
- **8kHz bandwidth** (vs 44kHz for podcasts) — half the audio detail is missing
- **Lossy compression** from voice codecs
- Background noise (cars, offices, wind)
- Echo and reverb from speakerphone

Our Phase 1 preprocessing (denoising, bandwidth extension, loudness normalization) helps partially, but there's still a significant gap. This is why commercial services like Deepgram, which train specifically on millions of hours of phone calls, perform much better on telephony audio.

### Hold Music and IVR (Phone Menus)

When someone is put on hold with music, Whisper doesn't know what to do:
- Sometimes it transcribes silence/music as phantom text
- Sometimes it generates gibberish
- IVR robot voices ("Press 1 for sales") are actually transcribed well (clear voice) but the content is irrelevant

**Solution:** A future improvement would detect hold music segments and skip them.

---

## 9. The Three Options

### Option A: Quick Upgrade (Recommended)

**What we do:** Keep our current setup but make three improvements:
1. Use faster-whisper as the default engine (4x faster)
2. Use large-v3-turbo model instead of medium (better accuracy)
3. Add ctc-forced-aligner for word-level timestamps
4. Add post-processing improvements

**Analogy:** Upgrading from a regular car to a turbo version of the same car, plus adding GPS (word timestamps). Same garage, same fuel.

**Result:**
- ~12% better accuracy (7.9% → ~7.0% WER)
- 4x faster transcription
- Word-level timestamps (know exactly when each word was spoken)
- Works today on CPU, no new hardware needed

### Option B: Canary-Qwen Microservice

**What we do:** Run Canary-Qwen in a separate Docker container with its own PyTorch version. Our main app sends audio to this container via HTTP.

**Analogy:** Renting a Ferrari for race days while keeping your daily driver. Two separate vehicles, separate fuel tanks.

**Result:**
- Best-in-class 5.63% WER
- But needs GPU hardware (8GB+ video memory)
- English only — Tamil/Hindi won't work
- Complex setup (two separate services to maintain)

### Option C: Option A Now, Add B Later

**What we do:** Implement Option A immediately for the quick win. Add Canary-Qwen later when GPU hardware is available (Phase 6).

**Analogy:** Upgrade your car now, buy the Ferrari when you can afford the garage.

**Result:** Immediate improvement today + a clear path to the best accuracy later.

### Comparison Table

| | Option A | Option B | Option C |
|---|---------|---------|---------|
| **English accuracy** | ~7.0% WER | **5.63% WER** | 7.0% now → 5.63% later |
| **Tamil/Hindi** | ~28-35% | Not supported | ~28-35% now, better later |
| **GPU needed?** | No | **Yes** | Not now, later |
| **Complexity** | Low | High | Medium |
| **Available now?** | **Yes** | No (need GPU) | Partially |
| **Word timestamps?** | **Yes** (ctc-aligner) | Not built-in | **Yes** |

---

## 10. Indian Language Findings

### OpenAI Lied About Tamil/Hindi Accuracy

This was a shocking discovery. OpenAI reports Whisper's Tamil WER as ~16.9% and Hindi as ~16.9%. But a research team at Deepgram found these numbers are **artificially inflated**.

**What OpenAI does wrong:** Their testing tool for Brahmic scripts (the writing system used by Tamil, Hindi, Bengali, etc.) breaks words into individual consonant characters before measuring errors. This makes errors look smaller than they actually are.

**Real-world accuracy:**
- Tamil: **~35-40% WER** (not 16.9%)
- Hindi: **~37% WER** (not 16.9%)

This means roughly 1 in 3 words is wrong when Whisper transcribes Tamil or Hindi.

### Better Options for Tamil/Hindi (Phase 4)

| Model | Tamil WER | Hindi WER | Code-Switching? | License |
|-------|-----------|-----------|-----------------|---------|
| Whisper Large-v3 | ~28-35% | ~37% | Per-chunk only | MIT |
| **AI4Bharat IndicConformer** | ~15-20% | ~15-20% | **Native** | MIT |
| **Dolphin** | Supported | Supported | **Native** | Apache 2.0 |
| Canary-Qwen 2.5B | Not supported | Not supported | N/A | CC-BY-4.0 |

**What is code-switching?** When a speaker mixes languages in one sentence: "Can you check the விலை details?" (English + Tamil). Normal Whisper handles this poorly — it picks one language and sticks with it. IndicConformer and Dolphin were specifically trained on mixed-language audio.

---

## 11. New Models We Discovered

### Dolphin (Dataocean AI + Tsinghua University)

A brand new model we hadn't considered before:
- Supports **40 Eastern languages** including Tamil, Hindi, and English
- Built specifically for Asian languages and code-switching
- Trained on 210,000 hours of speech data
- **Apache 2.0 license** (very permissive — commercial use OK)
- Available on GitHub

This could be a great fit for Phase 4 (Multilingual Mastery).

### Moonshine (Useful Sensors)

A tiny model designed for edge devices (phones, IoT):
- Only 27M parameters (Whisper Medium is 769M — that's 28x smaller)
- 5-15x faster than Whisper on CPU
- Surprisingly good accuracy for its size
- But not competitive with large models for long-form transcription

### Whisper Internal Word Aligner (September 2025 research paper)

Researchers discovered that Whisper already has word alignment ability hidden inside it — you just need to know how to extract it. At strict 50ms tolerance, it achieved **40% better accuracy** than WhisperX. Still experimental but promising.

---

## 12. What Commercial Services Do Better

### Why Deepgram Crushes Whisper on Phone Calls

| Model | Clean Audio WER | Phone Call WER |
|-------|----------------|----------------|
| Whisper Large-v3 | 2.7% | **17.7%** |
| Deepgram Nova-3 | ~5% | **5.3-6.8%** |

Deepgram is trained specifically on **call center recordings** — exactly the type of audio in sales calls. Their training data includes:
- Phone quality audio (8kHz, compressed)
- Multiple speakers with interruptions
- Background noise from offices and cars
- Industry terminology from real business calls

Whisper was trained on YouTube and podcasts — good microphones, single speakers, clean audio. When it encounters phone-quality audio, it struggles.

### What We Could Consider (If Budget Allows)

| Service | Best For | Price per 1000 min |
|---------|---------|-------------------|
| **Deepgram Nova-3** | Phone/sales calls | $4.30 |
| **AssemblyAI Universal-2** | General + good numbers/names | $4.50 |
| OpenAI Whisper API | Budget multilingual | $6.00 |
| Google Chirp 3 | Massive language support | $16.00 |

For now, our self-hosted approach with faster-whisper is the right choice — it's free, private (no data leaves your server), and good enough for production use. But if you ever need commercial-grade phone call accuracy, Deepgram is the most relevant option.

---

## Summary

### What We Learned

1. **Canary-Qwen 2.5B is the best** but needs newer PyTorch + GPU — deferred to Phase 6
2. **faster-whisper gives us 4x speedup** for free (already installed, same accuracy)
3. **large-v3-turbo** is the sweet spot model upgrade — barely bigger, meaningfully more accurate
4. **ctc-forced-aligner** solves word timestamps AND supports Tamil/Hindi — better than WhisperX for us
5. **Whisper struggles with phone calls** — 17.7% error rate vs 2.7% on clean audio
6. **OpenAI's Tamil/Hindi numbers are inflated** — real WER is ~35-40%, not ~17%
7. **Dolphin ASR** is a promising new model for Tamil/Hindi code-switching (Phase 4)
8. **Post-processing matters** — hotword boosting, number formatting, and hallucination filtering can significantly improve real-world usability

### What We're Recommending

**Option A (Quick Upgrade)** — switch to faster-whisper + large-v3-turbo + ctc-forced-aligner. Immediate improvement, no new hardware, compatible with our current stack.
