"""Actual issuer composition: real PG, synthetic signing keys; no bearer custody.

Both bearers are re-signed from the claims PostgreSQL stores and must match the
sealed fingerprints. Deployed key storage and restarts of a hosting process are
not qualified here.
"""
from __future__ import annotations

import hashlib
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

from connection_hub.delegated_credentials.automation_access import AutomationAccessService
from connection_hub.delegated_credentials.oauth_issuance import OAuthIssuanceResult, SlotOutcome
from kdcube_ai_app.auth.bundle import BundleSessionAuthManager
from kdcube_ai_app.auth.AuthManager import AuthenticationError
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceRefused
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts, store
from kdcube_ai_app.auth.tests.test_bound_session_issuer import authority
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_grants import _plan
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import OriginalExchangeRefused
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_pair_provider import OriginalCredentialPairProvider
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_store import PostgresOriginalRefreshStore, TABLE_REFRESH
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_issuer import HmacOriginalRefreshSigner


class NoCustody:
    """Any custody I/O fails the test: neither bearer is stored."""
    def __getattr__(self, name):
        pytest.fail(f"original pair touched custody.{name}")


@pytest_asyncio.fixture
async def paired(store):
    r = SimpleNamespace(plan=_plan(tenant=store.tenant, project=store.project), expiry=int(time.time()) + 3600,
                        signs=0, no_prepare=False, session_secret=None,
                        refresh_key=b"unit-original-refresh-key-32-bytes!")
    r.db = PostgresOriginalRefreshStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    await r.db.ensure_schema()
    async def key():
        r.signs += 1
        if r.refresh_key is None:
            raise TimeoutError("synthetic-refresh-key-unavailable")
        return r.refresh_key
    r.signer = HmacOriginalRefreshSigner(store.tenant, store.project, key)
    def factory(**kwargs):
        assert kwargs == {"tenant": store.tenant, "project": store.project}
        resolved = authority(store)
        if r.session_secret is not None:
            resolved._secret = r.session_secret
        if r.no_prepare:
            async def forbidden(*args, **kwargs):
                pytest.fail("read-only pair replay prepared or resolved a signing key")
            resolved.prepare_bound_session = resolved._resolve_secret = forbidden
        return resolved
    r.factory = factory
    r.provider = provider(r)
    return r


def provider(r, *, refresh_store=None, signer=None):
    return OriginalCredentialPairProvider(refresh_store=refresh_store or r.db, custody=NoCustody(),
        custody_namespace="unit-ignored", refresh_signer=signer or r.signer, card_kind="automation",
        refresh_ttl_seconds=180 * 86400, authority_factory=r.factory)


async def refresh_rows(r):
    async with r.db._db._connection() as connection:
        return [tuple(row) for row in await connection.fetch(
            f"SELECT identity, claims, state, bearer_sha256, terminal_digest FROM {r.db.schema}.{TABLE_REFRESH}")]


def applied(r, pair, *, state="committed", outcomes=None):
    outcomes = outcomes or {slot: "applied" for slot in r.plan.slots}
    return OAuthIssuanceResult(transaction_id=r.plan.transaction_id, intent_digest=r.plan.intent_digest,
        state=state, access_id=r.plan.access_id,
        card_revision=r.plan.candidate_revision if state == "committed" else r.plan.base_revision,
        expires_at=r.plan.expires_at, delivery_deadline=r.plan.delivery_deadline,
        receipt_digest="f" * 64 if state == "committed" else "",
        per_slot={slot: SlotOutcome(outcomes[slot], r.plan.effect_digests[slot], pair[slot].receipt.bearer_sha256)
                  for slot in r.plan.slots})


@pytest.mark.asyncio
async def test_concrete_original_pair_has_stable_hub_records_and_read_only_recovery(store, paired):
    first = await paired.provider.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    assert await counts(store) == (1, 1, 0) and len(await refresh_rows(paired)) == 1
    pinned = paired.plan.to_dict()
    pinned["intent"] = {"candidate": {"card_kind": "automation"}}
    for slot in paired.plan.slots:
        assert AutomationAccessService._checked_issuance_record(pinned, slot, first[slot].record) == first[slot].record
    paired.no_prepare = True
    signs = paired.signs
    again = await paired.provider.read_pair(plan=paired.plan, access_expires_at=paired.expiry)
    assert first == again and paired.signs == signs
    assert first["access"].record["credential"]["iat"] > 0
    assert first["refresh"].record["credential"]["exp"] == paired.plan.expires_at


