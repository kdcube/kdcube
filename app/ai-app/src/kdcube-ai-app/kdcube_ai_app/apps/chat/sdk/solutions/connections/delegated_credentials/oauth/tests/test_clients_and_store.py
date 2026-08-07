# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Tests for the public-client registry, PKCE verification, and the Redis-backed
authorization-code / refresh-token store.

The store is exercised against a tiny in-memory fake Redis (same approach as
auth/tests/test_bundle_sessions.py) so these stay pure unit tests.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.clients import (
    CLIENT_REGISTRATION_METADATA_DOCUMENT,
    PublicClient,
    get_client,
    redirect_uri_allowed,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.pkce import make_s256_challenge, verify_s256
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.http.deps import (
    get_grant_store,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.store import (
    CLIENT_TTL_SECONDS,
    GrantStore,
    GrantStoreUnavailable,
)


# ------------------------------- fake redis -------------------------------

class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = str(value)
        if ex is not None:
            self.ttls[key] = int(ex)
        return True

    async def setex(self, key, ttl, value):
        self.values[key] = str(value)
        self.ttls[key] = int(ttl)
        return True

    async def expire(self, key, ttl):
        if key not in self.values:
            return False
        self.ttls[key] = int(ttl)
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, *keys):
        removed = 0
        for k in keys:
            if k in self.values:
                removed += 1
                self.values.pop(k, None)
                self.ttls.pop(k, None)
        return removed

    async def eval(self, script, numkeys, *values):
        keys = list(values[:numkeys])
        args = list(values[numkeys:])
        if numkeys == 2 and "EXISTS" in script:
            old_key, new_key = keys
            current = self.values.get(old_key)
            if current is None:
                return 0
            if current != args[0]:
                return -1
            if new_key in self.values:
                return -2
            self.values.pop(old_key, None)
            self.ttls.pop(old_key, None)
            self.values[new_key] = str(args[1])
            self.ttls[new_key] = int(args[2])
            return 1
        if numkeys == 1 and "EXPIRE" in script:
            key = keys[0]
            current = self.values.get(key)
            if current is None:
                return None
            self.ttls[key] = int(args[0])
            return current
        if numkeys == 1:
            key = keys[0]
            current = self.values.pop(key, None)
            self.ttls.pop(key, None)
            return current
        raise AssertionError("unsupported Lua script in FakeRedis")


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


def test_metadata_document_redirect_requires_exact_port():
    client = PublicClient(
        client_id="https://client.example.test/oauth/client.json",
        redirect_uris=("http://127.0.0.1:41001/callback",),
        registration_kind=CLIENT_REGISTRATION_METADATA_DOCUMENT,
    )

    assert redirect_uri_allowed(client, "http://127.0.0.1:41001/callback")
    assert not redirect_uri_allowed(client, "http://127.0.0.1:41002/callback")


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
        scopes=["records:read"],
        operations=["records_export"],
        client_metadata={"client_name": "Verified client"},
    )
    payload = await store.consume_auth_code(code)
    assert payload["client_id"] == "claude"
    assert payload["sub"] == "google:admin@example.test"
    assert payload["scopes"] == ["records:read"]
    assert payload["operations"] == ["records_export"]
    assert payload["redirect_uri"] == "http://localhost:9999/callback"
    assert payload["client_metadata"]["client_name"] == "Verified client"


@pytest.mark.asyncio
async def test_auth_code_is_single_use(store):
    code = await store.create_auth_code(
        client_id="claude", redirect_uri="http://localhost:9999/callback",
        code_challenge=make_s256_challenge("v" * 50), sub="s", scopes=["records:read"], operations=[],
    )
    assert await store.consume_auth_code(code) is not None
    # Second consume must fail — replay protection.
    assert await store.consume_auth_code(code) is None


@pytest.mark.asyncio
async def test_auth_code_allows_only_one_concurrent_consumer(store):
    code = await store.create_auth_code(
        client_id="claude",
        redirect_uri="http://localhost:9999/callback",
        code_challenge=make_s256_challenge("v" * 50),
        sub="s",
        scopes=["records:read"],
        operations=[],
    )

    results = await asyncio.gather(
        store.consume_auth_code(code),
        store.consume_auth_code(code),
    )

    assert sum(result is not None for result in results) == 1


@pytest.mark.asyncio
async def test_unknown_auth_code_returns_none(store):
    assert await store.consume_auth_code("nope-not-a-real-code") is None


# ------------------------------- refresh-token store -------------------------------

