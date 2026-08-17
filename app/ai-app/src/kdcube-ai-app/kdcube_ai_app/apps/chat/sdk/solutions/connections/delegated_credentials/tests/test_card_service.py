# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Card mutation protocol against real Redis and durable storage."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.cache import (
    DelegatedCardRuntimeCache,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.model import (
    CARD_STATE_ACTIVE,
    CARD_STATE_REVOKED,
    CardAuthority,
    CardCurrentPointer,
    NamedServiceSelection,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.resolver import (
    CardUnavailable,
    DelegatedCardResolver,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.service import (
    CardCommitFailed,
    CardConflict,
    DelegatedCardService,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.store import (
    BundleStorageDelegatedCardStore,
)

SUBJECT_HASH = hashlib.sha256(b"platform-user-1").hexdigest()
ACCESS_ID = "aut_abc123"
NOW = 1_780_000_000

pytestmark = pytest.mark.skipif(
    not os.environ.get("REDIS_URL"),
    reason="REDIS_URL is not set; card mutation tests are skipped",
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
def cache(redis_client) -> DelegatedCardRuntimeCache:
    return DelegatedCardRuntimeCache(
        redis_client, tenant=f"t-{uuid.uuid4().hex[:8]}", project=f"p-{uuid.uuid4().hex[:8]}"
    )


@pytest.fixture
def store(tmp_path) -> BundleStorageDelegatedCardStore:
    return BundleStorageDelegatedCardStore(tmp_path)


@pytest.fixture
def service(store, cache) -> DelegatedCardService:
    return DelegatedCardService(store=store, cache=cache)


def _authority(*, revision: int = 1, expires_at: int = NOW + 3600) -> CardAuthority:
    return CardAuthority(
        access_id=ACCESS_ID,
        client_id="automation:abc",
        grantor_subject="platform-user-1",
        delegate_subject="integration:automation:abc",
        source="manual",
        label="CI bot",
        card_revision=revision,
        catalog_version="delegated_catalog_2026-08-11-10-30-00-123_d4e5f6a7b8c9",
        state=CARD_STATE_ACTIVE,
        resource_grants={"https://ex/mcp": ("slack:read",)},
        named_service_operations=NamedServiceSelection.none(),
        created_at=NOW,
        expires_at=expires_at,
    )


@pytest.mark.asyncio
async def test_create_commits_durably_before_exposing_authority(service, store, cache):
    pointer = await service.commit(
        _authority(), subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW
    )

    assert pointer.card_revision == 1
    durable = await store.read_current_authority(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID
    )
    assert durable is not None and durable[1].card_revision == 1
    entry = await cache.read(ACCESS_ID)
    assert entry.is_card and entry.card_revision == 1
    assert await cache.index_members(subject_hash=SUBJECT_HASH, now=NOW) == [ACCESS_ID]


@pytest.mark.asyncio
async def test_a_stale_expected_revision_is_rejected(service):
    await service.commit(_authority(), subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW)

    with pytest.raises(CardConflict) as exc:
        await service.commit(
            _authority(revision=2), subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW
        )
    # The lost-update check reads durable state, not the cache projection.
    assert exc.value.reason == "card_revision_moved"
    assert exc.value.current_revision == 1


@pytest.mark.asyncio
async def test_a_slow_commit_keeps_writers_out_after_its_marker_expires(
    service, store, cache, monkeypatch
):
    """The section, not the marker, is what excludes a second writer.

    The marker carries a short fixed residency. A durable write slower than
    that residency would let a second mutation in if exclusion depended on the
    marker alone, producing two revisions with the same number and a lost
    update.
    """
    await service.commit(_authority(), subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW)

    original = store.write_revision
    started = asyncio.Event()

    async def _slow_write(**kwargs):
        started.set()
        # The marker expires mid-write; only the section still holds.
        await cache._redis.delete(cache.card_key(ACCESS_ID))
        await asyncio.sleep(0.3)
        return await original(**kwargs)

    monkeypatch.setattr(store, "write_revision", _slow_write)

    async def _second_writer():
        await started.wait()
        await asyncio.sleep(0.05)
        return await service.commit(
            _authority(revision=2), subject_hash=SUBJECT_HASH, expected_revision=1, now=NOW
        )

    results = await asyncio.gather(
        service.commit(
            _authority(revision=2), subject_hash=SUBJECT_HASH, expected_revision=1, now=NOW
        ),
        _second_writer(),
        return_exceptions=True,
    )
    committed = [item for item in results if isinstance(item, CardCurrentPointer)]
    assert len(committed) == 1, "a second writer committed behind the expired marker"

    names = await store.list_revision_names(subject_hash=SUBJECT_HASH, access_id=ACCESS_ID)
    assert len(names) == 2


@pytest.mark.asyncio
async def test_a_marker_left_by_another_mutation_is_not_displaced(service, cache):
    await service.commit(_authority(), subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW)
    await cache.claim_transition(
        ACCESS_ID, mutation_id="other", expected_revision=1, ttl_seconds=15
    )

    with pytest.raises(CardConflict) as exc:
        await service.commit(
            _authority(revision=2), subject_hash=SUBJECT_HASH, expected_revision=1, now=NOW
        )
    assert exc.value.reason == "card_transition_not_claimed"


@pytest.mark.asyncio
async def test_a_failed_durable_commit_leaves_the_previous_revision_serving(
    service, store, cache, monkeypatch
):
    await service.commit(_authority(), subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW)

    async def _boom(**kwargs):
        raise OSError("storage down")

    monkeypatch.setattr(store, "write_revision", _boom)
    with pytest.raises(CardCommitFailed):
        await service.commit(
            _authority(revision=2), subject_hash=SUBJECT_HASH, expected_revision=1, now=NOW
        )

    # The marker was released, so a read-through restores the committed revision.
    assert await cache.read(ACCESS_ID) is None
    resolver = DelegatedCardResolver(cache=cache, store=store)
    monkeypatch.undo()
    resolved = await resolver.resolve(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID, now=NOW
    )
    assert resolved.card_revision == 1


@pytest.mark.asyncio
async def test_requests_fail_closed_while_a_mutation_owns_the_card(service, store, cache):
    await service.commit(_authority(), subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW)
    await cache.claim_transition(
        ACCESS_ID, mutation_id="in-flight", expected_revision=1, ttl_seconds=15
    )

    resolver = DelegatedCardResolver(cache=cache, store=store)
    with pytest.raises(CardUnavailable) as exc:
        await resolver.resolve(subject_hash=SUBJECT_HASH, access_id=ACCESS_ID, now=NOW)
    assert exc.value.reason == "card_updating"


@pytest.mark.asyncio
async def test_revoke_commits_durable_state_and_denies_immediately(service, store, cache):
    await service.commit(_authority(), subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW)

    pointer = await service.revoke(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID, expected_revision=1
    )
    assert pointer is not None and pointer.state == CARD_STATE_REVOKED

    _, durable = await store.read_current_authority(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID
    )
    assert durable.state == CARD_STATE_REVOKED
    assert durable.card_revision == 2

    resolver = DelegatedCardResolver(cache=cache, store=store)
    assert await resolver.resolve(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID, now=NOW
    ) is None
    assert await cache.index_members(subject_hash=SUBJECT_HASH, now=NOW) == []


@pytest.mark.asyncio
async def test_revocation_survives_the_tombstone_expiring(service, store, cache, redis_client):
    await service.commit(_authority(), subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW)
    await service.revoke(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID, expected_revision=1
    )
    await redis_client.delete(cache.card_key(ACCESS_ID))

    resolver = DelegatedCardResolver(cache=cache, store=store)
    assert await resolver.resolve(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID, now=NOW
    ) is None
    # Denial came from durable state; no active projection was rebuilt.
    assert await cache.read(ACCESS_ID) is None


@pytest.mark.asyncio
async def test_history_is_retained_across_revisions(service, store):
    await service.commit(_authority(), subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW)
    await service.commit(
        _authority(revision=2), subject_hash=SUBJECT_HASH, expected_revision=1, now=NOW
    )
    await service.revoke(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID, expected_revision=2
    )

    names = await store.list_revision_names(subject_hash=SUBJECT_HASH, access_id=ACCESS_ID)
    assert len(names) == 3
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_durable_revisions_contain_no_credential_material(service, store):
    await service.commit(_authority(), subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW)
    names = await store.list_revision_names(subject_hash=SUBJECT_HASH, access_id=ACCESS_ID)
    body = store.revision_path(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID, revision_name=names[0]
    ).read_text(encoding="utf-8")

    for forbidden in ("access_token", "refresh_token", "session_id"):
        assert forbidden not in body


@pytest.mark.asyncio
async def test_an_expired_card_is_not_indexed_or_served(service, cache, store):
    await service.commit(
        _authority(expires_at=NOW - 1), subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW
    )

    assert await cache.index_members(subject_hash=SUBJECT_HASH, now=NOW) == []
    resolver = DelegatedCardResolver(cache=cache, store=store)
    assert await resolver.resolve(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID, now=NOW
    ) is None


@pytest.mark.asyncio
async def test_read_through_does_not_re_cache_expired_authority(
    service, cache, store, redis_client
):
    """Recovery restores authority, never resurrects it."""
    await service.commit(
        _authority(expires_at=NOW - 1), subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW
    )
    await redis_client.delete(cache.card_key(ACCESS_ID))

    resolver = DelegatedCardResolver(cache=cache, store=store)
    assert await resolver.resolve(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID, now=NOW
    ) is None
    assert await redis_client.exists(cache.card_key(ACCESS_ID)) == 0


@pytest.mark.asyncio
async def test_a_damaged_projection_is_repaired_from_durable_state(service, store, cache, redis_client):
    """Unusable cache data must not read as a revoked card."""
    await service.commit(_authority(), subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW)
    await redis_client.set(cache.card_key(ACCESS_ID), "{truncated")

    resolver = DelegatedCardResolver(cache=cache, store=store)
    resolved = await resolver.resolve(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID, now=NOW
    )
    assert resolved is not None and resolved.card_revision == 1
    # The projection was repaired, not left damaged.
    assert (await cache.read(ACCESS_ID)).card_revision == 1


@pytest.mark.asyncio
async def test_a_damaged_projection_without_a_durable_source_is_unavailable(cache, redis_client):
    """The guard path has no store: it must report unavailability, not denial."""
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.cache import (
        CardCacheUnusable,
    )

    await redis_client.set(cache.card_key(ACCESS_ID), "{truncated")
    with pytest.raises(CardCacheUnusable) as exc:
        await cache.read(ACCESS_ID)
    assert exc.value.reason == "cached_card_not_decodable"


@pytest.mark.asyncio
async def test_an_idle_long_lived_card_stays_listed_and_revocable(service, store, cache):
    """An OAuth card idle past the old fixed index lifetime is still discoverable.

    The index scores each card by its own expires_at, so nothing about one
    card's lifetime, or about a week passing without writes, can hide another.
    """
    long_lived = _authority(revision=1, expires_at=NOW + 180 * 24 * 3600)
    await service.commit(long_lived, subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW)

    eight_days_later = NOW + 8 * 24 * 3600
    resolver = DelegatedCardResolver(cache=cache, store=store)
    listed = await resolver.list_active(subject_hash=SUBJECT_HASH, now=eight_days_later)
    assert [item.access_id for item in listed] == [ACCESS_ID]

    pointer = await service.revoke(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID, expected_revision=1
    )
    assert pointer is not None and pointer.state == CARD_STATE_REVOKED
    assert await resolver.list_active(subject_hash=SUBJECT_HASH, now=eight_days_later) == []


@pytest.mark.asyncio
async def test_a_short_card_expiring_does_not_hide_a_long_one(service, store, cache):
    await service.commit(
        _authority(expires_at=NOW + 60), subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW
    )
    base = _authority()
    long_lived = CardAuthority(
        access_id="oauth-longlived",
        client_id=base.client_id,
        grantor_subject=base.grantor_subject,
        delegate_subject=base.delegate_subject,
        source="oauth",
        card_revision=1,
        resource_grants=base.resource_grants,
        named_service_operations=base.named_service_operations,
        created_at=NOW,
        expires_at=NOW + 180 * 24 * 3600,
    )
    await service.commit(long_lived, subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW)

    later = NOW + 8 * 24 * 3600
    resolver = DelegatedCardResolver(cache=cache, store=store)
    listed = await resolver.list_active(subject_hash=SUBJECT_HASH, now=later)
    assert [item.access_id for item in listed] == ["oauth-longlived"]


# -- interruption windows ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_revision_written_without_becoming_current_is_not_authority(
    service, store, cache, monkeypatch
):
    """The pointer is the commit point, not the revision file.

    An interruption between ``write_revision`` and ``advance_current`` leaves an
    orphan revision on disk; it must never be served.
    """
    await service.commit(_authority(), subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW)

    async def _boom(**kwargs):
        raise OSError("interrupted before the pointer advanced")

    monkeypatch.setattr(store, "advance_current", _boom)
    with pytest.raises(CardCommitFailed):
        await service.commit(
            _authority(revision=2), subject_hash=SUBJECT_HASH, expected_revision=1, now=NOW
        )
    monkeypatch.undo()

    # The orphan revision exists, and the pointer still names the committed one.
    revisions = await store.list_revision_names(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID
    )
    assert len(revisions) == 2
    current = await store.read_current_authority(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID
    )
    assert current[1].card_revision == 1

    assert await cache.read(ACCESS_ID) is None
    resolver = DelegatedCardResolver(cache=cache, store=store)
    resolved = await resolver.resolve(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID, now=NOW
    )
    assert resolved.card_revision == 1


@pytest.mark.asyncio
async def test_a_committed_revision_is_restored_after_the_projection_write_fails(
    service, store, cache, redis_client, monkeypatch
):
    """Durable commit is the point of no return.

    When the projection write fails after it, read-through must restore the NEW
    revision — never the superseded one.
    """
    await service.commit(_authority(), subject_hash=SUBJECT_HASH, expected_revision=0, now=NOW)

    async def _boom(*args, **kwargs):
        raise ConnectionError("projection write failed")

    monkeypatch.setattr(cache, "commit_projection", _boom)
    with pytest.raises(ConnectionError):
        await service.commit(
            _authority(revision=2), subject_hash=SUBJECT_HASH, expected_revision=1, now=NOW
        )
    monkeypatch.undo()

    current = await store.read_current_authority(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID
    )
    assert current[1].card_revision == 2

    # The marker outlives the failure; readers fail closed until it expires.
    resolver = DelegatedCardResolver(cache=cache, store=store)
    with pytest.raises(CardUnavailable) as exc:
        await resolver.resolve(subject_hash=SUBJECT_HASH, access_id=ACCESS_ID, now=NOW)
    assert exc.value.reason == "card_updating"

    # The marker expires; read-through resolves the committed revision.
    await redis_client.delete(cache.card_key(ACCESS_ID))
    resolved = await resolver.resolve(
        subject_hash=SUBJECT_HASH, access_id=ACCESS_ID, now=NOW
    )
    assert resolved.card_revision == 2
