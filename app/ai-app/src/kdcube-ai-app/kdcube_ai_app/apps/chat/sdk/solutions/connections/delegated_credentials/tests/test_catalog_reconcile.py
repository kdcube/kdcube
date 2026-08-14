# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Save prunes what the catalog no longer offers, and adds nothing."""

import copy

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.models import (
    CatalogDocument,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.reconcile import (
    reconcile_selection,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.model import (
    NamedServiceSelection,
)

RESOURCE = "https://example.test/mcp/named-services"
OTHER = "https://example.test/mcp/records"

CONNECTIONS = {
    "delegated_credentials": {
        "oauth": {
            "enabled": True,
            "resources": [
                {
                    "resource": RESOURCE,
                    "grants": ["named_services:use", "mail:read"],
                    "tools": {"named_services_search": {"grants": ["named_services:use"]}},
                    "named_services": {
                        "namespaces": {
                            "mail": {
                                "tools": {
                                    "search": {"operation": "object.search", "grants": ["mail:read"]},
                                    "schema": {"operation": "object.schema", "grants": ["mail:read"]},
                                },
                            },
                        },
                    },
                },
            ],
        },
    },
}


def _document(connections=None) -> CatalogDocument:
    return CatalogDocument.build(connections if connections is not None else CONNECTIONS)


def _without(*, claim: str = "", operation_tool: str = "") -> dict:
    trimmed = copy.deepcopy(CONNECTIONS)
    resource = trimmed["delegated_credentials"]["oauth"]["resources"][0]
    if claim:
        resource["grants"] = [g for g in resource["grants"] if g != claim]
    if operation_tool:
        resource["named_services"]["namespaces"]["mail"]["tools"].pop(operation_tool)
    return trimmed


def _reconcile(grants, selection, connections=None):
    return reconcile_selection(
        resource_grants=grants,
        named_service_operations=selection,
        active=_document(connections),
    )


def test_a_selection_the_catalog_still_offers_survives_untouched():
    result = _reconcile(
        {RESOURCE: ["named_services:use", "mail:read"]},
        NamedServiceSelection.exact({RESOURCE: {"mail": ["object.search"]}}),
    )

    assert result.anything_pruned is False
    assert result.resource_grants == {RESOURCE: ["named_services:use", "mail:read"]}
    assert result.named_service_operations.operations == {
        RESOURCE: {"mail": ("object.search",)}
    }


def test_a_resource_the_catalog_dropped_is_pruned_with_everything_under_it():
    result = _reconcile(
        {RESOURCE: ["named_services:use"], OTHER: ["records:read"]},
        NamedServiceSelection.exact({OTHER: {"mail": ["object.search"]}}),
    )

    assert result.pruned_resources == [OTHER]
    assert OTHER not in result.resource_grants
    assert result.pruned_named_service_operations == [
        {"resource": OTHER, "namespace": "mail", "operation": "object.search"}
    ]


def test_a_claim_the_catalog_dropped_is_pruned_and_named():
    result = _reconcile(
        {RESOURCE: ["named_services:use", "mail:read"]},
        NamedServiceSelection.none(),
        connections=_without(claim="mail:read"),
    )

    assert result.pruned_claims == [{"resource": RESOURCE, "claim": "mail:read"}]
    assert result.resource_grants == {RESOURCE: ["named_services:use"]}


def test_an_operation_the_catalog_dropped_is_pruned_and_its_siblings_stay():
    result = _reconcile(
        {RESOURCE: ["named_services:use", "mail:read"]},
        NamedServiceSelection.exact({RESOURCE: {"mail": ["object.search", "object.schema"]}}),
        connections=_without(operation_tool="schema"),
    )

    assert result.pruned_named_service_operations == [
        {"resource": RESOURCE, "namespace": "mail", "operation": "object.schema"}
    ]
    assert result.named_service_operations.operations == {
        RESOURCE: {"mail": ("object.search",)}
    }


def test_a_wildcard_is_kept_as_a_wildcard():
    """"*" means everything shown by the catalog this save acknowledges."""
    result = _reconcile(
        {RESOURCE: ["named_services:use"]},
        NamedServiceSelection.all(),
        connections=_without(operation_tool="schema"),
    )

    assert result.named_service_operations.is_all
    assert result.pruned_named_service_operations == []


def test_an_explicit_empty_selection_stays_empty():
    result = _reconcile({RESOURCE: ["named_services:use"]}, NamedServiceSelection.none())

    assert result.named_service_operations.is_none
    assert result.anything_pruned is False


def test_pruning_never_adds_what_the_catalog_gained():
    widened = copy.deepcopy(CONNECTIONS)
    namespaces = widened["delegated_credentials"]["oauth"]["resources"][0]["named_services"]["namespaces"]
    namespaces["mail"]["tools"]["comments"] = {
        "operation": "object.comments",
        "grants": ["mail:read"],
    }

    result = _reconcile(
        {RESOURCE: ["named_services:use"]},
        NamedServiceSelection.exact({RESOURCE: {"mail": ["object.search"]}}),
        connections=widened,
    )

    assert result.named_service_operations.operations == {
        RESOURCE: {"mail": ("object.search",)}
    }


def test_a_resource_that_enumerates_no_claims_carries_no_ceiling():
    without_grants = copy.deepcopy(CONNECTIONS)
    resource = without_grants["delegated_credentials"]["oauth"]["resources"][0]
    resource.pop("grants")
    resource.pop("tools")
    resource.pop("named_services")

    result = _reconcile({RESOURCE: ["anything:at:all"]}, NamedServiceSelection.none(), without_grants)

    assert result.pruned_claims == []
    assert result.resource_grants == {RESOURCE: ["anything:at:all"]}


def test_pruning_everything_leaves_a_card_with_no_authority():
    result = _reconcile(
        {RESOURCE: ["mail:read"]},
        NamedServiceSelection.none(),
        connections=_without(claim="mail:read"),
    )

    assert result.empty is True
