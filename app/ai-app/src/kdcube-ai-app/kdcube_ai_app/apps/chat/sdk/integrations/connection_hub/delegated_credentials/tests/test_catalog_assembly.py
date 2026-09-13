# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.catalog.assembly import (  # noqa: E501
    CatalogAssemblyError,
    assemble_delegated_catalog,
    catalog_connection_differences,
)


NAMED_SERVICES_RESOURCE = "*/public/mcp/named_services*"


def _base() -> dict:
    return {
        "delegated_credentials": {
            "oauth": {
                "enabled": True,
                "capabilities": [{"grant": "base:read", "label": "Read base"}],
                "resources": [
                    {
                        "resource": NAMED_SERVICES_RESOURCE,
                        "label": "Named services",
                        "named_services": {"namespaces": {"base": {"label": "Base"}}},
                    }
                ],
            }
        }
    }


def _props(*, resource: str = "*/public/mcp/example*") -> dict:
    return {
        "delegated_catalog": {
            "version": "1",
            "capabilities": [{"grant": "example:read", "label": "Read example"}],
            "resources": [
                {
                    "resource": resource,
                    "label": "Example",
                    "tools": {"object.get": {"grants": ["example:read"]}},
                }
            ],
            "named_service_namespaces": [
                {
                    "resource": NAMED_SERVICES_RESOURCE,
                    "namespaces": {
                        "example": {
                            "label": "Example",
                            "tools": {
                                "get": {
                                    "operation": "object.get",
                                    "grants": ["named_services:use", "example:read"],
                                }
                            },
                        }
                    },
                }
            ],
        }
    }


def test_assembly_adds_app_owned_resources_and_shared_namespaces():
    result = assemble_delegated_catalog(
        base_connections=_base(),
        app_props={"example@1-0": _props(), "unrelated@1-0": {}},
    )

    oauth = result.connections["delegated_credentials"]["oauth"]
    assert result.contributors == ("example@1-0",)
    assert [row["grant"] for row in oauth["capabilities"]] == [
        "base:read",
        "example:read",
    ]
    assert [row["resource"] for row in oauth["resources"]] == [
        NAMED_SERVICES_RESOURCE,
        "*/public/mcp/example*",
    ]
    assert oauth["resources"][0]["named_services"]["namespaces"]["example"][
        "tools"
    ]["get"]["operation"] == "object.get"


def test_assembly_is_deterministic_by_bundle_id():
    first = assemble_delegated_catalog(
        base_connections=_base(),
        app_props={
            "zeta@1-0": _props(resource="*/zeta*"),
            "alpha@1-0": {
                "delegated_catalog": {
                    "version": "1",
                    "capabilities": [{"grant": "alpha:read"}],
                    "resources": [{"resource": "*/alpha*"}],
                }
            },
        },
    )
    second = assemble_delegated_catalog(
        base_connections=_base(),
        app_props={
            "alpha@1-0": {
                "delegated_catalog": {
                    "version": "1",
                    "capabilities": [{"grant": "alpha:read"}],
                    "resources": [{"resource": "*/alpha*"}],
                }
            },
            "zeta@1-0": _props(resource="*/zeta*"),
        },
    )

    assert first == second
    assert first.contributors == ("alpha@1-0", "zeta@1-0")


def test_two_apps_cannot_own_the_same_resource():
    with pytest.raises(CatalogAssemblyError) as captured:
        assemble_delegated_catalog(
            base_connections=_base(),
            app_props={
                "one@1-0": _props(resource="*/shared*"),
                "two@1-0": {
                    "delegated_catalog": {
                        "version": "1",
                        "resources": [{"resource": "*/shared*"}],
                    }
                },
            },
        )

    assert captured.value.code == "duplicate_resource"
    assert captured.value.owners == ("one@1-0", "two@1-0")
    assert "*/shared*" in str(captured.value)


def test_app_resource_cannot_shadow_connection_hub_resource():
    with pytest.raises(CatalogAssemblyError) as captured:
        assemble_delegated_catalog(
            base_connections=_base(),
            app_props={"example@1-0": _props(resource=NAMED_SERVICES_RESOURCE)},
        )

    assert captured.value.code == "duplicate_resource"
    assert captured.value.owners == ("connection-hub@1-0", "example@1-0")


