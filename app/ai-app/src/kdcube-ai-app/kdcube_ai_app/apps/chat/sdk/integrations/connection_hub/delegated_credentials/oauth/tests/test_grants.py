# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Tests for delegated-client access-token minting."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import sys
import time

import pytest

from connection_hub.authority_registry import DELEGATED_CLIENT_AUTHORITY_ID
from connection_hub.delegated_credentials.oauth_issuance import OAuthIssuancePlan, OAuthIssuanceResult, SlotOutcome
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth import grants
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceReceipt, SessionIssuanceRefused
from kdcube_ai_app.auth.bundle.session_planned_issuance import AppliedIssuanceContext, PlannedIssuanceContext
from kdcube_ai_app.auth.bundle.sessions import BundleSessionAuthority
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts, store

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.grants import (
    DELEGATED_CLIENT_ROLE,
    integration_subject,
    mint_delegated_client_access_token,
)

ADMIN_SUB = "google:admin@example.test"


def test_delegated_client_role_value():
    assert DELEGATED_CLIENT_ROLE == "kdcube:role:delegated-client"


def test_integration_subject_is_distinct_and_deterministic():
    isub = integration_subject(ADMIN_SUB, client_id="example-client")
    assert isub != ADMIN_SUB
    assert ADMIN_SUB in isub
    assert "example-client" in isub
    assert integration_subject(ADMIN_SUB, client_id="example-client") == isub


class _FakeAuthority:
    def __init__(self):
        self.calls = []

    async def login_or_register(self, *, sub, roles=None, **kw):
        self.calls.append({"sub": sub, "roles": list(roles or []), **kw})

        class _Grant:
            token = f"kst1.mock.{sub}"

        return _Grant()


@pytest.mark.asyncio
async def test_minter_uses_integration_identity_not_admin():
    authority = _FakeAuthority()
    out = await mint_delegated_client_access_token(
        ADMIN_SUB, ["records:read"], authority=authority, client_id="example-client", ttl_seconds=3600
    )
    assert out["expires_in"] == 3600
    assert out["access_token"].startswith("kst1.mock.integration:example-client:")

    call = authority.calls[0]
    assert call["sub"] == integration_subject(ADMIN_SUB, client_id="example-client")
    assert call["sub"] != ADMIN_SUB
    assert call["roles"] == [DELEGATED_CLIENT_ROLE]
    assert call["permissions"] == ["records:read"]


@pytest.mark.asyncio
async def test_minter_passes_credential_metadata_to_session_authority():
    authority = _FakeAuthority()
    credential = {
        "schema": "kdcube.credential.v1",
        "credential_id": "cred_test",
        "credential_kind": "delegated_client_access",
        "issuer_authority_id": "delegated_client",
        "issuer_authenticator_id": "delegated_client.bearer",
        "subject": integration_subject(ADMIN_SUB, client_id="claude"),
        "audience": "kdcube:delegated_client",
    }
    await mint_delegated_client_access_token(
        ADMIN_SUB,
        ["records:read"],
        authority=authority,
        client_id="claude",
        operations=["records_export"],
        credential=credential,
        ttl_seconds=3600,
    )

    metadata = authority.calls[0]["metadata"]
    assert metadata["credential"] == credential
    assert metadata["delegated_client"]["client_id"] == "claude"
    assert metadata["delegated_client"]["operations"] == ["records_export"]


def _plan(*, tenant="tenant-a", project="project-a"):
    now = int(time.time())
    return OAuthIssuancePlan(
        transaction_id="a" * 64, decision_request_id="b" * 64,
        intent_digest="c" * 64, original_input_digest="d" * 64,
        tenant=tenant, project=project, access_id="card-a", grantor_subject=ADMIN_SUB,
        client_id="example-client", credential_issuer=DELEGATED_CLIENT_AUTHORITY_ID,
        credential_subject=integration_subject(ADMIN_SUB, client_id="example-client"),
        base_revision=0, candidate_revision=1, expires_at=now + 86400,
        card_content_hash="e" * 64, operations=("records_export",),
        resource_grants={"records": ("records:read",), "files": ("files:read", "records:read")},
        resource_operations={"records": ("records_export",)},
        delivery_deadline=now + 600, reserved_until=now + 300,
        slots=("access", "refresh"), effect_digests={"access": "f" * 64, "refresh": "1" * 64},
    )


