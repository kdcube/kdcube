# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.tests.helpers import mount_test_oauth_adapter
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.clients import dcr_redirect_allowed, get_client
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import oauth_delegated_config


def test_delegated_client_routes_are_descriptor_gated():
    app = FastAPI()
    mount_test_oauth_adapter(app)

    response = TestClient(app).get("/.well-known/oauth-authorization-server")

    assert response.status_code == 404


def test_delegated_client_reads_explicit_connection_hub_mount_config():
    app = FastAPI()
    app.state.oauth_delegated_config = {
        "enabled": True,
        "issuer": "https://mcp.example.test",
        "tenant": "tenant-a",
        "project": "project-b",
        "auth_cookie_name": "__Secure-KDCUBE",
        "public_clients": [
            {
                "client_id": "local-client",
                "redirect_uris": ["http://localhost/callback"],
            },
        ],
        "dynamic_client_registration": {
            "allowed_redirect_uris": ["https://allowed.example.test/callback"],
        },
    }
    mount_test_oauth_adapter(app)

    cfg = oauth_delegated_config(app)
    assert cfg.enabled is True
    assert cfg.issuer == "https://mcp.example.test"
    assert cfg.tenant == "tenant-a"
    assert cfg.project == "project-b"
    assert cfg.auth_cookie_name == "__Secure-KDCUBE"
    assert cfg.brand == "KDCube"  # default when delegated_client brand is unset

    assert get_client("local-client", app) is not None
    assert get_client("claude", app) is None
    assert dcr_redirect_allowed("https://allowed.example.test/callback", app)
    assert not dcr_redirect_allowed("https://claude.ai/api/mcp/auth_callback", app)

    response = TestClient(app).get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    assert response.json()["issuer"] == "https://mcp.example.test"


def test_delegated_client_brand_defaults_to_kdcube():
    app = FastAPI()
    app.state.oauth_delegated_config = {"enabled": True}
    assert oauth_delegated_config(app).brand == "KDCube"


def test_delegated_client_brand_from_descriptor_is_reflected():
    app = FastAPI()
    app.state.oauth_delegated_config = {"enabled": True, "brand": "Acme AI"}
    assert oauth_delegated_config(app).brand == "Acme AI"


def test_delegated_client_parses_resource_capabilities_and_tools():
    app = FastAPI()
    app.state.oauth_delegated_config = {
        "enabled": True,
        "capabilities": [
            {
                "grant": "memories:read",
                "label": "Read memories",
                "delegable_roles": ["kdcube:role:chat-user"],
            },
            {
                "grant": "memories:maintain",
                "label": "Maintain memories",
                "delegable_roles": ["kdcube:role:privileged"],
            },
        ],
        "resources": [
            {
                "resource": "https://runtime.example.test/*/public/mcp/memories",
                "identity_scope": "grantor_identity_family",
                "tools": {
                    "memory_search": {
                        "label": "Search memories",
                        "grants": ["memories:read"],
                    },
                    "memory_reconcile": {
                        "label": "Reconcile memories",
                        "grants": ["memories:read", "memories:maintain"],
                    },
                },
            },
        ],
    }
    cfg = oauth_delegated_config(app)
    resource_cfg = cfg.resource_config("https://runtime.example.test/x/public/mcp/memories")

    assert resource_cfg is not None
    assert resource_cfg.identity_scope == "grantor_identity_family"

    assert cfg.supported_scopes("https://runtime.example.test/x/public/mcp/memories") == (
        "memories:read",
        "memories:maintain",
    )
    assert [tool.name for tool in cfg.tools_for_scopes(["memories:read"], resource="https://runtime.example.test/x/public/mcp/memories")] == ["memory_search"]
    assert [
        tool.name
        for tool in cfg.tools_for_scopes(
            ["memories:read", "memories:maintain"],
            resource="https://runtime.example.test/x/public/mcp/memories",
        )
    ] == ["memory_search", "memory_reconcile"]


def test_delegated_client_resource_collects_nested_named_service_grants():
    app = FastAPI()
    app.state.oauth_delegated_config = {
        "enabled": True,
        "capabilities": [
            {"grant": "named_services:use", "label": "Use named services"},
            {"grant": "memories:read", "label": "Read memories"},
            {"grant": "memories:write", "label": "Write memories"},
            {"grant": "tasks:read", "label": "Read tasks"},
            {"grant": "tasks:write", "label": "Write tasks"},
            {"grant": "canvas:read", "label": "Read canvas"},
            {"grant": "canvas:write", "label": "Write canvas"},
        ],
        "resources": [
            {
                "resource": "https://runtime.example.test/*/public/mcp/named_services",
                "tools": {
                    "named_services_schema": {"grants": ["named_services:use"]},
                    "named_services_search": {"grants": ["named_services:use"]},
                    "named_services_upsert": {"grants": ["named_services:use"]},
                    "named_services_host_file": {"grants": ["named_services:use"]},
                    "named_services_action": {"grants": ["named_services:use"]},
                    "named_services_delete": {"grants": ["named_services:use"]},
                },
                "named_services": {
                    "namespaces": {
                        "mem": {
                            "authority_id": "delegated_client",
                            "tools": {
                                "schema": {
                                    "operation": "object.schema",
                                    "grants": ["memories:read"],
                                },
                                "search": {
                                    "operation": "object.search",
                                    "grants": ["memories:read"],
                                },
                                "upsert": {
                                    "operation": "object.upsert",
                                    "label": "Write memory",
                                    "description": "Create or update a memory note.",
                                    "grants": ["memories:write"],
                                },
                                "action": {
                                    "operation": "object.action",
                                    "grants": ["memories:read"],
                                },
                                "delete": {
                                    "operation": "object.delete",
                                    "grants": ["memories:write"],
                                },
                            },
                        },
                        "task": {
                            "authority_id": "delegated_client",
                            "tools": {
                                "search": {
                                    "operation": "object.search",
                                    "grants": ["tasks:read"],
                                },
                                "upsert": {
                                    "operation": "object.upsert",
                                    "grants": ["tasks:write"],
                                },
                                "host_file": {
                                    "operation": "object.host_file",
                                    "grants": ["tasks:write"],
                                },
                                "delete": {
                                    "operation": "object.delete",
                                    "grants": ["tasks:write"],
                                },
                            },
                        },
                        "cnv": {
                            "authority_id": "delegated_client",
                            "tools": {
                                "search": {
                                    "operation": "object.search",
                                    "grants": ["canvas:read"],
                                },
                                "upsert": {
                                    "operation": "object.upsert",
                                    "grants": ["canvas:write"],
                                },
                            },
                        },
                    },
                },
            },
        ],
    }

    cfg = oauth_delegated_config(app)
    resource_cfg = cfg.resource_config("https://runtime.example.test/x/public/mcp/named_services")

    assert resource_cfg is not None
    assert resource_cfg.named_services["namespaces"]["mem"]["authority_id"] == "delegated_client"
    assert resource_cfg.named_services["namespaces"]["mem"]["tools"]["upsert"]["label"] == "Write memory"
    assert cfg.supported_scopes("https://runtime.example.test/x/public/mcp/named_services") == (
        "named_services:use",
        "memories:read",
        "memories:write",
        "tasks:read",
        "tasks:write",
        "canvas:read",
        "canvas:write",
    )
