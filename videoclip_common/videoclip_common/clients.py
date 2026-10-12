"""
Typed async HTTP clients for inter-service communication.
All clients send X-Service-Key for service auth and X-Forwarded-User for user context.
"""

import os
from typing import Any, Optional

import httpx


class ServiceClient:
    """Base HTTP client for inter-service calls."""

    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self, user_id: Optional[str] = None) -> dict:
        headers = {"Content-Type": "application/json"}
        service_key = os.getenv("INTERNAL_SERVICE_KEY", "")
        if service_key:
            headers["X-Service-Key"] = service_key
        if user_id:
            headers["X-Forwarded-User"] = user_id
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        user_id: Optional[str] = None,
        **kwargs,
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await client.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(user_id),
                **kwargs,
            )

    async def get(self, path: str, user_id: Optional[str] = None, **kwargs) -> httpx.Response:
        return await self._request("GET", path, user_id, **kwargs)

    async def post(self, path: str, user_id: Optional[str] = None, **kwargs) -> httpx.Response:
        return await self._request("POST", path, user_id, **kwargs)

    async def patch(self, path: str, user_id: Optional[str] = None, **kwargs) -> httpx.Response:
        return await self._request("PATCH", path, user_id, **kwargs)

    async def delete(self, path: str, user_id: Optional[str] = None, **kwargs) -> httpx.Response:
        return await self._request("DELETE", path, user_id, **kwargs)


class AuthClient(ServiceClient):
    """Client for Auth Service."""

    def __init__(self, base_url: Optional[str] = None):
        super().__init__(base_url or os.getenv("AUTH_SERVICE_URL", "http://localhost:8001"))


class BillingClient(ServiceClient):
    """Client for Billing Service."""

    def __init__(self, base_url: Optional[str] = None):
        super().__init__(base_url or os.getenv("BILLING_SERVICE_URL", "http://localhost:8003"))

    async def check_plan_limits(self, user_id: str) -> dict:
        resp = await self.get(f"/billing/internal/plan-limits/{user_id}", user_id=user_id)
        resp.raise_for_status()
        return resp.json()


class NotificationClient(ServiceClient):
    """Client for Notification Service."""

    def __init__(self, base_url: Optional[str] = None):
        super().__init__(base_url or os.getenv("NOTIFICATION_SERVICE_URL", "http://localhost:8002"))

    async def send(self, user_id: str, notification_type: str, data: dict) -> dict:
        resp = await self.post(
            "/notifications/internal/send",
            user_id=user_id,
            json={"user_id": user_id, "type": notification_type, "data": data},
        )
        resp.raise_for_status()
        return resp.json()


class CRMClient(ServiceClient):
    """Client for CRM Integration Service."""

    def __init__(self, base_url: Optional[str] = None):
        super().__init__(base_url or os.getenv("CRM_SERVICE_URL", "http://localhost:8004"))

    async def push_call(self, meeting_id: str, memoir_data: dict) -> dict:
        resp = await self.post(
            "/crm/internal/push-call",
            json={"meeting_id": meeting_id, "memoir_data": memoir_data},
        )
        resp.raise_for_status()
        return resp.json()


class OrchestratorClient(ServiceClient):
    """Client for Bot Orchestrator."""

    def __init__(self, base_url: Optional[str] = None):
        super().__init__(base_url or os.getenv("ORCHESTRATOR_URL", "http://localhost:4000"))

    async def create_bot(
        self,
        meeting_url: str,
        callback_url: Optional[str] = None,
        meeting_id: Optional[str] = None,
    ) -> dict:
        payload: dict[str, Any] = {"meetingUrl": meeting_url}
        if callback_url:
            payload["callbackUrl"] = callback_url
        if meeting_id:
            payload["meetingId"] = str(meeting_id)
        resp = await self.post("/api/bots", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def stop_bot(self, job_id: str) -> dict:
        resp = await self.delete(f"/api/bots/{job_id}")
        resp.raise_for_status()
        return resp.json()
