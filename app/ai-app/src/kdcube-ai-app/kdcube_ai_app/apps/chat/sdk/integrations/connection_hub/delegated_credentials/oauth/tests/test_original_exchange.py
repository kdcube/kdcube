"""Durable exchange-to-plan mapping; real PostgreSQL, synthetic Hub plan shape."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

from connection_hub.delegated_credentials.oauth.pkce import make_s256_challenge
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import store
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import (
    CodeExchangeProof, OriginalExchangeRefused, ValidatedCodeExchange,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange_store import (
    PostgresOriginalExchangeStore, TABLE_EXCHANGES,
)

VERIFIER = "unit-verifier-" + "x" * 48
CODE = "synthetic-authorization-code"


def proof(store, **changes):
    return CodeExchangeProof.from_request(**{
        "tenant": store.tenant, "project": store.project, "code": CODE,
        "client_id": "unit-client", "redirect_uri": "https://unit.test/callback",
        "verifier": VERIFIER, **changes,
    })


def validated(store, **changes):
    payload = {
        "client_id": "unit-client", "redirect_uri": "https://unit.test/callback",
        "code_challenge": make_s256_challenge(VERIFIER), "sub": "human",
        "scopes": ["records:read"], "expected_card_revision": 1,
        "payload_only_marker": "unit-consumed-payload-marker",
    }
    payload.update(changes)
    return ValidatedCodeExchange.from_consumed(
        proof(store), payload, decision_scope="connection-hub.card:oauth-issuance",
        original_input_digest="c" * 64,
    )


def plan(binding, **changes):
    now = int(time.time())
    values = {
        "transaction_id": "a" * 64, "decision_request_id": binding.decision_request_id,
        "intent_digest": "b" * 64, "original_input_digest": "c" * 64,
        "tenant": binding.proof.tenant, "project": binding.proof.project,
        "access_id": "unit-card", "grantor_subject": "human", "client_id": "unit-client",
        "credential_issuer": "unit-delegated-authority", "credential_subject": "integration:unit:human",
        "base_revision": 1, "candidate_revision": 2, "expires_at": now + 3600,
        "card_content_hash": "f" * 64, "operations": ["records.read"],
        "resource_grants": {"/records": ["records:read"]},
        "resource_operations": {"/records": ["records.read"]},
        "delivery_deadline": now + 590, "reserved_until": now + 300,
        "slots": ["access", "refresh"], "effect_digests": {"access": "d" * 64, "refresh": "e" * 64},
    }
    values.update(changes)
    return SimpleNamespace(**values, to_dict=lambda: copy.deepcopy(values))


@pytest_asyncio.fixture
async def ledger(store):
    value = PostgresOriginalExchangeStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    await value.ensure_schema()
    await value.ensure_schema()
    return value


@pytest.mark.asyncio
async def test_same_consumed_code_maps_to_same_plan_after_adapter_restart(store, ledger):
    binding = validated(store)
    original = await ledger.begin(binding)
    assert original.created and original.plan is None
    bound_plan = plan(binding)
    pinned = await ledger.pin_plan(binding, bound_plan)
    restarted = PostgresOriginalExchangeStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    recovered = await restarted.read(proof(store))
    assert recovered.plan == pinned.plan == bound_plan.to_dict()
    assert recovered.pending_until == original.pending_until
    assert recovered.delivery_deadline == bound_plan.delivery_deadline
    assert (await restarted.begin(binding)).created is False
    assert (await restarted.pin_plan(binding, bound_plan)).plan == recovered.plan


@pytest.mark.asyncio
async def test_noncredential_effects_are_pinned_in_full_and_cannot_change_on_retry(store, ledger):
    binding = validated(store)
    await ledger.begin(binding)
    effects = {"access": "d" * 64, "refresh": "e" * 64, "unit-policy": "0" * 64}
    bound_plan = plan(binding, effect_digests=effects)
    await ledger.pin_plan(binding, bound_plan)
    restarted = PostgresOriginalExchangeStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    assert (await restarted.read(proof(store))).plan == bound_plan.to_dict()
    assert (await restarted.pin_plan(binding, bound_plan)).plan == bound_plan.to_dict()
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_identity_conflict$"):
        await restarted.pin_plan(binding, plan(binding, effect_digests={**effects, "unit-policy": "1" * 64}))
    assert (await restarted.read(proof(store))).plan == bound_plan.to_dict()


@pytest.mark.asyncio
async def test_a_separate_interpreter_recovers_the_same_durable_plan(store, ledger):
    binding = validated(store)
    await ledger.begin(binding)
    first = await ledger.pin_plan(binding, plan(binding))
    # Request fixture values are imported in child memory, never arguments.
    # The child owns a new pool and no reference to this adapter or event loop.
    script = """
