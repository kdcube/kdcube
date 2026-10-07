"""First original access expiry: real PostgreSQL, synthetic validated Hub plan."""
from __future__ import annotations

import asyncio
import json
import sys
import time

import pytest

from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import store
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import (
    OriginalExchangeRefused,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange_store import (
    PostgresOriginalExchangeStore, TABLE_EXCHANGES,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_original_exchange import (
    ledger, plan, proof, store_namespace, validated,
)


async def pinned(store, ledger, **changes):
    binding = validated(store)
    await ledger.begin(binding)
    return await ledger.pin_plan(binding, plan(binding, **changes))


@pytest.mark.asyncio
async def test_first_access_expiry_survives_restart_and_elapsed_time(store, ledger):
    before = await pinned(store, ledger)
    first = await ledger.capture_access_expiry(proof(store), ttl_seconds=3600)
    assert first.access_ttl_seconds == 3600
    assert int(time.time()) + 3598 <= first.access_expires_at <= int(time.time()) + 3600
    async with store._pool.acquire() as connection:
        await connection.execute("SELECT pg_sleep(1.1)")
    restarted = PostgresOriginalExchangeStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    replay = await restarted.capture_access_expiry(proof(store), ttl_seconds=3600)
    assert replay.access_expires_at == first.access_expires_at
    assert (await restarted.read(proof(store))).access_expires_at == first.access_expires_at
    assert replay.plan == before.plan and replay.delivery_deadline == before.delivery_deadline
    assert replay.pending_until == before.pending_until


@pytest.mark.asyncio
async def test_first_access_expiry_is_capped_by_original_card_deadline(store, ledger):
    cap = int(time.time()) + 30
    await pinned(store, ledger, expires_at=cap, delivery_deadline=cap, reserved_until=cap)
    assert (await ledger.capture_access_expiry(proof(store), ttl_seconds=3600)).access_expires_at == cap


@pytest.mark.asyncio
async def test_concurrent_access_expiry_capture_has_one_original_without_nested_pool_use(store, ledger):
    await pinned(store, ledger)
    rows = await asyncio.wait_for(asyncio.gather(*(
        ledger.capture_access_expiry(proof(store), ttl_seconds=3600) for _ in range(16)
    )), 15)
    assert len({row.access_expires_at for row in rows}) == 1
    async with store._pool.acquire() as connection:
        assert await connection.fetchval(f"SELECT count(*) FROM {ledger.schema}.{TABLE_EXCHANGES}") == 1


@pytest.mark.asyncio
async def test_capture_cannot_manufacture_a_missing_or_unplanned_exchange(store, ledger):
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_not_validated$"):
        await ledger.capture_access_expiry(proof(store), ttl_seconds=3600)
    await ledger.begin(validated(store))
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_not_planned$"):
        await ledger.capture_access_expiry(proof(store), ttl_seconds=3600)
    assert (await ledger.read(proof(store))).access_expires_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl", [0, True, "3600", 3601])
async def test_access_ttl_must_be_an_explicit_bounded_integer(store, ledger, ttl):
    await pinned(store, ledger)
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_access_expiry_invalid$"):
        await ledger.capture_access_expiry(proof(store), ttl_seconds=ttl)
    assert (await ledger.read(proof(store))).access_expires_at is None


@pytest.mark.asyncio
async def test_replay_cannot_change_the_original_access_ttl(store, ledger):
    await pinned(store, ledger)
    first = await ledger.capture_access_expiry(proof(store), ttl_seconds=3600)
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_identity_conflict$"):
        await ledger.capture_access_expiry(proof(store), ttl_seconds=1800)
    assert (await ledger.read(proof(store))).access_expires_at == first.access_expires_at


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"client_id": "other-client"}, {"redirect_uri": "https://unit.test/other"},
    {"verifier": "wrong-verifier" + "z" * 48}, {"tenant": "other-tenant"},
])
async def test_access_expiry_capture_requires_the_same_retry_proof(store, ledger, changes):
    await pinned(store, ledger)
    reason = "namespace_mismatch" if "tenant" in changes else "proof_mismatch"
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_" + reason + "$"):
        await ledger.capture_access_expiry(proof(store, **changes), ttl_seconds=3600)
    assert (await ledger.read(proof(store))).access_expires_at is None


