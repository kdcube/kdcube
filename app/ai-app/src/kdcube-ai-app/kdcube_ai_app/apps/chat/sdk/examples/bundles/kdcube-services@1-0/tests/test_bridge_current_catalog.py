# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The named-services door intersects the card with the active catalog.

A card's materialized boundary proves an operation was selected under the
catalog generation the card was saved against. It does not prove the deployment
still offers it, so the door checks the parsed namespace and operation against
the active catalog before a provider is selected.
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.tests.helpers import (
    bind_delegated_catalog,
)

BUNDLE_ROOT = Path(__file__).resolve().parents[1]

RESOURCE = "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
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


class _Policy:
    namespace = "mail"

    def tool_configured(self, tool_name):
        return True

    def operation_configured(self, *, tool_name, operation):
        return True

    def grants_for(self, *, tool_name, operation):
        return []

    def authority_for(self, *, tool_name, operation):
        return ""


def _request(*, delegated=True, connections=CONNECTIONS, unavailable="", resolvers=True):
    state = SimpleNamespace()
    if delegated:
        state.delegated_credential = {
            "credential": {"attrs": {"grants": ["named_services:use"]}},
            "grant_record": {
                "client_id": "claude",
                "registry_access_id": "oauth-5aa44826664a0bdd",
                "card_revision": 8,
                "catalog_version": CARD_VERSION,
                "resource_grants": {RESOURCE: ["named_services:use"]},
                "named_services": copy.deepcopy(NAMED_SERVICES),
            },
        }
    if resolvers:
        bind_delegated_catalog(
            SimpleNamespace(state=state), connections, unavailable=unavailable
        )
    return SimpleNamespace(state=state)


def _bridge(module, request, *, config=None):
    return module.NamedServicesMcpBridge(
        config=config or {}, tenant="t", project="p", request=request
    )


def _without(namespace: str = "", operation_tool: str = "") -> dict:
    trimmed = copy.deepcopy(CONNECTIONS)
    namespaces = trimmed["delegated_credentials"]["oauth"]["resources"][0]["named_services"]["namespaces"]
    if namespace:
        namespaces.pop(namespace, None)
    if operation_tool:
        namespaces["mail"]["tools"].pop(operation_tool, None)
    return trimmed


async def test_an_operation_the_catalog_still_offers_is_admitted():
    module = _bridge_module()
    bridge = _bridge(module, _request())

    denial = await bridge._current_catalog_denial(
        namespace="mail", operation="object.search", tool_name="named_services_search"
    )

    assert denial is None


async def test_a_namespace_removed_from_the_catalog_denies_with_its_whole_path():
    module = _bridge_module()
    bridge = _bridge(module, _request(connections=_without(namespace="mail")))

    denial = await bridge._current_catalog_denial(
        namespace="mail", operation="object.search", tool_name="named_services_search"
    )

    assert denial is not None
    assert denial["error"]["code"] == "delegated_capability_no_longer_available"
    assert denial["error"]["retryable"] is False
    ret = denial["ret"]
    assert ret["access_id"] == "oauth-5aa44826664a0bdd"
    assert ret["card_revision"] == 8
    assert ret["card_catalog_version"] == CARD_VERSION
    assert ret["active_catalog_version"] != CARD_VERSION
    assert ret["requested_capability"] == {
        "kind": "named_service_operation",
        "resource": RESOURCE,
        "surface": "named_service",
        "outer_operation": "named_services_search",
        "namespace": "mail",
        "operation": "object.search",
    }


async def test_a_removed_operation_denies_while_its_namespace_survives():
    module = _bridge_module()
    bridge = _bridge(module, _request(connections=_without(operation_tool="schema")))

    removed = await bridge._current_catalog_denial(
        namespace="mail", operation="object.schema", tool_name="named_services_schema"
    )
    surviving = await bridge._current_catalog_denial(
        namespace="mail", operation="object.search", tool_name="named_services_search"
    )

    assert removed is not None
    assert removed["ret"]["requested_capability"]["operation"] == "object.schema"
    assert surviving is None


async def test_a_caller_without_a_delegated_credential_is_not_narrowed():
    module = _bridge_module()
    bridge = _bridge(module, _request(delegated=False))

    denial = await bridge._current_catalog_denial(
        namespace="mail", operation="object.search", tool_name="named_services_search"
    )

    assert denial is None


