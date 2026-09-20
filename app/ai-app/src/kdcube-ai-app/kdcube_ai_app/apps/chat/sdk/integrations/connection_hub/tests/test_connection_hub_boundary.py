# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Portable Connection Hub policy stays distinct from KDCube host bindings."""

from __future__ import annotations

from typing import Any

import pytest

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


def test_agent_capability_sync_is_local_only():
    operation = ConnectionHubProvider(
        entrypoint=object(),
    ).spec.operations[AGENT_CAPABILITY_SYNC]
    assert operation.transports == ("local",)
