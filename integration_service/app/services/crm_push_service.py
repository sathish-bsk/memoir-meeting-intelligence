"""
Unified CRM push orchestrator — formats extraction data and pushes to HubSpot or Zoho.
Provider-agnostic extraction data is formatted into provider-specific payloads.
"""

import logging
from datetime import datetime, timezone

from app.services.hubspot_client import HubSpotClient, DEFAULT_STAGE_MAPPING as HS_STAGES
from app.services.zoho_client import ZohoClient, DEFAULT_STAGE_MAPPING as ZOHO_STAGES

logger = logging.getLogger(__name__)


def _format_hubspot_note(extraction: dict, metadata: dict) -> str:
    """Format a HubSpot note body from extraction data."""
    lines = []
    title = metadata.get("title", "Call")
    date = metadata.get("timestamp", datetime.now(timezone.utc).isoformat())[:10]
    lines.append(f"Memoir Call Summary — {title} ({date})")
    lines.append("")

    summary = extraction.get("summary", "")
    if isinstance(summary, dict):
        summary = summary.get("narrative", summary.get("text", str(summary)))
    if summary:
        lines.append(summary)
        lines.append("")

    pain_points = extraction.get("pain_points", [])
    if pain_points:
        lines.append("Pain Points:")
        for p in pain_points[:5]:
            text = p if isinstance(p, str) else p.get("pain", p.get("text", str(p)))
            lines.append(f"  - {text}")
        lines.append("")

    next_steps = extraction.get("next_steps", [])
    if next_steps:
        lines.append("Next Steps:")
        for ns in next_steps[:5]:
            text = ns if isinstance(ns, str) else ns.get("step", ns.get("text", str(ns)))
            lines.append(f"  - {text}")
        lines.append("")

    action_items = extraction.get("action_items", [])
    if action_items:
        lines.append("Action Items:")
        for ai in action_items[:5]:
            if isinstance(ai, str):
                lines.append(f"  - {ai}")
            else:
                task = ai.get("task", ai.get("text", str(ai)))
                owner = ai.get("owner", "")
                due = ai.get("due", "")
                parts = [task]
                if owner:
                    parts.append(f"Owner: {owner}")
                if due:
                    parts.append(f"Due: {due}")
                lines.append(f"  - {' — '.join(parts)}")
        lines.append("")

    competitors = extraction.get("competitors_mentioned", [])
    if competitors:
        comp_names = [c if isinstance(c, str) else c.get("competitor", c.get("name", str(c))) for c in competitors[:5]]
        lines.append(f"Competitors Mentioned: {', '.join(comp_names)}")

    budget = extraction.get("budget_range", extraction.get("budget", ""))
    if isinstance(budget, dict):
        budget = budget.get("range", budget.get("text", str(budget)))
    if budget:
        lines.append(f"Budget: {budget}")

    decision_makers = extraction.get("decision_makers", [])
    if decision_makers:
        dm_names = [d if isinstance(d, str) else d.get("name", str(d)) for d in decision_makers[:5]]
        lines.append(f"Decision Makers: {', '.join(dm_names)}")

    sentiment = extraction.get("sentiment", "")
    if sentiment:
        lines.append(f"Sentiment: {sentiment}")

    call_url = metadata.get("url", "")
    if call_url:
        lines.append("")
        lines.append(f"View full transcript: {call_url}")

    return "\n".join(lines)


def _format_zoho_call_description(extraction: dict, metadata: dict) -> str:
    """Format a Zoho call record description."""
    # Same content as HubSpot note
    return _format_hubspot_note(extraction, metadata)


def _extract_contact_data(extraction: dict) -> dict:
    """Extract contact info from CRM extraction."""
    crm = extraction.get("crm", extraction)
    contact = crm.get("contact", crm.get("prospect", {}))
    if isinstance(contact, str):
        contact = {"name": contact}

    name = contact.get("name", contact.get("full_name", ""))
    parts = name.split(maxsplit=1) if name else ["", ""]
    first_name = contact.get("first_name", parts[0] if parts else "")
    last_name = contact.get("last_name", parts[1] if len(parts) > 1 else "")

    return {
        "email": contact.get("email", ""),
        "first_name": first_name,
        "last_name": last_name,
        "company": contact.get("company", contact.get("organization", "")),
        "title": contact.get("title", contact.get("role", "")),
        "phone": contact.get("phone", ""),
        "name": name,
    }


