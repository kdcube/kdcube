# SPDX-License-Identifier: MIT

"""
Unit tests for ModelRouter._mk_* lazy-resolution methods.

The router is still a synchronous client factory, so it can only use explicit
Config keys and already-loaded Settings fields. Async bundle-scoped secret
resolution happens through get_secret() before code enters sync factories.

Config objects are created via __new__ to bypass __init__'s settings
lookups, which are irrelevant to key-resolution logic.
"""

import sys
import types
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk import config as sdk_config
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_bundle_call_context
from kdcube_ai_app.infra.service_hub import inventory


# ---------------------------------------------------------------------------
# resolve_config_request_secrets
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_config_request_secret_resolution_prefers_bundle_secret(monkeypatch):
    calls = []

    async def fake_get_secret(key, default=None, **kwargs):
        calls.append((key, kwargs))
        if key == "b:services.openai.api_key" and kwargs.get("bundle_id") == "bundle@1":
            return "sk-bundle-openai"
        if key == "services.anthropic.api_key":
            return "sk-global-anthropic"
        return default

    monkeypatch.setattr(inventory, "get_secret", fake_get_secret)

    resolved = await inventory.resolve_config_request_secrets(
        inventory.ConfigRequest(google_api_key="gm-explicit"),
        bundle_id="bundle@1",
    )

    assert resolved.openai_api_key == "sk-bundle-openai"
    assert resolved.claude_api_key == "sk-global-anthropic"
    assert resolved.google_api_key == "gm-explicit"
    assert ("b:services.google.api_key", {"bundle_id": "bundle@1"}) not in calls


# ---------------------------------------------------------------------------
# Fixture: minimal Config that avoids get_settings() calls
# ---------------------------------------------------------------------------

def _make_config(*, openai_api_key="", claude_api_key="", google_api_key=""):
    cfg = object.__new__(inventory.Config)
    cfg.openai_api_key = openai_api_key
    cfg.claude_api_key = claude_api_key
    cfg.google_api_key = google_api_key
    cfg.log_level = "WARNING"
    cfg.gemini_cache_enabled = False
    cfg.gemini_cache_ttl_seconds = 3600
    cfg.custom_model_endpoint = None
    cfg.custom_model_api_key = None
    cfg.role_models = {}
    cfg.default_llm_model = {"provider": "anthropic", "model_name": "claude-sonnet-static"}
    cfg.format_fixer_model = "claude-haiku-static"
    return cfg


# ---------------------------------------------------------------------------
# _mk_openai
# ---------------------------------------------------------------------------

def _settings(**values):
    defaults = {
        "OPENAI_API_KEY": None,
        "ANTHROPIC_API_KEY": None,
        "GOOGLE_API_KEY": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)

class TestMkOpenai:
    def test_config_key_takes_priority_over_service_secret(self, monkeypatch):
        captured = {}
        def fake_make_chat_openai(*, model, api_key, temperature, stream_usage):
            captured["api_key"] = api_key
            return object()

        monkeypatch.setattr(inventory, "make_chat_openai", fake_make_chat_openai)
        monkeypatch.setattr(sdk_config, "get_settings", lambda: _settings(OPENAI_API_KEY="sk-should-not-use"))

        router = inventory.ModelRouter(_make_config(openai_api_key="sk-explicit"))
        router._mk_openai("gpt-4o", 0.7)
        assert captured["api_key"] == "sk-explicit"

    def test_bundle_key_used_when_config_key_is_empty(self, monkeypatch):
        captured = {}
        def fake_make_chat_openai(*, model, api_key, temperature, stream_usage):
            captured["api_key"] = api_key
            return object()

        monkeypatch.setattr(inventory, "make_chat_openai", fake_make_chat_openai)
        monkeypatch.setattr(sdk_config, "get_settings", lambda: _settings(OPENAI_API_KEY="sk-settings"))

        router = inventory.ModelRouter(_make_config(openai_api_key=""))
        router._mk_openai("gpt-4o", 0.7)
        assert captured["api_key"] == "sk-settings"

    def test_global_key_used_as_fallback(self, monkeypatch):
        captured = {}
        def fake_make_chat_openai(*, model, api_key, temperature, stream_usage):
            captured["api_key"] = api_key
            return object()

        monkeypatch.setattr(inventory, "make_chat_openai", fake_make_chat_openai)
        monkeypatch.setattr(sdk_config, "get_settings", lambda: _settings(OPENAI_API_KEY="sk-global"))

        router = inventory.ModelRouter(_make_config(openai_api_key=""))
        router._mk_openai("gpt-4o", 0.7)
        assert captured["api_key"] == "sk-global"

    def test_empty_key_passed_through_when_no_secret(self, monkeypatch):
        captured = {}
        def fake_make_chat_openai(*, model, api_key, temperature, stream_usage):
            captured["api_key"] = api_key
            return object()

        monkeypatch.setattr(inventory, "make_chat_openai", fake_make_chat_openai)
        monkeypatch.setattr(sdk_config, "get_settings", lambda: _settings())

        router = inventory.ModelRouter(_make_config(openai_api_key=""))
        router._mk_openai("gpt-4o", 0.0)
        assert captured["api_key"] is None