async def test_an_unreadable_catalog_is_retryable_unavailability():
    module = _bridge_module()
    bridge = _bridge(module, _request(unavailable="durable_active_unreadable"))

    denial = await bridge._current_catalog_denial(
        namespace="mail", operation="object.search", tool_name="named_services_search"
    )

    assert denial is not None
    assert denial["error"]["code"] == "temporarily_unavailable"
    assert denial["error"]["retryable"] is True
    assert denial["ret"]["reason"] == "durable_active_unreadable"


async def test_uninstalled_serving_resolvers_fail_closed():
    """Absent readers are a composition failure, never "no requirement"."""
    module = _bridge_module()
    bridge = _bridge(module, _request(resolvers=False))

    denial = await bridge._current_catalog_denial(
        namespace="mail", operation="object.search", tool_name="named_services_search"
    )

    assert denial is not None
    assert denial["error"]["code"] == "temporarily_unavailable"
    assert denial["ret"]["reason"] == "delegated_serving_resolvers_absent"


async def test_the_door_refuses_before_a_provider_is_selected(monkeypatch):
    module = _bridge_module()
    bridge = _bridge(module, _request(connections=_without(namespace="mail")))

    async def _never(*_args, **_kwargs):
        raise AssertionError("provider must not be reached")

    monkeypatch.setattr(module, "call_named_service_endpoint", _never)

    payload = await bridge.call(
        tool_name="named_services_search", operation="object.search", namespace="mail",
    )

    assert payload["error"]["code"] == "delegated_capability_no_longer_available"


async def test_a_delegated_card_with_an_empty_boundary_reaches_no_namespace():
    """The card's empty tree is its boundary; the descriptor does not refill it."""
    module = _bridge_module()
    request = _request()
    request.state.delegated_credential["grant_record"]["named_services"] = {"namespaces": {}}
    bridge = _bridge(module, request, config=copy.deepcopy(NAMED_SERVICES))

    payload = await bridge.call(
        tool_name="named_services_search", operation="object.search", namespace="mail",
    )

    assert payload["error"] == "namespace_not_configured"


async def test_a_card_written_before_the_boundary_field_keeps_the_descriptor():
    """Absence of the field is a pre-encoding card, not an empty boundary."""
    module = _bridge_module()
    request = _request()
    request.state.delegated_credential["grant_record"].pop("named_services")
    bridge = _bridge(module, request, config=copy.deepcopy(NAMED_SERVICES))

    assert set(bridge._catalog.namespace_names()) == {"mail"}


class _NarrowedPolicy(_Policy):
    """The card's boundary: the tool is configured, this operation is not."""

    def operation_configured(self, *, tool_name, operation):
        return operation != "object.search"


async def test_an_operation_the_card_does_not_cover_names_its_remedy():
    """The mirror of the removal denial.

    The catalog still offers this, so the answer is not "gone" — the card's
    grantor can add it. Without saying so the door returned an anonymous
    `named_service_operation_not_configured`, and the caller had nothing to act
    on in the one case where a remedy exists.
    """
    module = _bridge_module()
    bridge = _bridge(module, _request())

    denial = await bridge._authorize(
        _NarrowedPolicy(), "object.search", tool_name="named_services_search"
    )

    assert denial is not None
    assert denial["error"]["code"] == "delegated_capability_not_granted"
    assert denial["error"]["retryable"] is False
    ret = denial["ret"]
    assert ret["access_id"] == "oauth-5aa44826664a0bdd"
    assert ret["card_revision"] == 8
    assert ret["card_catalog_version"] == CARD_VERSION
    assert ret["recovery"] == {
        "action": "grant_capability_in_delegated_access",
        "retry_same_request": False,
        "request_user_consent": True,
    }
    assert ret["requested_capability"] == {
        "kind": "named_service_operation",
        "resource": RESOURCE,
        "surface": "named_service",
        "outer_operation": "named_services_search",
        "namespace": "mail",
        "operation": "object.search",
    }


async def test_a_non_delegated_caller_still_gets_the_descriptor_answer():
    """Nothing to grant and no card to point at: the boundary is the descriptor
    itself, so the flat configuration error stays."""
    module = _bridge_module()
    bridge = _bridge(module, _request(delegated=False))

    denial = await bridge._authorize(
        _NarrowedPolicy(), "object.search", tool_name="named_services_search"
    )

    assert denial["error"] == "named_service_operation_not_configured"
