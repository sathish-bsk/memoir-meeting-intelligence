"""
Deal matcher -- 3-tier matching to thread calls into deals.
READ-ONLY: results returned for intelligence.json, core_api writes to DB.

Matching priority:
  1. Exact email match (confidence: high)
  2. Domain match, skipping free-email providers (confidence: medium)
  3. Company name match, case-insensitive (confidence: low)
  4. No match -> return match_method="none", created_new_deal=true

Called after P2 (CRM Extraction) completes so we have contact.email and
contact.company. Results are stored in intelligence.json under "deal_match"
and core_api reads them on webhook to create/update memoir_deals rows.
"""

import logging
from typing import Optional

logger = logging.getLogger("job_processor.deal_matcher")

# Free email domains to skip for domain-based matching
FREE_EMAIL_DOMAINS = frozenset({
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "aol.com", "icloud.com", "protonmail.com", "mail.com",
    "live.com", "msn.com", "ymail.com", "zoho.com",
    "pm.me", "proton.me", "fastmail.com", "hey.com",
    "me.com", "mac.com", "googlemail.com",
})


async def match_call_to_deal(
    team_id: str,
    extracted_data: dict,
    current_meeting_id: Optional[str] = None,
) -> dict:
    """
    Match call to existing deal using 3-tier priority.

    Only matches OPEN deals. Returns match result dict (stored in JSON).

    Args:
        team_id: Team UUID as string.
        extracted_data: P2 CRM extraction result dict containing
            contact.email, contact.company, contact.name, etc.
        current_meeting_id: Current meeting UUID (excluded from
            previous-calls context query).

    Returns:
        Dict with deal_match data for intelligence.json:
        {
            match_method, matched_deal_id, match_confidence,
            extracted_email, extracted_company, extracted_domain,
            created_new_deal, call_number_in_deal,
            previous_calls_context, new_deal_data
        }
    """
    from .db import get_pool

    # Extract contact fields from P2 result
    contact = extracted_data.get("contact") or {}
    if not isinstance(contact, dict):
        contact = {}

    prospect_email = (contact.get("email") or "").strip().lower() or None
    prospect_company = (contact.get("company") or "").strip() or None
    prospect_name = (contact.get("name") or "").strip() or None

    # Extract domain from email
    prospect_domain = None
    if prospect_email and "@" in prospect_email:
        prospect_domain = prospect_email.split("@")[1].lower()
        if prospect_domain in FREE_EMAIL_DOMAINS:
            prospect_domain = None  # Don't use free-email domain for matching

    # Build the no-match / new-deal result template
    def _new_deal_result() -> dict:
        return {
            "match_method": "none",
            "matched_deal_id": None,
            "match_confidence": "low",
            "extracted_email": prospect_email,
            "extracted_company": prospect_company,
            "extracted_domain": prospect_domain,
            "created_new_deal": True,
            "call_number_in_deal": 1,
            "previous_calls_context": None,
            "new_deal_data": {
                "prospect_name": prospect_name,
                "prospect_company": prospect_company,
                "prospect_email": prospect_email,
                "prospect_domain": prospect_domain,
                "initial_stage": "awareness",
            },
        }

    # If we have nothing to match on, bail early
    if not prospect_email and not prospect_company:
        logger.info("Deal matcher: no email or company extracted, returning new deal")
        return _new_deal_result()

    try:
        pool = get_pool()
    except RuntimeError:
        logger.warning("Deal matcher: DB pool not available, returning new deal")
        return _new_deal_result()

    async with pool.acquire() as conn:
        # ---------------------------------------------------------------
        # Tier 1: Exact email match (highest confidence)
        # ---------------------------------------------------------------
        if prospect_email:
            row = await conn.fetchrow(
                """
                SELECT id, prospect_name, prospect_company, prospect_domain,
                       current_stage, total_calls, status
                FROM memoir_deals
                WHERE team_id = $1::uuid
                  AND prospect_email = $2
                  AND status = 'open'
                ORDER BY last_call_date DESC NULLS LAST
                LIMIT 1
                """,
                team_id, prospect_email,
            )
            if row:
                # Edge case: email matches but company name differs significantly
                # (possible job change). If extracted company is present and clearly
                # different from the deal's company, treat as a new deal.
                deal_company = (row["prospect_company"] or "").lower()
                if (
                    prospect_company
                    and deal_company
                    and prospect_company.lower() != deal_company
                    and len(prospect_company) >= 3
                ):
                    logger.info(
                        "Deal matcher: email matched deal %s but company "
                        "differs ('%s' vs '%s') -- creating new deal (job change?)",
                        row["id"], prospect_company, row["prospect_company"],
                    )
                    return _new_deal_result()

                deal_id = str(row["id"])
                total_calls = row["total_calls"] or 0
                context = await build_previous_calls_context(
                    conn, deal_id, current_meeting_id,
                )
                logger.info(
                    "Deal matcher: email match -> deal %s (%d prior calls)",
                    deal_id, total_calls,
                )
                return {
                    "match_method": "email",
                    "matched_deal_id": deal_id,
                    "match_confidence": "high",
                    "extracted_email": prospect_email,
                    "extracted_company": prospect_company,
                    "extracted_domain": prospect_domain,
                    "created_new_deal": False,
                    "call_number_in_deal": total_calls + 1,
                    "previous_calls_context": context,
                    "new_deal_data": None,
                }

        # ---------------------------------------------------------------
        # Tier 2: Domain match (medium confidence)
        # ---------------------------------------------------------------
        if prospect_domain:
            row = await conn.fetchrow(
                """
                SELECT id, prospect_name, prospect_company, prospect_email,
                       current_stage, total_calls, status
                FROM memoir_deals
                WHERE team_id = $1::uuid
                  AND prospect_domain = $2
                  AND status = 'open'
                ORDER BY last_call_date DESC NULLS LAST
                LIMIT 1
                """,
                team_id, prospect_domain,
            )
            if row:
                deal_id = str(row["id"])
                total_calls = row["total_calls"] or 0
                context = await build_previous_calls_context(
                    conn, deal_id, current_meeting_id,
                )
                logger.info(
                    "Deal matcher: domain match (%s) -> deal %s (%d prior calls)",
                    prospect_domain, deal_id, total_calls,
                )
                return {
                    "match_method": "domain",
                    "matched_deal_id": deal_id,
                    "match_confidence": "medium",
                    "extracted_email": prospect_email,
                    "extracted_company": prospect_company,
                    "extracted_domain": prospect_domain,
                    "created_new_deal": False,
                    "call_number_in_deal": total_calls + 1,
                    "previous_calls_context": context,
                    "new_deal_data": None,
                }

        # ---------------------------------------------------------------
        # Tier 3: Company name match (low confidence, >= 3 chars)
        # ---------------------------------------------------------------
        if prospect_company and len(prospect_company) >= 3:
            row = await conn.fetchrow(
                """
                SELECT id, prospect_name, prospect_company, prospect_email,
                       prospect_domain, current_stage, total_calls, status
                FROM memoir_deals
                WHERE team_id = $1::uuid
                  AND LOWER(prospect_company) = LOWER($2)
                  AND status = 'open'
                ORDER BY last_call_date DESC NULLS LAST
                LIMIT 1
                """,
                team_id, prospect_company,
            )
            if row:
                deal_id = str(row["id"])
                total_calls = row["total_calls"] or 0
                context = await build_previous_calls_context(
                    conn, deal_id, current_meeting_id,
                )
                logger.info(
                    "Deal matcher: company name match ('%s') -> deal %s (%d prior calls)",
                    prospect_company, deal_id, total_calls,
                )
                return {
                    "match_method": "company_name",
                    "matched_deal_id": deal_id,
                    "match_confidence": "low",
                    "extracted_email": prospect_email,
                    "extracted_company": prospect_company,
                    "extracted_domain": prospect_domain,
                    "created_new_deal": False,
                    "call_number_in_deal": total_calls + 1,
                    "previous_calls_context": context,
                    "new_deal_data": None,
                }

    # No match found -- core_api will create a new deal
    logger.info(
        "Deal matcher: no match for email=%s company=%s -> new deal",
        prospect_email, prospect_company,
    )
    return _new_deal_result()


