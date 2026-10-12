# Phase 7 Research: The "Gong" Intelligence Layer

## 1. Executive Summary

Phase 7 transforms our transcription platform from a "recording tool" into a "revenue intelligence platform." Right now, we produce accurate transcriptions with speaker labels, timestamps, and translations. Phase 7 adds a brain on top -- an LLM that reads these transcriptions and extracts structured business intelligence: who are the competitors mentioned, what objections did the customer raise, what are the agreed next steps, was budget discussed, and dozens more data points. This extracted intelligence then gets synced to Zoho CRM automatically, eliminating manual data entry.

Think of it this way: today, a sales manager has to listen to (or read) every call transcript to understand what happened. With Phase 7, the system produces a structured JSON report for every call -- like a standardized "call intelligence card" -- that can be searched, filtered, aggregated, and pushed into CRM records. This is exactly what Gong ($7.2B valuation), Chorus (acquired by ZoomInfo for $575M), and Fireflies ($1B unicorn as of June 2025) do, except we are building it self-hosted with open-source LLMs instead of proprietary cloud APIs.

The core technical approach is: take the structured transcript (Speaker + Text + Timestamp), feed it to a self-hosted LLM (Qwen 2.5) with a carefully engineered prompt and JSON schema, and get back structured intelligence — including per-segment sentiment analysis in the same extraction pass (no separate sentiment model needed; see Section 5 for rationale). For talk ratios and coaching metrics, we compute directly from the diarization data — no LLM needed. The extracted data maps to Zoho CRM via their REST API v6/v8 with OAuth 2.0 authentication.

**Key architectural decisions (updated Feb 2026):**
- **Sentiment**: LLM-integrated (in extraction pass), NOT a separate RoBERTa model. Competitors use proprietary models trained on millions of calls; no off-the-shelf model fits sales conversation domain. LLM approach gives sales-context awareness via prompt engineering.
- **Model config**: Separate config fields for Phase 5 (vocabulary correction) and Phase 7 (intelligence extraction). Same Ollama/vLLM base URL, different model sizes (7B for Phase 5, 14B for Phase 7 in production).
- **Microservice-ready**: Post-processing (Phases 5-7) designed for future extraction into a separate microservice (Phase 9). All modules are self-contained with zero audio dependencies.

---

## 2. What Competitors Do

### 2.1 Competitor Feature Comparison Table

| Feature | Gong | Chorus (ZoomInfo) | Fireflies.ai | Clari Copilot | Avoma | Jiminny | Zoom Revenue Accelerator | Revenue.io | SalesLoft |
|---------|------|-------------------|--------------|---------------|-------|---------|--------------------------|------------|-----------|
| **Transcription** | Yes (85-90% accuracy) | Yes | Yes (95%+ optimal) | Yes (real-time) | Yes | Yes | Yes | Yes | Yes |
| **Speaker identification** | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| **Competitor mentions** | Yes (trackers) | Yes | Yes (topics) | Yes (battle cards) | Yes (keyword tracking) | Yes | Yes | Yes | Yes |
| **Objection detection** | Yes | Yes | Yes | Yes (real-time) | Yes | Yes | Partial | Yes (Moments) | Yes |
| **Next steps / action items** | Yes (AI extracted) | Yes | Yes (auto-generated) | Yes | Yes | Yes | Yes (auto-suggest) | Yes | Yes |
| **Talk ratio analysis** | Yes | Yes (talk-to-listen) | Yes (talk-time breakdown) | Yes | Yes (talk patterns) | Yes | Yes | Yes | Yes |
| **Sentiment analysis** | Yes | Yes | Yes | Partial | Partial | Partial | Partial | Yes | Yes (Influence Graph) |
| **Deal health scoring** | Yes (Deal Boards) | Yes (deal progression) | No | Yes (deal inspection) | Partial | Yes (pipeline risks) | Partial | Partial | Yes (Rhythm AI) |
| **Coaching scorecards** | Yes | Yes | Partial | Yes (real-time battle cards) | Yes (MEDDIC/BANT/SPIN templates) | Yes (automated scoring) | Partial | Yes (coaching modules) | Yes (Sales Strategist Agent) |
| **Methodology scoring** | Yes (custom) | Yes | No | Yes | Yes (MEDDIC, SPIN, BANT pre-built) | Yes | No | Partial | Yes |
| **CRM auto-population** | Yes (AI Data Extractor) | Yes (Salesforce, HubSpot) | Partial | Yes | Yes | Yes (Salesforce, HubSpot) | Yes (Salesforce, HubSpot, Dynamics) | Yes (Salesforce native) | Yes (Salesforce native) |
| **Custom AI fields** | Yes (up to 20 per workspace) | Limited | Yes (custom AI apps) | Limited | Yes | Limited | Limited | Limited | Limited |
| **Real-time coaching** | Partial (new feature) | No | No | Yes (core feature) | No | Yes (incognito coaching) | No | Yes (Moments real-time) | Yes (Sales Strategist) |
| **Filler word detection** | Yes | Yes | Yes | Yes | Yes | Partial | Partial | Yes | Partial |
| **Monologue detection** | Yes | Yes | Yes | Yes | Yes | Partial | Partial | Yes | Partial |
| **Email/calendar integration** | Yes | Yes | Yes | Yes | Yes | Yes | Yes (Zoom native) | Yes | Yes |
| **Revenue forecasting** | Yes (Forecast add-on) | Yes | No | Yes (Clari core) | Partial | Partial | Partial | Partial | Yes (Clari merger) |
| **Multi-language** | Limited | Limited | Yes (69+ languages) | Limited | Limited | Limited | Limited | Limited | Limited |

### 2.2 Detailed Competitor Analysis

#### Gong.io -- The Market Leader

**What they do:** Gong is the most feature-rich platform, positioning itself as a "Revenue AI Operating System." Their core value proposition is turning unstructured sales conversations into structured, actionable data.

**Key intelligence features:**
- **AI Data Extractor (launched 2025):** An AI agent that automatically extracts custom data points from calls and populates CRM fields. Admins define "AI fields" as questions (e.g., "What competitor was mentioned?", "What is the customer's timeline?"), and Gong's AI answers them from conversation data. Supports text, numeric, boolean, and picklist data types. Up to 20 AI fields per workspace.
- **Deal Boards:** Real-time pipeline view where each deal is scored for "health" based on conversation patterns. Flags deals with no contact in 10+ days, missing decision-maker engagement, or negative sentiment trends.
- **Trackers:** Keyword/phrase tracking across all calls. The API returns a `content.trackers` array where each competitor is an object with `count` and `phrase` fields.
- **Interaction Stats:** Per-call statistics including talk ratio, interactivity score, patience score, longest monologue.
- **Database schema:** `CALL_TRANSCRIPTS` (JSON with speaker + text + timestamp), `CONVERSATION_TRACKERS` (keyword matches), `INTERACTION_STATS` (talk metrics).

**Pricing:** $1,600/user/year for Foundation + $50,000/year platform fee (as of March 2025 restructuring). Add-ons: Forecast ($700/user/year), Engage ($800/user/year). Implementation: $15K-$65K. Effectively $2,500-$3,000/user/year all-in for a 50-person team.

**AI models:** Proprietary. They do not disclose specific models but have built custom NLP pipelines. Likely fine-tuned transformer models for specific tasks (tracker detection, sentiment, deal scoring).

#### Chorus.ai (ZoomInfo) -- Data-Enriched Intelligence

**What they do:** Chorus combines conversation intelligence with ZoomInfo's massive B2B contact database. Their unique value is surfacing insights about deal participants and buying committees.

**Key intelligence features:**
- Talk-to-listen ratios, competitor mentions, objection handling metrics
- Deal progression tracking with risk signals
- Buying committee identification (enriched by ZoomInfo data)
- Stakeholder engagement levels per deal
- Triggered campaigns based on keyword mentions in calls

**Pricing:** Enterprise-focused, quote-based. Estimated $100-$200/user/month. ZoomInfo bundles often required.

#### Fireflies.ai -- Democratized Intelligence

**What they do:** Fireflies focuses on making conversation intelligence accessible to SMBs with affordable pricing and a broad feature set.

**Key intelligence features:**
- **AskFred:** GPT-powered conversational AI that answers natural language questions across all meeting transcripts
- Automatic meeting summaries with overview, key topics, decisions, action items
- Sentiment analysis per segment
- Talk-time breakdowns and topic detection
- Smart Search across action items, dates, tasks, topics, sentiments
- Custom AI Apps (build your own extraction)

