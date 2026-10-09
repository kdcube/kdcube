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
from kdcube_ai_app.auth.bundle import session_planned_issuer
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceRefused
from kdcube_ai_app.auth.bundle.session_planned_issuance import (
    AppliedIssuanceContext, PlannedIssuanceContext, TerminalIssuanceContext,
)
from kdcube_ai_app.auth.bundle.session_schema import TABLE_ISSUANCES, TABLE_USERS
from kdcube_ai_app.auth.bundle.sessions import _make_token
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts, store
from kdcube_ai_app.auth.tests.test_bound_session_issuer import authority

SIGNING_SECRET = "unit-session-signing-secret"  # the key `authority(store)` signs with


class NoCustody:
    """No bearer is kept in custody any more: any custody access fails the test."""
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        self.calls.append(name)
        pytest.fail(f"custody {name} reached; no bearer is stored")


class Signer:
    """The bundle session signature over stored claims, counting every sign call."""
    def __init__(self, secret=SIGNING_SECRET):
        self.secret, self.signed = secret, []

    async def __call__(self, claims):
        bearer = _make_token(claims, secret=self.secret)
        self.signed.append(bearer)
        return bearer


def forbidden_sign(reason):
    async def sign(claims):
        pytest.fail(reason)
    return sign


def sha(bearer):
    return hashlib.sha256(bearer.encode()).hexdigest()


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


async def read_bearer(store, result):
    return await authority(store).read_bound_session_bearer(result)


def scope(store):
    return dict(tenant=store.tenant, project=store.project, store=store)


def grant_inputs():
    return dict(user_id="integration:unit:human", roles=["delegated-client"], permissions=["records:read"])


@pytest.mark.asyncio
async def test_preparation_recovers_one_inactive_original_then_applied_result_activates(store):
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    assert await counts(store) == (1, 1, 0)
    assert (await store.get_user(bound.credential_subject))["permissions"] == []
    result = applied(bound, first)
    token = await read_bearer(store, result)
    assert sha(token) == first.bearer_sha256
    with pytest.raises(AuthenticationError, match="^bundle session is not active$"):
        await BundleSessionAuthManager(authority=authority(store)).authenticate(token)
    recovered = await prepare(store, custody, bound)
    assert recovered.outcome == "recovered" and custody.calls == []
    assert recovered.session_id == first.session_id
    assert recovered.secret_ref == first.secret_ref
    assert recovered.bearer_sha256 == first.bearer_sha256
    active = await activate(store, custody, result)
    again = await activate(store, custody, result)
    assert active.session_id == again.session_id == first.session_id
    assert active.bearer_sha256 == hashlib.sha256(token.encode()).hexdigest()
    assert await counts(store) == (1, 1, 1)
    assert await read_bearer(store, result) == token
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
async def test_changed_plan_refuses_before_signing_for_preparation_activation_and_read(store, field, value):
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    changed = SimpleNamespace(**{**vars(bound), field: getattr(bound, field) + 1 if value is None else value})
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await session_planned_issuer.prepare_bound_session(
            changed, **scope(store), **grant_inputs(), sign=forbidden_sign("changed plan reached signing"))
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await session_planned_issuer.activate_prepared_bound_session(
            applied(changed, first), **scope(store), sign=forbidden_sign("changed plan reached signing"))
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await session_planned_issuer.read_bound_session_bearer(
            applied(changed, first), **scope(store), sign=forbidden_sign("changed plan reached signing"))
    assert await counts(store) == (1, 1, 0)
    assert custody.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state,outcome", [
    ("pending", "applied"), ("aborted", "applied"),
    ("committed", "released"), ("committed", "superseded"),
])
async def test_non_applied_original_result_never_activates(store, state, outcome):
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_result_not_applied$"):
        await activate(store, custody, applied(bound, first, state=state, slot_outcome=outcome))
    assert await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_wrong_token_commitment_and_changed_applied_receipt_refuse(store):
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_commitment_mismatch$"):
        await activate(store, custody, applied(bound, first, token_sha256="f" * 64))
    assert await counts(store) == (1, 1, 0)
    await activate(store, custody, applied(bound, first))
    with pytest.raises(SessionIssuanceRefused, match="^issuance_activation_conflict$"):
        await activate(store, custody, applied(bound, first, receipt_digest="f" * 64))
    assert await counts(store) == (1, 1, 1)


