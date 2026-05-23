# SPDX-License-Identifier: MIT

"""
Tests Brave backend API-key handling.
"""

import pytest
import kdcube_ai_app.apps.chat.sdk.tools.backends.web.search_backends as search_mod


class TestBraveServiceKeyResolution:
    def test_explicit_key_is_used(self):
        backend = search_mod.BraveSearchBackend(api_key="sk-explicit-brave")

        assert backend.api_key == "sk-explicit-brave"

    @pytest.mark.asyncio
    async def test_async_factory_uses_bundle_secret_first(self, monkeypatch):
        async def _fake_get_secret(key, default=None, **kwargs):
            return "sk-bundle-brave" if key == "b:services.brave.api_key" else default

        monkeypatch.setattr(search_mod, "get_secret", _fake_get_secret)
        backend = await search_mod.get_search_backend("brave")

        assert backend.api_key == "sk-bundle-brave"

    @pytest.mark.asyncio
    async def test_async_factory_uses_global_secret_as_fallback(self, monkeypatch):
        async def _fake_get_secret(key, default=None, **kwargs):
            return "sk-global-brave" if key == "services.brave.api_key" else default

        monkeypatch.setattr(search_mod, "get_secret", _fake_get_secret)
        backend = await search_mod.get_search_backend("brave")

        assert backend.api_key == "sk-global-brave"

    @pytest.mark.asyncio
    async def test_no_key_raises(self, monkeypatch):
        async def _fake_get_secret(key, default=None, **kwargs):
            return default

        monkeypatch.setattr(search_mod, "get_secret", _fake_get_secret)

        with pytest.raises(search_mod.SearchBackendError, match="BRAVE_API_KEY"):
            await search_mod.get_search_backend("brave")
