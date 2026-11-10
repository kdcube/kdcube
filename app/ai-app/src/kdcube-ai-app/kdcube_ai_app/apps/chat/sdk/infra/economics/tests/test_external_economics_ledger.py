# SPDX-License-Identifier: MIT

"""`ExternalEconomicsLedger` against a real PostgreSQL.

The ledger is the payment idempotency protocol (claim, apply, mark) that the
Stripe webhook handler, the Stripe admin service and the subscription manager
share. These tests pin the protocol's observable behaviour on the shipped
`external_economics_events` table; see `conftest.py` for why a real database.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.economics.external_events import (
    ExternalEconomicsLedger,
    LedgerKey,
)

ECON_DIR = Path(__file__).resolve().parents[1]


def _key(source="stripe", kind="user_credits", external_id="pi_1") -> LedgerKey:
    return LedgerKey(source=source, kind=kind, external_id=external_id)


async def _claim(ledger, conn, db, key, **kw):
    kw.setdefault("user_id", "user-1")
    kw.setdefault("amount_cents", 500)
    kw.setdefault("currency", "usd")
    return await ledger.claim(conn=conn, key=key, tenant=db.tenant, project=db.project, **kw)


def test_only_the_ledger_module_writes_the_events_table():
    """Within the economics package one module owns the protocol: no other
    module names the table or hard-codes a `source` literal into its SQL."""
    offenders = []
    for path in ECON_DIR.glob("*.py"):
        if path.name == "external_events.py":
            continue
        if "external_economics_events" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert offenders == []


def test_ledger_key_iterates_in_primary_key_order():
    assert tuple(_key("internal", "wallet_refund", "r1")) == ("internal", "wallet_refund", "r1")


async def test_fresh_claim_creates_a_pending_row_with_the_callers_columns(econ_pg):
    db = econ_pg
    ledger = ExternalEconomicsLedger()
    key = _key()
    async with db.pool.acquire() as conn, conn.transaction():
        claim = await _claim(ledger, conn, db, key, tokens=42,
                             provider_event_id="evt_1", metadata={"a": 1})
    assert (claim.status, claim.was_created) == ("pending", True)
    assert not claim.already_applied and not claim.concurrent_duplicate
    row = await db.event_row(*key)
    assert row["tenant"] == db.tenant and row["project"] == db.project
    assert (row["user_id"], row["amount_cents"], row["tokens"], row["currency"]) == ("user-1", 500, 42, "usd")
    assert row["stripe_event_id"] == "evt_1"
    assert row["metadata"] == {"a": 1}
    assert row["applied_at"] is None


async def test_replayed_claim_reports_existing_row_and_changes_nothing(econ_pg):
    db = econ_pg
    ledger = ExternalEconomicsLedger()
    key = _key()
    async with db.pool.acquire() as conn:
        await _claim(ledger, conn, db, key, amount_cents=500)
        replay = await _claim(ledger, conn, db, key, amount_cents=999, user_id="other")
    assert (replay.status, replay.was_created) == ("pending", False)
    assert replay.concurrent_duplicate
    row = await db.event_row(*key)
    assert (row["amount_cents"], row["user_id"]) == (500, "user-1")
    assert await db.event_count() == 1


async def test_claim_after_mark_applied_reports_already_applied(econ_pg):
    db = econ_pg
    ledger = ExternalEconomicsLedger()
    key = _key()
    async with db.pool.acquire() as conn:
        await _claim(ledger, conn, db, key)
        await ledger.mark_applied(conn=conn, key=key, tenant=db.tenant, project=db.project)
        again = await _claim(ledger, conn, db, key)
    assert again.already_applied and not again.was_created
    row = await db.event_row(*key)
    assert row["status"] == "applied" and row["applied_at"] is not None and row["error"] is None


async def test_mark_failed_truncates_tolerates_none_and_keeps_applied_at(econ_pg):
    db = econ_pg
    ledger = ExternalEconomicsLedger()
    key = _key()
    async with db.pool.acquire() as conn:
        await _claim(ledger, conn, db, key)
        await ledger.mark_applied(conn=conn, key=key, tenant=db.tenant, project=db.project)
        applied_at = (await db.event_row(*key))["applied_at"]
        await ledger.mark_failed(conn=conn, key=key, tenant=db.tenant, project=db.project,
                                 error="x" * 5000)
        row = await db.event_row(*key)
        assert row["status"] == "failed" and len(row["error"]) == 2000
        assert row["applied_at"] == applied_at
        await ledger.mark_failed(conn=conn, key=key, tenant=db.tenant, project=db.project, error=None)
    assert (await db.event_row(*key))["error"] == ""


async def test_failed_claim_is_reported_as_failed_so_the_caller_retries(econ_pg):
    db = econ_pg
    ledger = ExternalEconomicsLedger()
    key = _key()
    async with db.pool.acquire() as conn:
        await _claim(ledger, conn, db, key)
        await ledger.mark_failed(conn=conn, key=key, tenant=db.tenant, project=db.project, error="boom")
        again = await _claim(ledger, conn, db, key)
    assert again.status == "failed" and not again.already_applied and not again.concurrent_duplicate


async def test_source_is_part_of_the_identity_and_marks_are_scoped_to_it(econ_pg):
    db = econ_pg
    ledger = ExternalEconomicsLedger()
    stripe_key = _key("stripe", "wallet_refund", "same")
    internal_key = _key("internal", "wallet_refund", "same")
    async with db.pool.acquire() as conn:
        a = await _claim(ledger, conn, db, stripe_key)
        b = await _claim(ledger, conn, db, internal_key)
        await ledger.mark_applied(conn=conn, key=internal_key, tenant=db.tenant, project=db.project)
    assert a.was_created and b.was_created
    assert (await db.event_row(*stripe_key))["status"] == "pending"
    assert (await db.event_row(*internal_key))["status"] == "applied"


async def test_fetch_for_update_reads_a_row_written_under_another_source(econ_pg):
    """The webhook handler resolves rows the admin service created."""
    db = econ_pg
    ledger = ExternalEconomicsLedger()
    key = _key("internal", "subscription_cancel", "sub_1")
    async with db.pool.acquire() as conn:
        assert await ledger.fetch_for_update(conn=conn, key=key, tenant=db.tenant, project=db.project) is None
        await _claim(ledger, conn, db, key)
        async with conn.transaction():
            row = await ledger.fetch_for_update(conn=conn, key=key, tenant=db.tenant, project=db.project)
    assert row is not None and row["status"] == "pending"


@pytest.mark.parametrize("codec", [True, False])
async def test_metadata_is_stored_as_a_json_object_on_both_pool_flavours(econ_pg, codec):
    db = econ_pg
    pool = db.pool if codec else await db.pool_without_codec()
    ledger = ExternalEconomicsLedger()
    key = _key(external_id=f"pi_codec_{codec}")
    async with pool.acquire() as conn:
        await _claim(ledger, conn, db, key, metadata={"payment_intent_id": "pi_x"})
        await ledger.merge_metadata(conn=conn, key=key, tenant=db.tenant, project=db.project,
                                    metadata={"stripe_refund_id": "re_1"})
    kind = await db.pool.fetchval(
        f"SELECT jsonb_typeof(metadata) FROM {db.events_table} WHERE external_id=$1", key.external_id)
    assert kind == "object"
    row = await db.event_row(*key)
    assert row["metadata"] == {"payment_intent_id": "pi_x", "stripe_refund_id": "re_1"}


async def test_list_by_status_filters_by_source_status_and_kind_oldest_first(econ_pg):
    db = econ_pg
    ledger = ExternalEconomicsLedger(pg_pool=db.pool)
    async with db.pool.acquire() as conn:
        for i, kind in enumerate(("wallet_refund", "subscription_cancel", "wallet_refund")):
            await _claim(ledger, conn, db, _key("internal", kind, f"r{i}"))
        await _claim(ledger, conn, db, _key("stripe", "wallet_refund", "s0"))
        await ledger.mark_applied(conn=conn, key=_key("internal", "wallet_refund", "r2"),
                                  tenant=db.tenant, project=db.project)
    rows = await ledger.list_by_status(tenant=db.tenant, project=db.project, source="internal")
    assert [r["external_id"] for r in rows] == ["r0", "r1"]
    rows = await ledger.list_by_status(tenant=db.tenant, project=db.project, source="internal",
                                       kinds=("wallet_refund",))
    assert [r["external_id"] for r in rows] == ["r0"]


async def test_claim_lock_blocks_a_concurrent_claimant_until_commit(econ_pg):
    db = econ_pg
    ledger = ExternalEconomicsLedger()
    key = _key()
    order = []
    first_holds = asyncio.Event()

    async def first():
        async with db.pool.acquire() as conn, conn.transaction():
            await _claim(ledger, conn, db, key)
            first_holds.set()
            await asyncio.sleep(0.3)
            await ledger.mark_applied(conn=conn, key=key, tenant=db.tenant, project=db.project)
            order.append("first-commit")

    async def second():
        await first_holds.wait()
        async with db.pool.acquire() as conn, conn.transaction():
            claim = await _claim(ledger, conn, db, key)
            order.append("second-claimed")
            return claim

    _, second_claim = await asyncio.gather(first(), second())
    assert order == ["first-commit", "second-claimed"]
    assert second_claim.already_applied


async def _credit(conn, db, user_id: str, cents: int) -> None:
    await conn.execute(
        f"""INSERT INTO {db.schema}.user_lifetime_credits (tenant, project, user_id, purchased_cents)
            VALUES ($1,$2,$3,$4)
            ON CONFLICT (tenant, project, user_id)
            DO UPDATE SET purchased_cents = {db.schema}.user_lifetime_credits.purchased_cents + EXCLUDED.purchased_cents""",
        db.tenant, db.project, user_id, cents,
    )


async def _credits(db, user_id: str):
    return await db.pool.fetchval(
        f"SELECT purchased_cents FROM {db.schema}.user_lifetime_credits WHERE user_id=$1", user_id)


async def test_apply_once_commits_effect_and_mark_together_and_replays_are_no_ops(econ_pg):
    db = econ_pg
    ledger = ExternalEconomicsLedger()
    key = _key()

    async def deliver():
        async with db.pool.acquire() as conn, conn.transaction():
            async with ledger.apply_once(conn=conn, key=key, tenant=db.tenant, project=db.project,
                                         user_id="user-1", amount_cents=500) as claim:
                if claim.already_applied:
                    return "duplicate"
                await _credit(conn, db, "user-1", 500)
                return "applied"

    results = await asyncio.gather(deliver(), deliver())
    assert sorted(results) == ["applied", "duplicate"]
    assert await _credits(db, "user-1") == 500
    applied_at = (await db.event_row(*key))["applied_at"]
    assert await deliver() == "duplicate"
    assert (await db.event_row(*key))["applied_at"] == applied_at


async def test_apply_once_rolls_the_claim_back_with_a_failing_effect(econ_pg):
    """The failure mark rides the caller's transaction: when the caller
    re-raises, nothing persists, so the provider's retry starts clean."""
    db = econ_pg
    ledger = ExternalEconomicsLedger()
    key = _key()
    with pytest.raises(RuntimeError):
        async with db.pool.acquire() as conn, conn.transaction():
            async with ledger.apply_once(conn=conn, key=key, tenant=db.tenant, project=db.project,
                                         user_id="user-1", amount_cents=500):
                await _credit(conn, db, "user-1", 500)
                raise RuntimeError("effect failed")
    assert await db.event_row(*key) is None
    assert await _credits(db, "user-1") is None


async def test_apply_once_marks_failed_when_the_caller_keeps_its_transaction(econ_pg):
    db = econ_pg
    ledger = ExternalEconomicsLedger()
    key = _key()
    async with db.pool.acquire() as conn, conn.transaction():
        try:
            async with ledger.apply_once(conn=conn, key=key, tenant=db.tenant, project=db.project):
                raise RuntimeError("effect failed")
        except RuntimeError:
            pass
    row = await db.event_row(*key)
    assert row["status"] == "failed" and row["error"] == "effect failed"
    assert row["metadata"] == {}
