from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
import os
import signal
import sys
import time

import pytest

from kdcube_ai_app.auth.bundle import BundleSessionAuthManager, BundleSessionAuthority
from kdcube_ai_app.auth.AuthManager import AuthenticationError
from kdcube_ai_app.auth.bundle.session_bound_issuer import issue_bound_session
from kdcube_ai_app.auth.bundle.sessions import _make_token
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceRefused
from kdcube_ai_app.auth.tests._bound_session_crash_fixtures import (
    SIGNING_SECRET, ForbiddenCustody, PhasedStore,
)
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts, store
from kdcube_ai_app.auth.tests.test_bound_session_issuer import authority, context, issue, resigned


async def _child(bound, request, workdir):
    """A fresh interpreter whose cwd and TMPDIR are an empty directory, with no bytecode writes."""
    env = {**os.environ, "TMPDIR": str(workdir), "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": os.pathsep.join(path or os.getcwd() for path in sys.path)}
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "kdcube_ai_app.auth.tests._bound_session_crash_worker",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, cwd=str(workdir), env=env,
    )
    request = {"dsn": os.environ["KDCUBE_TEST_POSTGRES_DSN"], "context": bound.to_record(), **request}
    process.stdin.write((json.dumps(request) + "\n").encode())
    await process.stdin.drain()
    return process


async def kill_after_commit(store, bound, phase, workdir):
    process = await _child(bound, {"mode": "issue", "phase": phase}, workdir)
    try:
        marker = await asyncio.wait_for(process.stdout.readline(), timeout=15)
        # No child bearer or arbitrary stderr is copied into a test failure.
        assert marker == (phase + "\n").encode(), "child failed before durable boundary"
        process.kill()
        await asyncio.wait_for(process.wait(), timeout=5)
        assert process.returncode == -signal.SIGKILL
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def recover_in_fresh_process(bound, workdir):
    """Recover in a fresh interpreter; only the re-signed bearer's SHA-256 crosses stdout."""
    process = await _child(bound, {"mode": "recover"}, workdir)
    try:
        line = await asyncio.wait_for(process.stdout.readline(), timeout=15)
        await asyncio.wait_for(process.wait(), timeout=5)
        assert process.returncode == 0, "fresh recovery process failed"
        return json.loads(line)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def bearer_in_any_table(store, token):
    async with store._pool.acquire() as connection:
        # PostgreSQL truncates the long test schema identifier to 63 bytes.
        tables = await connection.fetch(
            "SELECT table_name::text FROM information_schema.tables"
            " WHERE table_schema::text = left($1::text, 63)", store.schema,
        )
        assert tables
        for table in tables:
            rows = await connection.fetch(
                f'SELECT to_jsonb(t)::text FROM {store.schema}."{table[0]}" AS t'
            )
            if any(token in row[0] for row in rows):
                return True
    return False


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["after_reservation", "after_activation"])
async def test_sigkill_fresh_process_resigns_one_original_signed_session(store, phase, tmp_path):
    bound = context(store)
    await kill_after_commit(store, bound, phase, tmp_path)
    original = await store.read_issuance(bound.identity)
    assert original is not None
    assert original.state == ("active" if phase == "after_activation" else "reserved")
    assert await counts(store) == (1, 1, int(phase == "after_activation"))
    original_hash = original.record["token_sha256"]

    recovered = await recover_in_fresh_process(bound, tmp_path)
    assert recovered == {
        "outcome": "recovered", "session_id": original.session_id,
        "bearer_sha256": original_hash, "custody_calls": 0,
    }
    assert await counts(store) == (1, 1, 1)
    assert (await store.read_issuance(bound.identity)).secret_ref == original.secret_ref
    # The fresh process re-signed the byte-identical original bearer.
    token = await resigned(store, bound)
    authenticated = await BundleSessionAuthManager(authority=authority(store)).authenticate(token)
    assert authenticated.permissions == ["records:read"]
    # No bearer was written anywhere: not in PostgreSQL, not on disk.
    assert not await bearer_in_any_table(store, token)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_reserved_original_cannot_authenticate_after_sigkill_before_activation(store, tmp_path):
    bound = context(store)
    await kill_after_commit(store, bound, "after_reservation", tmp_path)
    original = await store.read_issuance(bound.identity)
    assert original is not None and original.state == "reserved"
    assert await counts(store) == (1, 1, 0)
    assert await store.get_validation_state(original.session_id) is None
    assert (await store.get_user(original.record["sub"]))["permissions"] == []

    # Re-sign only the original synthetic bearer using the fixture's signing
    # key. Its hash proves possession of the actual reserved token, rather
    # than a malformed token that signature validation would reject.
    token = _make_token(original.record["claims"], secret=SIGNING_SECRET)
    assert hashlib.sha256(token.encode()).hexdigest() == original.record["token_sha256"]
    with pytest.raises(AuthenticationError, match="^bundle session is not active$"):
        await BundleSessionAuthManager(authority=authority(store)).authenticate(token)
    assert await counts(store) == (1, 1, 0)
    assert (await store.read_issuance(bound.identity)).state == "reserved"

    recovered = await issue(store, bound)
    assert (recovered.session_id, recovered.secret_ref, recovered.bearer_sha256) == (
        original.session_id, original.secret_ref, original.record["token_sha256"],
    )
    assert await counts(store) == (1, 1, 1)
    user = await BundleSessionAuthManager(authority=authority(store)).authenticate(token)
    assert user.permissions == ["records:read"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["after_reservation", "after_activation"])
