# Phase 4: Multilingual Mastery — Complete Research (2025-2026)

> **Purpose:** Comprehensive research for handling English + Tamil/Hindi mixed-language sales calls.
> **Previous constraint:** PyTorch 2.1.1 (now lifted — resemble-enhance moved to microservice).
> **Date:** February 2026

---

## Table of Contents

1. [The Problem We're Solving](#1-the-problem-were-solving)
2. [What is Code-Switching?](#2-what-is-code-switching)
3. [Why Code-Switching is Hard for ASR](#3-why-code-switching-is-hard-for-asr)
4. [Every Model Evaluated (12 Models)](#4-every-model-evaluated)
5. [WER Comparison Tables](#5-wer-comparison-tables)
6. [Approaches to Code-Switching](#6-approaches-to-code-switching)
7. [Production Services Comparison](#7-what-production-services-do)
8. [Benchmark Datasets](#8-benchmark-datasets)
9. [Critical Corrections to Our Original Plan](#9-critical-corrections-to-our-original-plan)
10. [My Recommendation](#10-my-recommendation)
11. [Sources](#11-sources)

---

## 1. The Problem We're Solving

Our platform transcribes **sales calls** where speakers freely mix **English + Tamil** or **English + Hindi**. Currently, Whisper medium handles English well (~5% WER) but Tamil is terrible (~64% WER in reality).

**What we need:**
- Accurate Tamil ASR (currently ~64% WER — 6 out of 10 words wrong)
- Accurate Hindi ASR (currently ~43% WER)
- Handle mid-sentence language switching ("The ROI is excellent, **unga business-ku romba useful aaga irukkum**")
- Self-hosted (not cloud API)
- Works on CPU initially, GPU later

---

## 2. What is Code-Switching?

**Code-switching** = when a speaker switches between languages within a conversation, sentence, or even a single phrase. This is the **default** way bilingual Indians speak, not an exception.

### Types of Code-Switching

| Type | What happens | Example | Difficulty |
|------|-------------|---------|------------|
| **Inter-sentential** | Switch at sentence boundaries | "The product is great. **Idhu unga team-ku romba help aagum.**" | Easier — can detect language per sentence |
| **Intra-sentential** | Switch mid-sentence | "We can give you **best ROI unga business-ku**" | Very hard — language changes mid-flow |
| **Tag-switching** | Insert a word/filler from another language | "So the deal is confirmed, **illa?**" | Medium — small insertions |

**Your sales calls have ALL three types.** A sales rep might say:

> "Let me share the pricing details, **aapko ek discount bhi milega is quarter mein** — so basically a 20% discount on the annual plan."

This sentence has English → Hindi → English. No clear boundary. This is why code-switching is the hardest problem in multilingual ASR.

### Think of it like a bilingual brain

Imagine you're listening to a conversation and the speaker suddenly switches language mid-word. Your brain seamlessly adapts because you know both languages. But an ASR model trained on English has never heard Tamil sounds — and a Tamil model has never heard English sounds. When the speaker mixes both, neither model knows what to do.

---

## 3. Why Code-Switching is Hard for ASR

### 3.1 Whisper's Fundamental Design Flaw

Whisper detects the language **ONCE** from the first 30 seconds and applies that single language to the **ENTIRE** file. It has ONE language token in its decoder — it literally cannot switch languages mid-sequence.

**What happens in practice:**
- First 30s is mostly English → Whisper sets `<en>` → Tamil segments become garbage
- First 30s is mostly Tamil → Whisper sets `<ta>` → English segments become garbage
- Code-switched segment → Whisper picks one language → the other half is destroyed

**Real benchmark:** Interspeech 2025 paper found that Whisper-Turbo-v3 produces **over 100% WER** on Tamil-English code-switched data. Over 100% means it inserts MORE wrong words than there are real words.

### 3.2 Whisper's Inflated WER for Brahmic Scripts

**Critical finding:** OpenAI's published Tamil WER numbers are artificially deflated by ~30%.

Deepgram discovered that OpenAI's text normalization script strips Unicode "markers" (category 'M') from Brahmic scripts. In Tamil, vowel ligatures (matras like ா, ி, ீ, ு, ூ) are classified as Unicode markers. Stripping them breaks Tamil words into consonant fragments, making the reference text artificially similar to Whisper's broken output.

**Example:** The Tamil word "அலைச்சுக்குவான்" gets its vowel signs stripped, becoming a skeleton of consonants. This makes Whisper's errors look less bad than they really are.

**What this means:** Our CLAUDE.md said Whisper Tamil WER is ~35%. The real-world number is actually **~64%** (Whisper medium, no fine-tuning). Even Whisper large-v3's Tamil WER is probably ~40-50%, not the ~25-30% sometimes cited.

### 3.3 No Switch-Point Modeling

Standard ASR models are trained on monolingual data. They have no concept of "the next word might be in a different language." At language boundaries, the decoder's language model predictions become unreliable.

### 3.4 Data Scarcity

While there are millions of hours of monolingual English and Tamil data, **real code-switched training data is extremely scarce.** Collecting and transcribing code-switched speech requires annotators fluent in both languages — expensive and slow.

---

## 4. Every Model Evaluated

We evaluated **12 models** across 5 categories. Here's every single one:

### 4.1 AI4Bharat IndicConformer

**What it is:** A suite of ASR models built by AI4Bharat (IIT Madras) for all 22 official Indian languages. Uses the Conformer architecture — a hybrid of CNNs and Transformers.

**Think of it like:** A team of 22 specialist doctors (one per language) vs one general practitioner who knows all 22 (the multilingual model).

| Attribute | Detail |
|-----------|--------|
| **Architecture** | Hybrid CTC-RNNT Conformer |
| **Monolingual models** | 120M params each, 22 models (one per language) |
| **Multilingual model** | 600M params, all 22 languages in one model |
| **Tamil WER** | ~31% (IndicVoices test), significantly better than Whisper |
| **Hindi WER** | ~15% (IndicVoices test) |
| **English support** | **NO** — Indian languages only |
| **Code-switching** | **NO** — single language per inference |
| **License** | MIT (fully open, commercial OK) |
| **CPU inference** | Yes |
| **Framework (600M)** | HuggingFace Transformers + ONNX — **no NeMo needed!** |
| **Framework (120M)** | AI4Bharat NeMo fork (heavier dependency) |
| **HuggingFace** | `ai4bharat/indic-conformer-600m-multilingual` |

**How to use (600M multilingual — simple!):**
```python
from transformers import AutoModel
import torchaudio

model = AutoModel.from_pretrained(
    "ai4bharat/indic-conformer-600m-multilingual",
    trust_remote_code=True
)
wav, sr = torchaudio.load("audio.wav")
# Tamil transcription
text = model(wav, "ta", "rnnt")  # or "ctc" for faster decoding
```

**Dependencies:** `pip install transformers torchaudio onnxruntime`

**Verdict:** Best pure Tamil ASR available. But cannot handle English at all — you'd need a separate Whisper instance for English segments.

---

### 4.2 AI4Bharat IndicWhisper

**What it is:** OpenAI's Whisper model fine-tuned on 10,700 hours of Indian language speech data. It's the same Whisper architecture you already use, just with better Indian language weights.

**Think of it like:** Taking a native English speaker (Whisper) and teaching them Tamil for 10,000 hours. They become much better at Tamil but might lose some English nuance.

| Attribute | Detail |
|-----------|--------|
| **Base model** | Whisper medium (769M params) |
| **Training data** | 10,700 hours across 12 Indian languages |
| **Tamil WER** | **~24%** (Vistaar benchmark average) — down from 64%! |
| **Hindi WER** | **~14%** (Vistaar benchmark average) — down from 43%! |
| **English** | Some ability (inherited from Whisper), but degraded by fine-tuning |
| **Code-switching** | Limited — fine-tuned for single language, loses Whisper's weak CS ability |
| **License** | MIT |
| **Framework** | Same as current Whisper — zero new dependencies! |
| **Integration effort** | **Easiest** — just swap model weights |

**Per-benchmark Tamil WER:**
| Benchmark | Whisper Medium | IndicWhisper |
|-----------|---------------|-------------|
| Kathbath | Very high | 24.2% |
| FLEURS | Very high | 25.2% |
| CommonVoice | Very high | 29.2% |
| IndicTTS | Very high | 17.2% |
| MUCS | Very high | 28.3% |

**Verdict:** Cuts Tamil WER from 64% to 24% with ZERO new dependencies. Easiest integration path since we already use Whisper.

---

### 4.3 Dolphin ASR (Dataocean AI + Tsinghua University)

**What it is:** A multilingual ASR model for 40 Eastern languages, built with an E-Branchformer encoder.

| Attribute | Detail |
|-----------|--------|
| **Architecture** | E-Branchformer encoder + Transformer decoder |
| **Parameters** | Base: 140M, Small: 372M (public). Medium: 910M, Large: 1,679M (NOT public) |
| **Tamil WER** | 34-46% (small, varies by benchmark) |
| **Hindi WER** | 14-26% (small, varies by benchmark) |
| **English support** | **NO — does not support English at all** |
| **Code-switching** | **NO — requires single language per inference call** |
| **License** | Apache 2.0 |
| **CPU inference** | Yes (also MPS for Apple Silicon) |
| **Install** | `pip install dataoceanai-dolphin` |

**CRITICAL CORRECTION:** Our CLAUDE.md said Dolphin has "native code-switching" support. **This is wrong.** Dolphin:
- Does NOT support English (Eastern languages only)
- Does NOT handle code-switching (one language per inference)
- The "two-level language token" system handles **dialect variation** (e.g., Tamil-India vs Tamil-Singapore), NOT language mixing

**Tamil WER comparison (Dolphin vs others):**
| Model | CommonVoice Tamil | FLEURS Tamil |
|-------|------------------|-------------|
| Whisper large-v3 | 53.1% | 29.7% |
| Dolphin Small (372M) | 39.5% | 38.0% |
| Dolphin Large (1,679M) — **NOT public** | 34.5% | 33.1% |

**Verdict:** Worse than IndicWhisper for Tamil, doesn't support English, no code-switching. **Not recommended** for our use case. The best model (Large) isn't even publicly available.

---

### 4.4 NVIDIA Canary Family

**What it is:** NVIDIA's ASR model family built on FastConformer encoder. Multiple variants available.

| Model | Params | Languages | Tamil? | Hindi? | License |
|-------|--------|-----------|--------|--------|---------|
| Canary-1B (original) | 1B | 4 (EN, DE, ES, FR) | No | No | CC-BY-NC (non-commercial!) |
| Canary-1B-Flash | 883M | 4 + 19 via Riva | No | Yes (Riva only) | CC-BY-4.0 |
| **Canary-1B-v2** | 978M | 25 European | **No** | **No** | CC-BY-4.0 |
| **Canary-Qwen-2.5B** | 2.5B | **English only** | **No** | **No** | CC-BY-4.0 |
| Parakeet-TDT-0.6B-v3 | 600M | 25 European | No | No | CC-BY-4.0 |

**Key findings:**
- **No Tamil support in ANY Canary variant.** Not even in Riva.
- Hindi only available in Riva-deployed models (commercial, not open-source on HuggingFace)
- Best English WER: Canary-Qwen-2.5B at **5.63%** (beats Whisper large-v3's 7.44%)
- Requires **NeMo framework** — cannot use faster-whisper/CTranslate2
- **GPU effectively required** — CPU inference is impractical
- ONNX export is **broken** (GitHub issue closed as "not planned")

**Canary-Qwen-2.5B is interesting for Phase 7** (Intelligence Layer) because its LLM mode can summarize transcripts — combining ASR + summarization in one model. But it's English-only and needs GPU.

**Verdict:** Excellent English ASR, but **useless for Tamil.** Not relevant for Phase 4.

---

### 4.5 Pingala V1 (Shunya Labs) — NEW DISCOVERY

**What it is:** A Whisper-based ASR model supporting 204 languages with dedicated code-switching variants. Released July 2025.

| Attribute | Detail |
|-----------|--------|
| **Architecture** | Whisper-based |
| **Parameters** | ~1.5B (Universal and Verbatim variants) |
| **Languages** | 204 (including Tamil, Hindi, Telugu, Kannada, Bengali) |
| **English WER** | 2.94% (industry-best across 8 benchmarks) |
| **Code-switching** | **YES — dedicated Hindi-English "Hinglish" model** |
| **CPU support** | **Yes** — runs on CPU, ONNX optimized edge variants |
| **License** | Open source |
| **HuggingFace** | `shunyalabs/pingala-v1-universal` |

**Why this is exciting:** Pingala V1 is the only self-hosted model that:
1. Explicitly supports code-switching (Hindi-English at minimum)
2. Runs on CPU
3. Supports 204 languages including all major Indian languages
4. Is built on Whisper architecture (familiar integration path)

**Caveat:** Tamil-English code-switching model not confirmed — the published code-switch model is Hindi-English. Tamil WER numbers not independently benchmarked. This needs testing.

**Verdict:** Very promising. Must test on our actual sales call data.

---

### 4.6 Meta MMS (Massively Multilingual Speech)

**What it is:** Facebook's wav2vec 2.0 model with per-language adapter modules, covering 1,162 languages.

| Attribute | Detail |
|-----------|--------|
| **Parameters** | 1B (shared) + 2.5M per language adapter |
| **Languages** | 1,162 (including Tamil and Hindi) |
| **Architecture** | Wav2Vec2 + language adapters |
| **Tamil WER** | ~30-40% (estimated, no published number) |
| **Code-switching** | **NO** — each adapter is monolingual |
| **License** | **CC-BY-NC 4.0** (non-commercial!) |
| **CPU inference** | Yes |
| **HuggingFace** | `facebook/mms-1b-all` |

**Best use:** Language identification. MMS can detect which language a segment is in very reliably across 1,000+ languages.

**Verdict:** Good for language ID, not ideal as primary ASR. Non-commercial license is restrictive.

---

### 4.7 Meta Omnilingual ASR (November 2025)

**What it is:** Successor to MMS. CTC and LLM-based ASR models for 1,600+ languages.

| Attribute | Detail |
|-----------|--------|
| **Models** | CTC (300M, 1B, 3B, 7B) + LLM-ASR (~7.8B) |
| **Languages** | 1,600+ |
| **License** | **Apache 2.0** (much better than MMS!) |
| **Install** | `pip install omnilingual-asr` |
| **Audio limit** | **Only <40 seconds** (blocker for long calls) |
| **7B VRAM** | ~17-20 GB |
| **Performance** | CER <10% for 78% of languages |

**Verdict:** Promising future option. But the 40-second audio limit is a hard blocker for 30-60 minute sales calls. Monitor for updates.

---

### 4.8 Meta SeamlessM4T v2

**What it is:** Multimodal multilingual translation model that also does ASR. Key advantage: **best code-switching results after fine-tuning.**

| Attribute | Detail |
|-----------|--------|
| **Parameters** | Medium: 311M, Large: 635M |
| **Languages** | 101 (speech input), including Tamil and Hindi |
| **Code-switching** | **Best available** — explicitly designed for it |
| **Tamil-English CS WER** | **14.73%** after fine-tuning (Interspeech 2025) |
| **License** | **CC-BY-NC 4.0** (non-commercial!) |
| **GPU requirements** | Medium: 8-16GB, Large: >32GB |
| **HuggingFace** | `facebook/seamless-m4t-v2-large` |

**Code-switching results (Interspeech 2025 paper):**
| Model | Tamil-English CS WER (after fine-tuning) |
|-------|----------------------------------------|
| Whisper-Turbo-v3 | 20.46% |
| **SeamlessM4T v2** | **14.73%** (best) |
| MMS-1B-All | Higher |

**The catch:** Requires fine-tuning on synthetic code-switched data (~1,000 hours). Without fine-tuning, it's not great. Also CC-BY-NC license restricts commercial use.

**Verdict:** Best theoretical CS performance, but requires fine-tuning + non-commercial license. Consider for future research.

---

### 4.9 Microsoft VibeVoice-ASR (January 2026)

**What it is:** A 7B parameter ASR model that natively handles code-switching across 50+ languages, with integrated speaker diarization. Open-sourced January 2026.

| Attribute | Detail |
|-----------|--------|
| **Parameters** | 7B |
| **Languages** | 50+ |
| **Code-switching** | **Native support** — handles within and across utterances |
| **Diarization** | **Built-in** — identifies speakers without separate model |
| **Audio length** | 60 minutes in a single pass |
| **Hotword support** | Yes — for domain vocabulary |
| **HuggingFace** | `microsoft/VibeVoice-ASR` |

**Why this is exciting:** It does EVERYTHING our pipeline needs in one model:
- Code-switching (English+Hindi, English+Tamil?)
- Speaker diarization (built-in)
- Long audio (60 minutes)
- Hotword support (domain vocabulary)

**The catch:** Indian language performance is **NOT benchmarked publicly** yet. We don't know if Tamil is in the 50+ languages. The 7B size needs significant GPU (likely 16-20GB VRAM).

**Verdict:** Most promising future option. Must monitor for Indian language benchmarks before committing.

---

### 4.10 Sarvam AI

**What it is:** Indian AI startup with the best Indian language ASR — but API-only.

| Attribute | Detail |
|-----------|--------|
| **Model** | Sarvam Audio (Feb 2026), Saarika v2.5 |
| **Languages** | 22 Indian languages + English |
| **Code-switching** | **YES** — "handles mid-sentence language switches intelligently" |
| **Self-hostable** | **NO — API only** |
| **Claims** | Outperforms GPT-4o-Transcribe and Gemini-3-Flash on Indian languages |

**Verdict:** Best Indian language ASR available today, but violates our self-hosted requirement. Could be used as a cloud fallback or quality benchmark.

---

### 4.11 Google USM / Chirp 3

**What it is:** Google's Universal Speech Model, available only via Cloud API.

| Attribute | Detail |
|-----------|--------|
| **Parameters** | 2B |
| **Languages** | 300+ including Tamil and Hindi |
| **Self-hostable** | **NO — API only** |

**Verdict:** Not relevant for self-hosted system.

---

### 4.12 Vakyansh (Indian Government)

**What it is:** Open-source wav2vec 2.0 models for Indian languages from the EkStep Foundation.

| Attribute | Detail |
|-----------|--------|
| **Architecture** | wav2vec 2.0 (2022 era) |
| **Tamil model** | Trained on only 250 hours |
| **Last updated** | 2022-2023 |

**Verdict:** **Outdated.** Superseded by IndicConformer and IndicWhisper. The training data has been incorporated into newer models.

---

## 5. WER Comparison Tables

### Tamil WER (Lower = Better)

| Model | Params | FLEURS | CommonVoice | Vistaar Avg | Code-Switch | Self-Hosted |
|-------|--------|--------|-------------|-------------|-------------|-------------|
| Whisper medium (current) | 769M | ~50%+ | ~53% | **~64%** | >100% | Yes |
| Whisper large-v3-turbo | 809M | ~40-50%? | ~45%? | ~50-55%? | >100% | Yes |
| **IndicWhisper** | 769M | 25.2% | 29.2% | **~24%** | N/A | Yes |
| IndicConformer 600M | 600M | N/A | N/A | **~31%** | N/A | Yes |
| Dolphin Small | 372M | 38.0% | 39.5% | N/A | N/A | Yes |
| Dolphin Large (**not public**) | 1,679M | 33.1% | 34.5% | N/A | N/A | No |
| SeamlessM4T v2 (fine-tuned) | 635M | N/A | N/A | N/A | **14.7%** | Yes (NC) |
| Whisper-turbo (fine-tuned) | 809M | N/A | N/A | N/A | **20.5%** | Yes |

### Hindi WER (Lower = Better)

| Model | Params | FLEURS | CommonVoice | Vistaar Avg | Self-Hosted |
|-------|--------|--------|-------------|-------------|-------------|
| Whisper medium (current) | 769M | ~37% | ~35% | **~43%** | Yes |
| **IndicWhisper** | 769M | 15.0% | 11.4% | **~14%** | Yes |
| IndicConformer 600M | 600M | N/A | N/A | **~15%** | Yes |
| Dolphin Small | 372M | 14.4% | 15.9% | N/A | Yes |
| Google STT | ? | N/A | N/A | ~24% | No |
| Azure STT | ? | N/A | N/A | ~20% | No |

### English WER (for reference)

| Model | OpenASR Avg | LibriSpeech Clean |
|-------|-------------|-------------------|
| Whisper medium (current) | ~10% | ~4% |
| Whisper large-v3-turbo | ~7% | ~2.7% |
| Canary-Qwen-2.5B | **5.6%** | **1.6%** |
| Canary-1B-v2 | 7.2% | 2.2% |
| Pingala V1 | N/A | **2.9%** |

---

## 6. Approaches to Code-Switching

### Approach A: Language-Aware Routing (No Fine-Tuning — Quick Start)

**How it works:** Detect the language of each chunk/segment, route to the best model for that language.

```
Audio chunk (from VAD/diarization)
    |
    v
[Language Detection] — Whisper auto-detect or MMS LID
    |
    +--> English → Whisper large-v3-turbo
    |
    +--> Tamil → IndicConformer 600M or IndicWhisper Tamil
    |
    +--> Hindi → IndicConformer 600M or IndicWhisper Hindi
    |
    v
[Merge transcriptions with timestamps]
```

| Pros | Cons |
|------|------|
| No fine-tuning needed | Fails on intra-sentential CS ("best ROI unga business-ku") |
| Best-in-class model per language | Language detection errors propagate |
| Simple to implement | Extra latency from LID step |
| Works TODAY with available models | Cannot handle mixed-script output |

**Expected WER:** ~25-35% on code-switched speech (good for pure Tamil/English segments, bad for mixed segments)

### Approach B: Fine-Tuned Code-Switching Model

**How it works:** Fine-tune a multilingual model (SeamlessM4T v2 or Whisper) on synthetic code-switched data.

**The key insight from Interspeech 2025:** You can create synthetic code-switched training data WITHOUT recording real code-switched speech:
1. Take monolingual Tamil sentences + English sentences
2. Replace 10-30% of phrases with translations from the other language
3. Splice corresponding audio segments together
4. Fine-tune on ~1,000 hours of this synthetic data

| Pros | Cons |
|------|------|
| Best CS performance (14-20% WER) | Requires fine-tuning infrastructure |
| Handles mid-sentence switching | Need to generate synthetic training data |
| Single model for all languages | SeamlessM4T v2 has NC license |
| Proven by research | Not yet production-validated |

**Expected WER:** ~15-20% on code-switched speech

### Approach C: Evaluate Pingala V1 Code-Switching

**How it works:** Use Pingala V1's dedicated code-switching model (if Tamil-English is supported).

| Pros | Cons |
|------|------|
| Pre-trained for CS, no fine-tuning needed | Tamil-English CS model not confirmed |
| CPU-friendly | WER benchmarks not independently verified |
| Whisper-based (familiar integration) | Relatively new (July 2025) |
| 204 languages | Unknown production stability |

### Approach D: LLM Post-Processing (CLEAR System)

**How it works:** Run standard ASR (which will make mistakes at switch points), then use an LLM to rescore/correct the output.

This is complementary to Approaches A-C — you add LLM rescoring on top of any ASR output. Research shows **~8% relative WER reduction** from LLM rescoring on code-switched Indian English. This aligns with our Phase 5 (Post-Processing) plan.

### Approach E: Microsoft VibeVoice-ASR (Future)

**How it works:** Single 7B model handles ASR + code-switching + diarization + hotwords.

| Pros | Cons |
|------|------|
| All-in-one solution | Indian language performance unconfirmed |
| Native CS support | 7B model needs significant GPU |
| Built-in diarization | Very new (Jan 2026) |
| Hotword support | Unknown production stability |

---

## 7. What Production Services Do

| Service | Code-Switching Approach | Tamil? | Hindi? | Self-Hosted? |
|---------|------------------------|--------|--------|-------------|
| **Sarvam AI** | Trained on real code-mixed data | Yes | Yes | No (API) |
| **Google Meet** | Gemini AI, 5 Latin language pairs only | No | "Promising" | No (API) |
| **Microsoft Teams** | Per-speaker language selection (not CS) | Limited | Yes | No |
| **Rev.ai** | Pyannote fine-tuned + monolingual routing | Unknown | Unknown | No (API) |
| **Deepgram** | Multi-condition training, per-word attribution | Unknown | Unknown | No (API) |
| **AssemblyAI** | Custom embeddings, multichannel shortcut | Unknown | Unknown | No (API) |

**Key takeaway:** No production service has publicly solved English+Tamil code-switching. Even Google and Microsoft handle only a handful of CS pairs (mostly European languages). Sarvam AI claims to handle it but is API-only.

---

## 8. Benchmark Datasets

### Available for Tamil Code-Switching

| Dataset | Details | Type |
|---------|---------|------|
| **CS-FLEURS TA-EN** | Part of 113 CS pairs, Interspeech 2025 | Synthetic |
| **MUCS 2021 Tamil** | ~95 hours monolingual Tamil (no TA-EN CS pair) | Real monolingual |
| **Synthetic TA-EN** | Created using phrase-level mixing (Interspeech 2025 method) | Synthetic |

**Gap:** There is **NO large-scale real-world English-Tamil code-switching dataset.** All TA-EN evaluation uses synthetic data.

### Available for Hindi Code-Switching

| Dataset | Details | Type |
|---------|---------|------|
| **MUCS 2021 HI-EN** | ~600 hours, real Hindi-English CS | Real CS |
| **Microsoft CS Challenge** | Hindi-English and Bengali-English | Real CS |
| **CS-FLEURS HI-EN** | Part of CS-FLEURS benchmark | Synthetic |

Hindi-English CS is better served — MUCS 2021 provides real code-switched training data.

---

## 9. Critical Corrections to Our Original Plan

Our CLAUDE.md Phase 4 plan said:
> "IndicConformer/AI4Bharat + Dolphin for Hindi & Tamil code-switching"

**This plan has multiple errors:**

| Original Claim | Reality | Impact |
|----------------|---------|--------|
| "Dolphin for code-switching" | Dolphin does NOT support English and has NO code-switching | Plan invalid — Dolphin alone can't handle our use case |
| "Whisper Tamil WER ~35%" | Real Tamil WER is **~64%** (text normalization bug inflates numbers) | Current system is worse than we thought |
| "Canary-Qwen deferred to Phase 8" | Canary has NO Tamil support in any variant | Can't use Canary for Tamil even with GPU |
| "IndicConformer needs NeMo" | The 600M multilingual model uses Transformers + ONNX (no NeMo!) | Much easier to integrate than expected |
| PyTorch 2.1.1 blocked everything | Now lifted (resemble-enhance in microservice) | More models are available |

---

## 10. My Recommendation

### Strategy: Three-Step Implementation

#### Step 1: Quick Win — Language-Aware Routing (No Fine-Tuning)

**Do this first.** It requires no fine-tuning, uses existing models, and dramatically improves Tamil/Hindi accuracy.

```
Audio chunk (from existing VAD/diarization)
    |
    v
[Language Detection] — Whisper auto-detect (already in smart chunking)
    |
    +--> English chunks → faster-whisper large-v3-turbo (upgrade from medium)
    |
    +--> Tamil chunks → IndicConformer 600M (via Transformers+ONNX)
    |                    OR IndicWhisper Tamil (swap model weights)
    |
    +--> Hindi chunks → IndicConformer 600M
    |                    OR IndicWhisper Hindi
    |
    v
[ctc-forced-aligner] — word-level timestamps (already implemented)
    |
    v
[Merge transcriptions + speaker labels]
```

**Expected improvement:**
- Tamil: from ~64% WER → ~24-31% WER (2-3x better!)
- Hindi: from ~43% WER → ~14-15% WER (3x better!)
- English: from ~5% WER → ~3% WER (slight improvement with turbo)
- Code-switched segments: still poor (~35-50% WER)

**New dependencies:**
- IndicConformer 600M: `transformers`, `torchaudio`, `onnxruntime` (all lightweight)
- OR IndicWhisper: zero new dependencies (just swap model weights)

**My recommendation for Step 1:**

| Option | Tamil WER | New Deps | Integration Effort | Verdict |
|--------|-----------|----------|-------------------|---------|
| **IndicWhisper** | ~24% | Zero | Lowest — swap model weights | **Try this first** |
| **IndicConformer 600M** | ~31% | 3 packages | Low — Transformers API | Try if IndicWhisper quality isn't enough |

**Start with IndicWhisper** because:
1. Zero new dependencies
2. Better Tamil WER (~24% vs ~31%)
3. Same Whisper architecture — all our smart chunking, hallucination filtering, timestamps work unchanged
4. We can have per-language IndicWhisper models alongside our English Whisper

#### Step 2: Evaluate Pingala V1 (Code-Switching)

**After Step 1 works,** evaluate Pingala V1 for code-switched segments.

| Action | Detail |
|--------|--------|
| Install | Download from HuggingFace |
| Test | Run on actual sales call recordings with known CS segments |
| Compare | Compare CS segment accuracy vs our language-routing approach |
| Decide | If Pingala handles CS better, use it for detected CS chunks |

This is important because Step 1 fails on intra-sentential code-switching. If a salesperson says "the ROI is great, unga business-ku" in one chunk, language routing will pick either English or Tamil — neither gives good results for the mixed part.

#### Step 3: Future — Fine-Tune or VibeVoice (When Ready)

| Option | When | Effort |
|--------|------|--------|
| Fine-tune Whisper on synthetic CS data | When we need <20% CS WER | High (need to generate training data) |
| VibeVoice-ASR | When Indian language benchmarks are published | Medium (single model replaces pipeline) |
| SeamlessM4T v2 fine-tuning | If commercial license is OK | High (NC license issue) |

### Architecture Decision: IndicWhisper vs IndicConformer

| Factor | IndicWhisper | IndicConformer 600M |
|--------|-------------|---------------------|
| Tamil WER | **~24%** (better) | ~31% |
| Hindi WER | **~14%** (similar) | ~15% |
| Dependencies | **None new** | 3 packages |
| Integration | **Swap model weights** | New model loading code |
| All 22 languages | 12 languages | **22 languages** |
| Timestamps | **Native Whisper timestamps** | No native timestamps |
| Architecture | Same Whisper | Different (Conformer) |
| Model size | 769M per language | **600M for ALL languages** |

**Recommendation: Start with IndicWhisper for Tamil and Hindi.**

Reasons:
1. **Better WER** on both Tamil (~24%) and Hindi (~14%)
2. **Zero new dependencies** — same openai-whisper or faster-whisper framework
3. **Timestamps work out of the box** — no additional alignment needed
4. **Familiar architecture** — all our smart chunking, hallucination filtering, context carry-over work unchanged
5. **Easy fallback** — if IndicWhisper quality is poor on our data, we can try IndicConformer 600M

**But keep IndicConformer 600M as backup** because:
1. Covers all 22 Indian languages (IndicWhisper only covers 12)
2. Single model for all languages (IndicWhisper needs one model per language)
3. RNNT decoder may be more accurate for conversational speech

### Model Upgrade Path (Phase 3 remaining + Phase 4)

| Current | Upgrade To | What Changes |
|---------|-----------|-------------|
| Whisper medium (English) | **faster-whisper large-v3-turbo** | English WER ~5% → ~3% |
| Whisper medium (Tamil) | **IndicWhisper Tamil** | Tamil WER ~64% → ~24% |
| Whisper medium (Hindi) | **IndicWhisper Hindi** | Hindi WER ~43% → ~14% |
| Single model for all | **Per-language model routing** | Best model per language |

### What NOT to Use

| Model | Reason |
|-------|--------|
| Dolphin ASR | No English support, no code-switching |
| NVIDIA Canary (any variant) | No Tamil support |
| Google USM / Chirp 3 | API-only |
| Sarvam Audio | API-only |
| Vakyansh | Outdated (2022) |
| Meta MMS (as primary ASR) | CTC-only output (no punctuation/casing), NC license |

---

## 11. Sources

### Research Papers
- [Can we train ASR systems on Code-switch without real code-switch data? (Interspeech 2025)](https://arxiv.org/abs/2506.14177)
- [Adapting Whisper for Code-Switching (Dec 2024)](https://arxiv.org/abs/2412.16507)
- [Code-Switching in End-to-End ASR: Systematic Review (2025)](https://arxiv.org/html/2507.07741v1)
- [CS-FLEURS: Massively Multilingual Code-Switched Dataset (Interspeech 2025)](https://arxiv.org/abs/2509.14161)
- [MUCS: Multilingual and Code-Switching ASR Challenge](https://arxiv.org/abs/2104.00235)
- [CLEAR: Code-Mixed ASR with LLM-Driven Rescoring (2025)](https://aclanthology.org/2025.icnlsp-1.33.pdf)
- [Enhancing Whisper for Indian Languages (Dec 2024)](https://arxiv.org/html/2412.19785v1)
- [Vistaar: Benchmarks for Indian Language ASR (Interspeech 2023)](https://arxiv.org/pdf/2305.15386)
- [IndicVoices (ACL 2024)](https://arxiv.org/html/2403.01926v1)
- [Dolphin Paper (March 2025)](https://arxiv.org/html/2503.20212v1)
- [Canary-1B-v2 & Parakeet-TDT-0.6B-v3 (Sept 2025)](https://arxiv.org/html/2509.14128v1)

### Model Pages
- [AI4Bharat IndicConformer 600M](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual)
- [AI4Bharat IndicConformer Tamil](https://huggingface.co/ai4bharat/indicconformer_stt_ta_hybrid_ctc_rnnt_large)
- [AI4Bharat IndicConformer Collection (25 models)](https://huggingface.co/collections/ai4bharat/indicconformer-66d9e933a243cba4b679cb7f)
- [AI4Bharat Vistaar / IndicWhisper](https://github.com/AI4Bharat/vistaar)
- [Pingala V1 Universal](https://huggingface.co/shunyalabs/pingala-v1-universal)
- [Dolphin Base](https://huggingface.co/DataoceanAI/dolphin-base)
- [Dolphin Small](https://huggingface.co/DataoceanAI/dolphin-small)
- [NVIDIA Canary-1B-v2](https://huggingface.co/nvidia/canary-1b-v2)
- [NVIDIA Canary-Qwen-2.5B](https://huggingface.co/nvidia/canary-qwen-2.5b)
- [Meta MMS-1B-all](https://huggingface.co/facebook/mms-1b-all)
- [Meta Omnilingual ASR](https://github.com/facebookresearch/omnilingual-asr)
- [Meta SeamlessM4T v2](https://huggingface.co/facebook/seamless-m4t-v2-large)
- [Microsoft VibeVoice-ASR](https://huggingface.co/microsoft/VibeVoice-ASR)
- [Sarvam AI Shuka-v1](https://huggingface.co/sarvamai/shuka-1)
- [Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo)

### Blogs and Industry
- [Deepgram: Breaking Brahmic — Whisper's True WER for South Asian Languages](https://deepgram.com/learn/how-openai-s-text-normalization-hides-whisper-s-true-word-error-rate-for-south-asian-and-southeast-asian-languages)
- [Sarvam Audio Blog (Feb 2026)](https://www.sarvam.ai/blogs/sarvam-audio)
- [NVIDIA Speech AI Models Blog](https://developer.nvidia.com/blog/nvidia-speech-ai-models-deliver-industry-leading-accuracy-and-performance/)
- [Pingala V1 Press Release](https://www.prnewswire.com/news-releases/shunya-labs-launches-pingala-v1-top-ranked-cpu-based-asr-model-setting-performance-records-302513199.html)
- [Meta Omnilingual ASR Blog](https://ai.meta.com/blog/omnilingual-asr-advancing-automatic-speech-recognition/)
- [Whisper Code-Switching Discussion](https://github.com/openai/whisper/discussions/49)

### Datasets
- [MUCS 2021 (OpenSLR 104)](https://www.openslr.org/104/)
- [CS-FLEURS](https://www.isca-archive.org/interspeech_2025/yan25c_interspeech.html)
- [IndicVoices](https://ai4bharat.iitm.ac.in/areas/data/IndicVoices)
