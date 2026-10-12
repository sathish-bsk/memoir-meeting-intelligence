"""
Common schema types shared across modules.
"""

from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel


T = TypeVar("T")


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None


class MessageResponse(BaseModel):
    message: str


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int
    page_size: int
    has_next: bool
