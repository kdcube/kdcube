from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from connection_hub.delegated_credentials.authority_cutover import (
    AuthorityCutoverReceipt,
    AuthorityCutoverRequired,
    PostgresAuthorityCutoverStore,
)
from kdcube_ai_app.auth.AuthManager import AuthenticationError
from kdcube_ai_app.auth.bundle import BundleSessionAuthManager, BundleSessionAuthority
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceRefused
from kdcube_ai_app.auth.platform_session_schema import TABLE_PLATFORM_SESSIONS
from kdcube_ai_app.auth.session_authority_runtime import (
    KDCUBE_SESSION_AUTHORITY_FAMILIES,
    SessionAuthorityUnavailable,
    open_configured_session_authority,
    prepare_configured_session_authority,
    reset_session_authority_runtime_for_tests,
)
from kdcube_ai_app.auth.sessions import RequestContext, SessionManager, UserType
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts, store
from kdcube_ai_app.auth.tests.test_bound_session_issuer import context as committed_context
from kdcube_ai_app.auth.tests.test_planned_session_issuer import applied, plan

BUNDLE_FAMILIES = ("bundle_users", "bundle_sessions", "bundle_authority_version")


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_session_authority_runtime_for_tests()
    yield
    reset_session_authority_runtime_for_tests()


