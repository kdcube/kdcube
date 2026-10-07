"""Distinct refresh originals: real PostgreSQL, synthetic signing key/custody."""
from __future__ import annotations

import asyncio
import hashlib
import time
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


@pytest_asyncio.fixture
async def refresh(store):
    r = SimpleNamespace(plan=_plan(tenant=store.tenant, project=store.project), signs=0, creates=0,
                        values={}, key=b"unit-original-refresh-key-32-bytes!", lost_create=False)
    r.db = PostgresOriginalRefreshStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    await r.db.ensure_schema()
    await r.db.ensure_schema()
    async def key():
        r.signs += 1
        return r.key
    class Custody:
        async def get(self, *, secret_ref):
            return r.values.get(secret_ref)
        async def create(self, *, secret_ref, value, expires_at):
            assert expires_at == r.plan.expires_at
            if secret_ref in r.values:
                return False
            r.values[secret_ref] = value
            r.creates += 1
            if r.lost_create:
                r.lost_create = False
                raise TimeoutError("synthetic-lost-create-response")
            return True
        async def delete(self, *, secret_ref):
            r.values.pop(secret_ref, None)
    r.custody = Custody()
    r.signer = HmacOriginalRefreshSigner(store.tenant, store.project, key)
    r.issuer = OriginalRefreshIssuer(store=r.db, custody=r.custody, signer=r.signer,
                                    card_kind="automation", ttl_seconds=180 * 86400)
    return r


def result(r, original, *, state="committed", outcome="applied"):
    return OAuthIssuanceResult(transaction_id=r.plan.transaction_id, intent_digest=r.plan.intent_digest,
        state=state, access_id=r.plan.access_id,
        card_revision=r.plan.candidate_revision if state == "committed" else r.plan.base_revision,
        expires_at=r.plan.expires_at, delivery_deadline=r.plan.delivery_deadline,
        receipt_digest="f" * 64 if state == "committed" else "",
        per_slot={"refresh": SlotOutcome(outcome, r.plan.effect_digests["refresh"], original.bearer_sha256)})


@pytest.mark.asyncio
async def test_original_refresh_replay_preserves_input_reference_hash_and_expiry(store, refresh):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    later = await refresh.issuer.prepare(plan=refresh.plan)
    assert later == first and refresh.signs == refresh.creates == 1
    assert first.context.expires_at == refresh.plan.expires_at
    assert await counts(store) == (0, 0, 0)
    async with store._pool.acquire() as connection:
        assert await connection.fetchval(f"SELECT count(*) FROM {refresh.db.schema}.{TABLE_REFRESH}") == 1


@pytest.mark.asyncio
async def test_refresh_cannot_authenticate_as_bundle_access_even_with_prefix_rewritten(store, refresh):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    bearer = refresh.values[first.secret_ref]
    assert bearer.startswith("krt1.")
    for candidate in (bearer, "kst1." + bearer.split(".", 1)[1]):
        with pytest.raises(AuthenticationError):
            await BundleSessionAuthManager(authority=authority(store)).authenticate(candidate)
    assert await counts(store) == (0, 0, 0)


@pytest.mark.asyncio
async def test_original_refresh_read_only_recovery_uses_no_key_or_custody(store, refresh):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    db = PostgresOriginalRefreshStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    async def forbidden():
        pytest.fail("read-only refresh recovery resolved key")
    restarted = OriginalRefreshIssuer(store=db, custody=object(),
        signer=HmacOriginalRefreshSigner(store.tenant, store.project, forbidden),
        card_kind="automation", ttl_seconds=180 * 86400)
    assert await restarted.read(plan=refresh.plan) == first
    assert refresh.signs == refresh.creates == 1


@pytest.mark.asyncio
async def test_read_only_refresh_with_hash_but_reserved_state_is_unsealed(refresh):
    original = await refresh.db.reserve(plan=refresh.plan, card_kind="automation", ttl_seconds=180 * 86400)
    original = await refresh.db.seal(original, "a" * 64)
    assert original.state == "reserved" and original.bearer_sha256 is not None
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_unsealed$"):
        await refresh.issuer.read(plan=refresh.plan)
    assert refresh.signs == refresh.creates == 0


@pytest.mark.asyncio
async def test_existing_refresh_custody_value_must_match_sealed_original_digest(refresh):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    refresh.values[first.secret_ref] = "different-synthetic-refresh"
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_custody_mismatch$"):
        await refresh.issuer.prepare(plan=refresh.plan)
    assert refresh.signs == refresh.creates == 1


@pytest.mark.asyncio
async def test_original_refresh_resume_after_metadata_reservation_uses_first_claims(refresh):
    first = await refresh.db.reserve(plan=refresh.plan, card_kind="automation", ttl_seconds=180 * 86400)
    prepared = await refresh.issuer.prepare(plan=refresh.plan)
    assert prepared.claims == first.claims and prepared.secret_ref == first.secret_ref
    assert refresh.signs == refresh.creates == 1


@pytest.mark.asyncio
async def test_original_refresh_lost_custody_response_recovers_without_second_create_or_sign(refresh):
    refresh.lost_create = True
    with pytest.raises(TimeoutError):
        await refresh.issuer.prepare(plan=refresh.plan)
    await refresh.issuer.prepare(plan=refresh.plan)
    assert refresh.signs == refresh.creates == 1