@pytest.mark.asyncio
async def test_activation_with_changed_signing_key_refuses_and_never_activates(store):
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    other_key = BundleSessionAuthority(
        tenant=store.tenant, project=store.project, authority_store=store, secret="another-signing-secret",
    )
    with pytest.raises(SessionIssuanceRefused, match="^issuance_signing_mismatch$"):
        await other_key.activate_prepared_bound_session(applied(bound, first))
    with pytest.raises(SessionIssuanceRefused, match="^issuance_signing_mismatch$"):
        await other_key.read_bound_session_bearer(applied(bound, first))
    assert await counts(store) == (1, 1, 0)
    active = await activate(store, custody, applied(bound, first))
    assert active.bearer_sha256 == first.bearer_sha256 and custody.calls == []


@pytest.mark.asyncio
async def test_planned_reservation_cannot_use_legacy_activation_or_unknown_migration_fence(store):
    bound, custody = plan(store), NoCustody()
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
    bound, custody = plan(store), NoCustody()
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
    custody = NoCustody()
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
        await prepare(store, NoCustody(), bound)
    assert await counts(store) == (0, 0, 0)


@pytest.mark.asyncio
async def test_committed_but_lost_activation_response_recovers_only_with_the_same_key(store):
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)

    class LostActivation:
        def __getattr__(self, name):
            return getattr(store, name)

        async def activate_reserved(self, *args, **kwargs):
            await store.activate_reserved(*args, **kwargs)
            raise TimeoutError("synthetic committed response lost")

    issuer = BundleSessionAuthority(
        tenant=store.tenant, project=store.project, authority_store=LostActivation(),
        secret=SIGNING_SECRET,
    )
    with pytest.raises(SessionIssuanceRefused, match="^issuance_store_unavailable$") as refused:
        await issuer.activate_prepared_bound_session(applied(bound, first), custody=custody)
    assert refused.value.__suppress_context__
    assert await counts(store) == (1, 1, 1)
    # A fresh instance with a different signing secret cannot recover: the stored
    # claims re-sign to another bearer, so it refuses rather than yield one.
    with pytest.raises(SessionIssuanceRefused, match="^issuance_signing_mismatch$"):
        await BundleSessionAuthority(
            tenant=store.tenant, project=store.project, authority_store=store,
            secret="different-signing-secret",
        ).activate_prepared_bound_session(applied(bound, first))
    # A fresh instance with the same key recovers the one original and its bearer.
    fresh = BundleSessionAuthority(tenant=store.tenant, project=store.project, authority_store=store,
                                   secret=SIGNING_SECRET)
    recovered = await fresh.activate_prepared_bound_session(applied(bound, first))
    assert recovered.session_id == first.session_id
    assert recovered.bearer_sha256 == first.bearer_sha256
    assert sha(await fresh.read_bound_session_bearer(applied(bound, first))) == first.bearer_sha256
    assert await counts(store) == (1, 1, 1) and custody.calls == []


@pytest.mark.asyncio
async def test_concurrent_preparation_and_activation_keep_one_original(store):
    bound, custody = plan(store), NoCustody()
    prepared = await asyncio.gather(*[prepare(store, custody, bound) for _ in range(8)])
    assert len({(row.session_id, row.secret_ref, row.bearer_sha256) for row in prepared}) == 1
    assert sum(row.outcome == "issued" for row in prepared) == 1
    assert custody.calls == [] and await counts(store) == (1, 1, 0)
    activated = await asyncio.gather(*[activate(store, custody, applied(bound, row)) for row in prepared])
    assert len({row.session_id for row in activated}) == 1
    assert await counts(store) == (1, 1, 1)
    bearers = await asyncio.gather(*[read_bearer(store, applied(bound, row)) for row in prepared])
    assert len(set(bearers)) == 1 and sha(bearers[0]) == prepared[0].bearer_sha256


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"target_incarnation": True}, {"card_revision": 0},
    {"intent_digest": "not-a-digest"}, {"credential_issuer": " bad"},
    {"credential_subject": ""}, {"slot": "access\n"},
    {"delivery_deadline": False}, {"reserved_until": 9999999999999},
])
async def test_malformed_plan_refuses_before_any_storage(store, changes):
    with pytest.raises(SessionIssuanceRefused, match="^issuance_context_invalid$"):
        await prepare(store, NoCustody(), plan(store, **changes))
    assert await counts(store) == (0, 0, 0)