import asyncio, hashlib, json, os, sys
from types import SimpleNamespace
import asyncpg
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_original_exchange import proof
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import canonical
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange_store import PostgresOriginalExchangeStore
async def main():
    namespace = SimpleNamespace(**json.loads(sys.stdin.read()))
    pool = await asyncpg.create_pool(os.environ['KDCUBE_TEST_POSTGRES_DSN'], min_size=1, max_size=1)
    try:
        row = await PostgresOriginalExchangeStore(pg_pool=pool, tenant=namespace.tenant, project=namespace.project).read(proof(namespace))
        print(json.dumps({'identity': row.identity, 'plan_digest': hashlib.sha256(canonical(row.plan).encode()).hexdigest()}))
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
    recovered = json.loads(stdout)
    assert recovered == {"identity": first.identity, "plan_digest": hashlib.sha256(json.dumps(
        first.plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()}


@pytest.mark.asyncio
async def test_no_raw_code_verifier_or_payload_is_persisted(store, ledger):
    binding = validated(store)
    await ledger.begin(binding)
    await ledger.pin_plan(binding, plan(binding))
    async with store._pool.acquire() as connection:
        encoded = await connection.fetchval(f"SELECT row_to_json(e)::text FROM {ledger.schema}.{TABLE_EXCHANGES} e")
    assert CODE not in encoded and VERIFIER not in encoded
    assert "code_challenge" not in encoded and "unit-consumed-payload-marker" not in encoded
    assert hashlib.sha256(CODE.encode()).hexdigest() in encoded
    assert json.dumps(binding.proof.__dict__) not in encoded


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"client_id": "other-client"}, {"redirect_uri": "https://unit.test/other"},
    {"verifier": "wrong-verifier" + "z" * 48},
])
async def test_wrong_retry_proof_refuses_without_modifying_original(store, ledger, changes):
    binding = validated(store)
    await ledger.begin(binding)
    original = await ledger.pin_plan(binding, plan(binding))
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_proof_mismatch$"):
        await ledger.read(proof(store, **changes))
    assert (await ledger.read(proof(store))).plan == original.plan


@pytest.mark.asyncio
async def test_missing_code_mapping_cannot_be_manufactured_by_retry(store, ledger):
    assert await ledger.read(proof(store)) is None
    # Only the live consume-and-validate path can begin; possession of a retry
    # proof is not a first-exchange validation result.
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_binding_invalid$"):
        await ledger.begin(proof(store))


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"scopes": ["records:write"]}, {"expected_card_revision": 2}])
async def test_same_code_changed_server_payload_never_starts_a_new_exchange(store, ledger, changes):
    await ledger.begin(validated(store))
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_identity_conflict$"):
        await ledger.begin(validated(store, **changes))


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("tenant", "other-tenant"), ("project", "other-project"), ("client_id", "other-client"),
    ("grantor_subject", "other-human"), ("decision_request_id", "0" * 64),
    ("original_input_digest", "0" * 64),
])
async def test_plan_must_belong_to_original_validated_exchange(store, ledger, field, value):
    binding = validated(store)
    await ledger.begin(binding)
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_plan_mismatch$"):
        await ledger.pin_plan(binding, plan(binding, **{field: value}))
    assert (await ledger.read(proof(store))).plan is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("transaction_id", "0" * 64), ("intent_digest", "0" * 64), ("card_content_hash", "0" * 64),
    ("candidate_revision", 3), ("delivery_deadline", None), ("expires_at", None),
    ("operations", ["records.write"]), ("resource_grants", {"/records": ["records:write"]}),
    ("resource_operations", {"/records": ["records.write"]}),
])
async def test_pinned_plan_never_changes_on_retry(store, ledger, field, value):
    binding = validated(store)
    await ledger.begin(binding)
    first = plan(binding)
    await ledger.pin_plan(binding, first)
    altered = value if value is not None else getattr(first, field) + 1
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_identity_conflict$"):
        await ledger.pin_plan(binding, plan(binding, **{field: altered}))
    assert (await ledger.read(proof(store))).plan == first.to_dict()


@pytest.mark.asyncio
async def test_deadline_cannot_be_freshly_extended_when_binding_a_plan(store, ledger):
    binding = validated(store)
    first = await ledger.begin(binding)
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_deadline_invalid$"):
        candidate = plan(binding)
        await ledger.pin_plan(binding, plan(binding, delivery_deadline=candidate.expires_at + 1))
    assert (await ledger.begin(binding)).pending_until == first.pending_until


@pytest.mark.asyncio
async def test_expired_mapping_remains_a_tombstone_and_never_rebegins(store, ledger):
    binding = validated(store)
    await ledger.begin(binding)
    first = plan(binding, delivery_deadline=int(time.time()) + 2, reserved_until=int(time.time()) + 1)
    await ledger.pin_plan(binding, first)
    async with store._pool.acquire() as connection:
        await connection.execute(f"UPDATE {ledger.schema}.{TABLE_EXCHANGES} SET delivery_deadline = to_timestamp(1)")
    for operation in (lambda: ledger.read(proof(store)), lambda: ledger.begin(binding),
                      lambda: ledger.pin_plan(binding, first)):
        with pytest.raises(OriginalExchangeRefused, match="^original_exchange_delivery_expired$"):
            await operation()
    async with store._pool.acquire() as connection:
        assert await connection.fetchval(f"SELECT count(*) FROM {ledger.schema}.{TABLE_EXCHANGES}") == 1


