"""
Logging configuration with job-scoped structured logging.
"""
import logging
import sys
from app.core.config import settings


class JobIdFilter(logging.Filter):
    """Filter that adds job_id to log records if not present."""

    def filter(self, record):
        if not hasattr(record, "job_id"):
            record.job_id = "no-job"
        return True


def setup_logging() -> logging.Logger:
    """Configure and return the application logger."""
    logger = logging.getLogger("botworker")
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        handler.addFilter(JobIdFilter())

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - [%(job_id)s] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


class JobLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter that includes job_id in all log messages."""

    def process(self, msg, kwargs):
        kwargs.setdefault("extra", {})
        kwargs["extra"]["job_id"] = self.extra.get("job_id", "no-job")
        return msg, kwargs


def get_job_logger(job_id: str) -> JobLoggerAdapter:
    """Get a logger adapter with job_id context."""
    logger = logging.getLogger("botworker")
    return JobLoggerAdapter(logger, {"job_id": job_id})


# Initialize logger at module load
app_logger = setup_logging()