def _result(plan, receipt, *, state="committed", outcome="applied", token_sha256=None):
    return OAuthIssuanceResult(
        transaction_id=plan.transaction_id, intent_digest=plan.intent_digest,
        state=state, access_id=plan.access_id, card_revision=plan.candidate_revision,
        expires_at=plan.expires_at, delivery_deadline=plan.delivery_deadline,
        receipt_digest="2" * 64,
        per_slot={"access": SlotOutcome(outcome, plan.effect_digests["access"],
                                       token_sha256 or receipt.bearer_sha256)},
    )


def _prepare():
    call = getattr(grants, "prepare_delegated_client_access_token", None)
    assert callable(call), "The delegated SDK adapter must use planned inactive preparation, not ordinary login"
    return call


def _activate():
    call = getattr(grants, "activate_prepared_delegated_client_access_token", None)
    assert callable(call), "The delegated SDK adapter must activate only the matching original applied result"
    return call


class _PlannedAuthority(_FakeAuthority):
    def __init__(self, plan):
        super().__init__()
        self.tenant, self.project = plan.tenant, plan.project
        self.preparations, self.activations = [], []
        self.receipt = SessionIssuanceReceipt("bsn_synthetic", "3" * 32, "4" * 64)

    async def prepare_bound_session(self, context, **kwargs):
        self.preparations.append((context, kwargs))
        return self.receipt

    async def activate_prepared_bound_session(self, context, **kwargs):
        self.activations.append((context, kwargs))
        return self.receipt


@pytest.mark.asyncio
async def test_planned_adapter_prepares_only_plan_authority_without_publishing_bearer():
    plan = _plan()
    authority = _PlannedAuthority(plan)
    prepared = await _prepare()(plan=plan, expires_at=plan.delivery_deadline, authority=authority)
    assert authority.calls == []
    context, kwargs = authority.preparations[0]
    assert context == PlannedIssuanceContext.from_oauth_plan(plan, slot="access", expires_at=plan.delivery_deadline)
    assert kwargs == {"user_id": plan.credential_subject, "roles": [DELEGATED_CLIENT_ROLE],
                      "permissions": ["files:read", "records:read"]}
    assert prepared.context == context
    assert prepared.receipt == authority.receipt
    assert not hasattr(prepared, "access_token")
    assert "access_token" not in prepared.receipt.to_public_dict()


@pytest.mark.asyncio
async def test_planned_adapter_factory_uses_plan_namespace_not_default_config():
    plan, seen = _plan(), []
    authority = _PlannedAuthority(plan)

    def factory(**kwargs):
        seen.append(kwargs)
        return authority

    await _prepare()(plan=plan, expires_at=plan.delivery_deadline, authority_factory=factory)
    assert seen == [{"tenant": plan.tenant, "project": plan.project}]
    assert authority.calls == []


@pytest.mark.parametrize("changes", [
    {"grantor_subject": "integration:client:human"},
    {"credential_subject": ADMIN_SUB},
    {"credential_issuer": "another-issuer"},
    {"slots": ("refresh",)},
])
@pytest.mark.asyncio
async def test_invalid_planned_identity_refuses_before_authority_factory(changes):
    called = []
    with pytest.raises(SessionIssuanceRefused):
        await _prepare()(plan=replace(_plan(), **changes), expires_at=int(time.time()) + 60,
                         authority_factory=lambda **kw: called.append(kw))
    assert called == []


