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
from kdcube_ai_app.auth.tests.test_bound_session_issuer import MemoryCustody, authority
from kdcube_ai_app.auth.tests.test_planned_session_issuer import (
    activate, applied, oauth_result, plan, prepare,
)


class RetirementCustody(MemoryCustody):
    """Terminal seam fixture, not encrypted-provider durability evidence."""
    def __init__(self):
        super().__init__()
        self.deleted = []
        self.retired = set()

    async def delete(self, *, secret_ref):
        self.deleted.append(secret_ref)
        self.retired.add(secret_ref)
        self.values.pop(secret_ref, None)

    async def create(self, *, secret_ref, value, expires_at):
        if secret_ref in self.retired:
            return False
        return await super().create(secret_ref=secret_ref, value=value, expires_at=expires_at)


def terminal(bound, *, state="aborted", outcome="released", token_sha256="", **changes):
    return SimpleNamespace(
        plan=bound, state=state, slot_outcome=outcome,
        receipt_digest="e" * 64 if state == "committed" else "",
        token_sha256=token_sha256, **changes,
    )


async def retire(store, custody, context):
    return await authority(store).retire_prepared_bound_session(context, custody=custody)


@pytest.mark.asyncio
async def test_terminal_api_is_present_before_any_cleanup(store):
    assert callable(getattr(authority(store), "retire_prepared_bound_session", None))


@pytest.mark.asyncio
async def test_abort_before_prepare_is_a_durable_no_mint_tombstone(store):
    bound, custody = plan(store), RetirementCustody()
    result = await retire(store, custody, terminal(bound))
    assert result.identity and result.secret_ref is None
    fresh = PostgresBundleSessionStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await prepare(fresh, custody, bound)
    assert await counts(store) == (0, 0, 0)
    assert not custody.deleted and custody.created == 0
    assert (await retire(fresh, custody, terminal(bound))).identity == result.identity


@pytest.mark.asyncio
@pytest.mark.parametrize("state,outcome", [("aborted", "released"), ("committed", "superseded")])
async def test_terminal_retirement_erases_only_inactive_original_and_never_user_authority(store, state, outcome):
    bound, custody = plan(store), RetirementCustody()
    first = await prepare(store, custody, bound)
    sibling = plan(store, transaction_id="2" * 64)
    other = await prepare(store, custody, sibling)
    await activate(store, custody, applied(sibling, other))
    before = await store.get_login_state(bound.credential_subject)
    context = terminal(bound, state=state, outcome=outcome, token_sha256=first.bearer_sha256)
    retired = await retire(store, custody, context)
    assert retired.secret_ref == first.secret_ref
    assert custody.deleted == [first.secret_ref] and first.secret_ref not in custody.values
    assert other.secret_ref in custody.values
    assert await store.get_login_state(bound.credential_subject) == before
    assert await counts(store) == (1, 2, 1)
    fresh = PostgresBundleSessionStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    again = await retire(fresh, custody, context)
    assert again.secret_ref == first.secret_ref
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await prepare(fresh, custody, bound)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await activate(fresh, custody, applied(bound, first))
    assert custody.created == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("state,outcome", [
    ("pending", "pending"), ("pending", "released"), ("committed", "applied"),
    ("aborted", "applied"), ("aborted", "superseded"), ("committed", "released"),
])
async def test_non_terminal_or_applied_result_never_authorizes_cleanup(store, state, outcome):
    bound, custody = plan(store), RetirementCustody()
    first = await prepare(store, custody, bound)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_result_not_terminal$"):
        await retire(store, custody, terminal(bound, state=state, outcome=outcome,
                                            token_sha256=first.bearer_sha256))
    assert not custody.deleted and first.secret_ref in custody.values
    await activate(store, custody, applied(bound, first))


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("client_id", "other-client"), ("access_id", "other-card"), ("target_incarnation", "0" * 64),
    ("intent_digest", "0" * 64), ("effect_digest", "0" * 64),
    ("credential_subject", "integration:other:human"), ("card_revision", 3),
])
async def test_changed_terminal_plan_refuses_before_custody(store, field, value):
    bound, custody = plan(store), RetirementCustody()
    first = await prepare(store, custody, bound)
    changed = SimpleNamespace(**{**vars(bound), field: value})
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await retire(store, custody, terminal(changed))
    assert not custody.deleted and first.secret_ref in custody.values


@pytest.mark.asyncio
async def test_wrong_terminal_token_commitment_refuses_without_deletion(store):
    bound, custody = plan(store), RetirementCustody()
    first = await prepare(store, custody, bound)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_commitment_mismatch$"):
        await retire(store, custody, terminal(bound, token_sha256="0" * 64))
    assert not custody.deleted
    await activate(store, custody, applied(bound, first))


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_kind", ["abort", "superseded", "expired"])
async def test_active_session_is_never_retired_by_original_cleanup(store, terminal_kind):
    bound, custody = plan(store), RetirementCustody()
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
    assert not custody.deleted and first.secret_ref in custody.values
    assert (await store.read_issuance(hashlib.sha256(json.dumps(
        [bound.tenant, bound.project, bound.transaction_id, bound.slot],
        separators=(",", ":"),
    ).encode()).hexdigest())).state == "active"


