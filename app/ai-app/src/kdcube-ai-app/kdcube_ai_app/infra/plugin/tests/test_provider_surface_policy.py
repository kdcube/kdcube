# SPDX-License-Identifier: MIT

from __future__ import annotations

from kdcube_ai_app.infra.plugin.bundle_loader import (
    APIEndpointSpec,
    MCPEndpointSpec,
    UIWidgetSpec,
    apply_api_overrides,
    apply_bundle_overrides,
    apply_mcp_overrides,
    apply_widget_overrides,
    canonical_provider_surface_path,
    provider_surface_auth,
    BundleInterfaceManifest,
)


def test_provider_surface_policy_is_route_and_method_aware() -> None:
    props = {
        "surfaces": {
            "as_provider": {
                "api": {
                    "operations": {
                        "admin_data": {
                            "POST": {
                                "visibility": {
                                    "user_types": ["privileged"],
                                    "roles": ["kdcube:role:super-admin"],
                                },
                                "auth": {
                                    "authority_id": "platform",
                                    "grants": ["admin:data"],
                                },
                            },
                        },
                    },
                    "public": {
                        "admin_data": {
                            "GET": {
                                "visibility": {
                                    "user_types": [],
                                    "roles": [],
                                },
                            },
                        },
                    },
                },
            },
        },
    }

    operations = APIEndpointSpec(
        method_name="admin_data",
        alias="admin_data",
        http_method="POST",
        route="operations",
        user_types=(),
        roles=(),
    )
    public = APIEndpointSpec(
        method_name="admin_data_public",
        alias="admin_data",
        http_method="GET",
        route="public",
        user_types=("registered",),
        roles=("legacy",),
    )

    effective_operations = apply_api_overrides(operations, props)
    effective_public = apply_api_overrides(public, props)

    assert effective_operations.user_types == ("privileged",)
    assert effective_operations.roles == ("kdcube:role:super-admin",)
    assert effective_public.user_types == ()
    assert effective_public.roles == ()
    assert provider_surface_auth(
        props,
        "api",
        alias="admin_data",
        http_method="POST",
        route="operations",
    ) == {"authority_id": "platform", "grants": ["admin:data"]}
    assert canonical_provider_surface_path(
        "api",
        alias="admin_data",
        http_method="POST",
        route="operations",
    ) == "surfaces.as_provider.api.operations.admin_data.POST"


def test_provider_surface_policy_allows_api_alias_level_defaults() -> None:
    props = {
        "surfaces": {
            "as_provider": {
                "api": {
                    "operations": {
                        "status": {
                            "visibility": {
                                "user_types": [],
                                "roles": ["kdcube:role:super-admin"],
                            },
                            "auth": {
                                "authority_id": "platform",
                                "grants": ["status:read"],
                            },
                        },
                    },
                },
            },
        },
    }
    spec = APIEndpointSpec(
        method_name="status",
        alias="status",
        http_method="GET",
        route="operations",
        user_types=("registered",),
        roles=(),
    )

    effective = apply_api_overrides(spec, props)

    assert effective.user_types == ()
    assert effective.roles == ("kdcube:role:super-admin",)
    assert provider_surface_auth(
        props,
        "api",
        alias="status",
        http_method="GET",
        route="operations",
    ) == {"authority_id": "platform", "grants": ["status:read"]}


def test_provider_surface_policy_controls_bundle_widget_and_mcp() -> None:
    props = {
        "surfaces": {
            "as_provider": {
                "bundle": {"visibility": {"allowed_roles": ["kdcube:role:editor"]}},
                "widget": {
                    "settings": {
                        "visibility": {
                            "user_types": ["privileged"],
                            "roles": ["kdcube:role:super-admin"],
                        },
                    },
                },
                "mcp": {
                    "knowledge": {
                        "auth": {
                            "mode": "managed",
                            "authority_id": "custom.identity",
                            "grants": ["knowledge:read"],
                        },
                    },
                },
            },
        },
    }

    manifest = BundleInterfaceManifest(bundle_id="example@1-0")
    widget = UIWidgetSpec(method_name="settings", alias="settings", icon={})
    mcp = MCPEndpointSpec(
        method_name="knowledge",
        alias="knowledge",
        auth={"mode": "header", "header": "X-Knowledge-MCP-Token"},
    )

    assert apply_bundle_overrides(manifest, props).allowed_roles == ("kdcube:role:editor",)
    assert apply_widget_overrides(widget, props).user_types == ("privileged",)
    assert apply_widget_overrides(widget, props).roles == ("kdcube:role:super-admin",)
    assert apply_mcp_overrides(mcp, props).auth == {
        "mode": "managed",
        "authority_id": "custom.identity",
        "grants": ["knowledge:read"],
    }
