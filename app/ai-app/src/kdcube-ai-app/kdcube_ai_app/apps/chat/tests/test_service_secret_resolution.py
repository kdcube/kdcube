# SPDX-License-Identifier: MIT

"""
Unit tests for get_service_secret / get_service_secret_async.

These verify the bundle-first, global-fallback resolution strategy
introduced in the bundle-scoped service key migration.

The tests mock get_secret / get_secret_async directly so that
get_service_secret is tested in isolation from the full secrets-manager
lookup chain.
"""

import pytest

from kdcube_ai_app.apps.chat.sdk import config as sdk_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_get_secret(bundle_secrets: dict, global_secrets: dict):
    """
    Returns a fake get_secret that mimics the two-layer lookup:
      - 'b:services.<key>'  → bundle_secrets['services.<key>']
      - 'services.<key>'    → global_secrets['services.<key>']
    """
    def _inner(key: str, default=None):
        if key.startswith("b:"):
            tail = key[2:]  # strip 'b:' prefix
            return bundle_secrets.get(tail) or default
        return global_secrets.get(key) or default
    return _inner


def _fake_get_secret_async(bundle_secrets: dict, global_secrets: dict):
    sync = _fake_get_secret(bundle_secrets, global_secrets)
    async def _inner(key: str, default=None):
        return sync(key, default)
    return _inner


# ---------------------------------------------------------------------------
# get_service_secret — sync
# ---------------------------------------------------------------------------

class TestGetServiceSecret:
    def test_no_bundle_context_returns_global(self, monkeypatch):
        monkeypatch.setattr(sdk_config, "get_secret",
            _fake_get_secret({}, {"services.openai.api_key": "sk-global"}))
        assert sdk_config.get_service_secret("openai.api_key") == "sk-global"

    def test_no_keys_returns_none(self, monkeypatch):
        monkeypatch.setattr(sdk_config, "get_secret", _fake_get_secret({}, {}))
        assert sdk_config.get_service_secret("openai.api_key") is None

    def test_no_keys_returns_explicit_default(self, monkeypatch):
        monkeypatch.setattr(sdk_config, "get_secret", _fake_get_secret({}, {}))
        assert sdk_config.get_service_secret("openai.api_key", default="fallback") == "fallback"

    def test_bundle_override_shadows_global(self, monkeypatch):
        monkeypatch.setattr(sdk_config, "get_secret",
            _fake_get_secret(
                {"services.openai.api_key": "sk-bundle"},
                {"services.openai.api_key": "sk-global"},
            ))
        assert sdk_config.get_service_secret("openai.api_key") == "sk-bundle"

    def test_bundle_override_works_without_global(self, monkeypatch):
        monkeypatch.setattr(sdk_config, "get_secret",
            _fake_get_secret({"services.anthropic.api_key": "sk-bundle-ant"}, {}))
        assert sdk_config.get_service_secret("anthropic.api_key") == "sk-bundle-ant"

    def test_bundle_context_falls_back_to_global_when_no_override(self, monkeypatch):
        monkeypatch.setattr(sdk_config, "get_secret",
            _fake_get_secret({}, {"services.google.api_key": "gm-global"}))
        assert sdk_config.get_service_secret("google.api_key") == "gm-global"

    def test_empty_bundle_value_falls_back_to_global(self, monkeypatch):
        """Empty string from bundle → falsy → fallback to global."""
        def _empty_bundle(key, default=None):
            if key.startswith("b:"):
                return ""
            return "sk-global"
        monkeypatch.setattr(sdk_config, "get_secret", _empty_bundle)
        assert sdk_config.get_service_secret("openai.api_key") == "sk-global"

    def test_leading_dot_stripped_from_key(self, monkeypatch):
        """get_service_secret('.openai.api_key') must resolve same as 'openai.api_key'."""
        called = []
        def _recorder(key, default=None):
            called.append(key)
            return None
        monkeypatch.setattr(sdk_config, "get_secret", _recorder)
        sdk_config.get_service_secret(".openai.api_key")
        assert "b:services.openai.api_key" in called
        assert "services.openai.api_key" in called

    def test_bundle_lookup_tried_first_for_every_service_key(self, monkeypatch):
        """Smoke: all migrated service keys hit the bundle probe before the global lookup."""
        service_keys = [
            "openai.api_key",
            "anthropic.api_key",
            "anthropic.claude_code_key",
            "anthropic.auth_token",
            "google.api_key",
            "openrouter.api_key",
            "huggingface.api_key",
            "stripe.secret_key",
            "stripe.webhook_secret",
            "git.http_token",
            "git.http_user",
        ]
        probed = set()
        def _recorder(key, default=None):
            probed.add(key)
            return None
        monkeypatch.setattr(sdk_config, "get_secret", _recorder)
        for k in service_keys:
            probed.clear()
            sdk_config.get_service_secret(k)
            assert f"b:services.{k}" in probed, f"bundle key not probed for '{k}'"
            assert f"services.{k}" in probed, f"global key not probed for '{k}'"

    def test_two_different_bundles_are_isolated(self, monkeypatch):
        """Consecutive calls with different bundle secrets return independent values."""
        def _bundle_a(key, default=None):
            if key == "b:services.openai.api_key":
                return "sk-bundle-a"
            return None

        def _bundle_b(key, default=None):
            if key == "b:services.openai.api_key":
                return "sk-bundle-b"
            return None

        monkeypatch.setattr(sdk_config, "get_secret", _bundle_a)
        assert sdk_config.get_service_secret("openai.api_key") == "sk-bundle-a"

        monkeypatch.setattr(sdk_config, "get_secret", _bundle_b)
        assert sdk_config.get_service_secret("openai.api_key") == "sk-bundle-b"


# ---------------------------------------------------------------------------
# get_service_secret_async
# ---------------------------------------------------------------------------

class TestGetServiceSecretAsync:
    async def test_returns_bundle_override(self, monkeypatch):
        monkeypatch.setattr(sdk_config, "get_secret_async",
            _fake_get_secret_async(
                {"services.openai.api_key": "sk-bundle"},
                {"services.openai.api_key": "sk-global"},
            ))
        assert await sdk_config.get_service_secret_async("openai.api_key") == "sk-bundle"

    async def test_falls_back_to_global(self, monkeypatch):
        monkeypatch.setattr(sdk_config, "get_secret_async",
            _fake_get_secret_async({}, {"services.openai.api_key": "sk-global"}))
        assert await sdk_config.get_service_secret_async("openai.api_key") == "sk-global"

    async def test_returns_none_when_no_keys(self, monkeypatch):
        monkeypatch.setattr(sdk_config, "get_secret_async",
            _fake_get_secret_async({}, {}))
        assert await sdk_config.get_service_secret_async("openai.api_key") is None

    async def test_mirrors_sync_behaviour_for_all_keys(self, monkeypatch):
        """Async version probes the same key set as the sync version."""
        probed = set()
        async def _recorder(key, default=None):
            probed.add(key)
            return None
        monkeypatch.setattr(sdk_config, "get_secret_async", _recorder)
        await sdk_config.get_service_secret_async("stripe.secret_key")
        assert "b:services.stripe.secret_key" in probed
        assert "services.stripe.secret_key" in probed
