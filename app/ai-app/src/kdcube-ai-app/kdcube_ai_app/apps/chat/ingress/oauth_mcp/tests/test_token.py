# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Tests for POST /oauth/token: authorization_code exchange (with PKCE) and
refresh_token rotation. A fake access-token minter is injected so these stay
unit tests independent of the bundle-session authority.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.ingress.oauth_mcp import mount_oauth_mcp
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.authority import (
    OAUTH_MCP_CREDENTIAL_KIND,
)
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.store import GrantStore
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry import (
    OAUTH_MCP_AUTHENTICATOR_ID,
    OAUTH_MCP_AUTHORITY_ID,
)
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.pkce import make_s256_challenge
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.tests.test_clients_and_store import FakeRedis
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.tests.helpers import enable_oauth_mcp

VERIFIER = "code-verifier-" + "z" * 60
CHALLENGE = make_s256_challenge(VERIFIER)


async def _fake_minter(sub, scopes):
    return {"access_token": f"kst1.mock.{sub}", "expires_in": 3600}


@pytest.fixture
def ctx():
    app = FastAPI()
    enable_oauth_mcp(app)
    mount_oauth_mcp(app)
    store = GrantStore(FakeRedis(), tenant="home", project="demo")
    app.state.oauth_grant_store = store
    app.state.oauth_mint_access_token = _fake_minter
    return TestClient(app), store


async def _seed_code(store, *, redirect_uri="http://127.0.0.1:9000/callback", client_id="claude"):
    return await store.create_auth_code(
        client_id=client_id, redirect_uri=redirect_uri, code_challenge=CHALLENGE,
        sub="google:admin@example.test", scopes=["conversations:read"], tools=["conversations_export"],
    )


@pytest.mark.asyncio
async def test_access_token_grant_binds_consented_tools(ctx):
    client, store = ctx
    # Reviewer's scenario: admin unchecked the tool -> code carries no tools.
    code = await store.create_auth_code(
        client_id="claude", redirect_uri="http://127.0.0.1:9000/callback",
        code_challenge=CHALLENGE, sub="google:admin@example.test",
        scopes=["conversations:read"], tools=[],
    )
    body = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "http://127.0.0.1:9000/callback", "client_id": "claude",
        "code_verifier": VERIFIER,
    }).json()
    # The issued access token's grant must reflect the (empty) consent, and the
    # refresh token must carry it so it survives rotation.
    assert await store.get_access_grant(body["access_token"]) == []
    grant_record = await store.get_access_grant_record(body["access_token"])
    assert grant_record["authority"]["schema"] == "kdcube.credential.v1"
    assert grant_record["authority"]["credential_kind"] == OAUTH_MCP_CREDENTIAL_KIND
    assert grant_record["authority"]["issuer_authority_id"] == OAUTH_MCP_AUTHORITY_ID
    assert grant_record["authority"]["issuer_authenticator_id"] == OAUTH_MCP_AUTHENTICATOR_ID
    assert grant_record["authority"]["audience"] == "kdcube:mcp"
    refresh_record = await store.validate_refresh_token(body["refresh_token"])
    assert refresh_record["tools"] == []
    assert refresh_record["authority"]["issuer_authority_id"] == OAUTH_MCP_AUTHORITY_ID


@pytest.mark.asyncio
async def test_authorization_code_exchange_succeeds(ctx):
    client, store = ctx
    code = await _seed_code(store)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "http://127.0.0.1:9000/callback",
        "client_id": "claude",
        "code_verifier": VERIFIER,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] == "kst1.mock.google:admin@example.test"
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert body["scope"] == "conversations:read"
    assert body["refresh_token"]
    refresh_record = await store.validate_refresh_token(body["refresh_token"])
    assert refresh_record["authority"]["subject"] == "integration:claude:google:admin@example.test"
    assert r.headers.get("Cache-Control") == "no-store"


@pytest.mark.asyncio
async def test_exchange_fails_on_bad_verifier(ctx):
    client, store = ctx
    code = await _seed_code(store)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "http://127.0.0.1:9000/callback", "client_id": "claude",
        "code_verifier": "the-wrong-verifier-" + "q" * 50,
    })
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_exchange_fails_on_redirect_mismatch(ctx):
    client, store = ctx
    code = await _seed_code(store)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "http://127.0.0.1:1111/callback", "client_id": "claude",
        "code_verifier": VERIFIER,
    })
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_code_cannot_be_replayed(ctx):
    client, store = ctx
    code = await _seed_code(store)
    common = {
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "http://127.0.0.1:9000/callback", "client_id": "claude",
        "code_verifier": VERIFIER,
    }
    assert client.post("/oauth/token", data=common).status_code == 200
    # second use of the same code must fail.
    assert client.post("/oauth/token", data=common).status_code == 400


def test_unknown_code_is_invalid_grant(ctx):
    client, _ = ctx
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": "nope",
        "redirect_uri": "http://127.0.0.1:9000/callback", "client_id": "claude",
        "code_verifier": VERIFIER,
    })
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_unsupported_grant_type(ctx):
    client, _ = ctx
    r = client.post("/oauth/token", data={"grant_type": "password", "username": "x"})
    assert r.status_code == 400
    assert r.json()["error"] == "unsupported_grant_type"


@pytest.mark.asyncio
async def test_refresh_token_rotates_and_issues_new_access(ctx):
    client, store = ctx
    # First, get a refresh token via the code path.
    code = await _seed_code(store)
    first = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "http://127.0.0.1:9000/callback", "client_id": "claude",
        "code_verifier": VERIFIER,
    }).json()
    rt = first["refresh_token"]

    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": rt, "client_id": "claude",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] == "kst1.mock.google:admin@example.test"
    assert body["refresh_token"] and body["refresh_token"] != rt   # rotated

    # Old refresh token no longer works.
    again = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": rt, "client_id": "claude",
    })
    assert again.status_code == 400


def test_unknown_refresh_token_is_invalid_grant(ctx):
    client, _ = ctx
    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": "bogus", "client_id": "claude",
    })
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"
