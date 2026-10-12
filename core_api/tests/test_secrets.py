"""Tests for videoclip_common.secrets — pure-logic + env-override path,
no AWS calls.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "videoclip_common"))

from videoclip_common.secrets import (  # noqa: E402
    SecretsFetchError,
    clear_cache,
    fetch_secret_json,
)


@pytest.fixture(autouse=True)
def reset_secret_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture(autouse=True)
def clear_env_override():
    if "MEMOIR_BOT_GOOGLE_SA_JSON" in os.environ:
        del os.environ["MEMOIR_BOT_GOOGLE_SA_JSON"]
    yield


class TestLocalOverrideInline:
    def test_inline_json_string_short_circuits(self):
        payload = {"client_email": "bot@example.iam.gserviceaccount.com", "private_key": "..."}
        os.environ["MEMOIR_BOT_GOOGLE_SA_JSON"] = json.dumps(payload)
        result = fetch_secret_json("arn:will-be-ignored")
        assert result == payload

    def test_inline_with_surrounding_whitespace(self):
        os.environ["MEMOIR_BOT_GOOGLE_SA_JSON"] = "  " + json.dumps({"x": 1}) + "\n"
        assert fetch_secret_json("arn:ignored") == {"x": 1}


class TestLocalOverrideFile:
    def test_file_path_short_circuits(self, tmp_path):
        payload = {"client_email": "bot@example.com"}
        p = tmp_path / "sa.json"
        p.write_text(json.dumps(payload))
        os.environ["MEMOIR_BOT_GOOGLE_SA_JSON"] = str(p)
        assert fetch_secret_json("arn:ignored") == payload


class TestCacheBehavior:
    """When no override is set, the cache layer should mediate fetches.

    We mock boto3 via monkeypatching since we don't want real AWS in tests.
    """

    def test_cache_returns_after_first_fetch(self, monkeypatch):
        call_count = {"n": 0}

        class MockClient:
            def get_secret_value(self, SecretId):
                call_count["n"] += 1
                return {"SecretString": json.dumps({"value": call_count["n"]})}

        class MockBoto3:
            @staticmethod
            def client(name, region_name=None):
                return MockClient()

        monkeypatch.setitem(sys.modules, "boto3", MockBoto3)

        v1 = fetch_secret_json("arn:test")
        v2 = fetch_secret_json("arn:test")
        v3 = fetch_secret_json("arn:test")
        assert v1["value"] == 1
        assert v2["value"] == 1  # cached
        assert v3["value"] == 1  # cached
        assert call_count["n"] == 1

    def test_ttl_expiry_triggers_refetch(self, monkeypatch):
        call_count = {"n": 0}

        class MockClient:
            def get_secret_value(self, SecretId):
                call_count["n"] += 1
                return {"SecretString": json.dumps({"n": call_count["n"]})}

        class MockBoto3:
            @staticmethod
            def client(name, region_name=None):
                return MockClient()

        monkeypatch.setitem(sys.modules, "boto3", MockBoto3)

        # Use a tiny TTL so the second call refetches.
        assert fetch_secret_json("arn:t", ttl_seconds=0)["n"] == 1
        # With TTL=0 every subsequent call should hit boto.
        assert fetch_secret_json("arn:t", ttl_seconds=0)["n"] == 2


class TestErrorPath:
    def test_first_failure_raises(self, monkeypatch):
        class MockClient:
            def get_secret_value(self, SecretId):
                raise RuntimeError("network down")

        class MockBoto3:
            @staticmethod
            def client(name, region_name=None):
                return MockClient()

        monkeypatch.setitem(sys.modules, "boto3", MockBoto3)
        with pytest.raises(SecretsFetchError):
            fetch_secret_json("arn:fail")

    def test_failure_falls_back_to_stale_cache(self, monkeypatch):
        call_count = {"n": 0}
        fail_after = {"n": False}

        class MockClient:
            def get_secret_value(self, SecretId):
                call_count["n"] += 1
                if fail_after["n"]:
                    raise RuntimeError("network down")
                return {"SecretString": json.dumps({"n": call_count["n"]})}

        class MockBoto3:
            @staticmethod
            def client(name, region_name=None):
                return MockClient()

        monkeypatch.setitem(sys.modules, "boto3", MockBoto3)

        # Prime cache with a successful fetch.
        first = fetch_secret_json("arn:c", ttl_seconds=0)
        assert first["n"] == 1

        # Now make fetches fail. With ttl=0, the wrapper re-attempts and
        # falls back to the cached value when the underlying call raises.
        fail_after["n"] = True
        fallback = fetch_secret_json("arn:c", ttl_seconds=0)
        assert fallback["n"] == 1
