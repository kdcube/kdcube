# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Drift is computed from the card and two catalog generations, and explains
the card without changing it."""

import copy

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.drift import (
    DRIFT_BASELINE_MISSING,
    DRIFT_CHANGED,
    DRIFT_CURRENT,
    DRIFT_NO_RELEVANT_CHANGE,
    DRIFT_UNAVAILABLE,
    EFFECT_DENIED,
    card_drift,
    drift_unavailable,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.models import (
    CatalogDocument,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.model import (
    CardAuthority,
    NamedServiceSelection,
)

RESOURCE = "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"

NAMED_SERVICES = {
    "namespaces": {
        "mail": {
            "tools": {
                "search": {"operation": "object.search", "grants": ["mail:read"]},
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
                    "grants": ["named_services:use", "mail:read"],
                    "tools": {
                        "named_services_search": {"grants": ["named_services:use"]},
                        "named_services_schema": {"grants": ["named_services:use"]},
                    },
                    "named_services": copy.deepcopy(NAMED_SERVICES),
                },
            ],
        },
    },
}


def _document(connections=None) -> CatalogDocument:
    return CatalogDocument.build(connections if connections is not None else CONNECTIONS)


def _card(**overrides) -> CardAuthority:
    base = dict(
        access_id="aut_abc123",
        client_id="claude",
        grantor_subject="platform-user-1",
        delegate_subject="integration:claude:platform-user-1",
        source="oauth",
        card_revision=3,
        catalog_version="delegated_catalog_2026-08-09-09-00-00-000_a1b2c3d4e5f6",
        resource_grants={RESOURCE: ("named_services:use", "mail:read")},
        operations=("named_services_search", "named_services_schema"),
        named_service_operations=NamedServiceSelection.exact(
            {RESOURCE: {"mail": ["object.search", "object.schema"]}}
        ),
        expires_at=1_780_003_600,
    )
    base.update(overrides)
    return CardAuthority(**base)


def _trim(**changes) -> dict:
    trimmed = copy.deepcopy(CONNECTIONS)
    resource = trimmed["delegated_credentials"]["oauth"]["resources"][0]
    if changes.get("drop_resource"):
        trimmed["delegated_credentials"]["oauth"]["resources"] = []
        return trimmed
    if claim := changes.get("drop_claim"):
        resource["grants"] = [g for g in resource["grants"] if g != claim]
    if tool := changes.get("drop_tool"):
        resource["tools"].pop(tool, None)
    if ns_tool := changes.get("drop_named_service_tool"):
        resource["named_services"]["namespaces"]["mail"]["tools"].pop(ns_tool, None)
    if changes.get("drop_namespace"):
        resource["named_services"]["namespaces"].pop("mail", None)
    if added := changes.get("add_named_service_tool"):
        resource["named_services"]["namespaces"]["mail"]["tools"][added] = {
            "operation": f"object.{added}",
            "grants": ["named_services:use"],
        }
    if added_tool := changes.get("add_tool"):
        resource["tools"][added_tool] = {"grants": ["named_services:use"]}
    return trimmed


# -- status ---------------------------------------------------------------------


def test_a_card_on_the_active_version_is_current():
    active = _document()
    drift = card_drift(card=_card(catalog_version=active.version), active=active, baseline=active)

    assert drift["status"] == DRIFT_CURRENT
    assert drift["saved_version"] == active.version
    assert "removed" not in drift


def test_an_advanced_catalog_that_touches_nothing_is_not_a_change():
    baseline = _document()
    # A resource this card does not hold gains a tool.
    widened = copy.deepcopy(CONNECTIONS)
    widened["delegated_credentials"]["oauth"]["resources"].append(
        {"resource": "https://other.test/mcp", "grants": ["records:read"]}
    )
    active = _document(widened)

    drift = card_drift(card=_card(), active=active, baseline=baseline)

    assert drift["status"] == DRIFT_NO_RELEVANT_CHANGE
    assert drift["saved_version"] != drift["current_version"]


def test_a_missing_baseline_still_reports_removals_but_no_additions():
    active = _document(_trim(drop_named_service_tool="schema"))

    drift = card_drift(card=_card(), active=active, baseline=None, baseline_confirmed_absent=True)

    assert drift["status"] == DRIFT_BASELINE_MISSING
    assert drift["baseline_confirmed_absent"] is True
    assert [row["operation"] for row in drift["removed"]["named_service_operations"]] == [
        "object.schema"
    ]
    assert drift["added"] == {
        "claims": [],
        "outer_operations": [],
        "named_service_operations": [],
    }


def test_unavailability_is_its_own_status():
    payload = drift_unavailable("durable_active_unreadable")

    assert payload["status"] == DRIFT_UNAVAILABLE
    assert payload["reason"] == "durable_active_unreadable"


# -- removals -------------------------------------------------------------------


def test_a_removed_resource_is_reported_once_and_stops_there():
    active = _document(_trim(drop_resource=True))

    drift = card_drift(card=_card(), active=active, baseline=_document())

    assert drift["status"] == DRIFT_CHANGED
    assert drift["removed"]["resources"] == [
        {"resource": RESOURCE, "was_selected": True, "effect": EFFECT_DENIED}
    ]
    # Nothing beneath a removed resource is enumerated separately.
    assert drift["removed"]["claims"] == []
    assert drift["removed"]["named_service_operations"] == []


def test_a_removed_claim_is_reported_with_its_resource():
    active = _document(_trim(drop_claim="mail:read"))

    drift = card_drift(card=_card(), active=active, baseline=_document())

    assert drift["removed"]["claims"] == [
        {
            "resource": RESOURCE,
            "claim": "mail:read",
            "was_selected": True,
            "effect": EFFECT_DENIED,
        }
    ]


def test_a_removed_outer_operation_is_reported():
    active = _document(_trim(drop_tool="named_services_schema"))

    drift = card_drift(card=_card(), active=active, baseline=_document())

    assert drift["removed"]["outer_operations"] == [
        {
            "resource": RESOURCE,
            "operation": "named_services_schema",
            "was_selected": True,
            "effect": EFFECT_DENIED,
        }
    ]


def test_a_removed_namespace_reports_every_selected_operation_under_it():
    active = _document(_trim(drop_namespace=True))

    drift = card_drift(card=_card(), active=active, baseline=_document())

    assert sorted(
        row["operation"] for row in drift["removed"]["named_service_operations"]
    ) == ["object.schema", "object.search"]


def test_a_wildcard_card_is_compared_through_its_materialized_boundary():
    """"*" is not a live grant: what it expanded to at save time is."""
    card = _card(
        named_service_operations=NamedServiceSelection.all(),
        named_services=copy.deepcopy(NAMED_SERVICES),
    )
    active = _document(_trim(drop_named_service_tool="search"))

    drift = card_drift(card=card, active=active, baseline=_document())

    assert [row["operation"] for row in drift["removed"]["named_service_operations"]] == [
        "object.search"
    ]


def test_a_card_that_selected_nothing_reports_no_inner_removals():
    card = _card(named_service_operations=NamedServiceSelection.none(), named_services={})
    active = _document(_trim(drop_namespace=True))

    drift = card_drift(card=card, active=active, baseline=_document())

    assert drift["removed"]["named_service_operations"] == []


def test_a_resource_that_enumerates_no_named_services_reports_no_inner_removals():
    without_block = copy.deepcopy(CONNECTIONS)
    without_block["delegated_credentials"]["oauth"]["resources"][0].pop("named_services")

    drift = card_drift(card=_card(), active=_document(without_block), baseline=_document())

    assert drift["removed"]["named_service_operations"] == []


# -- additions ------------------------------------------------------------------


def test_a_new_named_service_operation_is_offered_unselected():
    active = _document(_trim(add_named_service_tool="comments"))

    drift = card_drift(card=_card(), active=active, baseline=_document())

    assert drift["status"] == DRIFT_CHANGED
    assert drift["added"]["named_service_operations"] == [
        {
            "resource": RESOURCE,
            "namespace": "mail",
            "operation": "object.comments",
            "selected": False,
        }
    ]


def test_a_new_outer_operation_is_offered_unselected():
    active = _document(_trim(add_tool="named_services_upsert"))

    drift = card_drift(card=_card(), active=active, baseline=_document())

    assert drift["added"]["outer_operations"] == [
        {"resource": RESOURCE, "operation": "named_services_upsert", "selected": False}
    ]


def test_additions_are_not_reported_for_resources_the_card_does_not_hold():
    widened = copy.deepcopy(CONNECTIONS)
    widened["delegated_credentials"]["oauth"]["resources"].append(
        {
            "resource": "https://other.test/mcp",
            "grants": ["records:read"],
            "tools": {"records_export": {"grants": ["records:read"]}},
        }
    )

    drift = card_drift(card=_card(), active=_document(widened), baseline=_document())

    assert drift["added"]["outer_operations"] == []
    assert drift["added"]["claims"] == []


def test_removal_and_addition_are_reported_together():
    active = _document(_trim(drop_named_service_tool="schema", add_named_service_tool="comments"))

    drift = card_drift(card=_card(), active=active, baseline=_document())

    assert drift["status"] == DRIFT_CHANGED
    assert [row["operation"] for row in drift["removed"]["named_service_operations"]] == [
        "object.schema"
    ]
    assert [row["operation"] for row in drift["added"]["named_service_operations"]] == [
        "object.comments"
    ]


# -- the administrator all-resource row does not mask a withdrawal --------------


def test_a_withdrawn_resource_reads_as_removed_even_beside_a_wildcard_row():
    """The deployment kept its admin `*` row and withdrew this card's door.

    Judged by the request URL, the `*` row would answer and the card would be
    described as intact; worse, the live run saw its claims reported as
    withdrawn and "already ineffective" while the calls kept working. The card
    holds no `*`, so the row is not its row: the resource itself is the removal.
    """
    withdrawn = copy.deepcopy(CONNECTIONS)
    withdrawn["delegated_credentials"]["oauth"]["resources"] = [
        {
            "resource": "*",
            "label": "All platform and application APIs",
            "admin_only": True,
            "grants": ["kdcube:role:super-admin"],
        }
    ]

    drift = card_drift(
        card=_card(),
        active=_document(withdrawn),
        baseline=_document(),
    )

    assert drift["status"] == DRIFT_CHANGED
    removed = drift["removed"]
    assert [row["resource"] for row in removed["resources"]] == [RESOURCE]
    assert removed["resources"][0]["effect"] == EFFECT_DENIED
    # What sits beneath a removed resource is not enumerated again, and none of
    # it is described against the admin row.
    assert removed["claims"] == []
    assert removed["outer_operations"] == []
    assert removed["named_service_operations"] == []


def test_an_administrator_card_reads_the_wildcard_row_as_its_own():
    """The mirror: a card that holds `*` is current against that row."""
    catalog = copy.deepcopy(CONNECTIONS)
    catalog["delegated_credentials"]["oauth"]["resources"].append(
        {
            "resource": "*",
            "label": "All platform and application APIs",
            "admin_only": True,
            "grants": ["kdcube:role:super-admin"],
        }
    )
    document = _document(catalog)
    card = _card(
        resource_grants={"*": ("kdcube:role:super-admin",)},
        operations=(),
        named_service_operations=NamedServiceSelection.none(),
    )

    drift = card_drift(card=card, active=document, baseline=document)

    assert drift["removed"]["resources"] == []
    assert drift["removed"]["claims"] == []
