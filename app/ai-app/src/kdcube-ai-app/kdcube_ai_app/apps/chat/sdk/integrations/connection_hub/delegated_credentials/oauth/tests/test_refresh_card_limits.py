# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Trusted live Card limits reach the portable rotation boundary before mint."""
from __future__ import annotations

import time
from functools import wraps
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from connection_hub.delegated_credentials.cards.model import CardAuthority
from connection_hub.delegated_credentials.oauth import store as portable_store
from connection_hub.delegated_credentials.oauth.store import GrantStore
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http import routes
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.helpers import (
    enable_delegated_client,
    mount_test_oauth_adapter,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_clients_and_store import FakeRedis


@pytest.fixture
def ctx(monkeypatch):
    app = FastAPI()
    enable_delegated_client(app)
    mount_test_oauth_adapter(app)
    store = GrantStore(FakeRedis(), tenant="home", project="demo")
    app.state.oauth_grant_store = store
    issued = []

    async def issue(_request, _store, **kwargs):
        issued.append(kwargs)
        return JSONResponse({"refresh_token": kwargs["refresh_token"]})

    monkeypatch.setattr(routes, "_issue_tokens", issue)
    monkeypatch.setattr(routes, "delegated_card_store", lambda **kwargs: None)
    with TestClient(app) as client:
        yield client, store, issued


async def _seed(store, *, pointer="trusted-card", kind="automation"):
    return await store.create_refresh_token(
        client_id="client", sub="human", scopes=["records:read"],
        operations=["records_export"], registry_access_id=pointer,
        card_kind=kind, resource="*",
    )


def _card(*, kind="automation", expires_at=0, revision=8):
    return CardAuthority(
        access_id="trusted-card", client_id="client", grantor_subject="human",
        delegate_subject="", source="oauth", card_kind=kind,
        card_revision=revision, expires_at=expires_at,
        operations=("records_export",), resource_grants={"*": ("records:read",)},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["automation", "agent", "connector"])
@pytest.mark.parametrize("bounded", [False, True])
async def test_pointer_refresh_forwards_exact_live_card_limits(ctx, monkeypatch, kind, bounded):
    client, store, issued = ctx
    token = await _seed(store, kind=kind)
    deadline = int(time.time()) + 300 if bounded else 0
    card = _card(kind=kind, expires_at=deadline)
    rotations = []
    original_rotate = store.rotate_refresh_token

    async def resolve(_redis, **kwargs):
        assert kwargs["access_id"] == "trusted-card"
        assert kwargs["expected_client_id"] == "client"
        assert kwargs["expected_grantor_subject"] == "human"
        return card

    @wraps(original_rotate)
    async def rotate(token, **kwargs):
        rotations.append(kwargs)
        return await original_rotate(token, **kwargs)

    monkeypatch.setattr(routes, "resolve_live_grant_card", resolve)
    monkeypatch.setattr(store, "rotate_refresh_token", rotate)
    response = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": token, "client_id": "client",
        "registry_access_id": "attacker-card", "expires_at_cap": "9999999999",
        "card_incarnation": "999999", "cap_expires_at": "9999999999",
    })

    assert response.status_code == 200, response.json()
    assert len(issued) == 1
    assert rotations[0]["card_incarnation"] == card.card_revision
    if bounded:
        assert rotations[0]["expires_at_cap"] == deadline
        ttl = store.redis.ttls[store._key("refresh", response.json()["refresh_token"])]
        assert 0 < ttl <= 300
    else:
        assert "expires_at_cap" not in rotations[0]
    assert await store.validate_refresh_token(token) is None


@pytest.mark.asyncio
async def test_unbound_refresh_does_not_accept_caller_card_limits(ctx, monkeypatch):
    client, store, issued = ctx
    token = await _seed(store, pointer="", kind="")
    rotations = []
    original_rotate = store.rotate_refresh_token

    async def resolve(*args, **kwargs):
        pytest.fail("An unbound stored record must not resolve a caller Card")

    @wraps(original_rotate)
    async def rotate(token, **kwargs):
        rotations.append(kwargs)
        return await original_rotate(token, **kwargs)

    monkeypatch.setattr(routes, "resolve_live_grant_card", resolve)
    monkeypatch.setattr(store, "rotate_refresh_token", rotate)
    response = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": token, "client_id": "client",
        "registry_access_id": "trusted-card", "expires_at_cap": "1", "card_incarnation": "8",
    })

    assert response.status_code == 200, response.json()
    assert len(issued) == 1
    assert "expires_at_cap" not in rotations[0]
    assert "card_incarnation" not in rotations[0]
    ttl = store.redis.ttls[store._key("refresh", response.json()["refresh_token"])]
    assert ttl == store._refresh_ttl


@pytest.mark.asyncio
async def test_deadline_passed_after_live_lookup_refuses_before_mint(ctx, monkeypatch):
    client, store, issued = ctx
    token = await _seed(store)

    async def resolve(*args, **kwargs):
        return _card(expires_at=1001)

    monkeypatch.setattr(routes, "resolve_live_grant_card", resolve)
    monkeypatch.setattr(portable_store, "time", SimpleNamespace(time=lambda: 1002))
    response = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": token, "client_id": "client",
    })

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"
    assert issued == []
    assert await store.validate_refresh_token(token) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["legacy", "cap_only", "revision_only", "kwargs", "positional_only", "uninspectable"])
