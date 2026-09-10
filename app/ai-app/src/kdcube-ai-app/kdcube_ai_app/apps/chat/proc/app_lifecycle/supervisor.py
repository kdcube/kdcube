# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from kdcube_ai_app.infra.plugin.app_readiness import (
    ApplicationLifecycleState,
    ApplicationReadinessMode,
    ApplicationReadinessRegistry,
    DesiredApplicationState,
)


@dataclass(frozen=True)
class ApplicationPreparation:
    application_id: str
    generation: str
    readiness: ApplicationReadinessMode
    payload: Any


@dataclass(frozen=True)
class _OwnedPreparationTask:
    generation: str
    task: asyncio.Task[None]


class ApplicationLifecycleSupervisor:
    """Strong owner for process-local preparation of configured applications."""

    def __init__(
        self,
        *,
        tenant: str,
        project: str,
        registry: ApplicationReadinessRegistry,
        prepare: Callable[[ApplicationPreparation], Awaitable[None]],
        on_ready: Callable[[ApplicationPreparation], Awaitable[None]] | None = None,
        concurrency: int = 4,
        retry_initial_seconds: float = 2.0,
        retry_max_seconds: float = 60.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        self.tenant = str(tenant).strip()
        self.project = str(project).strip()
        self.registry = registry
        self._prepare = prepare
        self._on_ready = on_ready
        self._semaphore = asyncio.Semaphore(max(1, int(concurrency)))
        self._retry_initial_seconds = max(0.0, float(retry_initial_seconds))
        self._retry_max_seconds = max(
            self._retry_initial_seconds,
            float(retry_max_seconds),
        )
        self._sleep = sleep
        self._logger = logger or logging.getLogger(__name__)
        self._lock = asyncio.Lock()
        self._tasks: dict[str, _OwnedPreparationTask] = {}
        self._retired_tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    @property
    def active_task_count(self) -> int:
        current = sum(1 for owned in self._tasks.values() if not owned.task.done())
        retired = sum(1 for task in self._retired_tasks if not task.done())
        return current + retired

    def task_generations(self) -> dict[str, str]:
        return {
            application_id: owned.generation
            for application_id, owned in self._tasks.items()
            if not owned.task.done()
        }

    async def reconcile(
        self,
        preparations: Mapping[str, ApplicationPreparation],
        *,
        force: set[str] | None = None,
    ) -> None:
        if self._closed:
            raise RuntimeError("Application lifecycle supervisor is closed")

        normalized = {
            str(application_id).strip(): preparation
            for application_id, preparation in preparations.items()
        }
        force_ids = {str(value).strip() for value in (force or set())}
        self.registry.replace_desired(
            tenant=self.tenant,
            project=self.project,
            applications={
                application_id: DesiredApplicationState(
                    generation=preparation.generation,
                    readiness=preparation.readiness,
                )
                for application_id, preparation in normalized.items()
            },
        )
        for application_id in force_ids:
            preparation = normalized.get(application_id)
            if preparation is not None:
                self.registry.transition(
                    tenant=self.tenant,
                    project=self.project,
                    application_id=application_id,
                    generation=preparation.generation,
                    state=ApplicationLifecycleState.PENDING,
                    attempt=0,
                )

        async with self._lock:
            for application_id, owned in tuple(self._tasks.items()):
                desired = normalized.get(application_id)
                if (
                    desired is None
                    or desired.generation != owned.generation
                    or application_id in force_ids
                ):
                    if not owned.task.done():
                        owned.task.cancel()
                        self._retired_tasks.add(owned.task)
                        owned.task.add_done_callback(self._retired_task_done)
                    self._tasks.pop(application_id, None)

            for application_id, preparation in normalized.items():
                owned = self._tasks.get(application_id)
                if owned is not None and not owned.task.done():
                    continue
                snapshot = self.registry.snapshot(
                    tenant=self.tenant,
                    project=self.project,
                    application_id=application_id,
                )
                if (
                    application_id not in force_ids
                    and snapshot is not None
                    and snapshot.ready
                    and snapshot.desired_generation == preparation.generation
                ):
                    continue
                task = asyncio.create_task(
                    self._run_preparation(preparation),
                    name=f"app-prepare:{application_id}:{preparation.generation[:12]}",
                )
                self._tasks[application_id] = _OwnedPreparationTask(
                    generation=preparation.generation,
                    task=task,
                )
                task.add_done_callback(
                    lambda completed, app_id=application_id: self._task_done(app_id, completed)
                )

    async def retire(self, application_id: str) -> None:
        """Cancel and forget one application while retaining sibling tasks."""
        normalized_id = str(application_id or "").strip()
        if not normalized_id:
            raise ValueError("application_id is required")
        self.registry.remove_application(
            tenant=self.tenant,
            project=self.project,
            application_id=normalized_id,
        )
        async with self._lock:
            owned = self._tasks.pop(normalized_id, None)
            if owned is None or owned.task.done():
                return
            owned.task.cancel()
            self._retired_tasks.add(owned.task)
            owned.task.add_done_callback(self._retired_task_done)

    async def quiesce(self, application_id: str) -> None:
        """Cancel this application's preparation without removing readiness state."""
        normalized_id = str(application_id or "").strip()
        if not normalized_id:
            raise ValueError("application_id is required")
        task: asyncio.Task[None] | None = None
        async with self._lock:
            owned = self._tasks.pop(normalized_id, None)
            if owned is None or owned.task.done():
                return
            task = owned.task
            task.cancel()
            self._retired_tasks.add(task)
            task.add_done_callback(self._retired_task_done)
        assert task is not None
        await asyncio.gather(task, return_exceptions=True)

    async def _run_preparation(self, preparation: ApplicationPreparation) -> None:
        attempt = 0
        delay = self._retry_initial_seconds
        while True:
            attempt += 1
            if not self.registry.transition(
                tenant=self.tenant,
                project=self.project,
                application_id=preparation.application_id,
                generation=preparation.generation,
                state=ApplicationLifecycleState.PREPARING,
                attempt=attempt,
            ):
                return
            try:
                async with self._semaphore:
                    await self._prepare(preparation)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                if not self.registry.transition(
                    tenant=self.tenant,
                    project=self.project,
                    application_id=preparation.application_id,
                    generation=preparation.generation,
                    state=ApplicationLifecycleState.RETRYING,
                    attempt=attempt,
                    error=exc,
                    retry_at=retry_at.isoformat(),
                ):
                    return
                self._logger.warning(
                    "Application preparation failed; retrying: application=%s generation=%s "
                    "attempt=%s delay_seconds=%.3f error=%s",
                    preparation.application_id,
                    preparation.generation,
                    attempt,
                    delay,
                    exc,
                    exc_info=True,
                )
                await self._sleep(delay)
                delay = min(
                    self._retry_max_seconds,
                    max(self._retry_initial_seconds, delay * 2),
                )
                continue

            transitioned = self.registry.transition(
                tenant=self.tenant,
                project=self.project,
                application_id=preparation.application_id,
                generation=preparation.generation,
                state=ApplicationLifecycleState.READY,
                attempt=attempt,
            )
            if transitioned and self._on_ready is not None:
                try:
                    await self._on_ready(preparation)
                except Exception:
                    self._logger.warning(
                        "Application ready notification failed: application=%s generation=%s",
                        preparation.application_id,
                        preparation.generation,
                        exc_info=True,
                    )
            return

    def _task_done(self, application_id: str, completed: asyncio.Task[None]) -> None:
        owned = self._tasks.get(application_id)
        if owned is not None and owned.task is completed:
            self._tasks.pop(application_id, None)
        if completed.cancelled():
            return
        try:
            error = completed.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            self._logger.error(
                "Application preparation task escaped supervisor handling: application=%s",
                application_id,
                exc_info=(type(error), error, error.__traceback__),
            )

    def _retired_task_done(self, completed: asyncio.Task[None]) -> None:
        self._retired_tasks.discard(completed)
        if completed.cancelled():
            return
        try:
            error = completed.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            self._logger.error(
                "Superseded application preparation task failed during cleanup",
                exc_info=(type(error), error, error.__traceback__),
            )

    async def wait_for_current(self) -> None:
        while True:
            tasks = [owned.task for owned in self._tasks.values() if not owned.task.done()]
            tasks.extend(task for task in self._retired_tasks if not task.done())
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    async def shutdown(self) -> None:
        self._closed = True
        async with self._lock:
            tasks = [owned.task for owned in self._tasks.values() if not owned.task.done()]
            tasks.extend(task for task in self._retired_tasks if not task.done())
            self._tasks.clear()
            self._retired_tasks.clear()
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.registry.deactivate_scope(
            tenant=self.tenant,
            project=self.project,
            clear=True,
        )