**Pricing:** Free tier available. Pro: $10/user/month (annual). Business: $19/user/month (annual). Enterprise: $39/user/month. Dramatically cheaper than Gong.

**Scale:** 20M+ users, 500K+ organizations, 75% of Fortune 500. Reached $1B unicorn valuation in June 2025.

#### Clari Copilot -- Real-Time Coaching Champion

**What they do:** Clari Copilot (formerly Wingman) differentiates with real-time, in-call coaching. While others analyze calls after they end, Copilot provides live guidance.

**Key intelligence features:**
- **Real-time battle cards:** During a live call, when a competitor is mentioned, the system surfaces a battle card with competitive positioning, objection handling scripts, and key differentiators. Sellers using battle cards see 10% higher win rates.
- **Playbooks:** Enforce call structure consistency (e.g., MEDDIC methodology steps)
- **Gametapes:** Library of winning call moments for onboarding

**Pricing:** Enterprise-focused. Clari merged with SalesLoft in December 2025 to form a combined revenue AI platform.

#### Avoma -- Methodology Scoring Leader

**What they do:** Avoma stands out with pre-built coaching scorecards based on popular sales methodologies.

**Key intelligence features:**
- **AI Scorecards:** Pre-built templates for MEDDIC, SPIN, BANT, plus custom scorecards. AI auto-scores every call and provides justifications for each score.
- Three default scorecard templates: SDR Prospecting Call, Sales Demo Call, Customer Success Call
- Topic intelligence with time-spent-per-topic analysis
- Automatic meeting transcription, speaker identification, keyword tracking

**Pricing:** More affordable than Gong/Chorus. ~$19/user/month for core features. Built for SMBs.

#### Other Notable Platforms

- **Jiminny:** $85-$100/user/month. Real-time incognito coaching (managers can join calls invisibly and send private messages). Pipeline risk monitoring with automated alerts.
- **Zoom Revenue Accelerator:** Native to Zoom. Conversation Explorer for AI Q&A across transcripts. Syncs with Salesforce, HubSpot, Dynamics. Added agentic AI in 2025 for auto-follow-ups and CRM updates.
- **Revenue.io (formerly RingDNA):** Moments(TM) real-time in-call coaching. Captures keywords, sentiment, objections, and follow-up actions. Native Salesforce integration.
- **SalesLoft:** Merged with Clari in December 2025. Rhythm AI layer prioritizes seller actions. Influence Graph maps stakeholders with sentiment and influence scores. Sales Strategist Agent provides integrated coaching insights.

### 2.3 Key Takeaways for Our Platform

1. **Table stakes features** (must-have): Competitor mentions, next steps, talk ratio, call summary, action items, CRM sync
2. **Differentiators** (nice-to-have): Methodology scoring (MEDDIC/BANT), deal health scoring, coaching scorecards
3. **Our unique advantages**: Self-hosted (data stays on-premise), multilingual (English + Tamil/Hindi), Zoho CRM native (most competitors focus on Salesforce/HubSpot), dramatically lower cost
4. **What competitors charge** for what we can build: $1,600-$3,000/user/year vs our self-hosted approach at infrastructure cost only

---

## 3. Intelligence Extraction Categories

### 3.1 What to Extract (with Examples)

Below is a comprehensive list of data points that can be extracted from sales call transcripts, organized by category. Each includes an example of what it looks like in a real call.

#### Category 1: Competitor Intelligence

| Data Point | Description | Example from Transcript |
|------------|-------------|------------------------|
| Competitors mentioned | Names of competing products/services | "We've also been evaluating Salesforce and HubSpot for this." |
| Competitor strengths cited | What the prospect likes about competitors | "Salesforce has a more mature reporting module." |
| Competitor weaknesses cited | Problems the prospect has with competitors | "But HubSpot's pricing is getting out of hand for our team size." |
| Competitive positioning | How the rep differentiates against competitors | "Unlike Salesforce, we don't charge per-seat for reporting." |

**Implementation:** Maintain a configurable competitor list. LLM matches mentions (including abbreviations, nicknames, and indirect references like "your competitor" or "the other tool we're looking at"). Also detect previously unknown competitors for list expansion.

#### Category 2: Pain Points & Challenges

| Data Point | Description | Example |
|------------|-------------|---------|
| Customer pain points | Problems the customer is trying to solve | "Our current system takes 3 days to generate a simple report." |
| Business impact | Quantified impact of the pain | "We're losing about 20 hours per week on manual data entry." |
| Current solution gaps | What their existing solution lacks | "The integration with our ERP is completely broken." |

#### Category 3: Objections

| Data Point | Description | Example |
|------------|-------------|---------|
| Pricing objections | Concerns about cost | "That's significantly more than what we budgeted for Q2." |
| Feature gap objections | Missing capabilities | "But you don't support SSO, and that's a hard requirement for us." |
| Timing objections | Not the right time | "We're in the middle of a migration, so maybe next quarter." |
| Authority objections | Decision-making concerns | "I'd need to run this by our VP of Engineering first." |
| Trust/risk objections | Concerns about vendor reliability | "You're a relatively new company. What if you shut down?" |
| Status quo objections | Resistance to change | "Honestly, what we have works well enough." |

#### Category 4: Next Steps & Action Items

| Data Point | Description | Example |
|------------|-------------|---------|
| Agreed next steps | What both parties committed to | "Let's schedule a technical demo with your engineering team next Tuesday." |
| Rep action items | What the rep promised to do | "I'll send over the security whitepaper and SOC 2 report by Friday." |
| Customer action items | What the customer promised to do | "I'll get the team's feedback and circle back next week." |
| Follow-up date | When the next interaction is planned | "Let's reconnect on March 15th." |
| Meeting scheduled | Whether a specific meeting was booked | "Great, I'll send the calendar invite for Thursday at 2 PM." |

#### Category 5: Budget & Pricing

| Data Point | Description | Example |
|------------|-------------|---------|
| Budget discussed | Whether budget/pricing was brought up | Yes (binary) |
| Budget range mentioned | Specific numbers or ranges | "We have about $50K allocated for this initiative." |
| Pricing presented | Whether the rep shared pricing | "Our enterprise plan is $200 per user per month." |
| Discount requested | Whether the customer asked for a discount | "Is there any flexibility on the per-seat pricing?" |
| ROI discussed | Whether return on investment was covered | "Based on the 20 hours saved per week, this pays for itself in 3 months." |

#### Category 6: Decision-Making

| Data Point | Description | Example |
|------------|-------------|---------|
| Decision makers identified | People with buying authority | "Our CTO, Rajesh, makes the final call on all software purchases." |
| Buying committee members | All stakeholders involved | "I'll need sign-off from Legal, IT Security, and our CFO." |
| Decision process | How the decision will be made | "We do a formal evaluation with a scoring matrix." |
| Decision timeline | When the decision will be made | "We're hoping to have a vendor selected by end of Q1." |
| Champion identified | Internal advocate for the product | "I'm a big fan of your approach -- let me champion this internally." |

#### Category 7: Deal Stage Indicators

| Data Point | Description | Example |
|------------|-------------|---------|
| Discovery signals | Early-stage exploration | "We're just starting to look at options in this space." |
| Evaluation signals | Active comparison | "We've narrowed it down to three vendors including you." |
| Negotiation signals | Terms discussion | "If we commit to a 2-year contract, what kind of discount can you offer?" |
| Closing signals | Ready to buy | "Can you send over the contract so we can get Legal to review it?" |
| Risk signals | Deal may be in trouble | "I'm not sure this is a priority for us right now." |

#### Category 8: Coaching & Communication Metrics

These are computed directly from the diarization data and transcript -- no LLM needed.

| Metric | Description | Benchmark |
|--------|-------------|-----------|
| Talk ratio | Rep speaking time vs customer speaking time | Ideal: 43% rep / 57% customer |
| Longest monologue | Longest uninterrupted speech by rep | Target: < 2 min 15 sec |
| Filler word count | "um", "uh", "like", "you know", "basically" | Lower is better (though Gong research shows no correlation with outcomes) |
| Question count | Number of questions asked by rep | More questions = better discovery |
| Interruption count | Times one speaker cut off another | Fewer interruptions = better listening |
| Response time | Average pause before responding | Shows active listening vs rushing |
| Silence duration | Total silence in the call | Some silence is healthy (thinking time) |
| Call duration | Total call length | Varies by call type |

**Important research finding on filler words:** Gong Labs analyzed thousands of calls and found "no statistically significant difference in the frequency or types of filler words used" between top, mid, and low performers. Filler words do not correlate with sales outcomes. Track them for coaching awareness, but do not use them in deal scoring.

