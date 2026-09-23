from __future__ import annotations

import os
import time
import uuid
from dataclasses import replace

import pytest

from kdcube_ai_app.auth.bundle.session_projection import (
    ProjectedBundleSessionStore,
    RedisBundleSessionProjection,
)
from kdcube_ai_app.auth.bundle.session_store import (
    BundleSessionValidationFence,
    BundleSessionValidationState,
    PostgresBundleSessionStore,
)
from kdcube_ai_app.auth.platform_session_projection import (
    ProjectedPlatformSessionStore,
    RedisPlatformSessionProjection,
)
from kdcube_ai_app.auth.platform_session_store import (
    PlatformSessionAuthorityFence,
    PlatformSessionAuthorityState,
    PostgresPlatformSessionStore,
)
from kdcube_ai_app.auth.platform_session_schema import TABLE_PLATFORM_SESSIONS


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self.values[key] = str(value)
        self.ttls[key] = int(ttl)

    async def delete(self, *keys: str):
        for key in keys:
            self.values.pop(key, None)
            self.sets.pop(key, None)
            self.ttls.pop(key, None)

    async def sadd(self, key: str, *values: str):
        self.sets.setdefault(key, set()).update(str(value) for value in values)

    async def srem(self, key: str, *values: str):
        self.sets.setdefault(key, set()).difference_update(
            str(value) for value in values
        )

    async def smembers(self, key: str):
        return set(self.sets.get(key, set()))

    async def expire(self, key: str, ttl: int):
        self.ttls[key] = int(ttl)


def _bundle_state(*, revision: int = 1, state: str = "active"):
    return BundleSessionValidationState(
        session={"session_id": "bsn-1", "sub": "user-1"},
        session_state=state,
        idle_expires_at=2_100_000_000,
        hard_expires_at=2_100_000_100,
        user={"sub": "user-1"},
        user_state="active",
        user_disabled=False,
        version=1,
        session_revision=revision,
        user_revision=1,
    )


class _BundleAuthority:
    def __init__(self) -> None:
        self.state = _bundle_state()
        self.full_reads = 0
        self.fence_reads = 0

    async def get_validation_state(self, _session_id: str):
        self.full_reads += 1
        return self.state

    async def get_validation_fence(self, _session_id: str):
        self.fence_reads += 1
        state = self.state
        return BundleSessionValidationFence(
            session_state=state.session_state,
            idle_expires_at=state.idle_expires_at,
            hard_expires_at=state.hard_expires_at,
            user_state=state.user_state,
            user_disabled=state.user_disabled,
            version=state.version,
            session_revision=state.session_revision,
            user_revision=state.user_revision,
        )


@pytest.mark.asyncio
async def test_bundle_projection_rebuilds_after_flush_and_rejects_old_snapshot() -> None:
    redis = _Redis()
    authority = _BundleAuthority()
    projection = RedisBundleSessionProjection(
        redis,
        tenant="tenant-a",
        project="project-a",
        generation_id="generation-1",
    )
    store = ProjectedBundleSessionStore(
        authority=authority,
        projection=projection,
    )

    first = await store.get_validation_state("bsn-1")
    assert first is not None and first.session_state == "active"
    assert authority.full_reads == 1
    assert redis.ttls and all(ttl > 0 for ttl in redis.ttls.values())

    snapshot_values = dict(redis.values)
    snapshot_sets = {key: set(value) for key, value in redis.sets.items()}
    snapshot_ttls = dict(redis.ttls)
    redis.values.clear()
    redis.sets.clear()
    redis.ttls.clear()
    assert (await store.get_validation_state("bsn-1")) is not None
    assert authority.full_reads == 2

    authority.state = replace(
        authority.state,
        session_state="revoked",
        session_revision=2,
    )
    redis.values = snapshot_values
    redis.sets = snapshot_sets
    redis.ttls = snapshot_ttls
    restored = await store.get_validation_state("bsn-1")

    assert restored is not None and restored.session_state == "revoked"
    assert restored.session_revision == 2
    assert authority.full_reads == 3
    assert authority.fence_reads == 3


class _PlatformAuthority:
    def __init__(self) -> None:
        self.state = PlatformSessionAuthorityState(
            record={"session_id": "psn-1", "user_id": "user-1"},
            authority_key="registered:user-1",
            revision=1,
            expires_at=2_100_000_000.0,
        )
        self.full_reads = 0
        self.revoked = False

    async def get_session_state_by_id(self, _session_id: str):
        self.full_reads += 1
        return None if self.revoked else self.state

    async def get_session_fence(self, _session_id: str):
        return PlatformSessionAuthorityFence(
            authority_key=self.state.authority_key,
            state="revoked" if self.revoked else "active",
            revision=self.state.revision,
            expires_at=self.state.expires_at,
        )


@pytest.mark.asyncio
async def test_platform_projection_fences_a_restored_pre_revocation_record() -> None:
    redis = _Redis()
    authority = _PlatformAuthority()
    projection = RedisPlatformSessionProjection(
        redis,
        tenant="tenant-a",
        project="project-a",
        generation_id="generation-1",
    )
    store = ProjectedPlatformSessionStore(
        authority=authority,
        projection=projection,
    )

    assert await store.get_session_by_id("psn-1") == authority.state.record
    old_values = dict(redis.values)
    old_ttls = dict(redis.ttls)
    authority.state = replace(authority.state, revision=2)
    authority.revoked = True
    redis.values = old_values
    redis.ttls = old_ttls

    assert await store.get_session_by_id("psn-1") is None
    assert authority.full_reads == 2


