from __future__ import annotations

import hashlib
import time

import pytest

from kdcube_ai_app.auth.bundle import BundleSessionAuthManager, BundleSessionAuthority
from kdcube_ai_app.auth.bundle.session_issuance import IssuanceContext, SessionIssuanceRefused
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts, store
from kdcube_ai_app.auth.tests.test_bundle_sessions import FakeRedis


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