**Monologue detection:** A monologue is typically defined as 75+ consecutive words (~40 seconds) by one speaker. Calls with 100+ word monologues tend not to lead to sales. The ideal longest monologue is ~2 minutes 15 seconds.

#### Category 9: Sentiment & Emotion

| Data Point | Description | Example |
|------------|-------------|---------|
| Segment sentiment | Positive/negative/neutral per segment | "That's exactly what we need!" (positive) |
| Overall call sentiment | Aggregate sentiment trajectory | Started neutral, became positive after demo |
| Customer sentiment | Sentiment from customer segments only | Net positive with one negative spike on pricing |
| Rep sentiment | Sentiment from rep segments only | Consistently positive and professional |
| Sentiment shifts | Points where sentiment changed significantly | Negative shift at timestamp 15:30 when pricing discussed |

#### Category 10: Product & Feature Discussion

| Data Point | Description | Example |
|------------|-------------|---------|
| Features discussed | Product features/capabilities mentioned | "The API integration, reporting dashboard, and SSO" |
| Feature requests | Features the customer wishes existed | "It would be great if you had a mobile app." |
| Use cases described | How the customer plans to use the product | "Primarily for our sales team's weekly pipeline reviews." |
| Technical requirements | Specific technical needs | "We need on-premise deployment with LDAP integration." |

### 3.2 Methodology Scoring (MEDDIC, BANT, SPIN)

Automated methodology scoring is a high-value feature that competitors charge premium prices for. Here is how each methodology maps to extractable data:

**MEDDIC:**
- **M**etrics: Was quantifiable business impact discussed? ("saves 20 hours/week")
- **E**conomic Buyer: Was the person with budget authority identified? ("our CFO approves")
- **D**ecision Criteria: Were evaluation criteria discussed? ("we need SOC 2 compliance")
- **D**ecision Process: Was the buying process mapped? ("we do formal vendor evaluation")
- **I**dentify Pain: Were specific pain points articulated? ("manual reporting takes 3 days")
- **C**hampion: Was an internal advocate identified? ("let me push this internally")

**BANT:**
- **B**udget: Was budget discussed? Amount? ("we have $50K allocated")
- **A**uthority: Was the decision-maker identified? ("CTO makes the call")
- **N**eed: Was a clear need articulated? ("we need automated reporting")
- **T**imeline: Was a timeline established? ("want to decide by end of Q1")

AI auto-scoring achieves 94% completion rates vs 15-30% when reps manually fill in CRM scorecards.

---

## 4. LLM Extraction Approaches

### 4.1 The Core Problem

A sales call transcript might be 5,000-20,000 words (30-60 minutes of audio). We need to extract 20-40 structured data points from this unstructured text. The LLM must:
1. Read the full transcript (or chunks of it)
2. Understand speaker roles (rep vs customer)
3. Identify relevant passages for each data point
4. Return a valid JSON object matching our schema
5. Handle cases where information is not present (return null, not hallucinate)

### 4.2 Prompting Strategies

#### System Prompt Design

The system prompt establishes the LLM's role, defines the extraction schema, and sets ground rules. A well-engineered system prompt is the single most important factor for extraction quality.

**Key principles:**
- **Role definition:** "You are a sales call analyst extracting structured intelligence from transcripts."
- **Schema definition:** Provide the exact JSON schema with field descriptions, types, and examples.
- **Ground rules:** "Only extract information explicitly stated in the transcript. If a data point is not discussed, return null. Never infer or fabricate information."
- **Speaker awareness:** "Segments prefixed with 'Rep:' are from the sales representative. Segments prefixed with 'Customer:' are from the prospect/customer."
- **Evidence requirement:** "For each extracted data point, include the relevant quote from the transcript."

#### Few-Shot Examples

Providing 2-3 complete input/output examples dramatically improves extraction quality and consistency. Each example should show:
- A realistic transcript snippet
- The expected JSON output
- Handling of missing data (null values)

#### Chain-of-Thought (CoT) Reasoning

For complex extractions (deal stage classification, sentiment trajectory), ask the LLM to "think step by step" before producing the final answer. This reduces errors by forcing the model to reason through the evidence.

**Example prompt pattern:**
```
First, identify all passages where competitors are mentioned.
Then, for each competitor, determine:
  1. The competitor name
  2. Whether it was mentioned positively, negatively, or neutrally
  3. The exact quote
Finally, output your answer as JSON.
```

### 4.3 Structured Output Enforcement

The critical challenge is ensuring the LLM returns valid JSON that matches our schema exactly. Several approaches exist:

#### Approach 1: JSON Mode (API-Level)

Both OpenAI and Anthropic APIs have a "JSON mode" that constrains the model to output valid JSON. For self-hosted models, vLLM provides `guided_json` parameter that uses constrained decoding (xgrammar backend) to enforce JSON schema compliance at the token level.

**How constrained decoding works (for beginners):** Normally, an LLM picks the most likely next word from its entire vocabulary. Constrained decoding restricts which words the model can pick at each step. For example, after outputting `{"competitors": [`, the model can only output `{` (to start an object) or `]` (to close the array). This guarantees valid JSON structure without post-processing.

**vLLM example:**
```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="dummy")

response = client.chat.completions.create(
    model="Qwen/Qwen2.5-14B-Instruct",
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": transcript}
    ],
    extra_body={
        "guided_json": json_schema  # Enforces schema compliance
    }
)
```

#### Approach 2: Function Calling

Define the extraction schema as a "function" and let the model "call" it. Both Qwen 2.5 and Llama 3.3 support function calling via the Hermes tool-calling format.

**vLLM function calling:**
```bash
vllm serve Qwen/Qwen2.5-14B-Instruct \
  --enable-auto-tool-choice \
  --tool-call-parser hermes
```

#### Approach 3: Pydantic Validation (Post-Processing)

Define a Pydantic model for the schema, let the LLM generate JSON freely, then validate and coerce it. This is the simplest approach but requires retry logic for invalid output.

```python
from pydantic import BaseModel, Field
from typing import Optional, List

class CompetitorMention(BaseModel):
    name: str = Field(description="Competitor company or product name")
    sentiment: str = Field(description="positive, negative, or neutral")
    quote: str = Field(description="Exact quote mentioning this competitor")

class CallIntelligence(BaseModel):
    competitors: List[CompetitorMention] = Field(default_factory=list)
    pain_points: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)
    budget_discussed: bool = Field(default=False)
    # ... more fields
```

**Recommendation:** Use vLLM with `guided_json` (Approach 1) as the primary method. It guarantees valid JSON with zero retries. Fall back to Pydantic validation (Approach 3) for models served via Ollama, which has more limited constrained decoding support.

### 4.4 Self-Hosted Model Comparison

#### Model Size vs Quality vs Speed Trade-off

| Model | Parameters | VRAM (Q4) | VRAM (FP16) | JSON Quality | Speed (tok/s on A6000) | Notes |
|-------|-----------|-----------|-------------|--------------|----------------------|-------|
| **Qwen 2.5 7B Instruct** | 7B | ~5-6 GB | ~17 GB | Good | ~80 | Best JSON output in its class. 94% extraction accuracy. |
| **Qwen 2.5 14B Instruct** | 14B | ~10-12 GB | ~30 GB | Very Good | ~50 | Sweet spot. Outperforms competitors at similar size. |
| **Qwen 2.5 32B Instruct** | 32B | ~20-24 GB | ~65 GB | Excellent | ~25 | Fits single 24GB GPU (Q4). Near 72B quality. |
| **Qwen 2.5 72B Instruct** | 72B | ~48 GB | ~145 GB | Excellent | ~12 | Needs 2x24GB GPUs or 1x48GB. Matches GPT-4o on extraction. |
| **Qwen 3 8B** | 8B | ~5-6 GB | ~17 GB | Very Good | ~75 | Latest generation. Chain-of-thought + tool calling in one pass. |
| **Llama 3.3 70B** | 70B | ~48 GB | ~140 GB | Excellent | ~12 | Strong instruction following (IFEval: 92.1). |
| **Llama 3.1 8B Instruct** | 8B | ~5-6 GB | ~17 GB | Good | ~80 | Reliable JSON via Hermes tool format. |
| **Phi-4** | 14B | ~10 GB | ~30 GB | Good | ~50 | Strong reasoning but weaker on strict formatting. |
| **Phi-4 Mini** | 3.8B | ~3-4 GB | ~8 GB | Fair | ~120 | Very fast. Native 128K context. Struggles with complex schemas. |
| **DeepSeek R1 Distill Qwen 14B** | 14B | ~10-12 GB | ~30 GB | Very Good | ~40 | Strong reasoning. JSON support added May 2025. |
| **Mistral/Mixtral** | Various | Various | Various | Good | Fast (MoE) | 6x faster inference via sparse MoE. |

