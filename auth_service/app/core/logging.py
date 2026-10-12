"""
Logging configuration with request-scoped context.
Matches BotWorker/TranscriptionBase pattern.
"""

import logging
import sys

from app.core.config import settings


class RequestIdFilter(logging.Filter):
    """Adds request_id to log records if not present."""

    def filter(self, record):
        if not hasattr(record, "request_id"):
            record.request_id = "no-request"
        return True


def setup_logging() -> logging.Logger:
    """Configure and return the auth service logger."""
    logger = logging.getLogger("auth_service")
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        handler.addFilter(RequestIdFilter())

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


class RequestLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter that injects request_id into all messages."""

    def process(self, msg, kwargs):
        kwargs.setdefault("extra", {})
        kwargs["extra"]["request_id"] = self.extra.get("request_id", "no-request")
        return msg, kwargs


def get_request_logger(request_id: str) -> RequestLoggerAdapter:
    """Get a logger scoped to a specific request."""
    logger = logging.getLogger("auth_service")
    return RequestLoggerAdapter(logger, {"request_id": request_id})


app_logger = setup_logging()
