# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
from types import ModuleType, SimpleNamespace

import pytest

from kdcube_ai_app.infra.plugin.bundle_loader import (
    BundleSpec,
    _bundle_load_done,
    _bundle_load_key,
    _bundle_load_tasks,
    _bundle_static_entrypoint_load_done,
    _bundle_static_entrypoint_load_tasks,
    _maybe_run_bundle_on_load,
    clear_bundle_loader_caches,
    invalidate_static_bundle_entrypoint_loads,
    run_static_bundle_entrypoint_load_once,
)


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("timed out waiting for condition")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_static_entrypoint_load_cleanup_marks_done_after_waiter_cancellation():
    clear_bundle_loader_caches()
    load_key = "test::static-entrypoint::success"
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = 0

    async def _load():
        nonlocal calls
        calls += 1
        started.set()
        await finish.wait()

    waiter = asyncio.create_task(
        run_static_bundle_entrypoint_load_once(
            load_key=load_key,
            load_coro_factory=_load,
        )
    )
    await started.wait()
    assert load_key in _bundle_static_entrypoint_load_tasks

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert load_key in _bundle_static_entrypoint_load_tasks
    finish.set()

    await _wait_until(lambda: load_key not in _bundle_static_entrypoint_load_tasks)
    assert load_key in _bundle_static_entrypoint_load_done
    assert calls == 1

    clear_bundle_loader_caches()


@pytest.mark.asyncio
async def test_bundle_on_load_continues_after_waiter_cancellation():
    clear_bundle_loader_caches()
    spec = BundleSpec(path="/tmp/test-bundle", module="entrypoint")
    config = SimpleNamespace(
        log_level="INFO",
        ai_bundle_spec=SimpleNamespace(id="test-bundle"),
    )
    comm_context = SimpleNamespace(
        actor=SimpleNamespace(tenant_id="tenant-a", project_id="project-a"),
    )
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = 0

    class Bundle:
        async def on_bundle_load(self):
            nonlocal calls
            calls += 1
            started.set()
            await finish.wait()

    load_key = _bundle_load_key(spec, comm_context)
    waiter = asyncio.create_task(
        _maybe_run_bundle_on_load(
            instance=Bundle(),
            mod=ModuleType("test_bundle"),
            spec=spec,
            config=config,
            comm_context=comm_context,
            pg_pool=None,
            redis=None,
        )
    )

    await started.wait()
    assert load_key in _bundle_load_tasks

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert load_key in _bundle_load_tasks
    assert load_key not in _bundle_load_done
    assert calls == 1

    finish.set()
    await _wait_until(lambda: load_key not in _bundle_load_tasks)
    assert load_key in _bundle_load_done
    assert calls == 1

    clear_bundle_loader_caches()

@pytest.mark.asyncio
async def test_static_entrypoint_load_cleanup_allows_retry_after_cancelled_waiter_and_failure():
    clear_bundle_loader_caches()
    load_key = "test::static-entrypoint::failure"
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = 0

    async def _load():
        nonlocal calls
        calls += 1
        started.set()
        await finish.wait()
        raise RuntimeError("load failed")

    waiter = asyncio.create_task(
        run_static_bundle_entrypoint_load_once(
            load_key=load_key,
            load_coro_factory=_load,
        )
    )
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    finish.set()

    await _wait_until(lambda: load_key not in _bundle_static_entrypoint_load_tasks)
    assert load_key not in _bundle_static_entrypoint_load_done
    assert calls == 1

    clear_bundle_loader_caches()


# ----------------------------------------------------------------------------
# Signature-aware short-circuit
#
# These tests cover the behaviour introduced so source edits on disk trigger
# the next HTML-entrypoint request to rebuild without requiring an explicit
# `kdcube reload`. The caller passes a `signature_provider` returning a
# deterministic string fingerprint of the source tree; the coalescer stores
# the captured fingerprint in `_done` on success and only short-circuits when
# the next caller's fingerprint matches.
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signature_aware_skip_when_signature_matches():
    """Two calls with the same `load_key` and the same signature value
    invoke the load coro factory exactly once."""
    clear_bundle_loader_caches()
    load_key = "test::sig::match"
    calls = 0

    async def _load():
        nonlocal calls
        calls += 1

    def _sig() -> str:
        return "sig-X"

    await run_static_bundle_entrypoint_load_once(
        load_key=load_key,
        load_coro_factory=_load,
        signature_provider=_sig,
    )
    await run_static_bundle_entrypoint_load_once(
        load_key=load_key,
        load_coro_factory=_load,
        signature_provider=_sig,
    )
    assert calls == 1
    assert _bundle_static_entrypoint_load_done.get(load_key) == "sig-X"

    clear_bundle_loader_caches()


@pytest.mark.asyncio
async def test_signature_aware_rebuild_when_signature_changes():
    """When the signature provider returns a different value than the one
    captured at the last successful build, the load coro factory is
    invoked again."""
    clear_bundle_loader_caches()
    load_key = "test::sig::change"
    calls = 0
    current_sig = "sig-X"

    async def _load():
        nonlocal calls
        calls += 1

    def _sig() -> str:
        return current_sig

    await run_static_bundle_entrypoint_load_once(
        load_key=load_key,
        load_coro_factory=_load,
        signature_provider=_sig,
    )
    assert calls == 1
    assert _bundle_static_entrypoint_load_done.get(load_key) == "sig-X"

    # Source tree "changes" — provider now reports a different signature.
    current_sig = "sig-Y"

    await run_static_bundle_entrypoint_load_once(
        load_key=load_key,
        load_coro_factory=_load,
        signature_provider=_sig,
    )
    assert calls == 2
    assert _bundle_static_entrypoint_load_done.get(load_key) == "sig-Y"

    clear_bundle_loader_caches()