def _extract_deal_data(extraction: dict) -> dict:
    """Extract deal info from CRM extraction."""
    crm = extraction.get("crm", extraction)
    deal = crm.get("deal", crm.get("opportunity", {}))
    if isinstance(deal, str):
        deal = {"name": deal}

    return {
        "name": deal.get("name", deal.get("deal_name", "")),
        "stage": deal.get("stage", deal.get("deal_stage", "")),
        "amount": deal.get("amount", deal.get("budget", "")),
    }


def _resolve_stage(stage: str, stage_mapping: dict, default_mapping: dict) -> str:
    """Resolve Memoir stage to CRM stage using custom mapping or defaults."""
    if not stage:
        return ""
    stage_lower = stage.lower().strip()
    # Custom mapping first
    if stage_mapping and stage_lower in stage_mapping:
        return stage_mapping[stage_lower]
    # Default mapping
    return default_mapping.get(stage_lower, "")


async def execute_crm_push(
    provider: str,
    access_token: str,
    api_domain: str | None,
    crm_extraction: dict,
    call_metadata: dict,
    field_mapping: dict = None,
    stage_mapping: dict = None,
) -> dict:
    """Execute CRM push for a specific provider.

    Returns dict with: status, crm_contact_id, crm_deal_id, crm_record_id, crm_record_url, pushed_data
    """
    field_mapping = field_mapping or {}
    stage_mapping = stage_mapping or {}

    contact_data = _extract_contact_data(crm_extraction)
    deal_data = _extract_deal_data(crm_extraction)

    # Must have at least a name or email to push
    if not contact_data.get("email") and not contact_data.get("name"):
        return {
            "status": "skipped",
            "error_message": "Insufficient contact data — no email or name found in extraction",
            "pushed_data": {},
        }

    if provider == "hubspot":
        return await _push_hubspot(access_token, contact_data, deal_data, crm_extraction, call_metadata, field_mapping, stage_mapping)
    else:
        return await _push_zoho(access_token, api_domain or "https://www.zohoapis.com", contact_data, deal_data, crm_extraction, call_metadata, field_mapping, stage_mapping)


