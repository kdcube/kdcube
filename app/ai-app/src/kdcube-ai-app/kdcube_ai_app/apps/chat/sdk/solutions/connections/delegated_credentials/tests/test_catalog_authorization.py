# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

import copy

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.authorization import (
    CAPABILITY_NAMED_SERVICE_NAMESPACE,
    CAPABILITY_NAMED_SERVICE_OPERATION,
    CAPABILITY_OUTER_OPERATION,
    CAPABILITY_RESOURCE,
    CAPABILITY_RESOURCE_CLAIM,
    DENIAL_CODE,
    DENIAL_REASON,
    ActiveCatalogCapabilities,
    CapabilityRequest,
    CardProvenance,
    authorize_current_capability,
    catalog_unavailable_denial,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.models import (
    CatalogDocument,
)

SELECTOR = "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
REQUEST_URL = (
    "https://kdcube.example/api/integrations/bundles/acme/prod/"
    "kdcube-services@1-0/public/mcp/named_services"
)

CONNECTIONS = {
    "delegated_credentials": {
        "oauth": {
            "enabled": True,
            "resources": [
                {
                    "resource": SELECTOR,
                    "label": "KDCube named services MCP",
                    "tools": {
                        "named_services_schema": {"grants": ["named_services:use"]},
                        "named_services_search": {"grants": ["named_services:use"]},
                    },
                    "named_services": {
                        "namespaces": {
                            "mail": {
                                "tools": {
                                    "schema": {
                                        "operation": "object.schema",
                                        "grants": ["named_services:use"],
                                    },
                                    "search": {
                                        "operation": "object.search",
                                        "grants": ["named_services:use", "mail:read"],
                                    },
                                },
                            },
                            "linkedin": {
                                "tools": {
                                    "call": {
                                        "operations": {
                                            "object.action.publish": {"grants": ["linkedin:write"]},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            ],
        },
    },
}

PROVENANCE = CardProvenance(
    access_id="oauth-5aa44826664a0bdd",
    card_revision=8,
    catalog_version="delegated_catalog_2026-08-09-09-00-00-000_a1b2c3d4e5f6",
)


def _catalog(connections=None) -> ActiveCatalogCapabilities:
    return ActiveCatalogCapabilities(
        CatalogDocument.build(connections if connections is not None else CONNECTIONS)
    )


def _authorize(request, *, connections=None):
    return authorize_current_capability(
        catalog=_catalog(connections), provenance=PROVENANCE, request=request
    )


def _without_namespace(name: str) -> dict:
    trimmed = copy.deepcopy(CONNECTIONS)
    resource = trimmed["delegated_credentials"]["oauth"]["resources"][0]
    resource["named_services"]["namespaces"].pop(name)
    return trimmed


# -- what the active catalog still offers --------------------------------------


@pytest.mark.parametrize(
    "request_",
    [
        CapabilityRequest(kind=CAPABILITY_RESOURCE, resource=SELECTOR, request_resource=REQUEST_URL),
        CapabilityRequest(
            kind=CAPABILITY_RESOURCE_CLAIM,
            resource=SELECTOR,
            request_resource=REQUEST_URL,
            claim="named_services:use",
        ),
        CapabilityRequest(
            kind=CAPABILITY_OUTER_OPERATION,
            resource=SELECTOR,
            request_resource=REQUEST_URL,
            surface="mcp",
            outer_operation="named_services_schema",
        ),
        CapabilityRequest(
            kind=CAPABILITY_NAMED_SERVICE_NAMESPACE,
            resource=SELECTOR,
            request_resource=REQUEST_URL,
            surface="mcp",
            namespace="mail",
        ),
        CapabilityRequest(
            kind=CAPABILITY_NAMED_SERVICE_OPERATION,
            resource=SELECTOR,
            request_resource=REQUEST_URL,
            surface="mcp",
            namespace="mail",
            operation="object.schema",
        ),
    ],
)
def test_a_capability_the_catalog_still_offers_is_allowed(request_):
    assert _authorize(request_) is None


def test_nested_operations_mapping_is_read_as_well_as_the_flat_form():
    allowed = CapabilityRequest(
        kind=CAPABILITY_NAMED_SERVICE_OPERATION,
        resource=SELECTOR,
        request_resource=REQUEST_URL,
        surface="mcp",
        namespace="linkedin",
        operation="object.action.publish",
    )
    assert _authorize(allowed) is None


def test_a_namespace_key_is_matched_after_normalization():
    request_ = CapabilityRequest(
        kind=CAPABILITY_NAMED_SERVICE_NAMESPACE,
        resource=SELECTOR,
        request_resource=REQUEST_URL,
        surface="mcp",
        namespace="Mail:",
    )
    assert _authorize(request_) is None


# -- what the active catalog no longer offers ----------------------------------


def test_a_removed_namespace_denies_and_names_the_whole_path():
    request_ = CapabilityRequest(
        kind=CAPABILITY_NAMED_SERVICE_OPERATION,
        resource=SELECTOR,
        request_resource=REQUEST_URL,
        surface="mcp",
        outer_operation="named_services_schema",
        namespace="mail",
        operation="object.schema",
    )
    denial = _authorize(request_, connections=_without_namespace("mail"))

    assert denial is not None
    assert denial["ok"] is False
    assert denial["error"]["code"] == DENIAL_CODE
    assert denial["error"]["retryable"] is False
    ret = denial["ret"]
    assert ret["reason"] == DENIAL_REASON
    assert ret["access_id"] == PROVENANCE.access_id
    assert ret["card_revision"] == 8
    assert ret["card_catalog_version"] == PROVENANCE.catalog_version
    assert ret["active_catalog_version"] != PROVENANCE.catalog_version
    assert ret["recovery"]["retry_same_request"] is False
    assert ret["requested_capability"] == {
        "kind": CAPABILITY_NAMED_SERVICE_OPERATION,
        "resource": SELECTOR,
        "request_resource": REQUEST_URL,
        "surface": "mcp",
        "outer_operation": "named_services_schema",
        "namespace": "mail",
        "operation": "object.schema",
    }


def test_a_removed_operation_denies_while_its_namespace_survives():
    trimmed = copy.deepcopy(CONNECTIONS)
    namespaces = trimmed["delegated_credentials"]["oauth"]["resources"][0]["named_services"]["namespaces"]
    namespaces["mail"]["tools"].pop("schema")

    denied = CapabilityRequest(
        kind=CAPABILITY_NAMED_SERVICE_OPERATION,
        resource=SELECTOR,
        request_resource=REQUEST_URL,
        surface="mcp",
        namespace="mail",
        operation="object.schema",
    )
    still_offered = CapabilityRequest(
        kind=CAPABILITY_NAMED_SERVICE_NAMESPACE,
        resource=SELECTOR,
        request_resource=REQUEST_URL,
        surface="mcp",
        namespace="mail",
    )

    assert _authorize(denied, connections=trimmed) is not None
    assert _authorize(still_offered, connections=trimmed) is None


def test_a_removed_claim_denies_even_though_the_resource_survives():
    trimmed = copy.deepcopy(CONNECTIONS)
    resource = trimmed["delegated_credentials"]["oauth"]["resources"][0]
    resource["named_services"]["namespaces"]["mail"]["tools"]["search"]["grants"] = [
        "named_services:use"
    ]

    denied = CapabilityRequest(
        kind=CAPABILITY_RESOURCE_CLAIM,
        resource=SELECTOR,
        request_resource=REQUEST_URL,
        claim="mail:read",
    )
    resource_itself = CapabilityRequest(
        kind=CAPABILITY_RESOURCE, resource=SELECTOR, request_resource=REQUEST_URL
    )

    assert _authorize(denied, connections=trimmed) is not None
    assert _authorize(resource_itself, connections=trimmed) is None


def test_a_removed_outer_operation_denies():
    trimmed = copy.deepcopy(CONNECTIONS)
    trimmed["delegated_credentials"]["oauth"]["resources"][0]["tools"].pop("named_services_schema")
    request_ = CapabilityRequest(
        kind=CAPABILITY_OUTER_OPERATION,
        resource=SELECTOR,
        request_resource=REQUEST_URL,
        surface="mcp",
        outer_operation="named_services_schema",
    )
    assert _authorize(request_, connections=trimmed) is not None


def test_a_removed_resource_denies_every_kind_beneath_it():
    empty = {"delegated_credentials": {"oauth": {"enabled": True, "resources": []}}}
    for request_ in (
        CapabilityRequest(kind=CAPABILITY_RESOURCE, resource=SELECTOR, request_resource=REQUEST_URL),
        CapabilityRequest(
            kind=CAPABILITY_RESOURCE_CLAIM,
            resource=SELECTOR,
            request_resource=REQUEST_URL,
            claim="named_services:use",
        ),
        CapabilityRequest(
            kind=CAPABILITY_NAMED_SERVICE_OPERATION,
            resource=SELECTOR,
            request_resource=REQUEST_URL,
            surface="mcp",
            namespace="mail",
            operation="object.schema",
        ),
    ):
        assert _authorize(request_, connections=empty) is not None


def test_a_namespace_block_emptied_of_namespaces_is_a_removal_not_an_absent_section():
    emptied = copy.deepcopy(CONNECTIONS)
    resource = emptied["delegated_credentials"]["oauth"]["resources"][0]
    resource["named_services"]["namespaces"] = {}

    request_ = CapabilityRequest(
        kind=CAPABILITY_NAMED_SERVICE_OPERATION,
        resource=SELECTOR,
        request_resource=REQUEST_URL,
        surface="mcp",
        namespace="mail",
        operation="object.schema",
    )
    assert _authorize(request_, connections=emptied) is not None


def test_a_resource_that_enumerates_no_named_services_carries_no_inner_ceiling():
    without_block = copy.deepcopy(CONNECTIONS)
    without_block["delegated_credentials"]["oauth"]["resources"][0].pop("named_services")

    request_ = CapabilityRequest(
        kind=CAPABILITY_NAMED_SERVICE_OPERATION,
        resource=SELECTOR,
        request_resource=REQUEST_URL,
        surface="mcp",
        namespace="mail",
        operation="object.schema",
    )
    assert _authorize(request_, connections=without_block) is None


def test_a_resource_that_enumerates_no_tools_carries_no_outer_ceiling():
    without_tools = {
        "delegated_credentials": {
            "oauth": {
                "enabled": True,
                "resources": [{"resource": "*", "grants": ["kdcube:role:super-admin"]}],
            }
        }
    }
    request_ = CapabilityRequest(
        kind=CAPABILITY_OUTER_OPERATION,
        resource="*",
        request_resource=REQUEST_URL,
        surface="rest",
        outer_operation="anything",
    )
    assert _authorize(request_, connections=without_tools) is None


def test_an_empty_catalog_body_denies_rather_than_permitting_everything():
    request_ = CapabilityRequest(
        kind=CAPABILITY_NAMED_SERVICE_OPERATION,
        resource=SELECTOR,
        request_resource=REQUEST_URL,
        surface="mcp",
        namespace="mail",
        operation="object.schema",
    )
    assert _authorize(request_, connections={}) is not None


# -- denial shape ---------------------------------------------------------------


def test_the_path_carries_only_the_fields_its_kind_requires():
    request_ = CapabilityRequest(
        kind=CAPABILITY_RESOURCE_CLAIM,
        resource=SELECTOR,
        request_resource=REQUEST_URL,
        surface="mcp",
        outer_operation="named_services_schema",
        claim="mail:read",
        namespace="mail",
        operation="object.schema",
    )
    empty = {"delegated_credentials": {"oauth": {"enabled": True, "resources": []}}}
    path = _authorize(request_, connections=empty)["ret"]["requested_capability"]

    assert path == {
        "kind": CAPABILITY_RESOURCE_CLAIM,
        "resource": SELECTOR,
        "request_resource": REQUEST_URL,
        "claim": "mail:read",
    }


def test_a_denial_never_names_the_leaf_operation_alone():
    request_ = CapabilityRequest(
        kind=CAPABILITY_NAMED_SERVICE_OPERATION,
        resource=SELECTOR,
        request_resource=REQUEST_URL,
        surface="mcp",
        namespace="mail",
        operation="object.schema",
    )
    path = _authorize(request_, connections=_without_namespace("mail"))["ret"][
        "requested_capability"
    ]
    assert {"resource", "surface", "namespace", "operation"}.issubset(path)


def test_unavailability_is_retryable_and_distinct_from_removal():
    payload = catalog_unavailable_denial("cache_unavailable")
    assert payload["ok"] is False
    assert payload["error"]["retryable"] is True
    assert payload["error"]["code"] != DENIAL_CODE
    assert payload["ret"]["reason"] == "cache_unavailable"