def test_two_apps_cannot_own_the_same_named_service_namespace():
    other = _props(resource="*/other*")
    other["delegated_catalog"]["capabilities"][0]["grant"] = "other:read"
    with pytest.raises(CatalogAssemblyError) as captured:
        assemble_delegated_catalog(
            base_connections=_base(),
            app_props={"one@1-0": _props(), "two@1-0": other},
        )

    assert captured.value.code == "duplicate_named_service_namespace"
    assert captured.value.owners == ("one@1-0", "two@1-0")


def test_named_service_extension_requires_a_declared_resource():
    props = _props()
    props["delegated_catalog"]["named_service_namespaces"][0]["resource"] = "*/missing*"

    with pytest.raises(CatalogAssemblyError) as captured:
        assemble_delegated_catalog(
            base_connections=_base(),
            app_props={"example@1-0": props},
        )

    assert captured.value.code == "named_service_resource_not_declared"


def test_existing_base_rows_move_to_app_without_disturbing_shared_catalog():
    legacy = assemble_delegated_catalog(
        base_connections=_base(),
        app_props={"example@1-0": _props()},
    ).connections

    with pytest.raises(CatalogAssemblyError) as captured:
        assemble_delegated_catalog(
            base_connections=legacy,
            app_props={"example@1-0": _props()},
        )
    assert captured.value.code == "duplicate_capability"
    assert captured.value.owners == ("connection-hub@1-0", "example@1-0")

    oauth = legacy["delegated_credentials"]["oauth"]
    oauth["capabilities"] = [
        row for row in oauth["capabilities"] if row["grant"] != "example:read"
    ]
    oauth["resources"] = [
        row for row in oauth["resources"] if row["resource"] != "*/public/mcp/example*"
    ]
    shared = next(
        row for row in oauth["resources"] if row["resource"] == NAMED_SERVICES_RESOURCE
    )
    shared["named_services"]["namespaces"].pop("example")

    migrated = assemble_delegated_catalog(
        base_connections=legacy,
        app_props={"example@1-0": _props()},
    ).connections["delegated_credentials"]["oauth"]

    assert [row["grant"] for row in migrated["capabilities"]] == [
        "base:read",
        "example:read",
    ]
    assert [row["resource"] for row in migrated["resources"]] == [
        NAMED_SERVICES_RESOURCE,
        "*/public/mcp/example*",
    ]
    assert migrated["resources"][0]["named_services"]["namespaces"] == {
        "base": {"label": "Base"},
        "example": _props()["delegated_catalog"]["named_service_namespaces"][0][
            "namespaces"
        ]["example"],
    }


def test_declaration_version_is_explicit():
    props = _props()
    props["delegated_catalog"].pop("version")

    with pytest.raises(CatalogAssemblyError) as captured:
        assemble_delegated_catalog(
            base_connections=_base(),
            app_props={"example@1-0": props},
        )

    assert captured.value.code == "unsupported_catalog_declaration_version"


def test_catalog_diff_reports_missing_extra_and_changed_values():
    differences = catalog_connection_differences(
        expected={
            "delegated_credentials": {
                "oauth": {
                    "capabilities": [
                        {"grant": "expected", "label": "Expected"},
                    ],
                    "resources": [{"resource": "*/expected*", "label": "Expected"}],
                }
            }
        },
        actual={
            "delegated_credentials": {
                "oauth": {
                    "capabilities": [
                        {"grant": "expected", "label": "Changed"},
                        {"grant": "extra"},
                    ],
                    "resources": [],
                    "unexpected": True,
                }
            }
        },
    )

    assert [(row["kind"], row["path"]) for row in differences] == [
        (
            "value_mismatch",
            "connections.delegated_credentials.oauth.capabilities.expected.label",
        ),
        (
            "extra",
            "connections.delegated_credentials.oauth.capabilities.extra",
        ),
        (
            "missing",
            "connections.delegated_credentials.oauth.resources.*/expected*",
        ),
        ("extra", "connections.delegated_credentials.oauth.unexpected"),
    ]


def test_catalog_diff_reports_duplicate_served_rows():
    differences = catalog_connection_differences(
        expected={"delegated_credentials": {"oauth": {"resources": []}}},
        actual={
            "delegated_credentials": {
                "oauth": {
                    "resources": [
                        {"resource": "*/duplicate*"},
                        {"resource": "*/duplicate*"},
                    ]
                }
            }
        },
    )

    assert any(
        row["kind"] == "duplicate" and row["path"].endswith("[*/duplicate*]")
        for row in differences
    )