@pytest.mark.asyncio
async def test_planned_adapter_rejects_caller_json_before_authority_factory():
    called = []
    with pytest.raises(SessionIssuanceRefused):
        await _prepare()(plan=_plan().to_dict(), expires_at=int(time.time()) + 60,
                         authority_factory=lambda **kw: called.append(kw))
    assert called == []


@pytest.mark.asyncio
async def test_planned_activation_rejects_non_access_slot_with_other_bindings_matching():
    plan = _plan()
    authority = _PlannedAuthority(plan)
    original = await _prepare()(plan=plan, expires_at=plan.delivery_deadline,
                               authority=authority)
    context = replace(original.context, slot="refresh", effect_digest=plan.effect_digests["refresh"])
    prepared = replace(original, context=context)
    result = replace(_result(plan, original.receipt), per_slot={
        "refresh": SlotOutcome("applied", context.effect_digest, original.receipt.bearer_sha256),
    })
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await _activate()(prepared=prepared, result=result, authority=authority)
    assert authority.activations == []
    assert authority.calls == []


@pytest.mark.asyncio
async def test_planned_prepare_rejects_integration_actor_with_derived_subject_matching():
    actor = "integration:already-delegated:human"
    plan = replace(_plan(), grantor_subject=actor,
                   credential_subject=integration_subject(actor, client_id="example-client"))
    authority, called = _PlannedAuthority(plan), []

    def factory(**kwargs):
        called.append(kwargs)
        return authority

    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await _prepare()(plan=plan, expires_at=plan.delivery_deadline,
                         authority_factory=factory)
    assert called == []
    assert authority.preparations == []
    assert authority.calls == []


@pytest.mark.parametrize("scope", ["", " records:read ", 3])
@pytest.mark.asyncio
async def test_planned_prepare_rejects_invalid_scope_before_authority_factory(scope):
    # One submitted scope isolates its validation: no mixed-type sort or
    # session-authority validation is allowed to mask the adapter's guard.
    plan = replace(_plan(), resource_grants={"records": (scope,)})
    authority, called = _PlannedAuthority(plan), []

    def factory(**kwargs):
        called.append(kwargs)
        return authority

    with pytest.raises(SessionIssuanceRefused, match="^issuance_authority_invalid$"):
        await _prepare()(plan=plan, expires_at=plan.delivery_deadline,
                         authority_factory=factory)
    assert called == []
    assert authority.preparations == []
    assert authority.calls == []


@pytest.mark.asyncio
async def test_planned_adapter_missing_api_or_namespace_never_falls_back_to_login():
    plan = _plan()
    old = _FakeAuthority()
    old.tenant, old.project = plan.tenant, plan.project
    partial = _PlannedAuthority(plan)
    partial.activate_prepared_bound_session = None
    wrong = _PlannedAuthority(replace(plan, tenant="another-tenant"))
    for authority in (old, partial, wrong):
        with pytest.raises(SessionIssuanceRefused):
            await _prepare()(plan=plan, expires_at=plan.delivery_deadline,
                             authority=authority)
        assert authority.calls == []
    assert partial.preparations == []
    assert wrong.preparations == []


@pytest.mark.asyncio
async def test_missing_portable_plan_api_refuses_without_ordinary_minter(monkeypatch):
    plan, called = _plan(), []
    monkeypatch.setitem(sys.modules, "connection_hub.delegated_credentials.oauth_issuance", None)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_plan_api_unavailable$"):
        await _prepare()(plan=plan, expires_at=plan.delivery_deadline,
                         authority_factory=lambda **kw: called.append(kw))
    assert called == []


@pytest.mark.asyncio
async def test_planned_activation_rejects_malformed_prepared_receipt_before_authority_call():
    plan = _plan()
    authority = _PlannedAuthority(plan)
    prepared = await _prepare()(plan=plan, expires_at=plan.delivery_deadline,
                                authority=authority)
    for supplied in ({}, replace(prepared, receipt={})):
        with pytest.raises(SessionIssuanceRefused):
            await _activate()(prepared=supplied, result=_result(plan, prepared.receipt),
                              authority=authority)
    assert authority.activations == []