@pytest.mark.asyncio
async def test_original_refresh_concurrent_preparation_has_one_durable_input_and_bearer(refresh):
    values = await asyncio.gather(*(refresh.issuer.prepare(plan=refresh.plan) for _ in range(12)))
    assert len({item.secret_ref for item in values}) == 1
    assert len({item.receipt().bearer_sha256 for item in values}) == 1
    assert len({item.claims["sid"] for item in values}) == 1 and refresh.creates == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("card_kind", "other"), ("ttl_seconds", 60)])
async def test_original_refresh_changed_policy_never_signs_or_replaces(refresh, field, value):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    setattr(refresh.issuer, field, value)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_identity_conflict$"):
        await refresh.issuer.prepare(plan=refresh.plan)
    assert refresh.signs == refresh.creates == 1 and first.secret_ref in refresh.values


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("intent_digest", "0" * 64), ("operations", ("other",)),
                                      ("card_content_hash", "0" * 64), ("delivery_deadline", 2000000000)])
async def test_original_refresh_changed_plan_never_reads_secret_or_signs(refresh, field, value):
    await refresh.issuer.prepare(plan=refresh.plan)
    changed = replace(refresh.plan, **{field: value})
    with pytest.raises((OriginalExchangeRefused, ValueError)):
        await refresh.issuer.prepare(plan=changed)
    assert refresh.signs == refresh.creates == 1


@pytest.mark.asyncio
async def test_original_refresh_key_change_cannot_replace_original_sealed_digest(refresh):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    refresh.values.clear()
    refresh.key = b"different-unit-refresh-signing-key!"
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_commitment_mismatch$"):
        await refresh.issuer.prepare(plan=refresh.plan)
    assert refresh.creates == 1 and refresh.values == {}


@pytest.mark.asyncio
async def test_applied_original_refresh_custody_missing_never_resigns_or_recreates(refresh):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    await refresh.issuer.protect_applied(plan=refresh.plan, result=result(refresh, first))
    refresh.values.clear()
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_custody_missing$"):
        await refresh.issuer.prepare(plan=refresh.plan)
    assert refresh.signs == refresh.creates == 1


@pytest.mark.asyncio
async def test_original_refresh_abort_retirement_is_exact_and_no_mint(refresh):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    terminal = TerminalIssuanceContext.from_oauth_result(first.context,
        result(refresh, first, state="aborted", outcome="released"))
    await refresh.issuer.retire(plan=refresh.plan, terminal=terminal)
    await refresh.issuer.retire(plan=refresh.plan, terminal=terminal)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_terminal$"):
        await refresh.issuer.prepare(plan=refresh.plan)
    assert not refresh.values and refresh.signs == refresh.creates == 1


@pytest.mark.asyncio
async def test_retirement_never_removes_applied_refresh_original(refresh):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    await refresh.issuer.protect_applied(plan=refresh.plan, result=result(refresh, first))
    terminal = TerminalIssuanceContext.from_oauth_result(first.context,
        result(refresh, first, outcome="superseded"))
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_already_applied$"):
        await refresh.issuer.retire(plan=refresh.plan, terminal=terminal)
    assert first.secret_ref in refresh.values and refresh.creates == 1


@pytest.mark.asyncio
async def test_early_expiry_without_preparation_cannot_make_terminal_tombstone(refresh):
    from kdcube_ai_app.auth.bundle.session_planned_issuance import PlannedIssuanceContext
    bound = PlannedIssuanceContext.from_oauth_plan(refresh.plan, slot="refresh", expires_at=refresh.plan.expires_at)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_not_expired$"):
        await refresh.issuer.retire(plan=refresh.plan, terminal=TerminalIssuanceContext.expired(bound))
    await refresh.issuer.prepare(plan=refresh.plan)
    assert refresh.creates == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["claims", "secret_ref"])
async def test_seal_refuses_changed_original_signing_inputs(refresh, field):
    original = await refresh.db.reserve(plan=refresh.plan, card_kind="automation", ttl_seconds=180 * 86400)
    changed = ({**original.claims, "iat": original.claims["iat"] - 1}
               if field == "claims" else "0" * 32)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_identity_conflict$"):
        await refresh.db.seal(replace(original, **{field: changed}), "a" * 64)
    assert refresh.signs == refresh.creates == 0


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
    assert refresh.signs == refresh.creates == 0


@pytest.mark.asyncio
async def test_legacy_unknown_refresh_original_is_not_reconstructed(refresh):
    original = await refresh.db.reserve(plan=refresh.plan, card_kind="automation", ttl_seconds=180 * 86400)
    async with refresh.db._db._connection() as connection:
        await connection.execute(f"UPDATE {refresh.db.schema}.{TABLE_REFRESH} SET original_digest=NULL WHERE identity=$1",
                                 original.context.identity)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_record_invalid$"):
        await refresh.issuer.prepare(plan=refresh.plan)
    assert refresh.signs == refresh.creates == 0


@pytest.mark.asyncio
async def test_corrupted_custody_coordinate_cannot_be_purged(refresh):
    first = await refresh.issuer.prepare(plan=refresh.plan)
    terminal = TerminalIssuanceContext.from_oauth_result(first.context,
        result(refresh, first, state="aborted", outcome="released"))
    async with refresh.db._db._connection() as connection:
        await connection.execute(f"UPDATE {refresh.db.schema}.{TABLE_REFRESH} SET secret_ref=$2 WHERE identity=$1",
                                 first.context.identity, "0" * 32)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_record_invalid$"):
        await refresh.issuer.retire(plan=refresh.plan, terminal=terminal)
    assert first.secret_ref in refresh.values and refresh.creates == 1