@pytest.mark.asyncio
async def test_expired_original_access_cannot_reopen_before_delivery_deadline(store, ledger):
    binding = validated(store)
    await ledger.begin(binding)
    original_plan = plan(binding)
    await ledger.pin_plan(binding, original_plan)
    await ledger.capture_access_expiry(proof(store), ttl_seconds=3600)
    async with store._pool.acquire() as connection:
        await connection.execute(f"UPDATE {ledger.schema}.{TABLE_EXCHANGES} SET access_expires_at=to_timestamp(1)")
    for call in (
        lambda: ledger.capture_access_expiry(proof(store), ttl_seconds=3600),
        lambda: ledger.read(proof(store)), lambda: ledger.begin(binding),
        lambda: ledger.pin_plan(binding, original_plan),
    ):
        with pytest.raises(OriginalExchangeRefused, match="^original_exchange_access_expired$"):
            await call()
    async with store._pool.acquire() as connection:
        assert await connection.fetchval(f"SELECT count(*) FROM {ledger.schema}.{TABLE_EXCHANGES}") == 1
        assert await connection.fetchval(f"SELECT extract(epoch FROM access_expires_at)::bigint FROM {ledger.schema}.{TABLE_EXCHANGES}") == 1


@pytest.mark.asyncio
async def test_access_expiry_is_checked_after_actual_row_lock_wait(store, ledger):
    await pinned(store, ledger)
    await ledger.capture_access_expiry(proof(store), ttl_seconds=3600)
    async with store._pool.acquire() as blocker:
        transaction = blocker.transaction()
        await transaction.start()
        try:
            await blocker.execute(f"UPDATE {ledger.schema}.{TABLE_EXCHANGES} SET access_expires_at=clock_timestamp() + interval '500 milliseconds'")
            replay = asyncio.create_task(ledger.capture_access_expiry(proof(store), ttl_seconds=3600))
            await blocker.execute("SELECT pg_sleep(0.7)")
            assert not replay.done()
        finally:
            await transaction.commit()
        with pytest.raises(OriginalExchangeRefused, match="^original_exchange_access_expired$"):
            await asyncio.wait_for(replay, 5)


@pytest.mark.asyncio
async def test_additive_schema_keeps_existing_plan_without_inventing_old_access_expiry(store, ledger):
    first = await pinned(store, ledger)
    async with store._pool.acquire() as connection:
        await connection.execute(f"ALTER TABLE {ledger.schema}.{TABLE_EXCHANGES} DROP COLUMN access_expires_at, DROP COLUMN access_ttl_seconds")
    await ledger.ensure_schema()
    recovered = await ledger.read(proof(store))
    assert recovered.plan == first.plan and recovered.delivery_deadline == first.delivery_deadline
    assert recovered.access_expires_at is None and recovered.access_ttl_seconds is None


@pytest.mark.asyncio
async def test_a_fresh_interpreter_reads_the_original_access_expiry(store, ledger):
    await pinned(store, ledger)
    first = await ledger.capture_access_expiry(proof(store), ttl_seconds=3600)
    script = """
import asyncio, json, os, sys
from types import SimpleNamespace
import asyncpg
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_original_exchange import proof
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange_store import PostgresOriginalExchangeStore
async def main():
    namespace = SimpleNamespace(**json.loads(sys.stdin.read()))
    pool = await asyncpg.create_pool(os.environ['KDCUBE_TEST_POSTGRES_DSN'], min_size=1, max_size=1)
    try:
        row = await PostgresOriginalExchangeStore(pg_pool=pool, tenant=namespace.tenant, project=namespace.project).read(proof(namespace))
        print(json.dumps({'access_expires_at': row.access_expires_at, 'access_ttl_seconds': row.access_ttl_seconds}))
    finally:
        await pool.close()
asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps({
        "tenant": store.tenant, "project": store.project,
    }).encode()), 15)
    assert process.returncode == 0, stderr.decode()
    assert json.loads(stdout) == {"access_expires_at": first.access_expires_at, "access_ttl_seconds": 3600}


@pytest.mark.asyncio
@pytest.mark.parametrize("assignment", [
    "access_ttl_seconds=NULL", "access_ttl_seconds=0", "access_expires_at=NULL",
])
async def test_malformed_stored_access_expiry_pair_refuses(store, ledger, assignment):
    await pinned(store, ledger)
    await ledger.capture_access_expiry(proof(store), ttl_seconds=3600)
    async with store._pool.acquire() as connection:
        await connection.execute(f"UPDATE {ledger.schema}.{TABLE_EXCHANGES} SET {assignment}")
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_access_expiry_invalid$"):
        await ledger.read(proof(store))


@pytest.mark.asyncio
async def test_access_capture_pool_failure_is_finite_and_secret_free(store_namespace):
    class FailedPool:
        def acquire(self, *, timeout):
            assert timeout == 5
            raise OSError("synthetic-sensitive-provider-failure")

    value = PostgresOriginalExchangeStore(
        pg_pool=FailedPool(), tenant=store_namespace.tenant, project=store_namespace.project,
    )
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_unavailable$") as error:
        await value.capture_access_expiry(proof(store_namespace), ttl_seconds=3600)
    assert error.value.__suppress_context__ and error.value.__cause__ is None
    assert "synthetic-sensitive" not in str(error.value)