@pytest.mark.asyncio
async def test_signature_provider_returning_none_falls_back_to_membership():
    """When the provider returns `None`, the membership-based short-circuit
    applies — same as a caller that supplied no provider."""
    clear_bundle_loader_caches()
    load_key = "test::sig::none"
    calls = 0

    async def _load():
        nonlocal calls
        calls += 1

    def _sig_none():
        return None

    await run_static_bundle_entrypoint_load_once(
        load_key=load_key,
        load_coro_factory=_load,
        signature_provider=_sig_none,
    )
    await run_static_bundle_entrypoint_load_once(
        load_key=load_key,
        load_coro_factory=_load,
        signature_provider=_sig_none,
    )
    assert calls == 1
    assert load_key in _bundle_static_entrypoint_load_done

    clear_bundle_loader_caches()


@pytest.mark.asyncio
async def test_signature_provider_raising_falls_back_to_membership():
    """A misbehaving signature provider must not break the request path —
    treated the same as `None` (legacy membership-based short-circuit)."""
    clear_bundle_loader_caches()
    load_key = "test::sig::raise"
    calls = 0

    async def _load():
        nonlocal calls
        calls += 1

    def _sig_boom():
        raise RuntimeError("signature unavailable")

    await run_static_bundle_entrypoint_load_once(
        load_key=load_key,
        load_coro_factory=_load,
        signature_provider=_sig_boom,
    )
    await run_static_bundle_entrypoint_load_once(
        load_key=load_key,
        load_coro_factory=_load,
        signature_provider=_sig_boom,
    )
    assert calls == 1

    clear_bundle_loader_caches()


@pytest.mark.asyncio
async def test_legacy_caller_unaffected_by_signature_dict():
    """Calls without `signature_provider` keep the original behaviour: any
    `_done` entry short-circuits the next call, regardless of value."""
    clear_bundle_loader_caches()
    load_key = "test::sig::legacy"
    calls = 0

    async def _load():
        nonlocal calls
        calls += 1

    await run_static_bundle_entrypoint_load_once(
        load_key=load_key,
        load_coro_factory=_load,
    )
    await run_static_bundle_entrypoint_load_once(
        load_key=load_key,
        load_coro_factory=_load,
    )
    assert calls == 1
    # Legacy callers store `None` as the captured signature.
    assert load_key in _bundle_static_entrypoint_load_done
    assert _bundle_static_entrypoint_load_done[load_key] is None

    clear_bundle_loader_caches()


@pytest.mark.asyncio
async def test_invalidation_drops_signature_entry():
    """`invalidate_static_bundle_entrypoint_loads` clears the signature
    entry; the next call rebuilds even if the signature is unchanged."""
    clear_bundle_loader_caches()
    load_key = "tenant-a::project-a::bundle-x::/storage/root"
    calls = 0

    async def _load():
        nonlocal calls
        calls += 1

    def _sig() -> str:
        return "sig-stable"

    await run_static_bundle_entrypoint_load_once(
        load_key=load_key,
        load_coro_factory=_load,
        signature_provider=_sig,
    )
    assert calls == 1

    removed = invalidate_static_bundle_entrypoint_loads(
        bundle_id="bundle-x", tenant="tenant-a", project="project-a"
    )
    assert removed >= 1
    assert load_key not in _bundle_static_entrypoint_load_done

    await run_static_bundle_entrypoint_load_once(
        load_key=load_key,
        load_coro_factory=_load,
        signature_provider=_sig,
    )
    assert calls == 2

    clear_bundle_loader_caches()


@pytest.mark.asyncio
async def test_concurrent_signature_aware_callers_share_one_task():
    """Concurrent HTML-entrypoint requests with the same signature must
    coalesce into a single build, not race past the lock."""
    clear_bundle_loader_caches()
    load_key = "test::sig::concurrent"
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = 0

    async def _load():
        nonlocal calls
        calls += 1
        started.set()
        await finish.wait()

    def _sig() -> str:
        return "sig-shared"

    tasks = [
        asyncio.create_task(
            run_static_bundle_entrypoint_load_once(
                load_key=load_key,
                load_coro_factory=_load,
                signature_provider=_sig,
            )
        )
        for _ in range(8)
    ]
    await started.wait()
    finish.set()
    await asyncio.gather(*tasks)

    assert calls == 1
    assert _bundle_static_entrypoint_load_done.get(load_key) == "sig-shared"

    clear_bundle_loader_caches()


@pytest.mark.asyncio
async def test_signature_captured_at_entry_even_when_waiter_cancelled():
    """The done-callback must record the signature observed at task
    install time, regardless of whether the original awaiter was
    cancelled (regression for the cancellation-safety fix combined
    with the new signature dict)."""
    clear_bundle_loader_caches()
    load_key = "test::sig::cancelled-waiter"
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = 0

    async def _load():
        nonlocal calls
        calls += 1
        started.set()
        await finish.wait()

    def _sig() -> str:
        return "sig-cancel"

    waiter = asyncio.create_task(
        run_static_bundle_entrypoint_load_once(
            load_key=load_key,
            load_coro_factory=_load,
            signature_provider=_sig,
        )
    )
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    finish.set()
    await _wait_until(lambda: load_key not in _bundle_static_entrypoint_load_tasks)
    assert _bundle_static_entrypoint_load_done.get(load_key) == "sig-cancel"
    assert calls == 1

    clear_bundle_loader_caches()