class TestRequestRoleModelOverrides:
    def test_bundle_call_context_role_models_override_static_role(self):
        cfg = _make_config()
        cfg.role_models = {
            "memory.reconciler": {"provider": "anthropic", "model": "claude-sonnet-static"}
        }
        router = inventory.ModelRouter(cfg)

        assert router.describe("memory.reconciler").model_name == "claude-sonnet-static"
        with bind_current_bundle_call_context(
            {
                "role_models": {
                    "memory.reconciler": {
                        "provider": "anthropic",
                        "model": "claude-haiku-request",
                    }
                }
            }
        ):
            hint = router.describe("memory.reconciler")
            assert hint.provider == "anthropic"
            assert hint.model_name == "claude-haiku-request"

        assert router.describe("memory.reconciler").model_name == "claude-sonnet-static"

    def test_bundle_call_context_role_models_accept_model_string(self):
        cfg = _make_config()
        cfg.role_models = {
            "agent.decision": {"provider": "anthropic", "model": "claude-sonnet-static"}
        }
        router = inventory.ModelRouter(cfg)

        with bind_current_bundle_call_context({"role_models": {"agent.decision": "claude-opus-request"}}):
            hint = router.describe("agent.decision")
            assert hint.provider == "anthropic"
            assert hint.model_name == "claude-opus-request"


# ---------------------------------------------------------------------------
# _mk_anthropic
# ---------------------------------------------------------------------------

class TestMkAnthropic:
    def _fake_anthropic_module(self, captured: dict):
        class FakeAnthropic:
            def __init__(self, *, api_key):
                captured["api_key"] = api_key

        mod = types.ModuleType("anthropic")
        mod.Anthropic = FakeAnthropic
        return mod

    def test_config_key_takes_priority(self, monkeypatch):
        captured = {}
        monkeypatch.setitem(sys.modules, "anthropic", self._fake_anthropic_module(captured))
        monkeypatch.setattr(sdk_config, "get_settings", lambda: _settings(ANTHROPIC_API_KEY="sk-should-not-use"))

        router = inventory.ModelRouter(_make_config(claude_api_key="sk-explicit-ant"))
        router._mk_anthropic()
        assert captured["api_key"] == "sk-explicit-ant"

    def test_bundle_key_used_when_config_key_is_empty(self, monkeypatch):
        captured = {}
        monkeypatch.setitem(sys.modules, "anthropic", self._fake_anthropic_module(captured))
        monkeypatch.setattr(sdk_config, "get_settings", lambda: _settings(ANTHROPIC_API_KEY="sk-settings-ant"))

        router = inventory.ModelRouter(_make_config(claude_api_key=""))
        router._mk_anthropic()
        assert captured["api_key"] == "sk-settings-ant"

    def test_client_is_cached_after_first_call(self, monkeypatch):
        """_mk_anthropic() must return the same object on repeated calls."""
        call_count = [0]

        class FakeAnthropic:
            def __init__(self, *, api_key):
                call_count[0] += 1

        mod = types.ModuleType("anthropic")
        mod.Anthropic = FakeAnthropic
        monkeypatch.setitem(sys.modules, "anthropic", mod)
        monkeypatch.setattr(sdk_config, "get_settings", lambda: _settings(ANTHROPIC_API_KEY="sk-x"))

        router = inventory.ModelRouter(_make_config())
        first = router._mk_anthropic()
        second = router._mk_anthropic()
        assert first is second
        assert call_count[0] == 1


