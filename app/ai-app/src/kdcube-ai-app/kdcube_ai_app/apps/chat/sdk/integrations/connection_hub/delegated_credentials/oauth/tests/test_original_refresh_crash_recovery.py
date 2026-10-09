"""Actual process termination with PG metadata; no bearer custody anywhere.

A fresh process re-signs the stored claims with the same synthetic key and must
match the sealed fingerprint. Deployed key storage and the full Hub hosting
process are not qualified here.
"""
from __future__ import annotations

import asyncio
import hashlib
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


async def finish(process):
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        return process.returncode, output
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["after_reservation", "after_seal", "after_ready"])
async def test_sigkill_fresh_process_recovers_same_original_refresh(store, tmp_path, phase):
    plan = _plan(tenant=store.tenant, project=store.project)
    request = {"dsn": os.environ["KDCUBE_TEST_POSTGRES_DSN"], "plan": plan.to_dict(), "phase": phase}
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
    assert (before["bearer_sha256"] is None) == (phase == "after_reservation")
    if before["bearer_sha256"] is not None:
        # A sealed original never yields another bearer: a changed key refuses and writes nothing.
        code, output = await finish(await launch({**request, "phase": None,
                                                  "key": "different-unit-refresh-signing-key!"}))
        assert code != 0 and output == b""
        async with db._db._connection() as connection:
            assert dict(await connection.fetchrow(f"SELECT * FROM {db.schema}.{TABLE_REFRESH}")) == dict(before)
    expected = {"sid": claims["sid"], "secret_ref": before["secret_ref"],
                "expires_at": plan.expires_at, "claims_digest": digest(canonical(claims)), "state": "ready"}
    code, output = await finish(await launch({**request, "phase": None}))
    assert code == 0, "fresh recovery process refused original"
    recovered = json.loads(output)
    assert {key: recovered[key] for key in expected} == expected
    async with db._db._connection() as connection:
        after = await connection.fetchrow(f"SELECT * FROM {db.schema}.{TABLE_REFRESH}")
        assert await connection.fetchval(f"SELECT count(*) FROM {db.schema}.{TABLE_REFRESH}") == 1
    assert recovered["bearer_sha256"] == recovered["delivered_sha256"] == after["bearer_sha256"]
    assert before["original_digest"] == after["original_digest"]
    if before["bearer_sha256"] is not None:
        assert recovered["bearer_sha256"] == before["bearer_sha256"]
    assert list(tmp_path.iterdir()) == []


@pytest_asyncio.fixture
async def pg_refresh(store):
    r = SimpleNamespace(plan=_plan(tenant=store.tenant, project=store.project), signs=0)
    r.db = PostgresOriginalRefreshStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    await r.db.ensure_schema()
    async def key():
        r.signs += 1
        return b"unit-original-refresh-key-32-bytes!"
    r.issuer = OriginalRefreshIssuer(store=r.db,
        signer=HmacOriginalRefreshSigner(store.tenant, store.project, key),
        card_kind="automation", ttl_seconds=180 * 86400)
    return r


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["after_reservation", "after_seal", "after_ready"])
async def test_lost_confirmed_pg_response_recovers_same_original_refresh(pg_refresh, phase):
    r = pg_refresh
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
    if before["bearer_sha256"] is not None:
        assert recovered.bearer_sha256 == before["bearer_sha256"]
    bearer = await r.issuer.bearer(plan=r.plan)
    assert hashlib.sha256(bearer.encode()).hexdigest() == recovered.bearer_sha256
    async with r.db._db._connection() as connection:
        assert await connection.fetchval(f"SELECT count(*) FROM {r.db.schema}.{TABLE_REFRESH}") == 1


@pytest.mark.asyncio
async def test_abort_before_inflight_ready_seal_fences_original(pg_refresh):
    r = pg_refresh
    seal = r.db.seal
    async def terminal_after_seal(original, token_sha256, *, ready=False):
        sealed = await seal(original, token_sha256, ready=ready)
        if not ready:
            result = OAuthIssuanceResult(transaction_id=r.plan.transaction_id, intent_digest=r.plan.intent_digest,
                state="aborted", access_id=r.plan.access_id, card_revision=r.plan.base_revision,
                expires_at=r.plan.expires_at, delivery_deadline=r.plan.delivery_deadline, receipt_digest="",
                per_slot={"refresh": SlotOutcome("released", r.plan.effect_digests["refresh"], sealed.bearer_sha256)})
            await r.issuer.retire(plan=r.plan, terminal=TerminalIssuanceContext.from_oauth_result(sealed.context, result))
        return sealed
    r.db.seal = terminal_after_seal
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_terminal$"):
        await r.issuer.prepare(plan=r.plan)
    r.db.seal = seal
    async with r.db._db._connection() as connection:
        row = await connection.fetchrow(f"SELECT * FROM {r.db.schema}.{TABLE_REFRESH}")
    assert row["state"] == "retired" and row["terminal_digest"] is not None
    signs = r.signs
    # After the terminal commit, preparation, delivery and reads refuse before any signing.
    for call in (r.issuer.prepare, r.issuer.bearer, r.issuer.read):
        with pytest.raises(OriginalExchangeRefused, match="^original_refresh_terminal$"):
            await call(plan=r.plan)
    assert r.signs == signs
