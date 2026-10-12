"""
Zoho CRM API client — search, create, update contacts/accounts/deals + call records.

Key Zoho differences from HubSpot:
- Auth header: "Zoho-oauthtoken {token}" (not Bearer)
- Region-specific API domain stored at OAuth time
- Separate Account (company) entity
- Native Calls module for call records (instead of Notes)
- API version v6
"""

import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

# Default stage mapping: Memoir stage → Zoho default pipeline stage
DEFAULT_STAGE_MAPPING = {
    "awareness": "Qualification",
    "interest": "Needs Analysis",
    "consideration": "Value Proposition",
    "intent": "Id. Decision Makers",
    "evaluation": "Perception Analysis",
    "purchase": "Closed Won",
}


class ZohoClient:
    def __init__(self, access_token: str, api_domain: str):
        self.token = access_token
        self.base = api_domain.rstrip("/")  # e.g. "https://www.zohoapis.in"
        self.headers = {
            "Authorization": f"Zoho-oauthtoken {access_token}",
            "Content-Type": "application/json",
        }

    # ─── Contacts ─────────────────────────────────────────────────────

    async def find_contact(self, email: str | None = None, first_name: str | None = None, last_name: str | None = None) -> dict | None:
        """Search for a contact by email (preferred) or name."""
        if email:
            criteria = f"(Email:equals:{email})"
        elif first_name and last_name:
            criteria = f"((First_Name:equals:{first_name})and(Last_Name:equals:{last_name}))"
        elif last_name:
            criteria = f"(Last_Name:equals:{last_name})"
        else:
            return None

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base}/crm/v6/Contacts/search",
                params={"criteria": criteria, "fields": "Email,First_Name,Last_Name,Phone,Title,Account_Name"},
                headers=self.headers,
            )
            if resp.status_code == 204:
                return None
            resp.raise_for_status()
            data = resp.json().get("data", [])
            return data[0] if data else None

    async def create_contact(self, data: dict) -> dict:
        """Create a new contact. data keys: Email, First_Name, Last_Name, Phone, Title, Account_Name."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self.base}/crm/v6/Contacts",
                json={"data": [data]},
                headers=self.headers,
            )
            resp.raise_for_status()
            results = resp.json().get("data", [])
            return results[0] if results else {}

    async def update_contact(self, contact_id: str, data: dict) -> dict:
        """Update an existing contact."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                f"{self.base}/crm/v6/Contacts/{contact_id}",
                json={"data": [data]},
                headers=self.headers,
            )
            resp.raise_for_status()
            results = resp.json().get("data", [])
            return results[0] if results else {}

    async def list_recent_contacts(self, limit: int = 200) -> list[dict]:
        """List recently modified contacts (names + emails) for ASR context."""
        contacts = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self.base}/crm/v6/Contacts",
                    params={
                        "fields": "First_Name,Last_Name,Email",
                        "per_page": min(limit, 200),
                        "sort_by": "Modified_Time",
                        "sort_order": "desc",
                    },
                    headers=self.headers,
                )
                if resp.status_code == 204:
                    return []
                resp.raise_for_status()
                for c in resp.json().get("data", []):
                    name = f"{c.get('First_Name', '')} {c.get('Last_Name', '')}".strip()
                    email = c.get("Email", "")
                    if name:
                        contacts.append({"name": name, "email": email})
        except Exception as e:
            logger.warning(f"Zoho list_recent_contacts failed: {e}")
        return contacts

    # ─── Accounts (Companies) ────────────────────────────────────────

    async def find_account(self, company_name: str) -> dict | None:
        """Search for an account by company name."""
        if not company_name:
            return None

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base}/crm/v6/Accounts/search",
                params={"criteria": f"(Account_Name:equals:{company_name})", "fields": "Account_Name,Phone,Website"},
                headers=self.headers,
            )
            if resp.status_code == 204:
                return None
            resp.raise_for_status()
            data = resp.json().get("data", [])
            return data[0] if data else None

    async def create_account(self, data: dict) -> dict:
        """Create a new account. data keys: Account_Name, Phone, Website."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self.base}/crm/v6/Accounts",
                json={"data": [data]},
                headers=self.headers,
            )
            resp.raise_for_status()
            results = resp.json().get("data", [])
            return results[0] if results else {}

    # ─── Deals ────────────────────────────────────────────────────────

    async def find_deal(self, contact_id: str) -> dict | None:
        """Find an open deal associated with a contact."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base}/crm/v6/Deals/search",
                params={
                    "criteria": f"(Contact_Name:equals:{contact_id})",
                    "fields": "Deal_Name,Stage,Amount,Closing_Date,Contact_Name",
                },
                headers=self.headers,
            )
            if resp.status_code == 204:
                return None
            resp.raise_for_status()
            data = resp.json().get("data", [])

        # Return first non-closed-lost deal
        for deal in data:
            if deal.get("Stage") != "Closed Lost":
                return deal
        return None

    async def search_deals(self, query: str = None, company: str = None, email: str = None) -> list[dict]:
        """Search for CRM deals by company name, contact email, or query string."""
        if email:
            # Find contact, then get related deals
            contact = await self.find_contact(email=email)
            if contact:
                return await self._get_deals_for_contact(contact["id"])
            return []

        if company or query:
            search_term = company or query
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self.base}/crm/v6/Deals/search",
                    headers=self.headers,
                    params={
                        "criteria": f"(Deal_Name:contains:{search_term})",
                        "fields": "Deal_Name,Stage,Amount,Closing_Date",
                    },
                )
                if resp.status_code == 204:
                    return []
                if resp.status_code != 200:
                    return []
                data = resp.json().get("data", [])
                return [
                    {
                        "id": d["id"],
                        "name": d.get("Deal_Name"),
                        "stage": d.get("Stage"),
                        "amount": d.get("Amount"),
                    }
                    for d in data[:10]
                ]
        return []

    async def _get_deals_for_contact(self, contact_id: str) -> list[dict]:
        """Get deals associated with a Zoho contact."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base}/crm/v6/Contacts/{contact_id}/Deals",
                headers=self.headers,
            )
            if resp.status_code != 200:
                return []
            data = resp.json().get("data", [])
            return [
                {
                    "id": d["id"],
                    "name": d.get("Deal_Name"),
                    "stage": d.get("Stage"),
                    "amount": d.get("Amount"),
                }
                for d in data[:10]
            ]

    async def create_deal(self, data: dict) -> dict:
        """Create a deal. data keys: Deal_Name, Stage, Amount, Closing_Date, Contact_Name (ID)."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self.base}/crm/v6/Deals",
                json={"data": [data]},
                headers=self.headers,
            )
            resp.raise_for_status()
            results = resp.json().get("data", [])
            return results[0] if results else {}

    async def update_deal(self, deal_id: str, data: dict) -> dict:
        """Update an existing deal."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                f"{self.base}/crm/v6/Deals/{deal_id}",
                json={"data": [data]},
                headers=self.headers,
            )
            resp.raise_for_status()
            results = resp.json().get("data", [])
            return results[0] if results else {}

    # ─── Call Records ────────────────────────────────────────────────

    async def create_call_record(self, data: dict) -> dict:
        """Create a call record in Zoho Calls module.
        data keys: Subject, Call_Type, Call_Start_Time, Call_Duration, Description,
                    Who_Id (contact), What_Id (deal), $se_module (Contacts/Deals).
        """
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self.base}/crm/v6/Calls",
                json={"data": [data]},
                headers=self.headers,
            )
            resp.raise_for_status()
            results = resp.json().get("data", [])
            return results[0] if results else {}

    # ─── Schema / Properties ─────────────────────────────────────────

    async def get_module_fields(self, module: str) -> list[dict]:
        """Get all fields for a module (Contacts, Deals, Accounts, Calls)."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base}/crm/v6/settings/fields",
                params={"module": module},
                headers=self.headers,
            )
            resp.raise_for_status()
            fields = resp.json().get("fields", [])
            return [
                {
                    "name": f["api_name"],
                    "label": f["display_label"],
                    "type": f["data_type"],
                    "read_only": f.get("read_only", False),
                }
                for f in fields
                if not f.get("read_only") and f.get("visible")
            ]

    async def get_deal_stages(self) -> list[dict]:
        """Get deal pipeline stages."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.base}/crm/v6/settings/pipeline",
                params={"module": "Deals"},
                headers=self.headers,
            )
            resp.raise_for_status()
            pipelines = resp.json().get("pipeline", [])

        stages = []
        for pipeline in pipelines:
            pipeline_name = pipeline.get("display_value", "Standard")
            for i, stage in enumerate(pipeline.get("maps", [])):
                stages.append({
                    "id": stage.get("id", stage.get("actual_value", "")),
                    "label": stage.get("display_value", ""),
                    "pipeline": pipeline_name,
                    "display_order": stage.get("sequence_number", i),
                })
        return sorted(stages, key=lambda s: s["display_order"])
