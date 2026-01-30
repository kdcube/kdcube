# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

import asyncio
import os

import kdcube_ai_app.apps.chat.sdk.tools.mcp.web_search.web_search_server as srv
from kdcube_ai_app.infra.service_hub import cache as cache_mod


def test_web_search_server_uses_backend(monkeypatch):
    called = {}

    async def _fake_search(*, _SERVICE, queries, objective, refinement, n, fetch_content,
                           include_binary_base64, freshness, country, safesearch, namespaced_kv_cache):
        called["svc"] = _SERVICE
        called["cache"] = namespaced_kv_cache
        return [{"ok": True}]

    monkeypatch.setenv("DEFAULT_LLM_MODEL_ID", "o3-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setattr(srv.search_backends, "web_search", _fake_search)

    async def _run():
        out = await srv.web_search(
            queries="test",
            objective=None,
            refinement="balanced",
            n=3,
            fetch_content=False,
            include_binary_base64=False,
            freshness=None,
            country=None,
            safesearch="moderate",
        )
        assert out == [{"ok": True}]
        assert called.get("svc") is not None

    asyncio.run(_run())


def test_web_search_cache_env(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "")
    cache = cache_mod.create_kv_cache_from_env(ttl_env_var="WEB_SEARCH_CACHE_TTL_SECONDS")
    assert cache is None

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("WEB_SEARCH_CACHE_TTL_SECONDS", "120")
    cache = cache_mod.create_kv_cache_from_env(ttl_env_var="WEB_SEARCH_CACHE_TTL_SECONDS")
    # In CI without redis, this still builds the cache object; connection is lazy.
    assert cache is not None
