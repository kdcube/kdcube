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
from kdcube_ai_app.auth.bundle.sessions import _make_token
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceRefused
from kdcube_ai_app.auth.bundle.session_schema import TABLE_ISSUANCES, TABLE_SESSIONS, TABLE_USERS
from kdcube_ai_app.auth.tests._bound_session_crash_fixtures import PhasedStore, PostgresTestCustody
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts, store
from kdcube_ai_app.auth.tests.test_bound_session_issuer import authority, context, issue


async def kill_after_commit(store, bound, phase):
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "kdcube_ai_app.auth.tests._bound_session_crash_worker",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    request = {
        "dsn": os.environ["KDCUBE_TEST_POSTGRES_DSN"],
        "context": bound.to_record(), "phase": phase,
    }
    try:
        process.stdin.write((json.dumps(request) + "\n").encode())
        await process.stdin.drain()
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


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["after_reservation", "after_custody", "after_activation"])
async def test_sigkill_retry_recovers_one_original_signed_session(store, phase):
    bound = context(store)
    custody = PostgresTestCustody(store)
    await custody.ensure_schema()
    await kill_after_commit(store, bound, phase)
    original = await store.read_issuance(bound.identity)
    assert original is not None
    assert original.state == ("active" if phase == "after_activation" else "reserved")
    assert await counts(store) == (1, 1, int(phase == "after_activation"))
    assert await custody.count() == int(phase != "after_reservation")
    original_hash = original.record["token_sha256"]

    recovered = await issue(store, custody, bound)
    assert recovered.outcome == "recovered"
    assert recovered.session_id == original.session_id
    assert recovered.secret_ref == original.secret_ref
    assert recovered.bearer_sha256 == original_hash
    assert await counts(store) == (1, 1, 1)
    assert await custody.count() == 1
    token = await custody.get(secret_ref=recovered.secret_ref)
    assert hashlib.sha256(token.encode()).hexdigest() == original_hash
    authenticated = await BundleSessionAuthManager(authority=authority(store)).authenticate(token)
    assert authenticated.permissions == ["records:read"]
    # The three production authority tables contain no original bearer.
    async with store._pool.acquire() as connection:
        for table in (TABLE_USERS, TABLE_ISSUANCES, TABLE_SESSIONS):
            rows = await connection.fetch(f"SELECT to_jsonb(t)::text FROM {store.schema}.{table} AS t")
            assert all(token not in row[0] for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["after_reservation", "after_custody"])
async def test_reserved_original_cannot_authenticate_after_sigkill_before_activation(store, phase):
    bound = context(store)
    custody = PostgresTestCustody(store)
    await custody.ensure_schema()
    await kill_after_commit(store, bound, phase)
    original = await store.read_issuance(bound.identity)
    assert original is not None and original.state == "reserved"
    assert await counts(store) == (1, 1, 0)
    assert await store.get_validation_state(original.session_id) is None
    assert (await store.get_user(original.record["sub"]))["permissions"] == []

    # Reconstruct only the original synthetic bearer using the fixture's
    # signing key. Its hash proves possession of the actual reserved token,
    # rather than a malformed token that signature validation would reject.
    token = _make_token(original.record["claims"], secret="unit-session-signing-secret")
    assert hashlib.sha256(token.encode()).hexdigest() == original.record["token_sha256"]
    assert await custody.get(secret_ref=original.secret_ref) == (
        token if phase == "after_custody" else None
    )
    with pytest.raises(AuthenticationError, match="^bundle session is not active$"):
        await BundleSessionAuthManager(authority=authority(store)).authenticate(token)
    assert await counts(store) == (1, 1, 0)
    assert (await store.read_issuance(bound.identity)).state == "reserved"

    recovered = await issue(store, custody, bound)
    assert (recovered.session_id, recovered.secret_ref, recovered.bearer_sha256) == (
        original.session_id, original.secret_ref, original.record["token_sha256"],
    )
    assert await counts(store) == (1, 1, 1)
    user = await BundleSessionAuthManager(authority=authority(store)).authenticate(token)
    assert user.permissions == ["records:read"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["after_reservation", "after_custody", "after_activation"])
async def test_committed_but_unknown_response_recovers_original(store, phase):
    bound = context(store)

    async def lost_response(boundary):
        if boundary == phase:
            raise TimeoutError("synthetic committed response lost")

    custody = PostgresTestCustody(store, hook=lost_response)
    await custody.ensure_schema()
    issuer = BundleSessionAuthority(
        tenant=store.tenant, project=store.project,
        authority_store=PhasedStore(store, lost_response), secret="unit-session-signing-secret",
    )
    expected = "issuance_custody_unavailable" if phase == "after_custody" else "issuance_store_unavailable"
    with pytest.raises(SessionIssuanceRefused, match="^" + expected + "$") as refused:
        await issuer.issue_bound_session(
            bound, user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], custody=custody,
        )
    assert refused.value.__suppress_context__
    original = await store.read_issuance(bound.identity)
    recovered = await issue(store, PostgresTestCustody(store), bound)
    assert (recovered.session_id, recovered.secret_ref, recovered.bearer_sha256) == (
        original.session_id, original.secret_ref, original.record["token_sha256"],
    )
    assert await counts(store) == (1, 1, 1)
    assert await custody.count() == 1