@pytest.mark.parametrize("state,outcome", [("pending", "pending"), ("aborted", "released"),
                                           ("committed", "superseded")])
@pytest.mark.asyncio
async def test_planned_activation_refuses_non_applied_original_before_authority_call(state, outcome):
    plan = _plan()
    authority = _PlannedAuthority(plan)
    prepared = await _prepare()(plan=plan, expires_at=plan.delivery_deadline,
                                authority=authority)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_result_not_applied$"):
        await _activate()(prepared=prepared, result=_result(plan, prepared.receipt, state=state, outcome=outcome),
                          authority=authority)
    assert authority.activations == []
    assert authority.calls == []


@pytest.mark.asyncio
async def test_planned_activation_pins_original_bearer_and_receipt_coordinates():
    plan = _plan()
    authority = _PlannedAuthority(plan)
    prepared = await _prepare()(plan=plan, expires_at=plan.delivery_deadline,
                                authority=authority)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_commitment_mismatch$"):
        await _activate()(prepared=prepared, result=_result(plan, prepared.receipt, token_sha256="5" * 64),
                          authority=authority)
    assert authority.activations == []
    authority.receipt = replace(authority.receipt, secret_ref="6" * 32)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await _activate()(prepared=prepared, result=_result(plan, prepared.receipt),
                          authority=authority)
    assert authority.calls == []


class _NoCustody:
    """No bearer is kept in custody any more: any custody access fails the test."""

    def __getattr__(self, name):
        pytest.fail(f"custody {name} reached; no bearer is stored")


_SIGNING_KEY = "synthetic-original-adapter-test-signing-key"


def _real_authority(plan, store, secret=_SIGNING_KEY):
    return BundleSessionAuthority(tenant=plan.tenant, project=plan.project, authority_store=store, secret=secret)


