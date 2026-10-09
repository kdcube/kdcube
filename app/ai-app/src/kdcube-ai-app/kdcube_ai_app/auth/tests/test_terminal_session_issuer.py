from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceRefused
from kdcube_ai_app.auth.bundle.session_store import PostgresBundleSessionStore
from kdcube_ai_app.auth.bundle.session_schema import TABLE_ISSUANCES, TABLE_ISSUANCE_TERMINALS
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts, store
from kdcube_ai_app.auth.tests.test_bound_session_issuer import authority
from kdcube_ai_app.auth.tests.test_planned_session_issuer import applied, oauth_result, plan


class NoCustody:
    """Former bearer custody: every call fails the test; no bearer is kept outside PostgreSQL."""
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append("create")
        pytest.fail("custody create was called")

    async def get(self, *args, **kwargs):
        self.calls.append("get")
        pytest.fail("custody get was called")

    async def delete(self, **kwargs):
        self.calls.append("delete")
        pytest.fail("custody delete was called")


def issuer(store, signs=None):
    """An authority whose signing-key resolutions (one per sign) append to signs."""
    made = authority(store)
    if signs is not None:
        resolve = made._resolve_secret
        async def counted():
            signs.append(1)
            return await resolve()
        made._resolve_secret = counted
    return made


async def prepare(store, custody, bound, signs=None):
    return await issuer(store, signs).prepare_bound_session(
        bound, user_id="integration:unit:human", roles=["delegated-client"],
        permissions=["records:read"], custody=custody,
    )


async def activate(store, custody, result, signs=None):
    return await issuer(store, signs).activate_prepared_bound_session(result, custody=custody)


async def bearer(store, result, signs=None):
    return await issuer(store, signs).read_bound_session_bearer(result)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def terminal(bound, *, state="aborted", outcome="released", token_sha256="", **changes):
    return SimpleNamespace(
        plan=bound, state=state, slot_outcome=outcome,
        receipt_digest="e" * 64 if state == "committed" else "",
        token_sha256=token_sha256, **changes,
    )


async def retire(store, custody, context):
    return await authority(store).retire_prepared_bound_session(context, custody=custody)


async def terminals(store):
    async with store._pool.acquire() as connection:
        return await connection.fetchval(f"SELECT count(*) FROM {store.schema}.{TABLE_ISSUANCE_TERMINALS}")


@pytest.mark.asyncio
async def test_terminal_api_is_present_before_any_cleanup(store):
    assert callable(getattr(authority(store), "retire_prepared_bound_session", None))


@pytest.mark.asyncio
async def test_abort_before_prepare_is_a_durable_no_mint_tombstone(store):
    bound, custody, signs = plan(store), NoCustody(), []
    result = await retire(store, custody, terminal(bound))
    assert result.identity and result.secret_ref is None
    fresh = PostgresBundleSessionStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await prepare(fresh, custody, bound, signs)
    assert await counts(store) == (0, 0, 0)
    assert not signs and not custody.calls
    assert (await retire(fresh, custody, terminal(bound))).identity == result.identity


@pytest.mark.asyncio
async def test_retirement_after_initial_read_fences_reserve_before_any_row(store):
    bound, custody = plan(store), NoCustody()
    read, resume = asyncio.Event(), asyncio.Event()

    class PausedRead:
        def __getattr__(self, name):
            return getattr(store, name)

        async def read_issuance(self, identity):
            original = await store.read_issuance(identity)
            assert original is None
            read.set()
            await resume.wait()
            return original

    task = asyncio.create_task(prepare(PausedRead(), custody, bound))
    try:
        await asyncio.wait_for(read.wait(), 5)
        retired = await retire(store, custody, terminal(bound))
        assert retired.secret_ref is None
        resume.set()
        with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
            await asyncio.wait_for(task, 5)
        assert await counts(store) == (0, 0, 0)
        assert not custody.calls
        assert await terminals(store) == 1
    finally:
        resume.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_retirement_after_resign_check_fences_activation_before_session_insert(store):
    from kdcube_ai_app.auth.bundle.session_issuance_store import PostgresSessionIssuanceStore
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    activating, resume = asyncio.Event(), asyncio.Event()

    class PausedPreLockRead(PostgresSessionIssuanceStore):
        async def read_issuance(self, identity):
            original = await super().read_issuance(identity)
            # Pause the store's own read before its user/issuance row locks,
            # after the SDK has already re-signed and matched the stored
            # claims. A second read fence alone cannot protect the ensuing
            # activation write either.
            activating.set()
            await resume.wait()
            return original

    class PausedActivation:
        def __getattr__(self, name):
            return getattr(store, name)

        async def activate_reserved(self, *args, **kwargs):
            return await PausedPreLockRead(
                pg_pool=store._pool, schema=store.schema,
                tenant=store.tenant, project=store.project,
            ).activate_reserved(*args, **kwargs)

    task = asyncio.create_task(activate(PausedActivation(), custody, applied(bound, first)))
    try:
        await asyncio.wait_for(activating.wait(), 5)
        retired = await retire(store, custody, terminal(bound))
        assert retired.secret_ref == first.secret_ref
        resume.set()
        with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
            await asyncio.wait_for(task, 5)
        assert await counts(store) == (1, 1, 0)
        with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
            await bearer(store, applied(bound, first))
        assert not custody.calls
        async with store._pool.acquire() as connection:
            assert await connection.fetchval(
                f"SELECT state FROM {store.schema}.{TABLE_ISSUANCES}",
            ) == "reserved"
    finally:
        resume.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("state,outcome", [("aborted", "released"), ("committed", "superseded")])