@pytest.mark.parametrize("bounded", [False, True])
async def test_unsupported_card_rotation_api_refuses_before_consume_or_mint(ctx, monkeypatch, caplog, api, bounded):
    client, store, issued = ctx
    token = await _seed(store)
    rotations = []

    async def resolve(*args, **kwargs):
        return _card(expires_at=int(time.time()) + 300 if bounded else 0)

    async def legacy(token, *, scopes=None):
        rotations.append(token)

    async def cap_only(token, *, expires_at_cap=None):
        rotations.append(token)

    async def revision_only(token, *, card_incarnation=None):
        rotations.append(token)

    async def kwargs_sink(token, **kwargs):
        rotations.append(token)

    async def positional_only(token, expires_at_cap=None, card_incarnation=None, /):
        rotations.append(token)

    class Uninspectable:
        __signature__ = 1

        async def __call__(self, *args, **kwargs):
            rotations.append(token)

    methods = {
        "legacy": legacy, "cap_only": cap_only, "revision_only": revision_only,
        "kwargs": kwargs_sink, "positional_only": positional_only,
        "uninspectable": Uninspectable(),
    }
    monkeypatch.setattr(store, "rotate_refresh_token", methods[api])
    monkeypatch.setattr(routes, "resolve_live_grant_card", resolve)
    with caplog.at_level("WARNING", logger="kdcube.connection_hub.oauth"):
        response = client.post("/oauth/token", data={
            "grant_type": "refresh_token", "refresh_token": token, "client_id": "client",
        })

    assert response.status_code == 503, response.json()
    assert response.json()["error"] == "temporarily_unavailable"
    assert response.headers["retry-after"] == "30"
    assert "refresh_card_limits_unsupported" in caplog.text
    assert token not in caplog.text
    assert rotations == []
    assert issued == []
    assert await store.validate_refresh_token(token) is not None


def test_mixed_facade_and_authority_api_is_not_qualified():
    async def current(token, *, expires_at_cap=None, card_incarnation=None):
        pass

    async def legacy(token):
        pass

    authority = SimpleNamespace(rotate_refresh_token=legacy)
    store = SimpleNamespace(rotate_refresh_token=current, _authority_store=authority)
    assert routes._refresh_store_supports_card_limits(store) is False
    authority.rotate_refresh_token = current
    assert routes._refresh_store_supports_card_limits(store) is True


@pytest.mark.asyncio
async def test_legacy_authority_refuses_without_calling_rotation(ctx, monkeypatch):
    client, store, issued = ctx
    token = await _seed(store)
    state = await store.get_refresh_token_state(token)
    rotations = []

    async def read(token):
        return state

    async def legacy_rotate(token, replacement, *, ttl_seconds):
        rotations.append(token)

    async def resolve(*args, **kwargs):
        return _card()

    monkeypatch.setattr(store, "_authority_store", SimpleNamespace(
        get_refresh_token_state=read, rotate_refresh_token=legacy_rotate,
    ))
    monkeypatch.setattr(routes, "resolve_live_grant_card", resolve)
    response = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": token, "client_id": "client",
    })
    assert response.status_code == 503, response.json()
    assert rotations == []
    assert issued == []
    monkeypatch.setattr(store, "_authority_store", None)
    assert await store.validate_refresh_token(token) is not None


@pytest.mark.asyncio
async def test_unbound_refresh_keeps_legacy_rotation_api(ctx, monkeypatch):
    client, store, issued = ctx
    token = await _seed(store, pointer="", kind="")
    original_rotate = store.rotate_refresh_token
    rotations = []

    async def legacy(token, *, scopes=None, operations=None, resource_grants=None,
                     resource_operations=None, resource=None, card_kind=None, state=None):
        rotations.append(token)
        return await original_rotate(
            token, scopes=scopes, operations=operations, resource_grants=resource_grants,
            resource_operations=resource_operations, resource=resource, card_kind=card_kind,
            state=state,
        )

    monkeypatch.setattr(store, "rotate_refresh_token", legacy)
    response = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": token, "client_id": "client",
    })
    assert response.status_code == 200, response.json()
    assert rotations == [token]
    assert len(issued) == 1


@pytest.mark.asyncio
async def test_actual_package_without_card_api_refuses_before_mint(ctx, monkeypatch):
    client, store, issued = ctx
    if routes._refresh_store_supports_card_limits(store):
        pytest.skip("This cross-version regression requires the older package API")
    token = await _seed(store)

    async def resolve(*args, **kwargs):
        return _card()

    monkeypatch.setattr(routes, "resolve_live_grant_card", resolve)
    response = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": token, "client_id": "client",
    })
    assert response.status_code == 503, response.json()
    assert response.json()["error"] == "temporarily_unavailable"
    assert issued == []
    assert await store.validate_refresh_token(token) is not None


@pytest.mark.asyncio
async def test_missing_live_card_refuses_without_rotation_or_mint(ctx, monkeypatch):
    client, store, issued = ctx
    token = await _seed(store)

    async def resolve(*args, **kwargs):
        return None

    async def rotate(*args, **kwargs):
        pytest.fail("A revoked or expired Card must be refused before rotation")

    monkeypatch.setattr(routes, "resolve_live_grant_card", resolve)
    monkeypatch.setattr(store, "rotate_refresh_token", rotate)
    response = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": token, "client_id": "client",
    })

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"
    assert issued == []
    assert await store.validate_refresh_token(token) is not None
