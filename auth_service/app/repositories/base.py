"""
Base repository with common CRUD operations.
All DB access goes through repository classes — services never touch ORM directly.
This makes it easy to swap databases in the future.
"""

import uuid
from typing import Generic, List, Optional, Type, TypeVar

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base


ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Generic repository providing common CRUD operations."""

    def __init__(self, model: Type[ModelType], db: AsyncSession):
        self.model = model
        self.db = db

    async def get_by_id(self, id: uuid.UUID) -> Optional[ModelType]:
        result = await self.db.execute(
            select(self.model).where(self.model.id == id)
        )
        return result.scalar_one_or_none()

    async def create(self, entity: ModelType) -> ModelType:
        self.db.add(entity)
        await self.db.flush()
        return entity

    async def update(self, entity: ModelType) -> ModelType:
        await self.db.flush()
        return entity

    async def delete(self, entity: ModelType) -> None:
        await self.db.delete(entity)
        await self.db.flush()

    async def count(self) -> int:
        result = await self.db.execute(select(func.count()).select_from(self.model))
        return result.scalar_one()

    async def commit(self):
        await self.db.commit()

    async def rollback(self):
        await self.db.rollback()