async def test_committed_but_unknown_response_recovers_original(store, phase):
    bound = context(store)

    async def lost_response(boundary):
        if boundary == phase:
            raise TimeoutError("synthetic committed response lost")

    custody = ForbiddenCustody()
    issuer = BundleSessionAuthority(
        tenant=store.tenant, project=store.project,
        authority_store=PhasedStore(store, lost_response), secret=SIGNING_SECRET,
    )
    with pytest.raises(SessionIssuanceRefused, match="^issuance_store_unavailable$") as refused:
        await issuer.issue_bound_session(
            bound, user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], custody=custody,
        )
    assert refused.value.__suppress_context__
    original = await store.read_issuance(bound.identity)
    recovered = await issue(store, bound, custody)
    assert (recovered.session_id, recovered.secret_ref, recovered.bearer_sha256) == (
        original.session_id, original.secret_ref, original.record["token_sha256"],
    )
    assert await counts(store) == (1, 1, 1)
    assert custody.calls == []
    await resigned(store, bound)


@pytest.mark.asyncio
async def test_concurrent_issuers_activate_only_one_original_credential(store):
    bound = context(store)
    custody = ForbiddenCustody()
    outcomes = await asyncio.gather(*[issue(store, bound, custody) for _ in range(8)])
    assert len({(row.session_id, row.secret_ref, row.bearer_sha256) for row in outcomes}) == 1
    assert sum(row.outcome == "issued" for row in outcomes) == 1
    assert await counts(store) == (1, 1, 1)
    assert custody.calls == []
    token = await resigned(store, bound)
    assert hashlib.sha256(token.encode()).hexdigest() == outcomes[0].bearer_sha256


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("actor", "other-human"), ("effect_digest", "d" * 64),
    ("receipt_digest", "e" * 64), ("access_id", "other-card"),
    ("target_incarnation", 2), ("expires_at", None),
])
async def test_changed_committed_authority_refuses_before_any_signing(store, field, value):
    bound = context(store)
    first = await issue(store, bound)
    issuer = authority(store)

    async def forbidden_key():
        pytest.fail("conflict reached the signing key")

    issuer._resolve_secret = forbidden_key
    changed = replace(bound, **{field: bound.expires_at + 1 if value is None else value})
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await issuer.issue_bound_session(
            changed, user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], custody=ForbiddenCustody(),
        )
    assert (await store.read_issuance(bound.identity)).session_id == first.session_id
    assert await counts(store) == (1, 1, 1)


@pytest.mark.asyncio
async def test_active_issuance_without_signing_key_is_not_reissued(store):
    bound = context(store)
    first = await issue(store, bound)
    issuer = authority(store)

    async def unavailable_secret():
        raise RuntimeError("synthetic-signing-backend-secret")

    issuer._resolve_secret = unavailable_secret
    with pytest.raises(SessionIssuanceRefused, match="^issuance_signing_unavailable$") as refused:
        await issuer.issue_bound_session(
            bound, user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], custody=ForbiddenCustody(),
        )
    assert refused.value.__suppress_context__
    original = await store.read_issuance(bound.identity)
    assert (original.session_id, original.state) == (first.session_id, "active")
    assert original.record["token_sha256"] == first.bearer_sha256
    assert await counts(store) == (1, 1, 1)