async def build_previous_calls_context(
    conn,
    deal_id: str,
    current_meeting_id: Optional[str] = None,
) -> Optional[str]:
    """
    Build concise summary of previous calls in this deal.
    READ-only from call_intelligence + meetings tables.
    Max ~500 tokens for context window efficiency.

    Args:
        conn: asyncpg connection (already acquired from pool).
        deal_id: UUID of the memoir_deal.
        current_meeting_id: Current meeting UUID to exclude.

    Returns:
        Formatted string for LLM context, or None for first call.
        Example:
          "Call 1 (2026-01-08): Discovery call - discussed pain points. [Stage: awareness, Health: on_track]"
    """
    # Build the query -- exclude current meeting if provided
    if current_meeting_id:
        rows = await conn.fetch(
            """
            SELECT
                m.id,
                m.call_number_in_deal,
                m.created_at,
                ci.summary->>'one_line_summary' AS one_line,
                ci.crm_extraction->'deal'->>'stage' AS ai_stage,
                ci.validated_stage,
                ci.overall_pmf_signal AS pmf_signal,
                ci.call_health,
                ci.deal_momentum
            FROM meetings m
            LEFT JOIN call_intelligence ci ON ci.meeting_id = m.id
            WHERE m.memoir_deal_id = $1::uuid
              AND m.id != $2::uuid
            ORDER BY m.call_number_in_deal ASC NULLS LAST, m.created_at ASC
            LIMIT 5
            """,
            deal_id, current_meeting_id,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT
                m.id,
                m.call_number_in_deal,
                m.created_at,
                ci.summary->>'one_line_summary' AS one_line,
                ci.crm_extraction->'deal'->>'stage' AS ai_stage,
                ci.validated_stage,
                ci.overall_pmf_signal AS pmf_signal,
                ci.call_health,
                ci.deal_momentum
            FROM meetings m
            LEFT JOIN call_intelligence ci ON ci.meeting_id = m.id
            WHERE m.memoir_deal_id = $1::uuid
            ORDER BY m.call_number_in_deal ASC NULLS LAST, m.created_at ASC
            LIMIT 5
            """,
            deal_id,
        )

    if not rows:
        return None

    lines = []
    for r in rows:
        call_num = r["call_number_in_deal"] or "?"
        date_str = r["created_at"].strftime("%Y-%m-%d") if r["created_at"] else "unknown"
        summary = r["one_line"] or "No summary"
        # Truncate summary to keep context compact
        if len(summary) > 120:
            summary = summary[:117] + "..."
        stage = r["validated_stage"] or r["ai_stage"] or "unknown"
        health = r["call_health"] or "unknown"
        momentum = r["deal_momentum"]

        line = f"Call {call_num} ({date_str}): {summary} [Stage: {stage}, Health: {health}"
        if momentum:
            line += f", Momentum: {momentum}"
        line += "]"
        lines.append(line)

    return "\n".join(lines)