@pytest.mark.asyncio
async def test_concrete_original_pair_applied_result_activates_only_access(store, paired):
    pair = await paired.provider.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    result = applied(paired, pair)
    one = await paired.provider.activate_access(plan=paired.plan, result=result, credential=pair["access"])
    two = await paired.provider.activate_access(plan=paired.plan, result=result, credential=pair["access"])
    assert one == two and await counts(store) == (1, 1, 1)
    bearers = await paired.provider.bearers(plan=paired.plan, result=result, pair=pair)
    for slot, bearer in bearers.items():
        assert hashlib.sha256(bearer.encode()).hexdigest() == pair[slot].receipt.bearer_sha256
        if slot == "access":
            user = await BundleSessionAuthManager(authority=authority(store)).authenticate(bearer)
            assert user.sub == paired.plan.credential_subject
        else:
            with pytest.raises(AuthenticationError):
                await BundleSessionAuthManager(authority=authority(store)).authenticate(bearer)


@pytest.mark.asyncio
async def test_pair_bearers_replay_identical_to_first_issue(store, paired):
    pair = await paired.provider.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    result = applied(paired, pair)
    await paired.provider.activate_access(plan=paired.plan, result=result, credential=pair["access"])
    first = await paired.provider.bearers(plan=paired.plan, result=result, pair=pair)
    assert set(first) == {"access", "refresh"} and first["access"] != first["refresh"]
    # The first issue is what the fingerprints sealed at preparation commit to.
    assert {slot: hashlib.sha256(value.encode()).hexdigest() for slot, value in first.items()} == {
        slot: value.token_sha256 for slot, value in result.per_slot.items()}
    rows = await refresh_rows(paired)
    for _ in range(3):
        assert await paired.provider.bearers(plan=paired.plan, result=result, pair=pair) == first
    assert await refresh_rows(paired) == rows and await counts(store) == (1, 1, 1)


@pytest.mark.asyncio
async def test_original_pair_survives_store_and_provider_reconstruction(store, paired):
    first = await paired.provider.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    result = applied(paired, first)
    await paired.provider.activate_access(plan=paired.plan, result=result, credential=first["access"])
    bearers = await paired.provider.bearers(plan=paired.plan, result=result, pair=first)
    rows = await refresh_rows(paired)
    # Rebuild the refresh store, signer and provider; only the PG rows and the keys are retained.
    async def same_key():
        return b"unit-original-refresh-key-32-bytes!"
    fresh = provider(paired, signer=HmacOriginalRefreshSigner(store.tenant, store.project, same_key),
        refresh_store=PostgresOriginalRefreshStore(pg_pool=store._pool, tenant=store.tenant, project=store.project))
    again = await fresh.read_pair(plan=paired.plan, access_expires_at=paired.expiry)
    assert again == first
    assert await fresh.bearers(plan=paired.plan, result=result, pair=again) == bearers
    assert await refresh_rows(paired) == rows and await counts(store) == (1, 1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", ["refresh_key", "session_key"])
async def test_unavailable_or_changed_signing_key_refuses_delivery_without_remint(store, paired, lost):
    pair = await paired.provider.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    result = applied(paired, pair)
    await paired.provider.activate_access(plan=paired.plan, result=result, credential=pair["access"])
    rows = await refresh_rows(paired)
    if lost == "refresh_key":
        paired.refresh_key = None
        refused = (OriginalExchangeRefused, "^original_refresh_signing_unavailable$")
    else:
        paired.session_secret = "different-unit-session-signing-secret"
        refused = (SessionIssuanceRefused, "^issuance_signing_mismatch$")
    with pytest.raises(refused[0], match=refused[1]):
        await paired.provider.bearers(plan=paired.plan, result=result, pair=pair)
    with pytest.raises(refused[0], match=refused[1]):
        await paired.provider.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    assert await refresh_rows(paired) == rows and await counts(store) == (1, 1, 1)


@pytest.mark.asyncio
async def test_concrete_pair_abort_retires_both_inactive_originals(store, paired):
    pair = await paired.provider.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    result = applied(paired, pair, state="aborted", outcomes={slot: "released" for slot in pair})
    await paired.provider.retire_pair(plan=paired.plan, result=result, access_expires_at=paired.expiry)
    assert await counts(store) == (1, 1, 0)
    assert [row[2] for row in await refresh_rows(paired)] == ["retired"]
    signs = paired.signs
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await paired.provider.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    with pytest.raises(OriginalExchangeRefused, match="^original_refresh_terminal$"):
        await paired.provider.refresh.bearer(plan=paired.plan)
    assert paired.signs == signs and await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_custody_arguments_are_ignored_and_never_touched(store, paired):
    other = OriginalCredentialPairProvider(refresh_store=paired.db, custody=NoCustody(),
        custody_namespace="other", refresh_signer=paired.signer, card_kind="automation",
        refresh_ttl_seconds=180 * 86400, authority_factory=paired.factory)
    first = await other.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    assert first == await paired.provider.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    assert await counts(store) == (1, 1, 0) and len(await refresh_rows(paired)) == 1
