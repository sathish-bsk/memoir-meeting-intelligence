"""
FAQ Grouper — uses Claude Haiku to group similar prospect questions into canonical topics.

Called from the digest generator to produce AI-grouped FAQ data stored in the
digest's metrics JSONB field.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_intelligence import CallQuestion

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"


async def group_similar_questions(
    db: AsyncSession,
    team_id: UUID,
    since: datetime,
) -> list[dict[str, Any]]:
    """Load recent questions, send to Haiku for grouping, return grouped result.

    Returns a list of dicts:
    [
        {
            "canonical_question": "What is the pricing?",
            "variations": ["How much does it cost?", "What are your plans?"],
            "total_asks": 15,
            "primary_category": "pricing",
            "unanswered_count": 2
        }
    ]
    """
    # Query distinct questions from call_questions
    result = await db.execute(
        select(
            CallQuestion.question,
            func.count(CallQuestion.id).label("ask_count"),
            func.sum(
                func.cast(CallQuestion.was_answered == False, func.integer())  # noqa: E712
            ).label("unanswered_count"),
        )
        .where(
            CallQuestion.team_id == team_id,
            CallQuestion.call_date >= since.date(),
        )
        .group_by(CallQuestion.question)
        .order_by(func.count(CallQuestion.id).desc())
        .limit(100)
    )
    rows = result.all()

    if not rows or len(rows) < 2:
        # Not enough questions to group
        return []

    # Format questions for the LLM
    question_list = []
    for row in rows:
        question_list.append({
            "question": row.question,
            "ask_count": row.ask_count,
            "unanswered_count": row.unanswered_count or 0,
        })

    # Try to call Claude Haiku
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping FAQ grouping")
        return []

    try:
        import httpx

        numbered = "\n".join(
            f"{i+1}. \"{q['question']}\" (asked {q['ask_count']}x, unanswered {q['unanswered_count']}x)"
            for i, q in enumerate(question_list)
        )

        prompt = f"""Group these {len(question_list)} questions into canonical topics.
Questions asking the same thing in different words should be merged.

Questions:
{numbered}

Return this JSON (and ONLY this JSON, no other text):
[
  {{
    "canonical_question": "What is the pricing?",
    "variations": ["How much does it cost?", "What are your plans?"],
    "total_asks": 15,
    "primary_category": "pricing",
    "unanswered_count": 2
  }}
]

Rules:
- Merge questions that ask the same thing in different words
- Keep the most clearly worded version as canonical_question
- primary_category: pricing|technical|security|integration|support|competitor|timeline|process|feature|other
- total_asks = sum of ask_count for all grouped questions
- unanswered_count = sum of unanswered_count for all grouped questions
- Sort by total_asks descending"""

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": HAIKU_MODEL,
                    "max_tokens": 4096,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt,
                        }
                    ],
                    "system": "You are a FAQ organizer for a B2B sales team. Group similar questions asked by prospects across sales calls. Return ONLY valid JSON.",
                },
            )

        if resp.status_code != 200:
            logger.error("Haiku API error %d: %s", resp.status_code, resp.text[:200])
            return []

        data = resp.json()
        text = data.get("content", [{}])[0].get("text", "")

        # Parse JSON from response
        # Handle potential markdown code blocks
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        groups = json.loads(text.strip())
        if not isinstance(groups, list):
            logger.error("Haiku returned non-list: %s", type(groups))
            return []

        logger.info("FAQ grouper: %d questions -> %d groups", len(question_list), len(groups))
        return groups

    except Exception:
        logger.exception("FAQ grouping failed")
        return []
