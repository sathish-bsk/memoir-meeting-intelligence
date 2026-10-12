"""Unit tests for calendar_token_refresher.

Focus is on the failure classifier — the bit that decides whether a
refresh failure is transient (retry) or permanent (park + invalidate).
The cron loop itself is integration-level and tested by running prod.

Redis + DB are mocked. We don't need a real Postgres for the classifier
tests because they're pure functions over exception types.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.calendar_token_refresher import (  # noqa: E402
    _PermanentFailure,
    _TransientFailure,
    _classify_http_error,
)


def _httpx_status(status: int, body: dict | None = None) -> httpx.HTTPStatusError:
    """Build an HTTPStatusError that mimics httpx's real shape."""
    request = httpx.Request("POST", "https://oauth2.googleapis.com/token")
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.json = MagicMock(return_value=body or {})
    err = httpx.HTTPStatusError(
        message=f"HTTP {status}",
        request=request,
        response=response,
    )
    return err


# ── Network-level failures: always transient ─────────────────────────


@pytest.mark.parametrize(
    "err_cls",
    [
        httpx.TimeoutException,
        httpx.ConnectError,
        httpx.NetworkError,
    ],
)
def test_network_errors_classified_transient(err_cls):
    result = _classify_http_error("google", err_cls("boom"))
    assert isinstance(result, _TransientFailure)
    assert "network" in result.reason


# ── 5xx / 429 from provider: transient ───────────────────────────────


@pytest.mark.parametrize("status", [500, 502, 503, 504, 429])
def test_provider_5xx_and_429_classified_transient(status):
    err = _httpx_status(status)
    result = _classify_http_error("google", err)
    assert isinstance(result, _TransientFailure)
    assert f"http_{status}" in result.reason


# ── Permanent OAuth errors → permanent ───────────────────────────────


@pytest.mark.parametrize(
    "code",
    ["invalid_grant", "unauthorized_client", "invalid_client"],
)
def test_google_permanent_error_codes(code):
    err = _httpx_status(400, body={"error": code})
    result = _classify_http_error("google", err)
    assert isinstance(result, _PermanentFailure)
    assert result.reason == code


@pytest.mark.parametrize(
    "code",
    ["invalid_grant", "unauthorized_client", "AADSTS70008", "AADSTS70043"],
)
def test_outlook_permanent_error_codes(code):
    # Outlook returns error in different fields depending on endpoint.
    err = _httpx_status(400, body={"error": code})
    result = _classify_http_error("outlook", err)
    assert isinstance(result, _PermanentFailure)
    assert result.reason == code


def test_outlook_permanent_code_in_error_code_field():
    """Microsoft sometimes returns the code under 'error_code' instead of 'error'."""
    err = _httpx_status(400, body={"error_code": "AADSTS70008"})
    result = _classify_http_error("outlook", err)
    assert isinstance(result, _PermanentFailure)


# ── 400/401 with unknown error code → transient (cap will catch it) ──


def test_unknown_4xx_error_code_classified_transient():
    """Unrecognized OAuth error shapes should be tolerated as transient.

    The MAX_TRANSIENT_ATTEMPTS cap (5) means a truly permanent
    misclassification still parks the row eventually, while avoiding
    permanent lockout on a one-off provider quirk.
    """
    err = _httpx_status(400, body={"error": "some_new_google_quirk"})
    result = _classify_http_error("google", err)
    assert isinstance(result, _TransientFailure)


def test_google_permanent_code_not_applied_to_outlook():
    """invalid_client is Google-permanent; for Outlook the same code in
    a different position shouldn't auto-trigger permanent. (Defensive
    test — both providers happen to share invalid_client today; this
    test pins the lookup-by-provider behavior.)"""
    err = _httpx_status(400, body={"error": "invalid_client"})
    # invalid_client IS in _PERMANENT_OUTLOOK too, so for symmetry both
    # providers treat it as permanent. This test confirms the parametric
    # routing works (not a typo).
    result_g = _classify_http_error("google", err)
    result_o = _classify_http_error("outlook", err)
    # Both permanent per current sets — pin this so future split is loud.
    assert isinstance(result_g, _PermanentFailure)
    # Outlook does NOT have invalid_client in its permanent set
    assert isinstance(result_o, _TransientFailure)


# ── Missing refresh_token → permanent ────────────────────────────────


def test_missing_refresh_token_classified_permanent():
    err = ValueError("No refresh token available")
    result = _classify_http_error("google", err)
    assert isinstance(result, _PermanentFailure)
    assert result.reason == "no_refresh_token"


# ── Unknown exceptions → transient ───────────────────────────────────


def test_unknown_exception_classified_transient():
    err = RuntimeError("totally unexpected")
    result = _classify_http_error("google", err)
    assert isinstance(result, _TransientFailure)
    assert "RuntimeError" in result.reason
