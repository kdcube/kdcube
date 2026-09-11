# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Tests for POST /oauth/token: authorization_code exchange (with PKCE) and
refresh_token rotation. A fake access-token minter is injected so these stay
unit tests independent of the platform-session authority.
"""
from __future__ import annotations

import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.helpers import (
    bind_delegated_card_persistence,
    mount_test_oauth_adapter,
)
from connection_hub.delegated_credentials.oauth.authority import (
    DELEGATED_CLIENT_CREDENTIAL_KIND,
)
from connection_hub.delegated_credentials.oauth.store import GrantStore
from connection_hub.authority_registry import (
    DELEGATED_CLIENT_AUTHENTICATOR_ID,
    DELEGATED_CLIENT_AUTHORITY_ID,
)
from connection_hub.delegated_credentials.oauth.pkce import make_s256_challenge
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_clients_and_store import FakeRedis
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.helpers import enable_delegated_client
from connection_hub.delegated_credentials.cache_io import (
    encode_cache_value,
)
from connection_hub.delegated_credentials.cards.cache import (
    DelegatedCardRuntimeCache,
)
from connection_hub.delegated_credentials.cards.model import (
    CardAuthority,
    NamedServiceSelection,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.cards.persistence import (
    DurableCardPersistence,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.cards.service import (
    CardCommitFailed,
)

VERIFIER = "code-verifier-" + "z" * 60
CHALLENGE = make_s256_challenge(VERIFIER)


async def _fake_minter(sub, scopes):
    return {"access_token": f"kst1.mock.{sub}", "expires_in": 3600}



def _card_key(access_id: str) -> str:
    return DelegatedCardRuntimeCache(None, tenant="home", project="demo").card_key(access_id)


def _seed_live_card(
    store,
    refresh_record,
    *,
    operations=("records_export",),
    resource_grants=None,
    resource_operations=None,
) -> str:
    """Establish the live card projection the refresh path reads.

    The precondition here is a live card, not a persistence layer, so the test
    states it directly.
    """
    credential = refresh_record.get("credential") or {}
    access_id = str(refresh_record["registry_access_id"])
    resource = str(refresh_record.get("resource") or "") or "*"
    authority = CardAuthority(
        access_id=access_id,
        client_id=str(refresh_record.get("client_id") or ""),
        grantor_subject=str(refresh_record.get("sub") or ""),
        delegate_subject=str(credential.get("subject") or ""),
        source="oauth",
        card_revision=1,
        operations=tuple(operations),
        resource_grants=(
            resource_grants if resource_grants is not None else {resource: ("records:read",)}
        ),
        resource_operations=resource_operations or {},
        named_service_operations=NamedServiceSelection.unknown(),
        expires_at=int(time.time()) + 3600,
    )
    store.redis.values[_card_key(access_id)] = encode_cache_value(
        {
            "kind": "card",
            "card_revision": authority.card_revision,
            "authority": authority.to_dict(),
        }
    )
    return access_id


@pytest.fixture
def ctx(tmp_path):
    if not os.environ.get("REDIS_URL"):
        pytest.skip("REDIS_URL is not set; token issuance commits a delegated card")
    import redis.asyncio as redis_asyncio

    app = FastAPI()
    enable_delegated_client(app)
    mount_test_oauth_adapter(app)
    store = GrantStore(FakeRedis(), tenant="home", project="demo")
    app.state.oauth_grant_store = store
    app.state.oauth_mint_access_token = _fake_minter
    redis = redis_asyncio.from_url(os.environ["REDIS_URL"])
    bind_delegated_card_persistence(app, redis=redis, storage_root=tmp_path)
    # Entered as a context manager so every request in a test runs on the same
    # event loop: the Redis client the card persistence holds cannot survive a
    # per-request loop.
    with TestClient(app) as client:
        yield client, store


async def _seed_code(store, *, redirect_uri="http://127.0.0.1:9000/callback", client_id="claude"):
    return await store.create_auth_code(
        client_id=client_id, redirect_uri=redirect_uri, code_challenge=CHALLENGE,
        sub="google:admin@example.test", scopes=["records:read"], operations=["records_export"],
        identity_scope="grantor_identity_family",
    )


@pytest.mark.asyncio
async def test_access_token_grant_binds_consented_tools(ctx):
    client, store = ctx
    # Reviewer's scenario: admin unchecked the tool -> code carries no tools.
    code = await store.create_auth_code(
        client_id="claude", redirect_uri="http://127.0.0.1:9000/callback",
        code_challenge=CHALLENGE, sub="google:admin@example.test",
        scopes=["records:read"], operations=[],
        grantor_authority={
            "grantor_roles": ["kdcube:role:super-admin"],
            "grantor_permissions": ["kdcube:*:records:*;read"],
            "economics_budget_bypass": True,
        },
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
    assert grant_record["credential"]["schema"] == "kdcube.credential.v1"
    assert grant_record["credential"]["credential_kind"] == DELEGATED_CLIENT_CREDENTIAL_KIND
    assert grant_record["credential"]["issuer_authority_id"] == DELEGATED_CLIENT_AUTHORITY_ID
    assert grant_record["credential"]["issuer_authenticator_id"] == DELEGATED_CLIENT_AUTHENTICATOR_ID
    assert grant_record["credential"]["audience"] == "kdcube:delegated_client"
    assert grant_record["credential"]["attrs"]["identity_scope"] == "grantor"
    assert grant_record["resource_operations"] == {}
    assert "grantor_roles" not in grant_record["credential"]["attrs"]
    assert grant_record["grantor_authority"]["grantor_roles"] == ["kdcube:role:super-admin"]
    assert grant_record["grantor_authority"]["economics_budget_bypass"] is True
    refresh_record = await store.validate_refresh_token(body["refresh_token"])
    assert refresh_record["operations"] == []
    assert refresh_record["resource_operations"] == {}
    assert refresh_record["credential"]["issuer_authority_id"] == DELEGATED_CLIENT_AUTHORITY_ID
    assert refresh_record["grantor_authority"]["grantor_permissions"] == ["kdcube:*:records:*;read"]


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
    assert body["scope"] == "records:read"
    assert body["refresh_token"]
    refresh_record = await store.validate_refresh_token(body["refresh_token"])
    assert body["access_id"] == refresh_record["registry_access_id"]
    assert refresh_record["credential"]["subject"] == "integration:claude:google:admin@example.test"
    assert refresh_record["resource_operations"] == {"*": ["records_export"]}
    assert refresh_record["credential"]["attrs"]["resource_operations"] == {
        "*": ["records_export"]
    }
    assert refresh_record["identity_scope"] == "grantor_identity_family"
    assert refresh_record["credential"]["attrs"]["identity_scope"] == "grantor_identity_family"
    assert r.headers.get("Cache-Control") == "no-store"


@pytest.mark.asyncio
async def test_owner_connector_authority_survives_oauth_exchange_and_refresh(ctx):
    client, store = ctx
    proxy = "https://hub.example.test/mcp/remote_mcp_proxy"
    connector = "urn:connection-hub:remote-mcp:mcp_0123456789abcdef01234567"
    resource_grants = {
        proxy: ["external_mcp:use"],
        connector: ["external_mcp:use"],
    }
    resource_operations = {
        proxy: [],
        connector: ["records.search"],
    }
    code = await store.create_auth_code(
        client_id="openclaw",
        redirect_uri="http://127.0.0.1:9000/callback",
        code_challenge=CHALLENGE,
        sub="google:admin@example.test",
        scopes=["external_mcp:use"],
        operations=["records.search"],
        resource=proxy,
        resource_grants=resource_grants,
        resource_operations=resource_operations,
    )

    first = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "http://127.0.0.1:9000/callback",
        "client_id": "openclaw",
        "code_verifier": VERIFIER,
    })

    assert first.status_code == 200
    first_body = first.json()
    first_access = await store.get_access_grant_record(first_body["access_token"])
    first_refresh = await store.validate_refresh_token(first_body["refresh_token"])
    assert first_access["resource_grants"] == resource_grants
    assert first_access["resource_operations"] == resource_operations
    assert first_refresh["resource_grants"] == resource_grants
    assert first_refresh["credential"]["attrs"]["resource_grants"] == resource_grants

    _seed_live_card(
        store,
        first_refresh,
        operations=("records.search",),
        resource_grants={key: tuple(value) for key, value in resource_grants.items()},
        resource_operations={
            key: tuple(value) for key, value in resource_operations.items()
        },
    )
    refreshed = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": first_body["refresh_token"],
        "client_id": "openclaw",
    })

    assert refreshed.status_code == 200
    refreshed_body = refreshed.json()
    refreshed_access = await store.get_access_grant_record(
        refreshed_body["access_token"]
    )
    refreshed_record = await store.validate_refresh_token(
        refreshed_body["refresh_token"]
    )
    assert refreshed_access["resource_grants"] == resource_grants
    assert refreshed_access["resource_operations"] == resource_operations
    assert refreshed_record["resource_grants"] == resource_grants
    assert refreshed_record["resource_operations"] == resource_operations


@pytest.mark.asyncio
async def test_token_is_withheld_when_the_card_does_not_commit(ctx, monkeypatch):
    client, store = ctx
    code = await _seed_code(store)

    async def failing_persist(self, *args, **kwargs):
        raise CardCommitFailed("durable_commit_failed")

    monkeypatch.setattr(DurableCardPersistence, "persist", failing_persist)

    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "http://127.0.0.1:9000/callback",
        "client_id": "claude",
        "code_verifier": VERIFIER,
    })

    assert r.status_code == 503
    assert r.json()["error"] == "temporarily_unavailable"
    # The code was consumed before the card commit was attempted, so the client
    # re-consents rather than retrying this exchange.
    replay = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "http://127.0.0.1:9000/callback",
        "client_id": "claude",
        "code_verifier": VERIFIER,
    })
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


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
    _seed_live_card(store, await store.validate_refresh_token(rt))

    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": rt, "client_id": "claude",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] == "kst1.mock.google:admin@example.test"
    assert body["refresh_token"] and body["refresh_token"] != rt   # rotated
    refresh_record = await store.validate_refresh_token(body["refresh_token"])
    assert refresh_record["identity_scope"] == "grantor_identity_family"
    assert refresh_record["credential"]["attrs"]["identity_scope"] == "grantor_identity_family"
    assert refresh_record["resource_operations"] == {"*": ["records_export"]}

    # Old refresh token no longer works.
    again = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": rt, "client_id": "claude",
    })
    assert again.status_code == 400


@pytest.mark.asyncio
async def test_refresh_token_keeps_old_token_when_live_card_lookup_is_unavailable(ctx):
    client, store = ctx
    code = await _seed_code(store)
    first = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "http://127.0.0.1:9000/callback",
        "client_id": "claude",
        "code_verifier": VERIFIER,
    }).json()
    refresh_token = first["refresh_token"]
    refresh_record = await store.validate_refresh_token(refresh_token)
    card_key = _card_key(_seed_live_card(store, refresh_record))
    original_get = store.redis.get

    async def failing_card_get(key):
        if key == card_key:
            raise RuntimeError("live card unavailable")
        return await original_get(key)

    store.redis.get = failing_card_get

    response = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": "claude",
    })

    assert response.status_code == 503
    assert response.json()["error"] == "temporarily_unavailable"
    assert await store.validate_refresh_token(refresh_token) is not None


@pytest.mark.asyncio
async def test_refresh_token_rejects_malformed_live_card_without_rotation(ctx):
    client, store = ctx
    code = await _seed_code(store)
    first = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "http://127.0.0.1:9000/callback",
        "client_id": "claude",
        "code_verifier": VERIFIER,
    }).json()
    refresh_token = first["refresh_token"]
    refresh_record = await store.validate_refresh_token(refresh_token)
    store.redis.values[_card_key(refresh_record["registry_access_id"])] = "{"

    response = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": "claude",
    })

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"
    assert await store.validate_refresh_token(refresh_token) is not None


@pytest.mark.asyncio
async def test_refresh_token_applies_empty_live_grant_narrowing(ctx):
    client, store = ctx
    code = await _seed_code(store)
    first = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "http://127.0.0.1:9000/callback",
        "client_id": "claude",
        "code_verifier": VERIFIER,
    }).json()
    refresh_token = first["refresh_token"]
    refresh_record = await store.validate_refresh_token(refresh_token)
    resource = str(refresh_record.get("resource") or "") or "*"
    _seed_live_card(
        store, refresh_record, operations=(), resource_grants={resource: ()}
    )

    response = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": "claude",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == ""
    assert await store.get_access_grant(body["access_token"]) == []
    rotated = await store.validate_refresh_token(body["refresh_token"])
    assert rotated["operations"] == []
    assert rotated["scopes"] == []


def test_unknown_refresh_token_is_invalid_grant(ctx):
    client, _ = ctx
    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": "bogus", "client_id": "claude",
    })
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_grant_store_outage_is_logged_and_returned_as_retryable_503(ctx, caplog):
    client, store = ctx

    async def unavailable_get(key):
        raise ConnectionError("redis unavailable")

    store.redis.get = unavailable_get
    with caplog.at_level("ERROR", logger="kdcube.connection_hub.oauth"):
        response = client.post("/oauth/token", data={
            "grant_type": "refresh_token",
            "refresh_token": "cannot-read",
            "client_id": "claude",
        })

    assert response.status_code == 503
    assert response.json() == {
        "error": "temporarily_unavailable",
        "error_description": "OAuth authorization state is temporarily unavailable",
    }
    assert "route=token operation=refresh_token.read" in caplog.text
