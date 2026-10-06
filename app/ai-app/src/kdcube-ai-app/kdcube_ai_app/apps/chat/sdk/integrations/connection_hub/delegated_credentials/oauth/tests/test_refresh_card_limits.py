# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Trusted live Card limits reach the portable rotation boundary before mint."""
from __future__ import annotations

import time
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
