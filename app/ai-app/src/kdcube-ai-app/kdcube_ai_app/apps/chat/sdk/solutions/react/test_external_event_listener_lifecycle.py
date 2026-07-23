# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react import browser as browser_module
from kdcube_ai_app.apps.chat.sdk.solutions.react.browser import ContextBrowser
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.listener import (
    LiveExternalEventOwnerLease,
    run_live_external_event_listener_loop,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx


class _Log:
    def log(self, *_args, **_kwargs) -> None:
        return None


@pytest.mark.asyncio
async def test_concurrent_listener_start_acquires_one_owner_lease(monkeypatch) -> None:
    source = SimpleNamespace()
    runtime = RuntimeCtx(
        tenant="tenant",
        project="project",
        conversation_id="conversation",
        turn_id="turn_current",
        bundle_id="bundle@1",
        external_event_source=source,
    )
    browser = ContextBrowser(runtime_ctx=runtime)
    browser._timeline = SimpleNamespace(last_external_event_id="")
    browser._external_event_hooks = [object()]
    browser._external_event_listener_requested = True

    acquire_calls = 0

    async def _acquire(**_kwargs):
        nonlocal acquire_calls
        acquire_calls += 1
        await asyncio.sleep(0.01)
        return LiveExternalEventOwnerLease(lease_token="lease-1", lease_epoch=1)

    async def _listen(*, stop_event, **_kwargs):
        await stop_event.wait()

    async def _release(**_kwargs):
        return None

    monkeypatch.setattr(browser_module, "acquire_live_external_event_owner", _acquire)
    monkeypatch.setattr(browser_module, "run_live_external_event_listener_loop", _listen)
    monkeypatch.setattr(browser_module, "release_live_external_event_owner", _release)

    await asyncio.gather(
        browser.ensure_external_event_listener(),
        browser.ensure_external_event_listener(),
    )
    try:
        assert acquire_calls == 1
        assert browser._external_event_task is not None
        assert not browser._external_event_task.done()
    finally:
        await browser.stop_external_event_listener()


@pytest.mark.asyncio
async def test_stop_waits_for_inflight_start_and_releases_owner_lease(monkeypatch) -> None:
    source = SimpleNamespace()
    runtime = RuntimeCtx(
        tenant="tenant",
        project="project",
        conversation_id="conversation",
        turn_id="turn_current",
        bundle_id="bundle@1",
        external_event_source=source,
    )
    browser = ContextBrowser(runtime_ctx=runtime)
    browser._timeline = SimpleNamespace(last_external_event_id="")
    browser._external_event_hooks = [object()]
    browser._external_event_listener_requested = True

    acquire_entered = asyncio.Event()
    allow_acquire = asyncio.Event()
    released_tokens = []

    async def _acquire(**_kwargs):
        acquire_entered.set()
        await allow_acquire.wait()
        return LiveExternalEventOwnerLease(lease_token="lease-1", lease_epoch=1)

    async def _listen(*, stop_event, **_kwargs):
        await stop_event.wait()

    async def _release(*, lease_token, **_kwargs):
        released_tokens.append(lease_token)

    monkeypatch.setattr(browser_module, "acquire_live_external_event_owner", _acquire)
    monkeypatch.setattr(browser_module, "run_live_external_event_listener_loop", _listen)
    monkeypatch.setattr(browser_module, "release_live_external_event_owner", _release)

    start_task = asyncio.create_task(browser.start_external_event_listener())
    await acquire_entered.wait()
    stop_task = asyncio.create_task(browser.stop_external_event_listener())
    await asyncio.sleep(0)
    assert not stop_task.done()

    allow_acquire.set()
    await start_task
    await stop_task

    assert released_tokens == ["lease-1"]
    assert browser._external_event_task is None
    assert browser._external_lease_token == ""


@pytest.mark.asyncio
async def test_scheduled_start_cannot_run_after_terminal_stop(monkeypatch) -> None:
    source = SimpleNamespace()
    runtime = RuntimeCtx(
        conversation_id="conversation",
        turn_id="turn_current",
        external_event_source=source,
    )
    browser = ContextBrowser(runtime_ctx=runtime)
    browser._timeline = SimpleNamespace(last_external_event_id="")
    browser._external_event_hooks = [object()]
    browser._external_event_listener_requested = True

    acquire_calls = 0

    async def _acquire(**_kwargs):
        nonlocal acquire_calls
        acquire_calls += 1
        return LiveExternalEventOwnerLease(lease_token="late-lease", lease_epoch=1)

    monkeypatch.setattr(browser_module, "acquire_live_external_event_owner", _acquire)

    await browser.stop_external_event_listener()
    await browser.start_external_event_listener()

    assert acquire_calls == 0
    assert browser._external_event_task is None


@pytest.mark.asyncio
async def test_superseded_turn_cannot_reacquire_owner_lease(monkeypatch) -> None:
    source = SimpleNamespace()
    runtime = RuntimeCtx(
        conversation_id="conversation",
        turn_id="turn_stale",
        external_event_source=source,
    )
    browser = ContextBrowser(runtime_ctx=runtime)
    browser._timeline = SimpleNamespace(last_external_event_id="")
    browser._external_event_hooks = [object()]
    browser._external_event_listener_requested = True
    browser._mark_external_event_lane_superseded(
        owner_turn_id="turn_new",
        handler_status="open",
        phase="owner_lease_lost",
    )

    acquire_calls = 0

    async def _acquire(**_kwargs):
        nonlocal acquire_calls
        acquire_calls += 1
        return LiveExternalEventOwnerLease(lease_token="lease-stale", lease_epoch=2)

    monkeypatch.setattr(browser_module, "acquire_live_external_event_owner", _acquire)

    await browser.ensure_external_event_listener()

    assert acquire_calls == 0
    assert browser._external_event_task is None


@pytest.mark.asyncio
async def test_refresh_rejection_supersedes_even_when_replacement_names_same_turn() -> None:
    owner = SimpleNamespace(turn_id="turn_current", lease_token="lease-new")

    class _Source:
        async def refresh_owner(self, **_kwargs):
            return None

        async def get_owner(self):
            return owner

    owner_lost = []
    await run_live_external_event_listener_loop(
        source_getter=_Source,
        runtime_ctx=RuntimeCtx(turn_id="turn_current"),
        stop_event=asyncio.Event(),
        listener_id="listener-old",
        lease_token_getter=lambda: "lease-old",
        last_cursor_getter=lambda: "",
        apply_events=lambda _events: asyncio.sleep(0, result=0),
        log=_Log(),
        on_owner_lost=lambda reason, current: owner_lost.append((reason, current)),
    )

    assert owner_lost == [("owner_lease_refresh_rejected", owner)]
