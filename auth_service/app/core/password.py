"""
Password hashing (Argon2id) and validation (NIST 800-63B).
"""

import hashlib
from typing import Optional

import httpx
from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings
from app.core.logging import app_logger


# Argon2id hasher with recommended parameters
_hasher = PasswordHasher(
    time_cost=3,        # iterations
    memory_cost=65536,  # 64 MB
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,       # Argon2id
)


# ─── Hashing ──────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash a password using Argon2id."""
    return _hasher.hash(password)


def verify_password(password: str, hash: str) -> bool:
    """Verify a password against an Argon2id hash."""
    try:
        return _hasher.verify(hash, password)
    except VerifyMismatchError:
        return False


def needs_rehash(hash: str) -> bool:
    """Check if a password hash needs to be re-hashed with current params."""
    return _hasher.check_needs_rehash(hash)


# ─── NIST 800-63B Password Validation ────────────────────────────

# Common passwords — top 100 most used (abbreviated list, extend as needed)
_COMMON_PASSWORDS = {
    "password", "123456", "12345678", "qwerty", "abc123", "monkey", "1234567",
    "letmein", "trustno1", "dragon", "baseball", "iloveyou", "master", "sunshine",
    "ashley", "bailey", "passw0rd", "shadow", "123456789", "1234567890",
    "password1", "password123", "admin", "welcome", "login", "princess",
    "admin123", "football", "!@#$%^&*", "charlie", "donald", "password1!",
}


def validate_password(
    password: str,
    email: Optional[str] = None,
    name: Optional[str] = None,
) -> list[str]:
    """Validate password per NIST 800-63B. Returns list of error messages (empty = valid)."""
    errors = []

    # Minimum length
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        errors.append(
            f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters"
        )

    # Check against common passwords
    if password.lower() in _COMMON_PASSWORDS:
        errors.append("This password is too common")

    # Check against user context (email, name)
    if email and email.split("@")[0].lower() in password.lower():
        errors.append("Password should not contain your email address")
    if name and len(name) > 2 and name.lower() in password.lower():
        errors.append("Password should not contain your name")

    return errors


async def check_hibp(password: str) -> bool:
    """Check if password appears in HaveIBeenPwned database using k-anonymity.

    Returns True if the password is compromised, False if safe.
    """
    if not settings.HIBP_CHECK_ENABLED:
        return False

    sha1 = hashlib.sha1(password.encode()).hexdigest().upper()
    prefix = sha1[:5]
    suffix = sha1[5:]

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"https://api.pwnedpasswords.com/range/{prefix}",
                headers={"Add-Padding": "true"},
            )
            if response.status_code != 200:
                # Fail open — don't block registration if HIBP is down
                return False

            for line in response.text.splitlines():
                hash_suffix, count = line.strip().split(":")
                if hash_suffix == suffix:
                    return True
    except Exception as e:
        app_logger.warning(f"HIBP check failed: {e}")
        # Fail open
        return False

    return False