@pytest.mark.asyncio
async def test_signing_key_change_cannot_replace_reserved_original(store, tmp_path):
    bound = context(store)
    await kill_after_commit(store, bound, "after_reservation", tmp_path)
    original = await store.read_issuance(bound.identity)
    issuer = BundleSessionAuthority(
        tenant=store.tenant, project=store.project, authority_store=store,
        secret="different-unit-session-signing-secret",
    )
    with pytest.raises(SessionIssuanceRefused, match="^issuance_signing_mismatch$"):
        await issuer.issue_bound_session(
            bound, user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], custody=ForbiddenCustody(),
        )
    assert (await store.read_issuance(bound.identity)).record == original.record
    assert (await store.read_issuance(bound.identity)).state == "reserved"
    assert await counts(store) == (1, 1, 0)


async def _reserved_after_lost_response(store, bound):
    async def lost_response(boundary):
        if boundary == "after_reservation":
            raise TimeoutError("synthetic committed response lost")

    issuer = BundleSessionAuthority(
        tenant=store.tenant, project=store.project,
        authority_store=PhasedStore(store, lost_response), secret=SIGNING_SECRET,
    )
    with pytest.raises(SessionIssuanceRefused, match="^issuance_store_unavailable$"):
        await issuer.issue_bound_session(
            bound, user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], custody=ForbiddenCustody(),
        )
    assert await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["different-synthetic-bearer", None])
async def test_failed_resign_never_activates_reserved_original(store, value):
    bound = context(store)
    await _reserved_after_lost_response(store, bound)
    signed = []

    async def wrong_sign(claims):
        signed.append(claims)
        return value

    with pytest.raises(SessionIssuanceRefused, match="^issuance_signing_mismatch$"):
        await issue_bound_session(
            bound, tenant=store.tenant, project=store.project, store=store,
            user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], sign=wrong_sign, custody=ForbiddenCustody(),
        )
    original = await store.read_issuance(bound.identity)
    # Exactly the stored claims were re-signed; no new claims or session id.
    assert signed == [original.record["claims"]]
    assert original.state == "reserved"
    assert await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_resign_outage_reason_does_not_disclose_backend_secret(store):
    bound = context(store)
    await _reserved_after_lost_response(store, bound)
    issuer = authority(store)

    async def unavailable_secret():
        raise RuntimeError("synthetic-backend-secret")

    issuer._resolve_secret = unavailable_secret
    with pytest.raises(SessionIssuanceRefused) as refused:
        await issuer.issue_bound_session(
            bound, user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], custody=ForbiddenCustody(),
        )
    assert str(refused.value) == "issuance_signing_unavailable"
    assert refused.value.__suppress_context__
    assert await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_store_read_outage_has_a_named_non_secret_refusal(store):
    class UnavailableStore(PhasedStore):
        async def read_issuance(self, identity):
            raise RuntimeError("synthetic-backend-secret")

    issuer = BundleSessionAuthority(
        tenant=store.tenant, project=store.project,
        authority_store=UnavailableStore(store, None), secret=SIGNING_SECRET,
    )
    with pytest.raises(SessionIssuanceRefused) as refused:
        await issuer.issue_bound_session(
            context(store), user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], custody=ForbiddenCustody(),
        )
    assert str(refused.value) == "issuance_store_unavailable"
    assert refused.value.__suppress_context__
    assert await counts(store) == (0, 0, 0)


@pytest.mark.asyncio
async def test_signing_backend_outage_is_named_before_reservation(store):
    issuer = authority(store)

    async def unavailable_secret():
        raise RuntimeError("synthetic-signing-backend-secret")

    issuer._resolve_secret = unavailable_secret
    with pytest.raises(SessionIssuanceRefused) as refused:
        await issuer.issue_bound_session(
            context(store), user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], custody=ForbiddenCustody(),
        )
    assert str(refused.value) == "issuance_signing_unavailable"
    assert refused.value.__suppress_context__
    assert await counts(store) == (0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["epoch", "disabled"])
async def test_user_authority_moves_after_reservation_before_activation(store, mutation):
    async def change_authority(boundary):
        if boundary == "after_reservation":
            if mutation == "epoch":
                await store.invalidate_user("integration:unit:human")
            else:
                await store.register_user(
                    sub="integration:unit:human", updates={"disabled": True}, now=int(time.time()),
                )

    custody = ForbiddenCustody()
    issuer = BundleSessionAuthority(
        tenant=store.tenant, project=store.project,
        authority_store=PhasedStore(store, change_authority), secret=SIGNING_SECRET,
    )
    bound = context(store)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_authority_moved$"):
        await issuer.issue_bound_session(
            bound, user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], custody=custody,
        )
    assert (await store.read_issuance(bound.identity)).state == "reserved"
    assert await counts(store) == (1, 1, 0)
    assert custody.calls == []


