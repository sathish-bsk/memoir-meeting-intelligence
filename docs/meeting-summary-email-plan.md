# Meeting Summary Email System — Unified Implementation Plan

## Context

Memoir sends a basic "Your call has been transcribed" email after transcription. The email has no branding, no decisions/open questions, flat action items without owners or urgency, and only goes to the recorder.

This plan implements the full meeting summary email system in a single phase: a new AI summary prompt (replacing P5), branded HTML email renderer (3 recipient types), multi-recipient dispatch with external approval gate, internal/external participant classification, token-based unsubscribe system, and the frontend UI for approval.

**Spec**: `/Users/bsk/Downloads/memoir-email-spec (1) (1).md`
**Sample HTML**: `/Users/bsk/Downloads/memoir-summary-branded (1).html`
**New Prompt**: `/Users/bsk/Downloads/memoir-summary-prompt.md`

---

## Step 1: Replace P5 Summary Prompt

**What**: Swap the P5 (Summary Generation) prompt in the 6-prompt Claude/Gemini pipeline with the new meeting intelligence prompt.

**Files to change:**
- `TranscriptionBase/app/job_processor/intelligence_pipeline.py`
  - Replace `SUMMARY_SYSTEM` (line 922) with new system prompt from `memoir-summary-prompt.md`
  - Replace `SUMMARY_USER` (line 927) with new user message template
  - Update `run_summary_generation()` function (line 1155) to:
    - Accept participant list + meeting metadata (title, date, duration, platform, recorder)
    - Build user message with participants table + transcript + JSON schema
    - Return new schema output (tldr, decisions, open_questions, action_items, discussion_threads, participants)
  - Change P5 tier from **fast (Haiku)** to **smart (Sonnet)** — the new prompt needs Sonnet-level reasoning for nuanced decision/action extraction

**Update pipeline orchestrator** (`run_full_pipeline()`, line ~1558):
- Pass participant list and meeting metadata to `run_summary_generation()`
- These are already available in the pipeline from P1/P2 results + function params

**Update 4 downstream consumers of P5 output:**

