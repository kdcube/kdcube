# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Catalog publication under the shared critical section, against real Redis."""

from __future__ import annotations

import asyncio
import copy
import os
import uuid

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.publisher import (
    CatalogPublicationError,
    ensure_delegated_catalog,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.resolver import (
    DelegatedCatalogResolver,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.runtime_cache import (
    DelegatedCatalogRuntimeCache,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.store import (
    BundleStorageDelegatedCatalogStore,
)

CONNECTIONS = {
    "delegated_credentials": {
        "oauth": {
            "enabled": True,
            "resources": [
                {"resource": "https://ex/mcp", "grants": ["named_services:use", "slack:read"]}
            ],
        }
    }
}


pytestmark = pytest.mark.skipif(
    not os.environ.get("REDIS_URL"),
    reason="REDIS_URL is not set; catalog publication tests are skipped",
)


@pytest.fixture
async def redis_client():
    import redis.asyncio as redis_asyncio

    client = redis_asyncio.from_url(os.environ["REDIS_URL"])
    try:
        await client.ping()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"Redis at REDIS_URL is unreachable: {exc}")
    yield client
    await client.aclose()


@pytest.fixture
def cache(redis_client) -> DelegatedCatalogRuntimeCache:
    return DelegatedCatalogRuntimeCache(
        redis_client, tenant=f"t-{uuid.uuid4().hex[:8]}", project=f"p-{uuid.uuid4().hex[:8]}"
    )


async def _ensure(connections, store, cache, **kwargs):
    return await ensure_delegated_catalog(
        connections=connections, store=store, cache=cache, **kwargs
    )


@pytest.mark.asyncio
async def test_first_publication_creates_one_version_and_active(tmp_path, cache):
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    result = await _ensure(CONNECTIONS, store, cache, reason="app_deploy:startup")

    assert result.created is True
    active = await store.read_active()
    assert active.version == result.version
    assert active.connections == CONNECTIONS
    assert await store.version_exists(result.version) is True
    assert (await cache.read_active()).version == result.version


@pytest.mark.asyncio
async def test_unchanged_connections_do_not_create_a_second_version(tmp_path, cache):
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    first = await _ensure(CONNECTIONS, store, cache, reason="app_deploy:startup")
    second = await _ensure(CONNECTIONS, store, cache, reason="app_deploy:props_changed")

    assert second.version == first.version
    assert second.created is False
    versions_dir = store.root / "versions"
    assert len(list(versions_dir.iterdir())) == 1


@pytest.mark.asyncio
async def test_map_key_reordering_does_not_create_a_version(tmp_path, cache):
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    first = await _ensure(CONNECTIONS, store, cache)

    reordered = {
        "delegated_credentials": {
            "oauth": {
                "resources": [
                    {"grants": ["named_services:use", "slack:read"], "resource": "https://ex/mcp"}
                ],
                "enabled": True,
            }
        }
    }
    second = await _ensure(reordered, store, cache)
    assert second.version == first.version
    assert second.created is False


@pytest.mark.asyncio
async def test_a_real_change_creates_a_new_active_version(tmp_path, cache):
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    first = await _ensure(CONNECTIONS, store, cache)

    changed = copy.deepcopy(CONNECTIONS)
    changed["delegated_credentials"]["oauth"]["resources"][0]["grants"] = ["slack:read"]
    second = await _ensure(changed, store, cache)

    assert second.version != first.version
    assert second.created is True
    assert (await store.read_active()).version == second.version
    # The previous immutable version remains available as a card baseline.
    assert await store.version_exists(first.version) is True


@pytest.mark.asyncio
async def test_concurrent_publishers_converge_on_one_version(tmp_path, cache):
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    results = await asyncio.gather(
        *(_ensure(CONNECTIONS, store, cache, reason=f"worker-{index}") for index in range(5))
    )

    versions = {result.version for result in results}
    assert len(versions) == 1
    assert len(list((store.root / "versions").iterdir())) == 1


@pytest.mark.asyncio
async def test_restart_restores_redis_without_creating_a_version(tmp_path, cache, redis_client):
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    first = await _ensure(CONNECTIONS, store, cache)

    await redis_client.delete(cache.active_key())
    await redis_client.delete(cache.version_key(first.version))

    second = await _ensure(CONNECTIONS, store, cache, reason="app_deploy:startup")
    assert second.version == first.version
    assert second.created is False
    assert (await cache.read_active()).version == first.version
    assert len(list((store.root / "versions").iterdir())) == 1


@pytest.mark.asyncio
async def test_unhashable_connections_are_a_publication_error(tmp_path, cache):
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    with pytest.raises(CatalogPublicationError) as exc:
        await _ensure({"resources": {object()}}, store, cache)
    assert exc.value.reason == "connections_not_hashable"
    assert await store.read_active() is None


@pytest.mark.asyncio
async def test_published_catalog_is_resolvable_for_request_serving(tmp_path, cache):
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    published = await _ensure(CONNECTIONS, store, cache)

    resolver = DelegatedCatalogResolver(cache=cache, store=store)
    active = await resolver.resolve_active()
    assert active.version == published.version
    assert (await resolver.resolve_version(published.version)).connections == CONNECTIONS
