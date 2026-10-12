"""
Common middleware for all VideoClip services.
Request ID propagation and structured error responses.
"""

import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Propagate or generate X-Request-ID on every request/response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