@pytest.mark.asyncio
async def test_sigkill_old_issuance_refuses_after_newer_grant_activates(store, tmp_path):
    bound = context(store)
    await kill_after_commit(store, bound, "after_reservation", tmp_path)
    old = await store.read_issuance(bound.identity)
    newer_bound = replace(bound, transaction_id="d" * 64)
    await authority(store).issue_bound_session(
        newer_bound, user_id="integration:unit:human",
        roles=["delegated-client"], permissions=["new:read", "new:write"], custody=ForbiddenCustody(),
    )
    newest_profile = await store.get_user("integration:unit:human")
    with pytest.raises(SessionIssuanceRefused, match="^issuance_authority_moved$"):
        await issue(store, bound)
    assert (await store.read_issuance(bound.identity)).session_id == old.session_id
    assert (await store.read_issuance(bound.identity)).state == "reserved"
    assert await store.get_user("integration:unit:human") == newest_profile
    assert await counts(store) == (1, 2, 1)
    token = await resigned(store, newer_bound)
    user = await BundleSessionAuthManager(authority=authority(store)).authenticate(token)
    assert user.permissions == ["new:read", "new:write"]


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_authority_moves_between_login_read_and_reservation(store, existing):
    if existing:
        await store.register_user(sub="integration:unit:human", updates={}, now=int(time.time()))

    class ChangedAfterRead(PhasedStore):
        async def get_login_state(self, sub):
            original = await store.get_login_state(sub)
            await store.register_user(
                sub=sub, updates={"roles": [], "permissions": ["newer:grant"]},
                now=int(time.time()),
            )
            return original

    issuer = BundleSessionAuthority(
        tenant=store.tenant, project=store.project,
        authority_store=ChangedAfterRead(store, None), secret=SIGNING_SECRET,
    )
    custody = ForbiddenCustody()
    with pytest.raises(SessionIssuanceRefused, match="^issuance_authority_moved$"):
        await issuer.issue_bound_session(
            context(store), user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], custody=custody,
        )
    assert (await store.get_user("integration:unit:human"))["permissions"] == ["newer:grant"]
    assert await counts(store) == (1, 0, 0)
    assert custody.calls == []


@pytest.mark.asyncio
async def test_deadline_passes_before_locked_activation_without_revival(store):
    bound = replace(context(store), expires_at=int(time.time()) + 2)
    custody = ForbiddenCustody()

    class LateActivation(PhasedStore):
        async def activate_reserved(self, identity):
            async with store._pool.acquire() as connection:
                await connection.execute(
                    "SELECT pg_sleep(GREATEST(0, $1 - extract(epoch FROM clock_timestamp())) + 0.05)",
                    bound.expires_at,
                )
            return await store.activate_reserved(identity)

    async def unused_hook(boundary):
        pass

    issuer = BundleSessionAuthority(
        tenant=store.tenant, project=store.project,
        authority_store=LateActivation(store, unused_hook), secret=SIGNING_SECRET,
    )
    with pytest.raises(SessionIssuanceRefused, match="^issuance_expired$"):
        await issuer.issue_bound_session(
            bound, user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], custody=custody,
        )
    original = await store.read_issuance(bound.identity)
    assert original.expires_at == bound.expires_at
    assert original.record["max_exp"] == bound.expires_at
    assert original.state == "reserved"
    with pytest.raises(SessionIssuanceRefused, match="^issuance_expired$"):
        await issue(store, bound, custody)
    assert await counts(store) == (1, 1, 0)
    assert custody.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["user_id", "roles", "permissions"])
@pytest.mark.parametrize("value", ["bad\ud800", "bad\nvalue", "bad\x7fvalue"])
async def test_invalid_text_refuses_with_named_reason_before_storage(kind, value):
    issuer = BundleSessionAuthority(tenant="unit", project="unit", authority_store=object())
    # A valid context can be validated without opening a real backing store.
    bound = context(type("Scope", (), {"tenant": "unit", "project": "unit"})())
    args = dict(user_id="integration:unit:human", roles=["delegated-client"],
                permissions=["records:read"], custody=ForbiddenCustody())
    args[kind] = value if kind == "user_id" else [value]
    reason = "issuance_user_invalid" if kind == "user_id" else "issuance_authority_invalid"
    with pytest.raises(SessionIssuanceRefused, match="^" + reason + "$"):
        await issuer.issue_bound_session(bound, **args)
