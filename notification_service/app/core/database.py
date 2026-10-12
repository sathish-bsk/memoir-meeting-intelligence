"""
Async SQLAlchemy engine and session for notification_service.
Lightweight — used only for reading email_templates and writing email_log.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger("notification")

engine = None
async_session = None


class Base(DeclarativeBase):
    pass


def _init_db():
    """Lazily initialise the DB engine (only if DATABASE_URL is set)."""
    global engine, async_session
    if engine is not None or not settings.DATABASE_URL:
        return
    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=3,
        max_overflow=2,
        pool_pre_ping=True,
        echo=False,
    )
    async_session = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    logger.info("Notification service DB engine initialised")


async def get_session() -> AsyncSession | None:
    """Return a DB session, or None if DATABASE_URL is not configured."""
    _init_db()
    if async_session is None:
        return None
    return async_session()