async def test_terminal_retirement_fences_only_inactive_original_and_never_user_authority(store, state, outcome):
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    sibling = plan(store, transaction_id="2" * 64)
    other = await prepare(store, custody, sibling)
    await activate(store, custody, applied(sibling, other))
    before = await store.get_login_state(bound.credential_subject)
    context = terminal(bound, state=state, outcome=outcome, token_sha256=first.bearer_sha256)
    retired = await retire(store, custody, context)
    assert retired.secret_ref == first.secret_ref
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await bearer(store, applied(bound, first))
    assert sha(await bearer(store, applied(sibling, other))) == other.bearer_sha256
    assert await store.get_login_state(bound.credential_subject) == before
    assert await counts(store) == (1, 2, 1)
    fresh = PostgresBundleSessionStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    again = await retire(fresh, custody, context)
    assert again.secret_ref == first.secret_ref
    signs = []
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await prepare(fresh, custody, bound, signs)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await activate(fresh, custody, applied(bound, first), signs)
    assert not signs and not custody.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("state,outcome", [
    ("pending", "pending"), ("pending", "released"), ("committed", "applied"),
    ("aborted", "applied"), ("aborted", "superseded"), ("committed", "released"),
])
async def test_non_terminal_or_applied_result_never_authorizes_cleanup(store, state, outcome):
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_result_not_terminal$"):
        await retire(store, custody, terminal(bound, state=state, outcome=outcome,
                                            token_sha256=first.bearer_sha256))
    assert await terminals(store) == 0
    assert sha(await bearer(store, applied(bound, first))) == first.bearer_sha256
    await activate(store, custody, applied(bound, first))
    assert not custody.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("client_id", "other-client"), ("access_id", "other-card"), ("target_incarnation", "0" * 64),
    ("intent_digest", "0" * 64), ("effect_digest", "0" * 64),
    ("credential_subject", "integration:other:human"), ("card_revision", 3),
])
async def test_changed_terminal_plan_refuses_and_keeps_original(store, field, value):
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    changed = SimpleNamespace(**{**vars(bound), field: value})
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await retire(store, custody, terminal(changed))
    assert await terminals(store) == 0
    assert sha(await bearer(store, applied(bound, first))) == first.bearer_sha256
    assert not custody.calls


@pytest.mark.asyncio
async def test_wrong_terminal_token_commitment_refuses_without_retirement(store):
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_commitment_mismatch$"):
        await retire(store, custody, terminal(bound, token_sha256="0" * 64))
    assert await terminals(store) == 0
    await activate(store, custody, applied(bound, first))
    assert sha(await bearer(store, applied(bound, first))) == first.bearer_sha256
    assert not custody.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_kind", ["abort", "superseded", "expired"])
async def test_active_session_is_never_retired_by_original_cleanup(store, terminal_kind):
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    await activate(store, custody, applied(bound, first))
    context = terminal(bound)
    if terminal_kind == "superseded":
        context = terminal(bound, state="committed", outcome="superseded",
                           token_sha256=first.bearer_sha256)
    elif terminal_kind == "expired":
        context = terminal(bound, state="expired", outcome="expired")
    with pytest.raises(SessionIssuanceRefused, match="^issuance_already_active$"):
        await retire(store, custody, context)
    assert await terminals(store) == 0 and not custody.calls
    assert sha(await bearer(store, applied(bound, first))) == first.bearer_sha256
    assert (await store.read_issuance(hashlib.sha256(json.dumps(
        [bound.tenant, bound.project, bound.transaction_id, bound.slot],
        separators=(",", ":"),
    ).encode()).hexdigest())).state == "active"


