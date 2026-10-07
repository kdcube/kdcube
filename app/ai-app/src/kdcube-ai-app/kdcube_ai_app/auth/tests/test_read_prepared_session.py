"""Original prepared-session reads use real PG and have no signer/custody port."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceRefused
from kdcube_ai_app.auth.bundle.session_planned_issuer import read_prepared_bound_session
from kdcube_ai_app.auth.bundle.session_planned_issuance import PreparedSessionSnapshot
from kdcube_ai_app.auth.bundle.session_schema import TABLE_ISSUANCES
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts, store
from kdcube_ai_app.auth.tests.test_bound_session_issuer import MemoryCustody, authority
from kdcube_ai_app.auth.tests.test_planned_session_issuer import applied, plan, prepare


async def read(store, bound, **changes):
    return await read_prepared_bound_session(
        bound, tenant=store.tenant, project=store.project,
        store=SimpleNamespace(read_issuance=store.read_issuance),
        user_id=changes.get("user_id", bound.credential_subject),
        roles=changes.get("roles", ["delegated-client"]),
        permissions=changes.get("permissions", ["records:read"]),
    )


@pytest.mark.asyncio
async def test_missing_original_read_never_provisions_or_prepares(store):
    with pytest.raises(SessionIssuanceRefused, match="^issuance_reservation_missing$"):
        await read(store, plan(store))
    assert await counts(store) == (0, 0, 0)


@pytest.mark.asyncio
async def test_public_fresh_authority_read_preserves_original_without_signing(store):
    bound, custody = plan(store), MemoryCustody()
    first = await prepare(store, custody, bound)
    fresh = authority(store)
    async def forbidden():
        pytest.fail("read-only original recovery resolved signing key")
    fresh._resolve_secret = forbidden
    one = await fresh.read_prepared_bound_session(
        bound, user_id=bound.credential_subject, roles=["delegated-client"], permissions=["records:read"])
    two = await read(store, bound)
    assert type(one) is PreparedSessionSnapshot and one == two
    assert one.receipt.session_id == first.session_id
    assert one.receipt.secret_ref == first.secret_ref
    assert one.receipt.bearer_sha256 == first.bearer_sha256
    assert one.receipt.outcome == "recovered" and one.issued_at > 0
    assert await counts(store) == (1, 1, 0) and custody.created == 1
    assert await custody.get(first.secret_ref) not in repr(one)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("actor", "other"), ("client_id", "other"), ("intent_digest", "0" * 64),
    ("target_incarnation", "0" * 64), ("effect_digest", "0" * 64),
    ("card_revision", 3), ("expires_at", None), ("delivery_deadline", None),
])
async def test_changed_original_plan_read_refuses_before_return(store, field, value):
    bound, custody = plan(store, cap_expires_at=int(time.time()) + 7200), MemoryCustody()
    await prepare(store, custody, bound)
    changed = SimpleNamespace(**{**vars(bound), field: getattr(bound, field) + 1 if value is None else value})
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await read(store, changed)
    assert await counts(store) == (1, 1, 0) and custody.created == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,reason", [
    ({"permissions": ["records:write"]}, "issuance_identity_conflict"),
    ({"roles": ["admin"]}, "issuance_identity_conflict"),
    ({"user_id": "integration:other"}, "issuance_user_invalid"),
])
async def test_changed_grant_inputs_read_refuses(store, changes, reason):
    bound, custody = plan(store), MemoryCustody()
    await prepare(store, custody, bound)
    with pytest.raises(SessionIssuanceRefused, match="^" + reason + "$"):
        await read(store, bound, **changes)
    assert custody.created == 1 and await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_committed_read_does_not_reapply_changed_user_permissions(store):
    bound, custody = plan(store), MemoryCustody()
    first = await prepare(store, custody, bound)
    await authority(store).activate_prepared_bound_session(applied(bound, first), custody=custody)
    await store.register_user(sub=bound.credential_subject, updates={"permissions": ["newer:grant"]}, now=int(time.time()))
    snapshot = await read(store, bound)
    assert snapshot.receipt.bearer_sha256 == first.bearer_sha256
    assert (await store.get_user(bound.credential_subject))["permissions"] == ["newer:grant"]
    assert await counts(store) == (1, 1, 1) and custody.created == 1


@pytest.mark.asyncio
async def test_malformed_original_signing_time_refuses_without_repair(store):
    bound, custody = plan(store), MemoryCustody()
    await prepare(store, custody, bound)
    async with store._pool.acquire() as connection:
        await connection.execute(f"UPDATE {store.schema}.{TABLE_ISSUANCES} SET session_record=jsonb_set(session_record, '{{iat}}', 'true'::jsonb)")
    with pytest.raises(SessionIssuanceRefused, match="^issuance_record_invalid$"):
        await read(store, bound)
    assert custody.created == 1


@pytest.mark.asyncio
async def test_original_read_rejects_other_namespace_before_store(store):
    bound = plan(store)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_namespace_mismatch$"):
        await read_prepared_bound_session(bound, tenant="other", project=store.project,
            store=object(), user_id=bound.credential_subject, roles=[], permissions=[])


@pytest.mark.asyncio
async def test_terminal_original_read_never_returns_retired_receipt(store):
    from kdcube_ai_app.auth.bundle.session_planned_issuance import TerminalIssuanceContext
    class Custody(MemoryCustody):
        async def delete(self, *, secret_ref):
            self.values.pop(secret_ref, None)
    bound, custody = plan(store), Custody()
    await prepare(store, custody, bound)
    terminal = TerminalIssuanceContext.from_context(SimpleNamespace(
        plan=bound, state="aborted", slot_outcome="released", receipt_digest="", token_sha256=""))
    await authority(store).retire_prepared_bound_session(terminal, custody=custody)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await read(store, bound)


@pytest.mark.asyncio
async def test_committed_replay_read_after_reservation_deadline_keeps_original(store, monkeypatch):
    from kdcube_ai_app.auth.bundle import session_planned_issuer
    bound, custody = plan(store), MemoryCustody()
    first = await prepare(store, custody, bound)
    monkeypatch.setattr(session_planned_issuer.time, "time", lambda: bound.reserved_until + 1)
    snapshot = await read(store, bound)
    assert snapshot.receipt.bearer_sha256 == first.bearer_sha256
    assert snapshot.context.expires_at == bound.expires_at and custody.created == 1


@pytest.mark.asyncio
async def test_original_read_expired_access_refuses_without_renewal(store, monkeypatch):
    from kdcube_ai_app.auth.bundle import session_planned_issuer
    bound, custody = plan(store), MemoryCustody()
    await prepare(store, custody, bound)
    monkeypatch.setattr(session_planned_issuer.time, "time", lambda: bound.expires_at)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_expired$"):
        await read(store, bound)
    assert custody.created == 1 and await counts(store) == (1, 1, 0)
