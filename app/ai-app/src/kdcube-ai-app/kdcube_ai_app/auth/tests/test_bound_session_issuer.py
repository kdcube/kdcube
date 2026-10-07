from __future__ import annotations

import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from kdcube_ai_app.auth.bundle import BundleSessionAuthManager, BundleSessionAuthority
from kdcube_ai_app.auth.bundle.session_issuance import IssuanceContext, SessionIssuanceRefused
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


def authority(store):
    return BundleSessionAuthority(
        tenant=store.tenant, project=store.project, authority_store=store,
        redis=FakeRedis(), secret="unit-session-signing-secret",
    )


async def issue(store, custody, bound):
    return await authority(store).issue_bound_session(
        bound, user_id="integration:unit:human", roles=["delegated-client"],
        permissions=["records:read"], custody=custody,
    )


@pytest.mark.asyncio
async def test_bound_issuer_recovers_original_custody_with_one_real_pg_session(store):
    custody = MemoryCustody()
    bound = context(store)
    first = await issue(store, custody, bound)
    recovered = await issue(store, custody, bound)
    assert first.outcome == "issued" and recovered.outcome == "recovered"
    assert first.session_id == recovered.session_id
    assert first.secret_ref == recovered.secret_ref
    assert first.bearer_sha256 == recovered.bearer_sha256
    assert custody.created == 1
    assert await counts(store) == (1, 1, 1)
    token = await custody.get(first.secret_ref)
    assert hashlib.sha256(token.encode()).hexdigest() == first.bearer_sha256
    user = await BundleSessionAuthManager(authority=authority(store)).authenticate(token)
    assert user.sub == "integration:unit:human"
    assert user.permissions == ["records:read"]
    assert set(first.to_public_dict()) == {"session_id", "secret_ref", "bearer_sha256", "outcome"}


@pytest.mark.asyncio
async def test_conflicting_request_refuses_before_custody_or_user_changes(store):
    custody = MemoryCustody()
    bound = context(store)
    first = await issue(store, custody, bound)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await authority(store).issue_bound_session(
            bound, user_id="integration:unit:human", roles=["admin"],
            permissions=["records:write"], custody=custody,
        )
    assert custody.created == 1
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
async def test_issuer_recovers_pending_enveloped_custody_after_lost_create_response(store):
    manager = _EnvelopeProviderFixture()
    custody = envelope_custody(manager)
    bound = context(store)

    class LostResponse:
        async def get(self, **kwargs):
            return await custody.get(**kwargs)

        async def create(self, **kwargs):
            await custody.create(**kwargs)
            raise RuntimeError("synthetic lost response")

    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_unavailable$"):
        await issue(store, LostResponse(), bound)
    reserved = await store.read_issuance(bound.identity)
    assert reserved.state == "reserved"
    assert await counts(store) == (1, 1, 0)
    original = hashlib.sha256((await custody.get(secret_ref=reserved.secret_ref)).encode()).hexdigest()
    assert await custody.purge_expired(now=int(time.time()), limit=1) == 0
    recovered = await issue(store, envelope_custody(manager), bound)
    assert recovered.session_id == reserved.session_id
    assert recovered.secret_ref == reserved.secret_ref
    assert recovered.bearer_sha256 == original
    assert await counts(store) == (1, 1, 1)


@pytest.mark.asyncio
async def test_expired_and_purged_active_custody_never_recreates_original(store):
    manager = _EnvelopeProviderFixture()
    custody = envelope_custody(manager)
    bound = context(store)
    first = await issue(store, custody, bound)
    namespace = "connection-hub-issuance-custody"
    raw = await manager.get_ephemeral_secret(namespace=namespace, secret_ref=first.secret_ref)
    # A backend with a shorter/incorrect expiry must still refuse recovery,
    # even when the immutable issuer context has not expired yet.
    envelope = {**json.loads(raw), "expires_at": int(time.time()) - 1}
    await manager.set_ephemeral_secret(
        namespace=namespace, secret_ref=first.secret_ref,
        value=json.dumps(envelope), expires_at=envelope["expires_at"],
    )
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_expired$"):
        await issue(store, custody, bound)
    assert await counts(store) == (1, 1, 1)
    assert await custody.purge_expired(now=int(time.time()), limit=1) == 1
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_missing$"):
        await issue(store, custody, bound)
    assert await counts(store) == (1, 1, 1)
    assert (await store.read_issuance(bound.identity)).session_id == first.session_id
    assert await manager.get_ephemeral_secret(namespace=namespace, secret_ref=first.secret_ref) is None
