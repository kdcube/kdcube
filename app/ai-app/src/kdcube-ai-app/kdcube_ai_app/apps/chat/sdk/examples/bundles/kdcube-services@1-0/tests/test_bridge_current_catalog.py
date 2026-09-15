# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The named-services door uses the guard's exact card/catalog snapshot."""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path
from connection_hub.authority_registry import (
    CredentialEnvelope,
)
from connection_hub.delegated_credentials.catalog.authorization import (
    ActiveCatalogCapabilities,
)
from connection_hub.delegated_credentials.catalog.models import (
    CatalogDocument,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.named_service_admission import (
    managed_named_service_admission,
    store_managed_named_service_admission_snapshot,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import NamedServiceRequest
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.transports import (
    api_client as named_service_api_client,
)

BUNDLE_ROOT = Path(__file__).resolve().parents[1]

RESOURCE = "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
REQUEST_RESOURCE = "/api/integrations/bundles/t/p/kdcube-services@1-0/public/mcp/named_services"
CARD_VERSION = "delegated_catalog_2026-08-09-09-00-00-000_a1b2c3d4e5f6"

NAMED_SERVICES = {
    "namespaces": {
        "mail": {
            "tools": {
                "search": {"operation": "object.search", "grants": ["named_services:use"]},
                "schema": {"operation": "object.schema", "grants": ["named_services:use"]},
            },
        },
    },
}

CONNECTIONS = {
    "delegated_credentials": {
        "oauth": {
            "enabled": True,
            "resources": [
                {
                    "resource": RESOURCE,
                    "grants": ["named_services:use"],
                    "tools": {"named_services_search": {"grants": ["named_services:use"]}},
                    "named_services": copy.deepcopy(NAMED_SERVICES),
                },
            ],
        },
    },
}


def _bridge_module():
    _name, module = load_dynamic_module_for_path(
        BUNDLE_ROOT / "services" / "named_services" / "bridge.py"
    )
    return module


def _without(namespace: str = "", operation_tool: str = "") -> dict:
    trimmed = copy.deepcopy(CONNECTIONS)
    namespaces = trimmed["delegated_credentials"]["oauth"]["resources"][0][
        "named_services"
    ]["namespaces"]
    if namespace:
        namespaces.pop(namespace, None)
    if operation_tool:
        namespaces["mail"]["tools"].pop(operation_tool, None)
    return trimmed


_DEFAULT_CARD_BOUNDARY = object()


def _request(
    *,
    connections=CONNECTIONS,
    card_boundary=_DEFAULT_CARD_BOUNDARY,
    snapshot: bool = True,
    client_id: str = "claude",
):
    boundary = (
        copy.deepcopy(NAMED_SERVICES)
        if card_boundary is _DEFAULT_CARD_BOUNDARY
        else copy.deepcopy(card_boundary)
    )
    grant_record = {
        "client_id": client_id,
        "registry_access_id": "oauth-5aa44826664a0bdd",
        "grantor_subject": "user-1",
        "delegate_subject": f"integration:{client_id}:user-1",
        "card_revision": 8,
        "catalog_version": CARD_VERSION,
        "resource_grants": {RESOURCE: ["named_services:use"]},
        "account_scope": {},
    }
    if card_boundary is not None:
        grant_record["named_services"] = boundary
    credential = CredentialEnvelope(
        subject=f"integration:{client_id}:user-1",
        attrs={
            "client_id": client_id,
            "grantor_subject": "user-1",
            "grants": ["named_services:use"],
            "resource": RESOURCE,
        },
    )
    request = SimpleNamespace(
        state=SimpleNamespace(
            delegated_credential={
                "credential": credential.to_dict(),
                "grant_record": grant_record,
            }
        )
    )
    if snapshot:
        store_managed_named_service_admission_snapshot(
            request,
            catalog=ActiveCatalogCapabilities(CatalogDocument.build(connections)),
            grant_record=grant_record,
            credential=credential,
            resource=RESOURCE,
            request_resource=REQUEST_RESOURCE,
            outer_operation="named_services_search",
        )
    return request


def _bridge(module, request, *, config=None):
    return module.NamedServicesMcpBridge(
        config=copy.deepcopy(NAMED_SERVICES) if config is None else config,
        tenant="t",
        project="p",
        request=request,
    )


async def _decision(request, *, operation="object.search"):
    return await managed_named_service_admission(request).authorize(
        NamedServiceRequest(operation=operation, namespace="mail")
    )


async def test_an_operation_the_catalog_still_offers_is_admitted():
    decision = await _decision(_request())
    assert decision.allowed is True


async def test_a_namespace_removed_from_the_catalog_denies_with_its_whole_path():
    decision = await _decision(_request(connections=_without(namespace="mail")))

    assert decision.allowed is False
    denial = decision.denial
    assert denial is not None
    assert denial.error.code == "delegated_capability_no_longer_available"
    assert denial.status == 403
    assert denial.ret["access_id"] == "oauth-5aa44826664a0bdd"
    assert denial.ret["card_revision"] == 8
    assert denial.ret["card_catalog_version"] == CARD_VERSION
    assert denial.ret["active_catalog_version"] != CARD_VERSION
    assert denial.ret["requested_capability"] == {
        "kind": "named_service_operation",
        "resource": RESOURCE,
        "request_resource": REQUEST_RESOURCE,
        "surface": "named_service",
        "outer_operation": "named_services_search",
        "namespace": "mail",
        "operation": "object.search",
    }


async def test_a_removed_operation_denies_while_its_namespace_survives():
    request = _request(connections=_without(operation_tool="schema"))

    removed = await _decision(request, operation="object.schema")
    surviving = await _decision(request, operation="object.search")

    assert removed.allowed is False
    assert removed.denial.ret["requested_capability"]["operation"] == "object.schema"
    assert surviving.allowed is True


async def test_the_door_refuses_before_a_provider_is_selected(monkeypatch):
    module = _bridge_module()
    bridge = _bridge(module, _request(connections=_without(namespace="mail")))

    async def _never(*_args, **_kwargs):
        raise AssertionError("provider must not be reached")

    monkeypatch.setattr(named_service_api_client, "_call_bundle_registry_endpoint", _never)
    payload = await bridge.call(
        tool_name="search",
        operation="object.search",
        namespace="mail",
    )

    assert payload["error"]["code"] == "delegated_capability_no_longer_available"


async def test_a_delegated_card_with_an_empty_boundary_reaches_no_operation(monkeypatch):
    module = _bridge_module()
    bridge = _bridge(module, _request(card_boundary={"namespaces": {}}))

    async def _never(*_args, **_kwargs):
        raise AssertionError("provider must not be reached")

    monkeypatch.setattr(named_service_api_client, "_call_bundle_registry_endpoint", _never)
    payload = await bridge.call(
        tool_name="search",
        operation="object.search",
        namespace="mail",
    )

    assert payload["error"]["code"] == "delegated_capability_not_granted"
    assert payload["ret"]["recovery"]["request_user_consent"] is True


async def test_a_hosted_agent_card_missing_the_operation_carries_a_consent_block(monkeypatch):
    # The card holds every claim the operation needs but not the operation
    # itself. A hosted agent's chat can only raise a banner from a
    # self-describing block, so this denial must carry one like a missing claim.
    module = _bridge_module()
    agent = "kdcube-agent:ported-langgraph-agents@2026-07-13:lg-react"
    bridge = _bridge(module, _request(card_boundary={"namespaces": {}}, client_id=agent))

    async def _never(*_args, **_kwargs):
        raise AssertionError("provider must not be reached")

    monkeypatch.setattr(named_service_api_client, "_call_bundle_registry_endpoint", _never)
    payload = await bridge.call(
        tool_name="search",
        operation="object.search",
        namespace="mail",
    )

    assert payload["error"]["code"] == "delegated_capability_not_granted"
    consent = payload["consent"]
    assert consent["kind"] == "delegated_agent_grant"
    assert consent["reason"] == "delegated_capability_not_granted"
    assert consent["agent_client_id"] == agent
    assert consent["resource"]
    assert consent["namespace"] == "mail"
    assert consent["operation"] == "object.search"
    assert consent["claims"] == []
    assert consent["grant"]["payload"]["named_service_operations"] == {"mail": ["object.search"]}


async def test_an_external_client_card_denial_keeps_no_grant_action(monkeypatch):
    module = _bridge_module()
    bridge = _bridge(module, _request(card_boundary={"namespaces": {}}))

    async def _never(*_args, **_kwargs):
        raise AssertionError("provider must not be reached")

    monkeypatch.setattr(named_service_api_client, "_call_bundle_registry_endpoint", _never)
    payload = await bridge.call(
        tool_name="search",
        operation="object.search",
        namespace="mail",
    )

    assert payload["error"]["code"] == "delegated_capability_not_granted"
    assert "grant" not in payload.get("consent", {})

async def test_a_pre_boundary_card_fails_closed_at_dispatch():
    decision = await _decision(_request(card_boundary=None))

    assert decision.allowed is False
    assert decision.denial.error.code == "delegated_card_boundary_unavailable"
    assert decision.denial.status == 503


async def test_the_listing_intersects_card_and_active_catalog():
    module = _bridge_module()

    listed = await _bridge(module, _request()).list_services()
    mail = next(ns for ns in listed["services"] if ns["namespace"] == "mail")
    assert set(mail["tools"]) == {"search", "schema"}

    withdrawn = await _bridge(
        module, _request(connections=_without(operation_tool="search"))
    ).list_services()
    mail = next(ns for ns in withdrawn["services"] if ns["namespace"] == "mail")
    assert set(mail["tools"]) == {"schema"}

    empty_card = await _bridge(
        module, _request(card_boundary={"namespaces": {}})
    ).list_services()
    assert empty_card["services"] == []


async def test_managed_bridge_uses_the_card_boundary_when_host_config_is_empty(monkeypatch):
    module = _bridge_module()
    bridge = _bridge(module, _request(), config={})
    called = {}

    async def _call(endpoint, request, *, admission):
        decision = await admission.authorize(request)
        assert decision.allowed is True
        called["namespace"] = endpoint.namespace
        called["operation"] = request.operation
        return module.NamedServiceResponse.ok_response(
            object={"namespace": request.namespace},
        )

    monkeypatch.setattr(module, "call_named_service_endpoint", _call)

    listed = await bridge.list_services()
    payload = await bridge.call(
        tool_name="search",
        operation="object.search",
        namespace="mail",
    )

    assert [service["namespace"] for service in listed["services"]] == ["mail"]
    assert payload["ok"] is True
    assert called == {"namespace": "mail", "operation": "object.search"}


async def test_a_namespace_the_catalog_dropped_leaves_the_listing():
    module = _bridge_module()
    listed = await _bridge(
        module, _request(connections=_without(namespace="mail"))
    ).list_services()
    assert listed["services"] == []


async def test_missing_managed_snapshot_fails_closed():
    module = _bridge_module()
    bridge = _bridge(module, _request(snapshot=False))

    listed = await bridge.list_services()
    called = await bridge.call(
        tool_name="search",
        operation="object.search",
        namespace="mail",
    )

    assert listed == {
        "ok": False,
        "status": 503,
        "error": "named_service_admission_unavailable",
        "message": "Managed named-service admission snapshot is unavailable",
    }
    assert called["status"] == 503
    assert called["error"] == "named_service_admission_unavailable"