@pytest.mark.asyncio
async def test_expiry_cleanup_uses_pg_clock_not_caller_or_python_clock(store, monkeypatch):
    bound, custody = plan(store), RetirementCustody()
    first = await prepare(store, custody, bound)
    monkeypatch.setattr("kdcube_ai_app.auth.bundle.session_bound_issuer.time.time",
                        lambda: bound.delivery_deadline + 1)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_delivery_not_expired$"):
        await retire(store, custody, terminal(bound, state="expired", outcome="expired"))
    assert not custody.deleted and first.secret_ref in custody.values


@pytest.mark.asyncio
async def test_expired_unprepared_plan_can_retire_without_provisioning(store):
    bound, custody = plan(store, delivery_deadline=int(time.time()) - 1,
                          reserved_until=int(time.time()) - 2), RetirementCustody()
    result = await retire(store, custody, terminal(bound, state="expired", outcome="expired"))
    assert result.secret_ref is None and await counts(store) == (0, 0, 0)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await prepare(store, custody, bound)


@pytest.mark.asyncio
async def test_lost_retirement_commit_response_never_reaches_custody_then_recovers_same_ref(store):
    bound, custody = plan(store), RetirementCustody()
    first = await prepare(store, custody, bound)
    class LostCommit:
        def __getattr__(self, name):
            return getattr(store, name)
        async def retire_issuance(self, context):
            await store.retire_issuance(context)
            raise TimeoutError("synthetic lost commit")
    with pytest.raises(SessionIssuanceRefused, match="^issuance_store_unavailable$"):
        await authority(LostCommit()).retire_prepared_bound_session(terminal(bound), custody=custody)
    assert not custody.deleted and first.secret_ref in custody.values
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await prepare(store, custody, bound)
    recovered = await retire(store, custody, terminal(bound))
    assert recovered.secret_ref == first.secret_ref and not custody.values


@pytest.mark.asyncio
async def test_lost_custody_delete_response_keeps_tombstone_and_retries_same_ref(store):
    bound, custody = plan(store), RetirementCustody()
    first = await prepare(store, custody, bound)
    original_delete = custody.delete
    async def lost_delete(**kwargs):
        await original_delete(**kwargs)
        raise TimeoutError("synthetic lost delete")
    custody.delete = lost_delete
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_unavailable$"):
        await retire(store, custody, terminal(bound))
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await prepare(store, custody, bound)
    custody.delete = original_delete
    assert (await retire(store, custody, terminal(bound))).secret_ref == first.secret_ref
    assert custody.created == 1 and custody.deleted == [first.secret_ref, first.secret_ref]


@pytest.mark.asyncio
async def test_late_first_custody_write_is_fenced_and_cleaned_after_terminal_commit(store):
    bound = plan(store)
    entered, resume = asyncio.Event(), asyncio.Event()
    class DelayedFirstWrite(RetirementCustody):
        async def create(self, *, secret_ref, value, expires_at):
            entered.set()
            await resume.wait()
            # Simulate an absent-reference delete which cannot fence a first
            # provider create. The SDK must recheck its durable terminal fence.
            return await MemoryCustody.create(self, secret_ref=secret_ref, value=value,
                                              expires_at=expires_at)
    custody = DelayedFirstWrite()
    task = asyncio.create_task(prepare(store, custody, bound))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        retired = await retire(store, custody, terminal(bound))
        resume.set()
        with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
            await asyncio.wait_for(task, 5)
        assert not custody.values
        assert custody.deleted == [retired.secret_ref, retired.secret_ref]
        assert await counts(store) == (1, 1, 0)
    finally:
        resume.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_terminal_context_adapts_exact_original_hub_result_and_rejects_dicts(store):
    from kdcube_ai_app.auth.bundle.session_planned_issuance import TerminalIssuanceContext
    bound, custody = plan(store), RetirementCustody()
    first = await prepare(store, custody, bound)
    hub = oauth_result(bound, first)
    hub.state, hub.receipt_digest, hub.card_revision = "aborted", "", bound.base_revision
    hub.per_slot["access"].outcome = "released"
    context = TerminalIssuanceContext.from_oauth_result(bound, hub)
    assert (await retire(store, custody, context)).secret_ref == first.secret_ref
    with pytest.raises(SessionIssuanceRefused, match="^issuance_result_invalid$"):
        await retire(store, custody, vars(context))