#### Recommended Models

**Primary: Qwen 2.5 14B Instruct** (or Qwen 3 8B if latest generation is available)
- **Why:** Qwen 2.5 dramatically outperforms on structured extraction tasks, with 94% accuracy on JSON extraction compared to Llama's 87%. Specifically trained to understand and generate structured data including JSON objects and formatted outputs. The 14B model fits comfortably on a single 24GB GPU (RTX 3090/4090) with Q4 quantization.
- **VRAM:** ~10-12 GB at Q4_K_M quantization
- **Context:** 32K tokens (sufficient for most call transcripts)

**Fallback: Qwen 2.5 7B Instruct**
- **Why:** If GPU memory is constrained. Still excellent JSON output for its size. Runs on 8GB VRAM.
- **Trade-off:** Slightly lower quality on complex multi-field extraction

**CPU-only fallback: Llama 3.1 8B Instruct via Ollama**
- **Why:** Ollama makes it trivial to run. Good JSON via Hermes tool format. Slower but works without GPU.

### 4.5 Chunking Strategies for Long Transcripts

A 60-minute sales call produces ~8,000-12,000 words (~10,000-15,000 tokens). Most 7B-14B models have 32K token context windows, so the full transcript usually fits in a single pass. However, for very long calls or smaller models with shorter contexts, chunking is needed.

#### Strategy 1: Full-Transcript Single Pass (Recommended)

If the transcript fits within the model's context window (with room for the system prompt and output), process it in one pass. This preserves full context and produces the best results.

**When to use:** Calls under 45-60 minutes with a 32K-context model.

#### Strategy 2: Map-Reduce (for Long Transcripts)

Split the transcript into overlapping chunks, extract from each chunk independently (Map phase), then merge and deduplicate results (Reduce phase).

**Map phase:** Each chunk gets the full extraction prompt. Each produces a partial JSON result.
**Reduce phase:** A second LLM pass receives all partial results and merges them into a single coherent output, resolving conflicts (e.g., same competitor mentioned in multiple chunks with different sentiments -- take the most recent or most specific one).

**Chunk size:** ~4,000-6,000 tokens per chunk with ~500 token overlap (to avoid splitting a conversation topic across chunk boundaries).

**When to use:** Calls over 60 minutes, or when using models with shorter context windows.

#### Strategy 3: Hierarchical Extraction

1. **Pass 1 (Summary):** LLM produces a 500-word summary of the entire transcript
2. **Pass 2 (Structured Extraction):** LLM extracts structured fields from the summary + selected high-relevance passages

**When to use:** When you need speed over completeness (e.g., quick post-call summary within 30 seconds).

### 4.6 Fine-Tuning vs Prompting

**Research consensus (2025):** Start with well-engineered prompts. Fine-tuning is only worth it when:
1. You have 500+ labeled examples of transcript-to-JSON pairs
2. Your extraction quality with prompting is below acceptable thresholds
3. You need to use a smaller model (fine-tuned 7B can match prompted 14B)
4. You have specific domain terminology that the base model consistently mishandles

**Key finding:** In a 2025 claim extraction study, fine-tuning yielded only "marginal improvements over a baseline model using a single-shot prompt." For structured extraction from sales calls, well-engineered prompting with a capable model (Qwen 2.5 14B+) should be sufficient.

**If fine-tuning becomes necessary:** Use LoRA (Low-Rank Adaptation) which requires only 10-20% additional VRAM and can be trained on a single GPU. Tools: Unsloth (2x faster LoRA training), or Hugging Face PEFT.

### 4.7 Evaluation Metrics

To measure extraction quality, use standard NER (Named Entity Recognition) evaluation metrics:

| Metric | What it Measures | Formula |
|--------|-----------------|---------|
| **Precision** | Of all extracted items, how many are correct? | Correct / (Correct + False Positives) |
| **Recall** | Of all actual items, how many did we find? | Correct / (Correct + False Negatives) |
| **F1 Score** | Harmonic mean of precision and recall | 2 * (Precision * Recall) / (Precision + Recall) |

**Evaluation process:**
1. Create a "gold standard" dataset: 50-100 manually annotated transcripts with expected JSON output
2. Run the extraction pipeline on each transcript
3. Compare extracted fields to gold standard
4. Calculate per-field and aggregate precision/recall/F1

**Target metrics:** Precision > 0.85, Recall > 0.80, F1 > 0.82 for production use.

---

## 5. Sentiment Analysis

### 5.1 Updated Decision: LLM-Integrated Sentiment (Feb 2026)

**Original plan:** Use `cardiffnlp/twitter-roberta-base-sentiment-latest` (~500MB) as a separate per-segment classifier.

**Updated decision:** Fold sentiment into the LLM extraction pass. No separate sentiment model.

**Why the change:** After researching what competitors actually use and evaluating off-the-shelf models, we found:

1. **Competitors use proprietary models** — Gong, Deepgram, Observe.AI all train on millions of their own recorded calls. No one uses off-the-shelf HuggingFace classifiers.
2. **Tweet-trained models are wrong domain** — `cardiffnlp/twitter-roberta-base-sentiment-latest` is trained on 124M tweets. Sales conversations have fundamentally different sentiment patterns (see 5.2).
3. **No sales-specific model exists** — Zero open-source models fine-tuned on sales/business conversations.
4. **No Tamil sentiment model exists** — None of the available models support Tamil. Hindi has partial coverage.
5. **LLM approach is better for sales** — The LLM sees full conversation context and can be prompt-engineered with sales-specific rules.

### 5.2 The Domain Mismatch Problem

Standard sentiment models (trained on tweets/reviews) misclassify sales conversation patterns:

| Sales Context | Tweet Model Says | Correct Label | Why |
|---------------|------------------|---------------|-----|
| "That's expensive" | Negative | **Neutral** | Price objection = buying signal in sales |
| "We're evaluating your competitors" | Negative | **Neutral/Positive** | Active evaluation = live deal |
| "Let me think about it" | Neutral | **Negative** | Classic stall = deal risk |
| "Can you do a discount?" | Not in training data | **Positive** | Ready to buy, negotiating |
| "Send over the contract" | Neutral | **Very Positive** | Closing signal |

An LLM with sales-specific prompt rules handles these correctly. A tweet-trained classifier cannot.

### 5.3 What Competitors Actually Use

| Competitor | Approach | Details |
|-----------|----------|---------|
| **Gong** | Proprietary multi-signal | Text + tone + pacing + engagement. Custom NLP trained on millions of sales calls |
| **Deepgram** | Task-Specific Language Models | Purpose-built small models fine-tuned on 60K+ domain conversations. CNN+RNN+Transformer. Uses acoustic cues |
| **Observe.AI** | Proprietary tonality-based | Trained on 10M+ hours of customer service calls. Superimposes audio features over text |
| **AssemblyAI** | Fine-tuned transformers | Undisclosed models, $0.02/hour suggesting small efficient models |
| **Fireflies** | "Advanced neural networks" | No model specifics disclosed. Per-speaker sentiment |

**Key takeaway:** Their moat is the training data (millions of calls), not the architecture.

### 5.4 Off-the-Shelf Model Evaluation (for reference)

| Model | Params | F1/Acc | Classes | Hindi | Tamil | Sales Fit |
|-------|--------|--------|---------|-------|-------|-----------|
| cardiffnlp/twitter-roberta-base | 125M | ~81% | 3 | No | No | **Poor** (tweets) |
| siebert/sentiment-roberta-large (SiEBERT) | 355M | 93.2% | 2 (no neutral) | No | No | **Partial** (no neutral class) |
| clapAI/modernBERT-base-multilingual | 150M | F1 80.2 | 3 | No | No | Good but English only |
| cardiffnlp/twitter-xlm-roberta-base | 278M | ~80% | 3 | Yes | **No** | **Poor** (tweets) |
| tabularisai/multilingual-sentiment | 66M | ~93%* | 5 | Yes | **No** | Synthetic data, commercial license |
| nlptown/bert-multilingual-sentiment | 110M | ~70% | 5 (stars) | No | No | **Poor** (product reviews) |

*On own test set. No model supports Tamil. No model trained on conversational/sales data.

### 5.5 Chosen Approach: LLM-Integrated Sentiment

Include per-segment sentiment + overall trajectory in the same JSON extraction pass:

