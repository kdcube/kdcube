# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Durable storage work must not run on the proc event loop.

Delegated catalog and card storage sit on a shared mount, where a single read
can take far longer than a local one. If any of that work ran inline, one slow
mount would stall every other request the process is serving.

The probe makes the synchronous primitives deliberately slow and measures how
long the loop is ever unable to run a timer. Dispatched work leaves the loop
responsive; inline work does not.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
import uuid

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials import durable_io
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.cache import (
    DelegatedCardRuntimeCache,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.model import (
    CARD_STATE_ACTIVE,
    CardAuthority,
    NamedServiceSelection,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.service import (
    DelegatedCardService,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cards.store import (
    BundleStorageDelegatedCardStore,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.publisher import (
    ensure_delegated_catalog,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.runtime_cache import (
    DelegatedCatalogRuntimeCache,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.store import (
    BundleStorageDelegatedCatalogStore,
)

CONNECTIONS = {"delegated_credentials": {"oauth": {"enabled": True, "resources": []}}}
SUBJECT_HASH = hashlib.sha256(b"platform-user-1").hexdigest()
NOW = 1_780_000_000

SLOW_CALL_SECONDS = 0.12
WATCHDOG_INTERVAL = 0.01
# Generous: a dispatched call costs a thread handoff, never the call itself.
MAX_TOLERATED_STALL = SLOW_CALL_SECONDS / 2

pytestmark = pytest.mark.skipif(
    not os.environ.get("REDIS_URL"),
    reason="REDIS_URL is not set; durable storage probes are skipped",
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
def scope() -> tuple[str, str]:
    return f"t-{uuid.uuid4().hex[:8]}", f"p-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def slow_storage(monkeypatch):
    """Every synchronous filesystem primitive takes a visible amount of time."""
    read = durable_io._read_text
    write = durable_io._write_text_atomic
    children = durable_io._list_children

    def _slow_read(path):
        time.sleep(SLOW_CALL_SECONDS)
        return read(path)

    def _slow_write(path, text):
        time.sleep(SLOW_CALL_SECONDS)
        return write(path, text)

    def _slow_children(path):
        time.sleep(SLOW_CALL_SECONDS)
        return children(path)

    monkeypatch.setattr(durable_io, "_read_text", _slow_read)
    monkeypatch.setattr(durable_io, "_write_text_atomic", _slow_write)
    monkeypatch.setattr(durable_io, "_list_children", _slow_children)


class _LoopProbe:
    """The longest interval in which the loop could not run a timer."""

    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.worst = 0.0

    async def __aenter__(self) -> "_LoopProbe":
        self._task = asyncio.create_task(self._run())
        await asyncio.sleep(WATCHDOG_INTERVAL * 2)
        self.worst = 0.0
        return self

    async def __aexit__(self, *_exc) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task

    async def _run(self) -> None:
        last = time.perf_counter()
        while not self._stop.is_set():
            await asyncio.sleep(WATCHDOG_INTERVAL)
            now = time.perf_counter()
            self.worst = max(self.worst, now - last - WATCHDOG_INTERVAL)
            last = now


def _authority(access_id: str) -> CardAuthority:
    return CardAuthority(
        access_id=access_id,
        client_id="automation:abc",
        grantor_subject="platform-user-1",
        delegate_subject="integration:automation:abc",
        source="manual",
        card_revision=1,
        state=CARD_STATE_ACTIVE,
        resource_grants={"https://ex/mcp": ("slack:read",)},
        named_service_operations=NamedServiceSelection.none(),
        created_at=NOW,
        expires_at=NOW + 3600,
    )


@pytest.mark.asyncio
async def test_catalog_publication_keeps_the_loop_responsive(
    tmp_path, redis_client, scope, slow_storage
):
    tenant, project = scope
    cache = DelegatedCatalogRuntimeCache(redis_client, tenant=tenant, project=project)
    store = BundleStorageDelegatedCatalogStore(tmp_path)

    started = time.perf_counter()
    async with _LoopProbe() as probe:
        await ensure_delegated_catalog(
            connections=CONNECTIONS, store=store, cache=cache, reason="probe"
        )
    elapsed = time.perf_counter() - started

    assert elapsed > SLOW_CALL_SECONDS, "the slow primitives were not exercised"
    assert probe.worst < MAX_TOLERATED_STALL, (
        f"the loop stalled for {probe.worst:.3f}s during publication"
    )


@pytest.mark.asyncio
async def test_card_commit_keeps_the_loop_responsive(
    tmp_path, redis_client, scope, slow_storage
):
    tenant, project = scope
    cache = DelegatedCardRuntimeCache(redis_client, tenant=tenant, project=project)
    store = BundleStorageDelegatedCardStore(tmp_path)
    service = DelegatedCardService(store=store, cache=cache)

    started = time.perf_counter()
    async with _LoopProbe() as probe:
        await service.commit(
            _authority("aut_probe1"),
            subject_hash=SUBJECT_HASH,
            expected_revision=0,
            now=NOW,
        )
    elapsed = time.perf_counter() - started

    assert elapsed > SLOW_CALL_SECONDS, "the slow primitives were not exercised"
    assert probe.worst < MAX_TOLERATED_STALL, (
        f"the loop stalled for {probe.worst:.3f}s during a card commit"
    )
