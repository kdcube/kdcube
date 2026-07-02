# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry_client import AuthorityRegistryClient


class _Entrypoint:
    bundle_props = {"connections": {"connection_hub": {"bundle_id": "connection-hub@test"}}}

    def bundle_prop(self, path: str, default=None):
        current = self.bundle_props
        for part in path.split("."):
            if not isinstance(current, dict):
                return default
            current = current.get(part)
        return default if current is None else current

    def runtime_identity(self):
        return {"tenant": "t", "project": "p"}


def _registry() -> dict:
    return {
        "authorities": {
            "kdcube.platform": {
                "platform": True,
                "providers": {
                    "versatile_google_session": {
                        "type": "bundle_session_login",
                        "entrypoints": {
                            "login": {
                                "bundle_id": "versatile@2026-03-31-13-36",
                                "route": "public",
                                "operation": "platform_login",
                            },
                        },
                    },
                },
            },
        },
    }


@pytest.mark.asyncio
async def test_authority_registry_client_resolves_public_provider_entrypoint():
    result = await AuthorityRegistryClient(_Entrypoint(), registry=_registry()).resolve_provider_entrypoint(
        authority_id="kdcube.platform",
        provider_id="versatile_google_session",
        entrypoint="login",
    )

    assert result["ok"] is True
    assert result["url"] == "/api/integrations/bundles/t/p/versatile%402026-03-31-13-36/public/platform_login"
    assert result["endpoint"]["operation"] == "platform_login"


@pytest.mark.asyncio
async def test_authority_registry_client_loads_connection_hub_props_from_store(monkeypatch):
    async def _get_bundle_props(redis, *, tenant: str, project: str, bundle_id: str):
        assert redis == "redis"
        assert tenant == "t"
        assert project == "p"
        assert bundle_id == "connection-hub@test"
        return {"authority_registry": _registry()}

    import kdcube_ai_app.infra.plugin.bundle_store as bundle_store

    monkeypatch.setattr(bundle_store, "get_bundle_props", _get_bundle_props)

    result = await AuthorityRegistryClient(
        _Entrypoint(),
        redis="redis",
        connection_hub_bundle_id="connection-hub@test",
    ).resolve_provider(
        authority_id="kdcube.platform",
        provider_id="versatile_google_session",
    )

    assert result["ok"] is True
    assert result["provider_type"] == "bundle_session_login"
