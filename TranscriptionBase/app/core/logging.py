"""
Logging configuration for the application.
"""
import logging
import sys
from app.core.config import settings


class RequestIdFilter(logging.Filter):
    """Filter that adds request_id to log records if not present."""

    def filter(self, record):
        if not hasattr(record, 'request_id'):
            record.request_id = 'no-request'
        return True


def setup_logging() -> logging.Logger:
    """Configure and return the application logger."""
    logger = logging.getLogger("transcription")
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)

        # Add filter to ensure request_id is always present
        handler.addFilter(RequestIdFilter())

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


class RequestLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter that includes request_id in all log messages."""

    def process(self, msg, kwargs):
        kwargs.setdefault("extra", {})
        kwargs["extra"]["request_id"] = self.extra.get("request_id", "no-request")
        return msg, kwargs


def get_request_logger(request_id: str) -> RequestLoggerAdapter:
    """Get a logger adapter with request_id context."""
    logger = logging.getLogger("transcription")
    return RequestLoggerAdapter(logger, {"request_id": request_id})


# Initialize logger at module load
app_logger = setup_logging()