```json
{
  "segment_sentiments": [
    {"segment_id": 0, "sentiment": "neutral", "confidence": 0.9},
    {"segment_id": 1, "sentiment": "positive", "confidence": 0.85}
  ],
  "overall_sentiment": {
    "overall": "mixed",
    "customer_net": "positive",
    "rep_net": "positive",
    "trajectory": "improving",
    "notable_shifts": [
      {"timestamp": 930.0, "from": "positive", "to": "negative", "trigger": "Pricing discussion"}
    ]
  }
}
```

**Sales-specific prompt rules:**
```
SENTIMENT RULES (sales context):
- Price objections ("that's expensive") are NEUTRAL, not negative — buying signals
- Competitor evaluation ("we're looking at X too") is NEUTRAL — active deal
- "Let me think about it" / "we'll get back to you" is NEGATIVE — deal risk
- "Send the contract" / "what's the next step?" is POSITIVE — closing signal
- Classify each segment considering surrounding conversation context
```

**Advantages over separate model:**
- Zero additional model/memory (sentiment extracted in same LLM pass)
- Sales-context awareness via prompt engineering
- Multilingual (works on English translations of Tamil/Hindi via IndicTrans2)
- ~90-94% accuracy (comparable to dedicated classifiers in zero-shot)

**Fallback (if LLM sentiment proves unreliable):**
Add `clapAI/modernBERT-base-multilingual-sentiment` (150M, F1 80.2, 3-class) as a verification layer. Run on English text only. Use as second opinion, not primary.

### 5.6 Sentiment Trajectory

Track how sentiment changes over time (computed from per-segment sentiments):
- **Where pricing discussion hurt:** Sentiment dropped at 15:30 when pricing was discussed
- **Where the demo won them over:** Sentiment shifted from neutral to positive during the product walkthrough
- **Whether the call ended well:** Positive ending sentiment correlates with deal progression
- **Trajectory classification:** Split call into thirds (opening/middle/closing), compare average sentiment → `improving`, `declining`, `stable_positive`, `stable_negative`, `volatile`

### 5.7 Model Configuration Strategy

**Separate config fields for Phase 5 vs Phase 7:**

```python
# Shared base URL (same Ollama/vLLM instance)
LLM_BASE_URL = "http://localhost:11434"

# Phase 5: Vocabulary correction (simple task — word swaps)
LLM_MODEL = "qwen2.5:7b-instruct"

# Phase 7: Intelligence extraction (complex task — 20+ field JSON + sentiment)
INTELLIGENCE_LLM_MODEL = "qwen2.5:7b-instruct"  # Same in dev, change in production
```

| Environment | Phase 5 Model | Phase 7 Model | Serving |
|-------------|---------------|---------------|---------|
| **Development (CPU)** | qwen2.5:7b-instruct | qwen2.5:7b-instruct | Ollama |
| **Production (GPU)** | qwen2.5:7b-instruct | qwen2.5:14b-instruct | vLLM (guided_json) |

Both use OpenAI-compatible `/v1/chat/completions` API — switching between Ollama and vLLM is config-only, no code changes.

---

## 6. Zoho CRM Integration

### 6.1 API Overview

Zoho CRM provides a REST API (latest version: V8, but V6 is still widely used) for managing records across modules like Leads, Contacts, Deals, Accounts, Tasks, Notes, Calls, and custom modules.

**Base URL:** `https://www.zohoapis.com/crm/v6/` (or `/v8/`)
**Authentication:** OAuth 2.0
**SDK:** `zohocrmsdk7-0` (PyPI) or `zohocrmsdk8-0` for V8

### 6.2 OAuth 2.0 Flow

Zoho uses OAuth 2.0 with a "Self-Client" flow ideal for server-to-server integrations (no user browser interaction needed after initial setup).

**Step-by-step setup:**