@pytest.mark.asyncio
async def test_concurrent_issuers_activate_only_one_original_credential(store):
    bound = context(store)
    custody = PostgresTestCustody(store)
    await custody.ensure_schema()
    outcomes = await asyncio.gather(*[issue(store, custody, bound) for _ in range(8)])
    assert len({(row.session_id, row.secret_ref, row.bearer_sha256) for row in outcomes}) == 1
    assert sum(row.outcome == "issued" for row in outcomes) == 1
    assert await counts(store) == (1, 1, 1)
    assert await custody.count() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("actor", "other-human"), ("effect_digest", "d" * 64),
    ("receipt_digest", "e" * 64), ("access_id", "other-card"),
    ("target_incarnation", 2), ("expires_at", None),
])
async def test_changed_committed_authority_refuses_before_any_custody_access(store, field, value):
    bound = context(store)
    custody = PostgresTestCustody(store)
    await custody.ensure_schema()
    first = await issue(store, custody, bound)

    class ForbiddenCustody:
        async def create(self, **kwargs):
            pytest.fail("conflict reached custody mutation")

        async def get(self, **kwargs):
            pytest.fail("conflict reached custody read")

    changed = replace(bound, **{field: bound.expires_at + 1 if value is None else value})
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await issue(store, ForbiddenCustody(), changed)
    assert (await store.read_issuance(bound.identity)).session_id == first.session_id
    assert await counts(store) == (1, 1, 1)
    assert await custody.count() == 1


@pytest.mark.asyncio
async def test_active_issuance_missing_custody_is_not_recreated(store):
    bound = context(store)
    custody = PostgresTestCustody(store)
    await custody.ensure_schema()
    first = await issue(store, custody, bound)
    async with store._pool.acquire() as connection:
        await connection.execute(f"DELETE FROM {store.schema}.test_issuance_custody")
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_missing$"):
        await issue(store, custody, bound)
    assert (await store.read_issuance(bound.identity)).session_id == first.session_id
    assert await counts(store) == (1, 1, 1)
    assert await custody.count() == 0


@pytest.mark.asyncio
async def test_signing_key_change_cannot_replace_uncustodied_original(store):
    bound = context(store)
    custody = PostgresTestCustody(store)
    await custody.ensure_schema()
    await kill_after_commit(store, bound, "after_reservation")
    original = await store.read_issuance(bound.identity)
    issuer = BundleSessionAuthority(
        tenant=store.tenant, project=store.project, authority_store=store,
        secret="different-unit-session-signing-secret",
    )
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_unrecoverable$"):
        await issuer.issue_bound_session(
            bound, user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], custody=custody,
        )
    assert (await store.read_issuance(bound.identity)).record == original.record
    assert await counts(store) == (1, 1, 0)
    assert await custody.count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["different-synthetic-bearer", None])
async def test_failed_readback_never_activates_candidate(store, value):
    custody = PostgresTestCustody(store)
    await custody.ensure_schema()

    class WrongReadback:
        reads = 0

        async def create(self, **kwargs):
            return await custody.create(**kwargs)

        async def get(self, *, secret_ref):
            self.reads += 1
            return None if self.reads == 1 else value

    reason = "issuance_custody_missing" if value is None else "issuance_custody_mismatch"
    with pytest.raises(SessionIssuanceRefused, match="^" + reason + "$"):
        await issue(store, WrongReadback(), context(store))
    assert await counts(store) == (1, 1, 0)
    assert await custody.count() == 1


@pytest.mark.asyncio
async def test_custody_outage_reason_does_not_disclose_backend_secret(store):
    class UnavailableCustody:
        async def create(self, **kwargs):
            pytest.fail("create cannot follow an unavailable read")

        async def get(self, *, secret_ref):
            raise RuntimeError("synthetic-backend-secret")

    with pytest.raises(SessionIssuanceRefused) as refused:
        await issue(store, UnavailableCustody(), context(store))
    assert str(refused.value) == "issuance_custody_unavailable"
    assert refused.value.__suppress_context__
    assert await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_store_read_outage_has_a_named_non_secret_refusal(store):
    class UnavailableStore(PhasedStore):
        async def read_issuance(self, identity):
            raise RuntimeError("synthetic-backend-secret")

    issuer = BundleSessionAuthority(
        tenant=store.tenant, project=store.project,
        authority_store=UnavailableStore(store, None), secret="unit-session-signing-secret",
    )
    with pytest.raises(SessionIssuanceRefused) as refused:
        await issuer.issue_bound_session(
            context(store), user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], custody=PostgresTestCustody(store),
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
            permissions=["records:read"], custody=PostgresTestCustody(store),
        )
    assert str(refused.value) == "issuance_signing_unavailable"
    assert refused.value.__suppress_context__
    assert await counts(store) == (0, 0, 0)


