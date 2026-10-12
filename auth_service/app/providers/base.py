"""
Abstract OAuth provider interface.
Each provider (Google, Microsoft) implements this.
"""

from abc import ABC, abstractmethod
from typing import Tuple


class OAuthProvider(ABC):
    """Base class for OAuth providers."""

    @abstractmethod
    async def get_authorize_url(self) -> Tuple[str, str]:
        """Generate authorization URL and state.

        Returns:
            (redirect_url, state) — state is for CSRF protection
        """
        ...

    @abstractmethod
    async def exchange_code(self, code: str) -> dict:
        """Exchange authorization code for user info.

        Returns dict with:
            id: str               — provider's user ID
            email: str            — user's email
            name: str             — user's display name
            email_verified: bool  — whether provider has verified the email
            raw_profile: dict     — full provider response (for storage)
        """
        ...
