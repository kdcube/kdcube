# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Late host issuance refusal cleanup; synthetic minter, real or fixture store."""
from __future__ import annotations

import json
import os
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio

from connection_hub.delegated_credentials.oauth.authority_store import PostgresOAuthAuthorityStore
from connection_hub.delegated_credentials.oauth.store import GrantStore
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.cards.service import CardConflict
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http import routes
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_clients_and_store import FakeRedis

ACCESS = "synthetic-withheld-access-no-live-credential"


@pytest_asyncio.fixture(params=["redis-fixture", "postgres"])
async def grant_store(request):
    if request.param == "redis-fixture":
        yield GrantStore(FakeRedis(), tenant="home", project="cleanup")
        return
    dsn = os.environ.get("KDCUBE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("KDCUBE_TEST_POSTGRES_DSN is not set")
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    authority = PostgresOAuthAuthorityStore(
        pg_pool=pool, tenant="w585-disposable-cleanup", project=uuid.uuid4().hex,
    )
    try:
        await authority.ensure_schema()
        yield GrantStore(FakeRedis(), tenant=authority.tenant, project=authority.project,
                         authority_store=authority)
    finally:
        async with pool.acquire() as connection:
            await connection.execute(f'DROP SCHEMA "{authority.schema}" CASCADE')
        await pool.close()


async def _refused_issuance(store, monkeypatch, *, reason, replace_authority=False):
    observed = {}

    async def mint(sub, scopes):
        observed["mint_count"] = observed.get("mint_count", 0) + 1
        return {"access_token": ACCESS, "expires_in": 300}

    async def record(**kwargs):
        observed.update(kwargs)
        # Both credentials exist when the actual Hub callback refuses.
        assert await store.get_access_grant_record(ACCESS) is not None
        assert await store.validate_refresh_token(kwargs["refresh_token"]) is not None
        raise CardConflict(reason, current_revision=9)

    monkeypatch.setattr(routes, "oauth_tenant_project", lambda request: ("home", "cleanup"))
    monkeypatch.setattr(routes, "get_access_token_minter", lambda request: mint)
    monkeypatch.setattr(routes, "get_automation_access", lambda request: SimpleNamespace(record_oauth_grant=record))
    response = await routes._issue_tokens(
        SimpleNamespace(), store, sub="human", scopes=["records:read"], client_id="client",
        operations=["records_export"], resource="*", registry_access_id="synthetic-card",
        card_kind="automation", replace_authority=replace_authority,
        expected_card_revision=7 if replace_authority else None,
    )
    return response, observed


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["delegated_access_requires_grantor", "synthetic_late_card_conflict"])
@pytest.mark.parametrize("replace_authority", [False, True], ids=["generic-refusal", "revision-conflict"])
async def test_late_card_conflict_revokes_both_withheld_credentials(grant_store, monkeypatch, reason, replace_authority):
    response, observed = await _refused_issuance(
        grant_store, monkeypatch, reason=reason, replace_authority=replace_authority,
    )
    assert response.status_code == (400 if replace_authority else 503)
    body = json.loads(response.body)
    assert body["error"] == ("invalid_grant" if replace_authority else "temporarily_unavailable")
    assert "access_token" not in body and "refresh_token" not in body
    assert observed["mint_count"] == 1
    assert await grant_store.get_access_grant_record(ACCESS) is None
    assert await grant_store.validate_refresh_token(observed["refresh_token"]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_operation", ["revoke_access_grant", "revoke_refresh_token"])
@pytest.mark.parametrize("replace_authority", [False, True], ids=["generic-refusal", "revision-conflict"])
async def test_one_cleanup_failure_does_not_prevent_the_other_or_expose_credentials(grant_store, monkeypatch, caplog, failed_operation, replace_authority):
    calls = []
    for name in ("revoke_access_grant", "revoke_refresh_token"):
        original = getattr(grant_store, name)

        async def revoke(token, *, operation=name, delegate=original):
            calls.append(operation)
            if operation == failed_operation:
                raise RuntimeError(ACCESS)
            return await delegate(token)

        monkeypatch.setattr(grant_store, name, revoke)
    response, observed = await _refused_issuance(
        grant_store, monkeypatch, reason="synthetic_late_card_conflict", replace_authority=replace_authority,
    )
    assert response.status_code == (400 if replace_authority else 503)
    assert calls == ["revoke_access_grant", "revoke_refresh_token"]
    assert ACCESS not in caplog.text and ACCESS.encode() not in response.body
    if failed_operation != "revoke_access_grant":
        assert await grant_store.get_access_grant_record(ACCESS) is None
    if failed_operation != "revoke_refresh_token":
        assert await grant_store.validate_refresh_token(observed["refresh_token"]) is None


@pytest.mark.asyncio
async def test_successful_card_commit_keeps_issued_credentials(grant_store, monkeypatch):
    async def mint(sub, scopes):
        return {"access_token": ACCESS, "expires_in": 300}

    async def record(**kwargs):
        return SimpleNamespace(access_id=kwargs["access_id"])

    async def unexpected_revoke(token):
        pytest.fail("A successful Card commit must retain its issued credentials")

    monkeypatch.setattr(routes, "oauth_tenant_project", lambda request: ("home", "cleanup"))
    monkeypatch.setattr(routes, "get_access_token_minter", lambda request: mint)
    monkeypatch.setattr(routes, "get_automation_access", lambda request: SimpleNamespace(record_oauth_grant=record))
    monkeypatch.setattr(grant_store, "revoke_access_grant", unexpected_revoke)
    monkeypatch.setattr(grant_store, "revoke_refresh_token", unexpected_revoke)
    response = await routes._issue_tokens(
        SimpleNamespace(), grant_store, sub="human", scopes=["records:read"], client_id="client",
        operations=["records_export"], resource="*", registry_access_id="synthetic-card", card_kind="automation",
    )
    assert response.status_code == 200
    body = json.loads(response.body)
    assert await grant_store.get_access_grant_record(body["access_token"]) is not None
    assert await grant_store.validate_refresh_token(body["refresh_token"]) is not None
