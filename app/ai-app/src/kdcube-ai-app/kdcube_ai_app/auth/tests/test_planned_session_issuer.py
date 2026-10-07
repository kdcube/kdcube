from __future__ import annotations

import asyncio
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from kdcube_ai_app.auth.AuthManager import AuthenticationError
from kdcube_ai_app.auth.bundle import BundleSessionAuthManager
from kdcube_ai_app.auth.bundle import BundleSessionAuthority
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceRefused
from kdcube_ai_app.auth.bundle.session_planned_issuance import AppliedIssuanceContext, PlannedIssuanceContext
from kdcube_ai_app.auth.bundle.session_schema import TABLE_ISSUANCES, TABLE_USERS
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts, store
from kdcube_ai_app.auth.tests.test_bound_session_issuer import MemoryCustody, authority


def plan(store, **changes):
    now = int(time.time())
    values = dict(
        tenant=store.tenant, project=store.project, transaction_id="a" * 64,
        slot="access", actor="human", client_id="unit-client",
        decision_request_id="1" * 64, intent_digest="b" * 64,
        original_input_digest="c" * 64, effect_digest="d" * 64,
        access_id="unit-card", target_incarnation="f" * 64, base_revision=1, card_revision=2,
        credential_issuer="unit-delegated-authority",
        credential_subject="integration:unit:human", expires_at=now + 3600, cap_expires_at=now + 3600,
        delivery_deadline=now + 600, reserved_until=now + 300,
    )
    return SimpleNamespace(**{**values, **changes})


def applied(bound, receipt, **changes):
    return SimpleNamespace(**{
        "plan": bound, "state": "committed", "slot_outcome": "applied",
        "receipt_digest": "e" * 64, "token_sha256": receipt.bearer_sha256,
        **changes,
    })


async def prepare(store, custody, bound, **changes):
    return await authority(store).prepare_bound_session(
        bound, user_id="integration:unit:human", roles=["delegated-client"],
        permissions=changes.get("permissions", ["records:read"]), custody=custody,
    )


async def activate(store, custody, result):
    return await authority(store).activate_prepared_bound_session(result, custody=custody)


@pytest.mark.asyncio
async def test_preparation_recovers_one_inactive_original_then_applied_result_activates(store):
    bound, custody = plan(store), MemoryCustody()
    first = await prepare(store, custody, bound)
    assert await counts(store) == (1, 1, 0)
    assert (await store.get_user(bound.credential_subject))["permissions"] == []
    token = await custody.get(first.secret_ref)
    with pytest.raises(AuthenticationError, match="^bundle session is not active$"):
        await BundleSessionAuthManager(authority=authority(store)).authenticate(token)
    recovered = await prepare(store, custody, bound)
    assert recovered.outcome == "recovered" and custody.created == 1
    assert recovered.session_id == first.session_id
    assert recovered.secret_ref == first.secret_ref
    assert recovered.bearer_sha256 == first.bearer_sha256
    result = applied(bound, first)
    active = await activate(store, custody, result)
    again = await activate(store, custody, result)
    assert active.session_id == again.session_id == first.session_id
    assert active.bearer_sha256 == hashlib.sha256(token.encode()).hexdigest()
    assert await counts(store) == (1, 1, 1)
    user = await BundleSessionAuthManager(authority=authority(store)).authenticate(token)
    assert user.sub == bound.credential_subject and user.permissions == ["records:read"]
    row = await store.read_issuance(hashlib.sha256(json.dumps(
        [bound.tenant, bound.project, bound.transaction_id, bound.slot],
        separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest())
    assert row.expires_at == bound.expires_at
    assert row.delivery_deadline == bound.delivery_deadline
    assert row.reserved_until == bound.reserved_until
    assert row.activation_digest is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("actor", "another-human"), ("client_id", "another-client"),
    ("decision_request_id", "f" * 64), ("intent_digest", "f" * 64),
    ("original_input_digest", "f" * 64), ("effect_digest", "f" * 64),
    ("access_id", "other-card"), ("target_incarnation", "0" * 64), ("base_revision", 0),
    ("card_revision", 3), ("credential_issuer", "other-issuer"),
    ("credential_subject", "integration:other:human"),
    ("expires_at", 1000000000), ("cap_expires_at", None), ("delivery_deadline", None), ("reserved_until", None),
])
async def test_changed_plan_refuses_before_custody_for_preparation_and_activation(store, field, value):
    bound, custody = plan(store), MemoryCustody()
    first = await prepare(store, custody, bound)
    changed = SimpleNamespace(**{**vars(bound), field: getattr(bound, field) + 1 if value is None else value})

    class ForbiddenCustody:
        async def get(self, **kwargs):
            pytest.fail("changed plan reached custody read")

        async def create(self, **kwargs):
            pytest.fail("changed plan reached custody write")

    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await prepare(store, ForbiddenCustody(), changed)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await activate(store, ForbiddenCustody(), applied(changed, first))
    assert await counts(store) == (1, 1, 0)
    assert custody.created == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state,outcome", [
    ("pending", "applied"), ("aborted", "applied"),
    ("committed", "released"), ("committed", "superseded"),
])
async def test_non_applied_original_result_never_activates(store, state, outcome):
    bound, custody = plan(store), MemoryCustody()
    first = await prepare(store, custody, bound)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_result_not_applied$"):
        await activate(store, custody, applied(bound, first, state=state, slot_outcome=outcome))
    assert await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_wrong_token_commitment_and_changed_applied_receipt_refuse(store):
    bound, custody = plan(store), MemoryCustody()
    first = await prepare(store, custody, bound)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_commitment_mismatch$"):
        await activate(store, custody, applied(bound, first, token_sha256="f" * 64))
    assert await counts(store) == (1, 1, 0)
    await activate(store, custody, applied(bound, first))
    with pytest.raises(SessionIssuanceRefused, match="^issuance_activation_conflict$"):
        await activate(store, custody, applied(bound, first, receipt_digest="f" * 64))
    assert await counts(store) == (1, 1, 1)


