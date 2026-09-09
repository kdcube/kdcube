# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Tests for Dynamic Client Registration (RFC 7591). When the Claude.ai "Add custom
connector" dialog is given no OAuth Client ID, the client self-registers here and
then runs the normal authorization_code + PKCE flow.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from connection_hub.delegated_credentials.oauth import authorization_server_metadata
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.helpers import mount_test_oauth_adapter
from connection_hub.delegated_credentials.oauth.store import GrantStore
from connection_hub.delegated_credentials.oauth.pkce import make_s256_challenge
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_clients_and_store import FakeRedis
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.helpers import enable_delegated_client

ISSUER = "https://connector.example.test"
CB = "https://claude.ai/api/mcp/auth_callback"


async def _authenticate(token):
    if token == "admin-tok":
        return {"sub": "google:admin@example.test", "roles": ["kdcube:role:super-admin"]}
    return None


@pytest.fixture
def client():
    app = FastAPI()
    enable_delegated_client(app, issuer=ISSUER)
    mount_test_oauth_adapter(app)
    app.state.oauth_authenticate = _authenticate
    app.state.oauth_grant_store = GrantStore(FakeRedis(), tenant="home", project="demo")
    return TestClient(app)


def test_metadata_advertises_registration_endpoint():
    md = authorization_server_metadata(ISSUER)
    assert md["registration_endpoint"] == f"{ISSUER}/oauth/register"


def test_register_returns_public_client(client):
    r = client.post("/oauth/register", json={
        "client_name": "KDCube test connector",
        "redirect_uris": [CB],
        "token_endpoint_auth_method": "none",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["client_id"]
    assert "client_secret" not in body          # public client
    assert body["token_endpoint_auth_method"] == "none"
    assert body["redirect_uris"] == [CB]
    assert body["application_type"] == "native"
    assert body["logo_uri"] == f"{ISSUER}/img/favicon.svg"
    assert body["client_uri"] == ISSUER


def test_register_retains_safe_client_metadata_for_the_card(client):
    response = client.post(
        "/oauth/register",
        json={
            "client_name": "Connection Hub CLI · worker_stream · codex:session-1",
            "redirect_uris": [CB],
            "token_endpoint_auth_method": "none",
            "kdcube_agent_id": "codex:session-1",
            "kdcube_machine_id": "machine-1",
        },
    )

    assert response.status_code == 201
    registered = response.json()
    assert registered["kdcube_agent_id"] == "codex:session-1"
    record = asyncio.run(
        client.app.state.oauth_grant_store.get_client_record(registered["client_id"])
    )
    assert record["metadata"]["client_metadata"]["kdcube_machine_id"] == "machine-1"


@pytest.mark.parametrize("field", ["client_secret", "kdcube_access_token"])
def test_register_rejects_secret_bearing_metadata(client, field):
    response = client.post(
        "/oauth/register",
        json={"redirect_uris": [CB], field: "not-for-storage"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"


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
        "scope": "records:read",
        "state": "s1",
        "code_challenge": make_s256_challenge("v" * 60),
        "code_challenge_method": "S256",
    }, headers={"Authorization": "Bearer admin-tok"})
    assert r.status_code == 200
    assert "records_export" in r.text


def test_registered_client_redirect_must_match(client):
    reg = client.post("/oauth/register", json={"redirect_uris": [CB]}).json()
    cid = reg["client_id"]
    # A redirect_uri the client did not register must be rejected (400, no bounce).
    r = client.get("/oauth/authorize", params={
        "client_id": cid,
        "redirect_uri": "https://evil.example/cb",
        "response_type": "code",
        "scope": "records:read",
        "code_challenge": make_s256_challenge("v" * 60),
        "code_challenge_method": "S256",
    }, headers={"Authorization": "Bearer admin-tok"}, follow_redirects=False)
    assert r.status_code == 400


def test_register_rejects_unknown_application_type(client):
    response = client.post(
        "/oauth/register",
        json={"redirect_uris": [CB], "application_type": "service"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"


def test_register_rejects_loopback_redirect_for_web_client(client):
    response = client.post(
        "/oauth/register",
        json={
            "redirect_uris": ["http://127.0.0.1/callback"],
            "application_type": "web",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"


def test_register_accepts_https_redirect_for_web_client(client):
    response = client.post(
        "/oauth/register",
        json={"redirect_uris": [CB], "application_type": "web"},
    )

    assert response.status_code == 201
    assert response.json()["application_type"] == "web"


def test_register_rejects_confidential_client_auth(client):
    response = client.post(
        "/oauth/register",
        json={
            "redirect_uris": [CB],
            "token_endpoint_auth_method": "client_secret_basic",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"


def test_register_is_absent_when_dcr_is_disabled():
    app = FastAPI()
    enable_delegated_client(app, issuer=ISSUER)
    app.state.oauth_delegated_config["dynamic_client_registration"] = {"enabled": False}
    mount_test_oauth_adapter(app)

    test_client = TestClient(app)
    response = test_client.post("/oauth/register", json={"redirect_uris": [CB]})
    discovery = test_client.get("/.well-known/oauth-authorization-server")

    assert response.status_code == 404
    assert "registration_endpoint" not in discovery.json()
