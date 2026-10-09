from __future__ import annotations

import hashlib
import time
from types import SimpleNamespace

import pytest

from kdcube_ai_app.auth.bundle import BundleSessionAuthManager, BundleSessionAuthority
from kdcube_ai_app.auth.bundle.session_issuance import IssuanceContext, SessionIssuanceRefused
from kdcube_ai_app.auth.bundle.sessions import _make_token
from kdcube_ai_app.auth.tests._bound_session_crash_fixtures import (
    SIGNING_SECRET, ForbiddenCustody, PhasedStore,
)
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts, store
from kdcube_ai_app.auth.tests.test_bundle_sessions import FakeRedis
from kdcube_ai_app.infra.secrets.issuance import issuance_secret_custody
from kdcube_ai_app.infra.secrets.manager import InMemorySecretsManager


class MemoryCustody:
    """Create-only custody seam; this fixture makes no durability claim."""
    def __init__(self):
        self.values = {}
        self.created = 0

    async def create(self, *, secret_ref, value, expires_at):
        if secret_ref in self.values:
            return False
        self.values[secret_ref] = value
        self.created += 1
        return True

    async def get(self, secret_ref):
        return self.values.get(secret_ref)


def context(store):
    return IssuanceContext(
        tenant=store.tenant, project=store.project, transaction_id="a" * 64,
        slot="grant:unit-card", actor="human", effect_digest="b" * 64,
        receipt_digest="c" * 64, access_id="unit-card", target_incarnation=1,
        expires_at=int(time.time()) + 600,
    )


def authority(store, secret=SIGNING_SECRET):
    return BundleSessionAuthority(
        tenant=store.tenant, project=store.project, authority_store=store,
        redis=FakeRedis(), secret=secret,
    )


async def issue(store, bound, custody=None):
    """Issue or recover; by default any custody call fails the test."""
    return await authority(store).issue_bound_session(
        bound, user_id="integration:unit:human", roles=["delegated-client"],
        permissions=["records:read"], custody=ForbiddenCustody() if custody is None else custody,
    )


async def resigned(store, bound, secret=SIGNING_SECRET):
    """The original bearer, re-signed from the stored claims; it must match the stored fingerprint."""
    original = await store.read_issuance(bound.identity)
    token = _make_token(original.record["claims"], secret=secret)
    assert hashlib.sha256(token.encode()).hexdigest() == original.record["token_sha256"]
    return token


@pytest.mark.asyncio
async def test_bound_issuer_recovers_original_by_resigning_with_one_real_pg_session(store):
    custody = ForbiddenCustody()
    bound = context(store)
    first = await issue(store, bound, custody)
    recovered = await issue(store, bound, custody)
    assert first.outcome == "issued" and recovered.outcome == "recovered"
    assert first.session_id == recovered.session_id
    assert first.secret_ref == recovered.secret_ref
    assert first.bearer_sha256 == recovered.bearer_sha256
    assert custody.calls == []
    assert await counts(store) == (1, 1, 1)
    token = await resigned(store, bound)
    assert hashlib.sha256(token.encode()).hexdigest() == first.bearer_sha256
    user = await BundleSessionAuthManager(authority=authority(store)).authenticate(token)
    assert user.sub == "integration:unit:human"
    assert user.permissions == ["records:read"]
    assert set(first.to_public_dict()) == {"session_id", "secret_ref", "bearer_sha256", "outcome"}


@pytest.mark.asyncio
async def test_conflicting_request_refuses_before_signing_or_user_changes(store):
    bound = context(store)
    first = await issue(store, bound)
    issuer = authority(store)

    async def forbidden_key():
        pytest.fail("conflict reached the signing key")

    issuer._resolve_secret = forbidden_key
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await issuer.issue_bound_session(
            bound, user_id="integration:unit:human", roles=["admin"],
            permissions=["records:write"], custody=ForbiddenCustody(),
        )
    assert (await store.read_issuance(bound.identity)).record["token_sha256"] == first.bearer_sha256
    assert (await store.get_user("integration:unit:human"))["permissions"] == ["records:read"]
    assert await counts(store) == (1, 1, 1)


class _EnvelopeProviderFixture(InMemorySecretsManager):
    """Explicit qualification stub, not deployed custody or durability proof."""
    provider_type = "secrets-service"

    async def qualify_runtime_custody(self, *, namespace):
        return namespace == "connection-hub-issuance-custody"


def envelope_custody(manager):
    return issuance_secret_custody(
        namespace="connection-hub-issuance-custody", manager=manager,
        settings=SimpleNamespace(SECRETS_SERVICE_BACKEND="host-vault"),
    )


@pytest.mark.asyncio
async def test_issuer_recovers_after_lost_activation_response_and_writes_no_secret(store):
    manager = _EnvelopeProviderFixture()
    bound = context(store)

    async def lost_response(boundary):
        if boundary == "after_reservation":
            raise RuntimeError("synthetic lost response")

    issuer = BundleSessionAuthority(
        tenant=store.tenant, project=store.project,
        authority_store=PhasedStore(store, lost_response), secret=SIGNING_SECRET,
    )
    with pytest.raises(SessionIssuanceRefused, match="^issuance_store_unavailable$"):
        await issuer.issue_bound_session(
            bound, user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], custody=envelope_custody(manager),
        )
    reserved = await store.read_issuance(bound.identity)
    assert reserved.state == "reserved"
    assert await counts(store) == (1, 1, 0)
    recovered = await issue(store, bound, envelope_custody(manager))
    assert recovered.session_id == reserved.session_id
    assert recovered.secret_ref == reserved.secret_ref
    assert recovered.bearer_sha256 == reserved.record["token_sha256"]
    assert await counts(store) == (1, 1, 1)
    # A passed secrets custody is ignored: no bearer reached the secrets manager.
    assert manager._data == {}
    assert await envelope_custody(manager).get(secret_ref=reserved.secret_ref) is None
    await resigned(store, bound)


@pytest.mark.asyncio
async def test_signing_key_change_never_replaces_active_original(store):
    bound = context(store)
    first = await issue(store, bound)
    issuer = authority(store, secret="different-unit-session-signing-secret")
    with pytest.raises(SessionIssuanceRefused, match="^issuance_signing_mismatch$"):
        await issuer.issue_bound_session(
            bound, user_id="integration:unit:human", roles=["delegated-client"],
            permissions=["records:read"], custody=ForbiddenCustody(),
        )
    assert await counts(store) == (1, 1, 1)
    original = await store.read_issuance(bound.identity)
    assert (original.session_id, original.state) == (first.session_id, "active")
    assert original.record["token_sha256"] == first.bearer_sha256
    # The original bearer is still the one the stored claims sign to with the original key.
    token = await resigned(store, bound)
    user = await BundleSessionAuthManager(authority=authority(store)).authenticate(token)
    assert user.permissions == ["records:read"]