1. **Register application:** Go to Zoho API Console (https://api-console.zoho.com/), create a "Self Client" application. Get `client_id` and `client_secret`.

2. **Generate grant token:** In the API console, go to your Self Client > "Generate Code." Enter scopes:
   ```
   ZohoCRM.modules.ALL,ZohoCRM.settings.ALL,ZohoCRM.users.READ
   ```
   Set expiry (10 minutes is fine). Click Generate. Copy the grant token.

3. **Exchange for refresh token:**
   ```bash
   curl -X POST "https://accounts.zoho.com/oauth/v2/token" \
     -d "code=GRANT_TOKEN" \
     -d "redirect_uri=https://example.com" \
     -d "client_id=YOUR_CLIENT_ID" \
     -d "client_secret=YOUR_CLIENT_SECRET" \
     -d "grant_type=authorization_code"
   ```
   Response contains `access_token` (expires in 1 hour) and `refresh_token` (does not expire).

4. **Store refresh token securely.** The SDK automatically refreshes access tokens using the refresh token.

**Python SDK token persistence:**
```python
from zohocrmsdk.src.com.zoho.api.authenticator.store import FileStore

# Tokens stored in a local file
token_store = FileStore(file_path="/path/to/token_store.txt")

# Or database storage
from zohocrmsdk.src.com.zoho.api.authenticator.store import DBStore
token_store = DBStore(
    host="localhost",
    database_name="zoho_tokens",
    user_name="root",
    password="password",
    port_number="3306"
)
```

### 6.3 Data Mapping: Intelligence to CRM

Here is how extracted intelligence maps to Zoho CRM modules and fields:

#### Approach 1: Custom Fields on Existing Modules (Recommended)

Add custom fields to the Deals module:

| Intelligence Field | CRM Field Type | CRM Module | Field Name |
|-------------------|---------------|------------|------------|
| Competitors mentioned | Multi-select picklist | Deals | `Competitors_Mentioned` |
| Primary pain point | Text (multi-line) | Deals | `Customer_Pain_Point` |
| Budget discussed | Checkbox (boolean) | Deals | `Budget_Discussed` |
| Budget amount | Currency | Deals | `Budget_Amount` |
| Decision maker | Text | Deals | `Decision_Maker` |
| Decision timeline | Date | Deals | `Decision_Timeline` |
| Next steps | Text (multi-line) | Deals | `Agreed_Next_Steps` |
| Deal health score | Number (0-100) | Deals | `AI_Deal_Health_Score` |
| MEDDIC score | Number (0-6) | Deals | `MEDDIC_Score` |
| Call sentiment | Picklist (Positive/Neutral/Negative) | Deals | `Last_Call_Sentiment` |
| Talk ratio | Decimal | Deals | `Rep_Talk_Ratio` |

**Custom field limits by Zoho edition:**
- Standard: 10 custom fields per module
- Professional: 155 custom fields per module
- Enterprise: 300 custom fields per module

For our use case, the Professional edition (155 fields) is sufficient.

#### Approach 2: Notes for Detailed Intelligence

Attach a formatted note to the Deal/Contact record with the full intelligence summary:

```python
import requests

def create_intelligence_note(deal_id: str, intelligence: dict, access_token: str):
    """Attach call intelligence as a Note on a Zoho CRM Deal."""
    url = f"https://www.zohoapis.com/crm/v6/Deals/{deal_id}/Notes"

    note_content = f"""
## Call Intelligence Summary
**Date:** {intelligence['call_date']}
**Duration:** {intelligence['duration']} minutes
**Talk Ratio:** Rep {intelligence['talk_ratio_rep']}% / Customer {intelligence['talk_ratio_customer']}%
**Sentiment:** {intelligence['overall_sentiment']}

### Competitors Mentioned
{chr(10).join(f"- {c['name']} ({c['sentiment']})" for c in intelligence['competitors'])}

### Pain Points
{chr(10).join(f"- {p}" for p in intelligence['pain_points'])}

### Objections
{chr(10).join(f"- [{o['type']}] {o['text']}" for o in intelligence['objections'])}

### Next Steps
{chr(10).join(f"- {n}" for n in intelligence['next_steps'])}

### Action Items
**Rep:** {chr(10).join(f"- {a}" for a in intelligence['rep_action_items'])}
**Customer:** {chr(10).join(f"- {a}" for a in intelligence['customer_action_items'])}
    """

    payload = {
        "data": [{
            "Note_Title": f"Call Intelligence - {intelligence['call_date']}",
            "Note_Content": note_content
        }]
    }

    response = requests.post(
        url,
        json=payload,
        headers={
            "Authorization": f"Zoho-oauthtoken {access_token}",
            "Content-Type": "application/json"
        }
    )
    return response.json()
```

#### Approach 3: Custom Module (Advanced)

Create a "Call Intelligence" custom module with all intelligence fields as native fields. Link it to Deals/Contacts via lookup fields. This provides the richest querying and reporting capabilities but requires more setup.

### 6.4 API Rate Limits

Zoho CRM uses a concurrency-based rate limiting model (not requests-per-minute):

| Limit Type | Value |
|------------|-------|
| Concurrent API calls per org | 20 (general) |
| Sub-concurrency (heavy operations) | 10 (create/update/upsert records) |
| API credits per day (Professional) | 50,000 + (user count * 500) |
| API credits per day (Enterprise) | 50,000 + (user count * 1,000) |
| Max credits per day (Enterprise) | 5,000,000 |
| Records per API call (batch) | 100 (insert/update) |

**Webhook limitations:** Cannot set up webhooks for Call Logs and Notes modules. Use polling or manual trigger for these.

**Our usage pattern:** We process calls asynchronously (not real-time), so rate limits are unlikely to be an issue. A single call intelligence sync requires ~3-5 API calls (lookup deal, update fields, create note, optionally create task for action items). Even processing 100 calls/day would use only 300-500 credits.

### 6.5 Finding the Right Deal/Contact

Before syncing intelligence, we need to find the correct CRM record. Two approaches:

1. **User provides Deal/Contact ID:** The simplest approach. User selects the CRM record when uploading the call.
2. **Search by email/phone:** Extract participant email or phone from the call metadata. Search Zoho CRM:
   ```
   GET /crm/v6/Contacts/search?email=customer@company.com
   GET /crm/v6/Deals/search?criteria=(Contact_Name:equals:{contact_id})
   ```
3. **Search by participant name:** Less reliable due to name variations.

**Recommendation:** Start with approach 1 (user provides record ID via UI dropdown). Add approach 2 as automation later.

---

## 7. Recommended Approach

### 7.1 Architecture Overview

```
Structured Transcript (Speaker + Text + Timestamp)
    |
    v
[Coaching Metrics Computation]  ── Direct from diarization data
    |-- Talk ratio (rep vs customer time)
    |-- Longest monologue
    |-- Question count
    |-- Interruptions
    |-- Filler word count
    |
    v
[LLM Intelligence Extraction + Sentiment]  ── Qwen 2.5 14B via vLLM (or Ollama)
    |-- System prompt with JSON schema + sales-specific sentiment rules
    |-- Full transcript as user message
    |-- guided_json for schema enforcement
    |-- Extracts: competitors, objections, next steps, budget, sentiment (all in one pass)
    |
    v
[Post-Processing]
    |-- Pydantic validation
    |-- Evidence linking (quote + timestamp for each extraction)
    |-- Methodology scoring (MEDDIC/BANT from extracted fields)
    |-- Deal health scoring (weighted composite)
    |
    v
[Output]
    |-- JSON intelligence report
    |-- UI display (rich cards/tables)
    |-- Zoho CRM sync (custom fields + notes)
    |-- PDF report (extended from existing)
```

### 7.2 Step-by-Step Implementation Plan

**Step 1: Coaching Metrics (No LLM Needed)**
Compute directly from diarization + transcript data:
- Talk ratio from speaker segment durations
- Longest monologue from consecutive same-speaker segments
- Question detection via regex (sentences ending in "?")
- Filler words via regex matching against a list
- Interruptions from overlapping speaker timestamps

**Step 2: LLM Extraction + Sentiment (Single Pass)**
Deploy Qwen 2.5 14B Instruct via vLLM (GPU) or Ollama (CPU). Design system prompt with JSON schema that includes per-segment sentiment + overall trajectory (sales-specific rules in prompt). Process transcripts and validate output with Pydantic. No separate sentiment model needed — see Section 5 for rationale.

**Step 4: Methodology Scoring**
Map extracted fields to MEDDIC/BANT/SPIN scoring rubrics. Compute scores automatically. No additional LLM call needed -- just logic over the extracted JSON.

**Step 5: Zoho CRM Sync**
Implement OAuth flow. Create custom fields on Deals module. Sync extracted intelligence (custom fields + note attachment). Add UI for CRM record selection.

**Step 6: UI Enhancement**
Add intelligence display to the existing UI: call summary card, competitor mentions, objections, next steps, sentiment chart, coaching metrics dashboard.

### 7.3 Serving Infrastructure

**Option A: vLLM (Recommended for GPU)**
```bash
pip install vllm
vllm serve Qwen/Qwen2.5-14B-Instruct-GPTQ-Int4 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.90
```
- Requires NVIDIA GPU with 12+ GB VRAM (24GB recommended)
- OpenAI-compatible API at http://localhost:8000/v1
- Supports guided_json for constrained decoding
- Batched inference for processing multiple calls

**Option B: Ollama (Recommended for CPU or Consumer GPU)**
```bash
ollama pull qwen2.5:14b
# Or for smaller GPU:
ollama pull qwen2.5:7b
```
- Runs on CPU (slower, ~5-10 tok/s) or consumer GPU
- OpenAI-compatible API at http://localhost:11434/v1
- Simpler setup, less control over constrained decoding
- Good for development and testing

**Option C: Serverless GPU (RunPod / Lambda)**
- Rent GPU by the minute for processing calls
- Pay only for actual processing time
- Useful if you process calls infrequently (< 20/day)

---

## 8. Implementation Priority

### Phase 7A: Core Intelligence (Build First)

| Priority | Feature | Complexity | Dependencies |
|----------|---------|-----------|--------------|
| **P0** | Talk ratio & coaching metrics | Low | Diarization data (Phase 2) |
| **P0** | Call summary (LLM-generated) | Medium | LLM serving infrastructure |
| **P0** | Competitor mentions extraction | Medium | LLM + competitor list config |
| **P0** | Next steps / action items | Medium | LLM |
| **P0** | Objections detected | Medium | LLM |
| **P1** | Per-segment sentiment analysis | Medium | Sentiment model |
| **P1** | Pain points extraction | Medium | LLM |
| **P1** | Budget / pricing discussion | Medium | LLM |
| **P1** | Decision maker identification | Medium | LLM |

### Phase 7B: Advanced Intelligence (Build Second)

| Priority | Feature | Complexity | Dependencies |
|----------|---------|-----------|--------------|
| **P2** | Deal stage classification | Medium | LLM + deal stage definitions |
| **P2** | MEDDIC/BANT scoring | Medium | Extracted fields + scoring logic |
| **P2** | Sentiment trajectory chart | Medium | Segment sentiments |
| **P2** | Feature requests extraction | Medium | LLM |
| **P2** | Buying committee mapping | Medium | LLM |
| **P3** | Deal health scoring | High | All extracted fields + weighting |
| **P3** | Coaching scorecards | High | Metrics + methodology scoring |
| **P3** | Risk signal detection | Medium | LLM + scoring logic |

### Phase 7C: CRM Integration (Build Third)

| Priority | Feature | Complexity | Dependencies |
|----------|---------|-----------|--------------|
| **P1** | Zoho OAuth setup | Medium | Zoho developer account |
| **P1** | Custom fields on Deals module | Low | Zoho admin access |
| **P1** | Note attachment (intelligence summary) | Medium | OAuth + Notes API |
| **P2** | Custom field sync (auto-populate) | Medium | Field mapping config |
| **P2** | Task creation for action items | Medium | Tasks API |
| **P3** | Deal/Contact auto-matching | High | Search API + matching logic |
| **P3** | Webhook-triggered analysis | High | Webhook + queue system |

---

## 9. Proposed JSON Schema

This is the complete output schema for the intelligence extraction pipeline. Every call produces one JSON object conforming to this schema.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "CallIntelligence",
  "type": "object",
  "properties": {

    "metadata": {
      "type": "object",
      "description": "Call metadata and processing info",
      "properties": {
        "call_id": { "type": "string", "description": "Unique identifier for this call" },
        "call_date": { "type": "string", "format": "date-time" },
        "duration_seconds": { "type": "integer" },
        "participants": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "speaker_label": { "type": "string", "description": "SPEAKER_00, SPEAKER_01, etc." },
              "role": { "type": "string", "enum": ["rep", "customer", "unknown"] },
              "name": { "type": ["string", "null"], "description": "Name if identified" }
            }
          }
        },
        "language": { "type": "string", "description": "Primary language detected (e.g., 'en', 'ta', 'mixed')" },
        "extraction_model": { "type": "string", "description": "Model used for extraction (e.g., 'Qwen2.5-14B-Instruct')" },
        "extraction_timestamp": { "type": "string", "format": "date-time" }
      }
    },

    "summary": {
      "type": "object",
      "description": "LLM-generated call summary",
      "properties": {
        "one_line": { "type": "string", "description": "One-sentence summary of the call" },
        "detailed": { "type": "string", "description": "2-3 paragraph detailed summary" },
        "key_topics": {
          "type": "array",
          "items": { "type": "string" },
          "description": "Main topics discussed (e.g., 'pricing', 'integration', 'timeline')"
        }
      }
    },

    "competitors": {
      "type": "array",
      "description": "Competitors mentioned in the call",
      "items": {
        "type": "object",
        "properties": {
          "name": { "type": "string", "description": "Competitor name" },
          "sentiment": { "type": "string", "enum": ["positive", "negative", "neutral"], "description": "How the competitor was discussed" },
          "context": { "type": "string", "description": "Why/how the competitor was mentioned" },
          "quote": { "type": "string", "description": "Exact quote from transcript" },
          "timestamp": { "type": "number", "description": "Timestamp in seconds where mentioned" }
        }
      }
    },

    "pain_points": {
      "type": "array",
      "description": "Customer pain points and challenges identified",
      "items": {
        "type": "object",
        "properties": {
          "description": { "type": "string" },
          "business_impact": { "type": ["string", "null"], "description": "Quantified impact if mentioned" },
          "quote": { "type": "string" }
        }
      }
    },

    "objections": {
      "type": "array",
      "description": "Objections raised by the customer",
      "items": {
        "type": "object",
        "properties": {
          "type": {
            "type": "string",
            "enum": ["pricing", "feature_gap", "timing", "authority", "trust", "status_quo", "other"]
          },
          "description": { "type": "string" },
          "handled": { "type": "boolean", "description": "Whether the rep addressed this objection" },
          "rep_response": { "type": ["string", "null"], "description": "How the rep responded" },
          "quote": { "type": "string" }
        }
      }
    },

    "next_steps": {
      "type": "array",
      "description": "Agreed next steps and action items",
      "items": {
        "type": "object",
        "properties": {
          "description": { "type": "string" },
          "owner": { "type": "string", "enum": ["rep", "customer", "both"], "description": "Who is responsible" },
          "due_date": { "type": ["string", "null"], "format": "date", "description": "When it should be done, if mentioned" },
          "type": { "type": "string", "enum": ["follow_up_call", "send_document", "internal_review", "demo", "contract", "other"] }
        }
      }
    },

    "budget": {
      "type": "object",
      "description": "Budget and pricing information",
      "properties": {
        "discussed": { "type": "boolean" },
        "amount": { "type": ["number", "null"], "description": "Budget amount if mentioned" },
        "currency": { "type": ["string", "null"], "description": "Currency code (USD, INR, etc.)" },
        "pricing_presented": { "type": "boolean", "description": "Whether the rep shared pricing" },
        "discount_requested": { "type": "boolean" },
        "roi_discussed": { "type": "boolean" },
        "quote": { "type": ["string", "null"] }
      }
    },

    "decision_making": {
      "type": "object",
      "description": "Decision-making process information",
      "properties": {
        "decision_maker": { "type": ["string", "null"], "description": "Name/role of decision maker" },
        "buying_committee": {
          "type": "array",
          "items": { "type": "string" },
          "description": "All stakeholders involved in the decision"
        },
        "decision_process": { "type": ["string", "null"], "description": "How the decision will be made" },
        "decision_timeline": { "type": ["string", "null"], "description": "When the decision will be made" },
        "champion_identified": { "type": "boolean" },
        "champion_name": { "type": ["string", "null"] }
      }
    },

    "deal_stage": {
      "type": "object",
      "description": "Deal stage assessment",
      "properties": {
        "current_stage": {
          "type": "string",
          "enum": ["discovery", "qualification", "demo", "evaluation", "negotiation", "closing", "unknown"]
        },
        "confidence": { "type": "number", "minimum": 0, "maximum": 1, "description": "Confidence in stage assessment" },
        "stage_signals": {
          "type": "array",
          "items": { "type": "string" },
          "description": "Evidence for the stage classification"
        },
        "risk_signals": {
          "type": "array",
          "items": { "type": "string" },
          "description": "Signals that the deal may be at risk"
        }
      }
    },

    "methodology_scores": {
      "type": "object",
      "description": "Sales methodology scoring",
      "properties": {
        "meddic": {
          "type": "object",
          "properties": {
            "total_score": { "type": "integer", "minimum": 0, "maximum": 6 },
            "metrics": { "type": "boolean", "description": "Were quantifiable business metrics discussed?" },
            "economic_buyer": { "type": "boolean", "description": "Was the economic buyer identified?" },
            "decision_criteria": { "type": "boolean", "description": "Were decision criteria discussed?" },
            "decision_process": { "type": "boolean", "description": "Was the decision process mapped?" },
            "identify_pain": { "type": "boolean", "description": "Were specific pain points identified?" },
            "champion": { "type": "boolean", "description": "Was an internal champion identified?" },
            "details": {
              "type": "object",
              "description": "Evidence for each MEDDIC element",
              "additionalProperties": { "type": ["string", "null"] }
            }
          }
        },
        "bant": {
          "type": "object",
          "properties": {
            "total_score": { "type": "integer", "minimum": 0, "maximum": 4 },
            "budget": { "type": "boolean" },
            "authority": { "type": "boolean" },
            "need": { "type": "boolean" },
            "timeline": { "type": "boolean" },
            "details": {
              "type": "object",
              "additionalProperties": { "type": ["string", "null"] }
            }
          }
        }
      }
    },

    "features": {
      "type": "object",
      "description": "Product features and requirements discussed",
      "properties": {
        "features_discussed": {
          "type": "array",
          "items": { "type": "string" },
          "description": "Product features/capabilities mentioned"
        },
        "feature_requests": {
          "type": "array",
          "items": { "type": "string" },
          "description": "Features the customer wishes existed"
        },
        "use_cases": {
          "type": "array",
          "items": { "type": "string" },
          "description": "How the customer plans to use the product"
        },
        "technical_requirements": {
          "type": "array",
          "items": { "type": "string" },
          "description": "Specific technical needs mentioned"
        }
      }
    },

    "sentiment": {
      "type": "object",
      "description": "Sentiment analysis results",
      "properties": {
        "overall": {
          "type": "string",
          "enum": ["positive", "negative", "neutral", "mixed"]
        },
        "customer_net": {
          "type": "string",
          "enum": ["positive", "negative", "neutral"]
        },
        "rep_net": {
          "type": "string",
          "enum": ["positive", "negative", "neutral"]
        },
        "trajectory": {
          "type": "string",
          "description": "How sentiment changed over the call",
          "enum": ["improving", "declining", "stable_positive", "stable_negative", "stable_neutral", "volatile"]
        },
        "notable_shifts": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "timestamp": { "type": "number" },
              "from_sentiment": { "type": "string" },
              "to_sentiment": { "type": "string" },
              "trigger": { "type": ["string", "null"], "description": "What caused the shift" }
            }
          }
        }
      }
    },

    "coaching_metrics": {
      "type": "object",
      "description": "Communication and coaching metrics (computed from diarization, not LLM)",
      "properties": {
        "talk_ratio": {
          "type": "object",
          "properties": {
            "rep_percentage": { "type": "number" },
            "customer_percentage": { "type": "number" },
            "assessment": { "type": "string", "enum": ["optimal", "rep_dominant", "customer_dominant"] }
          }
        },
        "longest_monologue_seconds": { "type": "number" },
        "longest_monologue_speaker": { "type": "string" },
        "question_count": {
          "type": "object",
          "properties": {
            "rep": { "type": "integer" },
            "customer": { "type": "integer" }
          }
        },
        "filler_word_count": {
          "type": "object",
          "properties": {
            "rep": { "type": "integer" },
            "customer": { "type": "integer" },
            "rep_details": {
              "type": "object",
              "description": "Count per filler word (um, uh, like, you know, basically, etc.)",
              "additionalProperties": { "type": "integer" }
            }
          }
        },
        "interruption_count": { "type": "integer" },
        "average_response_time_seconds": { "type": "number" },
        "silence_percentage": { "type": "number" }
      }
    },

    "questions_asked": {
      "type": "array",
      "description": "Questions asked during the call (by both parties)",
      "items": {
        "type": "object",
        "properties": {
          "question": { "type": "string" },
          "asked_by": { "type": "string", "enum": ["rep", "customer"] },
          "question_type": { "type": "string", "enum": ["open", "closed", "probing", "clarifying", "leading"] },
          "answered": { "type": "boolean" },
          "timestamp": { "type": "number" }
        }
      }
    }

  },
  "required": ["metadata", "summary", "coaching_metrics"]
}
```

### 9.1 Schema Design Principles

1. **Nullable fields:** Most fields are nullable (`["string", "null"]`). If the information was not discussed in the call, the value should be `null`, not an empty string or a hallucinated value.

2. **Evidence linking:** Each extracted item includes a `quote` field with the exact text from the transcript that supports the extraction. This enables human verification and builds trust.

3. **Enums for categories:** Objection types, sentiment values, deal stages, etc. use `enum` constraints to ensure consistent categorization across all calls.

4. **Separate computation paths:** `coaching_metrics` is computed directly from diarization data (no LLM needed). `sentiment` is computed by the sentiment model. Everything else comes from the LLM extraction pass.

5. **Schema versioning:** Include a `schema_version` field (not shown above) to track schema evolution over time.

### 9.2 Example System Prompt

```
You are a sales call intelligence analyst. Your job is to extract structured
business intelligence from a sales call transcript.

