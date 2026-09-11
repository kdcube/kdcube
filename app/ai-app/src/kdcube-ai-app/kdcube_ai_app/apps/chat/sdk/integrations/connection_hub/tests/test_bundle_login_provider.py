# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import pytest
from fastapi import HTTPException

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.authority_providers import bundle_login
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.authority_providers.bundle_login import (
    require_authenticator_type,
    resolve_bundle_login_provider,
    resolve_provider_ref,
)


class _EntryPoint:
    bundle_props = {
        "authority_registry": {
            "authorities": {
                "kdcube.platform": {
                    "platform": True,
                    "providers": {
                        "workspace_google_session": {
                            "type": "bundle",
                            "entrypoints": {
                                "login": {
                                    "bundle_id": "workspace@2026-03-31-13-36",
                                    "route": "public",
                                    "operation": "platform_login",
                                },
                                "session_issue": {
                                    "bundle_id": "workspace@2026-03-31-13-36",
                                    "route": "public",
                                    "operation": "auth_google_session",
                                },
                            },
                        }
                    },
                }
            }
        }
    }


@pytest.mark.asyncio
async def test_bundle_login_provider_accepts_registered_entrypoint_without_host():
    result = await resolve_bundle_login_provider(
        _EntryPoint(),
        bundle_id="workspace@2026-03-31-13-36",
        operation="platform_login",
    )

    assert result["ok"] is True
    assert result["authority_id"] == "kdcube.platform"
    assert result["provider_id"] == "workspace_google_session"
    assert result["entrypoints"]["login"]["operation"] == "platform_login"


@pytest.mark.asyncio
async def test_provider_ref_passes_the_referenced_bundle_to_the_registry_client(monkeypatch):
    seen = {}

    class Client:
        def __init__(self, entrypoint, *, connection_hub_bundle_id=None):
            seen["entrypoint"] = entrypoint
            seen["bundle_id"] = connection_hub_bundle_id

        async def resolve_provider(self, **kwargs):
            seen["lookup"] = kwargs
            return {"ok": True, "provider": {"type": "google"}}

    monkeypatch.setattr(bundle_login, "AuthorityRegistryClient", Client)
    entrypoint = object()

    await resolve_provider_ref(
        entrypoint,
        {
            "bundle_id": "identity-app@1-0",
            "authority_id": "google.accounts",
            "provider_id": "google",
        },
    )

    assert seen == {
        "entrypoint": entrypoint,
        "bundle_id": "identity-app@1-0",
        "lookup": {
            "authority_id": "google.accounts",
            "provider_id": "google",
        },
    }


def test_google_flow_requires_a_google_authenticator():
    with pytest.raises(HTTPException) as captured:
        require_authenticator_type(
            {"provider_type": "oidc", "provider": {"type": "oidc"}},
            "google",
        )

    assert "must have type `google`" in captured.value.detail