@pytest.mark.asyncio
async def test_activation_never_recreates_missing_prepared_custody(store):
    bound, custody = plan(store), MemoryCustody()
    first = await prepare(store, custody, bound)
    custody.values.clear()
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_missing$"):
        await activate(store, custody, applied(bound, first))
    assert custody.created == 1 and await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_planned_reservation_cannot_use_legacy_activation_or_unknown_migration_fence(store):
    bound, custody = plan(store), MemoryCustody()
    first = await prepare(store, custody, bound)
    async with store._pool.acquire() as connection:
        identity = await connection.fetchval(f"SELECT identity FROM {store.schema}.{TABLE_ISSUANCES}")
    with pytest.raises(SessionIssuanceRefused, match="^issuance_activation_required$"):
        await store.activate_reserved(identity)
    async with store._pool.acquire() as connection:
        await connection.execute(f"ALTER TABLE {store.schema}.{TABLE_ISSUANCES} DROP COLUMN delivery_deadline")
    await store.ensure_schema()
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await prepare(store, custody, bound)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await activate(store, custody, applied(bound, first))
    assert await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_newer_user_grant_fence_refuses_old_planned_activation(store):
    bound, custody = plan(store), MemoryCustody()
    first = await prepare(store, custody, bound)
    await store.register_user(sub=bound.credential_subject, updates={"permissions": ["newer:grant"]}, now=int(time.time()))
    with pytest.raises(SessionIssuanceRefused, match="^issuance_authority_moved$"):
        await activate(store, custody, applied(bound, first))
    assert (await store.get_user(bound.credential_subject))["permissions"] == ["newer:grant"]
    assert await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_activation_expiry_is_checked_by_pg_after_waiting_for_user_lock(store):
    async with store._pool.acquire() as connection:
        deadline = int(await connection.fetchval("SELECT extract(epoch FROM clock_timestamp())")) + 2
    bound = plan(store, delivery_deadline=deadline, reserved_until=deadline)
    custody = MemoryCustody()
    first = await prepare(store, custody, bound)
    async with store._pool.acquire() as blocker:
        async with blocker.transaction():
            await blocker.fetchrow(
                f"SELECT subject FROM {store.schema}.{TABLE_USERS} WHERE subject=$1 FOR UPDATE", bound.credential_subject,
            )
            task = asyncio.create_task(activate(store, custody, applied(bound, first)))
            try:
                async with asyncio.timeout(5):
                    while not await blocker.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' "
                        "AND query LIKE $1)", "%" + store.schema + "." + TABLE_USERS + "%",
                    ):
                        assert not task.done(), "activation never reached the user lock"
                        await asyncio.sleep(0.01)
                        # pg_stat_activity caches a snapshot in this explicit
                        # transaction; refresh it before observing the waiter.
                        await blocker.execute("SELECT pg_stat_clear_snapshot()")
                    while await blocker.fetchval("SELECT extract(epoch FROM clock_timestamp())") <= deadline:
                        await asyncio.sleep(0.02)
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
        with pytest.raises(SessionIssuanceRefused, match="^issuance_delivery_expired$"):
            await asyncio.wait_for(task, timeout=5)
    assert await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_expired_delivery_window_refuses_without_any_provisioning(store):
    bound = plan(store, delivery_deadline=int(time.time()) - 1, reserved_until=int(time.time()) - 1)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_delivery_expired$"):
        await prepare(store, MemoryCustody(), bound)
    assert await counts(store) == (0, 0, 0)


