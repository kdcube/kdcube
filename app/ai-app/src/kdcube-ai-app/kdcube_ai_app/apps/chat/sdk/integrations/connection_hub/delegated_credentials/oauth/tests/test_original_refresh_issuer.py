"""Distinct refresh originals: real PostgreSQL, synthetic signing key; no bearer custody."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest
import pytest_asyncio

from connection_hub.delegated_credentials.oauth_issuance import OAuthIssuanceResult, SlotOutcome
from kdcube_ai_app.auth.AuthManager import AuthenticationError
from kdcube_ai_app.auth.bundle import BundleSessionAuthManager
from kdcube_ai_app.auth.bundle.session_planned_issuance import TerminalIssuanceContext
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts, store
from kdcube_ai_app.auth.tests.test_bound_session_issuer import authority
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_grants import _plan
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import OriginalExchangeRefused
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_store import PostgresOriginalRefreshStore, TABLE_REFRESH
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_issuer import HmacOriginalRefreshSigner, OriginalRefreshIssuer


class NoCustody:
    """Any custody I/O fails the test: the refresh bearer is never stored."""
    def __getattr__(self, name):
        pytest.fail(f"refresh issuance touched custody.{name}")


@pytest_asyncio.fixture
async def refresh(store):
    r = SimpleNamespace(plan=_plan(tenant=store.tenant, project=store.project), signs=0,
                        key=b"unit-original-refresh-key-32-bytes!")
    r.db = PostgresOriginalRefreshStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    await r.db.ensure_schema()
    await r.db.ensure_schema()
    async def key():
        r.signs += 1
        return r.key
    r.signer = HmacOriginalRefreshSigner(store.tenant, store.project, key)
    r.issuer = OriginalRefreshIssuer(store=r.db, custody=NoCustody(), signer=r.signer,
                                    card_kind="automation", ttl_seconds=180 * 86400)
    return r


def result(r, original, *, state="committed", outcome="applied"):
    return OAuthIssuanceResult(transaction_id=r.plan.transaction_id, intent_digest=r.plan.intent_digest,
        state=state, access_id=r.plan.access_id,
        card_revision=r.plan.candidate_revision if state == "committed" else r.plan.base_revision,
        expires_at=r.plan.expires_at, delivery_deadline=r.plan.delivery_deadline,
        receipt_digest="f" * 64 if state == "committed" else "",
        per_slot={"refresh": SlotOutcome(outcome, r.plan.effect_digests["refresh"], original.bearer_sha256)})


def sha(bearer):
    return hashlib.sha256(bearer.encode()).hexdigest()


async def rows(r):
    async with r.db._db._connection() as connection:
        return await connection.fetch(f"SELECT * FROM {r.db.schema}.{TABLE_REFRESH}")


@pytest.mark.asyncio
async def test_original_refresh_replay_preserves_input_reference_hash_and_expiry(store, refresh):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    bearer = await refresh.issuer.bearer(plan=refresh.plan)
    later = await refresh.issuer.prepare(plan=refresh.plan)
    assert later == first and sha(bearer) == first.bearer_sha256
    assert await refresh.issuer.bearer(plan=refresh.plan) == bearer
    assert first.context.expires_at == refresh.plan.expires_at
    assert await counts(store) == (0, 0, 0)
    assert [row["bearer_sha256"] for row in await rows(refresh)] == [first.bearer_sha256]


@pytest.mark.asyncio
async def test_refresh_cannot_authenticate_as_bundle_access_even_with_prefix_rewritten(store, refresh):
    await refresh.issuer.prepare(plan=refresh.plan)
    bearer = await refresh.issuer.bearer(plan=refresh.plan)
    assert bearer.startswith("krt1.")
    for candidate in (bearer, "kst1." + bearer.split(".", 1)[1]):
        with pytest.raises(AuthenticationError):
            await BundleSessionAuthManager(authority=authority(store)).authenticate(candidate)
    assert await counts(store) == (0, 0, 0)


@pytest.mark.asyncio
async def test_original_refresh_read_only_recovery_uses_no_key_or_custody(store, refresh):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    signs = refresh.signs
    db = PostgresOriginalRefreshStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    async def forbidden():
        pytest.fail("read-only refresh recovery resolved key")
    restarted = OriginalRefreshIssuer(store=db, custody=NoCustody(),
        signer=HmacOriginalRefreshSigner(store.tenant, store.project, forbidden),
        card_kind="automation", ttl_seconds=180 * 86400)
    assert await restarted.read(plan=refresh.plan) == first
    assert refresh.signs == signs


@pytest.mark.asyncio
async def test_read_only_refresh_with_hash_but_reserved_state_is_unsealed(refresh):
    original = await refresh.db.reserve(plan=refresh.plan, card_kind="automation", ttl_seconds=180 * 86400)
    original = await refresh.db.seal(original, "a" * 64)
    assert original.state == "reserved" and original.bearer_sha256 is not None
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_unsealed$"):
        await refresh.issuer.read(plan=refresh.plan)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_unsealed$"):
        await refresh.issuer.bearer(plan=refresh.plan)
    assert refresh.signs == 0


@pytest.mark.asyncio
async def test_resigned_refresh_must_match_sealed_original_digest(refresh):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    async with refresh.db._db._connection() as connection:
        await connection.execute(f"UPDATE {refresh.db.schema}.{TABLE_REFRESH} SET bearer_sha256=$2 WHERE identity=$1",
                                 first.context.identity, "b" * 64)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_signing_mismatch$"):
        await refresh.issuer.prepare(plan=refresh.plan)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_signing_mismatch$"):
        await refresh.issuer.bearer(plan=refresh.plan)
    assert [row["bearer_sha256"] for row in await rows(refresh)] == ["b" * 64]


@pytest.mark.asyncio
async def test_original_refresh_resume_after_metadata_reservation_uses_first_claims(refresh):
    first = await refresh.db.reserve(plan=refresh.plan, card_kind="automation", ttl_seconds=180 * 86400)
    prepared = await refresh.issuer.prepare(plan=refresh.plan)
    assert prepared.claims == first.claims and prepared.secret_ref == first.secret_ref
    assert sha(await refresh.issuer.bearer(plan=refresh.plan)) == prepared.bearer_sha256


@pytest.mark.asyncio
async def test_original_refresh_lost_seal_response_recovers_same_bearer(refresh):
    real, lost = refresh.db.seal, [True]
    async def interrupted(*args, **kwargs):
        sealed = await real(*args, **kwargs)
        if lost and not kwargs.get("ready"):
            lost.clear()
            raise TimeoutError("synthetic-lost-seal-response")
        return sealed
    refresh.db.seal = interrupted
    with pytest.raises(TimeoutError):
        await refresh.issuer.prepare(plan=refresh.plan)
    [before] = await rows(refresh)
    assert before["state"] == "reserved" and before["bearer_sha256"] is not None
    recovered = await refresh.issuer.prepare(plan=refresh.plan)
    assert recovered.bearer_sha256 == before["bearer_sha256"] and recovered.claims == refresh.db._decode(before, recovered.context).claims
    assert sha(await refresh.issuer.bearer(plan=refresh.plan)) == before["bearer_sha256"]
    assert len(await rows(refresh)) == 1


@pytest.mark.asyncio
async def test_original_refresh_concurrent_preparation_has_one_durable_input_and_bearer(refresh):
    values = await asyncio.gather(*(refresh.issuer.prepare(plan=refresh.plan) for _ in range(12)))
    assert len({item.secret_ref for item in values}) == 1
    assert len({item.receipt().bearer_sha256 for item in values}) == 1
    assert len({item.claims["sid"] for item in values}) == 1
    bearers = await asyncio.gather(*(refresh.issuer.bearer(plan=refresh.plan) for _ in range(4)))
    assert len(set(bearers)) == 1 and sha(bearers[0]) == values[0].bearer_sha256
    assert len(await rows(refresh)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("card_kind", "other"), ("ttl_seconds", 60)])
async def test_original_refresh_changed_policy_never_signs_or_replaces(refresh, field, value):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    bearer = await refresh.issuer.bearer(plan=refresh.plan)
    signs, kept = refresh.signs, getattr(refresh.issuer, field)
    setattr(refresh.issuer, field, value)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_identity_conflict$"):
        await refresh.issuer.prepare(plan=refresh.plan)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_identity_conflict$"):
        await refresh.issuer.bearer(plan=refresh.plan)
    assert refresh.signs == signs
    setattr(refresh.issuer, field, kept)
    assert await refresh.issuer.bearer(plan=refresh.plan) == bearer
    assert [row["bearer_sha256"] for row in await rows(refresh)] == [first.bearer_sha256]


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("intent_digest", "0" * 64), ("operations", ("other",)),
                                      ("card_content_hash", "0" * 64), ("delivery_deadline", 2000000000)])
async def test_original_refresh_changed_plan_never_signs(refresh, field, value):
    await refresh.issuer.prepare(plan=refresh.plan)
    signs = refresh.signs
    changed = replace(refresh.plan, **{field: value})
    with pytest.raises((OriginalExchangeRefused, ValueError)):
        await refresh.issuer.prepare(plan=changed)
    with pytest.raises((OriginalExchangeRefused, ValueError)):
        await refresh.issuer.bearer(plan=changed)
    assert refresh.signs == signs


@pytest.mark.asyncio
async def test_original_refresh_key_change_refuses_and_keeps_original_sealed_digest(refresh):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    bearer = await refresh.issuer.bearer(plan=refresh.plan)
    original_key, refresh.key = refresh.key, b"different-unit-refresh-signing-key!"
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_signing_mismatch$"):
        await refresh.issuer.prepare(plan=refresh.plan)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_signing_mismatch$"):
        await refresh.issuer.bearer(plan=refresh.plan)
    assert [row["bearer_sha256"] for row in await rows(refresh)] == [first.bearer_sha256]
    refresh.key = original_key
    assert await refresh.issuer.bearer(plan=refresh.plan) == bearer


@pytest.mark.asyncio
async def test_applied_original_refresh_replays_same_bearer_and_never_resigns_lost_fingerprint(refresh):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    bearer = await refresh.issuer.bearer(plan=refresh.plan)
    await refresh.issuer.protect_applied(plan=refresh.plan, result=result(refresh, first))
    assert (await refresh.issuer.prepare(plan=refresh.plan)).bearer_sha256 == first.bearer_sha256
    assert await refresh.issuer.bearer(plan=refresh.plan) == bearer
    async with refresh.db._db._connection() as connection:
        await connection.execute(f"UPDATE {refresh.db.schema}.{TABLE_REFRESH} SET bearer_sha256=NULL WHERE identity=$1",
                                 first.context.identity)
    signs = refresh.signs
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_record_invalid$"):
        await refresh.issuer.prepare(plan=refresh.plan)
    assert refresh.signs == signs
    assert [(row["state"], row["bearer_sha256"]) for row in await rows(refresh)] == [("applied", None)]


@pytest.mark.asyncio
async def test_original_refresh_abort_retirement_is_exact_and_no_mint(refresh):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    terminal = TerminalIssuanceContext.from_oauth_result(first.context,
        result(refresh, first, state="aborted", outcome="released"))
    await refresh.issuer.retire(plan=refresh.plan, terminal=terminal)
    await refresh.issuer.retire(plan=refresh.plan, terminal=terminal)
    signs = refresh.signs
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_terminal$"):
        await refresh.issuer.prepare(plan=refresh.plan)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_terminal$"):
        await refresh.issuer.bearer(plan=refresh.plan)
    assert refresh.signs == signs
    assert [row["state"] for row in await rows(refresh)] == ["retired"]


@pytest.mark.asyncio
async def test_retirement_never_removes_applied_refresh_original(refresh):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    bearer = await refresh.issuer.bearer(plan=refresh.plan)
    await refresh.issuer.protect_applied(plan=refresh.plan, result=result(refresh, first))
    terminal = TerminalIssuanceContext.from_oauth_result(first.context,
        result(refresh, first, outcome="superseded"))
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_already_applied$"):
        await refresh.issuer.retire(plan=refresh.plan, terminal=terminal)
    assert [row["state"] for row in await rows(refresh)] == ["applied"]
    assert await refresh.issuer.bearer(plan=refresh.plan) == bearer


@pytest.mark.asyncio
async def test_early_expiry_without_preparation_cannot_make_terminal_tombstone(refresh):
    from kdcube_ai_app.auth.bundle.session_planned_issuance import PlannedIssuanceContext
    bound = PlannedIssuanceContext.from_oauth_plan(refresh.plan, slot="refresh", expires_at=refresh.plan.expires_at)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_not_expired$"):
        await refresh.issuer.retire(plan=refresh.plan, terminal=TerminalIssuanceContext.expired(bound))
    assert await rows(refresh) == []
    prepared = await refresh.issuer.prepare(plan=refresh.plan)
    assert prepared.state == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["claims", "secret_ref"])
async def test_seal_refuses_changed_original_signing_inputs(refresh, field):
    original = await refresh.db.reserve(plan=refresh.plan, card_kind="automation", ttl_seconds=180 * 86400)
    changed = ({**original.claims, "iat": original.claims["iat"] - 1}
               if field == "claims" else "0" * 32)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_identity_conflict$"):
        await refresh.db.seal(replace(original, **{field: changed}), "a" * 64)
    assert refresh.signs == 0


@pytest.mark.asyncio
async def test_reserved_refresh_refuses_changed_durable_original_claims(refresh):
    import json
    original = await refresh.db.reserve(plan=refresh.plan, card_kind="automation", ttl_seconds=180 * 86400)
    altered = {**original.claims, "iat": original.claims["iat"] - 1}
    async with refresh.db._db._connection() as connection:
        await connection.execute(f"UPDATE {refresh.db.schema}.{TABLE_REFRESH} SET claims=$2::jsonb WHERE identity=$1",
                                 original.context.identity, json.dumps(altered))
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_record_invalid$"):
        await refresh.issuer.prepare(plan=refresh.plan)
    assert refresh.signs == 0


@pytest.mark.asyncio
async def test_legacy_unknown_refresh_original_is_not_reconstructed(refresh):
    original = await refresh.db.reserve(plan=refresh.plan, card_kind="automation", ttl_seconds=180 * 86400)
    async with refresh.db._db._connection() as connection:
        await connection.execute(f"UPDATE {refresh.db.schema}.{TABLE_REFRESH} SET original_digest=NULL WHERE identity=$1",
                                 original.context.identity)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_record_invalid$"):
        await refresh.issuer.prepare(plan=refresh.plan)
    assert refresh.signs == 0


@pytest.mark.asyncio
async def test_corrupted_original_reference_cannot_be_retired(refresh):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    terminal = TerminalIssuanceContext.from_oauth_result(first.context,
        result(refresh, first, state="aborted", outcome="released"))
    async with refresh.db._db._connection() as connection:
        await connection.execute(f"UPDATE {refresh.db.schema}.{TABLE_REFRESH} SET secret_ref=$2 WHERE identity=$1",
                                 first.context.identity, "0" * 32)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_record_invalid$"):
        await refresh.issuer.retire(plan=refresh.plan, terminal=terminal)
    assert [(row["state"], row["terminal_digest"]) for row in await rows(refresh)] == [("ready", None)]