RULES:
1. Only extract information EXPLICITLY stated in the transcript.
2. If a data point was not discussed, set it to null. NEVER fabricate information.
3. Include exact quotes from the transcript as evidence for each extraction.
4. The transcript uses speaker labels like "SPEAKER_00:" and "SPEAKER_01:".
   Determine which speaker is the sales rep and which is the customer based on
   context (the rep typically introduces themselves and the product).
5. For competitor mentions, include indirect references like "the other tool"
   or "what we're currently using" if you can identify the specific product.
6. For objections, classify the type accurately and note whether the rep
   addressed the objection.

OUTPUT FORMAT:
Return a single JSON object matching the provided schema. Do not include any
text outside the JSON object.
```

---

## 10. Real-Time vs Post-Call Analysis

### 10.1 Comparison

| Aspect | Real-Time (During Call) | Post-Call (Our Approach) | Hybrid |
|--------|------------------------|--------------------------|--------|
| **Latency** | ~300ms per utterance | 30-120 seconds after call ends | Quick summary in 30s, deep analysis in 2-5 min |
| **Accuracy** | Lower (streaming ASR sacrifices precision) | Higher (full context available) | Best of both |
| **Features** | Live coaching, battle cards, real-time cues | Full intelligence extraction, deal scoring | Core intelligence immediately, deep analysis later |
| **Infrastructure** | WebSocket streaming, low-latency GPU | Batch processing, can use CPU | Both needed |
| **Use case** | Rep needs in-call help | Manager reviews calls, CRM population | Most practical for teams |
| **Who does it** | Clari Copilot, Revenue.io (Moments) | Gong, Chorus, Fireflies | Zoom Revenue Accelerator |

### 10.2 Recommendation: Post-Call with Quick Summary

Our platform processes uploaded audio files, so real-time streaming is not the primary use case. The recommended approach:

1. **Immediately after transcription completes (~30s):** Generate a quick 3-sentence summary using the LLM. Show this in the UI immediately.
2. **Background processing (~1-3 min):** Run the full intelligence extraction pipeline (sentiment, LLM extraction, coaching metrics, methodology scoring). Update the UI when complete.
3. **CRM sync (~5s):** After extraction, push intelligence to Zoho CRM.

This gives the user immediate value (summary) while the detailed analysis runs in the background.

### 10.3 Future: Real-Time Support

If the platform later supports live call integration (e.g., via Zoho PhoneBridge or VoIP integration), real-time features could be added:
- Stream audio chunks to the ASR pipeline
- Run lightweight keyword detection (competitor mentions, objection phrases) in real-time
- Surface battle cards via WebSocket to the rep's screen
- Full intelligence extraction happens post-call

This would be a Phase 8+ enhancement.

---

## 11. Key Technical Decisions Summary

| Decision | Choice | Reasoning |
|----------|--------|-----------|
| Primary LLM | Qwen 2.5 14B Instruct | 94% JSON extraction accuracy, best structured output in its class, fits 24GB GPU |
| Fallback LLM | Qwen 2.5 7B or Llama 3.1 8B | For smaller GPUs / CPU-only environments |
| LLM serving | vLLM (GPU) or Ollama (CPU) | vLLM: guided_json constrained decoding. Ollama: simple setup |
| Sentiment | LLM-integrated (in extraction pass) | No separate model. Sales-context rules via prompt. See Section 5 |
| Phase 5 LLM | Qwen 2.5 7B Instruct | Simpler vocab correction task, stays 7B even in production |
| Phase 7 LLM | Qwen 2.5 7B (dev) / 14B (prod) | Separate config field, same Ollama/vLLM base URL |
| Structured output | vLLM guided_json + Pydantic validation | Guarantees valid JSON with zero retries |
| CRM target | Zoho CRM v6/v8 REST API | Customer requirement. OAuth 2.0 self-client flow |
| Data mapping | Custom fields on Deals + Notes | Professional edition supports 155 custom fields |
| Chunking | Single-pass (< 45 min calls), Map-Reduce (longer) | Most calls fit in 32K context |
| Fine-tuning | Not initially, re-evaluate after 500+ labeled examples | Prompting sufficient for Qwen 2.5 14B |
| Coaching metrics | Direct computation from diarization data | No LLM needed, faster, deterministic |
| Analysis timing | Post-call with quick summary | Matches our file-upload workflow |

---

## Appendix A: Competitor Pricing Summary

| Platform | Price | Model |
|----------|-------|-------|
| Gong | $1,600/user/year + $50K platform fee | Quote-based, modular |
| Chorus (ZoomInfo) | ~$100-200/user/month | Bundled with ZoomInfo |
| Fireflies.ai | $10-39/user/month | Self-serve tiers |
| Clari Copilot | Enterprise quote-based | Merged with SalesLoft |
| Avoma | ~$19/user/month | Self-serve + enterprise |
| Jiminny | $85-100/user/month | Annual contract |
| Zoom Revenue Accelerator | Included with Zoom Business+ | Add-on to existing Zoom |
| Revenue.io | Enterprise quote-based | Annual contract |
| **Our platform (self-hosted)** | **Infrastructure cost only** | **No per-user fees** |

## Appendix B: Model VRAM Quick Reference

| Model | Q4_K_M | FP16 | Min GPU |
|-------|--------|------|---------|
| Qwen 2.5 7B | ~5-6 GB | ~17 GB | RTX 4060 (8GB) |
| Qwen 2.5 14B | ~10-12 GB | ~30 GB | RTX 3090 (24GB) |
| Qwen 2.5 32B | ~20-24 GB | ~65 GB | RTX 3090/4090 (24GB, tight) |
| Qwen 2.5 72B | ~48 GB | ~145 GB | 2x RTX 3090 or A6000 (48GB) |
| Llama 3.3 70B | ~48 GB | ~140 GB | 2x RTX 3090 or A6000 (48GB) |
| Phi-4 (14B) | ~10 GB | ~30 GB | RTX 3090 (24GB) |
| Phi-4 Mini (3.8B) | ~3-4 GB | ~8 GB | Any 8GB GPU |
| Sentiment model | N/A (LLM-integrated) | N/A | N/A |

## Appendix C: Post-Processing Microservice Architecture (Phase 9 — Future)

Phase 7's post-processing modules (vocabulary correction, LLM correction, coaching metrics, intelligence extraction, scoring) are designed to be self-contained with zero audio dependencies. This enables future extraction into a separate microservice.

**Separation boundary:**
```
ASR Microservice (GPU)                    Post-Processing Microservice (CPU/LLM)
├── Audio analysis                        ├── Vocabulary correction (5a/6a)
├── Core processing (resample, VAD, etc)  ├── LLM correction (5b)
├── Conditional processing                ├── Coaching metrics (7A)
├── Whisper ASR                           ├── Intelligence extraction (7A)
├── Pyannote diarization                  ├── Methodology scoring (7B)
├── ctc-forced-aligner                    ├── Deal health / coaching scorecard (7B)
├── VAD remapping                         └── (connects to Ollama/vLLM via HTTP)
├── Translation
└── Output: segments JSON ──────────────→ Input: segments JSON
```

**API contract:** POST /process with segments JSON → returns corrected segments + CallIntelligence JSON.

**Benefits:** Independent scaling, re-runnable without re-transcribing, lighter deployment (~50MB vs multi-GB), fault isolation.

**Design principle:** All post-processing modules (vocabulary.py, llm_corrector.py, coaching_metrics.py, intelligence.py) take pure dicts as input, zero imports from pipeline/transcribe/audio. This is intentional — enables microservice extraction with minimal refactoring.

See Phase 9 in CLAUDE.md for full architecture plan.
