# Memoir: Post-Processing System
## Call Summary → CRM Auto-Fill Pipeline

*Complete specification for: Summary Template, Email Template, LLM Prompt, Edge Cases*

---

# TABLE OF CONTENTS

1. [Confidence Framework](#1-confidence-framework)
2. [Summary Template](#2-summary-template)
3. [CRM Field Mapping (HubSpot)](#3-crm-field-mapping)
4. [Email Template Spec](#4-email-template)
5. [LLM Prompt (Production-Ready)](#5-llm-prompt)
6. [Edge Cases & Handling Rules](#6-edge-cases)
7. [Follow-Up Call Threading Logic](#7-threading-logic)

---

# 1. CONFIDENCE FRAMEWORK

Every extracted data point gets tagged with one of three confidence levels. This is the backbone of the entire system — it determines what gets pushed to CRM fields vs. what stays in notes vs. what gets excluded.

## Definitions

### 🟢 DEFINITIVE
The prospect explicitly stated this information in clear, unambiguous language.

**Examples:**
- "Our budget for this is $50,000"
- "I'm the one who signs off on this"
- "We need this live by March"
- "We're currently using Salesforce"

**CRM behavior:** Auto-pushed to custom field. Shown in summary with no qualifier.

### 🟡 INDICATIVE
Strongly implied through context, comparison, or indirect language. A reasonable salesperson would note this down as "likely true."

**Examples:**
- "Tools in this range usually cost us around 40-60K" (budget ~$40-60K)
- "I'll need to run this by my VP" (authority = not the final decision maker)
- "We've been evaluating options since Q3" (timeline = active evaluation)
- "We have about 30 people on the sales floor" (team size ~30)

**CRM behavior:** Auto-pushed to custom field with `[indicative]` tag. Shown in summary with "~" or "approximately" qualifier.

### 🔵 INFERRED
AI's interpretation based on indirect signals, industry patterns, or contextual clues. Not stated or clearly implied — this is the AI connecting dots.

**Examples:**
- Company size + industry suggests mid-market budget (no numbers mentioned)
- Prospect asked about enterprise features → likely evaluating for larger team
- Tone/urgency suggests timeline is short but no date given

**CRM behavior:** NOT pushed to custom fields. Included in the Notes section only, clearly marked as `[inferred]`. The user can promote these to fields manually during review.

### ⚪ AMBIGUOUS / CONTRADICTORY
Information was mentioned but is unclear, contradictory, or impossible to confidently categorize.

**Examples:**
- "Budget? Hmm, it depends on what we go with" (no usable data)
- "We need this ASAP" but later "probably looking at H2 rollout" (contradictory)
- Prospect gave a number that might refer to budget or headcount (unclear referent)

**CRM behavior:** EXCLUDED from both fields and notes. Listed in footer: *"The following topics were discussed but could not be confidently captured: [list]. Please review the transcript."*

---

# 2. SUMMARY TEMPLATE

This is the structured output format that the LLM must produce. It serves as both the internal data structure AND the human-readable summary.

```
═══════════════════════════════════════════════
MEMOIR — CALL SUMMARY
═══════════════════════════════════════════════

📞 Call Details
───────────────────────────────────────────────
Date:           [YYYY-MM-DD]
Time:           [HH:MM] - [HH:MM] ([duration] min)
Participants:   [Name, Title, Company] (prospect)
                [Name, Title, Company] (your side)
Call Type:      [Discovery / Demo / Follow-up / Negotiation / Check-in]
CRM Record:     [Company name → HubSpot deal link if available]

═══════════════════════════════════════════════
📊 DISCOVERY FIELDS (→ CRM Custom Fields)
═══════════════════════════════════════════════

Each field shows: Value | Confidence | Source Quote

Budget
  Value:      [$XX,XXX / range / not discussed]
  Confidence: [🟢 definitive / 🟡 indicative / 🔵 inferred]
  Source:     "[Exact quote from transcript]" — [Speaker Name]

Authority / Decision Process
  Decision Maker:    [Name, Title]
  Champions:         [Name(s) who are internal advocates]
  Blockers:          [Name(s) or roles that could block]
  Process:           [Describe approval flow if discussed]
  Confidence:        [🟢 / 🟡 / 🔵]
  Source:            "[Quote]" — [Speaker Name]

Need / Pain Points
  Primary Pain:      [One sentence]
  Secondary Pains:   [List if multiple]
  Current Workaround: [How they solve this today]
  Confidence:        [🟢 / 🟡 / 🔵]
  Source:            "[Quote]" — [Speaker Name]

Timeline
  Target Date:       [Date or timeframe]
  Driving Event:     [What's forcing the timeline, if any]
  Urgency Level:     [Urgent / Active / Exploring / No timeline]
  Confidence:        [🟢 / 🟡 / 🔵]
  Source:            "[Quote]" — [Speaker Name]

Current Tech Stack
  Relevant Tools:    [List of tools/platforms mentioned]
  Pain with Current: [What's not working]
  Integrations Needed: [What they need to connect with]
  Confidence:        [🟢 / 🟡 / 🔵]
  Source:            "[Quote]" — [Speaker Name]

Team / Users
  Team Size:         [Number or range]
  Roles:             [Who would use the product]
  Locations:         [If multi-site/remote]
  Confidence:        [🟢 / 🟡 / 🔵]
  Source:            "[Quote]" — [Speaker Name]

Competition
  Competitors Evaluating:  [List]
  Competitor Sentiment:    [What they like/dislike about each]
  Confidence:              [🟢 / 🟡 / 🔵]
  Source:                  "[Quote]" — [Speaker Name]

═══════════════════════════════════════════════
📝 CALL NARRATIVE
═══════════════════════════════════════════════

[3-5 paragraph narrative summary of the call. Written in past tense,
third person. Focus on what matters for the deal — not a transcript
rehash. Include key moments, objections raised, enthusiasm signals,
and any commitments made by either side.]

═══════════════════════════════════════════════
✅ ACTION ITEMS
═══════════════════════════════════════════════

Your Action Items:
  1. [Action] — by [date if mentioned]
  2. [Action] — by [date if mentioned]

Prospect's Commitments:
  1. [What they committed to do] — [Speaker Name]
  2. [What they committed to do] — [Speaker Name]

═══════════════════════════════════════════════
🔮 NEXT STEPS
═══════════════════════════════════════════════

Next Meeting:    [Date/time if scheduled, or "To be scheduled"]
Next Milestone:  [What needs to happen next in the deal]
Deal Stage:      [Suggested stage: Qualification / Demo / Proposal / Negotiation]

═══════════════════════════════════════════════
⚠️ SIGNALS & FLAGS
═══════════════════════════════════════════════

Buying Signals:
  - [Positive signal] — [Speaker Name]

Risk Signals:
  - [Negative signal or concern] — [Speaker Name]

═══════════════════════════════════════════════
📌 DISCLAIMER
═══════════════════════════════════════════════

The following topics were discussed but could not be confidently
captured. Please review the transcript for:
  - [Topic 1 — brief reason: "contradictory statements about timing"]
  - [Topic 2 — brief reason: "budget mentioned but referent unclear"]

[If applicable:]
⚠️ Confidential remarks were detected and excluded from this summary.

This summary was generated by Memoir AI from a [XX]-minute call
recording. All 🟢 Definitive and 🟡 Indicative fields will be pushed
to HubSpot upon your approval. 🔵 Inferred items appear in notes only.
```

---

# 3. CRM FIELD MAPPING (HubSpot)

## HubSpot Custom Properties to Create

These are the deal-level custom properties Memoir creates/updates:

| Memoir Field | HubSpot Property Name | Property Type | Group |
|---|---|---|---|
| Budget | `memoir_budget` | Currency / String | Memoir Discovery |
| Budget Confidence | `memoir_budget_confidence` | Dropdown (🟢🟡🔵) | Memoir Discovery |
| Decision Maker | `memoir_decision_maker` | Single-line text | Memoir Discovery |
| Decision Process | `memoir_decision_process` | Multi-line text | Memoir Discovery |
| Primary Pain | `memoir_primary_pain` | Multi-line text | Memoir Discovery |
| Timeline | `memoir_timeline` | Single-line text | Memoir Discovery |
| Timeline Urgency | `memoir_urgency` | Dropdown (Urgent/Active/Exploring/None) | Memoir Discovery |
| Current Tools | `memoir_current_stack` | Multi-line text | Memoir Discovery |
| Team Size | `memoir_team_size` | Number / String | Memoir Discovery |
| Competitors | `memoir_competitors` | Multi-line text | Memoir Discovery |
| Deal Stage (suggested) | `memoir_suggested_stage` | Dropdown | Memoir Discovery |
| Last Call Summary | `memoir_last_summary` | Multi-line text | Memoir Activity |
| Last Call Date | `memoir_last_call_date` | Date | Memoir Activity |
| Open Action Items | `memoir_action_items` | Multi-line text | Memoir Activity |
| Memoir Confidence Score | `memoir_overall_confidence` | Number (0-100) | Memoir Activity |

## Push Rules

```
IF confidence == DEFINITIVE:
    → Push to custom field (overwrite if changed from previous call)
    → Add to deal timeline note

IF confidence == INDICATIVE:
    → Push to custom field with "~" prefix (e.g., "~$40-60K")
    → Add to deal timeline note with [indicative] tag

IF confidence == INFERRED:
    → DO NOT push to custom field
    → Add to deal timeline note in "Inferred Insights" section

IF confidence == AMBIGUOUS:
    → DO NOT push anywhere
    → Add to disclaimer footer only

SPECIAL RULES:
    → Never overwrite a DEFINITIVE value with an INDICATIVE one
    → If new call has DEFINITIVE and old call had INDICATIVE for same
      field, upgrade the value
    → If new call contradicts old call at same confidence level,
      flag for human review — do not auto-overwrite
```

---

# 4. EMAIL TEMPLATE

## Spec

**Subject line:** `📞 Call Summary Ready: [Company Name] — [Call Date]`

**From:** Memoir <summaries@memoir.ai>

**Structure:**
1. Hero section with call metadata
2. Discovery fields in a scannable card layout
3. Narrative summary (collapsed/expandable in long form)
4. Action items as a checklist
5. Two CTA buttons: "✅ Approve & Push to CRM" and "✏️ Review & Edit"
6. Disclaimer footer

**Approve button behavior:**
- Clicks → API call → pushes all 🟢 and 🟡 fields to HubSpot
- Shows success confirmation page: "Pushed to [Company] deal in HubSpot ✓"
- Sends a brief confirmation email

**Edit button behavior:**
- Clicks → Opens Memoir web app with this summary pre-loaded
- User can: edit any field, change confidence level, add/remove items
- Then click "Push to CRM" from the web app
- This is where you capture the most valuable training signal — every edit the user makes teaches the LLM what they actually want

## Email HTML (simplified spec — build with your stack)

```
┌─────────────────────────────────────────────────┐
│  MEMOIR                                          │
│                                                  │
│  📞 Call Summary: [Company Name]                 │
│  [Date] · [Duration] min · [Call Type]           │
│  Participants: [Names]                           │
│                                                  │
├─────────────────────────────────────────────────┤
│                                                  │
│  💰 Budget          $50,000 🟢                   │
│  👤 Decision Maker  Sarah Chen, VP Ops 🟢        │
│  🎯 Primary Need    [One line summary] 🟢        │
│  📅 Timeline        Q1 2026 🟡                   │
│  🔧 Current Stack   Salesforce, Outreach 🟢      │
│  👥 Team Size       ~30 users 🟡                 │
│  ⚔️ Competition     Evaluating Gong, Chorus 🟢   │
│                                                  │
│  [Only showing 🟢 and 🟡 — inferred items        │
│   visible in full review]                        │
│                                                  │
├─────────────────────────────────────────────────┤
│                                                  │
│  📝 Summary                                      │
│  [First 2-3 sentences of narrative...]           │
│  [Read more →]                                   │
│                                                  │
├─────────────────────────────────────────────────┤
│                                                  │
│  ✅ Action Items                                  │
│  ☐ Send pricing proposal by Friday               │
│  ☐ Schedule demo with their engineering team      │
│  ☐ Follow up on security questionnaire            │
│                                                  │
│  🤝 They committed to:                            │
│  • Share their requirements doc — Sarah           │
│  • Loop in CTO for next call — Sarah              │
│                                                  │
├─────────────────────────────────────────────────┤
│                                                  │
│  ⚠️ Signals                                      │
│  🟢 "This could be a game-changer for us"        │
│  🔴 "Legal will need to review any contract"      │
│                                                  │
├─────────────────────────────────────────────────┤
│                                                  │
│  ┌──────────────────┐  ┌──────────────────┐     │
│  │ ✅ Approve & Push │  │  ✏️ Review & Edit │     │
│  │    to HubSpot    │  │                  │     │
│  └──────────────────┘  └──────────────────┘     │
│                                                  │
│  Approving will push 🟢 and 🟡 fields to         │
│  [Company Name] deal in HubSpot.                 │
│                                                  │
├─────────────────────────────────────────────────┤
│                                                  │
│  ⚠️ Some topics could not be confidently          │
│  captured: pricing discussion (contradictory      │
│  signals). Review transcript →                   │
│                                                  │
│  ⚠️ Confidential remarks were detected and        │
│  excluded.                                       │
│                                                  │
│  ─────────────────────────────────────────────   │
│  Memoir AI · Manage preferences · Unsubscribe    │
└─────────────────────────────────────────────────┘
```

---

# 5. LLM PROMPT (Production-Ready)

This is the actual system prompt + user prompt structure for the extraction LLM call.

## System Prompt

```
You are Memoir's call analysis engine. Your job is to extract structured
sales intelligence from call transcripts and produce a summary that a
sales professional would find immediately useful for updating their CRM
and preparing for next steps.

## YOUR PRINCIPLES

1. ACCURACY OVER COMPLETENESS. If you're not confident about a data
   point, do NOT fabricate or stretch. Mark it as ambiguous or exclude
   it. A CRM with no data is better than a CRM with wrong data.

2. ATTRIBUTION IS MANDATORY. Every extracted data point must include
   the speaker name and a supporting quote (or paraphrase if the quote
   is very long). This allows the user to verify and builds trust.

3. CONFIDENCE TAGGING. Every discovery field must be tagged:
   - DEFINITIVE: Explicitly stated in clear language
   - INDICATIVE: Strongly implied, reasonable inference from direct
     statements
   - INFERRED: AI interpretation from indirect signals — useful but
     less reliable
   - AMBIGUOUS: Mentioned but unclear, contradictory, or impossible
     to parse — EXCLUDE from fields, list in disclaimer

4. THE "RETURNING SALESPERSON" TEST. Write the summary as if you're
   a senior sales rep who took perfect notes. When the user reads this
   summary 3 weeks from now before their next call, they should
   immediately recall the context, know what was promised, and feel
   prepared. No fluff, no filler, no AI-speak.

5. RESPECT CONFIDENTIALITY. If a speaker uses language suggesting
   off-the-record intent ("between us", "don't quote me", "off the
   record", "this stays here", "I shouldn't be telling you this"),
   EXCLUDE that content from the summary entirely. Add a flag:
   "⚠️ Confidential remarks were detected and excluded from this
   summary."

6. HANDLE MULTIPLE SPEAKERS. Always attribute insights to the specific
   speaker by name. If multiple people from the prospect side speak,
   distinguish their contributions. If you cannot identify who said
   something, attribute it to "Unidentified Speaker (prospect side)."

## OUTPUT FORMAT

You must respond with valid JSON matching the schema provided in the
user message. Do not include any text outside the JSON object.

## WHAT NOT TO DO

- Do NOT invent information that wasn't in the transcript
- Do NOT assign DEFINITIVE confidence to something that was implied
- Do NOT include pleasantries, small talk, or filler in the narrative
- Do NOT use phrases like "The AI detected" or "Based on our analysis"
  — write as if a human took these notes
- Do NOT push inferred insights into the discovery fields — notes only
- Do NOT include content flagged as confidential/off-the-record
- Do NOT include exact lengthy quotes (>30 words) — paraphrase and
  note the timestamp range instead
```

## User Prompt Template

```
Analyze the following sales call transcript and produce a structured
summary.

## CONTEXT

Call Date: {{call_date}}
Call Duration: {{duration_minutes}} minutes
Your Company: {{user_company_name}}
Your Rep: {{user_rep_name}}, {{user_rep_title}}
Prospect Company: {{prospect_company}} (if known, else "Unknown")
Prospect Contact(s): {{prospect_names}} (if known, else "Unknown")
CRM Deal ID: {{hubspot_deal_id}} (if exists, else "New")
Call Type: {{call_type}} — one of [Discovery, Demo, Follow-up,
           Negotiation, Check-in, Cold Call, Inbound]

{{#if previous_summary}}
## PREVIOUS CALL CONTEXT

This is a follow-up call. Here is the summary from the previous
interaction:

"""
{{previous_summary}}
"""

IMPORTANT: Compare new information against previous fields. If a
field was previously DEFINITIVE and new information contradicts it,
flag this as a conflict — do not silently overwrite. If new
information UPGRADES a previous INDICATIVE to DEFINITIVE, note the
upgrade.
{{/if}}

## TRANSCRIPT

"""
{{transcript}}
"""

## REQUIRED OUTPUT

Respond with a JSON object matching this exact schema:

{
  "call_metadata": {
    "date": "YYYY-MM-DD",
    "start_time": "HH:MM",
    "end_time": "HH:MM",
    "duration_minutes": number,
    "call_type": "Discovery|Demo|Follow-up|Negotiation|Check-in",
    "participants": [
      {
        "name": "string",
        "title": "string or null",
        "company": "string",
        "role": "rep|prospect|other"
      }
    ]
  },

  "discovery_fields": {
    "budget": {
      "value": "string or null",
      "confidence": "definitive|indicative|inferred|ambiguous",
      "source_speaker": "string",
      "source_quote": "string (max 100 chars)",
      "source_timestamp_approx": "string or null",
      "changed_from_previous": "boolean (only if follow-up)",
      "conflict_with_previous": "boolean"
    },
    "authority": {
      "decision_maker": "string or null",
      "decision_maker_title": "string or null",
      "champions": ["string"],
      "blockers": ["string"],
      "approval_process": "string or null",
      "confidence": "definitive|indicative|inferred|ambiguous",
      "source_speaker": "string",
      "source_quote": "string",
      "changed_from_previous": "boolean",
      "conflict_with_previous": "boolean"
    },
    "need": {
      "primary_pain": "string or null",
      "secondary_pains": ["string"],
      "current_workaround": "string or null",
      "confidence": "definitive|indicative|inferred|ambiguous",
      "source_speaker": "string",
      "source_quote": "string",
      "changed_from_previous": "boolean",
      "conflict_with_previous": "boolean"
    },
    "timeline": {
      "target_date": "string or null",
      "driving_event": "string or null",
      "urgency": "urgent|active|exploring|none",
      "confidence": "definitive|indicative|inferred|ambiguous",
      "source_speaker": "string",
      "source_quote": "string",
      "changed_from_previous": "boolean",
      "conflict_with_previous": "boolean"
    },
    "tech_stack": {
      "current_tools": ["string"],
      "pain_with_current": "string or null",
      "integrations_needed": ["string"],
      "confidence": "definitive|indicative|inferred|ambiguous",
      "source_speaker": "string",
      "source_quote": "string",
      "changed_from_previous": "boolean",
      "conflict_with_previous": "boolean"
    },
    "team": {
      "size": "string or null",
      "roles": ["string"],
      "locations": "string or null",
      "confidence": "definitive|indicative|inferred|ambiguous",
      "source_speaker": "string",
      "source_quote": "string",
      "changed_from_previous": "boolean",
      "conflict_with_previous": "boolean"
    },
    "competition": {
      "competitors_evaluating": ["string"],
      "competitor_sentiment": [
        {
          "competitor": "string",
          "sentiment": "string (what they like/dislike)"
        }
      ],
      "confidence": "definitive|indicative|inferred|ambiguous",
      "source_speaker": "string",
      "source_quote": "string",
      "changed_from_previous": "boolean",
      "conflict_with_previous": "boolean"
    }
  },

  "narrative_summary": "string (3-5 paragraphs, past tense, third
    person, focused on deal-relevant information)",

  "action_items": {
    "rep_actions": [
      {
        "action": "string",
        "due_date": "string or null",
        "priority": "high|medium|low"
      }
    ],
    "prospect_commitments": [
      {
        "action": "string",
        "owner": "string (name)",
        "due_date": "string or null"
      }
    ]
  },

  "next_steps": {
    "next_meeting": "string or null",
    "next_milestone": "string",
    "suggested_deal_stage": "Qualification|Demo|Proposal|Negotiation|
      Closed Won|Closed Lost"
  },

  "signals": {
    "buying_signals": [
      {
        "signal": "string",
        "speaker": "string"
      }
    ],
    "risk_signals": [
      {
        "signal": "string",
        "speaker": "string"
      }
    ]
  },

  "disclaimer": {
    "ambiguous_topics": [
      {
        "topic": "string",
        "reason": "string"
      }
    ],
    "confidential_content_detected": "boolean",
    "transcript_quality_issues": "string or null"
  },

  "overall_confidence_score": "number 0-100 (weighted average of
    field confidences, where definitive=100, indicative=70,
    inferred=40, ambiguous=0)"
}

## RULES FOR EDGE CASES

1. If a discovery field was not discussed at all, set value to null
   and confidence to "ambiguous" with source_quote:
   "Not discussed in this call"

2. If the call is mostly small talk with minimal discovery content,
   still extract what you can. Set overall_confidence_score low and
   note in narrative that this was a relationship-building call with
   limited discovery.

3. If multiple people from the prospect side give different answers
   to the same question, capture the MOST SENIOR person's answer in
   the field and note the discrepancy in the narrative.

4. If the prospect corrects themselves mid-sentence, use the
   CORRECTED version. Do not include the original mistake.

5. For budget ranges ("somewhere between 30 and 50K"), capture the
   range as-is. Do not average or pick a midpoint.

6. If the rep is the one stating a number (e.g., "Our pricing starts
   at $40K"), this is NOT the prospect's budget. Only capture budget
   from what the PROSPECT says about their spending capacity.

7. If the transcript has quality issues (garbled audio, [inaudible]
   markers, speaker identification failures), note this in
   disclaimer.transcript_quality_issues and lower the confidence
   of any affected fields.

8. For follow-up calls: if a previously DEFINITIVE field now has new
   contradictory DEFINITIVE information, set conflict_with_previous
   to true and include BOTH values in the source_quote field like:
   "Previously: $50K. Now: $30K — [quote]"
```

---

# 6. EDGE CASES & HANDLING RULES

## Comprehensive Edge Case Matrix

| # | Edge Case | Detection Method | Handling |
|---|-----------|-----------------|----------|
| 1 | **Almost no discovery content** — call is 90% small talk, pleasantries, or off-topic | Count discovery-relevant sentences vs total. If <15%, flag. | Produce summary with low confidence score. Narrative explains: "This was primarily a relationship-building call. Limited discovery data was captured." Still extract any fragments found. |
| 2 | **Prospect says "don't quote me" / "off the record"** | Keyword detection: "off the record", "between us", "don't put this in writing", "this stays here", "I shouldn't tell you this", "confidentially" | Exclude ALL content from that passage until topic clearly changes. Set `confidential_content_detected: true`. Add disclaimer flag. Do NOT even hint at the content. |
| 3 | **Contradictory statements** — "We need this ASAP" then later "probably H2" | Same field gets conflicting values at same confidence level | Set confidence to AMBIGUOUS. Include both statements in source_quote. Add to disclaimer: "Timeline: contradictory statements detected." Let user resolve. |
| 4 | **Multiple prospect speakers disagree** | Two prospect participants give different answers to same question | Capture senior person's answer in field. Note discrepancy in narrative: "[Junior] mentioned X, but [Senior] indicated Y." |
| 5 | **Rep states pricing, not prospect stating budget** | Speaker role = "rep" AND content relates to pricing/cost | Do NOT capture as prospect's budget. Note in narrative: "Rep presented pricing at $X." Only capture budget from prospect's statements about their capacity/willingness to spend. |
| 6 | **Prospect mentions budget for a different project** | Budget figure mentioned in context of unrelated initiative | Do NOT capture as deal budget. Note in narrative if potentially relevant: "Prospect mentioned $X budget for their [other project], which may indicate overall spending capacity." Mark as INFERRED if included at all. |
| 7 | **Very poor transcript quality** | >10% of content marked [inaudible] or garbled | Set `transcript_quality_issues` with description. Lower ALL confidence levels by one tier (definitive → indicative, indicative → inferred). Add disclaimer. |
| 8 | **Call is a voicemail, not a conversation** | Only one speaker detected, duration <3 min | Produce minimal summary: "Voicemail left by [name]. Content: [brief description]. No discovery data extracted." Set all fields to null. |
| 9 | **Wrong call recorded** — clearly not a sales call | Content is internal meeting, personal call, etc. | Return error state: "This does not appear to be a sales call. Content appears to be [internal meeting / personal call / etc.]. No summary generated." |
| 10 | **Non-English content** | Language detection on transcript | If fully non-English: attempt extraction in that language, output summary in English. Flag: "Original call in [language]. Summary translated — verify key terms." If mixed: note code-switching, extract from both. |
| 11 | **Prospect gives a number without clear context** | "About fifty" — could be budget ($50K), team size (50 people), or timeline (50 days) | Do NOT assign to any field. Add to ambiguous topics: "Numeric reference '50' — unclear if referring to budget, team size, or another metric." |
| 12 | **Same person has different titles across calls** | Follow-up threading detects title change | Note the change: "Previously identified as [old title], now referred to as [new title]." Update the field. This could indicate a promotion, correction, or role change. |
| 13 | **Call drops and resumes** | Gap in transcript, or two transcript segments | Merge segments. Note: "Call experienced a disconnection. Summary covers both segments." Check for any data that might have been discussed during the gap. |
| 14 | **Demo call with minimal discovery** | Call type = Demo AND discovery field extraction is sparse | Expected behavior for demos. Focus narrative on: features shown, reactions, questions asked, objections raised. Lower expectation for discovery fields — many will be null. |
| 15 | **Prospect explicitly declines to share info** | "I can't share our budget at this stage" / "I'd rather not say" | Set field value to "Declined to share" with confidence DEFINITIVE (we definitively know they declined). This is different from "not discussed" — it's a signal in itself. |

---

# 7. FOLLOW-UP CALL THREADING LOGIC

## How Calls Get Associated

```
Call Association Rules (in priority order):

1. SAME HUBSPOT DEAL
   If the user has a HubSpot deal open and starts a call, auto-
   associate with that deal. Thread with all previous calls on
   that deal.

2. SAME PROSPECT EMAIL/PHONE
   If the prospect's email or phone matches a previous call's
   participant, associate with the same thread even if no deal
   exists yet.

3. SAME COMPANY DOMAIN
   If participant email domain matches a previous call, suggest
   association: "This appears to be related to your conversations
   with [Company]. Link this call? [Yes / No / New thread]"

4. MANUAL ASSOCIATION
   User can always manually link/unlink calls in the Memoir
   dashboard.
```

## Threading Behavior in Summaries

```
FIRST CALL IN THREAD:
  → Standard summary, all fields fresh
  → No "changed_from_previous" flags
  → Full discovery extraction

FOLLOW-UP CALL (2nd, 3rd, etc.):
  → Previous summary injected into LLM context
  → Each field compared against previous values:

     UNCHANGED:  "Budget: $50K 🟢 [unchanged from previous call]"
     UPGRADED:   "Budget: $50K 🟢 [upgraded from 🟡 ~$40-60K]"
     CHANGED:    "Budget: $30K 🟢 [changed from $50K — see note]"
     CONFLICTED: "Budget: ⚠️ CONFLICT — Previous: $50K 🟢, Now:
                  $30K 🟢. Flagged for review."
     NEW:        "Timeline: Q1 2026 🟡 [new — not previously
                  discussed]"
     NOT UPDATED: "Tech Stack: Salesforce, Outreach 🟢 [from
                   previous call — not discussed in this call]"

  → CRITICAL RULE: Never silently overwrite a DEFINITIVE value.
    Always flag changes for human review when both old and new are
    DEFINITIVE.

  → Previous call's action items are checked:
    "Previous action items status:
     ✅ Send pricing proposal — [completed/referenced in this call]
     ⏳ Schedule demo with engineering — [not mentioned]
     ❌ Follow up on security questionnaire — [prospect said they
        haven't received it]"
```

## Cumulative Deal Intelligence

After 3+ calls, Memoir should maintain a rolling "Deal Brief" that consolidates all calls:

```
DEAL BRIEF: [Company Name]
━━━━━━━━━━━━━━━━━━━━━━━━━
Call History: 4 calls over 6 weeks
Last Contact: [date]

CONSOLIDATED DISCOVERY (latest confident values):
Budget:       $50K 🟢 (confirmed call #3)
Decision:     Sarah Chen, VP Ops 🟢 (call #1, reconfirmed #3)
Need:         [evolved across calls — see progression]
Timeline:     Q1 2026 🟡 (call #3, shifted from "ASAP" in call #1)
Stack:        Salesforce + Outreach + Notion 🟢 (built across calls)
Team:         30 users 🟡 (call #2)
Competition:  Gong (negative), Chorus (neutral) 🟢 (call #2)

DEAL TRAJECTORY:
Call #1 (Discovery): High enthusiasm, broad pain discussion
Call #2 (Demo): Strong engagement, asked detailed questions
Call #3 (Follow-up): Budget confirmed, timeline shifted right
Call #4 (Negotiation): Pricing discussed, legal review needed

OPEN RISKS:
- Timeline slipped from "ASAP" to "Q1 2026"
- Legal review could add 2-4 weeks
- CTO hasn't been involved yet

OVERALL DEAL HEALTH: 72/100
```

---

# IMPLEMENTATION NOTES

## Processing Pipeline Order

```
1. Audio → Transcription (Deepgram/AssemblyAI)
      ↓
2. Speaker diarization + identification
      ↓
3. Transcript cleanup (remove filler words, fix obvious
   transcription errors using context)
      ↓
4. Fetch previous summary if follow-up call (from DB)
      ↓
5. LLM call with system prompt + user prompt + transcript
      ↓
6. JSON response validation (schema check)
      ↓
7. Confidence scoring + push rule application
      ↓
8. Format into email template
      ↓
9. Send email to user
      ↓
10. On "Approve": HubSpot API calls to update deal properties
    On "Edit": Redirect to web app with pre-loaded summary
      ↓
11. Store summary, transcript, and user edits in DB
    (User edits become training signal for fine-tuning)
```

## Cost Estimation Per Call

```
30-minute call:
  - Transcription (Deepgram): ~$0.25-0.50
  - LLM extraction (Claude Sonnet/GPT-4o): ~$0.05-0.15
  - Email delivery: ~$0.001
  - Total: ~$0.30-0.65 per call

At $29/mo with ~40 calls/month: $0.65 × 40 = $26 cost
Margin: ~10% at 40 calls — need to optimize or tier at 30 calls

At $29/mo with ~20 calls/month: $0.65 × 20 = $13 cost
Margin: ~55% — much healthier

RECOMMENDATION: Free tier at 5 calls/mo, Pro at $29 for 30 calls,
use Deepgram Nova-2 ($0.0043/min) not Whisper for cost efficiency.
Batch LLM calls where possible.
```
