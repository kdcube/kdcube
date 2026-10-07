from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest
import pytest_asyncio

from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceRefused
from kdcube_ai_app.auth.bundle.session_schema import TABLE_ISSUANCES, TABLE_SESSIONS, TABLE_USERS
from kdcube_ai_app.auth.bundle.session_store import PostgresBundleSessionStore


@pytest_asyncio.fixture
async def store():
    dsn = os.environ.get("KDCUBE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("KDCUBE_TEST_POSTGRES_DSN is not set")
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
    authority = PostgresBundleSessionStore(
        pg_pool=pool, tenant="bound-issuance-test-" + uuid.uuid4().hex,
        project="bound-sessions",
    )
    try:
        await authority.ensure_schema()
        await authority.ensure_schema()
        yield authority
    finally:
        async with pool.acquire() as connection:
            await connection.execute(f"DROP SCHEMA {authority.schema} CASCADE")
        await pool.close()


def candidate(expires_at, *, sub="integration:unit:human"):
    now = int(time.time())
    sid = "bsn_" + uuid.uuid4().hex
    return sid, uuid.uuid4().hex, {
        "schema": "kdcube.session_token.v1", "session_id": sid, "sub": sub,
        "provider": "integration", "provider_subject": "human",
        "token_sha256": uuid.uuid4().hex * 2, "version": 1, "active": True,
        "metadata": {}, "iat": now, "exp": expires_at,
        "max_exp": expires_at, "last_seen": now,
    }


async def reserve(store, *, identity="a" * 64, digest="b" * 64, expires_at=None,
                  sub="integration:unit:human", permissions=("records:read",)):
    expiry = expires_at if expires_at is not None else int(time.time()) + 600
    sid, ref, record = candidate(expiry, sub=sub)
    profile = {
        "sub": sub, "roles": ["delegated-client"], "permissions": list(permissions),
        "provider": "integration", "disabled": False,
        "created_at": record["iat"], "updated_at": record["iat"],
    }
    return await store.reserve_issuance(
        identity, digest, sid, ref, expiry, session_record=record,
        expected_version=1, user_record=profile,
    )


async def counts(store):
    async with store._pool.acquire() as connection:
        return tuple([await connection.fetchval(f"SELECT count(*) FROM {store.schema}.{table}")
                      for table in (TABLE_USERS, TABLE_ISSUANCES, TABLE_SESSIONS)])


@pytest.mark.asyncio
async def test_reservation_provisions_user_without_activating_session(store):
    original = await reserve(store)
    assert original.created and original.state == "reserved"
    assert original.expected_user_revision == 1
    assert await counts(store) == (1, 1, 0)
    assert (await store.get_user(original.record["sub"]))["permissions"] == []
    row = await store.read_issuance(original.identity)
    assert row.session_id == original.session_id
    assert row.secret_ref == original.secret_ref
    assert row.record == original.record


@pytest.mark.asyncio
async def test_authority_changes_only_on_first_activation_not_recovery(store):
    original = await reserve(store)
    await store.activate_reserved(original.identity)
    assert (await store.get_user(original.record["sub"]))["permissions"] == ["records:read"]
    await store.register_user(
        sub=original.record["sub"], updates={"permissions": ["newer:grant"]},
        now=int(time.time()),
    )
    await store.activate_reserved(original.identity)
    assert (await store.get_user(original.record["sub"]))["permissions"] == ["newer:grant"]


@pytest.mark.asyncio
async def test_pending_old_reservation_cannot_overwrite_newer_activated_grant(store):
    old = await reserve(store, permissions=("old:read",))
    newer = await reserve(store, identity="d" * 64, permissions=("new:read", "new:write"))
    await store.activate_reserved(newer.identity)
    newer_profile = await store.get_user(newer.record["sub"])
    newer_revision = (await store.get_login_state(newer.record["sub"])).user_revision
    with pytest.raises(SessionIssuanceRefused, match="^issuance_authority_moved$"):
        await store.activate_reserved(old.identity)
    assert (await store.read_issuance(old.identity)).state == "reserved"
    assert await store.get_user(newer.record["sub"]) == newer_profile
    assert (await store.get_login_state(newer.record["sub"])).user_revision == newer_revision
    assert await counts(store) == (1, 2, 1)


@pytest.mark.asyncio
async def test_pending_reservation_refuses_changed_grants_without_epoch_change(store):
    original = await reserve(store)
    before = await store.get_login_state(original.record["sub"])
    await store.register_user(
        sub=original.record["sub"], updates={"roles": [], "permissions": ["newer:grant"]},
        now=int(time.time()),
    )
    after = await store.get_login_state(original.record["sub"])
    assert before.version == after.version
    assert before.user_revision < after.user_revision
    with pytest.raises(SessionIssuanceRefused, match="^issuance_authority_moved$"):
        await store.activate_reserved(original.identity)
    assert (await store.get_user(original.record["sub"]))["permissions"] == ["newer:grant"]
    assert await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_legacy_pending_reservation_with_unknown_revision_refuses_activation(store):
    original = await reserve(store)
    async with store._pool.acquire() as connection:
        await connection.execute(
            f"UPDATE {store.schema}.{TABLE_ISSUANCES} SET expected_user_revision = NULL WHERE identity = $1",
            original.identity,
        )
    with pytest.raises(SessionIssuanceRefused, match="^issuance_authority_moved$"):
        await store.activate_reserved(original.identity)
    assert await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_legacy_active_reservation_replays_without_restoring_older_grants(store):
    original = await reserve(store)
    await store.activate_reserved(original.identity)
    async with store._pool.acquire() as connection:
        await connection.execute(
            f"UPDATE {store.schema}.{TABLE_ISSUANCES} SET expected_user_revision = NULL WHERE identity = $1",
            original.identity,
        )
    await store.register_user(
        sub=original.record["sub"], updates={"permissions": ["newer:grant"]}, now=int(time.time()),
    )
    recovered = await store.activate_reserved(original.identity)
    assert recovered.session_id == original.session_id
    assert recovered.expected_user_revision is None
    assert (await store.get_user(original.record["sub"]))["permissions"] == ["newer:grant"]
    assert await counts(store) == (1, 1, 1)


@pytest.mark.asyncio
async def test_additive_revision_migration_preserves_active_replay_and_refuses_pending(store):
    active = await reserve(store)
    await store.activate_reserved(active.identity)
    pending = await reserve(store, identity="d" * 64, permissions=("old:read",))
    await store.register_user(
        sub=active.record["sub"], updates={"permissions": ["newer:grant"]}, now=int(time.time()),
    )
    async with store._pool.acquire() as connection:
        await connection.execute(
            f"ALTER TABLE {store.schema}.{TABLE_ISSUANCES} DROP COLUMN expected_user_revision",
        )
    await store.ensure_schema()
    await store.ensure_schema()
    assert (await store.read_issuance(pending.identity)).expected_user_revision is None
    with pytest.raises(SessionIssuanceRefused, match="^issuance_authority_moved$"):
        await store.activate_reserved(pending.identity)
    recovered = await store.activate_reserved(active.identity)
    assert recovered.session_id == active.session_id
    assert recovered.expected_user_revision is None
    assert (await store.get_user(active.record["sub"]))["permissions"] == ["newer:grant"]
    assert (await store.read_issuance(pending.identity)).state == "reserved"
    assert await counts(store) == (1, 2, 1)


@pytest.mark.asyncio
async def test_identical_retry_after_lost_activation_response_has_one_original_session(store):
    original = await reserve(store)
    await store.activate_reserved(original.identity)  # committed; response may be lost
    fresh = PostgresBundleSessionStore(
        pg_pool=store._pool, tenant=store.tenant, project=store.project,
    )
    retried = await reserve(fresh, expires_at=original.expires_at)
    recovered = await fresh.activate_reserved(original.identity)
    assert not retried.created
    assert recovered.session_id == original.session_id
    assert recovered.secret_ref == original.secret_ref
    assert recovered.record["token_sha256"] == original.record["token_sha256"]
    assert await counts(store) == (1, 1, 1)


@pytest.mark.asyncio
async def test_concurrent_same_identity_reservations_keep_one_winner(store):
    expiry = int(time.time()) + 600
    reservations = await asyncio.gather(*[reserve(store, expires_at=expiry) for _ in range(8)])
    assert sum(row.created for row in reservations) == 1
    assert len({row.session_id for row in reservations}) == 1
    assert len({row.secret_ref for row in reservations}) == 1
    activations = await asyncio.gather(*[store.activate_reserved(row.identity) for row in reservations])
    assert len({row.record["token_sha256"] for row in activations}) == 1
    assert await counts(store) == (1, 1, 1)


@pytest.mark.asyncio
async def test_conflicting_identity_has_no_user_or_session_side_effect(store):
    original = await reserve(store)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await reserve(store, digest="c" * 64, expires_at=original.expires_at,
                      sub="integration:other:human")
    assert await counts(store) == (1, 1, 0)
    assert (await store.read_issuance(original.identity)).record == original.record


@pytest.mark.asyncio
async def test_changed_absolute_expiry_conflicts_even_with_repeated_digest(store):
    original = await reserve(store)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await reserve(store, expires_at=original.expires_at + 1)
    assert await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_revoked_session_is_not_reactivated_on_replay(store):
    original = await reserve(store)
    await store.activate_reserved(original.identity)
    await store.revoke_session(original.session_id)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_session_conflict$"):
        await store.activate_reserved(original.identity)
    async with store._pool.acquire() as connection:
        assert await connection.fetchval(
            f"SELECT state FROM {store.schema}.{TABLE_SESSIONS} WHERE session_id = $1",
            original.session_id,
        ) == "revoked"


@pytest.mark.asyncio
async def test_epoch_change_refuses_without_an_active_session(store):
    original = await reserve(store)
    await store.invalidate_user(original.record["sub"])
    with pytest.raises(SessionIssuanceRefused, match="^issuance_authority_moved$"):
        await store.activate_reserved(original.identity)
    assert await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_expired_reservation_never_activates_or_changes_deadline(store):
    original = await reserve(store)
    async with store._pool.acquire() as connection:
        await connection.execute(
            f"UPDATE {store.schema}.{TABLE_ISSUANCES} SET expires_at = clock_timestamp() - interval '1 second' "
            "WHERE identity = $1", original.identity,
        )
    with pytest.raises(SessionIssuanceRefused, match="^issuance_expired$"):
        await store.activate_reserved(original.identity)
    assert await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("secret_field", ["access_token", "accessToken", "private_key", "cookie"])
async def test_bearer_material_is_refused_before_any_storage(secret_field):
    authority = PostgresBundleSessionStore(pg_pool=object(), tenant="unit", project="unit")
    expiry = int(time.time()) + 600
    sid, ref, record = candidate(expiry)
    record["metadata"] = {secret_field: "synthetic-secret-not-persisted"}
    with pytest.raises(SessionIssuanceRefused, match="^issuance_record_invalid$"):
        await authority.reserve_issuance(
            "a" * 64, "b" * 64, sid, ref, expiry,
            session_record=record, expected_version=1,
        )
