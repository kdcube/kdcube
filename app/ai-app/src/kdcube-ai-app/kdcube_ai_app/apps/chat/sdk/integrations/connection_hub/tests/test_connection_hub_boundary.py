# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Portable Connection Hub policy stays distinct from KDCube host bindings."""

from __future__ import annotations

import inspect
from importlib import import_module
from typing import Any

import pytest

from connection_hub.delegated_credentials.automation_access import (
    AutomationAccessService as PortableAutomationAccessService,
)
from connection_hub.delegated_credentials.cards.persistence import (
    DurableCardPersistence as PortableDurableCardPersistence,
)
from connection_hub.delegated_credentials.cards.service import (
    CardServingUnavailable,
)
from connection_hub.delegated_credentials.oauth.store import (
    GrantStoreUnavailable,
)
from connection_hub.hub.edges import ConnectionEdgeStore as PortableConnectionEdgeStore

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.automation_access import (
    AutomationAccessService,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.cards.persistence import (
    DurableCardPersistence,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.hub import ConnectionEdgeStore
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.hub.provider_impl import (
    ConnectionHubProvider,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceRequest,
    SOURCE_BUNDLE_ID_METADATA,
)
from connection_hub.contract import AGENT_CAPABILITY_SYNC


def test_portable_contract_is_reexported_without_a_second_implementation():
    assert ConnectionEdgeStore is PortableConnectionEdgeStore


def test_oauth_consent_compatibility_path_is_the_portable_module():
    assert import_module(
        "kdcube_ai_app.apps.chat.sdk.integrations.connection_hub."
        "delegated_credentials.oauth.consent"
    ) is import_module("connection_hub.delegated_credentials.oauth.consent")


def test_kdcube_ports_are_explicit_host_types():
    assert AutomationAccessService.__module__.startswith(
        "kdcube_ai_app.apps.chat.sdk.integrations.connection_hub."
    )
    assert DurableCardPersistence.__module__.startswith(
        "kdcube_ai_app.apps.chat.sdk.integrations.connection_hub."
    )
    assert ConnectionHubProvider.__module__.startswith(
        "kdcube_ai_app.apps.chat.sdk.integrations.connection_hub."
    )


@pytest.mark.parametrize(
    ("host_type", "portable_type"),
    (
        (AutomationAccessService, PortableAutomationAccessService),
        (DurableCardPersistence, PortableDurableCardPersistence),
    ),
)
def test_kdcube_constructor_adapters_accept_every_portable_parameter(
    host_type: type,
    portable_type: type,
) -> None:
    host_parameters = inspect.signature(host_type).parameters
    portable_parameters = inspect.signature(portable_type).parameters

    assert set(portable_parameters) - set(host_parameters) == set()


def test_kdcube_automation_access_forwards_the_authority_backend() -> None:
    with pytest.raises(
        GrantStoreUnavailable,
        match="selected_authority.automation_grant_store_not_bound",
    ):
        AutomationAccessService(
            redis=object(),
            tenant="tenant-a",
            project="project-a",
            config=object(),
            authority_backend="postgresql",
        )


def test_kdcube_card_persistence_forwards_the_authority_backend() -> None:
    with pytest.raises(
        CardServingUnavailable,
        match="selected_authority.credential_handles_not_bound",
    ):
        DurableCardPersistence(
            redis=object(),
            tenant="tenant-a",
            project="project-a",
            card_store=object(),
            authority_backend="postgresql",
        )


class _CapabilityService:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def sync_agent_capability_control(
        self,
        user: dict[str, Any],
        **payload: Any,
    ) -> dict[str, Any]:
        self.calls.append((dict(user), dict(payload)))
        return {"ok": True, "projection": payload["capability_authority"]}


def _capability_request(application: str = "problem-board@1-0") -> NamedServiceRequest:
    return NamedServiceRequest(
        operation=AGENT_CAPABILITY_SYNC,
        namespace="connections",
        payload={
            "application": application,
            "agent_id": "main",
            "descriptor_revision": "descriptor-r1",
            "descriptor_payload": {"revision": "descriptor-r1"},
            "capability_authority": {
                "schema": "connection_hub.agent_capability_policy.v1",
                "resource": (
                    "urn:kdcube:app:demo-tenant:demo-project:"
                    "problem-board%401-0:main"
                ),
                "capabilities": {"tools": ["work.list"]},
            },
        },
    )


@pytest.mark.asyncio
async def test_agent_capability_sync_requires_platform_bound_source_bundle():
    service = _CapabilityService()
    provider = ConnectionHubProvider(
        entrypoint=object(),
        automation_access_factory=lambda: service,
    )
    context = NamedServiceContext(
        user_id="user-1",
        user_type="registered",
        metadata={SOURCE_BUNDLE_ID_METADATA: "another-app@1-0"},
    )

    response = await provider.agent_capability_sync(
        context,
        _capability_request(),
    )

    assert response.ok is False
    assert response.status == 403
    assert response.error is not None
    assert response.error.code == "agent_capability_source_mismatch"
    assert service.calls == []


@pytest.mark.asyncio
async def test_agent_capability_sync_forwards_authenticated_user_and_payload():
    service = _CapabilityService()
    provider = ConnectionHubProvider(
        entrypoint=object(),
        automation_access_factory=lambda: service,
    )
    context = NamedServiceContext(
        user_id="user-1",
        user_type="registered",
        roles=("kdcube:role:registered",),
        permissions=("projects:read",),
        metadata={SOURCE_BUNDLE_ID_METADATA: "problem-board@1-0"},
    )

    response = await provider.agent_capability_sync(
        context,
        _capability_request(),
    )

    assert response.ok is True
    assert response.object["projection"]["capabilities"] == {
        "tools": ["work.list"],
    }
    assert service.calls[0][0] == {
        "user_id": "user-1",
        "user_type": "registered",
        "roles": ["kdcube:role:registered"],
        "permissions": ["projects:read"],
    }
    assert service.calls[0][1]["application"] == "problem-board@1-0"


@pytest.mark.asyncio
async def test_agent_capability_sync_accepts_an_async_access_factory():
    service = _CapabilityService()
    factory_calls = []

    async def _factory():
        factory_calls.append("awaited")
        return service

    provider = ConnectionHubProvider(
        entrypoint=object(),
        automation_access_factory=_factory,
    )
    context = NamedServiceContext(
        user_id="user-1",
        user_type="registered",
        metadata={SOURCE_BUNDLE_ID_METADATA: "problem-board@1-0"},
    )

    response = await provider.agent_capability_sync(
        context,
        _capability_request(),
    )

    assert response.ok is True
    assert factory_calls == ["awaited"]


def test_agent_capability_sync_is_local_only():
    operation = ConnectionHubProvider(
        entrypoint=object(),
    ).spec.operations[AGENT_CAPABILITY_SYNC]
    assert operation.transports == ("local",)
