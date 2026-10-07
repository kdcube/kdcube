"""Atomic first plan/expiry pin using the real namespace-bound PG ledger."""
from __future__ import annotations

import asyncio
import time

import pytest

from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import store
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import OriginalExchangeRefused
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange_store import PostgresOriginalExchangeStore, TABLE_EXCHANGES
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_original_exchange import ledger, plan, proof, validated


@pytest.mark.asyncio
async def test_atomic_plan_and_expiry_pin_restart_preserves_both(store, ledger):
    binding = validated(store)
    before = await ledger.begin(binding)
    original_plan = plan(binding, expires_at=int(time.time()) + 7200)
    first = await ledger.pin_plan(binding, original_plan, access_ttl_seconds=3600)
    assert first.plan == original_plan.to_dict() and first.access_expires_at is not None
    assert first.access_ttl_seconds == 3600 and first.pending_until == before.pending_until
    restarted = PostgresOriginalExchangeStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    second = await restarted.pin_plan(binding, original_plan, access_ttl_seconds=3600)
    assert second.plan == first.plan and second.access_expires_at == first.access_expires_at


@pytest.mark.asyncio
async def test_atomic_pin_concurrent_replays_keep_one_expiry(store, ledger):
    binding = validated(store)
    await ledger.begin(binding)
    original_plan = plan(binding, expires_at=int(time.time()) + 7200)
    values = await asyncio.gather(*(ledger.pin_plan(binding, original_plan, access_ttl_seconds=3600) for _ in range(12)))
    assert len({value.access_expires_at for value in values}) == 1
    assert all(value.plan == values[0].plan for value in values)


@pytest.mark.asyncio
async def test_atomic_pin_changed_ttl_refuses_without_replacing_first(store, ledger):
    binding = validated(store)
    await ledger.begin(binding)
    original_plan = plan(binding)
    first = await ledger.pin_plan(binding, original_plan, access_ttl_seconds=3600)
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_identity_conflict$"):
        await ledger.pin_plan(binding, original_plan, access_ttl_seconds=60)
    assert (await ledger.read(proof(store))).access_expires_at == first.access_expires_at


@pytest.mark.asyncio
async def test_atomic_pin_does_not_infer_no_mint_from_legacy_unknown_expiry(store, ledger):
    binding = validated(store)
    await ledger.begin(binding)
    original_plan = plan(binding)
    first = await ledger.pin_plan(binding, original_plan)
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_access_expiry_unknown$"):
        await ledger.pin_plan(binding, original_plan, access_ttl_seconds=3600)
    after = await ledger.read(proof(store))
    assert after.plan == first.plan and after.access_expires_at is None and after.access_ttl_seconds is None


@pytest.mark.asyncio
async def test_atomic_pin_caps_first_expiry_to_original_card(store, ledger):
    binding = validated(store)
    await ledger.begin(binding)
    cap = int(time.time()) + 30
    original_plan = plan(binding, expires_at=cap, delivery_deadline=cap, reserved_until=cap)
    assert (await ledger.pin_plan(binding, original_plan, access_ttl_seconds=3600)).access_expires_at == cap


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl", [True, 0, 3601, "3600"])
async def test_invalid_atomic_ttl_never_pins_plan(store, ledger, ttl):
    binding = validated(store)
    await ledger.begin(binding)
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_access_expiry_invalid$"):
        await ledger.pin_plan(binding, plan(binding), access_ttl_seconds=ttl)
    after = await ledger.read(proof(store))
    assert after.plan is None and after.access_expires_at is None


@pytest.mark.asyncio
async def test_unplanned_row_with_existing_expiry_is_not_erased_or_renewed(store, ledger):
    binding = validated(store)
    await ledger.begin(binding)
    async with store._pool.acquire() as connection:
        await connection.execute(f"UPDATE {ledger.schema}.{TABLE_EXCHANGES} SET access_expires_at=clock_timestamp()+interval '5 minutes', access_ttl_seconds=300")
    first = await ledger.read(proof(store))
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_access_expiry_unknown$"):
        await ledger.pin_plan(binding, plan(binding), access_ttl_seconds=3600)
    after = await ledger.read(proof(store))
    assert after.access_expires_at == first.access_expires_at and after.plan is None
