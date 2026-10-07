"""Actual process termination with PG metadata and private fsynced file custody.

Source service ASGI transport and synthetic signing/reader/writer keys are used;
physical volume qualification is stubbed. This does not qualify encryption,
deployment topology or the full Hub hosting process.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from types import SimpleNamespace

import pytest
import pytest_asyncio

from connection_hub.delegated_credentials.oauth_issuance import OAuthIssuanceResult, SlotOutcome
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import store
from kdcube_ai_app.auth.bundle.session_planned_issuance import TerminalIssuanceContext
from kdcube_ai_app.infra.secrets.issuance import issuance_secret_custody
from kdcube_ai_app.infra.secrets.tests.test_runtime_http import rig as secrets_rig, service_adapter
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_grants import _plan
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import OriginalExchangeRefused, canonical, digest
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_store import PostgresOriginalRefreshStore, TABLE_REFRESH
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_issuer import HmacOriginalRefreshSigner, OriginalRefreshIssuer

WORKER = "kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests._original_refresh_crash_worker"


async def launch(request):
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", WORKER,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    process.stdin.write((json.dumps(request) + "\n").encode())
    await process.stdin.drain()
    process.stdin.close()
    return process


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["after_reservation", "after_seal", "after_custody", "after_ready"])
async def test_sigkill_fresh_process_recovers_same_original_refresh(store, tmp_path, phase):
    plan = _plan(tenant=store.tenant, project=store.project)
    request = {"dsn": os.environ["KDCUBE_TEST_POSTGRES_DSN"], "plan": plan.to_dict(),
               "root": str(tmp_path / "runtime"), "phase": phase}
    child = await launch(request)
    try:
        marker = await asyncio.wait_for(child.stdout.readline(), timeout=15)
        assert marker == (phase + "\n").encode(), "child did not reach requested durable boundary"
        child.kill()
        await asyncio.wait_for(child.wait(), timeout=5)
        assert child.returncode == -signal.SIGKILL
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()

    db = PostgresOriginalRefreshStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    async with db._db._connection() as connection:
        before = await connection.fetchrow(f"SELECT * FROM {db.schema}.{TABLE_REFRESH}")
    claims = json.loads(before["claims"]) if isinstance(before["claims"], str) else before["claims"]
    assert before["state"] == ("ready" if phase == "after_ready" else "reserved")
    expected = {"sid": claims["sid"], "secret_ref": before["secret_ref"],
                "expires_at": plan.expires_at, "claims_digest": digest(canonical(claims)), "state": "ready"}
    restarted = await launch({**request, "phase": None, "forbid_sign": phase in {"after_custody", "after_ready"}})
    try:
        output, _ = await asyncio.wait_for(restarted.communicate(), timeout=15)
        assert restarted.returncode == 0, "fresh recovery process refused original"
        recovered = json.loads(output)
    finally:
        if restarted.returncode is None:
            restarted.kill()
            await restarted.wait()
    assert {key: recovered[key] for key in expected} == expected
    async with db._db._connection() as connection:
        after = await connection.fetchrow(f"SELECT * FROM {db.schema}.{TABLE_REFRESH}")
        assert await connection.fetchval(f"SELECT count(*) FROM {db.schema}.{TABLE_REFRESH}") == 1
    assert recovered["bearer_sha256"] == after["bearer_sha256"]
    assert before["original_digest"] == after["original_digest"]
    if before["bearer_sha256"] is not None:
        assert recovered["bearer_sha256"] == before["bearer_sha256"]
    records = json.loads((tmp_path / "runtime" / "custody.json").read_text())
    assert set(records) == {before["secret_ref"]}


@pytest_asyncio.fixture
async def service_refresh(store, secrets_rig, monkeypatch):
    _, app, _, root, _ = secrets_rig
    r = SimpleNamespace(plan=_plan(tenant=store.tenant, project=store.project), signs=0, creates=0, root=root)
    r.db = PostgresOriginalRefreshStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    await r.db.ensure_schema()
    manager = service_adapter(app, monkeypatch)
    create = manager.create_ephemeral_secret
    async def counted_create(**kwargs):
        made = await create(**kwargs)
        r.creates += bool(made)
        return made
    manager.create_ephemeral_secret = counted_create
    r.custody = issuance_secret_custody(namespace="custody", manager=manager)
    async def key():
        r.signs += 1
        return b"unit-original-refresh-key-32-bytes!"
    r.issuer = OriginalRefreshIssuer(store=r.db, custody=r.custody,
        signer=HmacOriginalRefreshSigner(store.tenant, store.project, key),
        card_kind="automation", ttl_seconds=180 * 86400)
    return r


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["after_reservation", "after_seal", "after_ready"])
async def test_lost_confirmed_pg_response_recovers_same_original_refresh(service_refresh, phase):
    r = service_refresh
    method = "reserve" if phase == "after_reservation" else "seal"
    real = getattr(r.db, method)
    lost = True
    async def interrupted(*args, **kwargs):
        nonlocal lost
        original = await real(*args, **kwargs)
        boundary = "after_reservation" if method == "reserve" else (
            "after_ready" if kwargs.get("ready") else "after_seal")
        if lost and boundary == phase:
            lost = False
            raise TimeoutError("synthetic-confirmed-commit-response-lost")
        return original
    setattr(r.db, method, interrupted)
    with pytest.raises(TimeoutError):
        await r.issuer.prepare(plan=r.plan)
    async with r.db._db._connection() as connection:
        before = await connection.fetchrow(f"SELECT * FROM {r.db.schema}.{TABLE_REFRESH}")
    setattr(r.db, method, real)
    recovered = await r.issuer.prepare(plan=r.plan)
    claims = json.loads(before["claims"]) if isinstance(before["claims"], str) else before["claims"]
    assert recovered.claims == claims and recovered.secret_ref == before["secret_ref"]
    assert r.creates == 1 and r.signs == (2 if phase == "after_seal" else 1)
    if before["bearer_sha256"] is not None:
        assert recovered.bearer_sha256 == before["bearer_sha256"]
    async with r.db._db._connection() as connection:
        assert await connection.fetchval(f"SELECT count(*) FROM {r.db.schema}.{TABLE_REFRESH}") == 1


@pytest.mark.asyncio
async def test_abort_before_inflight_service_create_purges_only_original_reference(service_refresh):
    r = service_refresh
    create = r.custody.create
    original_ref = None
    async def terminal_before_create(**kwargs):
        nonlocal original_ref
        original = await r.db.reserve(plan=r.plan, card_kind="automation", ttl_seconds=180 * 86400)
        original_ref = original.secret_ref
        result = OAuthIssuanceResult(transaction_id=r.plan.transaction_id, intent_digest=r.plan.intent_digest,
            state="aborted", access_id=r.plan.access_id, card_revision=r.plan.base_revision,
            expires_at=r.plan.expires_at, delivery_deadline=r.plan.delivery_deadline, receipt_digest="",
            per_slot={"refresh": SlotOutcome("released", r.plan.effect_digests["refresh"], original.bearer_sha256)})
        terminal = TerminalIssuanceContext.from_oauth_result(original.context, result)
        await r.issuer.retire(plan=r.plan, terminal=terminal)
        return await create(**kwargs)
    r.custody.create = terminal_before_create
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_terminal$"):
        await r.issuer.prepare(plan=r.plan)
    assert await r.custody.get(secret_ref=original_ref) is None
    assert r.signs == r.creates == 1
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_terminal$"):
        await r.issuer.prepare(plan=r.plan)
    assert r.signs == r.creates == 1