@pytest.mark.asyncio
async def test_pool_saturation_concurrency_reuses_one_identity_without_nested_acquires(store, ledger):
    binding = validated(store)
    originals = await asyncio.wait_for(asyncio.gather(*(ledger.begin(binding) for _ in range(16))), 15)
    assert sum(row.created for row in originals) == 1
    assert len({row.pending_until for row in originals}) == 1
    first = plan(binding)
    pinned = await asyncio.wait_for(asyncio.gather(*(ledger.pin_plan(binding, first) for _ in range(16))), 15)
    assert all(row.plan == first.to_dict() for row in pinned)


@pytest.mark.asyncio
async def test_database_clock_after_real_row_lock_wait_closes_pending_window(store, ledger):
    binding = validated(store)
    await ledger.begin(binding)
    async with store._pool.acquire() as blocker:
        transaction = blocker.transaction()
        await transaction.start()
        try:
            await blocker.execute(f"UPDATE {ledger.schema}.{TABLE_EXCHANGES} SET pending_until = clock_timestamp() + interval '500 milliseconds'")
            replay = asyncio.create_task(ledger.begin(binding))
            # The competing begin must acquire this very row before checking
            # its lifetime. Holding it through expiry exercises the SQL clock.
            await blocker.execute("SELECT pg_sleep(0.7)")
            assert not replay.done()
        finally:
            await transaction.commit()
        with pytest.raises(OriginalExchangeRefused, match="^original_exchange_delivery_expired$"):
            await asyncio.wait_for(replay, 5)


@pytest.mark.asyncio
async def test_foreign_namespace_is_refused_before_database_access(store, ledger):
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_namespace_mismatch$"):
        await ledger.read(proof(store, tenant="other-tenant"))


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("operations", "records.read"),
    ("resource_grants", {"": ["records:read"]}),
    ("resource_operations", {"/records": ["records.read", "records.read"]}),
    ("slots", ["access", "unknown"]), ("effect_digests", {"access": "d" * 64}),
    ("effect_digests", {"access": "d" * 64, "refresh": "e" * 64, "": "0" * 64}),
    ("effect_digests", {"access": "d" * 64, "refresh": "e" * 64, "unit-policy": "not-a-digest"}),
    ("effect_digests", ["access", "refresh"]),
    ("base_revision", True), ("candidate_revision", 1),
])
async def test_missing_or_malformed_plan_authority_cannot_be_pinned(store, ledger, field, value):
    binding = validated(store)
    await ledger.begin(binding)
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_plan_invalid$"):
        await ledger.pin_plan(binding, plan(binding, **{field: value}))
    assert (await ledger.read(proof(store))).plan is None


@pytest.mark.asyncio
async def test_plan_cannot_be_bound_without_first_validated_mapping(store, ledger):
    binding = validated(store)
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_not_validated$"):
        await ledger.pin_plan(binding, plan(binding))


@pytest.mark.asyncio
async def test_expired_preparation_window_cannot_bind_new_plan(store, ledger):
    binding = validated(store)
    await ledger.begin(binding)
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_delivery_expired$"):
        await ledger.pin_plan(binding, plan(binding, reserved_until=1))
    assert (await ledger.read(proof(store))).plan is None


@pytest.mark.parametrize("changes", [
    {"client_id": "other-client"}, {"redirect_uri": "https://unit.test/other"},
    {"code_challenge": "wrong-challenge"}, {"sub": ""},
])
def test_first_exchange_requires_the_live_consumed_payload_proof(store_namespace, changes):
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_validation_failed$"):
        validated(store_namespace, **changes)


@pytest.fixture
def store_namespace():
    return SimpleNamespace(tenant="unit-tenant", project="unit-project")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["ensure_schema", "begin", "read", "pin_plan"])
async def test_pool_failure_is_finite_secret_free_and_not_a_rebegin(store_namespace, operation):
    class FailedPool:
        def acquire(self, *, timeout):
            assert timeout == 5
            raise OSError(CODE + " " + VERIFIER)

    binding = validated(store_namespace)
    ledger = PostgresOriginalExchangeStore(
        pg_pool=FailedPool(), tenant=store_namespace.tenant, project=store_namespace.project,
    )
    args = {"ensure_schema": (), "begin": (binding,), "read": (binding.proof,), "pin_plan": (binding, plan(binding))}
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_unavailable$") as error:
        await getattr(ledger, operation)(*args[operation])
    assert error.value.__suppress_context__ and error.value.__cause__ is None
    assert CODE not in str(error.value) and VERIFIER not in str(error.value)
