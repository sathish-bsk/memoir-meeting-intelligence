# Speaker Diarization — Complete Research & Beginner's Guide

> This document contains the full research behind Phase 2 (Speaker Diarization) of the TranscriptionBase project. It includes beginner-friendly explanations of all terminology, model comparisons, competitor analysis, edge case handling, and architectural recommendations.

---

## Table of Contents

1. [What is Speaker Diarization?](#what-is-speaker-diarization)
2. [Why Do We Need It?](#why-do-we-need-it)
3. [Terminology Explained (Beginner-Friendly)](#terminology-explained)
4. [How Diarization Works (The Big Picture)](#how-diarization-works)
5. [Model Comparison & Benchmarks](#model-comparison--benchmarks)
6. [Competitor Analysis](#competitor-analysis)
7. [Edge Cases & Challenges](#edge-cases--challenges)
8. [Recommended Architecture for Phase 2](#recommended-architecture)
9. [Research Sources](#research-sources)

---

## What is Speaker Diarization?

**The simplest explanation:** You have a recording of a phone call. Two people are talking. Diarization answers the question: **"Who spoke when?"**

It doesn't know their names. It just labels them as Speaker A and Speaker B, and tells you the exact time ranges each person was talking.

**Before diarization:**
```
"Hi, how are you? I'm good thanks. So about the pricing..."
```

**After diarization:**
```
Speaker A [0:00-0:02]: "Hi, how are you?"
Speaker B [0:02-0:04]: "I'm good thanks."
Speaker A [0:04-0:08]: "So about the pricing..."
```

The word "diarization" comes from "diary" — it's like writing a diary of who spoke at what time.

---

## Why Do We Need It?

Phase 1 already transcribes audio to text. But the output is just a wall of text — you don't know who said what. For a sales intelligence platform, you NEED to know:
- What did the **customer** say? (objections, pain points, interest signals)
- What did the **sales rep** say? (promises, pricing discussed, next steps offered)

Without diarization, you can't build Phase 5 (intelligence extraction). You can't tell a CRM: "The customer mentioned competitor X at minute 12" because you don't know which speaker is the customer.

**Phase 1 output:** "Here's what was said"
**Phase 2 output:** "Here's what was said AND who said it"

---

## Terminology Explained

### Core Concepts

#### DER (Diarization Error Rate)

**What it means:** The percentage of time the system got wrong. Lower = better.

Think of it like a test score in reverse — if DER is 15%, the system got 85% of the time correct.

DER has 3 types of errors:
- **Missed speech:** The system didn't detect someone was talking (it thought it was silence). Like a student not answering a question at all.
- **False alarm:** The system thought someone was talking when nobody was (it hallucinated speech). Like answering a question that wasn't asked.
- **Speaker confusion:** The system heard speech correctly but attributed it to the wrong person. Like getting the answer right but writing it on the wrong line.

**What's good?**
- < 10% DER = Excellent (production-grade)
- 10-20% DER = Good (usable)
- > 20% DER = Needs improvement

**Example:** In a 100-second call:
- 5 seconds of missed speech + 3 seconds of false alarm + 7 seconds of speaker confusion = **15% DER**

#### WDER (Word Diarization Error Rate)

Same idea as DER, but measured per **word** instead of per time-frame. More practical for transcription products because it tells you: "out of all the words in the transcript, what percentage got assigned to the wrong speaker?"

Rev.ai argues WDER is a better metric than DER because it measures what the user actually sees (words with speaker labels), not abstract time segments.

#### RTF (Real-Time Factor)

How fast the system processes audio relative to the audio's duration.

- **RTF = 1x:** 1 hour of audio takes 1 hour to process (as fast as listening to it)
- **RTF = 45x:** 1 hour of audio takes 80 seconds (45 times faster than real-time)
- **RTF = 0.5x:** 1 hour of audio takes 2 hours (slower than real-time)

| Setup | RTF | 1-hour call processing time |
|-------|-----|---------------------------|
| Pyannote on GPU | ~45x | ~1.5 minutes |
| Pyannote on CPU | ~0.5-1x | 30-60 minutes |
| Sortformer v2 on GPU | ~214x | ~17 seconds |

### Speaker Identification Concepts

#### Speaker Embeddings (Voice Fingerprints)

**Think of it like a fingerprint, but for voice.**

When you talk, your voice has unique characteristics — your pitch, how nasal you sound, how fast you speak, your accent, the shape of your vocal tract. A speaker embedding is a **list of numbers** (called a "vector") that captures these characteristics.

```
Person A's voice → [0.23, -0.45, 0.87, 0.12, -0.33, ...]  (256 numbers)
Person B's voice → [-0.67, 0.33, -0.21, 0.89, 0.15, ...]  (256 numbers)
```

Two recordings of the **same person** produce similar number lists. Two **different people** produce different number lists. The computer compares these lists to decide "same speaker or different speaker?"

**How similar is "similar enough"?** This is measured by **cosine similarity** — a math formula that compares the "angle" between two number lists:
- Score of 1.0 = Identical voices (same person)
- Score > 0.7 = Probably same person
- Score of 0.0 = Completely different voices

**Models that create embeddings:**

| Model | Who made it | What it is |
|-------|-----------|-----------|
| **ECAPA-TDNN** | SpeechBrain | A neural network designed specifically for voice recognition. One of the most accurate. |
| **ResNet34** | Microsoft (originally for images) | A deep learning architecture originally for image recognition, adapted to process voice spectrograms. |
| **TitaNet** | NVIDIA | NVIDIA's custom speaker embedding model. |
| **WeSpeaker ResNet34** | WeNet team | What pyannote uses. Trained on VoxCeleb (a dataset of ~7,000 celebrity voice recordings from YouTube interviews). |

#### Voiceprint / Voice Enrollment

Like Face ID on your phone, but for voice. You record a person's voice a few times, the system creates their "voice fingerprint" (embedding) and stores it. Next time they call, the system compares the new voice against stored fingerprints and says "that's John from sales."

**How Gong (our main competitor) does it:**
1. Sales rep opts in and records 5 short voice samples
2. After 2 recorded calls, the system can identify them
3. Samples are refreshed over time (voice changes with new headsets, different rooms, etc.)
4. The system can then retroactively label old calls: "oh, this was John in all these recordings"

**Privacy warning:** Voice is classified as **biometric data** (like fingerprints). Under GDPR in Europe, this requires explicit opt-in consent. Under Illinois BIPA in the US, there are statutory damages of $1,000-$5,000 per violation. Always make voice enrollment opt-in only.

### Audio Concepts

#### VAD (Voice Activity Detection)

You already have this in Phase 1 (Silero VAD). It simply answers: **"Is someone talking right now, yes or no?"** It doesn't care WHO is talking — just whether there's speech at all.

Think of it as a light switch: speech = ON, silence = OFF. The output is a timeline:
```
[silence] [SPEECH 0:00-0:05] [silence 0:05-0:07] [SPEECH 0:07-0:15] [silence] ...
```

**Diarization goes further:** not just "is someone talking" but "WHO is talking."

#### Overlap / Cross-Talk

When two people talk at the same time. Very common in conversations:
- **Interruptions:** "But wait—" "Let me finish—"
- **Backchannels:** "uh-huh", "right", "yeah" while the other person keeps talking
- **Excited agreement:** Both speakers saying something at the same time
- **Turn transitions:** Brief overlap as one speaker starts before the other finishes

**How common:** About 3-4 overlaps per minute in conversation. Most are very brief (under 1 second). Less than 5% of total speaking time involves overlap.

**Why it's hard for computers:** If you hear two voices mixed together in a single audio track, separating "this part is Person A, this part is Person B" is like trying to separate two colors of paint after mixing them — possible but much harder than keeping them in separate cans.

#### Backchannel

Short verbal responses that don't interrupt the speaker: "yeah", "uh-huh", "right", "ok", "hmm", "interesting". They signal the listener is paying attention.

**Why they matter for sales calls:** A customer saying "uh-huh" a lot means they're engaged. Silence might mean they've tuned out or are distracted. But these sounds are only 200-500 milliseconds long — very hard for diarization to catch correctly.

#### Multichannel vs Mono

**Mono:** One audio track with all speakers mixed together. Like a single microphone in the middle of a room — it captures everything mixed into one stream. This is the hard case for diarization.

**Multichannel/Stereo:** Each speaker has their own separate audio track. Like giving each person their own microphone — Speaker A on the left channel, Speaker B on the right channel.

**Key insight from Deepgram and AssemblyAI:** If you have multichannel audio, **don't use diarization at all.** Just label each channel as a speaker — it's almost 100% accurate. Many call center systems record calls with separate inbound (customer) and outbound (agent) channels. Our system should detect this and exploit it.

#### Narrowband vs Wideband Audio

| Type | Sample Rate | Frequency Range | Sounds Like |
|------|-------------|----------------|-------------|
| **Narrowband** (old telephone, PSTN) | 8 kHz | 0 - 4,000 Hz | Muffled, "phone voice" |
| **Wideband** (VoIP, Zoom, modern phone) | 16-48 kHz | 0 - 8,000+ Hz | Clear, natural |

Higher frequencies (4,000-8,000 Hz) contain sounds like "s", "f", "th" that help both:
- **ASR:** Distinguishing "fifteen" from "thirteen"
- **Speaker identification:** Distinguishing different voices (more frequency information = more unique characteristics to compare)

**Phase 1 already handles this** with bandwidth extension (upsampling 8kHz → 16kHz using SoX resampler). This helps both transcription AND diarization accuracy.

#### Speaker Drift

Over a long call (1-2 hours), a person's voice changes:
- **Fatigue:** Voice gets lower, slower, more monotone
- **Emotion:** Excitement during negotiation makes voice louder, faster, higher-pitched
- **Environment:** Moving from quiet office to noisy corridor
- **Equipment:** Switching from headset to speakerphone mid-call

The diarization system expects a speaker to sound consistent. If Speaker A at minute 5 sounds very different from Speaker A at minute 90, the system might incorrectly split them into two different speakers ("Speaker A" and "Speaker C").

### Machine Learning Concepts

#### Segmentation

Before you can create voice fingerprints, you need to know **where speech is happening** and **where speakers change.** Segmentation divides the audio into small time-labeled chunks:

```
0.0s - 5.2s:  Speech (Speaker A region)
5.2s - 5.5s:  Non-speech (pause)
5.5s - 12.8s: Speech (Speaker B region)
12.8s - 13.0s: Non-speech
13.0s - 15.5s: Speech (Speakers A AND B overlapping)
```

#### Powerset Encoding (Pyannote's Overlap Detection)

This is pyannote's clever trick for handling overlapping speech.

**The naive approach** (doesn't work well): Ask "is Speaker 1 talking? yes/no" and "is Speaker 2 talking? yes/no" independently. Problem: these two questions are not independent — if both are talking, the audio sounds very different from either talking alone.

**Pyannote's approach:** Ask ONE question with 7 possible answers:

| Class | What's happening |
|-------|-----------------|
| 0 | Nobody talking (silence) |
| 1 | Only Speaker 1 talking |
| 2 | Only Speaker 2 talking |
| 3 | Only Speaker 3 talking |
| 4 | Speaker 1 AND Speaker 2 talking at the same time |
| 5 | Speaker 1 AND Speaker 3 talking at the same time |
| 6 | Speaker 2 AND Speaker 3 talking at the same time |

By training the neural network on all these combinations explicitly, it learns what overlapping speech "sounds like" and can detect it directly. This is called "powerset" because it covers the "power set" (all possible subsets) of speakers.

**Limitations:**
- Maximum 3 speakers per 10-second window
- Maximum 2 speakers overlapping at any given instant
- Cannot detect 3-way simultaneous speech (extremely rare in sales calls)

#### Clustering

After extracting voice fingerprints for every small segment of audio, you have hundreds of fingerprints. **Clustering** groups them by speaker without knowing in advance how many speakers there are.

**Analogy:** Imagine you have 300 colored balls scattered on a table, but you're colorblind. You can measure the exact wavelength of each ball's color. Clustering means grouping balls by similar wavelength — "these 150 are all ~700nm (red), these 120 are all ~450nm (blue), these 30 are all ~520nm (green)" — and discovering that there are 3 colors, without anyone telling you.

**Types of clustering used in diarization:**

| Method | How it works | Used by | Good at |
|--------|-------------|---------|---------|
| **Agglomerative (HAC)** | Start with each segment as its own group. Repeatedly merge the two most similar groups. Stop when similarity drops below a threshold. | Pyannote 3.1 | Simple, well-understood |
| **VBx (Variational Bayes)** | Statistical method that models speakers as a sequence — it knows "speakers take turns, they don't randomly switch every 0.5 seconds." Uses probability theory to find the most likely speaker assignment. | Pyannote community-1 | **Much better at counting speakers correctly.** This is the main reason community-1 outperforms 3.1 |
| **Spectral clustering** | Converts the similarity problem into a graph (dots connected by lines of varying thickness based on similarity), then finds natural groups in the graph. | Some research systems | Handles unusual cluster shapes |

#### EEND (End-to-End Neural Diarization)

**Traditional approach (what pyannote does):** Three separate steps — (1) find speech segments, (2) create voice fingerprints, (3) cluster them. Each step is a separate model or algorithm.

**EEND approach:** One single neural network does everything at once. Audio goes in, speaker labels come out. No separate steps.

| Aspect | Traditional (Pyannote) | EEND (Sortformer) |
|--------|----------------------|-------------------|
| Architecture | 3 separate steps | 1 unified model |
| Overlap handling | Needs special encoding (powerset) | Natural — multiple output heads active simultaneously |
| Speed | Moderate (45x RTF) | Very fast (214x RTF) |
| Max speakers | Unlimited | **Fixed limit (usually 4)** |
| Maturity | Production-ready | Newer, less tested |

**EEND's big limitation:** It has a fixed number of output "slots" (typically 4). If there are 5+ speakers, it can't handle them. This makes it unsuitable for conference calls. For 2-speaker sales calls it's fine, but the architectural constraint is risky.

#### PIT (Permutation Invariant Training)

A training trick for EEND models. The problem: if the model outputs "Speaker 1" and "Speaker 2", but the training data happens to label them as "Speaker 2" and "Speaker 1", the model would be penalized even though it got the right answer — it just used different labels.

PIT solves this by trying all possible label assignments during training:
- Try mapping model's Speaker 1 → training Speaker 1, model's Speaker 2 → training Speaker 2
- Try mapping model's Speaker 1 → training Speaker 2, model's Speaker 2 → training Speaker 1
- Use whichever mapping gives the lowest error

Without PIT, EEND models can't be trained at all — they'd get confused by the arbitrary label ordering.

#### Silhouette Score (Diarization Confidence)

A way to measure how confident the system is about each speaker assignment. For each segment, it asks: "How similar is this segment to its assigned speaker cluster vs. the next-closest speaker cluster?"

**High silhouette score (close to 1.0):** "This segment CLEARLY belongs to Speaker A — it's very similar to other Speaker A segments and very different from Speaker B segments."

**Low silhouette score (close to 0.0):** "This segment could go either way — it's about equally similar to Speaker A and Speaker B segments."

**Negative silhouette score:** "This segment is actually MORE similar to a different speaker's cluster — it might be misassigned."

**Why this matters:** We can use silhouette scores to flag unreliable segments for human review, rather than showing users diarization results we're not confident about.

#### Exclusive Mode (Pyannote community-1)

Normally, diarization might say "Speaker A talks from 3.0-5.2s AND Speaker B talks from 3.8-5.2s" (overlap region from 3.8 to 5.2). This is accurate but creates a problem for ASR: if you send the overlap region to Whisper, whose text is it?

**Exclusive mode** forces exactly one speaker per moment:
- Before: Speaker A [3.0-5.2s] + Speaker B [3.8-5.2s] (overlap at 3.8-5.2s)
- After: Speaker A [3.0-3.8s], Speaker B [3.8-5.2s] (the "dominant" speaker gets the overlap)

It picks the speaker most likely to produce useful transcription. This dramatically simplifies ASR integration — you just send each non-overlapping segment to Whisper and attach the speaker label.

#### DiarizationLM

A newer approach (Google, 2024) where you use a Large Language Model (like GPT) to **fix diarization mistakes** after transcription. The LLM reads the transcript with speaker labels and uses context clues to correct obvious errors.

For example, if the transcript says:
```
Speaker A: "What's the price?"
Speaker A: "It's $500 per month."
Speaker B: "That seems high."
```

An LLM could recognize that "What's the price?" and "It's $500 per month" are a question-answer pair from different speakers, and fix the labels.

**Critical warning:** This ONLY works with **fine-tuned** LLMs (trained specifically on diarization correction data). Using a generic LLM out of the box (zero-shot) made things **1677% worse** in Google's research — the LLM hallucinated speaker labels randomly. Fine-tuning is mandatory.

---

## How Diarization Works (The Big Picture)

Here's what happens when someone uploads a sales call, step by step:

```
Sales call recording (48 minutes, mono audio)
    │
    ▼
Phase 1 (ALREADY BUILT):
    Clean the audio — remove noise, silence, normalize volume
    Output: clean 16kHz mono WAV file
    │
    ▼
Phase 2 (WHAT WE'RE BUILDING):
    │
    ▼
Step 0: Is this multichannel?
    The system checks if the audio has separate channels per speaker.
    Many call center systems record the agent and customer on separate channels.
    │
    ├── YES (separate channels) → Just label each channel as a speaker.
    │   No AI needed. Near-perfect accuracy. Done.
    │
    └── NO (mono, all voices mixed) → Continue to AI-based diarization...
    │
    ▼
Step 1: Segmentation
    A small neural network (PyanNet, ~2MB) slides across the audio in 10-second windows.
    For each tiny time slice (about 17ms), it outputs:
    "nobody talking" / "speaker 1" / "speaker 2" / "both talking"

    Result: A timeline showing WHERE speech happens and WHERE speakers change.
    │
    ▼
Step 2: Voice Fingerprinting
    For each speech segment found in Step 1, a bigger neural network (ResNet34, ~30MB)
    creates a "voice fingerprint" — a list of 256 numbers capturing what that voice sounds like.

    Result: Hundreds of fingerprint vectors, one per segment.
    Like having 300 unlabeled paint color samples.
    │
    ▼
Step 3: Clustering (Grouping)
    The VBx algorithm groups all the fingerprints:
    "These 150 fingerprints sound similar → call them SPEAKER_00"
    "These 120 fingerprints sound similar → call them SPEAKER_01"
    "These 30 fingerprints sound similar → call them SPEAKER_02"

    It figures out BOTH how many speakers there are AND which segments belong to whom.
    │
    ▼
Step 4: Exclusive Diarization
    Convert overlapping segments into non-overlapping:
    Pick the "dominant" speaker at each moment.

    Output:
    SPEAKER_00: 0:00 - 0:05
    SPEAKER_01: 0:05 - 0:12
    SPEAKER_00: 0:12 - 0:25
    SPEAKER_01: 0:25 - 0:30
    ...
    │
    ▼
Step 5: Per-Segment Transcription
    Send each speaker's segment to Whisper (our existing Phase 1 ASR).
    Attach the speaker label to the transcribed text.

    Output:
    SPEAKER_00 [0:00]: "Hi, thanks for taking the call today."
    SPEAKER_01 [0:05]: "Sure, I've been looking at your product."
    SPEAKER_00 [0:12]: "Great! Let me show you what we offer."
    │
    ▼
Step 6: Confidence Check
    Calculate silhouette scores to flag any segments where the speaker
    assignment might be wrong. Low-confidence segments can be:
    - Highlighted in the UI for human review
    - Excluded from automated intelligence extraction (Phase 5)
    │
    ▼
Phase 5 (FUTURE):
    Now an LLM can analyze the speaker-attributed transcript:
    "The CUSTOMER mentioned pricing concerns at 12:30"
    "The SALES REP promised a demo at 25:00"
    → Push structured data to Zoho CRM
```

---

## Model Comparison & Benchmarks

### All Models Evaluated

#### Pyannote Family (Recommended)

**Pyannote speaker-diarization-3.1:**
- The established open-source pipeline. Pure PyTorch.
- Architecture: PyanNet segmentation + WeSpeaker ResNet34 embeddings + agglomerative clustering
- DER: AMI 18.8%, CALLHOME 28.5%, DIHARD III 21.7%
- RTF: ~45x on GPU
- License: MIT (fully commercial)
- Known issue: tends to over-estimate speaker count

**Pyannote speaker-diarization-community-1 (v4.0, 2025):**
- Major improvement over 3.1. Same segmentation model but VBx clustering instead of agglomerative.
- DER: AMI 17.0%, CALLHOME 26.7%, DIHARD III 20.2% (~10% relative improvement)
- Exclusive diarization mode for STT alignment
- License: CC-BY-4.0 (commercial OK with attribution)
- Requires Python 3.10+

**PyannoteAI Precision-2 (Commercial):**
- +28% better than open-source 3.1
- 2x faster
- Best overall: 11.2% average DER
- Cloud API or on-premise enterprise license (paid)

#### NVIDIA NeMo Family

**NeMo MSDD (Multi-Scale Diarization Decoder):**
- Pipeline: VAD (MarbleNet) → Embedding (TitaNet) → Multi-scale clustering → MSDD refinement
- Can reduce DER up to 60% vs single-scale on 2-speaker datasets
- Has telephonic-specific model
- License: Apache 2.0 (code), model weights vary
- Strongly GPU-oriented. Complex NeMo framework required.

**Sortformer v2 / v2.1:**
- End-to-end (EEND) approach. 18-layer Conformer encoder.
- RTF: 214x (incredibly fast — 17 seconds per hour of audio on GPU)
- **4-speaker hard limit.** Performance degrades significantly with 5+ speakers (22.7% DER)
- v2 license: CC-BY-NC-SA-4.0 (non-commercial only)
- v2.1 license: NVIDIA Open Model License (commercial OK)
- Best for Mandarin (9.2% DER) and Japanese (12.7% DER), weaker on English (15.3%)

#### DiariZen (Brno University of Technology)

- Hybrid: pyannote segmentation + local EEND (WavLM + Conformer) + cross-chunk clustering
- Average DER: 13.3%
- **Best for 5+ speakers: 7.1% DER** (best of any model tested)
- WavLM parameters pruned from 316M to 63M (80% sparsity) for efficiency
- License: MIT (commercial OK)
- Newer, less battle-tested than pyannote

#### WhisperX

- Not a standalone diarization model — orchestrates Whisper + Wav2Vec2 + pyannote
- Provides word-level speaker labels (the gold standard for transcription products)
- Performance depends on which pyannote model is used underneath
- License: BSD-4-Clause (but pyannote models have their own licenses)
- Better suited for Phase 3 upgrade

#### Emerging (2025-2026)

**SpeakerLM (AAAI 2026):** First end-to-end diarization + recognition using a multimodal LLM (SenseVoice + Qwen2.5-7B). Jointly performs diarization AND ASR in one model. Too new and large for production, but represents the future direction.

### Benchmark Comparison Table

From the September 2025 SDBench paper (196.6 hours of multilingual audio):

**DER by Language (%):**

| Model | English | Mandarin | German | Japanese | Spanish | Average |
|-------|---------|----------|--------|----------|---------|---------|
| PyannoteAI (premium) | **6.6** | 10.0 | **8.3** | 13.8 | **14.3** | **11.2** |
| DiariZen | 7.0 | 10.1 | 11.6 | 15.6 | 19.1 | 13.3 |
| Sortformer v2 | 15.3 | **9.2** | 9.6 | **12.7** | 21.1 | — |
| Pyannote 3.1 (OSS) | 11.5 | 19.8 | 19.0 | 28.8 | 27.3 | — |

**DER by Speaker Count (%):**

| Model | 2 Speakers | 3 Speakers | 4 Speakers | 5+ Speakers |
|-------|-----------|-----------|-----------|------------|
| PyannoteAI (premium) | **9.9** | **9.1** | **10.1** | 6.6 |
| DiariZen | 11.4 | 10.3 | 12.7 | **7.1** |
| Sortformer v2 | 10.1 | 14.3 | 16.7 | 22.7 |
| Pyannote 3.1 (OSS) | 19.9 | 19.8 | 17.1 | 10.6 |

**Processing Speed:**

| Model | RTF | 1-hr audio time |
|-------|-----|----------------|
| Sortformer v2 | 214.3x | ~17 seconds |
| Pyannote 3.1 | 45.0x | ~80 seconds |
| DiariZen | 20.2x | ~3 minutes |

### Community-1 vs 3.1 (Detailed)

| Benchmark | 3.1 DER | Community-1 DER | Improvement |
|-----------|---------|----------------|-------------|
| AISHELL-4 | 12.2% | 11.7% | -0.5% |
| AliMeeting (ch1) | 24.5% | 20.3% | -4.2% |
| AMI (headset) | 18.8% | 17.0% | -1.8% |
| AMI (array) | 22.7% | 19.9% | -2.8% |
| CALLHOME (pt2) | 28.5% | 26.7% | -1.8% |
| DIHARD III | 21.4% | 20.2% | -1.2% |
| MSDWild | 25.4% | 22.8% | -2.6% |

Community-1 improves across almost every benchmark.

### Why We Chose Pyannote Community-1

| Requirement | Pyannote community-1 | Best Alternative |
|-------------|----------------------|-----------------|
| Commercial license | CC-BY-4.0 (yes) | DiariZen MIT (yes) |
| Handles unknown speaker count | Yes (VBx auto-detect) | Sortformer: NO (4 max) |
| Handles overlap | Yes (powerset, 2 per frame) | Sortformer: yes (native) |
| CPU viable | Yes (slow but works) | Sortformer: NO |
| GPU VRAM | 6-8 GB | DiariZen: 8-16 GB |
| English accuracy | Good (~7-17% DER) | DiariZen slightly better |
| Ease of integration | 3 lines of code | NeMo: very complex |
| Production maturity | 140K+ developers | DiariZen: newer |
| Exclusive mode for ASR | Yes | Others: no |
| Python 3.10+ | Required | 3.1 works with 3.8+ |

---

## Competitor Analysis

### How Major Players Implement Diarization

#### Gong.io (Closest Competitor — Sales Call Intelligence)

Gong is the gold standard for sales call analysis. They use a **multi-signal approach** that goes far beyond pure audio diarization:

**Three methods to identify speakers:**

1. **Telephony (mono audio):** Standard audio diarization separates voices. Then ML classifies which speaker is the "company side" (sales rep) vs customer.

2. **Web conferencing metadata:** For Zoom/Teams/Meet calls, Gong reads participant metadata — who joined, their display name, email — and maps speakers using this information. Much more reliable than pure audio.

3. **Visual diarization (patented):** US Patent describes using video stream analysis with neural networks to detect who is speaking from visual cues (lip movement, gestures). Used for talk-time analysis and interactivity metrics.

**Voice Enrollment (Speaker Recognition):**
- Collects up to 5 short recordings per enrolled sales rep (opt-in)
- After the 2nd recorded call, can typically identify the individual
- Voice samples refreshed continuously (handles new headsets, environments)
- Once enough samples exist, retroactively re-identifies speakers in earlier calls
- CRM integration maps speakers to deal records (Salesforce, HubSpot, Dynamics)

**Takeaway for us:** Start with audio diarization (Phase 2), plan voice enrollment for Phase 5 (CRM integration). Use meeting metadata when available.

#### Rev.ai

**Open-sourced their diarization as "Reverb":**
- Reverb v1: Identical to pyannote 3.0 architecture, fine-tuned on Rev's 26,000 hours of human-labeled data for 17 epochs
- Reverb v2: Replaced feature extractor with WavLM (self-supervised speech model)
- Results: 22.25% relative improvement in WDER over pyannote 3.0

**Key insight:** The architecture matters less than the training data. Rev took the same pyannote architecture and made it dramatically better just by training on domain-specific data. This suggests we should plan for fine-tuning on our own sales call data.

#### Deepgram

**Cascade system:** segmentation → embeddings → density-based clustering
- Embeddings trained on **100,000+ speakers** across **80+ languages**
- Per-word speaker attribution (not just per-segment)
- **No fixed limit** on speaker count
- Supports both streaming and batch
- 53.1% improved accuracy from previous version
- Key recommendation: Use **multichannel audio** when available — separate channels per speaker gives far superior results vs single-channel diarization

#### AssemblyAI

**Custom in-house framework** with proprietary innovations:
- Higher resolution: 16kHz input (not 8kHz) for speaker embeddings
- Advanced data augmentation targeting exact failure scenarios
- DER improved by 10.1%, cpWER by 13.2%
- **85.4% reduction in speaker count errors** (count error rate down to 2.9%)
- 43% improvement on short segments (250ms) in noisy conditions
- Default max 10 speakers, configurable higher

#### Others

| Provider | Key Fact |
|----------|---------|
| **Otter.ai** | Leverages meeting platform metadata + speech separation for overlap. Real-time focus. |
| **Recall.ai** | "Perfect diarization" — transcribes separate audio streams per Zoom/Meet/Teams participant. 100% accurate labels. Only works for remote meetings. |
| **Google Chirp 3** | Built-in diarization, struggles with overlap. No published DER. |
| **Amazon Transcribe** | Max 30 speakers, optimal 2-5. Serializes overlaps (misses simultaneous speech). |
| **Microsoft Azure** | **Cannot separate overlapping speakers at all.** Recently retired multichannel option. |

### Competitor Comparison Table

| Provider | Architecture | Overlap | # Speakers | Rep/Customer ID | Real-time |
|----------|-------------|---------|-----------|----------------|-----------|
| **Gong** | Multi-modal (audio+video+metadata+enrollment) | Undocumented | No limit | Yes (enrollment+CRM) | No (batch) |
| **Rev.ai** | Pyannote-based, fine-tuned on 26K hrs | Via pyannote | Auto-detect | No | Limited |
| **Deepgram** | Cascade, 100K+ speaker embeddings | Per-word | No limit | No | Yes |
| **AssemblyAI** | Custom embeddings, 16kHz | Yes | Default 10 | No | Limited |
| **Recall.ai** | Separate streams per participant | Perfect | From platform | Yes (metadata) | Yes |
| **Azure** | Mono add-on | **Cannot** | Unreliable >2 | No | Yes |
| **Amazon** | Internal | Serializes | Max 30 (opt 2-5) | No | Yes |

### Key Lessons for Our Implementation

1. **Multichannel is the "cheat code"** (Deepgram, Recall.ai) — detect and exploit separate channels
2. **Domain-specific fine-tuning matters more than architecture** (Rev.ai) — plan for fine-tuning on sales calls
3. **Voice enrollment enables rep identification** (Gong) — plan for Phase 5
4. **Better embeddings = fewer count errors** (AssemblyAI) — use 16kHz input, which Phase 1 already provides
5. **WDER > DER as the user-facing metric** (Rev.ai) — track what users actually experience
6. **Zero-shot LLM correction is catastrophically bad** (Google) — don't try it without fine-tuning

---

## Edge Cases & Challenges

### 1. Overlapping Speech / Cross-Talk

**How common in sales calls:**
- Less than 5% of total speaking time involves overlap
- But 30-50% of turn exchanges contain brief overlap
- 3-4 overlaps per minute on average
- Most overlaps are under 1 second
- Cross-talk increases during objection handling and negotiation

**How pyannote handles it:**
Powerset encoding detects up to 2 speakers overlapping per frame. The segmentation model was explicitly trained on overlapping speech combinations. Community-1's exclusive mode then picks the dominant speaker for each moment, simplifying ASR alignment.

**What we lose:** In overlap regions, exclusive mode attributes all speech to one speaker. The other speaker's overlapping words are "lost" for transcription. For brief backchannels ("uh-huh") this is acceptable. For extended cross-talk, some information is missed.

**State of the art (2025):** The most advanced approach is "overlap-adaptive hybrid diarization" (Interspeech 2025) which routes to different methods based on detected overlap percentage. For our use case (2-speaker sales calls with brief overlaps), pyannote's powerset + exclusive mode is sufficient.

### 2. Variable Number of Speakers (N Speakers)

**The challenge:** You don't know in advance how many people will be on a call. Could be 2 (1-on-1 sales call), 3 (sales rep + customer + manager), 5 (group demo), or even 10+ (webinar).

**How pyannote handles it:** VBx clustering auto-estimates speaker count without needing it upfront. You can provide hints:
- `num_speakers=2` — force exactly 2 (best accuracy when known)
- `min_speakers=2, max_speakers=5` — constrain the search range
- No parameters — fully automatic estimation

**Performance by count:**
- 2 speakers: ~10% DER (easiest, most common for sales)
- 3-4 speakers: ~10-13% DER (slight increase)
- 5+ speakers: varies (community-1 handles well, Sortformer degrades to 22.7%)

**Recommendation:** For known 1-on-1 calls, set `num_speakers=2`. For general use, set `min_speakers=2, max_speakers=10`. Consider adding a UI option for "call type" (1-on-1, group call, webinar).

### 3. Short Speaker Turns (Backchannels)

**The challenge:** "Yes", "Uh-huh", "Right" are 200-500ms long. These are too short to reliably extract voice fingerprints from, so they often get:
- Misattributed (assigned to the wrong speaker)
- Dropped entirely (below minimum duration threshold)
- Merged with adjacent segments from a different speaker

**How pyannote handles it:** Configurable `min_duration_on` parameter. Default behavior keeps segments as short as ~250ms.

**AssemblyAI's approach:** They specifically optimized for 250ms segments, achieving 43% improvement in noisy conditions. Their secret: advanced data augmentation that trains the model on short utterances specifically.

**Our recommendation:**
- Set `min_duration_on=0.25s` — preserve backchannels
- Accept slightly higher error rates on sub-500ms segments
- In the UI, show confidence indicators for short segments
- For Phase 5 intelligence extraction, weight longer segments more heavily

### 4. Similar Voices (Same Gender)

**The challenge:** If two speakers have very similar voices (same gender, similar age, similar accent), the voice fingerprints will be very close together, and the clustering algorithm may merge them into one speaker.

**Factors that make voices more similar:**
- Same gender (male-male or female-female)
- Similar age range
- Same regional accent
- Telephone narrowband (8kHz) — removes distinguishing high-frequency information
- Codec compression artifacts

**How to mitigate:**
- **Constrain speaker count** — if you know there are 2 speakers, force it. The system won't merge them even if voices are similar.
- **Use wideband audio** — Phase 1's bandwidth extension from 8kHz to 16kHz helps by recovering more frequency information.
- **Longer context windows** — more speech from each speaker provides more discriminative information.
- **Multi-scale embeddings** — extract fingerprints at different time scales and combine for more robust comparison.

**Research finding:** A 2024 study comparing embedding models found ECAPA-TDNN achieves 1.71% Equal Error Rate on close-talking audio but degrades significantly on narrowband. WavLM (used by Reverb v2 and DiariZen) showed more dispersed embeddings that may actually help in the similar-voice case.

### 5. Code-Switching / Multilingual Calls

**The challenge:** Sales calls in English + Tamil/Hindi. When a speaker switches languages mid-sentence, does the voice fingerprint change?

**Short answer:** Mostly no. Speaker embeddings capture vocal tract characteristics (pitch, timbre, formant structure) which are physical properties of the speaker's voice, not the language they're speaking. A person's voice sounds like "them" regardless of which language they use.

**However, in practice:**
- Models trained primarily on English (VoxCeleb) may encode subtle language-correlated features
- Acoustic characteristics of different phoneme inventories (Tamil retroflex consonants vs English fricatives) can slightly affect embeddings
- The DISPLACE 2024 challenge found language identification accuracy dropped from 95.4% to 22.2% on code-switched conversations

**For our use case:** Speaker diarization should still work well across languages. The bigger challenge is on the ASR side (Phase 1's multilingual mode handles this). Run diarization BEFORE ASR — diarize on acoustic characteristics, then send each speaker segment to the appropriate ASR model.

### 6. Long Recordings (1-2 Hours)

**Memory:** Pyannote uses sliding windows (10-second chunks), so memory usage is bounded regardless of audio length. Not an issue.

**Processing time:**
- GPU: ~1.5-4 minutes per hour of audio
- CPU: ~30-60 minutes per hour of audio

**Speaker drift:** Over a long call, voices change due to fatigue, emotion, environment changes. VBx's temporal modeling handles gradual drift because it considers the sequence of speaker assignments, not just individual segments in isolation.

**Scalability optimizations:** Research shows increasing the sliding window stride from 1 to 4 achieves 15-38x speedup with only 0.02 DER increase for calls with up to 5 speakers.

### 7. Channel Effects

**Telephone vs VoIP vs in-person:**

| Channel | Sample Rate | Challenges |
|---------|------------|------------|
| PSTN telephone | 8kHz | Narrowband, codec artifacts, less discriminative |
| VoIP (Zoom, Teams) | 16-48kHz | Packet loss, jitter, echo cancellation artifacts |
| In-person recording | Varies | Room reverb, distance from mic, ambient noise |

**Different microphone quality per speaker:** Surprisingly, this can HELP — the channel characteristics become an additional discriminative feature. One speaker on a headset and another on speakerphone will sound more different than if both used the same equipment.

**Echo and feedback:** If Speaker A's voice echoes through Speaker B's microphone, the diarizer may attribute the echo to Speaker B. Most VoIP platforms apply echo cancellation, but raw call recordings may not have this. Consider adding echo detection to the pipeline.

**Multichannel recordings:** The single most important optimization. If your telephony system provides separate channels (common in call centers), detect and use channel-based separation instead of model-based diarization. This is near-perfect accuracy with zero ML.

### 8. Integration with ASR

**Three approaches to combining diarization + transcription:**

| Approach | How | Pros | Cons |
|----------|-----|------|------|
| **Diarize-then-transcribe** | Run diarization first, send each speaker segment to ASR | Speaker labels guide ASR. Clean separation. | Diarization errors propagate to ASR |
| **Transcribe-then-diarize** | Run ASR first, then assign speaker labels to words | ASR unaffected by diarization errors | Imprecise word timestamps make assignment noisy |
| **Joint (end-to-end)** | One model does both simultaneously | No error propagation, theoretically optimal | Harder to train, less flexible, newer |

**Our approach (Phase 2):** Diarize-first. Pyannote runs on the preprocessed audio, produces speaker segments, then each segment goes through our existing Whisper pipeline. This is the simplest and most modular approach.

**Phase 3 upgrade:** WhisperX adds word-level speaker labels by running Whisper + Wav2Vec2 forced alignment + pyannote, then matching each word's timestamp to a speaker segment. This is the gold standard for transcription products.

### 9. Speaker Re-Identification Across Calls

**The goal:** "This is the same sales rep from last week." Associate speaker embeddings with named identities across multiple calls.

**How Gong does it:**
1. Sales rep opts in and Gong collects up to 5 voice samples
2. After 2 calls with enough speech, the system can identify them
3. Samples are refreshed over time
4. The system retroactively updates old calls when it learns a new voice

**Building our own (Phase 5 feature):**
1. After diarization, extract a high-quality embedding for each speaker
2. Store the embedding in a database, keyed to the user's identity
3. On subsequent calls, compare new embeddings against stored ones (cosine similarity > 0.7 = match)
4. Re-enroll periodically to handle voice drift

**Privacy requirements (CRITICAL):**
- GDPR (Europe): Voiceprints are biometric data under Article 9. Processing is prohibited by default unless you have explicit consent.
- BIPA (Illinois, US): Statutory damages of $1,000-$5,000 per violation for collecting biometric data without consent.
- Implementation: Opt-in only, encrypted storage, deletion on request, clear consent UI.

### 10. Failure Modes and Recovery

**How to detect bad diarization:**
- Speaker count mismatch: Known 2-person call shows 5+ speakers
- Excessive switching: Speakers alternate every 0.5 seconds for extended periods
- One speaker dominates >95%: Likely merged two speakers into one
- Very low average silhouette scores: Low confidence overall

**Fallback strategies:**
1. **Speaker-independent fallback:** If confidence is too low, output transcript without speaker labels rather than wrong labels
2. **Channel-based fallback:** If multichannel is available, use it instead of model-based diarization
3. **Confidence flagging:** Show low-confidence segments in the UI with warning indicators
4. **Human review queue:** Route calls with detected diarization failure to a human review queue

**Production monitoring:**
- Track DER/WDER on a test set from your actual domain (your sales calls, your telephony system)
- Monitor speaker count distribution over time — sudden changes indicate systematic issues
- Sample and manually review a percentage of calls regularly

---

## Recommended Architecture

### Summary Decision

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Model** | pyannote community-1 | Best accuracy, VBx clustering, exclusive mode, MIT-compatible license |
| **Pipeline order** | Diarize first, then transcribe | Clean separation, modular, leverages existing Phase 1 ASR |
| **Overlap handling** | Exclusive mode | One speaker per frame, simplifies ASR integration |
| **Speaker count** | Auto-detect with constraints | `min_speakers=2, max_speakers=10` default, UI option to force count |
| **Multichannel** | Auto-detect and exploit | Near-perfect accuracy when separate channels available |
| **Confidence** | Silhouette scores | Flag low-confidence segments for review |
| **GPU vs CPU** | GPU recommended, CPU fallback | GPU: 1.5-4 min/hr. CPU: 30-60 min/hr |
| **Voice enrollment** | Defer to Phase 5 | Requires CRM integration and consent flows |
| **Phase 3 upgrade** | WhisperX | Word-level speaker labels via forced alignment |

### GPU Memory Budget

| Component | VRAM | Phase |
|-----------|------|-------|
| Pyannote community-1 | ~6-8 GB | 2 |
| Whisper medium (faster-whisper INT8) | ~2-3 GB | 1 |
| Silero VAD | ~0.1 GB | 1 |
| DeepFilterNet3 (conditional) | ~0.5 GB | 1 |
| **Total Phase 1+2** | **~9-12 GB** | |
| Headroom for Phase 3 | ~12-15 GB | Future |
| **24GB GPU budget** | **Sufficient** | |

### New Dependencies (Phase 2)

```
pyannote.audio>=4.0               # Speaker diarization pipeline
# Requires: Python 3.10+
# Requires: HuggingFace account + token + model agreement
# Note: Check torch 2.1.1 compatibility — may need testing
```

### New Configuration (Phase 2)

```python
# Diarization settings
DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
DIARIZATION_DEVICE = "cpu"           # "cpu" or "cuda"
DIARIZATION_MIN_SPEAKERS = 2        # Lower bound for sales calls
DIARIZATION_MAX_SPEAKERS = 10       # Upper bound
DIARIZATION_MIN_DURATION_ON = 0.25  # Keep backchannels (seconds)
DIARIZATION_MIN_DURATION_OFF = 0.5  # Minimum gap for speaker boundary
HF_AUTH_TOKEN = ""                   # HuggingFace token (env var)
DIARIZATION_CONFIDENCE_THRESHOLD = 0.3  # Below this → flag for review
```

---

## Research Sources

### Benchmark Papers
- [SDBench: Comprehensive Benchmark Suite for Speaker Diarization (Interspeech 2025)](https://arxiv.org/abs/2507.16136v2)
- [Benchmarking Diarization Models (arXiv:2509.26177, Sep 2025)](https://arxiv.org/html/2509.26177v1)

### Pyannote
- [pyannote/speaker-diarization-3.1 — HuggingFace](https://huggingface.co/pyannote/speaker-diarization-3.1)
- [pyannote/speaker-diarization-community-1 — HuggingFace](https://huggingface.co/pyannote/speaker-diarization-community-1)
- [pyannote/segmentation-3.0 — HuggingFace](https://huggingface.co/pyannote/segmentation-3.0)
- [PyannoteAI Community-1 Blog](https://www.pyannote.ai/blog/community-1)
- [PyannoteAI Precision-2 Blog](https://www.pyannote.ai/blog/precision-2)
- [pyannote GitHub Repository](https://github.com/pyannote/pyannote-audio)
- [CPU Performance Issue #1621](https://github.com/pyannote/pyannote-audio/issues/1621)
- [GPU Hang Issue #1813](https://github.com/pyannote/pyannote-audio/issues/1813)

### NVIDIA NeMo
- [NVIDIA Streaming Sortformer Blog](https://developer.nvidia.com/blog/identify-speakers-in-meetings-calls-and-voice-apps-in-real-time-with-nvidia-streaming-sortformer/)
- [Sortformer v2.1 — HuggingFace](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1)
- [NeMo Speaker Diarization Documentation](https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/speaker_diarization/models.html)
- [NeMo MSDD Telephonic Model](https://catalog.ngc.nvidia.com/orgs/nvidia/teams/nemo/models/diar_msdd_telephonic)

### Other Models
- [DiariZen GitHub (BUTSpeechFIT)](https://github.com/BUTSpeechFIT/DiariZen)
- [WhisperX GitHub](https://github.com/m-bain/whisperX)
- [SpeakerLM: End-to-End Diarization with MLLMs (AAAI 2026)](https://arxiv.org/abs/2508.06372)

### Competitors
- [Rev: Introducing Reverb Open-Source ASR and Diarization](https://www.rev.com/blog/introducing-reverb-open-source-asr-diarization)
- [Reverb Diarization v2 — HuggingFace](https://huggingface.co/Revai/reverb-diarization-v2)
- [Deepgram: Next-Gen Speaker Diarization](https://deepgram.com/learn/nextgen-speaker-diarization-and-language-detection-models)
- [Deepgram: Multichannel vs Diarization](https://developers.deepgram.com/docs/multichannel-vs-diarization)
- [AssemblyAI: Speaker Diarization Update](https://www.assemblyai.com/blog/speaker-diarization-update)
- [AssemblyAI: Speaker Diarization Improvements](https://www.assemblyai.com/blog/speaker-diarization-improvements)
- [Gong: How Gong Identifies Speakers](https://help.gong.io/docs/how-gong-identifies-speakers)
- [Gong: Enable Voice Identification](https://help.gong.io/docs/enable-voice-identification)
- [Recall.ai: Perfect Diarization](https://docs.recall.ai/docs/perfect-diarization)

### Overlap & Cross-Talk
- [Overlap-Adaptive Hybrid Speaker Diarization (Interspeech 2025)](https://www.isca-archive.org/interspeech_2025/huang25k_interspeech.pdf)
- [Robust Target Speaker Diarization and Separation](https://arxiv.org/abs/2508.06393)
- [USED: Universal Speaker Extraction and Diarization](https://arxiv.org/abs/2309.10674)

### Multilingual / Code-Switching
- [DISPLACE 2024 Challenge Overview](https://arxiv.org/html/2406.09494v1)
- [SAGE-LD: Scalable Language Diarization](https://arxiv.org/html/2510.00582v1)

### Speaker Embeddings & Verification
- [Comparison of Modern Speaker Verification Models (MDPI 2024)](https://www.mdpi.com/2076-3417/14/4/1329)
- [Mitigating Intra-Speaker Variability (Interspeech 2025)](https://arxiv.org/html/2509.14632)
- [Speech Bandwidth Expansion for Speaker Recognition (Odyssey 2020)](https://www.isca-archive.org/odyssey_2020/sivaraman20_odyssey.pdf)

### Confidence Estimation
- [Investigating Confidence Estimation for Speaker Diarization (Interspeech 2024)](https://arxiv.org/html/2406.17124v1)

### LLM Post-Correction
- [DiarizationLM (Google, 2024)](https://arxiv.org/abs/2401.03506)
- [LLM-based Diarization Correction with Mistral 7B](https://arxiv.org/html/2406.04927v3)

### ASR Integration
- [DiCoW: Diarization-Conditioned Whisper](https://arxiv.org/abs/2501.00114)
- [PyannoteAI STT Orchestration Blog](https://www.pyannote.ai/blog/stt-orchestration)

### Privacy & Compliance
- [GDPR Biometric Data Compliance](https://www.gdprregister.eu/gdpr/biometric-data-gdpr/)
- [Voice Biometrics and GDPR (Phonexia)](https://www.phonexia.com/blog/using-voice-biometrics-world-gdpr/)

### Comparison Articles
- [Best Speaker Diarization Models Compared (2026)](https://brasstranscripts.com/blog/speaker-diarization-models-comparison)
- [Top 8 Speaker Diarization Libraries and APIs (AssemblyAI)](https://www.assemblyai.com/blog/top-speaker-diarization-libraries-and-apis)
- [Pyannote vs NeMo Comparison (La Javaness)](https://lajavaness.medium.com/comparing-state-of-the-art-speaker-diarization-frameworks-pyannote-vs-nemo-31a191c6300)
