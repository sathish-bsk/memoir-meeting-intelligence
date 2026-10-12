"""Unit tests for BotWorker.app.services.auth_session — pure-function
parts that don't need a real browser.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _import_auth_session(monkeypatch=None, auth_mode="guest"):
    """Helper to re-import with a fresh settings module if needed.

    We patch the module-level settings object so we can vary
    MEMOIR_BOT_AUTH_MODE per test.
    """
    from app.services import auth_session
    if monkeypatch is not None:
        monkeypatch.setattr(auth_session.settings, "MEMOIR_BOT_AUTH_MODE", auth_mode)
    return auth_session


class TestProviderFromUrl:
    def test_google_meet(self):
        mod = _import_auth_session()
        assert mod.provider_from_url("https://meet.google.com/abc-defg-hij") == "google"

    def test_google_meet_uppercase(self):
        mod = _import_auth_session()
        assert mod.provider_from_url("https://MEET.GOOGLE.COM/abc") == "google"

    def test_teams_microsoft(self):
        mod = _import_auth_session()
        assert (
            mod.provider_from_url(
                "https://teams.microsoft.com/l/meetup-join/19%3a..."
            )
            == "microsoft"
        )

    def test_teams_live(self):
        mod = _import_auth_session()
        assert (
            mod.provider_from_url("https://teams.live.com/meet/12345")
            == "microsoft"
        )

    def test_zoom_returns_none(self):
        mod = _import_auth_session()
        assert mod.provider_from_url("https://us02web.zoom.us/j/12345") is None

    def test_whereby_returns_none(self):
        mod = _import_auth_session()
        assert mod.provider_from_url("https://whereby.com/room") is None

    def test_empty_returns_none(self):
        mod = _import_auth_session()
        assert mod.provider_from_url("") is None
        assert mod.provider_from_url(None) is None  # type: ignore[arg-type]


class TestShouldUseAuthenticatedSession:
    def test_off_by_default(self, monkeypatch):
        mod = _import_auth_session(monkeypatch, auth_mode="guest")
        assert mod.should_use_authenticated_session("https://meet.google.com/abc") is False

    def test_authenticated_mode_with_google(self, monkeypatch):
        mod = _import_auth_session(monkeypatch, auth_mode="authenticated")
        assert mod.should_use_authenticated_session("https://meet.google.com/abc") is True

    def test_authenticated_mode_with_teams(self, monkeypatch):
        mod = _import_auth_session(monkeypatch, auth_mode="authenticated")
        assert (
            mod.should_use_authenticated_session(
                "https://teams.microsoft.com/l/meetup-join/abc"
            )
            is True
        )

    def test_authenticated_mode_with_zoom_falls_back(self, monkeypatch):
        """Even with auth-mode on, Zoom doesn't support our auth path."""
        mod = _import_auth_session(monkeypatch, auth_mode="authenticated")
        assert (
            mod.should_use_authenticated_session("https://us02web.zoom.us/j/x")
            is False
        )

    def test_authenticated_mode_with_unknown_url(self, monkeypatch):
        mod = _import_auth_session(monkeypatch, auth_mode="authenticated")
        assert mod.should_use_authenticated_session("https://whereby.com/x") is False


class TestProfileKeyConfig:
    """Verify _profile_key_for returns None when env vars aren't set —
    that's the signal that fallback-to-guest should fire.
    """

    def test_no_key_configured(self, monkeypatch):
        from app.services import auth_session
        monkeypatch.setattr(auth_session.settings, "MEMOIR_BOT_GOOGLE_PROFILE_KEY", "")
        monkeypatch.setattr(auth_session.settings, "MEMOIR_BOT_MICROSOFT_PROFILE_KEY", "")
        assert auth_session._profile_key_for("google") is None
        assert auth_session._profile_key_for("microsoft") is None

    def test_google_key_present(self, monkeypatch):
        from app.services import auth_session
        monkeypatch.setattr(
            auth_session.settings, "MEMOIR_BOT_GOOGLE_PROFILE_KEY", "bot-profiles/google-cookies.json"
        )
        assert auth_session._profile_key_for("google") == "bot-profiles/google-cookies.json"