@pytest.mark.asyncio
async def test_projection_generation_changes_make_old_keys_unreadable() -> None:
    redis = _Redis()
    state = _bundle_state()
    first = RedisBundleSessionProjection(
        redis,
        tenant="tenant-a",
        project="project-a",
        generation_id="generation-1",
    )
    second = RedisBundleSessionProjection(
        redis,
        tenant="tenant-a",
        project="project-a",
        generation_id="generation-2",
    )

    await first.write(state, now=2_000_000_000)

    assert await first.read("bsn-1") == state
    assert await second.read("bsn-1") is None


@pytest.mark.asyncio
async def test_real_session_projections_rebuild_and_fence_restored_snapshots() -> None:
    postgres_dsn = os.environ.get("KDCUBE_TEST_POSTGRES_DSN")
    redis_url = os.environ.get("KDCUBE_TEST_REDIS_URL")
    if not postgres_dsn or not redis_url:
        pytest.skip("real PostgreSQL and Redis test endpoints are required")

    import asyncpg
    from redis.asyncio import Redis

    tenant = f"session-projection-{uuid.uuid4().hex}"
    project = "integration"
    generation = f"generation-{uuid.uuid4().hex}"
    now = int(time.time())
    pool = await asyncpg.create_pool(postgres_dsn, min_size=1, max_size=2)
    redis = Redis.from_url(redis_url, decode_responses=False)
    bundle_authority = PostgresBundleSessionStore(
        pg_pool=pool,
        tenant=tenant,
        project=project,
    )
    platform_authority = PostgresPlatformSessionStore(
        pg_pool=pool,
        tenant=tenant,
        project=project,
    )
    bundle_projection = RedisBundleSessionProjection(
        redis,
        tenant=tenant,
        project=project,
        generation_id=generation,
    )
    platform_projection = RedisPlatformSessionProjection(
        redis,
        tenant=tenant,
        project=project,
        generation_id=generation,
    )
    bundle = ProjectedBundleSessionStore(
        authority=bundle_authority,
        projection=bundle_projection,
    )
    platform = ProjectedPlatformSessionStore(
        authority=platform_authority,
        projection=platform_projection,
    )

    try:
        await bundle_authority.ensure_schema()
        await platform_authority.ensure_schema()

        await bundle.register_user(
            sub="user-1",
            updates={"roles": ["reader"]},
            now=now,
        )
        login = await bundle.get_login_state("user-1")
        assert login is not None
        assert await bundle.issue_session(
            {
                "session_id": "bsn-1",
                "sub": "user-1",
                "token_sha256": "a" * 64,
                "version": login.version,
                "iat": now,
                "exp": now + 600,
                "max_exp": now + 1200,
                "last_seen": now,
            },
            expected_version=login.version,
        )
        bundle_key = bundle_projection._session_key("bsn-1")
        bundle_snapshot = await redis.get(bundle_key)
        assert bundle_snapshot is not None
        assert await redis.ttl(bundle_key) > 0

        await redis.delete(bundle_key)
        rebuilt_bundle = await bundle.get_validation_state("bsn-1")
        assert rebuilt_bundle is not None
        assert rebuilt_bundle.session_state == "active"
        assert await redis.get(bundle_key) is not None

        assert await bundle.revoke_session("bsn-1") is True
        await redis.setex(bundle_key, 600, bundle_snapshot)
        fenced_bundle = await bundle.get_validation_state("bsn-1")
        assert fenced_bundle is not None
        assert fenced_bundle.session_state == "revoked"

        platform_result = await platform.get_or_create(
            authority_key="registered:user-2",
            candidate={"session_id": "psn-1"},
            user_data={
                "user_id": "user-2",
                "username": "bob",
                "roles": ["reader"],
                "permissions": [],
            },
            request_context={
                "user_timezone": "UTC",
                "user_utc_offset_min": 0,
            },
            user_type="registered",
            ttl_seconds=600,
            now=float(now),
        )
        assert platform_result.created is True
        platform_key = platform_projection._session_key("psn-1")
        platform_snapshot = await redis.get(platform_key)
        assert platform_snapshot is not None
        assert await redis.ttl(platform_key) > 0

        await redis.delete(platform_key)
        assert await platform.get_session_by_id("psn-1") is not None
        assert await redis.get(platform_key) is not None

        async with pool.acquire() as connection:
            await connection.execute(
                f"UPDATE {platform_authority.schema}.{TABLE_PLATFORM_SESSIONS} "
                "SET state = 'revoked', revision = revision + 1 "
                "WHERE session_id = $1",
                "psn-1",
            )
        await redis.setex(platform_key, 600, platform_snapshot)
        assert await platform.get_session_by_id("psn-1") is None
    finally:
        keys = [
            key
            async for key in redis.scan_iter(match=f"{tenant}:{project}:*")
        ]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        schemas = {bundle_authority.schema, platform_authority.schema}
        async with pool.acquire() as connection:
            for schema in schemas:
                await connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await pool.close()
