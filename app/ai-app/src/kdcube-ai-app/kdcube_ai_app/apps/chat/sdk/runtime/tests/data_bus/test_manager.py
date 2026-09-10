# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.worker import (
    DataBusRuntimeManager,
    _DataBusWorkerKey,
)


class _Worker:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_remove_bundle_cancels_only_that_bundles_worker() -> None:
    manager = DataBusRuntimeManager(
        redis=object(),
        redis_url=None,
        tenant="tenant",
        project="project",
        instance_id="instance",
    )
    removed_worker = _Worker()
    stable_worker = _Worker()
    removed_task = asyncio.create_task(asyncio.Event().wait())
    stable_task = asyncio.create_task(asyncio.Event().wait())
    manager._workers = {
        _DataBusWorkerKey(bundle_id="remove@1-0"): (
            removed_task,
            "removed-signature",
            removed_worker,
        ),
        _DataBusWorkerKey(bundle_id="stable@1-0"): (
            stable_task,
            "stable-signature",
            stable_worker,
        ),
    }

    await manager.remove_bundle("remove@1-0")

    assert removed_worker.stopped is True
    assert removed_task.cancelled()
    assert stable_worker.stopped is False
    assert stable_task.cancelled() is False
    assert set(manager._workers) == {
        _DataBusWorkerKey(bundle_id="stable@1-0"),
    }
    await manager.shutdown()