@pytest.mark.asyncio
async def test_refresh_token_validates_then_rotates(store):
    rt = await store.create_refresh_token(
        client_id="claude",
        sub="google:admin@example.test",
        scopes=["records:read"],
        client_metadata={"client_name": "Verified client"},
    )
    rec = await store.validate_refresh_token(rt)
    assert rec["sub"] == "google:admin@example.test"
    assert rec["scopes"] == ["records:read"]

    new_rt = await store.rotate_refresh_token(rt)
    assert new_rt and new_rt != rt
    # Old token no longer valid after rotation (reuse detection boundary).
    assert await store.validate_refresh_token(rt) is None
    assert await store.validate_refresh_token(new_rt) is not None
    rotated = await store.validate_refresh_token(new_rt)
    assert rotated["client_metadata"]["client_name"] == "Verified client"


@pytest.mark.asyncio
async def test_refresh_token_allows_only_one_concurrent_rotation(store):
    rt = await store.create_refresh_token(
        client_id="claude",
        sub="google:admin@example.test",
        scopes=["records:read"],
    )
    state = await store.get_refresh_token_state(rt)
    assert state is not None

    results = await asyncio.gather(
        store.rotate_refresh_token(rt, state=state),
        store.rotate_refresh_token(rt, state=state),
    )
    replacements = [token for token in results if token]

    assert len(replacements) == 1
    assert await store.validate_refresh_token(rt) is None
    assert await store.validate_refresh_token(replacements[0]) is not None


@pytest.mark.asyncio
async def test_consent_csrf_allows_only_one_concurrent_consumer(store):
    csrf = await store.create_csrf_token("user-1", context={"client_id": "claude"})

    results = await asyncio.gather(
        store.consume_csrf_token_context(csrf, "user-1"),
        store.consume_csrf_token_context(csrf, "user-1"),
    )

    assert sum(result[0] for result in results) == 1
    assert sorted(result[1] for result in results) == ["not_found", "ok"]


@pytest.mark.asyncio
async def test_store_wraps_redis_failures_with_operation_context():
    class FailingRedis(FakeRedis):
        async def eval(self, script, numkeys, *values):
            raise ConnectionError("redis unavailable")

    failing_store = GrantStore(FailingRedis(), tenant="home", project="demo")

    with pytest.raises(GrantStoreUnavailable) as raised:
        await failing_store.consume_auth_code("code")

    assert raised.value.operation == "authorization_code.consume"


@pytest.mark.asyncio
async def test_refresh_token_preserves_named_service_catalog(store):
    named_services = {
        "namespaces": {
            "mem": {
                "authority_id": "delegated_client",
                "tools": {
                    "search": {
                        "operation": "object.search",
                        "grants": ["memories:read"],
                    },
                },
            },
        },
    }
    rt = await store.create_refresh_token(
        client_id="claude",
        sub="google:admin@example.test",
        scopes=["named_services:use", "memories:read"],
        named_services=named_services,
    )

    rec = await store.validate_refresh_token(rt)
    assert rec["named_services"] == named_services

    new_rt = await store.rotate_refresh_token(rt)
    rotated = await store.validate_refresh_token(new_rt)
    assert rotated["named_services"] == named_services


@pytest.mark.asyncio
async def test_access_grant_record_preserves_named_service_catalog(store):
    named_services = {
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
    }

    await store.bind_access_grant(
        "access-token",
        ["named_services_schema"],
        60,
        named_services=named_services,
    )
    record = await store.get_access_grant_record("access-token")
    assert record["named_services"] == named_services


# --------------------- DCR client registration TTL ---------------------


@pytest.mark.asyncio
async def test_client_registration_carries_sliding_ttl():
    """A dcr-* registration expires unless used (every retried connect mints a
    fresh client), and any read re-arms the full TTL so a connector in use
    never expires. Regression: registrations used to persist with no TTL and
    accumulated forever."""
    r = FakeRedis()
    store = GrantStore(r, tenant="home", project="demo")
    rec = await store.register_client(
        redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
        metadata={"client_name": "Claude"},
    )
    key = next(k for k in r.values if rec["client_id"] in k)
    assert r.ttls.get(key) == CLIENT_TTL_SECONDS

    # Simulate the record nearing expiry; a read must re-arm the full TTL.
    r.ttls[key] = 60
    fetched = await store.get_client_record(rec["client_id"])
    assert fetched is not None and fetched["client_id"] == rec["client_id"]
    assert r.ttls.get(key) == CLIENT_TTL_SECONDS


