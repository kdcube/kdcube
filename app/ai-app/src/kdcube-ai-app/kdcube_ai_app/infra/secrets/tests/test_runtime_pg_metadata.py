# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Real-PG metadata fences, not AWS/service/deployment qualification."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import replace

import pytest
import pytest_asyncio

from kdcube_ai_app.infra.secrets.runtime_pg_metadata import (
    PostgresRuntimeCustodyMetadata, RuntimeCleanupClaim,
)
from kdcube_ai_app.infra.secrets.runtime_pg_schema import (
    RuntimeMetadataError, create_runtime_metadata_schema, metadata_tables,
)

NS = "resident-secrets"
DIGEST = hashlib.sha256(b"synthetic-original-value").hexdigest()


def adapter(pool, schema, namespace=NS):
    return PostgresRuntimeCustodyMetadata(
        pool, schema=schema, namespace=namespace,
        authorized_namespaces=(NS, "other-runtime"), cloud_prefix="test/runtime",
    )


@pytest_asyncio.fixture
async def metadata():
    dsn = os.environ.get("KDCUBE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("KDCUBE_TEST_POSTGRES_DSN is not set")
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
    schema = "w585_metadata_" + uuid.uuid4().hex
    try:
        await create_runtime_metadata_schema(pool, schema=schema)
        await create_runtime_metadata_schema(pool, schema=schema)
        yield adapter(pool, schema)
    finally:
        async with pool.acquire() as connection:
            await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await pool.close()


async def reserve(metadata, *, secret_ref=None, digest=DIGEST, expires_at=None):
    return await metadata.reserve(
        secret_ref=secret_ref or uuid.uuid4().hex, request_digest=digest,
        expires_at=expires_at if expires_at is not None else int(time.time()) + 600,
    )


def arn(original, suffix="Ab1234"):
    return f"arn:aws:secretsmanager:eu-central-1:123456789012:secret:{original.secret_name}-{suffix}"


async def publish(metadata, original, **changes):
    args = dict(secret_ref=original.secret_ref, incarnation=original.incarnation,
                request_digest=original.request_digest, arn=arn(original),
                version_id=original.creation_token)
    args.update(changes)
    return await metadata.publish_original(**args)


async def expire(metadata, original):
    # Fixture-only time travel; production uses the database server's clock.
    async with metadata._pool.acquire() as connection:
        await connection.execute(
            f"UPDATE {metadata._records} SET expires_at = 1 WHERE secret_ref = $1",
            original.secret_ref,
        )


async def cleanup_rows(metadata):
    async with metadata._pool.acquire() as connection:
        return await connection.fetch(f"SELECT * FROM {metadata._cleanup} ORDER BY job_id")


@pytest.mark.asyncio
async def test_reservation_is_value_free_and_collision_preserves_every_original_coordinate(metadata):
    original = await reserve(metadata)
    same = await reserve(metadata, secret_ref=original.secret_ref,
                         expires_at=original.expires_at)
    different = await reserve(metadata, secret_ref=original.secret_ref,
                              digest="b" * 64, expires_at=original.expires_at + 1000)
    assert original.created and not same.created and not different.created
    assert same == different == original
    assert original.state == "reserved" and original.arn is None and original.version_id is None
    assert original.secret_name.endswith(f"/{original.secret_ref}/{original.incarnation}")
    async with metadata._pool.acquire() as connection:
        serialized = await connection.fetchval(f"SELECT row_to_json(r)::text FROM {metadata._records} r")
    assert "synthetic-original-value" not in serialized
    assert DIGEST in serialized


@pytest.mark.asyncio
async def test_concurrent_reservers_have_one_original_and_one_insert_owner(metadata):
    ref, deadline = uuid.uuid4().hex, int(time.time()) + 600
    contenders = await asyncio.gather(*[
        reserve(metadata, secret_ref=ref, expires_at=deadline) for _ in range(8)
    ])
    assert sum(row.created for row in contenders) == 1
    assert all(row == contenders[0] for row in contenders)


@pytest.mark.asyncio
async def test_fresh_pool_recovers_reservation_token_and_deadline_without_cloud_absence_claim(metadata):
    import asyncpg

    original = await reserve(metadata)
    pool = await asyncpg.create_pool(os.environ["KDCUBE_TEST_POSTGRES_DSN"], min_size=1, max_size=2)
    try:
        fresh = adapter(pool, metadata._records.split('"')[1])
        recovered = await reserve(fresh, secret_ref=original.secret_ref,
                                  expires_at=original.expires_at)
        assert recovered == original and not recovered.created
        with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_unavailable$"):
            await fresh.read_active(secret_ref=original.secret_ref)
    finally:
        await pool.close()


@pytest.mark.parametrize("phase", ["reserved", "active"])
@pytest.mark.asyncio
async def test_committed_original_survives_child_sigkill_before_response(metadata, phase):
    """Process-loss proof for metadata only; no cloud create happens here."""
    ref, deadline = uuid.uuid4().hex, int(time.time()) + 600
    child_env = dict(os.environ, W585_METADATA_SCHEMA=metadata._records.split('"')[1],
                     W585_METADATA_REF=ref, W585_METADATA_DEADLINE=str(deadline),
                     W585_METADATA_PHASE=phase)
    program = """
import asyncio, hashlib, json, os, signal
import asyncpg
from dataclasses import asdict
from kdcube_ai_app.infra.secrets.runtime_pg_metadata import PostgresRuntimeCustodyMetadata
async def run():
    pool = await asyncpg.create_pool(os.environ['KDCUBE_TEST_POSTGRES_DSN'], min_size=1, max_size=1)
    metadata = PostgresRuntimeCustodyMetadata(pool, schema=os.environ['W585_METADATA_SCHEMA'],
        namespace='resident-secrets', authorized_namespaces=('resident-secrets',), cloud_prefix='test/runtime')
    original = await metadata.reserve(secret_ref=os.environ['W585_METADATA_REF'],
        request_digest=hashlib.sha256(b'synthetic-original-value').hexdigest(),
        expires_at=int(os.environ['W585_METADATA_DEADLINE']))
    if os.environ['W585_METADATA_PHASE'] == 'active':
        await metadata.publish_original(secret_ref=original.secret_ref, incarnation=original.incarnation,
            request_digest=original.request_digest, version_id=original.creation_token,
            arn='arn:aws:secretsmanager:eu-central-1:123456789012:secret:'+original.secret_name+'-Ab1234')
        original = await metadata.read_active(secret_ref=original.secret_ref)
    print(json.dumps(asdict(original)), flush=True)
    os.kill(os.getpid(), signal.SIGKILL)
asyncio.run(run())
"""
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", program], env=child_env,
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == -signal.SIGKILL
    original = json.loads(result.stdout)
    recovered = await reserve(metadata, secret_ref=ref, expires_at=deadline)
    assert not recovered.created and recovered.state == phase
    assert recovered.incarnation == original["incarnation"]
    assert recovered.creation_token == original["creation_token"]
    assert recovered.request_digest == original["request_digest"]
    assert recovered.expires_at == deadline
    if phase == "reserved":
        with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_unavailable$"):
            await metadata.read_active(secret_ref=ref)
        assert await publish(metadata, recovered)
    active = await metadata.read_active(secret_ref=ref)
    assert active.arn == arn(recovered) and active.version_id == recovered.creation_token


@pytest.mark.asyncio
async def test_active_read_pins_original_full_arn_version_and_ignores_insert_telemetry(metadata):
    original = await reserve(metadata)
    assert await publish(metadata, original)
    assert await publish(metadata, original)
    active = await metadata.read_active(secret_ref=original.secret_ref)
    assert active.state == "active"
    assert (active.arn, active.version_id) == (arn(original), original.creation_token)
    assert active.expires_at == original.expires_at
    assert await metadata.confirms_active(replace(active, created=True))
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_binding_invalid$"):
        await publish(metadata, original, arn=arn(original, "Xy9876"))
    assert await metadata.read_active(secret_ref=original.secret_ref) == active


@pytest.mark.parametrize("field", ["incarnation", "request_digest", "version_id", "arn"])
@pytest.mark.asyncio
async def test_invalid_original_pin_cannot_activate_or_queue_foreign_resource(metadata, field):
    original = await reserve(metadata)
    wrong = {"incarnation": "0" * 32, "request_digest": "0" * 64,
             "version_id": "0" * 32,
             "arn": "arn:aws:secretsmanager:eu-central-1:123456789012:secret:other-Ab1234"}
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_binding_invalid$"):
        await publish(metadata, original, **{field: wrong[field]})
    recovered = await reserve(metadata, secret_ref=original.secret_ref)
    assert recovered.state == "reserved" and recovered.arn is None
    assert await cleanup_rows(metadata) == []


@pytest.mark.asyncio
async def test_terminal_fence_before_late_create_never_resurrects_and_keeps_unknown_cleanup(metadata):
    original = await reserve(metadata)
    await metadata.retire(secret_ref=original.secret_ref, incarnation=original.incarnation)
    assert not await publish(metadata, original)
    assert not await publish(metadata, original, arn=arn(original, "Xy9876"))
    assert await metadata.read_active(secret_ref=original.secret_ref) is None
    collision = await reserve(metadata, secret_ref=original.secret_ref,
                              digest="b" * 64, expires_at=original.expires_at + 1000)
    assert collision.state == "terminal" and collision.incarnation == original.incarnation
    assert collision.expires_at == original.expires_at and not collision.created
    rows = await cleanup_rows(metadata)
    assert len(rows) == 3  # Unresolved create plus both distinct full ARNs.
    assert {row["arn"] for row in rows} == {None, arn(original), arn(original, "Xy9876")}
    assert all(row["incarnation"] == original.incarnation for row in rows)
    await metadata.retire(secret_ref=original.secret_ref, incarnation=original.incarnation)
    assert len(await cleanup_rows(metadata)) == 3


@pytest.mark.asyncio
async def test_expired_create_ack_is_only_cleanup_and_read_recheck_fails_after_retirement(metadata):
    original = await reserve(metadata)
    await expire(metadata, original)
    assert not await publish(metadata, original)
    assert await metadata.read_active(secret_ref=original.secret_ref) is None
    assert len(await cleanup_rows(metadata)) == 2
    live = await reserve(metadata)
    await publish(metadata, live)
    active = await metadata.read_active(secret_ref=live.secret_ref)
    await metadata.retire(secret_ref=live.secret_ref, incarnation=live.incarnation)
    assert not await metadata.confirms_active(active)


@pytest.mark.asyncio
async def test_read_recheck_rejects_expiry_changed_pins_and_foreign_namespace(metadata):
    original = await reserve(metadata)
    await publish(metadata, original)
    active = await metadata.read_active(secret_ref=original.secret_ref)
    assert not await metadata.confirms_active(replace(active, arn=arn(original, "Xy9876")))
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_binding_invalid$"):
        await metadata.confirms_active(replace(active, namespace="other-runtime"))
    await expire(metadata, original)
    assert not await metadata.confirms_active(active)


@pytest.mark.asyncio
async def test_missing_read_is_absence_but_missing_or_wrong_incarnation_retire_is_refused(metadata):
    ref = uuid.uuid4().hex
    assert await metadata.read_active(secret_ref=ref) is None
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_binding_invalid$"):
        await metadata.retire(secret_ref=ref, incarnation=uuid.uuid4().hex)
    original = await reserve(metadata)
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_binding_invalid$"):
        await metadata.retire(secret_ref=original.secret_ref, incarnation=uuid.uuid4().hex)
    assert (await reserve(metadata, secret_ref=original.secret_ref)).state == "reserved"


@pytest.mark.asyncio
async def test_bounded_purge_atomically_retires_expired_rows_with_durable_cleanup(metadata):
    expired = [await reserve(metadata) for _ in range(2)]
    for row in expired:
        await publish(metadata, row)
        await expire(metadata, row)
    live = await reserve(metadata)
    await publish(metadata, live)
    now = int(time.time())
    assert await metadata.retire_expired(now=now, limit=1) == 1
    assert len(await cleanup_rows(metadata)) == 2
    assert (await metadata.read_active(secret_ref=live.secret_ref)).state == "active"
    assert await metadata.retire_expired(now=now, limit=1) == 1
    assert len(await cleanup_rows(metadata)) == 4
    assert await metadata.retire_expired(now=now, limit=1) == 0


@pytest.mark.asyncio
async def test_cleanup_enqueue_failure_rolls_back_terminal_retirement(metadata, monkeypatch):
    original = await reserve(metadata)
    await publish(metadata, original)
    active = await metadata.read_active(secret_ref=original.secret_ref)

    async def fail_enqueue(*args, **kwargs):
        raise RuntimeError("synthetic-database-canary-do-not-expose")

    monkeypatch.setattr(metadata, "_enqueue", fail_enqueue)
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_unavailable$"):
        await metadata.retire(secret_ref=original.secret_ref, incarnation=original.incarnation)
    assert await metadata.read_active(secret_ref=original.secret_ref) == active
    assert await cleanup_rows(metadata) == []


@pytest.mark.asyncio
async def test_purge_batch_failure_rolls_back_all_retirements_and_cleanup(metadata, monkeypatch):
    originals = [await reserve(metadata) for _ in range(2)]
    for original in originals:
        await expire(metadata, original)
    enqueue, calls = metadata._enqueue, 0

    async def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic-second-job-failure")
        await enqueue(*args, **kwargs)

    monkeypatch.setattr(metadata, "_enqueue", fail_second)
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_unavailable$"):
        await metadata.retire_expired(now=int(time.time()), limit=2)
    async with metadata._pool.acquire() as connection:
        states = await connection.fetch(f"SELECT state FROM {metadata._records}")
    assert [row["state"] for row in states] == ["reserved", "reserved"]
    assert await cleanup_rows(metadata) == []


@pytest.mark.asyncio
async def test_racing_publish_and_retire_finish_terminal_with_exact_cleanup(metadata):
    original = await reserve(metadata)
    await asyncio.gather(
        publish(metadata, original),
        metadata.retire(secret_ref=original.secret_ref, incarnation=original.incarnation),
    )
    assert await metadata.read_active(secret_ref=original.secret_ref) is None
    assert {row["arn"] for row in await cleanup_rows(metadata)} == {None, arn(original)}


@pytest.mark.asyncio
async def test_purge_future_time_is_refused_before_mutation(metadata):
    original = await reserve(metadata)
    await expire(metadata, original)
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_purge_invalid$"):
        await metadata.retire_expired(now=int(time.time()) + 600, limit=1)
    assert (await reserve(metadata, secret_ref=original.secret_ref)).state == "reserved"
    assert await cleanup_rows(metadata) == []


@pytest.mark.asyncio
async def test_cleanup_claims_are_bounded_exclusive_and_expired_tokens_cannot_settle(metadata):
    original = await reserve(metadata)
    await publish(metadata, original)
    await metadata.retire(secret_ref=original.secret_ref, incarnation=original.incarnation)
    first, second = await asyncio.gather(metadata.claim_cleanup(limit=1), metadata.claim_cleanup(limit=1))
    assert len(first) == len(second) == 1 and first[0].job_id != second[0].job_id
    assert await metadata.claim_cleanup(limit=1000) == ()
    old = first[0]
    async with metadata._pool.acquire() as connection:
        await connection.execute(
            f"UPDATE {metadata._cleanup} SET claim_until = clock_timestamp() - interval '1 second' "
            "WHERE job_id = $1", old.job_id,
        )
    assert not await metadata.settle_cleanup(old, outcome="retry")
    fresh, = await metadata.claim_cleanup(limit=1)
    assert fresh.job_id == old.job_id and fresh.claim_token != old.claim_token
    assert not await metadata.settle_cleanup(old, outcome="retry")
    assert await metadata.settle_cleanup(fresh, outcome="retry")
    assert not await metadata.settle_cleanup(fresh, outcome="retry")


@pytest.mark.asyncio
async def test_delete_accepted_is_not_physical_completion_and_unknown_create_cannot_disappear(metadata):
    original = await reserve(metadata)
    await publish(metadata, original)
    await metadata.retire(secret_ref=original.secret_ref, incarnation=original.incarnation)
    claims = await metadata.claim_cleanup(limit=2)
    unknown = next(claim for claim in claims if claim.phase == "reconcile")
    deletion = next(claim for claim in claims if claim.phase == "delete")
    for outcome in ("delete_accepted", "deleted"):
        with pytest.raises(RuntimeMetadataError, match="^runtime_secret_cleanup_binding_invalid$"):
            await metadata.settle_cleanup(unknown, outcome=outcome)
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_cleanup_binding_invalid$"):
        await metadata.settle_cleanup(deletion, outcome="deleted")
    assert await metadata.settle_cleanup(deletion, outcome="delete_accepted")
    confirmation, = await metadata.claim_cleanup(limit=1)
    assert confirmation.phase == "confirm" and confirmation.arn == arn(original)
    assert await metadata.settle_cleanup(confirmation, outcome="retry")
    confirmation, = await metadata.claim_cleanup(limit=1)
    assert confirmation.phase == "confirm"
    assert await metadata.settle_cleanup(confirmation, outcome="deleted")
    assert await metadata.settle_cleanup(unknown, outcome="retry")
    remaining, = await metadata.claim_cleanup(limit=1000)
    assert remaining.phase == "reconcile" and remaining.arn is None
    rows = await cleanup_rows(metadata)
    assert {row["state"] for row in rows} == {"claimed", "deleted"}


@pytest.mark.parametrize("field,value", [
    ("job_id", "0" * 64), ("secret_ref", "0" * 32),
    ("incarnation", "0" * 32), ("claim_token", "0" * 32),
    ("arn", "arn:aws:secretsmanager:eu-central-1:123456789012:secret:foreign-Ab1234"),
    ("version_id", "0" * 32),
])
@pytest.mark.asyncio
async def test_forged_cleanup_coordinates_cannot_settle_an_owned_claim(metadata, field, value):
    original = await reserve(metadata)
    await publish(metadata, original)
    await metadata.retire(secret_ref=original.secret_ref, incarnation=original.incarnation)
    claim = next(row for row in await metadata.claim_cleanup(limit=2) if row.phase == "delete")
    assert not await metadata.settle_cleanup(replace(claim, **{field: value}), outcome="retry")
    assert await metadata.settle_cleanup(claim, outcome="retry")


@pytest.mark.asyncio
async def test_namespaces_cannot_read_or_retire_each_others_records(metadata):
    other = adapter(metadata._pool, metadata._records.split('"')[1], "other-runtime")
    original = await reserve(metadata)
    assert await other.read_active(secret_ref=original.secret_ref) is None
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_binding_invalid$"):
        await other.retire(secret_ref=original.secret_ref, incarnation=original.incarnation)
    await metadata.retire(secret_ref=original.secret_ref, incarnation=original.incarnation)
    assert await other.claim_cleanup(limit=1000) == ()
    claim, = await metadata.claim_cleanup(limit=1)
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_cleanup_binding_invalid$"):
        await other.settle_cleanup(claim, outcome="retry")


@pytest.mark.parametrize("operation,args", [
    ("reserve", {"secret_ref": "bad", "request_digest": DIGEST, "expires_at": 9}),
    ("reserve", {"secret_ref": "a" * 32, "request_digest": [], "expires_at": 9}),
    ("reserve", {"secret_ref": "a" * 32, "request_digest": DIGEST, "expires_at": True}),
    ("read_active", {"secret_ref": []}),
    ("retire_expired", {"now": True, "limit": 1}),
    ("retire_expired", {"now": 1, "limit": 0}),
    ("retire_expired", {"now": 1, "limit": 1001}),
    ("claim_cleanup", {"limit": True}),
    ("claim_cleanup", {"limit": 1, "lease_seconds": 301}),
])
@pytest.mark.asyncio
async def test_malformed_operations_refuse_before_database_io(operation, args):
    service = adapter(ForbiddenPool(), "test_metadata")
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_binding_invalid$"):
        await getattr(service, operation)(**args)
    assert not service._pool.used


@pytest.mark.parametrize("outcome", [[], None, True, "unknown"])
@pytest.mark.asyncio
async def test_invalid_cleanup_outcome_is_a_finite_refusal_before_io(outcome):
    service = adapter(ForbiddenPool(), "test_metadata")
    claim = RuntimeCleanupClaim(NS, "a" * 64, "a" * 32, "b" * 32, None, None,
                                "reconcile", "c" * 32)
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_cleanup_binding_invalid$"):
        await service.settle_cleanup(claim, outcome=outcome)
    assert not service._pool.used


class ForbiddenPool:
    used = False

    def acquire(self):
        self.used = True
        raise RuntimeError("synthetic-database-canary-do-not-expose")


@pytest.mark.parametrize("changes,reason", [
    ({"schema": "invalid; DROP SCHEMA public"}, "configuration_invalid"),
    ({"namespace": "unlisted"}, "scope_forbidden"),
    ({"authorized_namespaces": ()}, "scope_forbidden"),
    ({"authorized_namespaces": NS}, "scope_forbidden"),
    ({"cloud_prefix": "/test"}, "configuration_invalid"),
    ({"cloud_prefix": "test/"}, "configuration_invalid"),
    ({"cloud_prefix": "test%runtime"}, "configuration_invalid"),
])
def test_invalid_host_configuration_has_no_database_io(changes, reason):
    pool = ForbiddenPool()
    args = dict(schema="test_metadata", namespace=NS, authorized_namespaces=(NS,),
                cloud_prefix="test/runtime")
    args.update(changes)
    with pytest.raises(RuntimeMetadataError, match=f"^runtime_secret_metadata_{reason}$" if
                       reason == "configuration_invalid" else "^runtime_secret_scope_forbidden$"):
        PostgresRuntimeCustodyMetadata(pool, **args)
    assert not pool.used


@pytest.mark.asyncio
async def test_database_failure_does_not_become_absence_or_expose_driver_text():
    service = adapter(ForbiddenPool(), "test_metadata")
    for operation, args in (("read_active", {"secret_ref": "a" * 32}),
                            ("reserve", {"secret_ref": "a" * 32, "request_digest": DIGEST,
                                         "expires_at": int(time.time()) + 600})):
        with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_unavailable$") as error:
            await getattr(service, operation)(**args)
        assert error.value.__suppress_context__
        assert "canary" not in str(error.value)
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_unavailable$"):
        await create_runtime_metadata_schema(ForbiddenPool(), schema="test_metadata")


@pytest.mark.asyncio
async def test_expired_new_reference_does_not_allocate_metadata(metadata):
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_expired$"):
        await reserve(metadata, expires_at=1)
    async with metadata._pool.acquire() as connection:
        assert await connection.fetchval(f"SELECT count(*) FROM {metadata._records}") == 0


@pytest.mark.asyncio
async def test_service_role_needs_dml_only_not_ddl_or_delete(metadata):
    """Disposable role fixture; does not attest a deployed pool or DB role."""
    import asyncpg

    role = "w585_role_" + uuid.uuid4().hex
    schema = metadata._records.split('"')[1]
    service_pool = None
    try:
        async with metadata._pool.acquire() as connection:
            await connection.execute(f'CREATE ROLE "{role}"')
            await connection.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
            await connection.execute(
                f'GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"',
            )
        service_pool = await asyncpg.create_pool(
            os.environ["KDCUBE_TEST_POSTGRES_DSN"], min_size=1, max_size=2,
            server_settings={"role": role},
        )
        service = adapter(service_pool, schema)
        original = await reserve(service)
        assert await publish(service, original)
        assert (await service.read_active(secret_ref=original.secret_ref)).arn == arn(original)
        await service.retire(secret_ref=original.secret_ref, incarnation=original.incarnation)
        claims = await service.claim_cleanup(limit=2)
        assert len(claims) == 2
        assert all([await service.settle_cleanup(claim, outcome="retry") for claim in claims])
        async with service_pool.acquire() as connection:
            for sql in (f"DELETE FROM {service._records}",
                        f'CREATE TABLE "{schema}".forbidden_ddl (id integer)'):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.execute(sql)
    finally:
        if service_pool is not None:
            await service_pool.close()
        async with metadata._pool.acquire() as connection:
            await connection.execute(f'DROP OWNED BY "{role}"')
            await connection.execute(f'DROP ROLE "{role}"')


def test_schema_names_are_quoted_and_do_not_use_card_or_session_tables():
    assert metadata_tables("runtime_test") == ('"runtime_test".runtime_secret_records',
                                               '"runtime_test".runtime_secret_cleanup')
