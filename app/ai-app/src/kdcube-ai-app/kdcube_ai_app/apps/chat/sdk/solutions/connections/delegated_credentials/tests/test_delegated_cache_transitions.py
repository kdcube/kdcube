# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Catalog/card cache transitions against a real Redis.

The compare-and-transition rules cannot be proved against a fake: they depend
on Redis executing the Lua body atomically. Set ``REDIS_URL`` to run them.
"""

from __future__ import annotations

import copy
import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cache_settings import (
    DelegatedCacheSettings,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.cache import (
    DelegatedCardRuntimeCache,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.model import (
    CARD_STATE_ACTIVE,
    CARD_STATE_REVOKED,
    CardAuthority,
    NamedServiceSelection,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.resolver import (
    CardUnavailable,
    DelegatedCardResolver,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.store import (
    BundleStorageDelegatedCardStore,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.models import (
    CatalogDocument,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.resolver import (
    CatalogUnavailable,
    DelegatedCatalogResolver,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.runtime_cache import (
    DelegatedCatalogRuntimeCache,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.store import (
    BundleStorageDelegatedCatalogStore,
)

CONNECTIONS = {"delegated_credentials": {"oauth": {"enabled": True}}}
SUBJECT_HASH = hashlib.sha256(b"platform-user-1").hexdigest()
ACCESS_ID = "aut_abc123"


def _redis_url() -> str:
    return os.environ.get("REDIS_URL") or ""


pytestmark = pytest.mark.skipif(
    not _redis_url(), reason="REDIS_URL is not set; real-Redis cache transitions are skipped"
)


@pytest.fixture
async def redis_client():
    import redis.asyncio as redis_asyncio

    client = redis_asyncio.from_url(_redis_url())
    try:
        await client.ping()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"Redis at REDIS_URL is unreachable: {exc}")
    yield client
    await client.aclose()


@pytest.fixture
def namespace() -> tuple[str, str]:
    return f"t-{uuid.uuid4().hex[:8]}", f"p-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def catalog_cache(redis_client, namespace) -> DelegatedCatalogRuntimeCache:
    tenant, project = namespace
    return DelegatedCatalogRuntimeCache(redis_client, tenant=tenant, project=project)


@pytest.fixture
def card_cache(redis_client, namespace) -> DelegatedCardRuntimeCache:
    tenant, project = namespace
    return DelegatedCardRuntimeCache(redis_client, tenant=tenant, project=project)


def _document(*, minute: int, enabled: bool = True) -> CatalogDocument:
    body = copy.deepcopy(CONNECTIONS)
    body["delegated_credentials"]["oauth"]["enabled"] = enabled
    return CatalogDocument.build(
        body, created_at=datetime(2026, 8, 11, 10, minute, 0, tzinfo=timezone.utc)
    )


def _authority(*, revision: int = 1, expires_at: int = 1_780_003_600, state: str = CARD_STATE_ACTIVE) -> CardAuthority:
    return CardAuthority(
        access_id=ACCESS_ID,
        client_id="automation:abc",
        grantor_subject="platform-user-1",
        delegate_subject="integration:automation:abc",
        source="manual",
        card_revision=revision,
        state=state,
        resource_grants={"https://ex/mcp": ("slack:read",)},
        named_service_operations=NamedServiceSelection.none(),
        created_at=1_780_000_000,
        expires_at=expires_at,
    )


class _RaisingCatalogStore:
    """Durable double that fails the test if a hot path reads through."""

    async def read_active(self):
        raise AssertionError("governed read must not touch durable storage on a cache hit")

    async def read_version(self, version):
        raise AssertionError("governed read must not touch durable storage on a cache hit")


# -- catalog cache transitions ------------------------------------------------


@pytest.mark.asyncio
async def test_publish_then_read_active(catalog_cache):
    document = _document(minute=30)
    assert await catalog_cache.publish_active(document, ttl_seconds=300) is True
    assert await catalog_cache.read_active() == document


@pytest.mark.asyncio
async def test_delayed_restore_cannot_downgrade_a_newer_published_catalog(catalog_cache):
    older = _document(minute=30, enabled=True)
    newer = _document(minute=45, enabled=False)

    await catalog_cache.publish_active(newer, ttl_seconds=300)
    assert await catalog_cache.restore_active(older, ttl_seconds=300) is False

    active = await catalog_cache.read_active()
    assert active.version == newer.version


@pytest.mark.asyncio
async def test_restore_installs_when_the_key_is_absent(catalog_cache):
    document = _document(minute=30)
    assert await catalog_cache.restore_active(document, ttl_seconds=300) is True
    assert (await catalog_cache.read_active()).version == document.version


@pytest.mark.asyncio
async def test_restore_does_not_slide_an_equal_live_version(catalog_cache, redis_client):
    document = _document(minute=30)
    await catalog_cache.publish_active(document, ttl_seconds=300)
    await redis_client.expire(catalog_cache.active_key(), 60)

    assert await catalog_cache.restore_active(document, ttl_seconds=300) is False
    assert await redis_client.ttl(catalog_cache.active_key()) <= 60


@pytest.mark.asyncio
async def test_publication_reinstalls_an_equal_version_with_fresh_residency(
    catalog_cache, redis_client
):
    document = _document(minute=30)
    await catalog_cache.publish_active(document, ttl_seconds=300)
    await redis_client.expire(catalog_cache.active_key(), 60)

    assert await catalog_cache.publish_active(document, ttl_seconds=300) is True
    assert await redis_client.ttl(catalog_cache.active_key()) > 60


@pytest.mark.asyncio
async def test_corrupt_cached_active_reads_as_a_miss(catalog_cache, redis_client):
    await redis_client.set(catalog_cache.active_key(), "{not json")
    assert await catalog_cache.read_active() is None


@pytest.mark.asyncio
async def test_historical_versions_coexist_under_separate_keys(catalog_cache):
    first = _document(minute=30, enabled=True)
    second = _document(minute=45, enabled=False)
    await catalog_cache.cache_version(first, ttl_seconds=3600)
    await catalog_cache.cache_version(second, ttl_seconds=3600)

    assert (await catalog_cache.read_version(first.version)).version == first.version
    assert (await catalog_cache.read_version(second.version)).version == second.version


# -- catalog resolver ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_warm_active_cache_serves_without_any_durable_read(catalog_cache):
    document = _document(minute=30)
    await catalog_cache.publish_active(document, ttl_seconds=300)
    resolver = DelegatedCatalogResolver(cache=catalog_cache, store=_RaisingCatalogStore())

    assert (await resolver.resolve_active()).version == document.version


@pytest.mark.asyncio
async def test_active_miss_reads_through_and_repopulates(catalog_cache, tmp_path):
    document = _document(minute=30)
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    await store.write_version(document)
    await store.publish_active(document)

    resolver = DelegatedCatalogResolver(
        cache=catalog_cache, store=store, settings=DelegatedCacheSettings()
    )
    assert (await resolver.resolve_active()).version == document.version
    assert (await catalog_cache.read_active()).version == document.version


@pytest.mark.asyncio
async def test_unregistered_active_catalog_is_unavailable(catalog_cache, tmp_path):
    resolver = DelegatedCatalogResolver(
        cache=catalog_cache, store=BundleStorageDelegatedCatalogStore(tmp_path)
    )
    with pytest.raises(CatalogUnavailable) as exc:
        await resolver.resolve_active()
    assert exc.value.reason == "active_catalog_not_registered"


@pytest.mark.asyncio
async def test_confirmed_absent_baseline_is_not_unavailable(catalog_cache, tmp_path):
    resolver = DelegatedCatalogResolver(
        cache=catalog_cache, store=BundleStorageDelegatedCatalogStore(tmp_path)
    )
    absent = _document(minute=30).version
    assert await resolver.resolve_version(absent) is None
    assert await resolver.resolve_version("not-a-version") is None


# -- card cache transitions ---------------------------------------------------


@pytest.mark.asyncio
async def test_marker_makes_the_card_fail_closed(card_cache):
    await card_cache.restore_projection(_authority(revision=7), ttl_seconds=300)
    assert await card_cache.claim_transition(
        ACCESS_ID, mutation_id="m1", expected_revision=7, ttl_seconds=15
    ) is True

    entry = await card_cache.read(ACCESS_ID)
    assert entry.is_updating and entry.mutation_id == "m1"


@pytest.mark.asyncio
async def test_a_second_mutation_cannot_claim_a_held_card(card_cache):
    await card_cache.restore_projection(_authority(revision=7), ttl_seconds=300)
    await card_cache.claim_transition(
        ACCESS_ID, mutation_id="m1", expected_revision=7, ttl_seconds=15
    )
    assert await card_cache.claim_transition(
        ACCESS_ID, mutation_id="m2", expected_revision=7, ttl_seconds=15
    ) is False


@pytest.mark.asyncio
async def test_claim_is_refused_when_the_live_revision_moved(card_cache):
    await card_cache.restore_projection(_authority(revision=8), ttl_seconds=300)
    assert await card_cache.claim_transition(
        ACCESS_ID, mutation_id="m1", expected_revision=7, ttl_seconds=15
    ) is False


@pytest.mark.asyncio
async def test_delayed_restore_cannot_overwrite_a_newer_revision(card_cache):
    await card_cache.restore_projection(_authority(revision=8), ttl_seconds=300)
    assert await card_cache.restore_projection(_authority(revision=7), ttl_seconds=300) is False
    assert (await card_cache.read(ACCESS_ID)).card_revision == 8


@pytest.mark.asyncio
async def test_delayed_restore_displaces_neither_marker_nor_tombstone(card_cache):
    await card_cache.claim_transition(
        ACCESS_ID, mutation_id="m1", expected_revision=0, ttl_seconds=15
    )
    assert await card_cache.restore_projection(_authority(revision=7), ttl_seconds=300) is False
    assert (await card_cache.read(ACCESS_ID)).is_updating

    await card_cache.commit_tombstone(
        ACCESS_ID, card_revision=8, mutation_id="m1", ttl_seconds=60
    )
    assert await card_cache.restore_projection(_authority(revision=7), ttl_seconds=300) is False
    assert (await card_cache.read(ACCESS_ID)).is_revoked


@pytest.mark.asyncio
async def test_only_the_owning_mutation_finalizes_its_marker(card_cache):
    await card_cache.claim_transition(
        ACCESS_ID, mutation_id="m1", expected_revision=0, ttl_seconds=15
    )
    assert await card_cache.commit_projection(
        _authority(revision=1), mutation_id="intruder", ttl_seconds=300
    ) is False
    assert (await card_cache.read(ACCESS_ID)).is_updating

    assert await card_cache.commit_projection(
        _authority(revision=1), mutation_id="m1", ttl_seconds=300
    ) is True
    assert (await card_cache.read(ACCESS_ID)).card_revision == 1


@pytest.mark.asyncio
async def test_restore_installs_over_a_strictly_older_projection(card_cache):
    await card_cache.restore_projection(_authority(revision=7), ttl_seconds=300)
    assert await card_cache.restore_projection(_authority(revision=8), ttl_seconds=300) is True
    assert (await card_cache.read(ACCESS_ID)).card_revision == 8


# -- per-grantor index --------------------------------------------------------


@pytest.mark.asyncio
async def test_one_cards_expiry_does_not_hide_another(card_cache):
    now = 1_780_000_000
    await card_cache.index_add(subject_hash=SUBJECT_HASH, access_id="aut_short", expires_at=now + 10)
    await card_cache.index_add(
        subject_hash=SUBJECT_HASH, access_id="oauth-longlived", expires_at=now + 180 * 24 * 3600
    )

    later = now + 8 * 24 * 3600
    members = await card_cache.index_members(subject_hash=SUBJECT_HASH, now=later)
    assert members == ["oauth-longlived"]


# -- card resolver ------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolver_fails_closed_while_a_mutation_owns_the_card(card_cache, tmp_path):
    resolver = DelegatedCardResolver(
        cache=card_cache, store=BundleStorageDelegatedCardStore(tmp_path)
    )
    await card_cache.claim_transition(
        ACCESS_ID, mutation_id="m1", expected_revision=0, ttl_seconds=15
    )
    with pytest.raises(CardUnavailable) as exc:
        await resolver.resolve(subject_hash=SUBJECT_HASH, access_id=ACCESS_ID)
    assert exc.value.reason == "card_updating"


@pytest.mark.asyncio
async def test_resolver_denies_a_tombstoned_card_without_durable_read(card_cache, tmp_path):
    resolver = DelegatedCardResolver(
        cache=card_cache, store=BundleStorageDelegatedCardStore(tmp_path)
    )
    await card_cache.claim_transition(
        ACCESS_ID, mutation_id="m1", expected_revision=0, ttl_seconds=15
    )
    await card_cache.commit_tombstone(
        ACCESS_ID, card_revision=2, mutation_id="m1", ttl_seconds=60
    )
    assert await resolver.resolve(subject_hash=SUBJECT_HASH, access_id=ACCESS_ID) is None


@pytest.mark.asyncio
async def test_evicted_projection_is_restored_from_durable_state(card_cache, tmp_path):
    store = BundleStorageDelegatedCardStore(tmp_path)
    now = 1_780_000_000
    authority = _authority(revision=3, expires_at=now + 3600)
    pointer = await store.write_revision(
        subject_hash=SUBJECT_HASH, authority=authority, updated_at=datetime.now(timezone.utc)
    )
    await store.advance_current(subject_hash=SUBJECT_HASH, pointer=pointer)

    resolver = DelegatedCardResolver(cache=card_cache, store=store)
    resolved = await resolver.resolve(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID, now=now
    )
    assert resolved == authority
    assert (await card_cache.read(ACCESS_ID)).card_revision == 3


@pytest.mark.asyncio
async def test_expired_and_revoked_durable_state_is_denied_and_not_recached(card_cache, tmp_path):
    store = BundleStorageDelegatedCardStore(tmp_path)
    now = 1_780_000_000
    resolver = DelegatedCardResolver(cache=card_cache, store=store)

    for authority in (
        _authority(revision=3, expires_at=now - 1),
        _authority(revision=4, expires_at=now + 3600, state=CARD_STATE_REVOKED),
    ):
        pointer = await store.write_revision(
            subject_hash=SUBJECT_HASH, authority=authority, updated_at=datetime.now(timezone.utc)
        )
        await store.advance_current(subject_hash=SUBJECT_HASH, pointer=pointer)
        assert await resolver.resolve(
            subject_hash=SUBJECT_HASH, access_id=ACCESS_ID, now=now
        ) is None
        assert await card_cache.read(ACCESS_ID) is None


@pytest.mark.asyncio
async def test_list_rebuilds_an_evicted_index_from_durable_records(card_cache, tmp_path):
    store = BundleStorageDelegatedCardStore(tmp_path)
    now = 1_780_000_000
    live = _authority(revision=1, expires_at=now + 180 * 24 * 3600)
    pointer = await store.write_revision(
        subject_hash=SUBJECT_HASH, authority=live, updated_at=datetime.now(timezone.utc)
    )
    await store.advance_current(subject_hash=SUBJECT_HASH, pointer=pointer)

    resolver = DelegatedCardResolver(cache=card_cache, store=store)
    listed = await resolver.list_active(subject_hash=SUBJECT_HASH, now=now)

    assert [item.access_id for item in listed] == [ACCESS_ID]
    assert await card_cache.index_members(subject_hash=SUBJECT_HASH, now=now) == [ACCESS_ID]