# ---------------------------------------------------------------------------
# _mk_anthropic_async
# ---------------------------------------------------------------------------

class TestMkAnthropicAsync:
    def test_bundle_key_used(self, monkeypatch):
        captured = {}

        class FakeAsyncAnthropic:
            def __init__(self, *, api_key):
                captured["api_key"] = api_key

        mod = types.ModuleType("anthropic")
        mod.Anthropic = object
        mod.AsyncAnthropic = FakeAsyncAnthropic
        monkeypatch.setitem(sys.modules, "anthropic", mod)
        monkeypatch.setattr(sdk_config, "get_settings", lambda: _settings(ANTHROPIC_API_KEY="sk-settings-async"))

        router = inventory.ModelRouter(_make_config(claude_api_key=""))
        router._mk_anthropic_async()
        assert captured["api_key"] == "sk-settings-async"

    def test_async_client_is_cached(self, monkeypatch):
        call_count = [0]

        class FakeAsyncAnthropic:
            def __init__(self, *, api_key):
                call_count[0] += 1

        mod = types.ModuleType("anthropic")
        mod.Anthropic = object
        mod.AsyncAnthropic = FakeAsyncAnthropic
        monkeypatch.setitem(sys.modules, "anthropic", mod)
        monkeypatch.setattr(sdk_config, "get_settings", lambda: _settings(ANTHROPIC_API_KEY="sk-x"))

        router = inventory.ModelRouter(_make_config())
        first = router._mk_anthropic_async()
        second = router._mk_anthropic_async()
        assert first is second
        assert call_count[0] == 1


# ---------------------------------------------------------------------------
# _mk_gemini
# ---------------------------------------------------------------------------

class TestMkGemini:
    def _fake_gemini_client(self, captured: dict):
        class FakeGeminiModelClient:
            def __init__(self, *, api_key, model_name, temperature, cache_enabled, cache_ttl_seconds):
                captured["api_key"] = api_key

        return FakeGeminiModelClient

    def test_bundle_key_used(self, monkeypatch):
        captured = {}
        import kdcube_ai_app.infra.service_hub.gemini as gemini_mod
        monkeypatch.setattr(gemini_mod, "GeminiModelClient", self._fake_gemini_client(captured))
        monkeypatch.setattr(sdk_config, "get_settings", lambda: _settings(GOOGLE_API_KEY="gm-settings"))

        router = inventory.ModelRouter(_make_config(google_api_key=""))
        router._mk_gemini("gemini-2.5-pro", 0.7)
        assert captured["api_key"] == "gm-settings"

    def test_config_key_takes_priority(self, monkeypatch):
        captured = {}
        import kdcube_ai_app.infra.service_hub.gemini as gemini_mod
        monkeypatch.setattr(gemini_mod, "GeminiModelClient", self._fake_gemini_client(captured))
        monkeypatch.setattr(sdk_config, "get_settings", lambda: _settings(GOOGLE_API_KEY="gm-should-not-use"))

        router = inventory.ModelRouter(_make_config(google_api_key="gm-explicit"))
        router._mk_gemini("gemini-2.5-pro", 0.7)
        assert captured["api_key"] == "gm-explicit"

    def test_raises_when_no_key(self, monkeypatch):
        monkeypatch.setattr(sdk_config, "get_settings", lambda: _settings())

        router = inventory.ModelRouter(_make_config(google_api_key=""))
        with pytest.raises(ValueError, match="Gemini provider requires"):
            router._mk_gemini("gemini-2.5-pro", 0.7)
