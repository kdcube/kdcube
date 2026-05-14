# SPDX-License-Identifier: MIT

"""
Tests that openrouter_completion resolves its API key through
get_service_secret (bundle-first, global-fallback).
"""

import pytest
import kdcube_ai_app.infra.service_hub.openrouter as openrouter_mod


class TestOpenrouterServiceKeyResolution:
    async def test_explicit_api_key_bypasses_service_secret(self, monkeypatch):
        """Caller-supplied api_key is used directly; get_service_secret is not consulted."""
        secret_calls = []
        monkeypatch.setattr(openrouter_mod, "get_service_secret",
                            lambda k, default=None: secret_calls.append(k) or "should-not-use")

        captured = {}

        def fake_post(url, *, headers, json, timeout):
            captured["auth"] = headers.get("Authorization")
            return _fake_200_response("hello")

        monkeypatch.setattr(openrouter_mod, "aiohttp", _fake_aiohttp(fake_post))

        result = await openrouter_mod.openrouter_completion.__wrapped__(
            model="anthropic/claude-3.5-sonnet",
            messages=[{"role": "user", "content": "hi"}],
            api_key="sk-explicit",
        )

        assert result["success"] is True
        assert captured["auth"] == "Bearer sk-explicit"
        assert secret_calls == []  # get_service_secret never called

    async def test_bundle_key_used_when_no_explicit_key(self, monkeypatch):
        monkeypatch.setattr(openrouter_mod, "get_service_secret",
                            lambda k, default=None: "sk-bundle-or" if k == "openrouter.api_key" else None)

        captured = {}

        def fake_post(url, *, headers, json, timeout):
            captured["auth"] = headers.get("Authorization")
            return _fake_200_response("hello")

        monkeypatch.setattr(openrouter_mod, "aiohttp", _fake_aiohttp(fake_post))

        result = await openrouter_mod.openrouter_completion.__wrapped__(
            model="anthropic/claude-3.5-sonnet",
            messages=[{"role": "user", "content": "hi"}],
        )

        assert result["success"] is True
        assert captured["auth"] == "Bearer sk-bundle-or"

    async def test_global_key_used_when_no_bundle_override(self, monkeypatch):
        """get_service_secret returns global key when no bundle context sets an override."""
        monkeypatch.setattr(openrouter_mod, "get_service_secret",
                            lambda k, default=None: "sk-global-or" if k == "openrouter.api_key" else None)

        captured = {}

        def fake_post(url, *, headers, json, timeout):
            captured["auth"] = headers.get("Authorization")
            return _fake_200_response("world")

        monkeypatch.setattr(openrouter_mod, "aiohttp", _fake_aiohttp(fake_post))

        result = await openrouter_mod.openrouter_completion.__wrapped__(
            model="anthropic/claude-3.5-sonnet",
            messages=[{"role": "user", "content": "hi"}],
        )

        assert result["success"] is True
        assert captured["auth"] == "Bearer sk-global-or"

    async def test_no_key_returns_error_dict(self, monkeypatch):
        monkeypatch.setattr(openrouter_mod, "get_service_secret",
                            lambda k, default=None: None)

        result = await openrouter_mod.openrouter_completion.__wrapped__(
            model="anthropic/claude-3.5-sonnet",
            messages=[{"role": "user", "content": "hi"}],
        )

        assert result["success"] is False
        assert "OPENROUTER_API_KEY" in result["error"]


# ---------------------------------------------------------------------------
# Minimal aiohttp mock
# ---------------------------------------------------------------------------

def _fake_200_response(content: str):
    body = {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        "model": "test-model",
    }

    class FakeResponse:
        status = 200

        async def json(self):
            return body

    class FakeResponseCM:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *_):
            pass

    return FakeResponseCM()


def _fake_aiohttp(post_fn):
    """
    Returns a fake aiohttp module.

    aiohttp.ClientSession is used as 'async with ClientSession() as session',
    and session.post() is used as 'async with session.post(...) as resp'.
    post_fn is a plain callable that returns an async context manager.
    """
    import types

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def post(self, url, *, headers, json, timeout):
            return post_fn(url, headers=headers, json=json, timeout=timeout)

    fake_mod = types.ModuleType("aiohttp")
    fake_mod.ClientSession = FakeSession
    fake_mod.ClientTimeout = lambda **kw: None
    return fake_mod