@pytest.mark.asyncio
async def test_expiry_cleanup_uses_pg_clock_not_caller_or_python_clock(store, monkeypatch):
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    monkeypatch.setattr("kdcube_ai_app.auth.bundle.session_bound_issuer.time.time",
                        lambda: bound.delivery_deadline + 1)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_delivery_not_expired$"):
        await retire(store, custody, terminal(bound, state="expired", outcome="expired"))
    monkeypatch.undo()
    assert await terminals(store) == 0 and not custody.calls
    assert sha(await bearer(store, applied(bound, first))) == first.bearer_sha256


@pytest.mark.asyncio
async def test_expired_unprepared_plan_can_retire_without_provisioning(store):
    bound, custody = plan(store, delivery_deadline=int(time.time()) - 1,
                          reserved_until=int(time.time()) - 2), NoCustody()
    result = await retire(store, custody, terminal(bound, state="expired", outcome="expired"))
    assert result.secret_ref is None and await counts(store) == (0, 0, 0)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await prepare(store, custody, bound)
    assert not custody.calls


@pytest.mark.asyncio
async def test_lost_retirement_commit_response_keeps_tombstone_then_recovers_same_ref(store):
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    class LostCommit:
        def __getattr__(self, name):
            return getattr(store, name)
        async def retire_issuance(self, context):
            await store.retire_issuance(context)
            raise TimeoutError("synthetic lost commit")
    with pytest.raises(SessionIssuanceRefused, match="^issuance_store_unavailable$"):
        await authority(LostCommit()).retire_prepared_bound_session(terminal(bound), custody=custody)
    assert await terminals(store) == 1
    signs = []
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await prepare(store, custody, bound, signs)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await bearer(store, applied(bound, first), signs)
    recovered = await retire(store, custody, terminal(bound))
    assert recovered.secret_ref == first.secret_ref
    assert not signs and not custody.calls


@pytest.mark.asyncio
async def test_retirement_is_the_pg_tombstone_alone_and_retries_same_ref(store):
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    assert (await retire(store, custody, terminal(bound))).secret_ref == first.secret_ref
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await prepare(store, custody, bound)
    assert (await retire(store, custody, terminal(bound))).secret_ref == first.secret_ref
    assert await terminals(store) == 1 and await counts(store) == (1, 1, 0)
    assert not custody.calls


@pytest.mark.asyncio
async def test_preparation_racing_terminal_commit_refuses_then_nothing_signs_again(store):
    bound, custody = plan(store), NoCustody()
    entered, resume = asyncio.Event(), asyncio.Event()
    class DelayedReserve:
        def __getattr__(self, name):
            return getattr(store, name)
        async def reserve_issuance(self, *args, **kwargs):
            reserved = await store.reserve_issuance(*args, **kwargs)
            # The reservation is committed; retirement commits before the
            # SDK's durable terminal recheck.
            entered.set()
            await resume.wait()
            return reserved
    task = asyncio.create_task(prepare(DelayedReserve(), custody, bound))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        retired = await retire(store, custody, terminal(bound))
        resume.set()
        with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
            await asyncio.wait_for(task, 5)
        assert retired.secret_ref is not None
        assert await counts(store) == (1, 1, 0)
        signs = []
        with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
            await prepare(store, custody, bound, signs)
        async with store._pool.acquire() as connection:
            token_sha256 = await connection.fetchval(
                f"SELECT session_record->>'token_sha256' FROM {store.schema}.{TABLE_ISSUANCES}",
            )
        result = applied(bound, SimpleNamespace(bearer_sha256=token_sha256))
        with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
            await activate(store, custody, result, signs)
        with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
            await bearer(store, result, signs)
        assert not signs and not custody.calls
    finally:
        resume.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_terminal_context_adapts_exact_original_hub_result_and_rejects_dicts(store):
    from kdcube_ai_app.auth.bundle.session_planned_issuance import TerminalIssuanceContext
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    hub = oauth_result(bound, first)
    hub.state, hub.receipt_digest, hub.card_revision = "aborted", "", bound.base_revision
    hub.per_slot["access"].outcome = "released"
    context = TerminalIssuanceContext.from_oauth_result(bound, hub)
    assert (await retire(store, custody, context)).secret_ref == first.secret_ref
    with pytest.raises(SessionIssuanceRefused, match="^issuance_result_invalid$"):
        await retire(store, custody, vars(context))
    assert not custody.calls


