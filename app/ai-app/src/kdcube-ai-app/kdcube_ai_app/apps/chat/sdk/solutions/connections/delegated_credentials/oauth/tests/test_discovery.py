# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Tests for delegated credential OAuth discovery metadata.

These cover the RFC 9728 (protected-resource) -> RFC 8414 (authorization-server)
handshake that Claude Code's MCP client uses to discover how to authenticate
against a concrete bundle MCP endpoint. All pure / deterministic; no Redis or DB needed.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth import (
    authorization_server_metadata,
    protected_resource_metadata,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.tests.helpers import mount_test_oauth_adapter
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.tests.helpers import enable_delegated_client

ISSUER = "https://connector.example.test"
TEST_ICON = f"{ISSUER}/img/favicon.svg"
TEST_WEBSITE = ISSUER
TEST_ICON_DESCRIPTOR = {
    "src": TEST_ICON,
    "mimeType": "image/svg+xml",
    "sizes": ["64x64"],
}


# ----------------------------- pure builders -----------------------------

def test_authorization_server_metadata_required_fields():
    md = authorization_server_metadata(ISSUER)

    assert md["issuer"] == ISSUER
    assert md["authorization_endpoint"] == f"{ISSUER}/oauth/authorize"
    assert md["token_endpoint"] == f"{ISSUER}/oauth/token"
    assert "authorization_code" in md["grant_types_supported"]
    assert md["response_types_supported"] == ["code"]
    assert md["code_challenge_methods_supported"] == ["S256"]
    assert md["token_endpoint_auth_methods_supported"] == ["none"]
    assert md["authorization_response_iss_parameter_supported"] is True
    assert md["scopes_supported"] == []


def test_authorization_server_metadata_omits_jwks_uri():
    # Tokens are opaque (kst1) -> no asymmetric signing, so jwks_uri must be absent.
    md = authorization_server_metadata(ISSUER)
    assert "jwks_uri" not in md


def test_protected_resource_metadata_points_at_as():
    resource = "https://connector.example.test/api/integrations/bundles/demo/prod/app@1/public/mcp/export"
    md = protected_resource_metadata(ISSUER, resource=resource)

    assert md["resource"] == resource
    assert md["authorization_servers"] == [ISSUER]
    assert md["scopes_supported"] == []


# ----------------------------- served endpoints -----------------------------

@pytest.fixture
def client():
    app = FastAPI()
    enable_delegated_client(app, issuer=ISSUER)
    mount_test_oauth_adapter(app)
    return TestClient(app)


def test_well_known_authorization_server_served(client):
    resp = client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200
    assert resp.json() == authorization_server_metadata(
        ISSUER,
        service_name="KDCube",
        logo_uri=TEST_ICON,
        client_uri=TEST_WEBSITE,
        icons=[TEST_ICON_DESCRIPTOR],
        scopes_supported=["records:read"],
    )


def test_well_known_openid_configuration_alias_served(client):
    resp = client.get("/.well-known/openid-configuration")
    assert resp.status_code == 200
    data = resp.json()
    assert data == authorization_server_metadata(
        ISSUER,
        service_name="KDCube",
        logo_uri=TEST_ICON,
        client_uri=TEST_WEBSITE,
        icons=[TEST_ICON_DESCRIPTOR],
        scopes_supported=["records:read"],
    )
    assert data["registration_endpoint"] == f"{ISSUER}/oauth/register"


def test_well_known_protected_resource_served(client):
    resource = "https://connector.example.test/api/integrations/bundles/demo/prod/app@1/public/mcp/export"
    resp = client.get("/.well-known/oauth-protected-resource", params={"resource": resource})
    assert resp.status_code == 200
    data = resp.json()
    assert data["resource"] == resource
    assert data["authorization_servers"] == [ISSUER]
    assert data["scopes_supported"] == ["records:read"]
    assert data["resource_name"] == "KDCube"
    assert data["logo_uri"] == TEST_ICON
    assert data["icons"] == [TEST_ICON_DESCRIPTOR]
    assert data["kdcube_capabilities"][0]["grant"] == "records:read"
    assert data["kdcube_tools"][0]["grants"] == ["records:read"]


def test_well_known_protected_resource_serves_named_service_catalog():
    app = FastAPI()
    app.state.oauth_delegated_config = {
        "enabled": True,
        "issuer": ISSUER,
        "capabilities": [
            {"grant": "named_services:use", "label": "Use named services"},
            {"grant": "memories:read", "label": "Read memories"},
        ],
        "resources": [
            {
                "resource": "https://connector.example.test/api/integrations/bundles/demo/prod/kdcube-services@1-0/public/mcp/named_services",
                "tools": {
                    "named_services_schema": {
                        "label": "Named service schema",
                        "grants": ["named_services:use"],
                    },
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
                            },
                        },
                    },
                },
            },
        ],
    }
    mount_test_oauth_adapter(app)

    resource = "https://connector.example.test/api/integrations/bundles/demo/prod/kdcube-services@1-0/public/mcp/named_services"
    resp = TestClient(app).get("/.well-known/oauth-protected-resource", params={"resource": resource})
    assert resp.status_code == 200
    data = resp.json()

    assert data["scopes_supported"] == ["named_services:use", "memories:read"]
    assert data["kdcube_tools"][0]["name"] == "named_services_schema"
    assert data["kdcube_named_services"]["namespaces"]["mem"]["authority_id"] == "delegated_client"
    assert (
        data["kdcube_named_services"]["namespaces"]["mem"]["tools"]["schema"]["grants"]
        == ["memories:read"]
    )
