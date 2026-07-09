"""The delegated-access map is a faithful read-only projection of
``config.connections.*`` — grant vocabulary, resource exposure with
per-namespace operation grants, and the provider-backed claim vocabulary.
Nothing invented, nothing secret, and referenced-but-undeclared grants are
flagged instead of silently rendered."""

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.access_map import (
    build_delegated_access_map,
)


def _config() -> dict:
    return {
        "delegated_credentials": {
            "oauth": {
                "enabled": True,
                "capabilities": [
                    {
                        "grant": "kdcube:role:super-admin",
                        "label": "Use all platform and application APIs",
                        "description": "Admin-only delegated automation access.",
                        "admin_only": True,
                        "delegable_roles": ["kdcube:role:super-admin"],
                        "delegable_permissions": ["kdcube:role:super-admin"],
                    },
                    {
                        "grant": "memories:read",
                        "label": "Read KDCube memories",
                        "description": "Read the user's memory notes.",
                        "delegable_roles": ["kdcube:role:chat-user"],
                        "delegable_permissions": ["memories:read"],
                    },
                    {
                        "grant": "memories:write",
                        "label": "Write KDCube memories",
                        "description": "Create or update memory notes.",
                        "delegable_roles": ["kdcube:role:chat-user"],
                        "delegable_permissions": ["memories:write"],
                    },
                ],
                "resources": [
                    {
                        "resource": "*",
                        "label": "All platform and application APIs",
                        "admin_only": True,
                        "grants": ["kdcube:role:super-admin"],
                    },
                    {
                        "resource": "*/public/mcp/named_services*",
                        "label": "KDCube named services MCP",
                        "tools": {
                            "named_services_list": {
                                "label": "List named services",
                                "description": "List configured namespaces.",
                                "grants": ["named_services:use"],
                            },
                        },
                        "namespaces": {
                            "mem": {
                                "label": "User memories",
                                "description": "Memory notes for the approving user.",
                                "authority_id": "delegated_client",
                                "tools": {
                                    "search": {
                                        "operation": "object.search",
                                        "label": "Search memories",
                                        "grants": ["memories:read"],
                                    },
                                    "upsert": {
                                        "operation": "object.upsert",
                                        "label": "Write memory",
                                        "grants": ["memories:write"],
                                    },
                                    # Generic dispatch: contributes grants to
                                    # the union, produces no entry row.
                                    "call": {
                                        "label": "Generic memory call",
                                        "operations": {
                                            "object.delete": {"grants": ["memories:write"]},
                                        },
                                    },
                                },
                            },
                        },
                    },
                ],
            },
        },
        "delegated_to_kdcube": {
            "enabled": True,
            "providers": {
                "google": {
                    "label": "Google",
                    "enabled": True,
                    "connector_apps": {
                        "gmail": {
                            "label": "Gmail",
                            "enabled": True,
                            "client_id": "SECRET-ISH-NOT-COPIED",
                            "client_secret_ref": "connections....client_secret",
                            "allowed_claims": ["gmail:read", "gmail:send"],
                        },
                    },
                    "claims": {
                        "gmail:read": {"label": "Read Gmail", "description": "Search and read Gmail."},
                        "gmail:send": {"label": "Send Gmail", "description": "Send email."},
                    },
                },
            },
        },
    }


def test_access_map_resolves_grants_resources_namespaces_and_providers():
    out = build_delegated_access_map(_config())
    assert out["enabled"] is True

    grants = {row["grant"]: row for row in out["grants"]}
    assert grants["kdcube:role:super-admin"]["admin_only"] is True
    assert grants["memories:read"]["delegable_permissions"] == ["memories:read"]

    resources = {row["resource"]: row for row in out["resources"]}
    star = resources["*"]
    assert star["admin_only"] is True
    assert star["grant_union"] == ["kdcube:role:super-admin"]

    ns_resource = resources["*/public/mcp/named_services*"]
    assert [t["name"] for t in ns_resource["tools"]] == ["named_services_list"]
    mem = ns_resource["namespaces"][0]
    assert mem["namespace"] == "mem"
    assert mem["authority_id"] == "delegated_client"
    # Entry rows: single-operation tools only; the generic call block adds
    # its per-operation grants to the union without an entry row.
    assert {(e["tool"], e["operation"]) for e in mem["entries"]} == {
        ("search", "object.search"), ("upsert", "object.upsert"),
    }
    assert mem["grants"] == ["memories:read", "memories:write"]
    assert set(ns_resource["grant_union"]) == {"memories:read", "memories:write", "named_services:use"}

    providers = {row["provider_id"]: row for row in out["providers"]}
    google = providers["google"]
    assert google["connector_apps"][0]["allowed_claims"] == ["gmail:read", "gmail:send"]
    assert {c["claim"] for c in google["claims"]} == {"gmail:read", "gmail:send"}
    # No secret material rides the view.
    flat = repr(out)
    assert "SECRET-ISH-NOT-COPIED" not in flat
    assert "client_secret" not in flat

    # named_services:use is referenced by the resource but not declared in
    # the capabilities vocabulary — flagged, not hidden.
    assert out["unknown_grants"] == ["named_services:use"]


def test_access_map_is_total_over_missing_config():
    assert build_delegated_access_map(None) == {
        "enabled": False, "grants": [], "resources": [], "providers": [], "unknown_grants": [],
    }
    assert build_delegated_access_map({"delegated_credentials": {}})["resources"] == []
