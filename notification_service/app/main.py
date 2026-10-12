"""
Notification Service — minimal email sending via SendGrid.
"""

import logging

from fastapi import FastAPI

from app.core.config import settings
from app.api.email import router as email_router

logging.basicConfig(
    level=logging.DEBUG if settings.is_dev else logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("notification")

app = FastAPI(
    title="Memoir Notification Service",
    version="1.0.0",
    docs_url="/notifications/docs" if settings.is_dev else None,
    redoc_url=None,
)

app.include_router(email_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "notification"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
