# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Tests for the OAuth2 Authorization Server + MCP resource discovery metadata.

These cover the RFC 9728 (protected-resource) -> RFC 8414 (authorization-server)
handshake that Claude Code's MCP client uses to discover how to authenticate
against KDCube's /mcp endpoint. All pure / deterministic; no Redis or DB needed.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.ingress.oauth_mcp import (
    mount_oauth_mcp,
    authorization_server_metadata,
    protected_resource_metadata,
)
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.tests.helpers import enable_oauth_mcp

ISSUER = "https://yey.boats"


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
    assert md["scopes_supported"] == ["conversations:read"]


def test_authorization_server_metadata_omits_jwks_uri():
    # Tokens are opaque (kst1) -> no asymmetric signing, so jwks_uri must be absent.
    md = authorization_server_metadata(ISSUER)
    assert "jwks_uri" not in md


def test_protected_resource_metadata_points_at_as():
    md = protected_resource_metadata(ISSUER)

    assert md["resource"] == f"{ISSUER}/mcp"
    assert md["authorization_servers"] == [ISSUER]
    assert md["scopes_supported"] == ["conversations:read"]


# ----------------------------- served endpoints -----------------------------

@pytest.fixture
def client():
    app = FastAPI()
    enable_oauth_mcp(app, issuer=ISSUER)
    mount_oauth_mcp(app)
    return TestClient(app)


def test_well_known_authorization_server_served(client):
    resp = client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200
    assert resp.json() == authorization_server_metadata(ISSUER)


def test_well_known_protected_resource_served(client):
    resp = client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    assert resp.json() == protected_resource_metadata(ISSUER)


def test_mcp_unauthenticated_returns_401_with_resource_metadata(client):
    # RFC 9728 §5.1: an unauthenticated MCP request advertises the
    # protected-resource metadata URL in WWW-Authenticate so the client can
    # discover the authorization server.
    resp = client.get("/mcp")
    assert resp.status_code == 401
    www = resp.headers.get("WWW-Authenticate", "")
    assert www.startswith("Bearer ")
    assert f'resource_metadata="{ISSUER}/.well-known/oauth-protected-resource"' in www
