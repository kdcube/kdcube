# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.auth.platform_auth_reload import (
    PlatformAuthManagerHolder,
    PlatformAuthSelection,
    PlatformAuthSettingsHandler,
)
from kdcube_ai_app.infra.platform_settings.updates import PlatformSettingsUpdate
from kdcube_ai_app.apps.middleware.platform_auth import create_platform_auth_manager


def selection(provider_id="cognito", family="cognito"):
    return PlatformAuthSelection(
        auth_type="cognito",
        bundle_id="connection-hub@1-0",
        authority_id="kdcube.platform",
        provider_id=provider_id,
        provider_family=family,
    )


def manager(name, provider="multi-cognito"):
    return SimpleNamespace(
        name=name,
        authenticator_id=f"kdcube.{provider}",
        authority_id="kdcube.platform",
        authenticator_provider=provider,
        runtime_auth_config=SimpleNamespace(ID_TOKEN_HEADER_NAME="X-ID-Token"),
    )


def event(scope):
    return PlatformSettingsUpdate.create(
        tenant="tenant-a",
        project="project-a",
        section="auth",
        scope=scope,
        reason="test update",
    )


def test_holder_swaps_manager_without_changing_its_reference():
    built = iter([manager("old"), manager("new")])
    holder = PlatformAuthManagerHolder(lambda: next(built), selection_reader=selection)

    result = holder.rebuild("provider edit")

    assert result.reloaded is True
    assert holder.name == "new"
    assert result.old_descriptor["manager"] == "SimpleNamespace"
    assert holder.runtime_auth_config.ID_TOKEN_HEADER_NAME == "X-ID-Token"


def test_holder_refuses_live_selection_change_without_building_candidate():
    selected = [selection()]
    builds = []

    def factory():
        builds.append(len(builds))
        return manager(f"manager-{len(builds)}")

    holder = PlatformAuthManagerHolder(factory, selection_reader=lambda: selected[0])
    selected[0] = selection(provider_id="browser_session", family="session")

    result = holder.rebuild("provider edit")

    assert result.status == "refresh_required"
    assert len(builds) == 1
    assert holder.name == "manager-1"


@pytest.mark.asyncio
async def test_auth_handler_rebuilds_providers_and_catch_up_but_not_lane():
    built = iter([manager("initial"), manager("provider"), manager("catch-up")])
    holder = PlatformAuthManagerHolder(lambda: next(built), selection_reader=selection)
    handler = PlatformAuthSettingsHandler(holder)

    await handler(event("providers"))
    assert holder.name == "provider"
    await handler(event("catch_up"))
    assert holder.name == "catch-up"
    await handler(event("lane"))
    assert holder.name == "catch-up"
    assert handler.last_result.status == "refresh_required"


def test_single_cognito_manager_uses_the_supplied_settings_snapshot(monkeypatch):
    auth_config = SimpleNamespace(
        COGNITO_REGION="eu-west-1",
        COGNITO_USER_POOL_ID="pool-current",
        COGNITO_APP_CLIENT_ID="client-current",
        COGNITO_TRUSTED_PROVIDERS=[
            SimpleNamespace(
                region="eu-west-1",
                user_pool_id="pool-current",
                app_client_id="client-current",
                hosted_ui_domain="https://login.example.test",
            )
        ],
    )
    settings = SimpleNamespace(
        AUTH_PROVIDER="cognito",
        AUTH=auth_config,
        plain=lambda *_args, **_kwargs: None,
        connection_hub_platform_auth_config=lambda: {"auth_provider": "cognito"},
    )
    captured = {}

    def from_values(**kwargs):
        captured.update(kwargs)
        return manager("single", provider="cognito")

    monkeypatch.setattr(
        "kdcube_ai_app.auth.implementations.cognito.CognitoAuthManager.from_values",
        from_values,
    )

    resolved = create_platform_auth_manager(settings=settings, service_label="test")

    assert captured["pool_id"] == "pool-current"
    assert captured["client_id"] == "client-current"
    assert captured["hosted_ui"] == "https://login.example.test"
    assert resolved.runtime_auth_config is auth_config