async def _push_hubspot(
    access_token: str,
    contact_data: dict,
    deal_data: dict,
    extraction: dict,
    metadata: dict,
    field_mapping: dict,
    stage_mapping: dict,
) -> dict:
    """Push to HubSpot: find/create contact + deal, create note."""
    client = HubSpotClient(access_token)
    result = {"status": "completed", "pushed_data": {}}

    # 1. Find or create contact
    contact = await client.find_contact(
        email=contact_data.get("email"),
        name=contact_data.get("name"),
        company=contact_data.get("company"),
    )

    if contact:
        contact_id = contact["id"]
        logger.info(f"HubSpot: found existing contact {contact_id}")
    else:
        props = {}
        if contact_data.get("email"):
            props["email"] = contact_data["email"]
        if contact_data.get("first_name"):
            props["firstname"] = contact_data["first_name"]
        if contact_data.get("last_name"):
            props["lastname"] = contact_data["last_name"]
        if contact_data.get("company"):
            props["company"] = contact_data["company"]
        if contact_data.get("title"):
            props["jobtitle"] = contact_data["title"]
        if contact_data.get("phone"):
            props["phone"] = contact_data["phone"]

        contact = await client.create_contact(props)
        contact_id = contact["id"]
        logger.info(f"HubSpot: created new contact {contact_id}")

    result["crm_contact_id"] = contact_id

    # 2. Find or create deal
    deal = await client.find_deal(contact_id)
    deal_id = None

    if deal:
        deal_id = deal["id"]
        logger.info(f"HubSpot: found existing deal {deal_id}")

        # Update deal stage if extracted
        crm_stage = _resolve_stage(deal_data.get("stage", ""), stage_mapping, HS_STAGES)
        if crm_stage:
            await client.update_deal(deal_id, {"dealstage": crm_stage})
    else:
        deal_name = deal_data.get("name") or f"Memoir: {contact_data.get('company') or contact_data.get('name', 'New Deal')}"
        crm_stage = _resolve_stage(deal_data.get("stage", ""), stage_mapping, HS_STAGES) or "appointmentscheduled"
        deal_props = {"dealname": deal_name, "dealstage": crm_stage, "pipeline": "default"}
        if deal_data.get("amount"):
            deal_props["amount"] = str(deal_data["amount"])

        deal = await client.create_deal(deal_props, contact_id)
        deal_id = deal["id"]
        logger.info(f"HubSpot: created new deal {deal_id}")

    result["crm_deal_id"] = deal_id

    # 3. Apply custom field mapping
    if field_mapping:
        contact_updates = {}
        deal_updates = {}
        for memoir_field, crm_field in field_mapping.items():
            value = extraction.get(memoir_field, "")
            if isinstance(value, (list, dict)):
                value = str(value)
            if value and crm_field:
                # Determine if contact or deal field based on prefix convention
                if crm_field.startswith("deal_"):
                    deal_updates[crm_field[5:]] = value
                else:
                    contact_updates[crm_field] = value

        if contact_updates:
            await client.update_contact(contact_id, contact_updates)
        if deal_updates and deal_id:
            await client.update_deal(deal_id, deal_updates)

    # 4. Create note
    note_body = _format_hubspot_note(extraction, metadata)
    note = await client.create_note(note_body, contact_id, deal_id, metadata.get("timestamp"))
    note_id = note["id"]
    result["crm_record_id"] = note_id

    # Build CRM record URL
    portal_id = metadata.get("portal_id", "")
    if portal_id:
        result["crm_record_url"] = f"https://app.hubspot.com/contacts/{portal_id}/contact/{contact_id}"
    else:
        result["crm_record_url"] = f"https://app.hubspot.com/contacts/contact/{contact_id}"

    result["pushed_data"] = {
        "contact": contact_data,
        "deal": deal_data,
        "call_summary": note_body,
    }

    logger.info(f"HubSpot push complete: contact={contact_id}, deal={deal_id}, note={note_id}")
    return result


