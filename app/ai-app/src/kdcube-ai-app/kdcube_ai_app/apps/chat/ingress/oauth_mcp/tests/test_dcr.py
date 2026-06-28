# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Tests for Dynamic Client Registration (RFC 7591). When the Claude.ai "Add custom
connector" dialog is given no OAuth Client ID, the client self-registers here and
then runs the normal authorization_code + PKCE flow.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.ingress.oauth_mcp import mount_oauth_mcp, authorization_server_metadata
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp.store import GrantStore
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp.pkce import make_s256_challenge
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.tests.test_clients_and_store import FakeRedis
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.tests.helpers import enable_oauth_mcp

ISSUER = "https://yey.boats"
CB = "https://claude.ai/api/mcp/auth_callback"


async def _authenticate(token):
    if token == "admin-tok":
        return {"sub": "google:admin@example.test", "roles": ["kdcube:role:super-admin"]}
    return None


@pytest.fixture
def client():
    app = FastAPI()
    enable_oauth_mcp(app, issuer=ISSUER)
    mount_oauth_mcp(app)
    app.state.oauth_authenticate = _authenticate
    app.state.oauth_grant_store = GrantStore(FakeRedis(), tenant="home", project="demo")
    return TestClient(app)


def test_metadata_advertises_registration_endpoint():
    md = authorization_server_metadata(ISSUER)
    assert md["registration_endpoint"] == f"{ISSUER}/oauth/register"


def test_register_returns_public_client(client):
    r = client.post("/oauth/register", json={
        "client_name": "yey.boats KDCube admin",
        "redirect_uris": [CB],
        "token_endpoint_auth_method": "none",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["client_id"]
    assert "client_secret" not in body          # public client
    assert body["token_endpoint_auth_method"] == "none"
    assert body["redirect_uris"] == [CB]


def test_register_requires_redirect_uris(client):
    r = client.post("/oauth/register", json={"client_name": "x"})
    assert r.status_code == 400


def test_registered_client_can_authorize(client):
    reg = client.post("/oauth/register", json={"redirect_uris": [CB]}).json()
    cid = reg["client_id"]
    r = client.get("/oauth/authorize", params={
        "client_id": cid,
        "redirect_uri": CB,
        "response_type": "code",
        "scope": "conversations:read",
        "state": "s1",
        "code_challenge": make_s256_challenge("v" * 60),
        "code_challenge_method": "S256",
    }, headers={"Authorization": "Bearer admin-tok"})
    assert r.status_code == 200
    assert "conversations_export" in r.text


def test_registered_client_redirect_must_match(client):
    reg = client.post("/oauth/register", json={"redirect_uris": [CB]}).json()
    cid = reg["client_id"]
    # A redirect_uri the client did not register must be rejected (400, no bounce).
    r = client.get("/oauth/authorize", params={
        "client_id": cid,
        "redirect_uri": "https://evil.example/cb",
        "response_type": "code",
        "scope": "conversations:read",
        "code_challenge": make_s256_challenge("v" * 60),
        "code_challenge_method": "S256",
    }, headers={"Authorization": "Bearer admin-tok"}, follow_redirects=False)
    assert r.status_code == 400
