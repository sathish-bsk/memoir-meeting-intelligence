# Memoir — Use Case Document

## Product Overview

Memoir is a meeting intelligence platform built for sales teams. It records sales calls, transcribes them with AI, extracts structured intelligence, and pushes insights to CRM systems — turning every conversation into actionable data.

**Core value proposition**: Record → Transcribe → Analyze → CRM. Automatically.

---

## Table of Contents

1. [User Roles & Access Control](#1-user-roles--access-control)
2. [Authentication & Account Management](#2-authentication--account-management)
3. [Onboarding](#3-onboarding)
4. [Meeting Recording](#4-meeting-recording)
5. [Transcription Pipeline](#5-transcription-pipeline)
6. [Memoir Summary (AI Analysis)](#6-memoir-summary-ai-analysis)
7. [Call Intelligence](#7-call-intelligence)
8. [Coaching Metrics](#8-coaching-metrics)
9. [Calendar Integration & Auto-Join](#9-calendar-integration--auto-join)
10. [CRM Integration](#10-crm-integration)
11. [Team Collaboration](#11-team-collaboration)
12. [Notifications & Digests](#12-notifications--digests)
13. [Search & Discovery](#13-search--discovery)
14. [Sharing & Public Access](#14-sharing--public-access)
15. [Dashboard & Analytics](#15-dashboard--analytics)
16. [Chrome Extension](#16-chrome-extension)
17. [Admin & Super Admin](#17-admin--super-admin)
18. [Billing & Plan Management](#18-billing--plan-management)

---

## 1. User Roles & Access Control

Memoir uses a role-based access control (RBAC) system with three roles:

### Roles

| Role | Description |
|------|-------------|
| **Owner** | Full control. Can manage billing, transfer ownership, change any member's role, delete resources. One per team. |
| **Admin** | Can manage team members (invite, remove), delete calls, write intelligence, and access billing (read-only). |
| **Member** | Can read/write calls, share calls, read intelligence, and view team info. |

### Permissions Matrix

| Permission | Member | Admin | Owner |
|------------|--------|-------|-------|
| View calls | Yes | Yes | Yes |
| Record/upload calls | Yes | Yes | Yes |
| Edit transcripts & summaries | Yes | Yes | Yes |
| Delete calls | No | Yes | Yes |
| Share calls (public link) | Yes | Yes | Yes |
| View intelligence | Yes | Yes | Yes |
| Edit intelligence | No | Yes | Yes |
| View team members | Yes | Yes | Yes |
| Invite/remove members | No | Yes | Yes |
| Manage roles | No | No | Yes |
| View billing | No | Yes | Yes |
| Manage billing | No | No | Yes |
| Transfer ownership | No | No | Yes |

### Scope Overrides

Owners can configure per-member permission overrides that deviate from the default role permissions. For example, a specific Member can be granted `calls:delete` without being promoted to Admin.

---

## 2. Authentication & Account Management

### Sign Up

- **Email & password registration** with NIST 800-63B password policy (8+ characters, HIBP breach check)
- **OAuth sign up** via Google or Microsoft accounts (passwordless)
- **Invite-based sign up**: Users receiving a team invite link (`/join?code=MEM-XXXX-XXXX`) are guided through a streamlined signup with pre-filled email

### Login

- Email + password login
- OAuth login (Google, Microsoft)
- Redirect preservation: deep links are restored after login

### Token Management

- **JWT (ES256)**: 15-minute access tokens, 7-day refresh tokens
- **Family-based rotation**: Refresh tokens use family IDs to detect token reuse (replay attacks)
- **httpOnly cookies**: Refresh tokens stored securely, not accessible to JavaScript

### Profile Management

- Edit display name
- Change email (requires password verification, triggers new email verification)
- Email verification flow (link sent to new email)
- Change password (requires current password)
- View connected OAuth providers (Google, Microsoft badges)
- View active sessions with ability to revoke individual sessions

---

## 3. Onboarding

### Two-Step Flow

**Step 1 — Team Decision**
- "Solo or Team?" choice:
  - **Just for me**: Creates a personal workspace
  - **For my team**: Two sub-options:
    - **Create new workspace**: Proceed to Step 2
    - **Join existing team**: Domain-based lookup finds teams matching the user's email domain. If found, user can "Request Access" (admin approval required). If not found, option to create own workspace.

**Step 2 — Workspace Setup**
- Company/workspace name (pre-filled from email domain)
- Role selection (4 options): Founder, Sales Lead, Sales Rep, RevOps/Product
- Completes onboarding and redirects to home

### Setup Checklist (Post-Onboarding)

Shown on the home page until all steps are completed:
- Connect a calendar (Google or Outlook)
- Connect a CRM (HubSpot or Zoho)
- Install Chrome extension

---

## 4. Meeting Recording

Memoir supports **5 methods** of getting meeting audio into the system:

### 4a. Bot Recording (Automated)

A Playwright-based bot joins the meeting via browser automation inside a Docker container (Xvfb + PulseAudio + Chromium).

**Supported platforms**: Google Meet, Zoom, Microsoft Teams

**How it works**:
1. User clicks "Record" on a calendar event, or auto-join triggers automatically
2. Core API sends a job to the Orchestrator via BullMQ
3. Orchestrator spawns a BotWorker Docker container with the meeting URL
4. Bot joins the meeting, starts Xvfb virtual display + PulseAudio audio capture
5. Records audio in real-time (FFmpeg)
6. Monitors for meeting end via DOM signals and participant count tracking
7. On meeting end: stops recording, uploads audio, triggers transcription pipeline
8. Container is cleaned up

**Lifecycle events**: `bot_joining` → `waiting_room_entered` → `joined_meeting` → `meeting_ended` → `recording_complete`

### 4b. Chrome Extension Recording

The Memoir Chrome Extension captures audio directly from the browser tab.

**Two recording modes**:
- **Mixed mode**: Tab audio + microphone merged into a single stream
- **Separate mode**: Tab audio and microphone recorded as independent tracks (better for speaker diarization)

**How it works**:
1. User clicks the extension popup's record button while in a meeting
2. Extension uses `tabCapture` API to capture tab audio, `getUserMedia` for microphone
3. Audio is chunked every 30 seconds using MediaRecorder (Opus WebM codec)
4. Chunks are uploaded to Core API via a streaming session (`tab_audio_sessions`)
5. On recording stop: session is finalized, chunks are concatenated to WAV, transcription job is queued

**Platform-agnostic**: Works on any meeting platform that runs in a browser tab.

### 4c. File Upload

Users can upload pre-recorded audio/video files directly.

**Supported formats**: WAV, MP3, MP4, M4A, WebM, OGG, FLAC

**How it works**:
1. User opens the upload modal on the Calls page
2. Drags & drops or selects a file
3. Optionally enters a title and meeting metadata
4. File is uploaded to storage, a Meeting record is created, and a transcription job is queued

### 4d. Link Upload (URL / YouTube)

Users can paste a URL to a media file or a YouTube video.

**Supported sources**: Direct media URLs (MP3, MP4, etc.) and YouTube video URLs

**How it works**:
1. User pastes a link in the upload modal
2. Core API detects if it's a YouTube URL or direct media URL
3. TranscriptionBase downloads the media (yt-dlp for YouTube, httpx for direct URLs)
4. Converts to WAV with FFmpeg and processes through the standard pipeline

### 4e. Cloud Recording Import

Memoir can automatically import cloud recordings from Zoom and Microsoft Teams.

**Zoom Cloud Recording**:
- Zoom sends a `recording.completed` webhook when a cloud recording is ready
- Memoir verifies HMAC-SHA256 signature, downloads the best audio file, uploads to R2 storage
- Creates a Meeting + transcription job automatically

**Teams Cloud Recording**:
- Microsoft Graph sends a webhook notification when a call record is created
- Memoir fetches the call record details, downloads the recording from Graph API
- Creates a Meeting + transcription job automatically

**Settings per integration**:
- `auto_import`: Enable/disable automatic import
- `recording_preference`: "Bot only" / "Auto (bot + cloud)" / "Cloud only"

---

## 5. Transcription Pipeline

Memoir uses a sophisticated multi-phase transcription pipeline running on self-hosted ML models.

### Phase 1 — Audio Preprocessing
- **Format detection**: Identify codec, sample rate, channels
- **Loudness normalization**: Target -23 LUFS (EBU R128)
- **VAD silence trimming**: Remove leading/trailing silence using Silero VAD
- **Optional denoising**: If SNR < 15dB, apply DeepFilterNet3 or Resemble Enhance (generative diffusion model)

### Phase 2 — Speaker Diarization
- **Pyannote 3.1**: Identifies "who spoke when" before transcription
- Produces timestamped speaker segments (SPEAKER_00, SPEAKER_01, etc.)
- Runs before ASR to improve per-speaker accuracy

### Phase 3 — Speech Recognition (ASR)
- **Engine**: faster-whisper (CTranslate2 implementation of OpenAI Whisper)
- **Model sizes**: medium (default) or large-v3-turbo
- **Smart chunking**: Splits long audio at silence boundaries for parallel processing
- **Language detection**: Per-chunk language detection with auto-detect fallback
- **Supports 17+ languages** including English, Hindi, Tamil code-switching

### Phase 4 — Forced Alignment
- **CTC forced aligner**: Produces word-level timestamps
- **VAD remapping**: Corrects alignment drift across chunks
- **Tail re-transcription**: Handles words at chunk boundaries

### Phase 5 — Post-Processing Correction
- **5a — Vocabulary correction**: Phonetic matching (Metaphone) + edit distance against custom vocabulary lists. Corrects domain-specific terms (product names, acronyms, jargon)
- **5b — LLM context correction**: Qwen 2.5 (3B dev / 14B prod) via Ollama corrects low-confidence words using surrounding context
- **Abbreviation normalization**: Expands common abbreviations to full forms

### Phase 6 — Translation (Optional)
- **IndicTrans2** for Tamil/Hindi → English translation
- **Whisper** for other language translations

### Phase 7 — Intelligence Extraction
- **7A — Coaching metrics**: Talk ratio, longest monologue, question count, filler word count, interruptions
- **7B — Call intelligence**: Single-pass LLM extraction of competitors, objections, pain points, feature requests, buying signals, sentiment, deal stage, MEDDIC/BANT scoring

### Phase 10 — Memoir Summary
- **7 discovery fields** with confidence tagging (definitive / indicative / inferred) and source attribution (exact quote + speaker name)
- **Narrative summary**: One-paragraph call summary
- **Action items**: Rep tasks + prospect commitments
- **Next steps**: Next meeting, next milestone, suggested deal stage

### Pipeline Output
For each meeting, the pipeline produces:
- `transcript.txt` — Full text transcript
- `transcript_segments.json` — Speaker-labeled segments with timestamps
- `analysis.json` — Coaching metrics + intelligence
- `intelligence.json` — Structured extraction (competitors, objections, etc.)
- `embeddings.json` — Speaker voice embeddings (192-dim ECAPA-TDNN)

---

## 6. Memoir Summary (AI Analysis)

The Memoir Summary is the primary output of each call — a CRM-ready analysis presented on the call detail page.

### Components

**Narrative Summary**: A one-paragraph overview of the call covering key topics, outcomes, and sentiment.

**Discovery Fields**: Structured CRM data extracted from the conversation:
- Budget range
- Authority / decision maker
- Need / pain point
- Timeline
- Competitors mentioned
- Next steps
- Custom fields

Each field includes:
- **Value**: The extracted data point
- **Confidence level**: Definitive (explicitly stated), Indicative (strongly implied), Inferred (deduced from context)
- **Speaker**: Who said it
- **Exact quote**: The verbatim quote supporting the extraction

**Action Items**: Split into two categories:
- **Your To-dos**: Tasks for the sales rep (with checkboxes)
- **Their Commitments**: Promises made by the prospect

**Signals**:
- **Buying signals**: Positive indicators (e.g., "asking about implementation timeline", "mentioning budget approval")
- **Risk signals**: Red flags (e.g., "mentioned competitor evaluation", "pushed back on pricing")

**Next Steps**:
- Next meeting date/time
- Next milestone
- Suggested deal stage

### Editing
All memoir components are editable inline. Users can:
- Edit the narrative summary
- Modify discovery field values
- Add/remove/check action items
- Edit signal lists
- Reset to original AI version

---

## 7. Call Intelligence

The Intelligence Hub aggregates insights across all calls for the team, providing a bird's-eye view of market signals.

### Intelligence Categories

**Voice of Customer (Pain Points)**
- Aggregated pain points across all calls, ranked by mention frequency
- Each pain point includes: mention count, exact quotes, speaker names, call dates
- Deep-dive view with full quote context

**Product & Feature Requests**
- Feature requests aggregated across calls
- Ranked by frequency with urgency breakdown
- Helps prioritize product roadmap

**Competitive Intelligence** (Admin-only)
- Competitor mentions with frequency tracking
- Likes (what prospects like about competitors) and dislikes
- Sentiment breakdown per competitor
- Track competitive positioning over time

**Deal Intelligence (Objections)**
- Common objections with frequency and category grouping
- Resolution rate tracking
- CRM stage grouping available

**PMF Signals (Product-Market Fit)** (Admin-only)
Five-dimension weekly scoring:
1. **Feature Demand**: Top requested features with trend vs. previous week (1-5 stars)
2. **Objection Frequency**: Top objections with trend (1-5 stars)
3. **Competitor Mentions**: Top competitors with trend (1-5 stars)
4. **Buy Signal Strength**: % of calls with committed next steps (1-5 stars)
5. **Pain Clarity**: % convergence on a single top pain point (1-5 stars)

Historical trend charts show week-over-week progression.

**FAQ (Frequently Asked Questions)**
- Questions asked by prospects, grouped by similarity
- Ranked by frequency
- Helps create better sales enablement materials

### Filtering & Export
- Date range filter: 7 days, 30 days, 90 days, All time
- CSV export of all intelligence categories
- Shareable public links for intelligence deep-dives

---

## 8. Coaching Metrics

Each call produces automated coaching metrics to help reps improve their sales technique.

### Metrics Tracked

| Metric | Description |
|--------|-------------|
| **Talk Ratio** | Percentage of time the rep spoke vs. the prospect. Ideal: 40-60% rep talk time. |
| **Longest Monologue** | Duration of the rep's longest uninterrupted speaking segment. Flags one-sided pitches. |
| **Question Count** | Number of questions asked by the rep vs. the prospect. Discovery calls should have more rep questions. |
| **Filler Word Count** | Count of filler words (um, uh, like, you know) per speaker. Indicates confidence level. |
| **Interruptions** | Number of times each speaker interrupted the other. |

### Coaching Trends (Dashboard)
- Daily aggregated coaching metrics over time
- Team-level trends to identify training needs
- Per-rep comparison (when multiple reps are on the team)

---

## 9. Calendar Integration & Auto-Join

### Calendar Connection

Memoir connects to Google Calendar and Outlook Calendar via OAuth to sync upcoming meetings.

**Google Calendar**:
- OAuth scope: `calendar.events.readonly`
- Syncs next 7 days of events
- Extracts meeting URLs from: Google Meet conference data, description, location

**Outlook Calendar**:
- OAuth via Microsoft Graph API
- Syncs next 7 days of events
- Extracts meeting URLs from: `onlineMeeting.joinUrl`, body content, location

### Calendar Event Display
- Events listed with title, start/end time, meeting URL, provider
- Manual "Record" button per event to schedule a bot
- Cancel bot for scheduled events

### Auto-Join (Automatic Bot Scheduling)

When enabled, Memoir automatically sends a recording bot to join meetings at their scheduled start time.

**How it works**:
1. Calendar events are synced to the database via push notifications (Google watch channels / Microsoft Graph subscriptions)
2. A scheduler polls every 60 seconds for events starting within the next 1 minute
3. For eligible events (has meeting URL, `auto_record = true`, not already scheduled):
   - Marks the event as `bot_scheduled = true` immediately
   - Creates a Meeting record (`source = "calendar"`)
   - Dispatches a bot via the Orchestrator
4. Deduplication: If multiple team members share the same calendar event, only one bot is sent

**Retry logic**:
- On orchestrator failure: `bot_retry_count` increments, event is unlocked for retry
- After 3 failures: Event is permanently marked, a failed Meeting record is created for user visibility

**User control**:
- Per-connection toggle: "Automatically record meetings" checkbox in Integrations page
- Defaults to enabled for new connections

### Real-time Calendar Sync

**Google Calendar**: Push notifications via `events.watch()` API. Google sends a webhook when any event changes. Memoir performs an incremental sync using `syncToken`.

**Microsoft Graph**: Subscriptions via `POST /subscriptions`. Microsoft sends a webhook with change details. Memoir performs an incremental sync using `deltaLink`.

**Channel/subscription renewal**: Automatically renewed every 6 hours before expiry (Google ~7 days, Microsoft ~3 days).

---

## 10. CRM Integration

Memoir integrates with HubSpot and Zoho CRM to push call intelligence directly into CRM records.

### Supported CRMs

| CRM | Features |
|-----|----------|
| **HubSpot** | Contact/deal lookup, field mapping, auto-push, stage mapping |
| **Zoho CRM** | Contact/deal lookup, field mapping, auto-push, stage mapping |

### Connection Flow
1. OAuth authentication with the CRM provider
2. Field mapping configuration (map Memoir discovery fields to CRM properties)
3. Stage mapping configuration (map deal stages between systems)
4. Enable auto-push toggle

### Field Mapping
Maps Memoir's discovery fields to CRM contact or deal properties:
- Pain Points → CRM custom field
- Next Steps → CRM field
- Budget Range → CRM field
- Decision Makers → CRM field
- Competitors → CRM field
- Call Summary → CRM notes/description

### CRM Push Flow
1. After transcription completes, Memoir extracts CRM-ready data
2. Matches the call to an existing CRM contact/deal via:
   - Email → domain → company name matching
   - Deal match logs for audit trail
3. If `auto_push_enabled`: Automatically pushes to CRM
4. If manual: User reviews data and clicks "Push to CRM" or "Skip"
5. Push results tracked with status: pending → processing → completed/failed/skipped

### Features
- **Auto-push**: Automatically push call data to CRM after each call
- **Manual review**: Review and edit extracted data before pushing
- **Re-push**: Re-push with edited data
- **Batch push**: Push all historical calls to CRM at once
- **Regenerate extraction**: Re-run CRM extraction for a specific meeting
- **Push audit log**: Track all CRM pushes with timestamps and status

---

## 11. Team Collaboration

### Team Management

**Invite Members**:
- Admin/Owner generates an invite link with code (`MEM-XXXX-XXXX` format)
- Shareable join URL: `app.meetmemoir.com/join?code=MEM-XXXX-XXXX`
- Invited user signs up or logs in, then auto-joins the team

**Join Requests**:
- Users can request to join a team discovered via email domain lookup
- Admins/Owners see pending requests with Approve/Decline actions

**Member Management**:
- View all team members with their roles and activity (calls this week)
- Remove members (admin/owner)
- Resend or revoke pending invites

**Role Management** (Owner only):
- Change member roles (Member ↔ Admin)
- Transfer ownership (irreversible, demotes current owner to Admin)

### Usage Tracking
- Member count vs. plan limit displayed as a progress bar
- Plan-based limits enforced (e.g., 5 members on free plan)

---

## 12. Notifications & Digests

### Event Notifications

Three notification channels: **In-app**, **Email** (SendGrid), **Slack**

| Event | Description |
|-------|-------------|
| **Call processed** | Triggered when transcript and analysis are ready for a call |
| **Intelligence digest** | Periodic summary of call intelligence across the team |
| **FAQ digest** | Periodic summary of frequently asked questions |

Each event can be independently toggled on/off per channel.

### Digest System

**Intelligence Digest**: Aggregates 7 days (or configured period) of call data and generates a PMF-focused narrative summary.

Contents:
- Total calls analyzed
- Average pull score, ICP fit, pain fit
- Top 5 pain points, objections, competitors, feature requests
- Buying signals and hesitation signals
- Unexpected insights
- LLM-generated narrative assessment (via Groq/Llama 3.3 70B, or template fallback)

**PMF Weekly Digest**: Five-dimension product-market fit scoring (feature demand, objections, competitors, buy signals, pain clarity) with week-over-week trends and star ratings.

**FAQ Digest**: Groups similar questions from calls and ranks by frequency.

### Digest Scheduling
- Configurable frequency: Daily, Weekly, Biweekly, Monthly
- Configurable day of week (for weekly/biweekly) or day of month (for monthly)
- Configurable time of day (default: 06:00 UTC)
- "Generate Now" button for manual trigger
- Audit log showing all past digest runs with status and results

### Slack Notifications
- Connect Slack workspace via OAuth
- Select notification channel (public or private)
- Send test message to verify configuration
- Digest summaries posted to the configured channel

---

## 13. Search & Discovery

### Full-Text Search
- Searches across meeting titles and full transcript text
- Uses PostgreSQL `tsvector` for performant full-text indexing
- Returns results with highlighted snippets showing matching context
- Filters by team context

### Enhanced Search
- **Call results**: Matches in meeting metadata (title, participant, company)
- **Transcript results**: Matches within transcript text with timestamp references
- Results link directly to the call detail page

---

## 14. Sharing & Public Access

### Public Call Sharing
1. On any call detail page, click "Share" → "Create public link"
2. Generates a unique share token
3. Public URL: `app.meetmemoir.com/shared/{token}`
4. Anyone with the link can view (no login required):
   - Call summary and narrative
   - Action items
   - Signals (buying/risk)
   - Discovery fields with confidence levels
   - Coaching metrics
   - Full transcript with speakers and timestamps
5. View counter tracks how many times the link has been accessed
6. Link can be revoked at any time

### Intelligence Sharing
- Public share links can also be created for intelligence deep-dive pages
- Share competitive intel, pain points, or feature request analyses externally

---

## 15. Dashboard & Analytics

### Home Dashboard (All Users)

**This Week at a Glance** (4 metrics):
- Calls this week
- Total calls (all time)
- Total minutes recorded
- Week-over-week call delta (percentage with trend arrow)

**Recent Calls**: Last 5 completed calls with:
- Title and primary participant
- Date and duration
- Confidence score (color-coded border: green ≥70%, amber ≥40%, red <40%)

**Setup Checklist**: Tracks completion of calendar, CRM, and extension setup

### Admin Dashboard

**Overview Cards** (5 metrics):
- Total teams
- Total users
- Meetings today (with weekly count)
- Cost this month (with audio hours)
- Failed jobs today (with success rate)

**Churn Risk Panel**: Teams ranked by inactivity with risk levels (low/medium/high)

**Recent Activity**: Last 15 activities across all teams

---

## 16. Chrome Extension

### Overview
The Memoir Chrome Extension enables one-click recording from any meeting platform directly in the browser.

### Features

**Recording**:
- One-click start/stop from the extension popup
- Floating timer overlay in the meeting tab
- Dual-track audio capture (tab audio + microphone)
- Two modes: mixed (single stream) or separate (independent tracks)
- Platform detection: automatically identifies Google Meet, Zoom, Teams

**Authentication Bridge**:
- Shares authentication state with the main web app
- OAuth token stored in `chrome.storage.local`
- Automatic token refresh (every 12 minutes, before 15-minute expiry)

**Chunked Upload**:
- Audio is recorded in 30-second chunks (Opus WebM codec)
- Chunks are uploaded in real-time to Core API
- On recording stop: session finalized, chunks concatenated, transcription queued

**Platform Support**:
- Google Meet: Content script injected at `document_idle`
- Zoom Web: Content script for web client (`zoom.us/wc/*`, `zoom.us/j/*`, etc.)
- Microsoft Teams: Content script injected at `document_start` (both content world and MAIN world)

### Build
- Development: `npm run build:dev` (with hot reload via `npm run watch`)
- Production: `npm run build` → outputs to `chrome_extension/dist/`
- Load as unpacked extension in Chrome

---

## 17. Admin & Super Admin

### Super Admin Dashboard

**Access**: Only users with `is_super_admin = true` flag

**Team Lookup**:
- Search by email address
- Returns: user name, email, team name, current plan, plan limits, trial end date

**Plan Management**:
- Change any team's plan: Trial → Free → Paid
- Updates plan limits dynamically
- Immediate effect on team's feature access

### Admin Analytics

**Cost Analytics**:
- Total cost this month
- Average cost per meeting
- Total audio hours processed
- Cost breakdown by source (bot, upload, cloud recording)
- Projected monthly cost
- Daily cost trend charts

**Per-Team Cost Breakdown**:
- Cost per team with meeting count and average cost
- Drill-down to individual meeting costs (ASR + intelligence costs)

**Customer Health**:
- All teams with member count, weekly meetings, last activity, churn risk
- Drill-down: per-team recording sources, meeting trends, cost trends, member activity
- Churn risk classification: low (<7 days inactive), medium (7-14 days), high (>14 days)

**Processing Monitoring**:
- Active/waiting/completed/failed job counts
- Success rate (7-day rolling)
- Average processing time
- Queue depth
- Failure log with error messages and retry counts

---

## 18. Billing & Plan Management

### Plans

| Feature | Free | Trial | Paid |
|---------|------|-------|------|
| Calls per month | Limited | Generous | Unlimited |
| Team members | Limited | Generous | Unlimited |
| Storage | Limited | Generous | Unlimited |
| Intelligence reports | Limited | Generous | Unlimited |

### Trial Management
- Trial duration with countdown ("X days remaining")
- Trial expiry warning
- Upgrade prompt after expiry

### Plan Display
- Current plan badge (color-coded: green=Paid, blue=Trial, gray=Free)
- Plan limits table showing current limits
- Usage tracking (calls this month vs. limit)

### Upgrade Flow
- Only team owners can manage billing
- Contact sales flow (`hello@meetmemoir.com`) for plan upgrades

---

## Appendix: Integration Matrix

| Integration | Auth Method | Data Flow | Features |
|-------------|-------------|-----------|----------|
| Google Calendar | OAuth 2.0 | Sync events → Auto-join bots | Event sync, push notifications, auto-record |
| Outlook Calendar | OAuth 2.0 (Microsoft Graph) | Sync events → Auto-join bots | Event sync, push notifications, auto-record |
| Zoom | OAuth 2.0 | Cloud recording import | Auto-import, recording preference |
| Microsoft Teams | OAuth 2.0 (Graph API) | Cloud recording import + call records | Auto-import, recording preference, admin consent |
| Slack | OAuth 2.0 | Notifications → Slack channel | Channel selection, event notifications, digest delivery |
| HubSpot | OAuth 2.0 | Call data → CRM records | Field mapping, stage mapping, auto-push |
| Zoho CRM | OAuth 2.0 | Call data → CRM records | Field mapping, stage mapping, auto-push |
| Chrome Extension | JWT (shared auth) | Tab audio → Memoir | Two recording modes, chunked upload, platform detection |

---

## Appendix: Meeting Lifecycle

```
                    ┌──────────┐
                    │  Created │
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │ Pending  │
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │ Joining  │
                    └────┬─────┘
                         │
               ┌─────────▼──────────┐
               │ Waiting in Lobby   │  (optional)
               └─────────┬──────────┘
                         │
                    ┌────▼─────┐
                    │Recording │
                    └────┬─────┘
                         │
                    ┌────▼──────┐
                    │Processing │
                    └────┬──────┘
                         │
             ┌───────────▼────────────┐
             │Waiting for Transcription│
             └───────────┬────────────┘
                         │
              ┌──────────▼──────────┐
              │     Completed       │
              │ (transcript ready)  │
              └─────────────────────┘

              ┌─────────────────────┐
              │       Failed        │  (at any stage)
              └─────────────────────┘
```

---

*Last updated: March 2026*
