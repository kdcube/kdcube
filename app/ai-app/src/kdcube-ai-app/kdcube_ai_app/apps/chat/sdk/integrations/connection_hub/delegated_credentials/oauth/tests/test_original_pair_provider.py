"""Actual issuer composition: real PG with synthetic qualified-manager seam.

This fixture does not qualify encrypted service storage, ACLs or restarts.
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
from kdcube_ai_app.infra.secrets.issuance import issuance_secret_custody
from kdcube_ai_app.infra.secrets.manager import InMemorySecretsManager
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_grants import _plan
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import OriginalExchangeRefused
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_pair_provider import OriginalCredentialPairProvider
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_store import PostgresOriginalRefreshStore
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_issuer import HmacOriginalRefreshSigner


@pytest_asyncio.fixture
async def paired(store):
    r = SimpleNamespace(plan=_plan(tenant=store.tenant, project=store.project), expiry=int(time.time()) + 3600,
                        signs=0, creates=0, qualified=True, no_prepare=False)
    r.namespace = "unit-original-custody"
    class Manager(InMemorySecretsManager):
        provider_type = "secrets-service"
        async def qualify_runtime_custody(self, *, namespace):
            return namespace == r.namespace and r.qualified
        async def create_ephemeral_secret(self, **kwargs):
            created = await super().create_ephemeral_secret(**kwargs)
            r.creates += bool(created)
            return created
    r.manager = Manager()
    r.custody = issuance_secret_custody(namespace=r.namespace, manager=r.manager)
    r.db = PostgresOriginalRefreshStore(pg_pool=store._pool, tenant=store.tenant, project=store.project)
    await r.db.ensure_schema()
    async def key():
        r.signs += 1
        return b"unit-original-refresh-key-32-bytes!"
    r.signer = HmacOriginalRefreshSigner(store.tenant, store.project, key)
    def factory(**kwargs):
        assert kwargs == {"tenant": store.tenant, "project": store.project}
        resolved = authority(store)
        if r.no_prepare:
            async def forbidden(*args, **kwargs):
                pytest.fail("read-only pair replay prepared or resolved a signing key")
            resolved.prepare_bound_session = resolved._resolve_secret = forbidden
        return resolved
    r.factory = factory
    r.provider = OriginalCredentialPairProvider(refresh_store=r.db, custody=r.custody,
        custody_namespace=r.namespace, refresh_signer=r.signer, card_kind="automation",
        refresh_ttl_seconds=180 * 86400, authority_factory=factory)
    return r


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
    assert await counts(store) == (1, 1, 0) and paired.creates == 2
    pinned = paired.plan.to_dict()
    pinned["intent"] = {"candidate": {"card_kind": "automation"}}
    for slot in paired.plan.slots:
        assert AutomationAccessService._checked_issuance_record(pinned, slot, first[slot].record) == first[slot].record
    paired.no_prepare = True
    again = await paired.provider.read_pair(plan=paired.plan, access_expires_at=paired.expiry)
    assert first == again and paired.creates == 2 and paired.signs == 1
    assert first["access"].record["credential"]["iat"] > 0
    assert first["refresh"].record["credential"]["exp"] == paired.plan.expires_at


@pytest.mark.asyncio
async def test_concrete_original_pair_applied_result_activates_only_access(store, paired):
    pair = await paired.provider.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    result = applied(paired, pair)
    one = await paired.provider.activate_access(plan=paired.plan, result=result, credential=pair["access"])
    two = await paired.provider.activate_access(plan=paired.plan, result=result, credential=pair["access"])
    assert one == two and await counts(store) == (1, 1, 1) and paired.creates == 2
    for slot in pair:
        bearer = await paired.custody.get(secret_ref=pair[slot].receipt.secret_ref)
        assert hashlib.sha256(bearer.encode()).hexdigest() == pair[slot].receipt.bearer_sha256
        if slot == "access":
            user = await BundleSessionAuthManager(authority=authority(store)).authenticate(bearer)
            assert user.sub == paired.plan.credential_subject
        else:
            with pytest.raises(AuthenticationError):
                await BundleSessionAuthManager(authority=authority(store)).authenticate(bearer)


@pytest.mark.asyncio
async def test_concrete_pair_abort_retires_both_inactive_originals(store, paired):
    pair = await paired.provider.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    result = applied(paired, pair, state="aborted", outcomes={slot: "released" for slot in pair})
    await paired.provider.retire_pair(plan=paired.plan, result=result, access_expires_at=paired.expiry)
    for slot in pair:
        assert await paired.custody.get(secret_ref=pair[slot].receipt.secret_ref) is None
    assert await counts(store) == (1, 1, 0) and paired.creates == 2
    with pytest.raises(SessionIssuanceRefused, match="^issuance_terminal$"):
        await paired.provider.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    assert paired.creates == 2


@pytest.mark.asyncio
async def test_unqualified_common_custody_refuses_before_access_preparation(store, paired):
    paired.qualified = False
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_not_durable$"):
        await paired.provider.prepare_pair(plan=paired.plan, access_expires_at=paired.expiry)
    assert await counts(store) == (0, 0, 0) and paired.creates == paired.signs == 0


@pytest.mark.asyncio
async def test_unknown_custody_namespace_refuses_before_provider_use(paired):
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_custody_not_bound$"):
        OriginalCredentialPairProvider(refresh_store=paired.db, custody=paired.custody,
            custody_namespace="other", refresh_signer=paired.signer, card_kind="automation",
            refresh_ttl_seconds=180 * 86400, authority_factory=paired.factory)
