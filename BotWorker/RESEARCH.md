# Meeting Bot Worker — Complete Research & Beginner's Guide

> This document explains every concept, technology, and design decision needed to build a Meeting Bot Worker from scratch. Each section covers **what it is**, **why we need it**, and **how competitors solve it**.

---

## Table of Contents

1. [What Is a Meeting Bot?](#1-what-is-a-meeting-bot)
2. [The Linux Audio Stack (ALSA → PulseAudio → PipeWire)](#2-the-linux-audio-stack)
3. [PulseAudio — The Audio Server](#3-pulseaudio)
4. [PipeWire — The Modern Alternative](#4-pipewire)
5. [Virtual Audio Devices (Null Sinks)](#5-virtual-audio-devices)
6. [How Chromium Outputs Audio](#6-how-chromium-outputs-audio)
7. [Xvfb — Virtual Display](#7-xvfb---virtual-display)
8. [FFmpeg — Recording & Chunking](#8-ffmpeg---recording--chunking)
9. [Audio Chunking Strategies Compared](#9-audio-chunking-strategies-compared)
10. [Browser Automation: Playwright vs Puppeteer vs Selenium](#10-browser-automation)
11. [Google Meet Joining Flow](#11-google-meet-joining-flow)
12. [Bot Detection & How to Avoid It](#12-bot-detection)
13. [Presigned URL Upload Pattern](#13-presigned-url-upload-pattern)
14. [Deployment: Why Fargate Fails, EC2 Wins](#14-deployment)
15. [Scaling Patterns](#15-scaling-patterns)
16. [Competitor Deep Dive](#16-competitor-deep-dive)
17. [Open-Source Meeting Bots](#17-open-source-meeting-bots)
18. [Build vs Buy (Recall.ai vs DIY)](#18-build-vs-buy)
19. [The Three Approaches to Meeting Bots](#19-the-three-approaches)
20. [Our Chosen Architecture & Why](#20-our-chosen-architecture)

---

## 1. What Is a Meeting Bot?

A meeting bot is a software program that **joins an online meeting as a participant**, just like a human would. Once inside, it can:

- **Record audio** (and optionally video) of the meeting
- **Scrape captions/transcripts** from the meeting platform's built-in captioning
- **Report events** (who joined, who left, when the meeting started/ended)
- **Upload recordings** to cloud storage for later processing (transcription, summarization)

### Why Do We Need One?

If you're building a meeting notes product, you need to get the meeting audio somehow. There are three ways:

| Approach | How it works | Pros | Cons |
|----------|-------------|------|------|
| **User records manually** | User clicks "record" in Zoom/Meet, downloads file, uploads to your app | Simple | Terrible UX, users forget, low adoption |
| **Desktop app captures** | Your app runs on user's computer, captures system audio | No bot visible in meeting | Must install app, OS-specific, privacy concerns |
| **Meeting bot joins** | Your bot joins as a participant, records automatically | Fully automated, no install needed | Bot is visible, needs platform-specific code |

The meeting bot approach is what **Otter.ai, Fireflies.ai, Fathom, Gong**, and most meeting-notes products use. It's the industry standard because it requires zero effort from the user — just schedule a meeting and the bot handles everything.

### The Big Picture: How It All Fits Together

```
User schedules meeting
        │
        ▼
Your Backend detects meeting (via calendar integration)
        │
        ▼
Backend tells Bot Worker: "Join this meeting URL"
        │
        ▼
Bot Worker (Docker container):
  1. Opens a headless browser (Chromium via Playwright)
  2. Navigates to the meeting URL
  3. Turns off mic/camera
  4. Clicks "Join"
  5. Records system audio (what everyone in the meeting says)
  6. Splits audio into 30-second chunks
  7. Uploads each chunk to S3 via presigned URLs
  8. Sends heartbeat events to your backend
        │
        ▼
Meeting ends (or bot is stopped)
        │
        ▼
Backend triggers transcription pipeline (Whisper, etc.)
        │
        ▼
User gets meeting notes, summary, action items
```

---

## 2. The Linux Audio Stack

Before we can record audio from a browser inside a Docker container, we need to understand how audio works on Linux. There are **three layers**:

### Layer 1: ALSA (Kernel Level)

**What:** ALSA (Advanced Linux Sound Architecture) is built into the Linux kernel. It provides device drivers that talk to physical sound hardware (sound cards, USB audio devices, HDMI audio).

**Think of it as:** The low-level plumbing that connects software to speakers/microphones.

**The problem:** ALSA gives **exclusive access** — only one program can use the sound card at a time. If Firefox is playing music through ALSA, Spotify can't play simultaneously. This was acceptable in the 1990s but not today.

### Layer 2: PulseAudio or PipeWire (User Space Sound Server)

**What:** A daemon (background process) that sits between applications and ALSA. It opens the ALSA device once and then accepts audio from multiple programs, mixes them together, and sends the combined output to the hardware.

**Think of it as:** A switchboard operator for audio — it routes audio from many sources to many destinations.

**Why we need it:** In Docker, we have no physical sound card. But we can create a **virtual sound card** (null sink) using PulseAudio, and our browser will happily output audio to it as if it were a real speaker.

### Layer 3: Applications

Programs like Chromium, FFmpeg, and media players connect to the sound server (PulseAudio/PipeWire) to play or record audio.

### The Complete Picture

```
┌─────────────────────────────────────────────────────┐
│                   Applications                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ Chromium  │  │  FFmpeg   │  │  Other app       │  │
│  │(plays     │  │(records   │  │                  │  │
│  │ meeting   │  │ audio)    │  │                  │  │
│  │ audio)    │  │           │  │                  │  │
│  └─────┬─────┘  └─────┬─────┘  └────────┬────────┘  │
│        │              │                  │           │
│        ▼              ▼                  ▼           │
│  ┌───────────────────────────────────────────────┐  │
│  │          PulseAudio (Sound Server)             │  │
│  │  - Mixes multiple audio streams               │  │
│  │  - Routes audio to correct devices            │  │
│  │  - Provides virtual devices (null sinks)      │  │
│  └──────────────────────┬────────────────────────┘  │
│                         │                            │
│                         ▼                            │
│  ┌───────────────────────────────────────────────┐  │
│  │              ALSA (Kernel)                     │  │
│  │  - Talks to hardware sound cards              │  │
│  │  - (In Docker: no real hardware needed)       │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

---

## 3. PulseAudio

### What It Is

PulseAudio is a **user-space sound server** — a background daemon that manages all audio on a Linux system. Created by Lennart Poettering (who also created systemd), it's been the default Linux audio server since ~2008.

### Why It Exists

| Without PulseAudio | With PulseAudio |
|---|---|
| Only one app can play audio at a time | Multiple apps play audio simultaneously |
| No per-app volume control | Each app gets its own volume slider |
| No dynamic device switching | Plug in headphones → audio switches automatically |
| No virtual audio devices | Can create software-only "fake" sound cards |
| No network audio | Can stream audio between machines |

### How It Works

Applications connect to PulseAudio via a **UNIX socket** (a file-like communication channel). Chromium, for example, links against `libpulse` (PulseAudio's client library) and sends its audio data through this socket to the PulseAudio daemon, which mixes it and routes it to the output device.

### User Mode vs System Mode

| Mode | How it works | When to use |
|------|-------------|------------|
| **User mode** (default) | One PulseAudio daemon per logged-in user. Starts automatically when you log in. | Desktop Linux |
| **System mode** (`--system`) | One PulseAudio daemon for the entire machine. Runs as the `pulse` system user. | **Docker containers**, embedded systems, headless servers |

**For our meeting bot, we use system mode** because Docker containers don't have "user sessions" — there's no login, no desktop, no session bus. System mode just works.

```bash
# Start PulseAudio in system mode inside Docker
pulseaudio --system --daemonize --no-cpu-limit \
  --disallow-exit --exit-idle-time=-1
```

Flags explained:
- `--system`: Run as system-wide daemon
- `--daemonize`: Run in background
- `--no-cpu-limit`: Don't limit CPU (we need it for real-time audio)
- `--disallow-exit`: Don't exit when no clients are connected
- `--exit-idle-time=-1`: Never exit due to idle timeout

### How Competitors Use PulseAudio

Every meeting bot company that does browser-based audio recording uses PulseAudio in Docker:
- **Recall.ai**: PulseAudio + null sink in their bot containers
- **Gladia's open-source bot**: PulseAudio + dummy output sink + virtual mic source
- **screenappai/meeting-bot**: PulseAudio inside Docker containers
- **Most CI/CD audio testing setups**: PulseAudio in system mode

It's the industry standard because it's mature, well-documented, and Chromium supports it natively.

---

## 4. PipeWire — The Modern Alternative

### What It Is

PipeWire is the **next-generation replacement** for PulseAudio. Created by Wim Taymans (GStreamer co-creator), it handles both audio AND video streams. It's now the default on Fedora, Ubuntu 22.10+, Debian 12+, and Arch Linux.

### PulseAudio vs PipeWire

| Aspect | PulseAudio | PipeWire |
|--------|-----------|----------|
| Audio mixing | Yes | Yes |
| Pro audio (low latency) | No | Yes (replaces JACK too) |
| Video handling | No | Yes (screen sharing, cameras) |
| Container/Docker support | Excellent (10+ years of use) | Immature (requires D-Bus + WirePlumber) |
| CPU overhead | Higher | Lower |
| Backwards compatibility | N/A | Provides `pipewire-pulse` (drop-in PulseAudio replacement) |

### Why We Choose PulseAudio Over PipeWire

**PipeWire is better on desktop Linux**, but for Docker containers:

1. **PipeWire needs D-Bus session bus + WirePlumber** (a session manager). Setting these up correctly in Docker adds complexity and failure modes.
2. **PulseAudio in system mode "just works"** in Docker with a single command. No session bus, no session manager.
3. **Every existing meeting bot project uses PulseAudio**. The patterns are proven.
4. **PipeWire's `pipewire-pulse` layer** means if we ever switch, our FFmpeg recording commands don't change — they still use the PulseAudio protocol.

**Bottom line:** PulseAudio for now. PipeWire when its Docker story matures.

---

## 5. Virtual Audio Devices (Null Sinks)

### The Problem

In Docker, there are no speakers or microphones. No physical audio hardware at all. But Chromium needs to "play" the meeting audio somewhere. If there's no audio output device, Chromium's audio subsystem won't even initialize.

### The Solution: Null Sink

A **null sink** is a virtual audio output device created by PulseAudio. It:
- Accepts audio data from applications (like a real speaker would)
- **Discards the audio** (sends it nowhere — like `/dev/null` for audio)
- Applications can't tell the difference — they think they're playing through a real speaker

```bash
# Create a null sink
pactl load-module module-null-sink \
  sink_name=bot_sink \
  sink_properties=device.description="BotRecordingSink" \
  rate=48000 channels=1 format=s16le

# Make it the default output (so Chromium uses it)
pactl set-default-sink bot_sink
```

### The Magic: Monitor Sources

Here's the key insight that makes everything work. Every PulseAudio sink automatically creates a **monitor source** — a readable audio input that provides a copy of everything sent to the sink.

If our sink is called `bot_sink`, its monitor is `bot_sink.monitor`.

```
Chromium plays meeting audio
        │
        ▼
    bot_sink (null sink — audio "goes to speakers")
        │
        ├──► Audio is discarded (no real speakers)
        │
        └──► bot_sink.monitor (COPY of the audio is readable here!)
                    │
                    ▼
            FFmpeg reads from bot_sink.monitor
                    │
                    ▼
            Audio chunks saved to disk
```

**This is the entire trick.** The browser thinks it's playing audio through speakers. The null sink silently captures a copy. FFmpeg reads that copy and saves it to files.

### Multiple Bots = Multiple Sinks

For running 2+ bots simultaneously, each bot gets its own null sink:

```bash
# Bot 1 (recording Meeting A)
pactl load-module module-null-sink sink_name=bot_sink_meetingA ...

# Bot 2 (recording Meeting B)
pactl load-module module-null-sink sink_name=bot_sink_meetingB ...
```

Then we route each Chromium instance's audio to its specific sink using `pactl move-sink-input`. This prevents audio from Meeting A leaking into Meeting B's recording.

---

## 6. How Chromium Outputs Audio

### Multi-Process Architecture

Chromium splits work across multiple processes for security:

- **Renderer processes** (one per tab) are heavily sandboxed — they can't access files, devices, or network
- **Browser process** is privileged — it handles all system interactions including audio

Since renderer processes can't open audio devices directly, Chromium uses **inter-process communication (IPC)**:

1. Web content (JavaScript `AudioContext`, `<audio>` tag, WebRTC) produces audio samples in the renderer
2. Renderer sends a request to the browser process: "please open an audio output for me"
3. Browser process creates a `PulseAudioOutputStream` — a connection to PulseAudio
4. Audio data flows: renderer → shared memory → browser process → PulseAudio → null sink

### The Critical Fact: Headless Mode Has No Audio

**Chromium's `--headless` mode completely disables audio output.** This is by design — headless mode is optimized for rendering HTML and taking screenshots, not multimedia.

This means you **cannot** do:
```python
# THIS WON'T WORK — no audio will be captured
browser = playwright.chromium.launch(headless=True)
```

Instead, you must run Chromium in **headed mode** (`headless=False`) pointed at a virtual display (Xvfb). This keeps the full audio pipeline active.

### How Competitors Handle This

Every meeting bot that records audio uses the same approach:
- **Recall.ai**: headed Chromium + virtual display
- **Gladia**: headed Chrome + Xvfb + PulseAudio
- **screenappai/meeting-bot**: headed Playwright + Xvfb
- No one uses `--headless` for audio recording

---

## 7. Xvfb — Virtual Display

### What It Is

Xvfb (X Virtual Framebuffer) is a **display server that runs entirely in memory**. It implements the full X11 graphics protocol but never actually shows anything on a screen. Applications think they're rendering to a real monitor, but the pixels just go to memory.

### Why We Need It

As explained above, Chromium in `--headless` mode has no audio. We need Chromium to run in headed mode, but we're in Docker — there's no physical monitor. Xvfb solves this:

```
Without Xvfb:
  Chromium (headless=True) → NO audio output → Can't record anything

With Xvfb:
  Xvfb creates virtual display :99 (1920x1080)
  Chromium (headless=False, DISPLAY=:99) → Full audio pipeline active
  Chromium renders to Xvfb (pixels go to memory, discarded)
  Chromium outputs audio to PulseAudio → We can record it!
```

### Setup

```bash
# Start virtual display
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &

# Tell applications to use it
export DISPLAY=:99

# Now Chromium will use this virtual display
# and its audio subsystem will work
```

Parameters:
- `:99` — Display number (arbitrary, just pick one that's not in use)
- `-screen 0 1920x1080x24` — Screen 0, 1920x1080 resolution, 24-bit color
- `-nolisten tcp` — Security: don't accept network connections

### Resource Usage

Xvfb uses essentially **zero CPU** — it just allocates memory for the framebuffer (~8MB for 1920x1080x24) and handles X protocol messages. It's extremely lightweight.

---

## 8. FFmpeg — Recording & Chunking

### What FFmpeg Is

FFmpeg is the Swiss Army knife of audio/video processing. It can:
- **Record** from devices (microphones, PulseAudio sources, screens)
- **Convert** between any audio/video format
- **Split** files into chunks
- **Stream** media over networks
- Much more

For our bot, FFmpeg does one critical job: **read audio from the PulseAudio monitor source and save it to files.**

### Basic Recording Command

```bash
ffmpeg -f pulse -i "bot_sink.monitor" output.ogg
```

Breakdown:
- `-f pulse` — Use the PulseAudio input driver
- `-i "bot_sink.monitor"` — Read from our null sink's monitor source
- `output.ogg` — Save as OGG file (FFmpeg auto-selects Vorbis codec)

### Recording with Specific Settings

```bash
ffmpeg \
  -f pulse \
  -i "bot_sink.monitor" \
  -c:a libopus \       # Use Opus codec
  -b:a 48k \           # 48 kilobits per second
  -ar 48000 \          # 48kHz sample rate
  -ac 1 \              # Mono (1 channel)
  output.ogg
```

### Codec Choices for Speech Recording

| Codec | Bitrate (mono speech) | File size per minute | Quality | When to use |
|-------|----------------------|---------------------|---------|-------------|
| **WAV (PCM)** | ~768 kbps | ~5.6 MB | Lossless (perfect) | When you need raw audio for processing |
| **Opus** | 24-48 kbps | ~180-360 KB | Excellent for speech | Best choice for meeting recordings |
| **AAC** | ~64 kbps | ~480 KB | Good | When you need Apple/web compatibility |
| **MP3** | ~64 kbps | ~480 KB | Decent | Legacy compatibility only |

**Our choice: Opus at 48kbps.** Opus was literally designed for speech. At 48kbps it's nearly indistinguishable from lossless for spoken words, and files are tiny (~180KB per 30-second chunk). This matters when you're uploading chunks every 30 seconds.

### The Segment Muxer (Chunking)

FFmpeg's **segment muxer** splits a continuous recording into multiple files:

```bash
ffmpeg -f pulse -i "bot_sink.monitor" \
  -c:a libopus -b:a 48k -ar 48000 -ac 1 \
  -f segment \              # Use segment muxer
  -segment_time 30 \        # Split every 30 seconds
  -segment_format ogg \     # Each chunk is an OGG file
  -reset_timestamps 1 \     # Each chunk starts at timestamp 0
  "chunks/chunk_%04d.ogg"   # chunk_0000.ogg, chunk_0001.ogg, ...
```

This is a single continuous FFmpeg process that:
1. Reads audio from PulseAudio non-stop
2. Writes to `chunk_0000.ogg` for 30 seconds
3. Seamlessly switches to `chunk_0001.ogg` for the next 30 seconds
4. Continues until stopped

**Key benefit: zero gap between chunks.** Since it's one continuous process, there's no audio lost at chunk boundaries.

---

## 9. Audio Chunking Strategies Compared

There are three ways to split a continuous recording into chunks. Here's a detailed comparison:

### Strategy 1: FFmpeg Segment Muxer (Our Choice)

```
Single FFmpeg process running continuously
  → chunk_0000.ogg (0:00-0:30)
  → chunk_0001.ogg (0:30-1:00)
  → chunk_0002.ogg (1:00-1:30)
  → ...
```

| Pros | Cons |
|------|------|
| Zero gap between chunks | Chunk only available after it finishes (30s delay) |
| Single process (simple) | Need a file watcher to detect completed chunks |
| Built-in to FFmpeg | Cannot change encoding settings mid-recording |
| Rock solid reliability | |

**How competitors use it:** Most production meeting bots use this approach. The Gladia tutorial, screenappai/meeting-bot, and Recall.ai's internal systems all use segment muxer or equivalent.

### Strategy 2: Rolling Restarts (Kill & Restart FFmpeg)

```
Start FFmpeg → record for 30s → kill FFmpeg → upload chunk → start new FFmpeg → ...
```

| Pros | Cons |
|------|------|
| Each chunk immediately available for upload | **10-50ms audio gap** between kill and restart |
| Simple to understand | Must handle FFmpeg process lifecycle carefully |
| Can change settings per chunk | SIGKILL can corrupt the file (must use SIGINT) |

**The gap problem:** When you kill FFmpeg and start a new one, there's a brief period where nobody is recording. At 10-50ms, this gap is usually inaudible but it's technically imperfect.

### Strategy 3: Record Everything, Split Later

```
FFmpeg records entire meeting → one big file → split after meeting ends
```

| Pros | Cons |
|------|------|
| Zero gaps, simplest code | **No chunks until meeting ends** |
| Can re-split with different sizes later | Can't stream transcription in real-time |
| Most reliable single recording | If container crashes, lose everything |

**Not suitable for us** because we want to upload chunks in real-time so transcription can start while the meeting is still ongoing.

### Our Decision: Segment Muxer

We use the segment muxer because:
1. Zero gaps (critical for transcription quality)
2. Single process (less to go wrong)
3. File watcher detects completed chunks and triggers upload
4. Industry-proven approach

---

## 10. Browser Automation: Playwright vs Puppeteer vs Selenium

We need a tool to control a web browser programmatically — navigate to Google Meet, click buttons, type text, detect page states. Three main options:

### Comparison

| Feature | Playwright | Puppeteer | Selenium |
|---------|-----------|-----------|----------|
| **Creator** | Microsoft | Google | Open-source community |
| **Languages** | Python, JS, Java, C# | JavaScript only | Almost all languages |
| **Speed** | Fast (WebSocket) | Fast (WebSocket) | Slower (HTTP round-trips) |
| **Auto-wait** | Built-in (waits for elements to be clickable) | Manual (`waitForSelector`) | Manual (`WebDriverWait`) |
| **Browser support** | Chromium, Firefox, WebKit | Chromium only | All browsers |
| **Parallel contexts** | Native browser contexts (lightweight) | Incognito contexts | Separate browser processes (heavy) |
| **Anti-detection** | `playwright-extra` + stealth | `puppeteer-extra-plugin-stealth` (mature) | `undetected-chromedriver` |
| **Auth state save/restore** | `storageState` (JSON) | Cookie export/import | Cookie export/import |
| **Trace/debug** | Built-in trace viewer | CDP tracing | Screenshots only |

### Why Playwright Wins for Meeting Bots

1. **Auto-waiting is critical.** Google Meet's UI loads asynchronously — buttons appear, disappear, and reappear as React components mount. Playwright automatically waits for elements to be visible, enabled, and stable before clicking. With Puppeteer/Selenium, you write manual wait logic that breaks when Meet's loading behavior changes.

2. **Python support.** Our bot worker is in Python. Playwright has first-class Python bindings (`playwright` pip package). Puppeteer is JavaScript-only.

3. **Browser contexts for isolation.** Each bot runs in its own `BrowserContext` within a single Chromium process. This is cheaper than spawning separate browser instances. For 2 concurrent bots, we share one Chromium process with 2 isolated contexts.

4. **`storageState` for authentication.** Playwright can save and restore cookies + localStorage as a JSON file. Authenticate once, save the state, and every bot loads it to skip login. Avoids triggering Google's 2FA/CAPTCHA.

5. **Trace viewer for debugging.** When a bot fails to join in production, Playwright's trace file gives you a full timeline: screenshots, DOM snapshots, network requests, console logs. Invaluable for debugging flaky join flows.

### How Competitors Choose

| Company/Project | Tool | Why |
|-----------------|------|-----|
| **Recall.ai** | Playwright | Multi-language, auto-wait, trace viewer |
| **screenappai/meeting-bot** | Playwright | TypeScript + stealth plugins |
| **Gladia tutorial** | Selenium + undetected-chromedriver | Older tutorial, stealth focus |
| **attendee-labs** | (Django-based, tool not specified) | Backend-focused |
| **balena meetbot** (archived) | Puppeteer | Was JS-only project; **abandoned due to maintenance burden** |

---

## 11. Google Meet Joining Flow

Here's the exact step-by-step process a bot follows to join a Google Meet. Understanding this is critical because each step can fail in different ways.

### Step 1: Navigate to Meeting URL

```python
await page.goto("https://meet.google.com/abc-defg-hij")
```

**What can go wrong:**
- Google redirects to `accounts.google.com` if not authenticated
- Network timeout
- Meet shows "Meeting not found" for invalid URLs

### Step 2: Dismiss Popups & Cookie Banners

Google Meet often shows dialogs before you can interact with the join screen:
- "Before you join" information popup
- Cookie consent banner
- "Got it" education popup about Meet features

```python
# Try to dismiss any "Got it" buttons
got_it = page.get_by_text("Got it")
if await got_it.is_visible(timeout=3000):
    await got_it.click()
```

### Step 3: Turn Off Microphone and Camera

**This is essential.** If the bot joins with mic on, it creates echo. If camera is on, it wastes bandwidth and looks suspicious.

```python
# Turn off microphone
mic_button = page.locator('[aria-label*="Turn off microphone"]')
if await mic_button.is_visible(timeout=5000):
    await mic_button.click()

# Turn off camera
cam_button = page.locator('[aria-label*="Turn off camera"]')
if await cam_button.is_visible(timeout=5000):
    await cam_button.click()
```

**Gotcha:** The aria-label text changes based on current state. If mic is already off, label becomes "Turn on microphone". Must handle both states.

### Step 4: Enter Display Name (if not logged in)

If the bot is not logged into a Google account, Meet shows a name input:

```python
name_input = page.locator('input[aria-label="Your name"]')
if await name_input.is_visible(timeout=3000):
    await name_input.fill("Meeting Notetaker")
```

### Step 5: Click "Join now" or "Ask to join"

The button text varies:
- **Same organization as host** → "Join now" (auto-admitted)
- **External/guest** → "Ask to join" (goes to waiting room, host must approve)

```python
join_button = page.locator('button:has-text("Join now"), button:has-text("Ask to join")')
await join_button.click(timeout=10000)
```

### Step 6: Wait for Admission

After clicking join, three things can happen:

| Outcome | How to detect | What to do |
|---------|--------------|------------|
| **Admitted** | "Leave call" button appears | Start recording! |
| **Waiting room** | Text "Asking to be let in" visible | Wait (up to timeout) |
| **Denied** | Text "You can't join this video call" | Take screenshot, send ERROR event |
| **Meeting ended** | Text "You left the meeting" | Finalize |
| **Kicked** | Text "You've been removed" | Finalize |

```python
# Race multiple conditions
result = await Promise.race([
    page.wait_for_selector('[aria-label*="Leave call"]').then("admitted"),
    page.wait_for_selector('text="You can\'t join"').then("denied"),
    asyncio.sleep(120).then("timeout"),
])
```

### Step 7: Stay in Meeting & Record

Once in the meeting, the bot:
- Starts FFmpeg recording
- Periodically checks the DOM for meeting-ended signals
- Dismisses any popups that appear (Google Meet shows various notifications)
- Sends heartbeat events every 30 seconds

### Common Pitfalls

| Pitfall | Cause | Solution |
|---------|-------|---------|
| Join button never appears | Mic/camera permission dialog not dismissed | Always handle media dialogs first |
| Stuck in waiting room | Host has "Quick access" off for external users | Implement timeout (2 min default) |
| Selectors break | Google updates Meet UI | Use aria-label selectors (more stable than CSS classes), keep selectors in config |
| 2FA prompt | Google detects new device/IP | Pre-authenticate, save `storageState` as JSON |
| CAPTCHA | Rate limiting | Rotate bot accounts, stagger join times |

---

## 12. Bot Detection & How to Avoid It

Google uses multiple layers to detect automated browsers. Understanding each layer helps us avoid detection.

### Layer 1: `navigator.webdriver` Property

When Chrome is automated, JavaScript's `navigator.webdriver` returns `true`. Google Meet checks this.

**Fix:** Launch with `--disable-blink-features=AutomationControlled`

### Layer 2: Headless Browser Fingerprinting

Even with the new headless Chrome, detectable signals include:
- Missing `chrome.runtime` properties
- `navigator.plugins.length === 0` (real Chrome has PDF Viewer plugin)
- WebGL renderer showing "SwiftShader" (software) instead of a real GPU
- Chrome DevTools Protocol being active

**Fix:** Use stealth plugins (`playwright-extra` with stealth). These patch dozens of fingerprint signals.

### Layer 3: Behavioral Analysis

Google tracks:
- Click speed (bots click instantly; humans take 100-500ms)
- Mouse movement patterns (bots teleport; humans curve)
- Keyboard timing

**Fix:** Add randomized delays between actions. Let Playwright's auto-waiting handle most timing naturally.

### Layer 4: IP Reputation

AWS/GCP IP ranges are flagged. Repeated joins from cloud IPs look suspicious.

**Fix:** Use residential proxies for production. Or accept that some Google orgs may block your bot and handle it gracefully.

### The Most Important Trick

**Run with `headless=False` on Xvfb, not `headless=True`.**

This single decision eliminates most fingerprinting because:
- The browser fingerprint is identical to a real desktop Chrome
- All Chrome APIs work normally (audio, WebGL, plugins)
- No headless-specific signals to detect

This is what **every production meeting bot does**.

---

## 13. Presigned URL Upload Pattern

### The Problem

The bot records audio chunks and needs to upload them to S3. But we don't want to give the bot container AWS credentials — if the container is compromised, an attacker could access our entire S3 bucket.

### What Presigned URLs Are

A presigned URL is a specially crafted S3 URL that contains:
- The target bucket and object key
- A cryptographic signature
- An expiration timestamp
- The permitted operation (GET or PUT)

Anyone with this URL can perform that one operation on that one object, without needing AWS credentials. The URL expires after a configurable time (e.g., 5 minutes).

### How It Works

```
Bot Container              Your Backend              AWS S3
     │                         │                       │
     │── POST /presign ──────►│                       │
     │   {chunk_index: 0}     │                       │
     │                         │── generatePresignedUrl()
     │                         │   (signs with IAM creds)
     │                         │                       │
     │◄── {presigned_url} ────│                       │
     │                         │                       │
     │── HTTP PUT url ────────┼─────────────────────►│
     │   (raw audio bytes)     │                       │
     │                         │                       │
     │◄── 200 OK ─────────────┼──────────────────────│
     │                         │                       │
     │── POST /events ────────►│                       │
     │   CHUNK_UPLOADED         │                       │
```

### Why This Is Better Than Direct Upload

| Direct Upload | Presigned URL |
|---|---|
| Bot needs AWS credentials | Bot needs zero AWS credentials |
| Credentials in env vars could leak | Only the backend has credentials |
| Bot could access any S3 object | Bot can only upload the specific file the URL was created for |
| If container is compromised, attacker gets full S3 access | If container is compromised, attacker gets nothing useful |

### Security Details

- **Short expiration:** Set to 5-15 minutes. URL only needs to live long enough for the upload.
- **Cannot be revoked early:** Once generated, it's valid until expiration. Keep expiration short.
- **Tamper-proof:** If anyone modifies the URL parameters (bucket, key, expiration), S3 rejects it with `SignatureDoesNotMatch`.
- **Content-type enforcement:** Include `ContentType` in the signed request to prevent different file types being uploaded.

---

## 14. Deployment: Why Fargate Fails, EC2 Wins

### What Is AWS Fargate?

Fargate is AWS's serverless container service. You define a Docker container, and AWS runs it on shared infrastructure you don't manage. No EC2 instances to provision, no servers to patch. You pay only for CPU/memory your task uses.

Sounds perfect, right? Not for meeting bots.

### Why Fargate Cannot Run Meeting Bots

Fargate has three showstopper limitations:

| Limitation | Why it matters for meeting bots |
|---|---|
| **No privileged mode** | PulseAudio in system mode benefits from `CAP_SYS_NICE` capability. Fargate strips all elevated capabilities. |
| **No `/dev/shm` configuration** | Chromium uses shared memory (`/dev/shm`) for IPC between browser and renderer processes. Fargate's default `/dev/shm` is tiny (64MB) and cannot be increased. The workaround (`--disable-dev-shm-usage`) causes instability in long-running meetings. |
| **No device access** | No `/dev/snd`, no PulseAudio socket from host. The virtual audio device chain (null sink → monitor → FFmpeg) needs process capabilities Fargate doesn't allow. |

### EC2: Full Control

On EC2, you control the instance. You can:
- Install Docker with any flags (`--privileged`, `--shm-size=2g`, device mounts)
- Install PulseAudio at the host level or inside containers
- Use any Linux capability
- Configure `/dev/shm` to any size
- Mount devices if needed

### Recommended: ECS on EC2

Best of both worlds: ECS orchestration (task definitions, auto-scaling, placement) running on EC2 instances you control.

```
ECS Cluster
  └── Auto Scaling Group (EC2 t3.xlarge instances)
       ├── Instance 1: Bot Container A, Bot Container B
       ├── Instance 2: Bot Container C, Bot Container D
       └── Instance 3: (auto-scaled when needed)
```

### How Competitors Deploy

| Company | Deployment | Why |
|---------|-----------|-----|
| **Recall.ai** | EC2 (8M+ instances/month!) | Need full audio control |
| **Gladia tutorial** | EC2 / Docker | PulseAudio requires it |
| **screenappai/meeting-bot** | Docker on EC2 | Need privileged mode |
| **Most DIY bots** | EC2 or bare-metal | Fargate simply doesn't work |

**Nobody uses Fargate for meeting bots.** It's physically impossible with current Fargate limitations.

---

## 15. Scaling Patterns

### Resource Requirements Per Bot

Each meeting bot runs a full Chromium browser. Approximate usage:

| Resource | Per bot |
|----------|---------|
| RAM | 500MB - 2GB (depends on meeting participants, screen sharing) |
| CPU | 0.5 - 1.5 vCPU |
| Bandwidth | ~50 KB/s (audio recording + events) |
| Disk | ~6 MB/hour (Opus at 48kbps) |

### Scaling Tiers

#### Tier 1: 1-10 Concurrent Bots (Single Host)

```
One EC2 t3.xlarge (4 vCPU, 16GB RAM)
  └── Docker Compose
       ├── Bot Worker (FastAPI + Playwright)
       │   └── Up to 2-4 concurrent bots per container
       ├── Redis (optional job queue)
       └── PostgreSQL (optional metadata)
```

Cost: ~$0.17/hr ($125/month) or ~$0.05/hr spot ($37/month)

#### Tier 2: 10-100 Concurrent Bots (ECS on EC2)

```
ECS Cluster
  └── Auto Scaling Group
       ├── EC2 Instance 1 (2 bots)
       ├── EC2 Instance 2 (2 bots)
       ├── ... auto-scaled based on queue depth
       └── EC2 Instance N (2 bots)
```

Cost: ~$0.05-0.10 per bot-hour on spot instances

#### Tier 3: 100+ Concurrent Bots (EKS / Kubernetes)

At this scale you need Kubernetes for orchestration, Karpenter for fast node provisioning, pod disruption budgets to prevent eviction mid-meeting, and distributed state management.

### The Golden Rule: One Meeting Per Container

Never run multiple meetings in a single browser context. Audio capture, DOM observation, and error isolation all break when shared. **The container is the unit of isolation.**

---

## 16. Competitor Deep Dive

### Recall.ai — Meeting Bot as a Service

**What they are:** Y Combinator-backed API company ($38M Series B, $250M valuation). They provide "meeting bot as a service" — you call their API, they join the meeting, and you get the data back.

**Scale:** 2,000+ companies use it. Infrastructure processes **3+ TB of raw video per second**, spins up **8+ million EC2 instances per month**.

**How it works:**
1. Developer calls `POST /api/v1/bot` with a `meeting_url`
2. Recall.ai spins up a Docker container with Playwright + Chromium
3. Bot joins the meeting as a participant
4. Real-time data (transcripts, media frames) delivered via webhooks/WebSockets
5. Post-meeting: recordings, transcripts, metadata available via API

**Platform support:** Zoom, Google Meet, Microsoft Teams, Webex, GoTo Meeting, Slack Huddles

**Technical approach per platform:**
- **Zoom:** Browser automation + caption scraping. Also supports Zoom RTMS (botless WebSocket streaming).
- **Google Meet:** Browser automation with MutationObserver on `[aria-live]` region for captions.
- **Teams:** Browser automation or Teams Meeting SDK.

**Pricing:** ~$0.50/hour recording + $0.15/hour transcription + ~$1,000/month platform fee

**Additional products:**
- **Desktop Recording SDK** — records locally on Mac/Windows without a bot joining ($0.50/hour)
- **Meeting Direct Connect** — botless approach using Zoom RTMS and Google Meet Media API
- **Output Media API** — streams audio/video INTO meetings (AI agents that talk)

### Fireflies.ai — End-User Product

**What they are:** Consumer/prosumer meeting notes product. Their bot "Fred" (`fred@fireflies.ai`) joins meetings.

**How it joins:**
1. Auto-join via calendar integration (reads Google Calendar/Outlook)
2. Manual: invite `fred@fireflies.ai` as a guest
3. Manual: paste meeting link into dashboard

**Transcription:** Proprietary speech model, ~95% accuracy, 100+ languages, speaker diarization.

**Key difference from Recall.ai:** Fireflies is an end-user product, not a developer API. You can't customize the bot behavior or get raw media.

**Pricing:** Free tier (limited), Pro $10/seat/month, Business $19/seat/month, Enterprise $39/seat/month

### Otter.ai — Proprietary ASR Engine

**What they are:** Meeting notes product with a **proprietary neural network** trained on millions of hours of audio. Their ASR engine is built from scratch (not using Deepgram, AssemblyAI, or Google STT).

**OtterPilot** (their bot):
- Connects to calendars, auto-joins meetings
- Real-time transcription with voice fingerprint-based speaker diarization
- Detects screen shares and captures slide screenshots

**Pricing:** Free (300 min/month), Pro $8.33/month, Business $20/seat/month

### Fathom — Dual Approach

**What they are:** Meeting notes product with a unique hybrid strategy.

**For Zoom:** Native Zoom Marketplace app that runs as a sidebar in the Zoom desktop client. Uses Zoom's Live Streaming (RTMP/S) for audio/video — **no visible bot participant**. This is a significant UX advantage.

**For Google Meet & Teams:** Falls back to a **visible cloud bot** ("Fathom Notetaker") because these platforms don't offer equivalent native integration.

**Speed:** Delivers meeting summaries within **30 seconds** of call ending (competitors take 5+ minutes).

**Pricing:** Free forever (unlimited recordings, 5 AI summaries/month), Premium $19/month

### Gong — Enterprise Revenue Intelligence

**What they are:** Enterprise sales intelligence platform. Their bot joins meetings to record sales calls for analysis.

**Approach:** Bot joins as "Gong" participant via calendar integration. Also supports Zoom native recording mode (no separate bot participant).

**Key difference:** Gong is focused on sales intelligence — deal scoring, coaching, competitive tracking. Not a general-purpose meeting notes tool.

**Pricing:** Custom (enterprise only, typically $100+/seat/month)

---

## 17. Open-Source Meeting Bots

### Recall.ai's Google Meet Bot (Reference Implementation)

**GitHub:** [recallai/google-meet-meeting-bot](https://github.com/recallai/google-meet-meeting-bot)

**Stack:** TypeScript, Node.js, Playwright, Express, Prisma + PostgreSQL, Docker

**Architecture:**
- POST /submit-link → spawns Docker container
- Playwright joins Meet with saved auth (`auth.json`)
- Enables captions, injects MutationObserver on `[aria-live]`
- Scrapes speaker name from `.NWpY1d` / `.xoMHSc` selectors
- Buffers in memory, flushes to PostgreSQL every 30s
- Exit phrase: "Notetaker, please leave"
- Post-meeting: OpenAI summarization

**Key insight: Caption scraping only, no audio recording.** This is simpler but less reliable (~90% accuracy) and depends on the platform's captioning quality.

### screenappai/meeting-bot

**GitHub:** [screenappai/meeting-bot](https://github.com/screenappai/meeting-bot)

**Stack:** TypeScript, Node.js 20+, Playwright, Redis, Docker, Prometheus

**Architecture:**
- REST API: `POST /google/join`, `POST /microsoft/join`, `POST /zoom/join`
- Single-job-at-a-time model (one bot per container)
- Redis queue for async job processing
- Full audio/video recording via Playwright
- Stealth mode with anti-detection

### Gladia's Google Meet Bot Tutorial

**Stack:** Python, Selenium + Undetected Chrome Driver, PulseAudio, XVFB, FFmpeg, Docker

This is the **full audio/video recording approach** (closest to what we're building):
- XVFB creates virtual display
- PulseAudio creates virtual sound card
- FFmpeg captures X11 screen + PulseAudio audio → MP4
- Undetected Chrome Driver evades bot detection
- Post-recording: submitted to Gladia API for transcription

### Meeting BaaS (Open Source)

**GitHub:** [Meeting-Baas/meet-teams-bot](https://github.com/Meeting-Baas/meet-teams-bot)

Open-source engine behind Meeting BaaS. BSL license. Supports Google Meet, Teams, and Zoom. Includes real-time transcription and automated summarization.

### Notable Failure: balena meetbot (Archived)

Balena's meetbot was **abandoned** because it became "increasingly hard to maintain with changing Google Meet UI and features breaking." This is the **#1 risk** of building your own meeting bot.

---

## 18. Build vs Buy (Recall.ai vs DIY)

### Engineering Effort

| Factor | Build Your Own | Use Recall.ai |
|--------|---------------|---------------|
| Time to first bot | 2-4 weeks | Hours |
| Time to production | 3-6 months | Days |
| Ongoing maintenance | Constant (selector updates) | Handled by Recall.ai |
| Platform coverage | Each platform is a separate project | Single API, 6+ platforms |
| Team size needed | 2-3 specialized engineers | 1 engineer |

### Cost at Scale

| Scale | DIY Monthly Cost | Recall.ai Monthly Cost |
|-------|-----------------|----------------------|
| 100 hours/month | ~$50 infra + engineer time | ~$65 + $1,000 platform fee |
| 1,000 hours/month | ~$200 infra + engineer time | ~$650 + $1,000 platform fee |
| 10,000 hours/month | ~$2,000 infra + engineer time | ~$6,500 + $1,000 platform fee |

**The hidden cost of DIY:** Engineer salaries. If one engineer spends 25% of their time maintaining the bot (selector updates, platform changes, debugging), that's ~$40,000-50,000/year. Recall.ai at $1,000/month + usage fees is cheaper until you're at very high volume.

### When to Build Your Own

- You need full control over the recording pipeline
- You're building it as a core competency (you ARE a meeting bot company)
- You need custom audio processing (your own ASR, diarization, etc.)
- You need to keep meeting data entirely within your infrastructure (compliance)
- You're at scale where Recall.ai costs exceed your infra + engineering costs
- **Our case:** We have an existing transcription pipeline (TranscriptionBase) and want full control

### When to Use Recall.ai

- You need to ship fast (days vs months)
- You need multi-platform support (Meet + Zoom + Teams) immediately
- You don't want to maintain browser automation code
- Your team is small and focused on product, not infrastructure
- Meeting recording is a feature, not your core product

---

## 19. The Three Approaches to Meeting Bots

### Approach 1: Browser Automation (What We're Building)

**How:** Playwright controls a Chromium browser, joins the meeting's web client, records system audio via PulseAudio + FFmpeg.

**Used by:** Recall.ai (for Meet), Fireflies, Otter, most open-source bots

**Pros:**
- Works on any platform with a web client
- No SDK approval needed
- Full audio capture (not dependent on platform's captioning)

**Cons:**
- Fragile CSS selectors (main maintenance burden)
- Bot detection risk
- Resource-heavy (~1 CPU + 1GB RAM per bot)
- Visible bot participant in meeting

### Approach 2: Native Platform SDK

**How:** Use the platform's official SDK to receive raw audio/video frames.

**Used by:** Recall.ai (for Zoom SDK), Gong (Zoom native), Fathom (Zoom app)

**Pros:**
- Direct media access (PCM audio, raw video frames)
- No browser overhead
- Better quality, lower resource usage

**Cons:**
- Platform-specific (Zoom SDK ≠ Teams SDK)
- Requires feature review/approval (weeks to months)
- Limited platforms (no Google Meet SDK for this)

### Approach 3: Botless (Platform Streaming API)

**How:** Use platform APIs that stream media without joining as a participant.

- **Zoom RTMS:** WebSocket streaming of per-participant audio/video
- **Google Meet Media API:** WebRTC-based live audio/video
- **Teams:** Post-meeting recording only (no real-time botless option)

**Used by:** Recall.ai (Meeting Direct Connect), Fathom (Zoom Live Streaming)

**Pros:**
- No visible bot participant
- Lower resource cost
- Official API support

**Cons:**
- Not available on all platforms
- Complex (raw WebRTC for Google)
- Limited features (Zoom RTMS: no chat. Google Meet API: only 3 participants)
- Requires platform approval

### Why We Choose Browser Automation

1. **Google Meet has no SDK** for real-time audio access
2. **Google Meet Media API** is WebRTC-based, requires implementing a full WebRTC stack, and limits to 3 participants
3. **Browser automation is the industry standard** for Google Meet bots
4. **We can add Zoom SDK/RTMS later** as a separate adapter without changing the rest of the architecture

---

## 20. Our Chosen Architecture & Why

Based on all the research above, here's our technology stack and why each choice was made:

| Component | Choice | Why | Competitor validation |
|-----------|--------|-----|----------------------|
| **Language** | Python | Matches existing TranscriptionBase backend | Gladia uses Python |
| **Browser automation** | Playwright | Auto-wait, Python support, browser contexts, trace viewer | Recall.ai, screenappai use Playwright |
| **Audio server** | PulseAudio (system mode) | Proven Docker support, Chromium default | Every meeting bot uses PulseAudio in Docker |
| **Virtual audio** | module-null-sink | Standard approach, monitor source for recording | Industry standard |
| **Display** | Xvfb | Required for Chromium audio (headless=no audio) | Every meeting bot uses Xvfb |
| **Recording** | FFmpeg segment muxer | Zero-gap chunking, single process | Industry standard |
| **Codec** | Opus 48kbps in OGG | Best speech compression, tiny files | Widely supported |
| **Chunk duration** | 30 seconds | Balances latency vs overhead | Common industry choice |
| **Upload** | Presigned S3 URLs | Least privilege, no AWS creds in bot container | Standard cloud pattern |
| **Deployment** | EC2 (not Fargate) | PulseAudio needs capabilities Fargate doesn't provide | Recall.ai uses EC2 exclusively |
| **Concurrency** | 2 bots per t3.xlarge | Each Chromium ~1 CPU + ~1 GB RAM | Similar to industry benchmarks |
| **Platform abstraction** | Adapter pattern | Enables adding Zoom/Teams later | Recall.ai has per-platform adapters |

### The Recording Pipeline (Putting It All Together)

```
┌─── Docker Container ──────────────────────────────────────────┐
│                                                                │
│  Xvfb :99                    PulseAudio (system mode)         │
│  (virtual display)            │                                │
│       │                       │                                │
│       ▼                       ▼                                │
│  Chromium (headless=false)   bot_sink (null sink)             │
│  DISPLAY=:99                  │                                │
│  Opens meet.google.com/xxx    │                                │
│  Plays meeting audio ────────►│                                │
│                               │                                │
│                          bot_sink.monitor                      │
│                               │                                │
│                               ▼                                │
│                          FFmpeg (segment muxer)                │
│                          -f pulse -i bot_sink.monitor          │
│                          -f segment -segment_time 30           │
│                               │                                │
│                               ▼                                │
│                    chunk_0000.ogg (30s)                        │
│                    chunk_0001.ogg (30s)                        │
│                    chunk_0002.ogg (30s)                        │
│                               │                                │
│                    File watcher detects completed chunk        │
│                               │                                │
│                    ┌──────────▼──────────┐                    │
│                    │   Upload Pipeline    │                    │
│                    │ 1. GET presigned URL │                    │
│                    │ 2. PUT to S3         │                    │
│                    │ 3. POST CHUNK_UPLOADED│                   │
│                    └─────────────────────┘                    │
└───────────────────────────────────────────────────────────────┘
```

---

## Sources

### PulseAudio & Audio in Docker
- [Audio in Docker containers, Linux audio subsystems](https://joonas.fi/2020/12/audio-in-docker-containers-linux-audio-subsystems-spotifyd/)
- [PulseAudio Modules Documentation](https://www.freedesktop.org/wiki/Software/PulseAudio/Documentation/User/Modules/)
- [Running PulseAudio as System-Wide Daemon](https://www.freedesktop.org/wiki/Software/PulseAudio/Documentation/User/SystemWide/)
- [PulseAudio/Examples - ArchWiki](https://wiki.archlinux.org/title/PulseAudio/Examples)
- [Using null-sinks with PulseAudio](http://www.g0hww.net/2013/11/using-null-sinks-with-pulseaudio.html)
- [x11docker Container Sound Wiki](https://github.com/mviereck/x11docker/wiki/Container-sound:-ALSA-or-Pulseaudio)

### PipeWire
- [PipeWire vs PulseAudio - It's FOSS](https://itsfoss.com/pipewire-vs-pulseaudio/)
- [PipeWire in Docker - Walker Griggs](https://walkergriggs.com/2022/12/03/pipewire_in_docker/)
- [Docker-Virtual-XVFB-pipewire](https://github.com/louisoutin/Docker-Virtual-XVFB-pipewire)

### Chromium Audio
- [Chromium Media README](https://chromium.googlesource.com/chromium/src/+/HEAD/media/README.md)
- [Chromium PulseAudio Output Source](https://chromium.googlesource.com/chromium/src/media/+/refs/heads/main/audio/pulse/pulse_output.cc)
- [Puppeteer headless audio issue #6774](https://github.com/puppeteer/puppeteer/issues/6774)
- [Chromium Autoplay Policy](https://developer.chrome.com/blog/autoplay)

### FFmpeg
- [FFmpeg Formats Documentation](https://ffmpeg.org/ffmpeg-formats.html)
- [Opus Recommended Settings - XiphWiki](https://wiki.xiph.org/Opus_Recommended_Settings)
- [Recording PulseAudio output with FFmpeg](https://passbe.com/2021/recording-pulseaudio-output-with-ffmpeg/)

### Browser Automation
- [Playwright vs Puppeteer - BrowserStack](https://www.browserstack.com/guide/playwright-vs-puppeteer)
- [Checkly Speed Comparison](https://www.checklyhq.com/blog/puppeteer-vs-selenium-vs-playwright-speed-comparison/)

### Bot Detection
- [Antoine Vastel - New Headless Chrome Fingerprint](https://antoinevastel.com/bot%20detection/2023/02/19/new-headless-chrome.html)
- [Intoli - Making Chrome Headless Undetectable](https://intoli.com/blog/making-chrome-headless-undetectable/)
- [DataDome - CDP Signal Detection](https://datadome.co/threat-research/how-new-headless-chrome-the-cdp-signal-are-impacting-bot-detection/)

### Competitor Research
- [Recall.ai - What is a meeting bot](https://www.recall.ai/blog/what-is-a-meeting-bot)
- [Recall.ai - How to build a Zoom bot](https://www.recall.ai/blog/how-to-build-a-zoom-bot)
- [Recall.ai - How to Build a Google Meet Bot](https://www.recall.ai/blog/how-i-built-an-in-house-google-meet-bot)
- [Recall.ai GitHub - Google Meet bot](https://github.com/recallai/google-meet-meeting-bot)
- [Fireflies.ai - How it joins meetings](https://guide.fireflies.ai/articles/9554534786)
- [Fathom - Using with Zoom Live Stream](https://help.fathom.video/en/articles/295552)
- [Gladia - How to build a Google Meet Bot](https://www.gladia.io/blog/how-to-build-a-google-meet-bot-for-recording-and-video-transcription)

### Deployment
- [AWS Fargate Security Considerations](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-security-considerations.html)
- [GitHub - Fargate /dev/shm issue](https://github.com/aws/containers-roadmap/issues/710)
- [Building a Zoom Meeting Bot on AWS](https://dev.to/atsimabistov/building-a-zoom-meeting-bot-in-2025-on-aws-2k4h)

### Open Source Bots
- [screenappai/meeting-bot](https://github.com/screenappai/meeting-bot)
- [meetingbot/meetingbot](https://github.com/meetingbot/meetingbot)
- [attendee-labs/attendee](https://github.com/attendee-labs/attendee)
- [Meeting-BaaS/meet-teams-bot](https://github.com/Meeting-Baas/meet-teams-bot)
- [balena meetbot (archived)](https://github.com/balena-io-experimental/meetbot)

### Google Meet Flow
- [Google Meet Waiting Rooms](https://support.google.com/meet/answer/16523457?hl=en)
- [S3 Presigned URLs - AWS Docs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/PresignedUrlUploadObject.html)
- [Illustrated Guide to Presigned URLs](https://fourtheorem.com/the-illustrated-guide-to-s3-pre-signed-urls/)