@pytest.mark.asyncio
async def test_aborted_original_hub_result_with_absent_slot_fences_first_mint(store):
    from kdcube_ai_app.auth.bundle.session_planned_issuance import TerminalIssuanceContext
    bound, custody = plan(store), RetirementCustody()
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
        await prepare(store, custody, bound)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"state": {}}, {"slot_outcome": []}, {"receipt_digest": "raw-receipt"},
    {"token_sha256": True}, {"token_sha256": "not-a-hash"},
])
async def test_malformed_terminal_inputs_have_finite_refusal_before_store(store, changes):
    context = terminal(plan(store))
    vars(context).update(changes)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_result_(invalid|not_terminal)$"):
        await retire(store, RetirementCustody(), context)
    assert await counts(store) == (0, 0, 0)


@pytest.mark.asyncio
async def test_changed_terminal_receipt_cannot_replace_first_tombstone(store):
    bound, custody = plan(store), RetirementCustody()
    first = await prepare(store, custody, bound)
    await retire(store, custody, terminal(bound, state="committed", outcome="superseded",
                                         token_sha256=first.bearer_sha256))
    changed = terminal(bound, state="committed", outcome="superseded",
                       token_sha256=first.bearer_sha256)
    changed.receipt_digest = "0" * 64
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal_conflict$"):
        await retire(store, custody, changed)
    assert custody.deleted == [first.secret_ref]


@pytest.mark.asyncio
async def test_one_connection_pool_parallel_retirement_is_bounded_and_pins_one_tombstone(store):
    import asyncpg
    import os
    bound, custody = plan(store), RetirementCustody()
    first = await prepare(store, custody, bound)
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
    assert first.secret_ref not in encoded and "bearer" not in encoded
    assert await counts(store) == (1, 1, 0) and custody.created == 1


@pytest.mark.asyncio
async def test_delivery_expiry_is_rechecked_after_real_identity_lock_wait(store):
    from kdcube_ai_app.auth.bundle.session_planned_issuance import PlannedIssuanceContext
    async with store._pool.acquire() as connection:
        deadline = int(await connection.fetchval("SELECT floor(extract(epoch FROM clock_timestamp()))")) + 2
    bound, custody = plan(store, delivery_deadline=deadline, reserved_until=deadline), RetirementCustody()
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
    assert not custody.values


@pytest.mark.asyncio
async def test_retirement_with_real_file_provider_survives_restart_and_cannot_recreate(store, tmp_path):
    from kdcube_ai_app.infra.secrets.runtime_file import RuntimeFileStore
    class FileCustody:
        def __init__(self):
            self.backend = RuntimeFileStore(root=tmp_path, namespace="w585-terminal",
                                           authorized_namespaces=["w585-terminal"])
        async def create(self, **kwargs):
            return self.backend.create(**kwargs)
        async def get(self, *, secret_ref):
            return self.backend.get(secret_ref=secret_ref)
        async def delete(self, *, secret_ref):
            self.backend.delete(secret_ref=secret_ref)
    bound, custody = plan(store), FileCustody()
    first = await prepare(store, custody, bound)
    await retire(store, custody, terminal(bound))
    fresh = FileCustody()
    assert await fresh.get(secret_ref=first.secret_ref) is None
    assert not await fresh.create(secret_ref=first.secret_ref, value="synthetic-late-value",
                                  expires_at=bound.expires_at)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await prepare(store, fresh, bound)
    assert (await retire(store, fresh, terminal(bound))).secret_ref == first.secret_ref


@pytest.mark.asyncio
async def test_fresh_interpreter_and_new_pool_recover_original_terminal_reference(store):
    bound, custody = plan(store), RetirementCustody()
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
async def test_terminal_namespace_refusal_precedes_storage_or_custody(store):
    bound = plan(store, tenant="another-tenant")
    class ForbiddenStore:
        async def retire_issuance(self, context):
            pytest.fail("namespace mismatch reached storage")
    from kdcube_ai_app.auth.bundle.session_planned_issuer import retire_prepared_bound_session
    with pytest.raises(SessionIssuanceRefused, match="^issuance_namespace_mismatch$"):
        await retire_prepared_bound_session(
            terminal(bound), tenant=store.tenant, project=store.project,
            store=ForbiddenStore(), custody=RetirementCustody(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError, OSError])
async def test_retirement_store_failure_is_finite_and_does_not_touch_custody(store, failure):
    class Unavailable:
        def __getattr__(self, name):
            return getattr(store, name)
        async def retire_issuance(self, context):
            raise failure("synthetic non-public failure")
    custody = RetirementCustody()
    with pytest.raises(SessionIssuanceRefused, match="^issuance_store_unavailable$") as captured:
        await authority(Unavailable()).retire_prepared_bound_session(terminal(plan(store)), custody=custody)
    assert captured.value.__suppress_context__ and not custody.deleted
