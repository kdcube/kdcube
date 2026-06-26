# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
DCR redirect-URI allowlist.

Dynamic client registration is open (it must run before the user authenticates),
so an attacker could register a client pointing at their own server and use it in
a phishing/CSRF attack. Restricting the redirect_uris a DCR client may register to
a trusted set — claude.ai's MCP callback + loopback (any port) — means a stolen
auth code can only ever be delivered to claude.ai or the victim's own machine,
never an attacker-controlled URL.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.ingress.oauth_mcp import mount_oauth_mcp
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.clients import dcr_redirect_allowed
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.store import GrantStore
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.tests.test_clients_and_store import FakeRedis
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.tests.helpers import enable_oauth_mcp

CLAUDE_CB = "https://claude.ai/api/mcp/auth_callback"


# ----------------------------- pure predicate -----------------------------

def test_allows_claude_callback():
    assert dcr_redirect_allowed(CLAUDE_CB)


def test_allows_loopback_any_port():
    assert dcr_redirect_allowed("http://127.0.0.1:54321/callback")
    assert dcr_redirect_allowed("http://localhost:8765/callback")


def test_rejects_foreign_host():
    assert not dcr_redirect_allowed("https://evil.example/cb")
    assert not dcr_redirect_allowed("https://claude.ai.evil.example/api/mcp/auth_callback")


# ----------------------------- /oauth/register -----------------------------

@pytest.fixture
def client():
    app = FastAPI()
    enable_oauth_mcp(app)
    mount_oauth_mcp(app)
    app.state.oauth_grant_store = GrantStore(FakeRedis(), tenant="home", project="demo")
    return TestClient(app)


def test_register_accepts_allowed_redirect(client):
    r = client.post("/oauth/register", json={"redirect_uris": [CLAUDE_CB]})
    assert r.status_code == 201


def test_register_accepts_loopback_any_port(client):
    r = client.post("/oauth/register", json={"redirect_uris": ["http://127.0.0.1:5000/callback"]})
    assert r.status_code == 201


def test_register_rejects_foreign_redirect(client):
    r = client.post("/oauth/register", json={"redirect_uris": ["https://evil.example/cb"]})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_redirect_uri"


def test_register_rejects_if_any_redirect_is_foreign(client):
    r = client.post(
        "/oauth/register",
        json={"redirect_uris": [CLAUDE_CB, "https://evil.example/cb"]},
    )
    assert r.status_code == 400