def test_grant_store_reuses_proc_owned_async_redis_client():
    app = FastAPI()
    redis = FakeRedis()
    app.state.redis_async = redis
    app.state.oauth_delegated_config = {
        "enabled": True,
        "tenant": "tenant-a",
        "project": "project-a",
    }
    request = Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/oauth/authorize",
        "raw_path": b"/oauth/authorize",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("runtime.example.test", 443),
        "app": app,
    })

    store = get_grant_store(request)

    assert store.redis is redis
    assert store._tenant == "tenant-a"
    assert store._project == "project-a"


def test_grant_store_factory_failure_is_normalized(monkeypatch):
    from kdcube_ai_app.infra.redis import client as redis_client

    app = FastAPI()
    app.state.oauth_delegated_config = {
        "enabled": True,
        "tenant": "tenant-a",
        "project": "project-a",
    }
    request = Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/oauth/authorize",
        "raw_path": b"/oauth/authorize",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("runtime.example.test", 443),
        "app": app,
    })
    monkeypatch.setattr(
        redis_client,
        "get_async_redis_client",
        lambda _url: (_ for _ in ()).throw(RuntimeError("state backend unavailable")),
    )

    with pytest.raises(GrantStoreUnavailable) as raised:
        get_grant_store(request)

    assert raised.value.operation == "initialize"


@pytest.mark.asyncio
async def test_real_redis_oauth_transitions_are_atomic() -> None:
    redis_url = str(os.getenv("KDCUBE_TEST_REDIS_URL") or "").strip()
    if not redis_url:
        pytest.skip("set KDCUBE_TEST_REDIS_URL to run the disposable-Redis regression")

    redis_asyncio = pytest.importorskip("redis.asyncio")
    redis = redis_asyncio.from_url(redis_url, decode_responses=True)
    tenant = f"test-oauth-{uuid.uuid4().hex}"
    project = "atomic-transitions"
    real_store = GrantStore(redis, tenant=tenant, project=project)
    cleanup_keys: list[str] = []

    try:
        code = await real_store.create_auth_code(
            client_id="claude",
            redirect_uri="http://localhost:9999/callback",
            code_challenge=make_s256_challenge("v" * 50),
            sub="user-1",
            scopes=["records:read"],
        )
        cleanup_keys.append(real_store._key("code", code))
        code_results = await asyncio.gather(
            real_store.consume_auth_code(code),
            real_store.consume_auth_code(code),
        )
        assert sum(result is not None for result in code_results) == 1

        csrf = await real_store.create_csrf_token("user-1")
        cleanup_keys.append(real_store._key("csrf", csrf))
        csrf_results = await asyncio.gather(
            real_store.consume_csrf_token_context(csrf, "user-1"),
            real_store.consume_csrf_token_context(csrf, "user-1"),
        )
        assert sum(result[0] for result in csrf_results) == 1

        refresh = await real_store.create_refresh_token(
            client_id="claude",
            sub="user-1",
            scopes=["records:read"],
        )
        cleanup_keys.append(real_store._key("refresh", refresh))
        state = await real_store.get_refresh_token_state(refresh)
        assert state is not None
        rotations = await asyncio.gather(
            real_store.rotate_refresh_token(refresh, state=state),
            real_store.rotate_refresh_token(refresh, state=state),
        )
        replacements = [token for token in rotations if token]
        assert len(replacements) == 1
        cleanup_keys.extend(
            real_store._key("refresh", token) for token in replacements
        )
    finally:
        if cleanup_keys:
            await redis.delete(*cleanup_keys)
        closer = getattr(redis, "aclose", None) or getattr(redis, "close")
        await closer()


def test_metadata_document_portless_loopback_accepts_any_port():
    """A native client cannot publish the ephemeral port it will bind, so a
    portless loopback URI in the document matches any port (RFC 8252 7.3).
    Claude Code publishes exactly this shape."""
    client = PublicClient(
        client_id="https://claude.ai/oauth/claude-code-client-metadata",
        redirect_uris=("http://localhost/callback", "http://127.0.0.1/callback"),
        registration_kind=CLIENT_REGISTRATION_METADATA_DOCUMENT,
    )

    assert redirect_uri_allowed(client, "http://localhost:3118/callback")
    assert redirect_uri_allowed(client, "http://127.0.0.1:52341/callback")
    # Host, scheme and path are still matched exactly.
    assert not redirect_uri_allowed(client, "http://localhost:3118/other")
    assert not redirect_uri_allowed(client, "https://localhost:3118/callback")
    assert not redirect_uri_allowed(client, "http://client.example.test:3118/callback")
