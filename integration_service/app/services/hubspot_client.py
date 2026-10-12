"""
HubSpot CRM API client — search, create, update contacts/deals/notes.
All calls use Bearer token auth and https://api.hubapi.com base URL.
"""

import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

HUBSPOT_API = "https://api.hubapi.com"

# Default stage mapping: Memoir stage → HubSpot default pipeline stage ID
DEFAULT_STAGE_MAPPING = {
    "awareness": "appointmentscheduled",
    "interest": "qualifiedtobuy",
    "consideration": "presentationscheduled",
    "intent": "decisionmakerboughtin",
    "evaluation": "contractsent",
    "purchase": "closedwon",
}


class HubSpotClient:
    def __init__(self, access_token: str):
        self.token = access_token
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    # ─── Contacts ─────────────────────────────────────────────────────

    async def find_contact(self, email: str | None = None, name: str | None = None, company: str | None = None) -> dict | None:
        """Search for a contact by email (preferred) or name+company."""
        if email:
            filters = [{"propertyName": "email", "operator": "EQ", "value": email}]
        elif name:
            filters = [{"propertyName": "firstname", "operator": "CONTAINS_TOKEN", "value": name.split()[0]}]
            if company:
                filters.append({"propertyName": "company", "operator": "CONTAINS_TOKEN", "value": company})
        else:
            return None

        payload = {
            "filterGroups": [{"filters": filters}],
            "properties": ["email", "firstname", "lastname", "company", "jobtitle", "phone"],
            "limit": 1,
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{HUBSPOT_API}/crm/v3/objects/contacts/search",
                json=payload,
                headers=self.headers,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            return results[0] if results else None

    async def create_contact(self, data: dict) -> dict:
        """Create a new contact. data = {email, firstname, lastname, company, jobtitle, phone}."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{HUBSPOT_API}/crm/v3/objects/contacts",
                json={"properties": data},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def update_contact(self, contact_id: str, data: dict) -> dict:
        """Update an existing contact."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.patch(
                f"{HUBSPOT_API}/crm/v3/objects/contacts/{contact_id}",
                json={"properties": data},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    # ─── Deals ────────────────────────────────────────────────────────

    async def list_recent_contacts(self, limit: int = 200) -> list[dict]:
        """List recently updated contacts (names + emails) for ASR context."""
        contacts = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{HUBSPOT_API}/crm/v3/objects/contacts",
                    params={
                        "limit": min(limit, 100),
                        "properties": "firstname,lastname,email",
                        "sorts": "-lastmodifieddate",
                    },
                    headers=self.headers,
                )
                resp.raise_for_status()
                for c in resp.json().get("results", []):
                    props = c.get("properties", {})
                    name = f"{props.get('firstname', '')} {props.get('lastname', '')}".strip()
                    email = props.get("email", "")
                    if name:
                        contacts.append({"name": name, "email": email})
        except Exception as e:
            logger.warning(f"HubSpot list_recent_contacts failed: {e}")
        return contacts

    async def find_deal(self, contact_id: str) -> dict | None:
        """Find an open deal associated with a contact."""
        # First get associated deal IDs
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{HUBSPOT_API}/crm/v4/objects/contacts/{contact_id}/associations/deals",
                headers=self.headers,
            )
            resp.raise_for_status()
            associations = resp.json().get("results", [])

        if not associations:
            return None

        # Get the first associated deal
        deal_id = associations[0].get("toObjectId")
        if not deal_id:
            return None

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{HUBSPOT_API}/crm/v3/objects/deals/{deal_id}",
                params={"properties": "dealname,dealstage,amount,closedate,pipeline"},
                headers=self.headers,
            )
            resp.raise_for_status()
            deal = resp.json()

        # Only return if not closed-lost
        stage = deal.get("properties", {}).get("dealstage", "")
        if stage == "closedlost":
            return None
        return deal

    async def search_deals(self, query: str = None, company: str = None, email: str = None) -> list[dict]:
        """Search for CRM deals by company name, contact email, or query string."""
        if email:
            # Find contact first, then get associated deals
            contact = await self.find_contact(email=email)
            if contact:
                return await self._get_deals_for_contact(contact["id"])
            return []

        if company or query:
            search_term = company or query
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{HUBSPOT_API}/crm/v3/objects/deals/search",
                    headers=self.headers,
                    json={
                        "filterGroups": [{
                            "filters": [{
                                "propertyName": "dealname",
                                "operator": "CONTAINS_TOKEN",
                                "value": search_term,
                            }]
                        }],
                        "properties": ["dealname", "dealstage", "amount", "closedate", "pipeline"],
                        "limit": 10,
                    },
                )
                if resp.status_code != 200:
                    return []
                results = resp.json().get("results", [])
                return [
                    {
                        "id": r["id"],
                        "name": r.get("properties", {}).get("dealname"),
                        "stage": r.get("properties", {}).get("dealstage"),
                        "amount": r.get("properties", {}).get("amount"),
                        "pipeline": r.get("properties", {}).get("pipeline"),
                    }
                    for r in results
                ]
        return []

    async def _get_deals_for_contact(self, contact_id: str) -> list[dict]:
        """Get deals associated with a HubSpot contact."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{HUBSPOT_API}/crm/v4/objects/contacts/{contact_id}/associations/deals",
                headers=self.headers,
            )
            if resp.status_code != 200:
                return []
            associations = resp.json().get("results", [])

            deals = []
            for assoc in associations[:10]:
                deal_id = assoc.get("toObjectId")
                if not deal_id:
                    continue
                deal_resp = await client.get(
                    f"{HUBSPOT_API}/crm/v3/objects/deals/{deal_id}",
                    params={"properties": "dealname,dealstage,amount,closedate,pipeline"},
                    headers=self.headers,
                )
                if deal_resp.status_code == 200:
                    d = deal_resp.json()
                    deals.append({
                        "id": d["id"],
                        "name": d.get("properties", {}).get("dealname"),
                        "stage": d.get("properties", {}).get("dealstage"),
                        "amount": d.get("properties", {}).get("amount"),
                        "pipeline": d.get("properties", {}).get("pipeline"),
                    })
            return deals

    async def create_deal(self, data: dict, contact_id: str) -> dict:
        """Create a deal and associate with a contact."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{HUBSPOT_API}/crm/v3/objects/deals",
                json={"properties": data},
                headers=self.headers,
            )
            resp.raise_for_status()
            deal = resp.json()

        deal_id = deal["id"]

        # Associate deal with contact
        async with httpx.AsyncClient(timeout=15) as client:
            await client.put(
                f"{HUBSPOT_API}/crm/v4/objects/deals/{deal_id}/associations/contacts/{contact_id}",
                json=[{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 3}],
                headers=self.headers,
            )

        return deal

    async def update_deal(self, deal_id: str, data: dict) -> dict:
        """Update an existing deal."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.patch(
                f"{HUBSPOT_API}/crm/v3/objects/deals/{deal_id}",
                json={"properties": data},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    # ─── Notes ────────────────────────────────────────────────────────

    async def create_note(self, body: str, contact_id: str, deal_id: str | None = None, timestamp: str | None = None) -> dict:
        """Create an engagement note associated with contact (and optionally deal)."""
        ts = timestamp or datetime.now(timezone.utc).isoformat()

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{HUBSPOT_API}/crm/v3/objects/notes",
                json={
                    "properties": {
                        "hs_note_body": body,
                        "hs_timestamp": ts,
                    },
                },
                headers=self.headers,
            )
            resp.raise_for_status()
            note = resp.json()

        note_id = note["id"]

        # Associate with contact
        async with httpx.AsyncClient(timeout=15) as client:
            await client.put(
                f"{HUBSPOT_API}/crm/v4/objects/notes/{note_id}/associations/contacts/{contact_id}",
                json=[{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}],
                headers=self.headers,
            )

        # Associate with deal if provided
        if deal_id:
            async with httpx.AsyncClient(timeout=15) as client:
                await client.put(
                    f"{HUBSPOT_API}/crm/v4/objects/notes/{note_id}/associations/deals/{deal_id}",
                    json=[{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 214}],
                    headers=self.headers,
                )

        return note

    # ─── Schema / Properties ─────────────────────────────────────────

    async def get_contact_properties(self) -> list[dict]:
        """Get all contact properties for field mapping UI."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{HUBSPOT_API}/crm/v3/properties/contacts",
                headers=self.headers,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            return [
                {"name": p["name"], "label": p["label"], "type": p["type"], "group": p.get("groupName", "")}
                for p in results
                if not p.get("hidden") and not p.get("calculated")
            ]

    async def get_deal_properties(self) -> list[dict]:
        """Get all deal properties for field mapping UI."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{HUBSPOT_API}/crm/v3/properties/deals",
                headers=self.headers,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            return [
                {"name": p["name"], "label": p["label"], "type": p["type"], "group": p.get("groupName", "")}
                for p in results
                if not p.get("hidden") and not p.get("calculated")
            ]

    async def get_pipeline_stages(self) -> list[dict]:
        """Get deal pipeline stages for stage mapping UI."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{HUBSPOT_API}/crm/v3/pipelines/deals",
                headers=self.headers,
            )
            resp.raise_for_status()
            pipelines = resp.json().get("results", [])

        stages = []
        for pipeline in pipelines:
            pipeline_label = pipeline.get("label", "Default")
            for stage in pipeline.get("stages", []):
                stages.append({
                    "id": stage["id"],
                    "label": stage["label"],
                    "pipeline": pipeline_label,
                    "display_order": stage.get("displayOrder", 0),
                })
        return sorted(stages, key=lambda s: s["display_order"])
