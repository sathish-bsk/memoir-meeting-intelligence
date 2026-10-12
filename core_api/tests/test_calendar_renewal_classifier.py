"""Unit tests for the channel-renewal failure classifier in
calendar_sync.py.

Mirrors the test layout for calendar_token_refresher's classifier —
exercises the pure-function classification logic against the shapes
of httpx errors we expect from Google and Microsoft.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.calendar_sync import (  # noqa: E402
    _classify_renewal_error,
    _RenewalPermanent,
    _RenewalTransient,
)


def _httpx_status(status: int, body: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.com/x")
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.json = MagicMock(return_value=body or {})
    return httpx.HTTPStatusError(
        message=f"HTTP {status}", request=request, response=response
    )


# ── Network-level failures: always transient ─────────────────────────


@pytest.mark.parametrize(
    "err_cls",
    [httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError],
)
def test_network_errors_classified_transient(err_cls):
    result = _classify_renewal_error("google", err_cls("boom"))
    assert isinstance(result, _RenewalTransient)
    assert "network" in result.reason


# ── 5xx / 429 from provider: transient ───────────────────────────────


@pytest.mark.parametrize("status", [500, 502, 503, 504, 429])
def test_provider_5xx_and_429_classified_transient(status):
    err = _httpx_status(status)
    result = _classify_renewal_error("google", err)
    assert isinstance(result, _RenewalTransient)
    assert f"http_{status}" in result.reason


# ── Permanent OAuth/grant errors → permanent ─────────────────────────


@pytest.mark.parametrize(
    "code",
    ["invalid_grant", "unauthorized_client", "invalid_client", "forbidden"],
)
def test_google_permanent_error_codes(code):
    err = _httpx_status(400, body={"error": code})
    result = _classify_renewal_error("google", err)
    assert isinstance(result, _RenewalPermanent)
    assert result.reason == code


def test_google_channel_id_corruption_codes():
    """channelIdNotUnique / watchAlreadyExists indicate corrupted
    channel state — Google won't accept a new registration, so we
    have to mark the connection invalid and force a reconnect to
    rebuild the channel pairing."""
    for code in ("channelIdNotUnique", "watchAlreadyExists"):
        err = _httpx_status(400, body={"error": code})
        result = _classify_renewal_error("google", err)
        assert isinstance(result, _RenewalPermanent)


@pytest.mark.parametrize(
    "code",
    ["invalid_grant", "unauthorized_client", "AADSTS70008", "AADSTS50105"],
)
def test_outlook_permanent_error_codes(code):
    err = _httpx_status(400, body={"error": code})
    result = _classify_renewal_error("outlook", err)
    assert isinstance(result, _RenewalPermanent)
    assert result.reason == code


def test_outlook_nested_error_code_in_dict():
    """Microsoft Graph sometimes returns {"error": {"code": "..."}}
    instead of {"error": "..."}. Classifier handles both shapes."""
    err = _httpx_status(403, body={"error": {"code": "AADSTS70008"}})
    result = _classify_renewal_error("outlook", err)
    assert isinstance(result, _RenewalPermanent)


def test_outlook_error_code_field():
    """Sometimes the code lives at top-level error_code rather than error."""
    err = _httpx_status(400, body={"error_code": "AADSTS70008"})
    result = _classify_renewal_error("outlook", err)
    assert isinstance(result, _RenewalPermanent)


# ── Unknown 4xx → transient (cap will catch persistent ones) ─────────


def test_unknown_4xx_error_code_classified_transient():
    err = _httpx_status(400, body={"error": "some_brand_new_google_quirk"})
    result = _classify_renewal_error("google", err)
    assert isinstance(result, _RenewalTransient)


def test_google_permanent_code_not_outlook():
    """forbidden is in Google's permanent set but not Outlook's. Test
    cross-provider safety — a Google-specific code shouldn't trigger
    permanent for an Outlook connection."""
    err = _httpx_status(400, body={"error": "forbidden"})
    result_g = _classify_renewal_error("google", err)
    result_o = _classify_renewal_error("outlook", err)
    assert isinstance(result_g, _RenewalPermanent)
    # Outlook doesn't have "forbidden" in its permanent set
    assert isinstance(result_o, _RenewalTransient)


# ── Missing refresh_token → permanent ────────────────────────────────


def test_missing_refresh_token_classified_permanent():
    err = ValueError("No refresh token available")
    result = _classify_renewal_error("google", err)
    assert isinstance(result, _RenewalPermanent)
    assert result.reason == "no_refresh_token"


def test_missing_refresh_token_underscore_variant():
    err = ValueError("Missing refresh_token")
    result = _classify_renewal_error("google", err)
    assert isinstance(result, _RenewalPermanent)


# ── Unknown exceptions → transient ───────────────────────────────────


def test_unknown_exception_classified_transient():
    err = RuntimeError("totally unexpected")
    result = _classify_renewal_error("google", err)
    assert isinstance(result, _RenewalTransient)
    assert "RuntimeError" in result.reason