@pytest.mark.asyncio
async def test_request_dicts_are_not_trusted_plan_or_result_contexts(store):
    custody, bound = NoCustody(), plan(store)
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
    original, custody = plan(store), NoCustody()
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
    original, custody = plan(store), NoCustody()
    bound = PlannedIssuanceContext.from_context(original)
    first = await prepare(store, custody, bound)
    result = oauth_result(bound, first)
    setattr(result, field, value)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        AppliedIssuanceContext.from_oauth_result(bound, result)
    assert await counts(store) == (1, 1, 0)


def terminal(bound):
    return TerminalIssuanceContext.from_context(SimpleNamespace(
        plan=bound, state="aborted", slot_outcome="released", receipt_digest="", token_sha256=""))


@pytest.mark.asyncio
async def test_bearer_read_resigns_the_identical_first_bearer(store):
    bound, sign = plan(store), Signer()
    first = await session_planned_issuer.prepare_bound_session(bound, **scope(store), **grant_inputs(), sign=sign)
    assert len(sign.signed) == 1 and sha(sign.signed[0]) == first.bearer_sha256
    original = sign.signed[0]
    result = applied(bound, first)
    assert await session_planned_issuer.read_bound_session_bearer(result, **scope(store), sign=sign) == original
    await session_planned_issuer.activate_prepared_bound_session(result, **scope(store), sign=sign)
    assert await session_planned_issuer.read_bound_session_bearer(result, **scope(store), sign=Signer()) == original
    assert await read_bearer(store, result) == original
    assert await counts(store) == (1, 1, 1)
    async with store._pool.acquire() as connection:
        rows = await connection.fetch(f"SELECT * FROM {store.schema}.{TABLE_ISSUANCES}")
    assert original not in json.dumps([dict(row) for row in rows], default=str)


@pytest.mark.asyncio
async def test_bearer_read_after_retirement_refuses_terminal_before_signing(store):
    bound = plan(store)
    first = await session_planned_issuer.prepare_bound_session(bound, **scope(store), **grant_inputs(), sign=Signer())
    await session_planned_issuer.retire_prepared_bound_session(terminal(bound), **scope(store))
    sign = Signer()
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await session_planned_issuer.read_bound_session_bearer(applied(bound, first), **scope(store), sign=sign)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await session_planned_issuer.activate_prepared_bound_session(applied(bound, first), **scope(store), sign=sign)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await session_planned_issuer.prepare_bound_session(bound, **scope(store), **grant_inputs(), sign=sign)
    assert sign.signed == [] and await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_expired_access_with_live_delivery_refuses_read_and_activation_before_signing(store, monkeypatch):
    """The access expiry can precede the delivery deadline; an expired original never reaches the signer."""
    now = int(time.time())
    bound = plan(store, expires_at=now + 400, delivery_deadline=now + 600)
    first = await session_planned_issuer.prepare_bound_session(bound, **scope(store), **grant_inputs(), sign=Signer())
    monkeypatch.setattr(session_planned_issuer, "time", SimpleNamespace(time=lambda: now + 450))
    sign = Signer()
    with pytest.raises(SessionIssuanceRefused, match="^issuance_expired$"):
        await session_planned_issuer.read_bound_session_bearer(applied(bound, first), **scope(store), sign=sign)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_expired$"):
        await session_planned_issuer.activate_prepared_bound_session(applied(bound, first), **scope(store), sign=sign)
    assert sign.signed == [] and await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_bearer_read_with_changed_key_refuses_signing_mismatch(store):
    bound, sign = plan(store), Signer()
    first = await session_planned_issuer.prepare_bound_session(bound, **scope(store), **grant_inputs(), sign=sign)
    other = Signer("another-signing-secret")
    with pytest.raises(SessionIssuanceRefused, match="^issuance_signing_mismatch$"):
        await session_planned_issuer.read_bound_session_bearer(applied(bound, first), **scope(store), sign=other)
    assert len(other.signed) == 1 and sha(other.signed[0]) != first.bearer_sha256
    assert await session_planned_issuer.read_bound_session_bearer(
        applied(bound, first), **scope(store), sign=sign) == sign.signed[0]
    assert await counts(store) == (1, 1, 0)