| Consumer | File | Current Field | Change |
|----------|------|---------------|--------|
| CallIntelligence DB | `intelligence_store.py:94` | `call_health = summary.get("call_health")` | Set `call_health = None` (P4's `deal_momentum` is the better signal; already stored separately) |
| MemoirDeal | `intelligence_store.py:267` | `deal.latest_call_health = data["summary"]["call_health"]` | Guard with `if data.get("summary", {}).get("call_health"):` — won't be populated from new P5, falls back to existing value |
| Intelligence API | `intelligence.py:649` | `row.summary.get("one_line_summary")` | Map to `row.summary.get("tldr", "")[:200]` (use first 200 chars of tldr) |
| Digest Generator | `digest_generator.py:174` | `summary_data.get("one_line_summary")` | Map to `summary_data.get("tldr", "")[:200]` |

**Output schema change** — P5 result stored in `CallIntelligence.summary` (JSONB column):
```
# BEFORE (P5 output):
{"agenda": {...}, "key_points": [...], "action_items": [...], "one_line_summary": "...", "call_health": "..."}

# AFTER (new prompt output):
{"meeting": {...}, "summary": {"tldr": "...", "decisions": [...], "open_questions": [...], "action_items": [...], "discussion_threads": [...]}}
```
No DB migration needed — JSONB column accepts any JSON shape.

**Fallback**: existing meetings with old P5 output continue to work. Consumers check for both `one_line_summary` and `tldr` keys.

---

## Step 2: Build Branded HTML Email Renderer

**New file:** `core_api/app/services/email_renderer.py`

Build the complete HTML email body in Python with all CSS inlined (email clients strip `<style>` tags). Convert the sample HTML (`memoir-summary-branded (1).html`) from CSS-variable-based to inline styles, using table-based layout for Outlook compatibility.

### Three renderer functions:

**`build_recorder_email(meeting, summary_data, call_url, edit_url, transcript_url, unsubscribe_url) -> (subject, html)`**

Sections (from spec §2, matching sample HTML):
- Header: Memoir logo + date/duration + meeting title + meta (platform · participants · type · recorder)
- 30-Second Summary: purple left-accent bar, `tldr` text
- Decisions Made: green cards with ✓ icon, title + detail (omit if empty)
- Still Unresolved: amber cards with ? icon (omit if empty)
- Action Items: urgency-grouped (Today → This Week → Next Week → Upcoming → Async), owner + urgency badge + task + context/blocking. Show "No action items identified" if empty.
- Discussion Threads: priority bars (high=purple, medium=amber, low=grey), title + summary + attributions. Cap at 6.
- Participants: tags with present/absent styling
- CTA Block: primary "View Full Meeting →" + secondary "Edit Summary" / "View Transcript"
- Footer: "Powered by Memoir · Delivered X minutes after meeting ended" + unsubscribe/settings/help links

Subject: `Your meeting summary: {title}`

**`build_internal_email(meeting, summary_data, recipient_email, recipient_name, call_url, unsubscribe_url) -> (subject, html)`**

Sections (from spec §4B):
- Header (same as recorder)
- 30-Second Summary (same)
- Action Items: filtered — highlight recipient's items, show count: "{n} other action items assigned to the team — view in Memoir"
- Decisions Made (same)
- CTA: "View Full Meeting Notes →"
- Footer

Subject: `Meeting recap: {title} — You have {n} action items`

**`build_external_email(meeting, summary_data, recipient_email, recipient_name, call_url) -> (subject, html)`**

Sections (from spec §4C):
- Header (meeting title + date + recorder name only)
- 30-Second Summary
- Their action items only (if any)
- CTA: "View Full Meeting Notes →" → gated page
- Growth footer: "Meeting notes powered by Memoir — AI meeting intelligence for teams that close deals."

Subject: `Notes from your meeting: {title}`

### Internal section builders (all return HTML strings, empty string if no data):
```python
_render_header(title, date, duration, platform, participant_count, org_name, recorder)
_render_tldr(tldr_text)
_render_decisions(decisions)
_render_open_questions(questions)
_render_action_items(items, filter_email=None, show_team_count=False)
_render_discussion_threads(threads)
_render_participants(participants)
_render_cta(call_url, edit_url=None, transcript_url=None)
_render_footer(unsubscribe_url=None, delivery_minutes=None, growth_mode=False)
```

### Color tokens (from spec §1):
```python
BRAND = "#6C5CE7"; BRAND_DARK = "#5647C4"; BRAND_LIGHT = "#EDE9FD"; BRAND_ULTRA_LIGHT = "#F7F5FF"
BG_PAGE = "#FAFAF8"; BG_SURFACE = "#FFFFFF"; BORDER = "#E8E6E1"; BORDER_LIGHT = "#F0EEEA"
TEXT_PRIMARY = "#1A1A18"; TEXT_SECONDARY = "#6B6B66"; TEXT_TERTIARY = "#9C9C96"
DECISION_BG = "#EDF7ED"; DECISION_BORDER = "#B8D4B8"; DECISION_TEXT = "#2D5A2D"
OPEN_BG = "#FFF8ED"; OPEN_BORDER = "#E0CC8A"; OPEN_TEXT = "#7A6520"
URGENT = "#E04B2F"; THIS_WEEK = "#D4890A"; UPCOMING = "#9C9C96"
```

---

## Step 3: Update Notification Client

**File:** `core_api/app/clients/notification_client.py`

**Replace** the current `send_meeting_summary_email()` and its 3 helper functions (`_build_summary_section`, `_build_action_items_html`, `_build_key_topics_html`) with:

```python
async def send_meeting_summary_email(
    to_email: str,
    recipient_type: str,          # "recorder" | "internal" | "external"
    recipient_name: str,
    meeting_id: str,
    meeting_title: str,
    meeting_date: str,
    duration_seconds: int,
    platform: str,
    summary_data: dict,           # New P5 output: {meeting: {...}, summary: {tldr, decisions, ...}}
    created_by: str,
    recipient_email: str = None,  # For action item filtering (internal/external)
    unsubscribe_url: str = None,  # Generated per recipient type (see Step 6)
) -> None:
```

This function:
1. Calls the appropriate `build_*_email()` from `email_renderer.py`
2. Passes the complete HTML to `send_email(template="meeting_summary_v2", data={"subject": subject, "body": html_body, "unsubscribe_url": unsubscribe_url})`

**Keep old helpers** as dead code briefly for rollback safety, then remove.

---

## Step 4: Update Webhook — Multi-Recipient Dispatch

**File:** `core_api/app/api/webhooks.py` — `_handle_transcription_complete()`

### 4a: Extract new P5 summary data

The 6-prompt pipeline result (from `intelligence.json`) now has the new summary format under the `summary` key:
```python
intelligence_data = await storage.read_json(intelligence_path)
summary_data = intelligence_data.get("summary", {})
```

### 4b: Send recorder email (always)
```python
await send_meeting_summary_email(
    to_email=owner_email,
    recipient_type="recorder",
    recipient_name=owner_name,
    meeting_id=meeting_id_str,
    meeting_title=meeting.title,
    meeting_date=meeting.started_at.isoformat(),
    duration_seconds=meeting.duration_seconds,
    platform=meeting.platform or "other",
    summary_data=summary_data,
    created_by=str(meeting.created_by),
    unsubscribe_url=f"{settings.FRONTEND_URL}/settings/notifications",
)
```

### 4c: Send internal participant emails
```python
from app.models.speaker import MeetingParticipant
from app.services.speaker_mapping import is_internal_participant

team = await db.get(Team, meeting.team_id)

participants = (await db.execute(
    select(MeetingParticipant)
    .options(selectinload(MeetingParticipant.speaker_profile))
    .where(MeetingParticipant.meeting_id == meeting.id)
)).scalars().all()

for p in participants:
    if not p.speaker_profile:
        continue
    if not is_internal_participant(p.speaker_profile, team):
        continue
    if p.speaker_profile.internal_user_id and str(p.speaker_profile.internal_user_id) == str(meeting.created_by):
        continue  # skip recorder

    user_id = str(p.speaker_profile.internal_user_id) if p.speaker_profile.internal_user_id else None
    if not user_id:
        continue

    user = await batch_get_users([user_id])
    user_data = user.get(user_id)
    if not user_data or not user_data.get("email"):
        continue

    # Check notification preferences
    pref = await _get_notification_pref(db, user_id, "participant_summary")
    if pref and not pref.email_enabled:
        continue

    await send_meeting_summary_email(
        to_email=user_data["email"],
        recipient_type="internal",
        recipient_name=user_data.get("name", "there"),
        ...,
        recipient_email=user_data["email"],
        unsubscribe_url=f"{settings.FRONTEND_URL}/settings/notifications",
    )
```

### 4d: Send external participant emails (only if approved)
```python
memoir_summary = (await db.execute(
    select(MemoirSummary).where(MemoirSummary.meeting_id == meeting.id)
    .order_by(MemoirSummary.version.desc()).limit(1)
)).scalar_one_or_none()

if memoir_summary and memoir_summary.external_share_approved:
    for p in participants:
        if not p.speaker_profile:
            continue
        if is_internal_participant(p.speaker_profile, team):
            continue  # skip internal
        if not p.speaker_profile.email:
            continue

        # Check suppression list
        suppressed = await db.execute(
            select(EmailSuppression).where(
                EmailSuppression.email == p.speaker_profile.email,
                EmailSuppression.team_id == meeting.team_id,
            )
        )
        if suppressed.scalar_one_or_none():
            continue

        # Generate unsubscribe token for external participant
        unsub_token = await _create_unsubscribe_token(db, p.speaker_profile.email, meeting.team_id, "meeting_summary")
        unsub_url = f"{settings.FRONTEND_URL}/api/notifications/unsubscribe?token={unsub_token}"

        await send_meeting_summary_email(
            to_email=p.speaker_profile.email,
            recipient_type="external",
            recipient_name=p.speaker_profile.display_name or "there",
            ...,
            recipient_email=p.speaker_profile.email,
            unsubscribe_url=unsub_url,
        )
```

---

## Step 5: Internal/External Participant Classification

**Problem**: `SpeakerProfile.internal_user_id` exists but is never populated. No logic classifies participants as internal vs external. We need this for multi-recipient emails.

**Classification logic** (two checks):

```python
def is_internal_participant(speaker_profile: SpeakerProfile, team: Team) -> bool:
    # Check 1: linked to an internal user account
    if speaker_profile.internal_user_id:
        return True
    # Check 2: email domain matches team domain
    if speaker_profile.email and team.domain:
        email_domain = speaker_profile.email.split("@")[1].lower()
        if email_domain == team.domain.lower():
            return True
    return False
```

**File:** `core_api/app/services/speaker_mapping.py`

Add a helper function `is_internal_participant()` that runs the two checks. Call it from the webhook dispatch loop (Step 4c/4d) instead of checking `internal_user_id` alone.

**Populate `internal_user_id` during speaker mapping** (same file, `_find_or_create_profile()`):
- When creating/updating a SpeakerProfile with an email, query `users` table (via auth service) to check if an account exists with that email in the same team
- If match found, set `internal_user_id = user.id`
- This auto-links participants to their Memoir accounts

**Update Step 4c/4d** to use `is_internal_participant()` instead of checking `internal_user_id` alone.

---

## Step 6: Token-Based Unsubscribe System

**Problem**: Current unsubscribe requires login. External participants don't have accounts. Email footer "Unsubscribe" link goes to settings page behind auth. No RFC 8058 one-click unsubscribe header.

### 6a: Unsubscribe token model

**File:** `core_api/app/models/notification.py` — add new model:

```python
class EmailUnsubscribeToken(Base):
    __tablename__ = "email_unsubscribe_tokens"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(320), nullable=False)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(50), nullable=False)  # "meeting_summary", "participant_summary"
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
```

Generate token: `secrets.token_urlsafe(32)` → 43-char URL-safe string.

### 6b: Unsubscribe endpoint (no login required)

**File:** `core_api/app/api/notifications.py`

```
GET /api/notifications/unsubscribe?token={token}
```

Logic:
1. Look up `EmailUnsubscribeToken` by token
2. For external participants (no account): create a row in `email_suppressions` table (email + team_id) — checked before sending external emails
3. Return a simple HTML page: "You've been unsubscribed from meeting summary emails."

### 6c: Email suppression for external participants

**File:** `core_api/app/models/notification.py` — add model:

```python
class EmailSuppression(Base):
    __tablename__ = "email_suppressions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(320), nullable=False)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    reason = Column(String(50), default="unsubscribed")  # "unsubscribed", "bounced", "complained"
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # Unique constraint: one suppression per email per team
```

Check this table before sending to any external participant.

### 6d: Generate unsubscribe links when sending emails

**File:** `core_api/app/clients/notification_client.py`

**Internal participants** (have Memoir accounts):
- `unsubscribe_url = f"{FRONTEND_URL}/settings/notifications"` (product settings page, requires login)
- Unsubscribing toggles `NotificationPreference.enabled = False` for `event_type="participant_summary"`
- Stored in existing `notification_preferences` table

**External participants** (no account):
- Create `EmailUnsubscribeToken(email=recipient, team_id=team_id, event_type="meeting_summary")`
- `unsubscribe_url = f"{FRONTEND_URL}/api/notifications/unsubscribe?token={token}"` (no login required)
- Unsubscribing creates a row in `email_suppressions` table
- Checked before every external email send

### 6e: Add List-Unsubscribe email header

**File:** `notification_service/app/api/email.py`

When sending via SendGrid or SMTP, add headers:
```python
# RFC 8058 one-click unsubscribe
headers = {
    "List-Unsubscribe": f"<{unsubscribe_url}>",
    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
}
```

This makes Gmail/Outlook show the native "Unsubscribe" button in the email header. Pass `unsubscribe_url` through the template data from the notification client.

### 6f: Migration

Add to the migration in Step 7:
```sql
CREATE TABLE email_unsubscribe_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token VARCHAR(64) UNIQUE NOT NULL,
    email VARCHAR(320) NOT NULL,
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ix_unsub_token ON email_unsubscribe_tokens(token);

CREATE TABLE email_suppressions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(320) NOT NULL,
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    reason VARCHAR(50) DEFAULT 'unsubscribed',
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (email, team_id)
);
```

---

## Step 7: DB Migration

**New file:** `core_api/alembic/versions/NNN_meeting_summary_v2.py`

### 7a: New email template
```sql
INSERT INTO email_templates (name, subject, html_body, description, category)
VALUES ('meeting_summary_v2', '{subject}', '{body}', 'Branded meeting summary (v2)', 'system');

UPDATE email_templates SET is_active = false WHERE name = 'meeting_summary';
```

### 7b: External share approval columns on MemoirSummary
```sql
ALTER TABLE memoir_summaries
ADD COLUMN external_share_approved BOOLEAN NOT NULL DEFAULT false,
ADD COLUMN external_share_approved_at TIMESTAMPTZ,
ADD COLUMN external_share_approved_by UUID;
```

### 7c: Store full summary JSON for Phase 2 preparation (spec §7)
```sql
ALTER TABLE memoir_summaries
ADD COLUMN summary_json JSONB;
```
This stores the complete new P5 summary output (tldr, decisions, action items, threads) per meeting — indexed by team_id via the meeting foreign key. Phase 2 cross-call context queries this.

### 7d: Unsubscribe tables
(See Step 6f above for SQL)

---

## Step 8: External Share Approval API

**File:** `core_api/app/api/meetings.py`

New endpoint:
```
POST /api/meetings/{id}/approve-external-share
Body: {}  (approval is binary — no section-level toggles for v1)
```

Logic:
1. Verify caller is the meeting's `created_by` (recorder only)
2. Set `memoir_summary.external_share_approved = True`, `approved_at = now()`, `approved_by = user_id`
3. Trigger external participant emails immediately (query participants, send)
4. Return `{"status": "shared", "recipients_count": n}`

---

## Step 9: Frontend — Share Approval UI

**File:** `frontend/src/app/(app)/calls/[id]/page.tsx`

After transcription completes (when memoir data loads), show a banner at the top of the call detail page IF there are external participants who haven't been shared with:

```
┌─────────────────────────────────────────────────────┐
│ 📤 Share summary with external participants?        │
│ Nick De Jonghe and 1 other external participant     │
│ will receive a summary of this meeting.             │
│                                                     │
│ [Share Summary]  [Skip]                             │
└─────────────────────────────────────────────────────┘
```

- "Share Summary" → `POST /api/meetings/{id}/approve-external-share` → banner disappears, toast "Summary shared with 2 external participants"
- "Skip" → banner disappears (no API call, preference not persisted — they can share later from settings)

**API needed to populate banner:** `GET /api/meetings/{id}` already returns `has_memoir` flag. Add `external_participants_count` and `external_share_approved` to the response schema. Source: count MeetingParticipant records where speaker profile is classified as external (via `is_internal_participant()` check) and has an email.

---

## Step 10: Notification Preferences

**File:** `core_api/app/api/notifications.py`

Add default preference for internal participant emails:
```python
DEFAULT_PREFS = {
    "transcription_complete": {"email": True, "slack": False},
    "participant_summary": {"email": True, "slack": False},  # NEW
}
```

Internal participants are emailed under event_type `"participant_summary"` so they can toggle it independently from their own recording notifications.

**Frontend:** `frontend/src/app/(app)/settings/notifications/page.tsx` — add toggle: "Receive summaries for meetings I participated in" (default: on).

---

## Files Changed Summary

| File | Action |
|------|--------|
| `TranscriptionBase/app/job_processor/intelligence_pipeline.py` | Replace P5 system/user prompts, update `run_summary_generation()`, change to Sonnet tier |
| `core_api/app/services/email_renderer.py` | **NEW** — 3 renderer functions + 9 section builders (~500 lines) |
| `core_api/app/clients/notification_client.py` | Replace `send_meeting_summary_email()` + add unsubscribe token generation |
| `core_api/app/api/webhooks.py` | Add multi-recipient dispatch (recorder + internal + external) with classification |
| `core_api/app/api/meetings.py` | Add `POST /approve-external-share` endpoint; add `external_participants_count` to GET response |
| `core_api/app/services/intelligence_store.py` | Update P5 field mappings (call_health, one_line_summary) |
| `core_api/app/services/digest_generator.py` | Map `one_line_summary` → `tldr` |
| `core_api/app/api/intelligence.py` | Map `one_line_summary` → `tldr` |
| `core_api/app/services/speaker_mapping.py` | Add `is_internal_participant()` helper + auto-populate `internal_user_id` during profile creation |
| `core_api/app/models/memoir_summary.py` | Add `external_share_approved`, `external_share_approved_at`, `external_share_approved_by`, `summary_json` columns |
| `core_api/app/models/notification.py` | Add `EmailUnsubscribeToken` and `EmailSuppression` models |
| `core_api/app/api/notifications.py` | Add `GET /unsubscribe?token=` endpoint (no auth) + `participant_summary` event type |
| `notification_service/app/api/email.py` | Add `List-Unsubscribe` + `List-Unsubscribe-Post` headers to outbound emails |
| `core_api/alembic/versions/NNN_*.py` | **NEW** migration — template + MemoirSummary columns + unsubscribe tables |
| `frontend/src/app/(app)/calls/[id]/page.tsx` | Add external share approval banner |
| `frontend/src/app/(app)/settings/notifications/page.tsx` | Add participant summary toggle |

---

## Execution Order

```
Step 1 (P5 prompt) ──────────────────────────┐
                                              ├──► Step 4 (webhook) ──► Step 10 (prefs)
Step 2 (email renderer) ──► Step 3 ──────────┘         │
                            (notification               │
                             client + unsub tokens)     │
                                              Step 5 (classification)
                                                        │
Step 6 (unsubscribe system) ─────────────────────────── │
                                                        │
                                              Step 7 (migration — all tables/columns)
                                                        │
                                              Step 8 (approval API)
                                                        │
                                              Step 9 (frontend banner)
```

Steps 1, 2, and 6 are independent — can be worked in parallel. Step 3 depends on Steps 2+6. Step 4 depends on Steps 1+3+5. Step 7 (migration) should run before deployment. Steps 8-10 can be done in any order after Step 7.

---

## Verification

1. **P5 prompt**: Run a test transcript through the 6-prompt pipeline. Verify JSON output matches the new schema (tldr, decisions, open_questions, action_items with urgency, discussion_threads with priority).
2. **Email renderer**: Render sample HTML from the new P5 output. Open in browser, compare against `memoir-summary-branded (1).html`. Check: all sections render, conditional sections hide when empty, urgency badges show correct colors.
3. **Email sending**: Trigger a real transcription. Verify recorder receives branded email. Check Gmail + Outlook rendering.
4. **Internal emails**: Verify internal participants receive lean email with only their action items highlighted.
5. **External gate**: Verify external participants do NOT receive email until recorder clicks "Share Summary" on the call detail page. After approval, verify teaser email is sent.
6. **Fallback**: Process an old meeting (pre-prompt-change). Verify email still renders with graceful degradation (uses `narrative_summary[:400]` as tldr fallback, omits missing sections).
7. **Preferences**: Toggle participant summary preference off. Verify internal emails stop for that user.
8. **Unsubscribe (internal)**: Click unsubscribe link in internal email → opens settings page → toggle off → verify no more participant emails.
9. **Unsubscribe (external)**: Click unsubscribe link in external email → token-based endpoint → confirmation page → verify email added to `email_suppressions` → verify no more external emails for that address.
10. **List-Unsubscribe header**: Check email headers in Gmail → native "Unsubscribe" button should appear in email header bar.