@pytest.mark.asyncio
async def test_aborted_original_hub_result_with_absent_slot_fences_first_mint(store):
    from kdcube_ai_app.auth.bundle.session_planned_issuance import TerminalIssuanceContext
    bound, custody, signs = plan(store), NoCustody(), []
    hub = SimpleNamespace(
        transaction_id=bound.transaction_id, intent_digest=bound.intent_digest,
        state="aborted", access_id=bound.access_id, card_revision=bound.base_revision,
        expires_at=bound.cap_expires_at, delivery_deadline=bound.delivery_deadline,
        receipt_digest="", per_slot={"access": SimpleNamespace(
            outcome="pending", effect_digest=bound.effect_digest, token_sha256="",
        )},
    )
    terminal_context = TerminalIssuanceContext.from_oauth_result(bound, hub)
    assert (await retire(store, custody, terminal_context)).secret_ref is None
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await prepare(store, custody, bound, signs)
    assert not signs and not custody.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"state": {}}, {"slot_outcome": []}, {"receipt_digest": "raw-receipt"},
    {"token_sha256": True}, {"token_sha256": "not-a-hash"},
])
async def test_malformed_terminal_inputs_have_finite_refusal_before_store(store, changes):
    context = terminal(plan(store))
    vars(context).update(changes)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_result_(invalid|not_terminal)$"):
        await retire(store, NoCustody(), context)
    assert await counts(store) == (0, 0, 0)


@pytest.mark.asyncio
async def test_changed_terminal_receipt_cannot_replace_first_tombstone(store):
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    await retire(store, custody, terminal(bound, state="committed", outcome="superseded",
                                         token_sha256=first.bearer_sha256))
    async with store._pool.acquire() as connection:
        pinned = await connection.fetchval(
            f"SELECT row_to_json(t)::text FROM {store.schema}.{TABLE_ISSUANCE_TERMINALS} t",
        )
    changed = terminal(bound, state="committed", outcome="superseded",
                       token_sha256=first.bearer_sha256)
    changed.receipt_digest = "0" * 64
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal_conflict$"):
        await retire(store, custody, changed)
    async with store._pool.acquire() as connection:
        assert await connection.fetchval(
            f"SELECT row_to_json(t)::text FROM {store.schema}.{TABLE_ISSUANCE_TERMINALS} t",
        ) == pinned
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await bearer(store, applied(bound, first))
    assert not custody.calls


@pytest.mark.asyncio
async def test_one_connection_pool_parallel_retirement_is_bounded_and_pins_one_tombstone(store):
    import asyncpg
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    token = await bearer(store, applied(bound, first))
    pool = await asyncpg.create_pool(os.environ["KDCUBE_TEST_POSTGRES_DSN"], min_size=1, max_size=1)
    fresh = PostgresBundleSessionStore(pg_pool=pool, tenant=store.tenant, project=store.project)
    try:
        results = await asyncio.wait_for(asyncio.gather(*[
            retire(fresh, custody, terminal(bound)) for _ in range(16)
        ]), 5)
    finally:
        await pool.close()
    assert {result.secret_ref for result in results} == {first.secret_ref}
    async with store._pool.acquire() as connection:
        assert await connection.fetchval(f"SELECT count(*) FROM {store.schema}.{TABLE_ISSUANCE_TERMINALS}") == 1
        encoded = await connection.fetchval(
            f"SELECT row_to_json(t)::text FROM {store.schema}.{TABLE_ISSUANCE_TERMINALS} t",
        )
    assert first.secret_ref not in encoded and "bearer" not in encoded and token not in encoded
    assert await counts(store) == (1, 1, 0) and not custody.calls


@pytest.mark.asyncio
async def test_delivery_expiry_is_rechecked_after_real_identity_lock_wait(store):
    from kdcube_ai_app.auth.bundle.session_planned_issuance import PlannedIssuanceContext
    async with store._pool.acquire() as connection:
        deadline = int(await connection.fetchval("SELECT floor(extract(epoch FROM clock_timestamp()))")) + 2
    bound, custody = plan(store, delivery_deadline=deadline, reserved_until=deadline), NoCustody()
    first = await prepare(store, custody, bound)
    identity = PlannedIssuanceContext.from_context(bound).identity
    async with store._pool.acquire() as blocker:
        async with blocker.transaction():
            await blocker.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                store.schema + ":session-issuance:" + identity,
            )
            task = asyncio.create_task(retire(
                store, custody, terminal(bound, state="expired", outcome="expired"),
            ))
            try:
                async with asyncio.timeout(5):
                    while not await blocker.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                        "WHERE wait_event_type='Lock' AND wait_event='advisory' AND pid<>pg_backend_pid())",
                    ):
                        assert not task.done(), "retirement did not wait for the identity lock"
                        await asyncio.sleep(0.01)
                        await blocker.execute("SELECT pg_stat_clear_snapshot()")
                    while await blocker.fetchval("SELECT extract(epoch FROM clock_timestamp())") <= deadline:
                        await asyncio.sleep(0.02)
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
        assert (await asyncio.wait_for(task, 5)).secret_ref == first.secret_ref
    assert await terminals(store) == 1 and not custody.calls