async def _push_zoho(
    access_token: str,
    api_domain: str,
    contact_data: dict,
    deal_data: dict,
    extraction: dict,
    metadata: dict,
    field_mapping: dict,
    stage_mapping: dict,
) -> dict:
    """Push to Zoho: find/create contact + account + deal, create call record."""
    client = ZohoClient(access_token, api_domain)
    result = {"status": "completed", "pushed_data": {}}

    # 1. Find or create account (company)
    account_id = None
    company = contact_data.get("company")
    if company:
        account = await client.find_account(company)
        if account:
            account_id = account.get("id")
            logger.info(f"Zoho: found existing account {account_id}")
        else:
            account = await client.create_account({"Account_Name": company})
            account_id = account.get("details", {}).get("id", account.get("id"))
            logger.info(f"Zoho: created new account {account_id}")

    # 2. Find or create contact
    contact = await client.find_contact(
        email=contact_data.get("email"),
        first_name=contact_data.get("first_name"),
        last_name=contact_data.get("last_name"),
    )

    if contact:
        contact_id = contact["id"]
        logger.info(f"Zoho: found existing contact {contact_id}")
    else:
        props = {}
        if contact_data.get("first_name"):
            props["First_Name"] = contact_data["first_name"]
        if contact_data.get("last_name"):
            props["Last_Name"] = contact_data["last_name"]
        elif contact_data.get("name"):
            props["Last_Name"] = contact_data["name"]
        else:
            props["Last_Name"] = "Unknown"
        if contact_data.get("email"):
            props["Email"] = contact_data["email"]
        if contact_data.get("title"):
            props["Title"] = contact_data["title"]
        if contact_data.get("phone"):
            props["Phone"] = contact_data["phone"]
        if account_id:
            props["Account_Name"] = {"id": account_id}

        contact = await client.create_contact(props)
        contact_id = contact.get("details", {}).get("id", contact.get("id"))
        logger.info(f"Zoho: created new contact {contact_id}")

    result["crm_contact_id"] = str(contact_id)

    # 3. Find or create deal
    deal = await client.find_deal(str(contact_id))
    deal_id = None

    if deal:
        deal_id = deal["id"]
        logger.info(f"Zoho: found existing deal {deal_id}")

        crm_stage = _resolve_stage(deal_data.get("stage", ""), stage_mapping, ZOHO_STAGES)
        if crm_stage:
            await client.update_deal(str(deal_id), {"Stage": crm_stage})
    else:
        deal_name = deal_data.get("name") or f"Memoir: {company or contact_data.get('name', 'New Deal')}"
        crm_stage = _resolve_stage(deal_data.get("stage", ""), stage_mapping, ZOHO_STAGES) or "Qualification"
        deal_props = {
            "Deal_Name": deal_name,
            "Stage": crm_stage,
            "Contact_Name": {"id": contact_id},
        }
        if account_id:
            deal_props["Account_Name"] = {"id": account_id}
        if deal_data.get("amount"):
            deal_props["Amount"] = float(deal_data["amount"]) if str(deal_data["amount"]).replace(".", "").isdigit() else None
            if deal_props["Amount"] is None:
                del deal_props["Amount"]

        deal = await client.create_deal(deal_props)
        deal_id = deal.get("details", {}).get("id", deal.get("id"))
        logger.info(f"Zoho: created new deal {deal_id}")

    if deal_id:
        result["crm_deal_id"] = str(deal_id)

    # 4. Apply custom field mapping
    if field_mapping:
        contact_updates = {}
        deal_updates = {}
        for memoir_field, crm_field in field_mapping.items():
            value = extraction.get(memoir_field, "")
            if isinstance(value, (list, dict)):
                value = str(value)
            if value and crm_field:
                if crm_field.startswith("deal_"):
                    deal_updates[crm_field[5:]] = value
                else:
                    contact_updates[crm_field] = value

        if contact_updates:
            await client.update_contact(str(contact_id), contact_updates)
        if deal_updates and deal_id:
            await client.update_deal(str(deal_id), deal_updates)

    # 5. Create call record
    description = _format_zoho_call_description(extraction, metadata)
    title = metadata.get("title", "Memoir Call")
    prospect_name = contact_data.get("name", "")
    duration_seconds = metadata.get("duration", 0)
    duration_min = f"{int(duration_seconds) // 60}:{int(duration_seconds) % 60:02d}" if duration_seconds else ""

    call_data = {
        "Subject": f"Memoir: {title}" + (f" — {prospect_name}" if prospect_name else ""),
        "Call_Type": "Outbound",
        "Call_Status": "Completed",
        "Description": description,
        "Who_Id": {"id": contact_id},
        "$se_module": "Contacts",
    }
    if deal_id:
        call_data["What_Id"] = {"id": deal_id}
    if metadata.get("timestamp"):
        call_data["Call_Start_Time"] = metadata["timestamp"]
    if duration_min:
        call_data["Call_Duration"] = duration_min

    call_record = await client.create_call_record(call_data)
    call_record_id = call_record.get("details", {}).get("id", call_record.get("id"))
    result["crm_record_id"] = str(call_record_id) if call_record_id else None

    # Build record URL
    result["crm_record_url"] = f"{api_domain.replace('apis', 'crm')}/crm/tab/Contacts/{contact_id}" if contact_id else None

    result["pushed_data"] = {
        "contact": contact_data,
        "deal": deal_data,
        "call_summary": description,
    }

    logger.info(f"Zoho push complete: contact={contact_id}, deal={deal_id}, call={call_record_id}")
    return result