@pytest.mark.asyncio
async def test_committed_but_lost_activation_response_recovers_without_resigning(store):
    bound, custody = plan(store), MemoryCustody()
    first = await prepare(store, custody, bound)

    class LostActivation:
        def __getattr__(self, name):
            return getattr(store, name)

        async def activate_reserved(self, *args, **kwargs):
            await store.activate_reserved(*args, **kwargs)
            raise TimeoutError("synthetic committed response lost")

    issuer = BundleSessionAuthority(
        tenant=store.tenant, project=store.project, authority_store=LostActivation(),
        secret="unused-for-activation",
    )
    with pytest.raises(SessionIssuanceRefused, match="^issuance_store_unavailable$") as refused:
        await issuer.activate_prepared_bound_session(applied(bound, first), custody=custody)
    assert refused.value.__suppress_context__
    assert await counts(store) == (1, 1, 1)
    # A fresh instance with a different signing secret can still recover:
    # activation reads the original custody and never signs a bearer.
    recovered = await BundleSessionAuthority(
        tenant=store.tenant, project=store.project, authority_store=store,
        secret="different-unused-signing-secret",
    ).activate_prepared_bound_session(applied(bound, first), custody=custody)
    assert recovered.session_id == first.session_id
    assert recovered.bearer_sha256 == first.bearer_sha256 and custody.created == 1


@pytest.mark.asyncio
async def test_concurrent_preparation_and_activation_keep_one_original(store):
    bound, custody = plan(store), MemoryCustody()
    prepared = await asyncio.gather(*[prepare(store, custody, bound) for _ in range(8)])
    assert len({(row.session_id, row.secret_ref, row.bearer_sha256) for row in prepared}) == 1
    assert sum(row.outcome == "issued" for row in prepared) == 1
    assert custody.created == 1 and await counts(store) == (1, 1, 0)
    activated = await asyncio.gather(*[activate(store, custody, applied(bound, row)) for row in prepared])
    assert len({row.session_id for row in activated}) == 1
    assert await counts(store) == (1, 1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"target_incarnation": True}, {"card_revision": 0},
    {"intent_digest": "not-a-digest"}, {"credential_issuer": " bad"},
    {"credential_subject": ""}, {"slot": "access\n"},
    {"delivery_deadline": False}, {"reserved_until": 9999999999999},
])
async def test_malformed_plan_refuses_before_any_storage(store, changes):
    with pytest.raises(SessionIssuanceRefused, match="^issuance_context_invalid$"):
        await prepare(store, MemoryCustody(), plan(store, **changes))
    assert await counts(store) == (0, 0, 0)


@pytest.mark.asyncio
async def test_request_dicts_are_not_trusted_plan_or_result_contexts(store):
    custody, bound = MemoryCustody(), plan(store)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_context_invalid$"):
        await prepare(store, custody, vars(bound))
    first = await prepare(store, custody, bound)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_result_invalid$"):
        await activate(store, custody, vars(applied(bound, first)))
    assert await counts(store) == (1, 1, 0)


def oauth_plan(bound):
    return SimpleNamespace(
        **{key: value for key, value in vars(bound).items() if key not in {
            "slot", "actor", "effect_digest", "target_incarnation", "card_revision", "expires_at", "cap_expires_at",
        }},
        grantor_subject=bound.actor, card_content_hash=bound.target_incarnation,
        candidate_revision=bound.card_revision, expires_at=bound.cap_expires_at,
        slots=("access", "refresh"), effect_digests={"access": bound.effect_digest, "refresh": "2" * 64},
    )


def oauth_result(bound, receipt):
    return SimpleNamespace(
        transaction_id=bound.transaction_id, intent_digest=bound.intent_digest,
        state="committed", access_id=bound.access_id, card_revision=bound.card_revision,
        expires_at=bound.cap_expires_at, delivery_deadline=bound.delivery_deadline,
        receipt_digest="e" * 64, per_slot={"access": SimpleNamespace(
            outcome="applied", effect_digest=bound.effect_digest, token_sha256=receipt.bearer_sha256,
        )},
    )


@pytest.mark.asyncio
async def test_original_oauth_shapes_map_card_hash_and_revision_separately(store):
    original, custody = plan(store), MemoryCustody()
    bound = PlannedIssuanceContext.from_oauth_plan(oauth_plan(original), slot="access", expires_at=original.expires_at - 1)
    assert bound.target_incarnation == original.target_incarnation
    assert bound.card_revision == original.card_revision and bound.base_revision == original.base_revision
    assert bound.cap_expires_at == original.cap_expires_at
    first = await prepare(store, custody, bound)
    result = AppliedIssuanceContext.from_oauth_result(bound, oauth_result(bound, first))
    active = await activate(store, custody, result)
    assert active.session_id == first.session_id and await counts(store) == (1, 1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("transaction_id", "0" * 64), ("intent_digest", "0" * 64),
    ("access_id", "other-card"), ("card_revision", 3),
    ("expires_at", 1000000000), ("delivery_deadline", 1000000000),
])
async def test_oauth_result_adapter_refuses_other_originals(store, field, value):
    original, custody = plan(store), MemoryCustody()
    bound = PlannedIssuanceContext.from_context(original)
    first = await prepare(store, custody, bound)
    result = oauth_result(bound, first)
    setattr(result, field, value)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        AppliedIssuanceContext.from_oauth_result(bound, result)
    assert await counts(store) == (1, 1, 0)
