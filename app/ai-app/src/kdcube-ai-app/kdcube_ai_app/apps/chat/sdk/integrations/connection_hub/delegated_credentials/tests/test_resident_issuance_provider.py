"""Resident original-session adapter: real PostgreSQL, synthetic signing keys.

Card decision authentication, group publication and secret-custody recovery
are qualified by the Hub effect integration tests, separately from this port.
"""

from dataclasses import replace
import hashlib
import time
from unittest.mock import Mock

import pytest

from connection_hub.delegated_credentials.automation_access import AutomationAccessService
from connection_hub.delegated_credentials.cards.identity import resident_client_id, stable_resident_access_id
from connection_hub.delegated_credentials.oauth.grants import integration_subject
from connection_hub.delegated_credentials.oauth_issuance import OAuthIssuanceResult, SlotOutcome
from kdcube_ai_app.auth.bundle import BundleSessionAuthManager
from kdcube_ai_app.auth.AuthManager import AuthenticationError
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceRefused
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts, store
from kdcube_ai_app.auth.tests.test_bound_session_issuer import authority, resigned
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.cards.resident_issuance import (
    ResidentCredentialProvider,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_grants import _plan


def resident_plan(*, tenant="tenant-a", project="project-a"):
    original = _plan(tenant=tenant, project=project)
    client = resident_client_id("synthetic-app", "agent")
    expiry = int(time.time()) + 7 * 86400
    return replace(original, client_id=client, access_id=stable_resident_access_id(original.grantor_subject, client),
                   credential_subject=integration_subject(original.grantor_subject, client_id=client),
                   expires_at=expiry, delivery_deadline=expiry, slots=("access",),
                   effect_digests={"access": original.effect_digests["access"]})


def result(plan, prepared, *, state="committed", outcome="applied"):
    return OAuthIssuanceResult(
        transaction_id=plan.transaction_id, intent_digest=plan.intent_digest, state=state,
        access_id=plan.access_id, card_revision=plan.candidate_revision if state == "committed" else plan.base_revision,
        expires_at=plan.expires_at, delivery_deadline=plan.delivery_deadline,
        receipt_digest="2" * 64 if state == "committed" else "",
        per_slot={"access": SlotOutcome(outcome, plan.effect_digests["access"], prepared.receipt.bearer_sha256)})


@pytest.mark.asyncio
async def test_prepare_is_inactive_non_secret_and_replay_reads_the_original(store):
    plan = resident_plan(tenant=store.tenant, project=store.project)
    plan = replace(plan, effect_digests={**plan.effect_digests, "policy-effect": "9" * 64})
    provider = ResidentCredentialProvider(authority_factory=lambda **kwargs: authority(store))
    first = await provider.prepare(plan=plan)
    assert await counts(store) == (1, 1, 0)
    inactive_bearer = await resigned(store, first.context)
    with pytest.raises(AuthenticationError):
        await BundleSessionAuthManager(authority=authority(store)).authenticate(inactive_bearer)
    pinned = {**plan.to_dict(), "intent": {"card_kind": "agent"}}
    assert AutomationAccessService._checked_issuance_record(pinned, "access", first.record) == first.record
    assert "access_token" not in first.record and first.record["card_kind"] == "agent"

    def read_only(**kwargs):
        host = authority(store)

        async def forbidden(*args, **kwargs):
            pytest.fail("a resident original read must not prepare or resolve a signing key")

        host.prepare_bound_session = host._resolve_secret = forbidden
        return host

    fresh = ResidentCredentialProvider(authority_factory=read_only)
    assert await fresh.read(plan=plan) == first
    assert await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_only_applied_commit_activates_the_same_bearer_and_reconstructs_after_restart(store):
    plan = resident_plan(tenant=store.tenant, project=store.project)
    factory = lambda **kwargs: authority(store)
    provider = ResidentCredentialProvider(authority_factory=factory)
    original = await provider.prepare(plan=plan)
    with pytest.raises(SessionIssuanceRefused, match="issuance_result_not_applied"):
        await provider.activate(plan=plan, result=result(plan, original, state="pending", outcome="pending"))
    assert await counts(store) == (1, 1, 0)
    committed = result(plan, original)
    handles = await provider.activate(plan=plan, result=committed)
    assert hashlib.sha256(handles.access_token.encode()).hexdigest() == original.receipt.bearer_sha256
    assert handles.access_id == plan.access_id and handles.session_id == original.receipt.session_id
    authenticated = await BundleSessionAuthManager(authority=authority(store)).authenticate(handles.access_token)
    assert authenticated.sub == plan.credential_subject
    fresh = ResidentCredentialProvider(authority_factory=factory)
    assert await fresh.activate(plan=plan, result=committed) == handles
    assert await counts(store) == (1, 1, 1)


@pytest.mark.asyncio
async def test_abort_retires_the_inactive_original_without_activation(store):
    plan = resident_plan(tenant=store.tenant, project=store.project)
    provider = ResidentCredentialProvider(authority_factory=lambda **kwargs: authority(store))
    original = await provider.prepare(plan=plan)
    inactive_bearer = await resigned(store, original.context)
    await provider.retire(plan=plan, result=result(plan, original, state="aborted", outcome="released"))
    assert await counts(store) == (1, 1, 0)
    with pytest.raises(AuthenticationError):
        await BundleSessionAuthManager(authority=authority(store)).authenticate(inactive_bearer)
    with pytest.raises(SessionIssuanceRefused, match="issuance_terminal"):
        await provider.prepare(plan=plan)


@pytest.mark.asyncio
async def test_resident_recovery_uses_the_card_lease_after_the_oauth_delivery_interval(store, monkeypatch):
    plan = resident_plan(tenant=store.tenant, project=store.project)
    factory = lambda **kwargs: authority(store)
    original = await ResidentCredentialProvider(authority_factory=factory).prepare(plan=plan)
    later = int(time.time()) + 601
    # Exercise the SDK's delivery/reservation clock checks; the productive
    # decision/PG clock and delayed effect recovery are separate Hub tests.
    monkeypatch.setattr(time, "time", lambda: later)
    fresh = ResidentCredentialProvider(authority_factory=factory)
    handles = await fresh.activate(plan=plan, result=result(plan, original))
    assert hashlib.sha256(handles.access_token.encode()).hexdigest() == original.receipt.bearer_sha256


@pytest.mark.asyncio
async def test_changed_signing_key_refuses_without_preparing_a_replacement(store):
    plan = resident_plan(tenant=store.tenant, project=store.project)
    provider = ResidentCredentialProvider(authority_factory=lambda **kwargs: authority(store))
    original = await provider.prepare(plan=plan)

    def changed(**kwargs):
        host = authority(store)
        host._secret = "different-synthetic-resident-signing-key"
        return host

    fresh = ResidentCredentialProvider(authority_factory=changed)
    with pytest.raises(SessionIssuanceRefused, match="issuance_signing_mismatch"):
        await fresh.activate(plan=plan, result=result(plan, original))
    assert await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", ["json", "pair", "client", "identity", "digests"])
async def test_invalid_resident_plan_refuses_before_any_authority_call(malformed):
    plan = resident_plan()
    if malformed == "json":
        plan = plan.to_dict()
    elif malformed == "pair":
        plan = replace(plan, slots=("access", "refresh"))
    elif malformed == "client":
        plan = replace(plan, client_id="ordinary-client")
    elif malformed == "identity":
        plan = replace(plan, access_id="agent-another")
    else:
        plan = replace(plan, effect_digests=None)
    factory = Mock(side_effect=AssertionError("invalid plan reached an authority"))
    with pytest.raises(SessionIssuanceRefused, match="resident_issuance_plan_invalid"):
        await ResidentCredentialProvider(authority_factory=factory).prepare(plan=plan)
    factory.assert_not_called()
