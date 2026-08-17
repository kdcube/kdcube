# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Publication windows and serving-entry damage, against real Redis.

Covers the cases the happy-path publication tests do not reach: a publication
interrupted between its durable steps, a serving write that does not land, a
changed source that has not been published yet, and a projection that must be
repaired or refused.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cache_settings import (
    DelegatedCacheSettings,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.publisher import (
    ensure_delegated_catalog,
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

CONNECTIONS = {
    "delegated_credentials": {
        "oauth": {
            "enabled": True,
            "resources": [
                {
                    "resource": "https://ex/mcp",
                    "grants": ["named_services:use", "mem:read"],
                    "named_services": {
                        "namespaces": {
                            "mem": {
                                "tools": {
                                    "list": {"operation": "object.list"},
                                    "get": {"operation": "object.get"},
                                }
                            }
                        }
                    },
                }
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


def _changed(grants: list[str]) -> dict:
    other = copy.deepcopy(CONNECTIONS)
    other["delegated_credentials"]["oauth"]["resources"][0]["grants"] = grants
    return other


async def _ensure(connections, store, cache, **kwargs):
    return await ensure_delegated_catalog(
        connections=connections, store=store, cache=cache, **kwargs
    )


# -- interrupted publication ------------------------------------------------


@pytest.mark.asyncio
async def test_interrupted_publication_leaves_the_previous_catalog_active(
    tmp_path, cache, monkeypatch
):
    """The durable active replacement is the commit point."""
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    first = await _ensure(CONNECTIONS, store, cache)

    async def _fail(_document):
        raise OSError("interrupted before the active replacement")

    monkeypatch.setattr(store, "publish_active", _fail)
    with pytest.raises(OSError):
        await _ensure(_changed(["mem:read"]), store, cache)
    monkeypatch.undo()

    assert (await store.read_active()).version == first.version
    assert (await cache.read_active()).version == first.version

    # The generation is not ready, so the next deployment completes it.
    second = await _ensure(_changed(["mem:read"]), store, cache, reason="app_deploy:retry")
    assert second.created is True
    assert second.version != first.version
    assert (await store.read_active()).version == second.version
    assert await store.version_exists(first.version) is True


@pytest.mark.asyncio
async def test_an_interrupted_first_publication_serves_no_catalog_at_all(
    tmp_path, cache, monkeypatch
):
    """With no previous catalog, requests are refused rather than guessed."""
    store = BundleStorageDelegatedCatalogStore(tmp_path)

    async def _fail(_document):
        raise OSError("interrupted before the active replacement")

    monkeypatch.setattr(store, "publish_active", _fail)
    with pytest.raises(OSError):
        await _ensure(CONNECTIONS, store, cache)
    monkeypatch.undo()

    resolver = DelegatedCatalogResolver(cache=cache, store=store)
    with pytest.raises(CatalogUnavailable) as exc:
        await resolver.resolve_active()
    assert exc.value.reason == "active_catalog_not_registered"

    published = await _ensure(CONNECTIONS, store, cache, reason="app_deploy:startup")
    assert (await resolver.resolve_active()).version == published.version


@pytest.mark.asyncio
async def test_a_serving_write_that_does_not_land_fails_the_generation(
    tmp_path, cache, monkeypatch
):
    """Durable success alone does not make a generation ready.

    The write reports success and stores nothing, so the failure can only be
    caught by the readiness probe rather than by a propagating exception.
    """
    store = BundleStorageDelegatedCatalogStore(tmp_path)

    async def _silently_drop(_document, **_kwargs):
        return True

    monkeypatch.setattr(cache, "publish_active", _silently_drop)
    with pytest.raises(RuntimeError, match="ready"):
        await _ensure(CONNECTIONS, store, cache)
    monkeypatch.undo()

    # Durable state committed; the serving entry did not.
    assert await store.read_active() is not None
    assert await cache.read_active() is None

    repeated = await _ensure(CONNECTIONS, store, cache, reason="app_deploy:retry")
    assert repeated.created is False
    assert (await cache.read_active()).version == repeated.version


@pytest.mark.asyncio
async def test_an_unwritable_serving_entry_fails_the_generation(
    tmp_path, cache, monkeypatch
):
    """The raising variant: the failure reaches the deployer."""
    store = BundleStorageDelegatedCatalogStore(tmp_path)

    async def _fail(_document, **_kwargs):
        raise ConnectionError("serving entry unwritable")

    monkeypatch.setattr(cache, "publish_active", _fail)
    with pytest.raises(ConnectionError):
        await _ensure(CONNECTIONS, store, cache)
    monkeypatch.undo()

    assert await cache.read_active() is None


@pytest.mark.asyncio
async def test_the_serving_entry_is_a_complete_self_contained_document(
    tmp_path, cache, redis_client
):
    """The serving entry carries the catalog, not a pointer to it."""
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    published = await _ensure(CONNECTIONS, store, cache)

    raw = await redis_client.get(cache.active_key())
    body = json.loads(raw)
    assert body["version"] == published.version
    assert body["content_hash"] == published.content_hash
    assert body["connections"] == CONNECTIONS


# -- missing durable state --------------------------------------------------


@pytest.mark.asyncio
async def test_missing_durable_state_is_republished_by_the_next_deployment(
    tmp_path, cache, redis_client
):
    """Reconciliation is idempotent from an empty durable root."""
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    first = await _ensure(CONNECTIONS, store, cache)

    (store.root / "active.json").unlink()
    for path in (store.root / "versions").iterdir():
        path.unlink()
    await redis_client.delete(cache.active_key(), cache.version_key(first.version))

    restored = await _ensure(CONNECTIONS, store, cache, reason="app_deploy:startup")
    assert restored.content_hash == first.content_hash
    assert (await store.read_active()).version == restored.version
    assert await store.version_exists(restored.version) is True
    assert (await cache.read_active()).version == restored.version

    again = await _ensure(CONNECTIONS, store, cache, reason="app_deploy:startup")
    assert again.version == restored.version
    assert again.created is False
    assert len(list((store.root / "versions").iterdir())) == 1


# -- source changed but not published ---------------------------------------


@pytest.mark.asyncio
async def test_changed_connections_do_not_reach_requests_until_publication(tmp_path, cache):
    """Requests read the registered catalog, never the current source."""
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    first = await _ensure(CONNECTIONS, store, cache)
    resolver = DelegatedCatalogResolver(cache=cache, store=store)

    changed = _changed(["mem:read", "mem:write"])
    active = await resolver.resolve_active()
    assert active.version == first.version
    assert active.connections == CONNECTIONS
    assert active.connections != changed

    second = await _ensure(changed, store, cache, reason="app_deploy:props_changed")
    assert (await resolver.resolve_active()).version == second.version


# -- damaged projections ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_corrupt_serving_entry_is_repaired_from_durable_state(
    tmp_path, cache, redis_client
):
    """A damaged projection is repaired, not served and not fatal."""
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    published = await _ensure(CONNECTIONS, store, cache)

    await redis_client.set(cache.active_key(), "{not json")
    resolver = DelegatedCatalogResolver(cache=cache, store=store)

    resolved = await resolver.resolve_active()
    assert resolved.version == published.version
    assert resolved.connections == CONNECTIONS
    assert (await cache.read_active()).version == published.version


@pytest.mark.asyncio
async def test_an_unreadable_durable_active_is_refused_without_stale_fallback(
    tmp_path, cache, redis_client
):
    """Damage on both tiers denies; the corrupt entry is never authority."""
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    await _ensure(CONNECTIONS, store, cache)

    await redis_client.set(cache.active_key(), "{not json")
    (store.root / "active.json").write_text("{not json either", encoding="utf-8")

    resolver = DelegatedCatalogResolver(cache=cache, store=store)
    with pytest.raises(CatalogUnavailable) as exc:
        await resolver.resolve_active()
    assert exc.value.reason == "durable_active_unreadable"


# -- residency --------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_ordinary_active_read_does_not_extend_residency(
    tmp_path, cache, redis_client
):
    """Only publication and read-through assign a TTL."""
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    settings = DelegatedCacheSettings.from_connections(
        {"delegated_credentials": {"catalog": {"active_cache_seconds": 40}}}
    )
    await _ensure(CONNECTIONS, store, cache, settings=settings)

    resolver = DelegatedCatalogResolver(cache=cache, store=store, settings=settings)
    await redis_client.expire(cache.active_key(), 12)
    for _ in range(3):
        await resolver.resolve_active()

    assert await redis_client.ttl(cache.active_key()) <= 12


@pytest.mark.asyncio
async def test_read_through_assigns_the_configured_residency(tmp_path, cache, redis_client):
    """Repair installs the configured lifetime, not the remainder."""
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    settings = DelegatedCacheSettings.from_connections(
        {"delegated_credentials": {"catalog": {"active_cache_seconds": 40}}}
    )
    published = await _ensure(CONNECTIONS, store, cache, settings=settings)

    await redis_client.delete(cache.active_key())
    resolver = DelegatedCatalogResolver(cache=cache, store=store, settings=settings)
    assert (await resolver.resolve_active()).version == published.version

    ttl = await redis_client.ttl(cache.active_key())
    assert 30 < ttl <= 40
    assert len(list((store.root / "versions").iterdir())) == 1


@pytest.mark.asyncio
async def test_unavailable_resolution_leaves_durable_catalog_storage_untouched(
    tmp_path, cache, monkeypatch
):
    """A refused read is not a repair attempt: history is append-only here."""
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    await _ensure(CONNECTIONS, store, cache)

    def _digest() -> dict[str, str]:
        out: dict[str, str] = {}
        for path in sorted(store.root.rglob("*")):
            if path.is_file():
                out[str(path.relative_to(store.root))] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        return out

    before = _digest()

    async def _unreachable():
        raise ConnectionError("redis is gone")

    monkeypatch.setattr(cache, "read_active", _unreachable)
    resolver = DelegatedCatalogResolver(cache=cache, store=store)
    with pytest.raises(CatalogUnavailable) as exc:
        await resolver.resolve_active()
    assert exc.value.reason == "cache_unavailable"

    assert _digest() == before