@pytest.mark.asyncio
async def test_retired_identity_after_restart_refuses_and_no_bearer_is_stored(store):
    import asyncpg
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    token = await bearer(store, applied(bound, first))
    await retire(store, custody, terminal(bound))
    pool = await asyncpg.create_pool(os.environ["KDCUBE_TEST_POSTGRES_DSN"], min_size=1, max_size=1)
    try:
        fresh = PostgresBundleSessionStore(pg_pool=pool, tenant=store.tenant, project=store.project)
        signs = []
        with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
            await prepare(fresh, custody, bound, signs)
        with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
            await activate(fresh, custody, applied(bound, first), signs)
        with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
            await bearer(fresh, applied(bound, first), signs)
        assert (await retire(fresh, custody, terminal(bound))).secret_ref == first.secret_ref
        assert not signs and not custody.calls
    finally:
        await pool.close()
    assert await counts(store) == (1, 1, 0) and await terminals(store) == 1
    async with store._pool.acquire() as connection:
        for table in (TABLE_ISSUANCES, TABLE_ISSUANCE_TERMINALS):
            encoded = await connection.fetchval(
                f"SELECT coalesce(string_agg(row_to_json(t)::text, ''), '') FROM {store.schema}.{table} t",
            )
            assert token not in encoded


@pytest.mark.asyncio
async def test_fresh_interpreter_and_new_pool_recover_original_terminal_reference(store):
    bound, custody = plan(store), NoCustody()
    first = await prepare(store, custody, bound)
    retired = await retire(store, custody, terminal(bound))
    source = """
import asyncio, json, os, sys
import asyncpg
from types import SimpleNamespace
from kdcube_ai_app.auth.bundle.session_store import PostgresBundleSessionStore
from kdcube_ai_app.auth.bundle.session_planned_issuance import TerminalIssuanceContext
async def main():
    raw = json.load(sys.stdin)
    pool = await asyncpg.create_pool(os.environ["KDCUBE_TEST_POSTGRES_DSN"], min_size=1, max_size=1)
    try:
        store = PostgresBundleSessionStore(pg_pool=pool, tenant=raw["tenant"], project=raw["project"])
        original = await store.retire_issuance(TerminalIssuanceContext.from_context(SimpleNamespace(
            plan=SimpleNamespace(**raw), state="aborted", slot_outcome="released",
            receipt_digest="", token_sha256="",
        )))
        print(json.dumps({"identity": original.identity, "secret_ref": original.secret_ref}))
    finally:
        await pool.close()
asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", source, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, error = await asyncio.wait_for(process.communicate(json.dumps(vars(bound)).encode()), 10)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    assert process.returncode == 0, "separate-interpreter retirement failed"
    assert json.loads(output) == {"identity": retired.identity, "secret_ref": first.secret_ref}
    assert b"Traceback" not in error


@pytest.mark.asyncio
async def test_terminal_namespace_refusal_precedes_storage(store):
    bound, custody = plan(store, tenant="another-tenant"), NoCustody()
    class ForbiddenStore:
        async def retire_issuance(self, context):
            pytest.fail("namespace mismatch reached storage")
    from kdcube_ai_app.auth.bundle.session_planned_issuer import retire_prepared_bound_session
    with pytest.raises(SessionIssuanceRefused, match="^issuance_namespace_mismatch$"):
        await retire_prepared_bound_session(
            terminal(bound), tenant=store.tenant, project=store.project,
            store=ForbiddenStore(), custody=custody,
        )
    assert not custody.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError, OSError])
async def test_retirement_store_failure_is_finite_and_does_not_touch_custody(store, failure):
    class Unavailable:
        def __getattr__(self, name):
            return getattr(store, name)
        async def retire_issuance(self, context):
            raise failure("synthetic non-public failure")
    custody = NoCustody()
    with pytest.raises(SessionIssuanceRefused, match="^issuance_store_unavailable$") as captured:
        await authority(Unavailable()).retire_prepared_bound_session(terminal(plan(store)), custody=custody)
    assert captured.value.__suppress_context__ and not custody.calls
