# SPDX-License-Identifier: MIT

"""
Tests that BraveSearchBackend resolves its API key through
get_service_secret (bundle-first, global-fallback).
"""

import pytest
import kdcube_ai_app.apps.chat.sdk.tools.backends.web.search_backends as search_mod


class TestBraveServiceKeyResolution:
    def test_explicit_key_bypasses_service_secret(self, monkeypatch):
        secret_calls = []
        monkeypatch.setattr(search_mod, "get_service_secret",
                            lambda k, default=None: secret_calls.append(k) or "should-not-use")

        backend = search_mod.BraveSearchBackend(api_key="sk-explicit-brave")

        assert backend.api_key == "sk-explicit-brave"
        assert secret_calls == []

    def test_bundle_key_used_when_no_explicit_key(self, monkeypatch):
        monkeypatch.setattr(search_mod, "get_service_secret",
                            lambda k, default=None: "sk-bundle-brave" if k == "brave.api_key" else None)

        backend = search_mod.BraveSearchBackend()

        assert backend.api_key == "sk-bundle-brave"

    def test_global_key_used_as_fallback(self, monkeypatch):
        monkeypatch.setattr(search_mod, "get_service_secret",
                            lambda k, default=None: "sk-global-brave" if k == "brave.api_key" else None)

        backend = search_mod.BraveSearchBackend()

        assert backend.api_key == "sk-global-brave"

    def test_no_key_raises(self, monkeypatch):
        monkeypatch.setattr(search_mod, "get_service_secret", lambda k, default=None: None)

        with pytest.raises(search_mod.SearchBackendError, match="BRAVE_API_KEY"):
            search_mod.BraveSearchBackend()
