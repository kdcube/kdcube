# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Tests for the public-client registry, PKCE verification, and the Redis-backed
authorization-code / refresh-token store.

The store is exercised against a tiny in-memory fake Redis (same approach as
auth/tests/test_bundle_sessions.py) so these stay pure unit tests.
"""
from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp.clients import (
    get_client,
    redirect_uri_allowed,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp.pkce import make_s256_challenge, verify_s256
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp.store import GrantStore


# ------------------------------- fake redis -------------------------------

class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = str(value)
        return True

    async def setex(self, key, ttl, value):
        self.values[key] = str(value)
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, *keys):
        removed = 0
        for k in keys:
            if k in self.values:
                removed += 1
                self.values.pop(k, None)
        return removed


# ------------------------------- clients -------------------------------

def test_known_client_resolves():
    client = get_client("claude")
    assert client is not None
    assert client.token_endpoint_auth_method == "none"


def test_unknown_client_is_none():
    assert get_client("not-registered") is None


def test_exact_redirect_uri_allowed():
    client = get_client("claude")
    assert redirect_uri_allowed(client, "https://claude.ai/api/mcp/auth_callback")


def test_loopback_redirect_allowed_on_any_port():
    # RFC 8252: a native client's loopback redirect may use a dynamic port.
    client = get_client("claude")
    assert redirect_uri_allowed(client, "http://127.0.0.1:54321/callback")
    assert redirect_uri_allowed(client, "http://localhost:8765/callback")


def test_foreign_redirect_uri_rejected():
    client = get_client("claude")
    assert not redirect_uri_allowed(client, "https://evil.example/callback")
    # Non-loopback host must match exactly, port games not allowed.
    assert not redirect_uri_allowed(client, "https://claude.ai:9999/api/mcp/auth_callback")


# ------------------------------- PKCE -------------------------------

def test_pkce_s256_roundtrip():
    verifier = "abc123~the-quick-brown-fox_jumps.over-LAZY-dog0123456789"
    challenge = make_s256_challenge(verifier)
    assert verify_s256(verifier, challenge)


def test_pkce_wrong_verifier_fails():
    challenge = make_s256_challenge("the-real-verifier-value-aaaaaaaaaaaaaaaaaaaa")
    assert not verify_s256("a-different-verifier-bbbbbbbbbbbbbbbbbbbbbbbb", challenge)


def test_pkce_challenge_has_no_padding():
    # base64url without '=' padding per RFC 7636.
    challenge = make_s256_challenge("verifier-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    assert "=" not in challenge and "+" not in challenge and "/" not in challenge


# ------------------------------- auth-code store -------------------------------

@pytest.fixture
def store():
    return GrantStore(FakeRedis(), tenant="home", project="demo")


@pytest.mark.asyncio
async def test_auth_code_consume_returns_bound_payload(store):
    code = await store.create_auth_code(
        client_id="claude",
        redirect_uri="http://localhost:9999/callback",
        code_challenge=make_s256_challenge("v" * 50),
        sub="google:admin@example.test",
        scopes=["conversations:read"],
        tools=["conversations_export"],
    )
    payload = await store.consume_auth_code(code)
    assert payload["client_id"] == "claude"
    assert payload["sub"] == "google:admin@example.test"
    assert payload["scopes"] == ["conversations:read"]
    assert payload["tools"] == ["conversations_export"]
    assert payload["redirect_uri"] == "http://localhost:9999/callback"


@pytest.mark.asyncio
async def test_auth_code_is_single_use(store):
    code = await store.create_auth_code(
        client_id="claude", redirect_uri="http://localhost:9999/callback",
        code_challenge=make_s256_challenge("v" * 50), sub="s", scopes=["conversations:read"], tools=[],
    )
    assert await store.consume_auth_code(code) is not None
    # Second consume must fail — replay protection.
    assert await store.consume_auth_code(code) is None


@pytest.mark.asyncio
async def test_unknown_auth_code_returns_none(store):
    assert await store.consume_auth_code("nope-not-a-real-code") is None


# ------------------------------- refresh-token store -------------------------------

@pytest.mark.asyncio
async def test_refresh_token_validates_then_rotates(store):
    rt = await store.create_refresh_token(
        client_id="claude", sub="google:admin@example.test", scopes=["conversations:read"],
    )
    rec = await store.validate_refresh_token(rt)
    assert rec["sub"] == "google:admin@example.test"
    assert rec["scopes"] == ["conversations:read"]

    new_rt = await store.rotate_refresh_token(rt)
    assert new_rt and new_rt != rt
    # Old token no longer valid after rotation (reuse detection boundary).
    assert await store.validate_refresh_token(rt) is None
    assert await store.validate_refresh_token(new_rt) is not None