@pytest.mark.asyncio
async def test_existing_runtime_secret_adapter_keyword_only_contract(store):
    from kdcube_ai_app.infra.secrets.ephemeral import KDCubeEphemeralSecretStore
    from kdcube_ai_app.infra.secrets.manager import InMemorySecretsManager

    # This checks the production adapter's call signature only. In-memory
    # manager use here is not a durable backend qualification or fallback.
    custody = KDCubeEphemeralSecretStore(InMemorySecretsManager(), namespace="unit-bound-issuance")
    first = await issue(store, custody, context(store))
    token = await custody.get(secret_ref=first.secret_ref)
    assert hashlib.sha256(token.encode()).hexdigest() == first.bearer_sha256
    assert await counts(store) == (1, 1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["epoch", "disabled"])
async def test_user_authority_moves_after_custody_before_activation(store, mutation):
    async def change_authority(boundary):
        if boundary == "after_custody":
            if mutation == "epoch":
                await store.invalidate_user("integration:unit:human")
            else:
                await store.register_user(
                    sub="integration:unit:human", updates={"disabled": True}, now=int(time.time()),
                )

    custody = PostgresTestCustody(store, hook=change_authority)
    await custody.ensure_schema()
    bound = context(store)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_authority_moved$"):
        await issue(store, custody, bound)
    assert (await store.read_issuance(bound.identity)).state == "reserved"
    assert await counts(store) == (1, 1, 0)
    assert await custody.count() == 1


@pytest.mark.asyncio
async def test_sigkill_old_issuance_refuses_after_newer_grant_activates(store):
    bound = context(store)
    custody = PostgresTestCustody(store)
    await custody.ensure_schema()
    await kill_after_commit(store, bound, "after_reservation")
    old = await store.read_issuance(bound.identity)
    newer = await authority(store).issue_bound_session(
        replace(bound, transaction_id="d" * 64), user_id="integration:unit:human",
        roles=["delegated-client"], permissions=["new:read", "new:write"], custody=custody,
    )
    newest_profile = await store.get_user("integration:unit:human")
    with pytest.raises(SessionIssuanceRefused, match="^issuance_authority_moved$"):
        await issue(store, custody, bound)
    assert (await store.read_issuance(bound.identity)).session_id == old.session_id
    assert (await store.read_issuance(bound.identity)).state == "reserved"
    assert await store.get_user("integration:unit:human") == newest_profile
    assert await counts(store) == (1, 2, 1)
    token = await custody.get(secret_ref=newer.secret_ref)
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
        authority_store=ChangedAfterRead(store, None), secret="unit-session-signing-secret",
    )
    custody = PostgresTestCustody(store)
    await custody.ensure_schema()
    with pytest.raises(SessionIssuanceRefused, match="^issuance_authority_moved$"):
        await issuer.issue_bound_session(
            context(store), user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], custody=custody,
        )
    assert (await store.get_user("integration:unit:human"))["permissions"] == ["newer:grant"]
    assert await counts(store) == (1, 0, 0)
    assert await custody.count() == 0


@pytest.mark.asyncio
async def test_deadline_passes_before_locked_activation_without_revival(store):
    bound = replace(context(store), expires_at=int(time.time()) + 2)
    custody = PostgresTestCustody(store)
    await custody.ensure_schema()

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
        authority_store=LateActivation(store, unused_hook), secret="unit-session-signing-secret",
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
        await issue(store, custody, bound)
    assert await counts(store) == (1, 1, 0)
    assert await custody.count() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["user_id", "roles", "permissions"])
@pytest.mark.parametrize("value", ["bad\ud800", "bad\nvalue", "bad\x7fvalue"])
async def test_invalid_text_refuses_with_named_reason_before_storage(kind, value):
    issuer = BundleSessionAuthority(tenant="unit", project="unit", authority_store=object())
    # A valid context can be validated without opening a real backing store.
    bound = context(type("Scope", (), {"tenant": "unit", "project": "unit"})())
    args = dict(user_id="integration:unit:human", roles=["delegated-client"],
                permissions=["records:read"], custody=object())
    args[kind] = value if kind == "user_id" else [value]
    reason = "issuance_user_invalid" if kind == "user_id" else "issuance_authority_invalid"
    with pytest.raises(SessionIssuanceRefused, match="^" + reason + "$"):
        await issuer.issue_bound_session(bound, **args)