def _settings(scope, *, backend="postgresql", platform_backend="redis"):
    return SimpleNamespace(
        TENANT=scope.tenant, PROJECT=scope.project,
        AUTH=SimpleNamespace(SESSIONS=SimpleNamespace(
            AUTHORITY=SimpleNamespace(
                BACKEND=backend, GENERATION_ID="mixed-test-v1" if backend == "postgresql" else "",
            ),
            PLATFORM=SimpleNamespace(BACKEND=platform_backend),
        )),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_backend,receipt_families,ready", [
    ("authority", KDCUBE_SESSION_AUTHORITY_FAMILIES, True),
    ("redis", KDCUBE_SESSION_AUTHORITY_FAMILIES, True),
    ("redis", BUNDLE_FAMILIES, True),
    ("authority", BUNDLE_FAMILIES, False),
    ("redis", BUNDLE_FAMILIES[:-1], False),
])
async def test_runtime_selected_store_keeps_bound_issuance_on_postgres(
    store, platform_backend, receipt_families, ready,
):
    """Real PG/Redis composition, not an explicitly injected raw PG authority."""
    redis_url = os.environ.get("KDCUBE_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("KDCUBE_TEST_REDIS_URL is not set")
    from redis.asyncio import Redis

    redis = Redis.from_url(redis_url, decode_responses=False)
    receipts = PostgresAuthorityCutoverStore(
        pg_pool=store._pool, tenant=store.tenant, project=store.project,
    )
    settings = _settings(store, platform_backend=platform_backend)
    # Consumers created before preparation must observe the final split too.
    browser = SessionManager(redis_url, tenant=store.tenant, project=store.project, session_ttl=60)
    browser.redis = redis
    authority = BundleSessionAuthority(
        tenant=store.tenant, project=store.project, secret="unit-session-signing-secret",
    )
    try:
        await receipts.ensure_schema()
        family_counts = {family: 0 for family in receipt_families}
        await receipts.activate(AuthorityCutoverReceipt(
            generation_id="mixed-test-v1", source_generation="a" * 64,
            target_generation="b" * 64, source_counts=family_counts,
            target_counts=family_counts, preview_sha256="c" * 64,
        ))
        if not ready:
            with pytest.raises(AuthorityCutoverRequired):
                await prepare_configured_session_authority(
                    pg_pool=store._pool, settings=settings, redis=redis,
                )
            with pytest.raises(SessionAuthorityUnavailable, match="generation_not_prepared"):
                _ = browser.authority_store
            return
        snapshot = await prepare_configured_session_authority(
            pg_pool=store._pool, settings=settings, redis=redis,
        )
        assert snapshot.ready
        assert (browser.authority_store is None) == (platform_backend == "redis")

        # The ordinary auth entry point uses the selected backend, with no
        # bundle-specific state added to the disposable browser record.
        request = RequestContext(client_ip="192.0.2.10", user_agent="mixed-test-browser")
        anonymous = await browser.get_or_create_session(request, UserType.ANONYMOUS)
        registered = await browser.get_or_create_session(
            request, UserType.REGISTERED, {"user_id": "browser-user", "roles": ["reader"]},
        )
        assert (await browser.get_session_by_id(registered.session_id)).user_id == "browser-user"
        if platform_backend == "redis":
            for session in (anonymous, registered):
                index = f"{browser.SESSION_INDEX_PREFIX}:{session.session_id}"
                key = await redis.get(index)
                assert key is not None
                assert 0 < await redis.ttl(index) <= 60
                assert 0 < await redis.ttl(key) <= 60
            async with store._pool.acquire() as connection:
                # Mixed preparation does not even create the platform table.
                assert await connection.fetchval(
                    "SELECT to_regclass($1)", f"{store.schema}.{TABLE_PLATFORM_SESSIONS}",
                ) is None

        # The wrapper must expose the PG ledger as well as normal login methods.
        bound = plan(store)
        prepared = await authority.prepare_bound_session(
            bound, user_id=bound.credential_subject,
            roles=["delegated-client"], permissions=["records:read"],
        )
        assert await counts(store) == (1, 1, 0)
        result = applied(bound, prepared)
        active = await authority.activate_prepared_bound_session(result)
        replay = await authority.activate_prepared_bound_session(result)
        assert active.session_id == replay.session_id == prepared.session_id
        assert await counts(store) == (1, 1, 1)
        bearer = await authority.read_bound_session_bearer(result)
        authenticator = BundleSessionAuthManager(authority=authority)
        assert (await authenticator.authenticate(bearer)).permissions == ["records:read"]

        # One-step committed issuance also reaches the runtime-selected PG ledger.
        await authority.issue_bound_session(
            committed_context(store), user_id="integration:unit:human",
            roles=["delegated-client"], permissions=["records:read"],
        )
        assert await counts(store) == (1, 2, 2)
        await authority.invalidate_user(bound.credential_subject)
        with pytest.raises(AuthenticationError):
            await authenticator.authenticate(bearer)
    finally:
        keys = [key async for key in redis.scan_iter(match=f"{store.tenant}:{store.project}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


@pytest.mark.asyncio
async def test_source_mode_refuses_bound_issuance_without_a_postgres_ledger():
    scope = SimpleNamespace(tenant="source-test", project="sessions")
    await prepare_configured_session_authority(
        pg_pool=None, settings=_settings(scope, backend="redis-migration-source"),
    )
    authority = BundleSessionAuthority(
        tenant=scope.tenant, project=scope.project, secret="unit-session-signing-secret",
    )
    with pytest.raises(SessionIssuanceRefused, match="^issuance_store_unavailable$"):
        await authority.prepare_bound_session(
            plan(scope), user_id="integration:unit:human",
            roles=["delegated-client"], permissions=["records:read"],
        )


@pytest.mark.asyncio
async def test_standalone_mixed_mode_opens_postgres_and_keeps_preparation_fail_closed(monkeypatch):
    import asyncpg
    from kdcube_ai_app.apps.chat.sdk import config as config_module

    scope = SimpleNamespace(tenant="standalone-test", project="sessions")
    settings = _settings(scope)
    for name in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE"):
        setattr(settings, name, None)
    monkeypatch.setattr(config_module, "resolve_asyncpg_ssl", lambda _settings: None)
    events = []

    class Pool:
        async def close(self):
            events.append("closed")

    async def create_pool(**_kwargs):
        events.append("opened")
        return Pool()

    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    # No Redis projection endpoint is available; PG selection must not fall
    # back to source mode just because platform sessions select Redis.
    with pytest.raises(SessionAuthorityUnavailable, match="requires_redis_projection"):
        await open_configured_session_authority(settings=settings)
    assert events == ["opened", "closed"]