def _sha(bearer):
    return hashlib.sha256(bearer.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_planned_adapter_real_postgres_original_survives_fresh_authority_and_applied_retry(store):
    plan = _plan(tenant=store.tenant, project=store.project)
    custody = _NoCustody()

    def authority():
        return _real_authority(plan, store)

    original = await _prepare()(plan=plan, expires_at=plan.delivery_deadline,
                               authority=authority(), custody=custody)
    assert await counts(store) == (1, 1, 0)
    result = _result(plan, original.receipt)
    bearer = await grants.read_delegated_client_access_bearer(prepared=original, result=result, authority=authority())
    assert _sha(bearer) == original.receipt.bearer_sha256
    replayed = await _prepare()(plan=plan, expires_at=plan.delivery_deadline,
                               authority=authority(), custody=custody)
    assert replayed.context == original.context
    assert replayed.receipt.secret_ref == original.receipt.secret_ref
    assert replayed.receipt.bearer_sha256 == original.receipt.bearer_sha256
    assert replayed.receipt.session_id == original.receipt.session_id
    assert await counts(store) == (1, 1, 0)
    # Neither a later clock-derived expiry nor a changed permission snapshot
    # can turn this original identity into a second preparation.
    for changed, expiry in ((plan, plan.delivery_deadline + 1),
                            (replace(plan, resource_grants={"records": ("records:write",)}), plan.delivery_deadline)):
        with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
            await _prepare()(plan=changed, expires_at=expiry, authority=authority(), custody=custody)
    assert await counts(store) == (1, 1, 0)
    active = await _activate()(prepared=replayed, result=result, authority=authority(), custody=custody)
    assert active.secret_ref == original.receipt.secret_ref
    assert await counts(store) == (1, 1, 1)
    profile = await store.get_user(plan.credential_subject)
    assert profile["roles"] == [DELEGATED_CLIENT_ROLE]
    assert profile["permissions"] == ["files:read", "records:read"]
    again = await _activate()(prepared=replayed, result=result, authority=authority(), custody=custody)
    assert again == active
    assert await grants.read_delegated_client_access_bearer(
        prepared=replayed, result=result, authority=authority()) == bearer
    assert (await store.read_issuance(original.context.identity)).expires_at == plan.delivery_deadline


@pytest.mark.asyncio
async def test_planned_adapter_real_postgres_activated_replay_with_changed_key_never_remints(store):
    plan = _plan(tenant=store.tenant, project=store.project)
    authority = _real_authority(plan, store)
    original = await _prepare()(plan=plan, expires_at=plan.delivery_deadline, authority=authority)
    result = _result(plan, original.receipt)
    await _activate()(prepared=original, result=result, authority=authority)
    bearer = await grants.read_delegated_client_access_bearer(prepared=original, result=result, authority=authority)
    rotated = _real_authority(plan, store, secret="another-signing-key")
    with pytest.raises(SessionIssuanceRefused, match="^issuance_signing_mismatch$"):
        await _activate()(prepared=original, result=result, authority=rotated)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_signing_mismatch$"):
        await grants.read_delegated_client_access_bearer(prepared=original, result=result, authority=rotated)
    assert await counts(store) == (1, 1, 1)
    assert await grants.read_delegated_client_access_bearer(
        prepared=original, result=result, authority=_real_authority(plan, store)) == bearer


@pytest.mark.asyncio
async def test_read_delegated_access_bearer_is_the_identical_first_issued_bearer(store, monkeypatch):
    from kdcube_ai_app.auth.bundle import sessions

    plan, issued = _plan(tenant=store.tenant, project=store.project), []
    make_token = sessions._make_token

    def recording(claims, *, secret):
        issued.append(make_token(claims, secret=secret))
        return issued[-1]

    monkeypatch.setattr(sessions, "_make_token", recording)
    prepared = await _prepare()(plan=plan, expires_at=plan.delivery_deadline, authority=_real_authority(plan, store))
    assert len(issued) == 1 and _sha(issued[0]) == prepared.receipt.bearer_sha256
    first = issued[0]
    monkeypatch.setattr(sessions, "_make_token", make_token)
    result = _result(plan, prepared.receipt)
    assert await grants.read_delegated_client_access_bearer(
        prepared=prepared, result=result, authority=_real_authority(plan, store)) == first
    await _activate()(prepared=prepared, result=result, authority=_real_authority(plan, store))
    assert await grants.read_delegated_client_access_bearer(
        prepared=prepared, result=result,
        authority_factory=lambda **kw: _real_authority(plan, store)) == first
    assert first not in repr(prepared) and first not in repr(prepared.receipt.to_public_dict())
    assert await counts(store) == (1, 1, 1)


@pytest.mark.asyncio
async def test_read_delegated_access_bearer_refuses_changed_applied_fingerprint(store):
    plan = _plan(tenant=store.tenant, project=store.project)
    authority = _real_authority(plan, store)
    prepared = await _prepare()(plan=plan, expires_at=plan.delivery_deadline, authority=authority)
    changed = _result(plan, prepared.receipt, token_sha256="5" * 64)

    async def forbidden(*args, **kwargs):
        pytest.fail("changed applied fingerprint reached the session authority")

    host = _PlannedAuthority(plan)
    host.read_bound_session_bearer = forbidden
    for target in (authority, host):
        with pytest.raises(SessionIssuanceRefused, match="^issuance_commitment_mismatch$"):
            await grants.read_delegated_client_access_bearer(prepared=prepared, result=changed, authority=target)
    # The session authority refuses the same mismatch on its own, before signing.
    applied = AppliedIssuanceContext.from_oauth_result(prepared.context, changed)
    authority._resolve_secret = forbidden
    with pytest.raises(SessionIssuanceRefused, match="^issuance_commitment_mismatch$"):
        await authority.read_bound_session_bearer(applied)
    assert await counts(store) == (1, 1, 0)
