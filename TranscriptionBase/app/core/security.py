"""
Security utilities: SSRF protection, URL validation, etc.
"""
import ipaddress
import socket
from urllib.parse import urlparse
from typing import Tuple, Optional
from app.core.config import settings


class SecurityError(Exception):
    """Raised when a security check fails."""
    pass


def is_private_ip(ip: str) -> bool:
    """Check if an IP address is private, loopback, or link-local."""
    try:
        ip_obj = ipaddress.ip_address(ip)
        return (
            ip_obj.is_private or
            ip_obj.is_loopback or
            ip_obj.is_link_local or
            ip_obj.is_multicast or
            ip_obj.is_reserved or
            ip_obj.is_unspecified
        )
    except ValueError:
        return True  # Invalid IP, treat as unsafe


def resolve_and_validate_url(url: str) -> Tuple[str, str]:
    """
    Resolve URL hostname and validate it's not pointing to private/internal IPs.
    Returns (hostname, resolved_ip) if safe.
    Raises SecurityError if URL is unsafe.
    """
    parsed = urlparse(url)

    # Only allow http/https
    if parsed.scheme not in ("http", "https"):
        raise SecurityError(f"Invalid URL scheme: {parsed.scheme}. Only http/https allowed.")

    hostname = parsed.hostname
    if not hostname:
        raise SecurityError("Invalid URL: no hostname found.")

    # Check for IP address directly in URL
    try:
        ip_obj = ipaddress.ip_address(hostname)
        if is_private_ip(str(ip_obj)):
            raise SecurityError(f"Direct IP addresses to private networks are not allowed: {hostname}")
        return hostname, str(ip_obj)
    except ValueError:
        pass  # Not an IP, it's a hostname - continue with DNS resolution

    # Resolve hostname to IP
    try:
        resolved_ip = socket.gethostbyname(hostname)
    except socket.gaierror as e:
        raise SecurityError(f"Failed to resolve hostname: {hostname}") from e

    # Check resolved IP
    if is_private_ip(resolved_ip):
        raise SecurityError(
            f"URL resolves to private/internal IP: {hostname} -> {resolved_ip}"
        )

    return hostname, resolved_ip


def validate_youtube_url(url: str) -> bool:
    """
    Validate that a YouTube URL is from an allowed domain.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname

    if not hostname:
        return False

    # Remove www. prefix for comparison
    hostname_clean = hostname.lower()
    if hostname_clean.startswith("www."):
        hostname_clean = hostname_clean[4:]

    # Check against allowlist
    allowed = [d.lower().replace("www.", "") for d in settings.YOUTUBE_ALLOWED_DOMAINS]
    return hostname_clean in allowed or hostname in settings.YOUTUBE_ALLOWED_DOMAINS


def validate_file_extension(filename: str) -> bool:
    """Validate file has an allowed extension."""
    if not filename:
        return False
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in settings.ALLOWED_MEDIA_EXTENSIONS)


def validate_content_type(content_type: Optional[str]) -> bool:
    """Validate content type is allowed."""
    if not content_type:
        return True  # Some servers don't send content-type
    # Extract main type (ignore charset etc.)
    main_type = content_type.split(";")[0].strip().lower()
    return main_type in settings.ALLOWED_CONTENT_TYPES
