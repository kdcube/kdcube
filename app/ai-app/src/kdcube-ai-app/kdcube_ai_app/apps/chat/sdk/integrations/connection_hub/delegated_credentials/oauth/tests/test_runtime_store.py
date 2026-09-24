# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import inspect
import threading
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth import (
    runtime_store,
)
from kdcube_ai_app.infra.plugin import bundle_store


@pytest.mark.asyncio
async def test_authority_props_read_does_not_block_the_event_loop(monkeypatch):
    assert inspect.iscoroutinefunction(runtime_store._connections)

    events: list[str] = []
    read_started = threading.Event()
    release_read = threading.Event()
    expected = {
        "delegated_credentials": {
            "authority": {"backend": "redis-migration-source"}
        }
    }

    def blocking_read(**_kwargs):
        events.append("read-started")
        read_started.set()
        release_read.wait(timeout=1)
        events.append("read-finished")
        return {"connections": expected}

    monkeypatch.setattr(
        bundle_store,
        "_get_bundle_props_from_authority",
        blocking_read,
    )
    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace()),
    )

    async def observe_loop_progress() -> None:
        while not read_started.is_set():
            await asyncio.sleep(0)
        events.append("loop-progressed")
        release_read.set()

    resolved, _ = await asyncio.gather(
        runtime_store._connections(
            request,
            tenant="home",
            project="demo",
            bundle_id=runtime_store.DEFAULT_CONNECTION_HUB_BUNDLE_ID,
            connections=None,
        ),
        observe_loop_progress(),
    )

    assert resolved == expected
    assert events.index("loop-progressed") < events.index("read-finished")
