"""Read-only access adapter over the original real PG issuance."""
from __future__ import annotations

import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceReceipt, SessionIssuanceRefused
from kdcube_ai_app.auth.bundle.session_planned_issuance import PlannedIssuanceContext, PreparedSessionSnapshot
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts, store
from kdcube_ai_app.auth.tests.test_bound_session_issuer import MemoryCustody, authority
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth import grants
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_grants import _plan


@pytest.mark.asyncio
async def test_public_access_read_recovers_original_without_preparing_or_key_access(store):
    plan, custody = _plan(tenant=store.tenant, project=store.project), MemoryCustody()
    expiry = int(time.time()) + 3600
    prepared = await grants.prepare_delegated_client_access_token(plan=plan, expires_at=expiry,
                                                                custody=custody, authority=authority(store))
    fresh = authority(store)
    async def forbidden(*args, **kwargs):
        pytest.fail("read-only adapter reached preparation, activation or signing")
    fresh._resolve_secret = forbidden
    fresh.prepare_bound_session = fresh.activate_prepared_bound_session = forbidden
    one = await grants.read_prepared_delegated_client_access_token(plan=plan, expires_at=expiry, authority=fresh)
    two = await grants.read_prepared_delegated_client_access_token(plan=plan, expires_at=expiry, authority=fresh)
    assert one == two and one.context == prepared.context
    assert one.receipt.session_id == prepared.receipt.session_id
    assert one.receipt.bearer_sha256 == prepared.receipt.bearer_sha256
    assert one.issued_at > 0 and custody.created == 1 and await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
async def test_access_read_changed_grants_refuses_with_no_repair(store):
    plan, custody = _plan(tenant=store.tenant, project=store.project), MemoryCustody()
    expiry = int(time.time()) + 3600
    await grants.prepare_delegated_client_access_token(plan=plan, expires_at=expiry,
                                                     custody=custody, authority=authority(store))
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await grants.read_prepared_delegated_client_access_token(
            plan=replace(plan, resource_grants={"records": ("records:write",)}),
            expires_at=expiry, authority=authority(store))
    assert custody.created == 1 and await counts(store) == (1, 1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["type", "context", "time", "receipt"])
async def test_malformed_read_snapshot_refuses(change):
    plan, expiry = _plan(), int(time.time()) + 3600
    bound = PlannedIssuanceContext.from_oauth_plan(plan, slot="access", expires_at=expiry)
    good = PreparedSessionSnapshot(bound, SessionIssuanceReceipt("unit", "a" * 32, "b" * 64), expiry - 100)
    value = {"type": vars(SimpleNamespace(context=bound)),
             "context": replace(good, context=replace(bound, actor="other")),
             "time": replace(good, issued_at=True), "receipt": replace(good, receipt=object())}[change]
    async def forbidden(*args, **kwargs):
        pytest.fail("read adapter used prepare or activation")
    async def reader(*args, **kwargs):
        return value
    host = SimpleNamespace(tenant=plan.tenant, project=plan.project,
        prepare_bound_session=forbidden, activate_prepared_bound_session=forbidden,
        read_prepared_bound_session=reader)
    with pytest.raises(SessionIssuanceRefused, match="^session_issuance_result_invalid$"):
        await grants.read_prepared_delegated_client_access_token(plan=plan, expires_at=expiry, authority=host)


@pytest.mark.asyncio
async def test_missing_public_read_api_never_falls_back_to_prepare():
    plan = _plan()
    async def forbidden(*args, **kwargs):
        pytest.fail("missing read API fell back to prepare")
    host = SimpleNamespace(tenant=plan.tenant, project=plan.project,
                           prepare_bound_session=forbidden, activate_prepared_bound_session=forbidden)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_store_unavailable$"):
        await grants.read_prepared_delegated_client_access_token(plan=plan, expires_at=int(time.time()) + 60,
                                                               authority=host)
