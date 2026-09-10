# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio

import pytest

from kdcube_ai_app.apps.chat.proc.app_lifecycle.supervisor import (
    ApplicationLifecycleSupervisor,
    ApplicationPreparation,
)
from kdcube_ai_app.infra.plugin.app_readiness import (
    ApplicationReadinessMode,
    ApplicationReadinessRegistry,
)


def _preparation(
    application_id: str,
    generation: str,
    readiness: ApplicationReadinessMode = ApplicationReadinessMode.INDEPENDENT,
) -> ApplicationPreparation:
    return ApplicationPreparation(
        application_id=application_id,
        generation=generation,
        readiness=readiness,
        payload=None,
    )


@pytest.mark.asyncio
async def test_independent_applications_prepare_concurrently() -> None:
    registry = ApplicationReadinessRegistry()
    started = {"a": asyncio.Event(), "b": asyncio.Event()}
    release = asyncio.Event()

    async def _prepare(item: ApplicationPreparation) -> None:
        started[item.application_id].set()
        await release.wait()

    supervisor = ApplicationLifecycleSupervisor(
        tenant="tenant-a",
        project="project-a",
        registry=registry,
        prepare=_prepare,
        concurrency=2,
    )
    await supervisor.reconcile({
        "a": _preparation("a", "generation-a"),
        "b": _preparation("b", "generation-b"),
    })

    await asyncio.wait_for(asyncio.gather(started["a"].wait(), started["b"].wait()), timeout=1)
    assert supervisor.active_task_count == 2
    release.set()
    await asyncio.wait_for(supervisor.wait_for_current(), timeout=1)

    assert registry.require_ready(tenant="tenant-a", project="project-a", application_id="a")
    assert registry.require_ready(tenant="tenant-a", project="project-a", application_id="b")
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_retry_is_owned_until_preparation_succeeds() -> None:
    registry = ApplicationReadinessRegistry()
    attempts = 0

    async def _prepare(item: ApplicationPreparation) -> None:
        nonlocal attempts
        del item
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary")

    supervisor = ApplicationLifecycleSupervisor(
        tenant="tenant-a",
        project="project-a",
        registry=registry,
        prepare=_prepare,
        retry_initial_seconds=0,
        retry_max_seconds=0,
    )
    await supervisor.reconcile({"app": _preparation("app", "generation-a")})
    await asyncio.wait_for(supervisor.wait_for_current(), timeout=1)

    snapshot = registry.require_ready(
        tenant="tenant-a",
        project="project-a",
        application_id="app",
    )
    assert snapshot is not None
    assert snapshot.attempt == 2
    assert attempts == 2
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_supersession_cancels_only_the_replaced_application() -> None:
    registry = ApplicationReadinessRegistry()
    old_started = asyncio.Event()
    old_cancelled = asyncio.Event()
    stable_release = asyncio.Event()

    async def _prepare(item: ApplicationPreparation) -> None:
        if item.application_id == "changing" and item.generation == "generation-a":
            old_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                old_cancelled.set()
                raise
        if item.application_id == "stable":
            await stable_release.wait()

    supervisor = ApplicationLifecycleSupervisor(
        tenant="tenant-a",
        project="project-a",
        registry=registry,
        prepare=_prepare,
        concurrency=3,
    )
    await supervisor.reconcile({
        "changing": _preparation("changing", "generation-a"),
        "stable": _preparation("stable", "generation-stable"),
    })
    await asyncio.wait_for(old_started.wait(), timeout=1)

    await supervisor.reconcile({
        "changing": _preparation("changing", "generation-b"),
        "stable": _preparation("stable", "generation-stable"),
    })
    await asyncio.wait_for(old_cancelled.wait(), timeout=1)
    stable_release.set()
    await asyncio.wait_for(supervisor.wait_for_current(), timeout=1)

    changing = registry.require_ready(
        tenant="tenant-a",
        project="project-a",
        application_id="changing",
    )
    assert changing is not None
    assert changing.desired_generation == "generation-b"
    assert changing.ready_generation == "generation-b"
    assert registry.require_ready(
        tenant="tenant-a",
        project="project-a",
        application_id="stable",
    )
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_retire_cancels_only_requested_application() -> None:
    registry = ApplicationReadinessRegistry()
    started = {"remove": asyncio.Event(), "stable": asyncio.Event()}
    cancelled = {"remove": asyncio.Event(), "stable": asyncio.Event()}
    stable_release = asyncio.Event()

    async def _prepare(item: ApplicationPreparation) -> None:
        started[item.application_id].set()
        try:
            if item.application_id == "stable":
                await stable_release.wait()
            else:
                await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled[item.application_id].set()
            raise

    supervisor = ApplicationLifecycleSupervisor(
        tenant="tenant-a",
        project="project-a",
        registry=registry,
        prepare=_prepare,
        concurrency=2,
    )
    await supervisor.reconcile({
        "remove": _preparation("remove", "generation-remove"),
        "stable": _preparation("stable", "generation-stable"),
    })
    await asyncio.wait_for(
        asyncio.gather(started["remove"].wait(), started["stable"].wait()),
        timeout=1,
    )

    await supervisor.retire("remove")
    await asyncio.wait_for(cancelled["remove"].wait(), timeout=1)

    assert cancelled["stable"].is_set() is False
    assert "remove" not in supervisor.task_generations()
    assert supervisor.task_generations()["stable"] == "generation-stable"
    assert registry.snapshot(
        tenant="tenant-a",
        project="project-a",
        application_id="remove",
    ) is None
    assert registry.snapshot(
        tenant="tenant-a",
        project="project-a",
        application_id="stable",
    ) is not None

    stable_release.set()
    await asyncio.wait_for(supervisor.wait_for_current(), timeout=1)
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_and_reaps_owned_tasks() -> None:
    registry = ApplicationReadinessRegistry()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _prepare(item: ApplicationPreparation) -> None:
        del item
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    supervisor = ApplicationLifecycleSupervisor(
        tenant="tenant-a",
        project="project-a",
        registry=registry,
        prepare=_prepare,
    )
    await supervisor.reconcile({"app": _preparation("app", "generation-a")})
    await asyncio.wait_for(started.wait(), timeout=1)
    await supervisor.shutdown()

    assert cancelled.is_set()
    assert supervisor.active_task_count == 0
    assert registry.snapshot(
        tenant="tenant-a",
        project="project-a",
        application_id="app",
    ) is None


@pytest.mark.asyncio
async def test_ready_notification_runs_after_state_is_published() -> None:
    registry = ApplicationReadinessRegistry()
    observed_ready = asyncio.Event()

    async def _prepare(item: ApplicationPreparation) -> None:
        del item

    async def _on_ready(item: ApplicationPreparation) -> None:
        registry.require_ready(
            tenant="tenant-a",
            project="project-a",
            application_id=item.application_id,
        )
        observed_ready.set()

    supervisor = ApplicationLifecycleSupervisor(
        tenant="tenant-a",
        project="project-a",
        registry=registry,
        prepare=_prepare,
        on_ready=_on_ready,
    )
    await supervisor.reconcile({"app": _preparation("app", "generation-a")})
    await asyncio.wait_for(observed_ready.wait(), timeout=1)
    await supervisor.shutdown()
