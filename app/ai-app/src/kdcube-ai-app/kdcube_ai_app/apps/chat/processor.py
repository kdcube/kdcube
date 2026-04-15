# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter


# chat/processor.py
from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Iterable

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.continuations import build_conversation_continuation_source
from kdcube_ai_app.apps.chat.external_events import build_conversation_external_event_source
from kdcube_ai_app.apps.chat.sdk.continuations import bind_current_conversation_continuation_source
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context
from kdcube_ai_app.infra.availability.health_and_heartbeat import MultiprocessDistributedMiddleware, logger
from kdcube_ai_app.infra.aws.ecs_container_instance_drain import (
    build_ecs_container_instance_drain_detector,
)
from kdcube_ai_app.infra.aws.task_protection import build_task_scale_in_protection
from kdcube_ai_app.infra.metrics.rolling_stats import record_metric
from kdcube_ai_app.infra.namespaces import REDIS
from kdcube_ai_app.infra.plugin.bundle_registry import get_all as _get_bundle_registry
from kdcube_ai_app.infra.plugin.git_bundle import (
    ensure_git_bundle_async,
    GitBundleCooldown,
    compute_git_bundle_paths,
    resolve_git_bundles_root,
)
from kdcube_ai_app.storage.storage import create_storage_backend
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload, ServiceCtx, ConversationCtx
from kdcube_ai_app.apps.chat.emitters import ChatRelayCommunicator, ChatCommunicator


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


QUEUE_BLOCK_TIMEOUT_SEC = 0.1
QUEUE_CALL_TIMEOUT_SEC = 2.0
CONFIG_GET_MESSAGE_TIMEOUT_SEC = 1.0
CONFIG_CALL_TIMEOUT_SEC = 5.0
INFLIGHT_REAPER_INTERVAL_SEC = 5.0
TASK_WATCHDOG_POLL_INTERVAL_SEC = 1.0


class _TaskExecutionWatchdogTimeout(asyncio.TimeoutError):
    def __init__(
        self,
        *,
        timeout_kind: str,
        limit_sec: float,
        wall_age_sec: float,
        idle_age_sec: float,
        last_activity_kind: str,
        last_activity_at: Optional[str],
    ):
        self.timeout_kind = str(timeout_kind or "timeout")
        self.limit_sec = float(limit_sec)
        self.wall_age_sec = float(wall_age_sec)
        self.idle_age_sec = float(idle_age_sec)
        self.last_activity_kind = str(last_activity_kind or "unknown")
        self.last_activity_at = last_activity_at
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        if self.timeout_kind == "idle":
            return (
                f"Task idle timeout exceeded after {self.idle_age_sec:.3f}s "
                f"(limit={self.limit_sec:.3f}s, last_activity={self.last_activity_kind}, "
                f"last_activity_at={self.last_activity_at or 'unknown'})"
            )
        return (
            f"Task max wall time exceeded after {self.wall_age_sec:.3f}s "
            f"(limit={self.limit_sec:.3f}s, last_activity={self.last_activity_kind}, "
            f"idle_age={self.idle_age_sec:.3f}s)"
        )


class _ActivityTrackingCommunicator:
    """
    Lightweight proxy that updates processor task activity whenever the workflow
    emits user-visible chat events.
    """

    def __init__(self, inner: ChatCommunicator, *, touch: callable):
        self._inner = inner
        self._touch = touch

    def __getattr__(self, item):
        return getattr(self._inner, item)

    async def emit(self, event: str, data: dict, broadcast: bool = False):
        result = await self._inner.emit(event, data, broadcast=broadcast)
        typ = ""
        try:
            typ = str((data or {}).get("type") or event or "").strip().lower()
        except Exception:
            typ = str(event or "emit")
        self._touch(f"emit:{typ or 'event'}")
        return result

    async def emit_enveloped(self, env: dict):
        result = await self._inner.emit_enveloped(env)
        typ = ""
        try:
            typ = str((env or {}).get("type") or "").strip().lower()
        except Exception:
            typ = ""
        self._touch(f"emit:{typ or 'enveloped'}")
        return result

    async def start(self, **kwargs):
        result = await self._inner.start(**kwargs)
        self._touch("chat.start")
        return result

    async def step(self, **kwargs):
        result = await self._inner.step(**kwargs)
        self._touch("chat.step")
        return result

    async def delta(self, **kwargs):
        result = await self._inner.delta(**kwargs)
        marker = str((kwargs or {}).get("marker") or "answer").strip().lower()
        self._touch(f"chat.delta:{marker}")
        return result

    async def complete(self, **kwargs):
        result = await self._inner.complete(**kwargs)
        self._touch("chat.complete")
        return result

    async def error(self, **kwargs):
        result = await self._inner.error(**kwargs)
        self._touch("chat.error")
        return result

    async def event(self, **kwargs):
        result = await self._inner.event(**kwargs)
        etype = str((kwargs or {}).get("type") or "event").strip().lower()
        self._touch(f"chat.event:{etype}")
        return result


async def prefetch_git_bundles() -> dict[str, str]:
    """
    Resolve configured git-backed bundles into the local bundle store once.

    Returns a mapping of bundle_id -> error string for bundles that could not be
    prepared. Existing bundle paths are skipped unless BUNDLE_GIT_ALWAYS_PULL is
    enabled.
    """
    errors: dict[str, str] = {}
    reg = _get_bundle_registry()
    force_pull = os.environ.get("BUNDLE_GIT_ALWAYS_PULL", "0").lower() in {"1", "true", "yes"}

    for bid, entry in reg.items():
        repo = entry.get("repo")
        if not repo:
            continue

        path_val = (entry.get("path") or "").strip()
        if not path_val:
            try:
                paths = compute_git_bundle_paths(
                bundle_id=bid,
                git_url=repo,
                git_ref=entry.get("ref"),
                git_subdir=entry.get("subdir"),
                bundles_root=resolve_git_bundles_root(),
            )
                path_val = str(paths.bundle_root)
            except Exception:
                path_val = ""

        if path_val and not force_pull:
            try:
                if Path(path_val).exists():
                    continue
            except Exception:
                pass

        try:
            await ensure_git_bundle_async(
                bundle_id=bid,
                git_url=repo,
                git_ref=entry.get("ref"),
                git_subdir=entry.get("subdir"),
                bundles_root=resolve_git_bundles_root(),
                atomic=os.environ.get("BUNDLE_GIT_ATOMIC", "1").lower() in {"1", "true", "yes"},
            )
        except GitBundleCooldown as e:
            errors[bid] = str(e)
        except Exception as e:
            errors[bid] = str(e)

    return errors


class EnhancedChatRequestProcessor:
    """
    Queue worker that:
      - Pops tasks fairly from multiple queues
      - Acquires + renews a per-task Redis lock
      - Emits chat_* events via ChatCommunicator (async)
      - Enforces per-task idle timeout plus hard wall-time cap
      - Handles graceful shutdown
    """

    QUEUE_ORDER: Iterable[str] = ("privileged", "registered", "anonymous", "paid")

    def __init__(
            self,
            middleware: MultiprocessDistributedMiddleware,
            chat_handler,
            *,
            conversation_ctx: ContextRAGClient,
            process_id: Optional[int] = None,
            relay: Optional[ChatRelayCommunicator] = None,   # unified relay (pub/sub)
            queue_analytics_updater=None,
            max_concurrent: Optional[int] = None,
            task_timeout_sec: Optional[int] = None,
            task_idle_timeout_sec: Optional[int] = None,
            task_max_wall_time_sec: Optional[int] = None,
            lock_ttl_sec: int = 300,
            lock_renew_sec: int = 60,
            started_marker_ttl_sec: Optional[int] = None,
            redis=None,
            host_drain_detector=None,
    ):
        self.middleware = middleware
        self.redis = redis or middleware.redis
        self.chat_handler = chat_handler
        self.process_id = process_id or os.getpid()
        self.max_concurrent = int(max_concurrent or 5)
        legacy_task_timeout_sec = max(
            1,
            int(os.getenv("CHAT_TASK_TIMEOUT_SEC", str(task_timeout_sec or 600))),
        )
        self.task_timeout_sec = legacy_task_timeout_sec
        self.task_idle_timeout_sec = max(
            1,
            int(os.getenv("CHAT_TASK_IDLE_TIMEOUT_SEC", str(task_idle_timeout_sec or legacy_task_timeout_sec))),
        )
        self.task_max_wall_time_sec = max(
            self.task_idle_timeout_sec,
            int(
                os.getenv(
                    "CHAT_TASK_MAX_WALL_TIME_SEC",
                    str(task_max_wall_time_sec or max(legacy_task_timeout_sec, legacy_task_timeout_sec * 4)),
                )
            ),
        )
        self.lock_ttl_sec = lock_ttl_sec
        self.lock_renew_sec = lock_renew_sec
        default_started_marker_ttl = max(
            self.task_max_wall_time_sec + self.lock_ttl_sec + 60,
            self.lock_ttl_sec * 2,
        )
        self.started_marker_ttl_sec = int(
            os.getenv(
                "CHAT_TASK_STARTED_MARKER_TTL_SEC",
                str(started_marker_ttl_sec or default_started_marker_ttl),
            )
        )
        self.conversation_ctx = conversation_ctx
        self.queue_analytics_updater = queue_analytics_updater

        self._relay = relay or ChatRelayCommunicator()  # transport
        self._processor_task: Optional[asyncio.Task] = None
        self._config_task: Optional[asyncio.Task] = None
        self._reaper_task: Optional[asyncio.Task] = None
        self._scheduler: Optional[Any] = None
        self._active_tasks: set[asyncio.Task] = set()
        self._active_task_details: dict[asyncio.Task, Dict[str, Any]] = {}
        self._current_load = 0
        self._stop_event = asyncio.Event()
        self._queue_idx = 0
        ns_fn = getattr(self.middleware, "ns", None)
        self._inflight_queue_prefix = (
            ns_fn(REDIS.CHAT.PROMPT_QUEUE_INFLIGHT_PREFIX)
            if callable(ns_fn)
            else f"{self.middleware.QUEUE_PREFIX}:inflight"
        )
        self.queue_block_timeout_sec = QUEUE_BLOCK_TIMEOUT_SEC
        self.queue_call_timeout_sec = QUEUE_CALL_TIMEOUT_SEC
        self.config_get_message_timeout_sec = CONFIG_GET_MESSAGE_TIMEOUT_SEC
        self.config_call_timeout_sec = CONFIG_CALL_TIMEOUT_SEC
        self.inflight_reaper_interval_sec = INFLIGHT_REAPER_INTERVAL_SEC
        self._last_queue_poll_completed_at = time.monotonic()
        self._last_config_poll_completed_at = time.monotonic()
        self._last_reaper_poll_completed_at = time.monotonic()
        self._last_queue_error: Optional[str] = None
        self._last_config_error: Optional[str] = None
        self._last_reaper_error: Optional[str] = None
        self._stale_requeue_count = 0
        self._stale_interrupted_count = 0
        self._task_scale_in_protection = build_task_scale_in_protection(logger_=logger)
        self._task_protection_reconcile_task: Optional[asyncio.Task] = None
        self._task_protection_reconcile_interval_sec = max(
            5.0,
            float(os.getenv("ECS_TASK_PROTECTION_RECONCILE_INTERVAL_SEC", "30") or "30"),
        )
        self._host_drain_detector = host_drain_detector or build_ecs_container_instance_drain_detector(
            logger_=logger,
        )
        self._host_drain_watch_task: Optional[asyncio.Task] = None
        self._host_drain_poll_interval_sec = max(
            5.0,
            float(os.getenv("ECS_CONTAINER_INSTANCE_DRAIN_POLL_INTERVAL_SEC", "15") or "15"),
        )
        self._task_watchdog_poll_interval_sec = max(
            0.1,
            float(os.getenv("CHAT_TASK_WATCHDOG_POLL_INTERVAL_SEC", str(TASK_WATCHDOG_POLL_INTERVAL_SEC)) or str(TASK_WATCHDOG_POLL_INTERVAL_SEC)),
        )
        self._host_draining = False
        self._host_draining_since: Optional[str] = None

    # ---------------- Public API ----------------

    async def start_processing(self):
        self._stop_event.clear()
        if self._processor_task and not self._processor_task.done():
            return
        self._processor_task = asyncio.create_task(self._processing_loop(), name="chat-processing-loop")
        if not self._config_task or self._config_task.done():
            self._config_task = asyncio.create_task(self._config_listener_loop(), name="config-bundles-listener")
        if not self._reaper_task or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(
                self._inflight_recovery_loop(),
                name="chat-inflight-recovery-loop",
            )
        if (
            self._task_scale_in_protection.enabled
            and (not self._task_protection_reconcile_task or self._task_protection_reconcile_task.done())
        ):
            self._task_protection_reconcile_task = asyncio.create_task(
                self._task_protection_reconcile_loop(),
                name="chat-task-protection-reconcile-loop",
            )
        if (
            getattr(self._host_drain_detector, "enabled", False)
            and (not self._host_drain_watch_task or self._host_drain_watch_task.done())
        ):
            self._host_drain_watch_task = asyncio.create_task(
                self._host_drain_watch_loop(),
                name="chat-host-drain-watch-loop",
            )
        if self._scheduler is None:
            from kdcube_ai_app.apps.chat.sdk.config import get_settings
            from kdcube_ai_app.apps.chat.sdk.runtime.bundle_scheduler import BundleSchedulerManager
            from kdcube_ai_app.infra.plugin.bundle_store import load_registry
            _settings = get_settings()
            self._scheduler = BundleSchedulerManager(
                redis=self.redis,
                redis_url=getattr(_settings, "REDIS_URL", None),
                tenant=_settings.TENANT,
                project=_settings.PROJECT,
                instance_id=_settings.INSTANCE_ID,
            )
            try:
                _reg = await load_registry(self.redis, _settings.TENANT, _settings.PROJECT)
                await self._scheduler.reconcile(_reg)
            except Exception:
                logger.warning("Initial bundle scheduler reconcile failed; will retry on next registry update", exc_info=True)

    async def _await_background_task_exit(
            self,
            task: Optional[asyncio.Task],
            *,
            name: str,
            timeout: float = 10.0,
    ) -> None:
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Background processor task did not stop within %.1fs: %s; cancelling it",
                timeout,
                name,
            )
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def get_active_task_details(self) -> list[Dict[str, Any]]:
        details: list[Dict[str, Any]] = []
        for task in list(self._active_tasks):
            info = dict(self._active_task_details.get(task) or {})
            info.setdefault("task_name", task.get_name())
            info["done"] = task.done()
            info.update(self._task_age_snapshot(info))
            info.pop("claimed_monotonic", None)
            info.pop("started_monotonic", None)
            info.pop("last_activity_monotonic", None)
            details.append(info)
        details.sort(key=lambda item: str(item.get("task_id") or item.get("task_name") or ""))
        return details

    async def wait_for_active_tasks(self) -> None:
        pending = {task for task in list(self._active_tasks) if not task.done()}
        if not pending:
            return
        logger.info(
            "Waiting for %s in-flight processor tasks to finish: %s",
            len(pending),
            self.get_active_task_details(),
        )
        try:
            await asyncio.wait(pending)
        except asyncio.CancelledError:
            logger.warning(
                "Processor drain was cancelled with %s in-flight tasks still running: %s",
                len(self._active_tasks),
                self.get_active_task_details(),
            )
            raise

    async def stop_processing(self):
        self._stop_event.set()
        await self._await_background_task_exit(self._processor_task, name="chat-processing-loop")
        self._processor_task = None
        await self._await_background_task_exit(self._config_task, name="config-bundles-listener")
        self._config_task = None
        await self._await_background_task_exit(self._reaper_task, name="chat-inflight-recovery-loop")
        self._reaper_task = None
        await self._await_background_task_exit(
            self._task_protection_reconcile_task,
            name="chat-task-protection-reconcile-loop",
        )
        self._task_protection_reconcile_task = None
        await self._await_background_task_exit(
            self._host_drain_watch_task,
            name="chat-host-drain-watch-loop",
        )
        self._host_drain_watch_task = None
        if self._scheduler is not None:
            await self._scheduler.shutdown()
            self._scheduler = None
        await self.wait_for_active_tasks()

    def get_current_load(self) -> int:
        return self._current_load

    def get_runtime_metadata(self) -> Dict[str, Any]:
        now = time.monotonic()
        oldest_active_task_wall_age_sec = 0.0
        max_active_task_idle_age_sec = 0.0
        for task in list(self._active_tasks):
            info = self._active_task_details.get(task) or {}
            ages = self._task_age_snapshot(info, now=now)
            wall_age = ages.get("wall_age_sec")
            idle_age = ages.get("idle_age_sec")
            if wall_age is not None:
                oldest_active_task_wall_age_sec = max(oldest_active_task_wall_age_sec, float(wall_age))
            if idle_age is not None:
                max_active_task_idle_age_sec = max(max_active_task_idle_age_sec, float(idle_age))
        metadata = {
            "current_load": self._current_load,
            "active_tasks": len(self._active_tasks),
            "draining": self._stop_event.is_set(),
            "host_draining": self._host_draining,
            "host_draining_since": self._host_draining_since,
            "accepting_new_tasks": not self._stop_event.is_set() and not self._host_draining,
            "task_timeout_sec": self.task_timeout_sec,
            "task_idle_timeout_sec": self.task_idle_timeout_sec,
            "task_max_wall_time_sec": self.task_max_wall_time_sec,
            "task_watchdog_poll_interval_sec": self._task_watchdog_poll_interval_sec,
            "oldest_active_task_wall_age_sec": round(oldest_active_task_wall_age_sec, 3),
            "max_active_task_idle_age_sec": round(max_active_task_idle_age_sec, 3),
            "queue_loop_lag_sec": round(max(0.0, now - self._last_queue_poll_completed_at), 3),
            "config_loop_lag_sec": round(max(0.0, now - self._last_config_poll_completed_at), 3),
            "reaper_loop_lag_sec": round(max(0.0, now - self._last_reaper_poll_completed_at), 3),
            "last_queue_error": self._last_queue_error,
            "last_config_error": self._last_config_error,
            "last_reaper_error": self._last_reaper_error,
            "stale_requeue_count": self._stale_requeue_count,
            "stale_interrupted_count": self._stale_interrupted_count,
        }
        try:
            metadata["task_protection"] = self._task_scale_in_protection.snapshot()
        except Exception:
            metadata["task_protection"] = {"enabled": bool(getattr(self._task_scale_in_protection, "enabled", False))}
            logger.debug("Failed to snapshot ECS task protection state", exc_info=True)
        try:
            metadata["ecs_host_drain"] = self._host_drain_detector.snapshot()
        except Exception:
            metadata["ecs_host_drain"] = {"enabled": bool(getattr(self._host_drain_detector, "enabled", False))}
            logger.debug("Failed to snapshot ECS host-drain state", exc_info=True)
        return metadata

    def is_host_draining(self) -> bool:
        return self._host_draining

    def _task_info(self, task: Optional[asyncio.Task] = None) -> Optional[Dict[str, Any]]:
        resolved_task = task or asyncio.current_task()
        if resolved_task is None:
            return None
        return self._active_task_details.get(resolved_task)

    def _touch_task_activity(self, kind: str, *, task: Optional[asyncio.Task] = None) -> None:
        info = self._task_info(task)
        if info is None:
            return
        now = time.monotonic()
        info["last_activity_at"] = _utc_now_iso()
        info["last_activity_monotonic"] = now
        info["last_activity_kind"] = str(kind or "processor.activity")
        info["activity_count"] = int(info.get("activity_count") or 0) + 1

    def _mark_task_execution_started(self, *, task: Optional[asyncio.Task] = None) -> None:
        info = self._task_info(task)
        if info is None:
            return
        now_iso = _utc_now_iso()
        now_mono = time.monotonic()
        info["started_execution"] = True
        info["started_at"] = now_iso
        info["started_monotonic"] = now_mono
        if not info.get("last_activity_at"):
            info["last_activity_at"] = now_iso
        if info.get("last_activity_monotonic") is None:
            info["last_activity_monotonic"] = now_mono
        if not info.get("last_activity_kind"):
            info["last_activity_kind"] = "processor.execution_started"

    def _task_age_snapshot(
            self,
            info: Dict[str, Any],
            *,
            now: Optional[float] = None,
    ) -> Dict[str, Optional[float]]:
        now_mono = float(now if now is not None else time.monotonic())
        start_mono = info.get("started_monotonic")
        if start_mono is None:
            start_mono = info.get("claimed_monotonic")
        last_mono = info.get("last_activity_monotonic")
        if last_mono is None:
            last_mono = start_mono

        wall_age_sec = None
        idle_age_sec = None
        try:
            if start_mono is not None:
                wall_age_sec = max(0.0, now_mono - float(start_mono))
        except Exception:
            wall_age_sec = None
        try:
            if last_mono is not None:
                idle_age_sec = max(0.0, now_mono - float(last_mono))
        except Exception:
            idle_age_sec = None

        return {
            "wall_age_sec": round(wall_age_sec, 3) if wall_age_sec is not None else None,
            "idle_age_sec": round(idle_age_sec, 3) if idle_age_sec is not None else None,
        }

    async def _cancel_handler_for_watchdog(
            self,
            handler_task: asyncio.Task,
            *,
            timeout_kind: str,
            limit_sec: float,
            wall_age_sec: float,
            idle_age_sec: float,
            last_activity_kind: str,
            last_activity_at: Optional[str],
    ) -> None:
        if not handler_task.done():
            handler_task.cancel()
            try:
                await handler_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("Handler raised while cancelling after watchdog timeout", exc_info=True)
        raise _TaskExecutionWatchdogTimeout(
            timeout_kind=timeout_kind,
            limit_sec=limit_sec,
            wall_age_sec=wall_age_sec,
            idle_age_sec=idle_age_sec,
            last_activity_kind=last_activity_kind,
            last_activity_at=last_activity_at,
        )

    async def _run_handler_with_watchdog(self, payload: ChatTaskPayload):
        handler_task = asyncio.create_task(
            self.chat_handler(payload),
            name=f"chat-handler:{payload.meta.task_id}",
        )
        try:
            while True:
                done, _pending = await asyncio.wait(
                    {handler_task},
                    timeout=self._task_watchdog_poll_interval_sec,
                )
                if handler_task in done:
                    return await handler_task

                info = self._task_info() or {}
                ages = self._task_age_snapshot(info)
                wall_age_sec = float(ages.get("wall_age_sec") or 0.0)
                idle_age_sec = float(ages.get("idle_age_sec") or 0.0)
                last_activity_kind = str(info.get("last_activity_kind") or "unknown")
                last_activity_at = info.get("last_activity_at")

                if wall_age_sec >= float(self.task_max_wall_time_sec):
                    await self._cancel_handler_for_watchdog(
                        handler_task,
                        timeout_kind="wall",
                        limit_sec=float(self.task_max_wall_time_sec),
                        wall_age_sec=wall_age_sec,
                        idle_age_sec=idle_age_sec,
                        last_activity_kind=last_activity_kind,
                        last_activity_at=last_activity_at,
                    )

                if idle_age_sec >= float(self.task_idle_timeout_sec):
                    await self._cancel_handler_for_watchdog(
                        handler_task,
                        timeout_kind="idle",
                        limit_sec=float(self.task_idle_timeout_sec),
                        wall_age_sec=wall_age_sec,
                        idle_age_sec=idle_age_sec,
                        last_activity_kind=last_activity_kind,
                        last_activity_at=last_activity_at,
                    )
        except asyncio.CancelledError:
            handler_task.cancel()
            try:
                await handler_task
            except asyncio.CancelledError:
                pass
            raise

    async def _task_protection_reconcile_loop(self) -> None:
        try:
            await self._task_scale_in_protection.reconcile(label="processor-startup", force=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Initial ECS task protection reconcile failed", exc_info=True)

        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(self._task_protection_reconcile_interval_sec)
                if self._stop_event.is_set():
                    break
                await self._task_scale_in_protection.reconcile(
                    label=f"processor-periodic:load={self._current_load}",
                    force=False,
                )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.warning("Periodic ECS task protection reconcile failed", exc_info=True)

    async def _host_drain_watch_loop(self) -> None:
        logger.info(
            "ECS host-drain watcher enabled: poll_interval_sec=%s",
            self._host_drain_poll_interval_sec,
        )
        while not self._stop_event.is_set():
            try:
                if self._host_draining:
                    await asyncio.sleep(self._host_drain_poll_interval_sec)
                    continue
                if await self._host_drain_detector.is_host_draining():
                    self._latch_host_draining()
                await asyncio.sleep(self._host_drain_poll_interval_sec)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.warning("ECS host-drain watcher failed", exc_info=True)
                await asyncio.sleep(self._host_drain_poll_interval_sec)

    def _latch_host_draining(self) -> None:
        if self._host_draining:
            return
        self._host_draining = True
        self._host_draining_since = _utc_now_iso()
        logger.warning(
            "Processor host entered ECS DRAINING; stopping new task intake: instance_id=%s process_id=%s current_load=%s active_tasks=%s",
            self.middleware.instance_id,
            self.process_id,
            self._current_load,
            len(self._active_tasks),
        )

    async def _reset_shared_async_pool(self, reason: str) -> None:
        logger.warning("Resetting shared async Redis pool for processor: %s", reason)
        try:
            pool = getattr(self.redis, "connection_pool", None)
            if pool is not None:
                await pool.disconnect(inuse_connections=True)
        except Exception:
            logger.warning("Failed to disconnect shared async Redis pool", exc_info=True)

    def _ready_queue_key(self, user_type: str) -> str:
        return f"{self.middleware.QUEUE_PREFIX}:{user_type}"

    def _inflight_queue_key(self, user_type: str) -> str:
        return f"{self._inflight_queue_prefix}:{user_type}"

    @staticmethod
    def _task_logical_id(task_dict: Dict[str, Any]) -> Optional[str]:
        return task_dict.get("meta", {}).get("task_id") or task_dict.get("task_id")

    def _task_lock_key(self, logical_id: str) -> str:
        return f"{self.middleware.LOCK_PREFIX}:{logical_id}"

    def _task_started_key(self, logical_id: str) -> str:
        return f"{self.middleware.LOCK_PREFIX}:started:{logical_id}"

    async def _started_marker_exists(self, logical_id: str) -> bool:
        ttl = await self.redis.ttl(self._task_started_key(logical_id))
        return ttl is not None and ttl >= -1

    async def _mark_task_started(self, task_data: Dict[str, Any], payload: ChatTaskPayload, request_id: str) -> Optional[str]:
        logical_id = self._task_logical_id(task_data)
        if not logical_id:
            return None
        started_key = self._task_started_key(logical_id)
        marker = {
            "task_id": logical_id,
            "request_id": request_id,
            "started_at": _utc_now_iso(),
            "tenant": payload.actor.tenant_id,
            "project": payload.actor.project_id,
            "conversation_id": payload.routing.conversation_id,
            "turn_id": payload.routing.turn_id,
            "session_id": payload.routing.session_id,
            "by_instance": f"{self.middleware.instance_id}:{self.process_id}",
        }
        await self.redis.set(
            started_key,
            json.dumps(marker, ensure_ascii=False),
            ex=self.started_marker_ttl_sec,
        )
        task_data["_started_key"] = started_key
        return started_key

    def _build_runtime_context(self, payload: ChatTaskPayload):
        session_id = payload.routing.session_id
        socket_id = payload.routing.socket_id
        task_id = payload.meta.task_id
        request_id = (payload.accounting.envelope or {}).get("request_id", task_id)
        svc = ServiceCtx(
            request_id=request_id,
            tenant=payload.actor.tenant_id,
            project=payload.actor.project_id,
            user=payload.user.user_id or payload.user.fingerprint,
            user_obj=payload.user,
        )
        conv = ConversationCtx(
            session_id=session_id,
            conversation_id=(payload.routing.conversation_id or session_id),
            turn_id=payload.routing.turn_id,
        )
        comm = ChatCommunicator(
            emitter=self._relay,
            service=svc.model_dump(),
            conversation=conv.model_dump(),
            room=session_id,
            target_sid=socket_id,
            tenant=payload.actor.tenant_id,
            project=payload.actor.project_id,
            user_id=payload.user.user_id,
            user_type=payload.user.user_type,
        )
        return request_id, svc, conv, comm

    def _continuation_source_for(self, payload: ChatTaskPayload):
        return build_conversation_continuation_source(redis=self.redis, payload=payload)

    def _external_event_source_for(self, payload: ChatTaskPayload):
        return build_conversation_external_event_source(
            redis=self.redis,
            tenant=payload.actor.tenant_id,
            project=payload.actor.project_id,
            conversation_id=payload.routing.conversation_id or payload.routing.session_id,
        )

    async def _mark_task_interrupted(self, task_dict: Dict[str, Any], *, reason: str) -> None:
        try:
            payload = ChatTaskPayload.model_validate(task_dict)
        except Exception:
            logger.warning("Could not materialize interrupted task payload for reason=%s", reason, exc_info=True)
            return

        request_id, svc, conv, comm = self._build_runtime_context(payload)
        try:
            res = await self.conversation_ctx.set_conversation_state(
                tenant=payload.actor.tenant_id,
                project=payload.actor.project_id,
                user_id=payload.user.user_id,
                conversation_id=payload.routing.conversation_id,
                new_state="error",
                by_instance=f"{self.middleware.instance_id}:{self.process_id}",
                request_id=request_id,
                last_turn_id=payload.routing.turn_id,
                require_not_in_progress=False,
                user_type=payload.user.user_type,
                bundle_id=payload.routing.bundle_id,
            )
            await self._relay.emit_conv_status(
                svc,
                conv,
                routing=payload.routing,
                state="error",
                updated_at=res["updated_at"],
                current_turn_id=res.get("current_turn_id"),
                completion="interrupted",
                target_sid=None,
            )
        except Exception:
            logger.warning("Failed to mark interrupted conversation state for task %s", payload.meta.task_id, exc_info=True)

        try:
            await comm.error(
                message=f"Turn interrupted before completion ({reason}).",
                data={"task_id": payload.meta.task_id, "error_type": "turn_interrupted", "reason": reason},
            )
        except Exception:
            logger.debug("Failed to emit interrupted error for task %s", payload.meta.task_id, exc_info=True)

    async def _promote_next_continuation(self, payload: ChatTaskPayload) -> Optional[Dict[str, Any]]:
        external_promoted = await self._promote_next_external_event(payload)
        if external_promoted is not None:
            return external_promoted

        source = self._continuation_source_for(payload)
        envelope = await source.take_next()
        if envelope is None:
            return None

        try:
            next_payload = envelope.task_payload()
        except Exception:
            logger.exception(
                "Dropping malformed continuation envelope for conversation=%s message_id=%s",
                payload.routing.conversation_id,
                envelope.message_id,
            )
            return None

        user_type = next_payload.user.user_type
        if hasattr(user_type, "value"):
            user_type = user_type.value
        ready_queue_key = self._ready_queue_key(str(user_type).lower())
        raw_payload = json.dumps(next_payload.model_dump(), ensure_ascii=False)

        try:
            await self.redis.lpush(ready_queue_key, raw_payload)
        except Exception:
            await source.restore_taken(envelope)
            raise

        logger.info(
            "Promoted continuation message_id=%s kind=%s conversation=%s turn_id=%s to %s",
            envelope.message_id,
            envelope.kind,
            next_payload.routing.conversation_id,
            next_payload.routing.turn_id,
            ready_queue_key,
        )
        return {
            "envelope": envelope,
            "payload": next_payload,
            "ready_queue_key": ready_queue_key,
        }

    async def _promote_next_external_event(self, payload: ChatTaskPayload) -> Optional[Dict[str, Any]]:
        source = self._external_event_source_for(payload)
        claimant_id = f"{self.middleware.instance_id}:{self.process_id}:{time.time_ns()}"
        while True:
            event = await source.claim_next_promotable(claimant_id=claimant_id)
            if event is None:
                return None
            logger.warning(
                "Claimed promotable external event conversation=%s current_turn=%s event_id=%s kind=%s seq=%s target_turn=%s active_turn=%s owner_turn=%s text=%r",
                payload.routing.conversation_id,
                payload.routing.turn_id,
                event.message_id,
                event.kind,
                event.sequence,
                event.target_turn_id,
                event.active_turn_id_at_ingress,
                event.owner_turn_id,
                (event.text or "")[:160],
            )

            event_kind = str(getattr(event, "kind", "") or "").strip().lower()
            if event_kind == "steer":
                logger.info(
                    "Discarding stale steer external event conversation=%s current_turn=%s event_id=%s seq=%s target_turn=%s active_turn=%s owner_turn=%s",
                    payload.routing.conversation_id,
                    payload.routing.turn_id,
                    event.message_id,
                    event.sequence,
                    event.target_turn_id,
                    event.active_turn_id_at_ingress,
                    event.owner_turn_id,
                )
                await source.mark_failed(
                    message_id=event.message_id,
                    claimant_id=claimant_id,
                    reason="steer_expired_not_promoted",
                )
                continue

            try:
                next_payload = event.task_payload_model()
            except Exception:
                logger.exception(
                    "Dropping malformed external event payload for conversation=%s event_id=%s",
                    payload.routing.conversation_id,
                    event.message_id,
                )
                await source.mark_failed(
                    message_id=event.message_id,
                    claimant_id=claimant_id,
                    reason="malformed_task_payload",
                )
                continue

            user_type = next_payload.user.user_type
            if hasattr(user_type, "value"):
                user_type = user_type.value
            ready_queue_key = self._ready_queue_key(str(user_type).lower())
            raw_payload = json.dumps(next_payload.model_dump(), ensure_ascii=False)

            try:
                await self.redis.lpush(ready_queue_key, raw_payload)
            except Exception:
                await source.release_claim(message_id=event.message_id, claimant_id=claimant_id)
                raise

            await source.mark_promoted(
                message_id=event.message_id,
                claimant_id=claimant_id,
                task_id=str(getattr(next_payload.meta, "task_id", "") or ""),
            )
            logger.info(
                "Promoted external event message_id=%s kind=%s conversation=%s turn_id=%s to %s",
                event.message_id,
                event.kind,
                next_payload.routing.conversation_id,
                next_payload.routing.turn_id,
                ready_queue_key,
            )
            return {
                "envelope": event,
                "payload": next_payload,
                "ready_queue_key": ready_queue_key,
            }

    async def _queue_claim(self, ready_queue_key: str, inflight_queue_key: str):
        try:
            result = await asyncio.wait_for(
                self.redis.brpoplpush(
                    ready_queue_key,
                    inflight_queue_key,
                    timeout=self.queue_block_timeout_sec,
                ),
                timeout=self.queue_call_timeout_sec,
            )
            self._last_queue_poll_completed_at = time.monotonic()
            self._last_queue_error = None
            return result
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self._last_queue_poll_completed_at = time.monotonic()
            self._last_queue_error = (
                "Queue claim exceeded "
                f"{self.queue_call_timeout_sec:.2f}s on {ready_queue_key}->{inflight_queue_key}"
            )
            logger.error(
                "Queue claim timed out after %.2fs on %s->%s; disconnecting shared pool",
                self.queue_call_timeout_sec,
                ready_queue_key,
                inflight_queue_key,
            )
            await self._reset_shared_async_pool("queue claim timeout")
            return None
        except Exception as e:
            self._last_queue_poll_completed_at = time.monotonic()
            self._last_queue_error = str(e)
            logger.error(
                "Queue claim failed on %s->%s: %s",
                ready_queue_key,
                inflight_queue_key,
                e,
                exc_info=True,
            )
            await self._reset_shared_async_pool(f"queue claim error: {e}")
            raise

    async def _drop_claimed_payload(
            self,
            *,
            inflight_queue_key: Optional[str],
            raw_payload,
            lock_key: Optional[str] = None,
            started_key: Optional[str] = None,
            reason: str,
    ) -> bool:
        removed = 0
        try:
            if inflight_queue_key and raw_payload is not None:
                removed = await self.redis.lrem(inflight_queue_key, 1, raw_payload)
                if not removed:
                    logger.warning(
                        "Claimed payload missing from inflight queue during drop: %s reason=%s",
                        inflight_queue_key,
                        reason,
                    )
            if lock_key:
                await self.redis.delete(lock_key)
            if started_key:
                await self.redis.delete(started_key)
        except Exception:
            logger.exception("Failed to drop claimed payload: %s", reason)
            return False
        return bool(removed or lock_key or started_key)

    async def _requeue_claimed_payload(
            self,
            *,
            ready_queue_key: Optional[str],
            inflight_queue_key: Optional[str],
            raw_payload,
            lock_key: Optional[str] = None,
            started_key: Optional[str] = None,
            reason: str,
    ) -> bool:
        try:
            removed = 0
            if inflight_queue_key and raw_payload is not None:
                removed = await self.redis.lrem(inflight_queue_key, 1, raw_payload)
                if removed and ready_queue_key:
                    await self.redis.rpush(ready_queue_key, raw_payload)
                elif not removed:
                    logger.warning(
                        "Claimed payload missing from inflight queue during requeue: %s reason=%s",
                        inflight_queue_key,
                        reason,
                    )
            if lock_key:
                await self.redis.delete(lock_key)
            if started_key:
                await self.redis.delete(started_key)
            return bool(removed)
        except Exception:
            logger.exception("Failed to requeue claimed payload: %s", reason)
            return False

    async def _ack_claimed_task(self, task_data: Dict[str, Any]) -> None:
        await self._drop_claimed_payload(
            inflight_queue_key=task_data.get("_inflight_queue_key"),
            raw_payload=task_data.get("_raw_payload"),
            lock_key=task_data.get("_lock_key"),
            started_key=task_data.get("_started_key"),
            reason=f"task-finished:{self._task_logical_id(task_data) or 'unknown'}",
        )

    async def _inflight_recovery_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                reclaimed = await self._requeue_stale_inflight_tasks()
                self._last_reaper_poll_completed_at = time.monotonic()
                self._last_reaper_error = None
                if reclaimed:
                    self._stale_requeue_count += reclaimed
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._last_reaper_poll_completed_at = time.monotonic()
                self._last_reaper_error = str(e)
                logger.error("Inflight recovery loop error: %s", e, exc_info=True)
            await asyncio.sleep(self.inflight_reaper_interval_sec)

    async def _requeue_stale_inflight_tasks(self) -> int:
        reclaimed = 0
        for user_type in self.QUEUE_ORDER:
            ready_queue_key = self._ready_queue_key(user_type)
            inflight_queue_key = self._inflight_queue_key(user_type)
            try:
                raw_items = await self.redis.lrange(inflight_queue_key, 0, -1)
            except Exception:
                logger.exception("Failed to inspect inflight queue %s", inflight_queue_key)
                continue

            for raw_payload in raw_items or []:
                try:
                    task_dict = json.loads(raw_payload)
                except Exception:
                    if await self._drop_claimed_payload(
                        inflight_queue_key=inflight_queue_key,
                        raw_payload=raw_payload,
                        reason="invalid-json-inflight",
                    ):
                        logger.error("Dropped invalid JSON payload from inflight queue %s", inflight_queue_key)
                    continue

                logical_id = self._task_logical_id(task_dict)
                if not logical_id:
                    if await self._drop_claimed_payload(
                        inflight_queue_key=inflight_queue_key,
                        raw_payload=raw_payload,
                        reason="missing-task-id-inflight",
                    ):
                        logger.error("Dropped inflight payload without task_id from %s", inflight_queue_key)
                    continue

                ttl = await self.redis.ttl(self._task_lock_key(logical_id))
                if ttl is not None and ttl >= 0:
                    continue

                started_key = self._task_started_key(logical_id)
                if await self._started_marker_exists(logical_id):
                    if await self._drop_claimed_payload(
                        inflight_queue_key=inflight_queue_key,
                        raw_payload=raw_payload,
                        started_key=started_key,
                        reason=f"started-task-interrupted:{logical_id}",
                    ):
                        self._stale_interrupted_count += 1
                        await self._mark_task_interrupted(task_dict, reason="worker_lost_after_start")
                        logger.warning(
                            "Marked stale started task %s as interrupted and removed it from %s",
                            logical_id,
                            inflight_queue_key,
                        )
                    continue

                if await self._requeue_claimed_payload(
                    ready_queue_key=ready_queue_key,
                    inflight_queue_key=inflight_queue_key,
                    raw_payload=raw_payload,
                    started_key=started_key,
                    reason=f"stale-prestart-inflight:{logical_id}",
                ):
                    reclaimed += 1
                    logger.warning(
                        "Requeued stale pre-start inflight task %s from %s to %s",
                        logical_id,
                        inflight_queue_key,
                        ready_queue_key,
                    )
        return reclaimed

    # ---------------- Core loop ----------------

    async def _processing_loop(self):
        while not self._stop_event.is_set():
            try:
                if self._host_draining:
                    await asyncio.sleep(0.1)
                    continue

                if self._current_load >= self.max_concurrent:
                    await asyncio.sleep(0.05)
                    continue

                task_data = await self._pop_any_queue_fair()
                if not task_data:
                    await asyncio.sleep(0.05)
                    continue

                if self._stop_event.is_set():
                    await self._requeue_claimed_payload(
                        ready_queue_key=task_data.get("_ready_queue_key") or task_data.get("_queue_key"),
                        inflight_queue_key=task_data.get("_inflight_queue_key"),
                        raw_payload=task_data.get("_raw_payload"),
                        lock_key=task_data.get("_lock_key"),
                        reason="processor-drain-before-task-start",
                    )
                    continue

                task_id = self._task_logical_id(task_data)
                task = asyncio.create_task(
                    self._process_task(task_data),
                    name=f"chat-task:{task_id}",
                )
                self._active_tasks.add(task)
                claimed_at = _utc_now_iso()
                claimed_monotonic = time.monotonic()
                self._active_task_details[task] = {
                    "task_id": task_id,
                    "queue_key": task_data.get("_ready_queue_key") or task_data.get("_queue_key"),
                    "inflight_queue_key": task_data.get("_inflight_queue_key"),
                    "claimed_at": claimed_at,
                    "claimed_monotonic": claimed_monotonic,
                    "started_at": None,
                    "started_execution": False,
                    "last_activity_at": claimed_at,
                    "last_activity_monotonic": claimed_monotonic,
                    "last_activity_kind": "processor.claimed",
                    "activity_count": 1,
                }

                def _on_done(t: asyncio.Task) -> None:
                    self._active_tasks.discard(t)
                    self._active_task_details.pop(t, None)

                task.add_done_callback(_on_done)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Processing loop error: {e}")
                await asyncio.sleep(0.5)

    async def _pop_any_queue_fair(self) -> Optional[Dict[str, Any]]:
        for _ in range(len(self.QUEUE_ORDER)):
            if self._stop_event.is_set() or self._host_draining:
                return None
            user_type = self.QUEUE_ORDER[self._queue_idx]
            self._queue_idx = (self._queue_idx + 1) % len(self.QUEUE_ORDER)

            if self._current_load >= self.max_concurrent:
                return None

            queue_key = self._ready_queue_key(user_type)
            inflight_queue_key = self._inflight_queue_key(user_type)
            raw_payload = await self._queue_claim(queue_key, inflight_queue_key)
            if raw_payload is None:
                continue

            if self._stop_event.is_set() or self._host_draining:
                await self._requeue_claimed_payload(
                    ready_queue_key=queue_key,
                    inflight_queue_key=inflight_queue_key,
                    raw_payload=raw_payload,
                    reason=f"{'host-draining' if self._host_draining else 'processor-drain'}-before-lock:{user_type}",
                )
                logger.info(
                    "Processor %s; returned claimed queue item to %s before processing",
                    "host draining" if self._host_draining else "draining",
                    queue_key,
                )
                return None

            try:
                task_dict = json.loads(raw_payload)
            except Exception:
                logger.error("Invalid task payload (not JSON); dropping")
                await self._drop_claimed_payload(
                    inflight_queue_key=inflight_queue_key,
                    raw_payload=raw_payload,
                    reason=f"invalid-json:{user_type}",
                )
                continue

            logical_id = self._task_logical_id(task_dict)
            if not logical_id:
                logger.error("Task missing task_id; dropping")
                await self._drop_claimed_payload(
                    inflight_queue_key=inflight_queue_key,
                    raw_payload=raw_payload,
                    reason=f"missing-task-id:{user_type}",
                )
                continue

            lock_key = self._task_lock_key(logical_id)
            acquired = await self.redis.set(
                lock_key,
                f"{self.middleware.instance_id}:{self.process_id}",
                nx=True,
                ex=self.lock_ttl_sec,
            )
            if acquired:
                if self._stop_event.is_set() or self._host_draining:
                    await self._requeue_claimed_payload(
                        ready_queue_key=queue_key,
                        inflight_queue_key=inflight_queue_key,
                        raw_payload=raw_payload,
                        lock_key=lock_key,
                        reason=f"{'host-draining' if self._host_draining else 'processor-drain'}-after-lock:{logical_id}",
                    )
                    logger.info(
                        "Processor %s; returned locked task %s to %s",
                        "host draining" if self._host_draining else "draining",
                        logical_id,
                        queue_key,
                    )
                    return None
                self._current_load += 1
                created_at = (task_dict.get("meta") or {}).get("created_at")
                queue_wait_ms = None
                if created_at:
                    try:
                        queue_wait_ms = int((time.time() - float(created_at)) * 1000)
                    except Exception:
                        queue_wait_ms = None
                logger.info(
                    f"Process {self.process_id} acquired task {logical_id} ({user_type})"
                    + (f" queue_wait_ms={queue_wait_ms}" if queue_wait_ms is not None else "")
                )
                task_dict["_queue_wait_ms"] = queue_wait_ms
                task_dict["_lock_key"] = lock_key
                task_dict["_queue_key"] = queue_key
                task_dict["_ready_queue_key"] = queue_key
                task_dict["_inflight_queue_key"] = inflight_queue_key
                task_dict["_raw_payload"] = raw_payload
                return task_dict

            await self._requeue_claimed_payload(
                ready_queue_key=queue_key,
                inflight_queue_key=inflight_queue_key,
                raw_payload=raw_payload,
                reason=f"lock-not-acquired:{logical_id}",
            )
        return None

    # ---------------- Config loop ----------------
    async def _config_listener_loop(self):
        import kdcube_ai_app.infra.namespaces as namespaces
        from kdcube_ai_app.apps.chat.sdk.config import get_settings
        from kdcube_ai_app.infra.plugin.bundle_registry import (
            set_registry_async, serialize_to_env, get_all, get_default_id
        )
        from kdcube_ai_app.infra.plugin.agentic_loader import clear_agentic_caches
        from kdcube_ai_app.infra.plugin.bundle_store import (
            load_registry as store_load,
            save_registry as store_save,
            publish_update as store_publish,
            apply_update,
            BundlesRegistry
        )

        settings = get_settings()
        tenant = settings.TENANT
        project = settings.PROJECT
        update_channel = namespaces.CONFIG.BUNDLES.UPDATE_CHANNEL.format(tenant=tenant, project=project)
        cleanup_channel = namespaces.CONFIG.BUNDLES.CLEANUP_CHANNEL.format(tenant=tenant, project=project)
        props_update_channel = namespaces.CONFIG.BUNDLES.PROPS_UPDATE_CHANNEL.format(tenant=tenant, project=project)
        backoff = 0.5
        while not self._stop_event.is_set():
            pubsub = None
            try:
                pubsub = self.redis.pubsub()
                await pubsub.subscribe(
                    update_channel,
                    cleanup_channel,
                    props_update_channel,
                )
                logger.info(
                    "Subscribed to bundles channels: "
                    f"{update_channel}, {cleanup_channel}, {props_update_channel}"
                )
                backoff = 0.5
                self._last_config_error = None

                while not self._stop_event.is_set():
                    try:
                        message = await asyncio.wait_for(
                            pubsub.get_message(
                                ignore_subscribe_messages=True,
                                timeout=self.config_get_message_timeout_sec,
                            ),
                            timeout=self.config_call_timeout_sec,
                        )
                    except asyncio.TimeoutError as e:
                        raise RuntimeError(
                            f"Config listener get_message exceeded {self.config_call_timeout_sec:.2f}s"
                        ) from e
                    self._last_config_poll_completed_at = time.monotonic()
                    self._last_config_error = None
                    if self._stop_event.is_set():
                        break
                    if not message or message.get("type") != "message":
                        await asyncio.sleep(0.1)
                        continue

                    raw = message.get("data")
                    try:
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8")
                        evt = json.loads(raw)
                    except Exception:
                        logger.warning("Invalid bundles broadcast; ignoring")
                        continue

                    if "registry" in evt:
                        from kdcube_ai_app.apps.chat.sdk.runtime.local_sidecars import stop_inactive_local_sidecars
                        try:
                            reg = BundlesRegistry(**(evt.get("registry") or {}))
                        except Exception:
                            logger.warning("Invalid registry payload; ignoring")
                            continue
                        await set_registry_async(
                            {bid: be.model_dump() for bid, be in reg.bundles.items()},
                            reg.default_bundle_id
                        )
                        serialize_to_env(get_all(), get_default_id())
                        try:
                            stopped_sidecars = stop_inactive_local_sidecars(
                                active_bundle_ids={str(bid).strip() for bid in reg.bundles.keys() if str(bid).strip()},
                                tenant=tenant,
                                project=project,
                                terminate_timeout_sec=2.0,
                                kill_timeout_sec=1.0,
                            )
                            if stopped_sidecars:
                                logger.info(
                                    "Stopped inactive local sidecars after bundles SNAPSHOT: tenant=%s project=%s count=%s",
                                    tenant,
                                    project,
                                    stopped_sidecars,
                                )
                        except Exception:
                            logger.warning("Failed to stop inactive local sidecars after snapshot", exc_info=True)
                        try:
                            clear_agentic_caches()
                        except Exception:
                            pass

                        try:
                            await store_save(self.redis, reg)
                        except Exception:
                            logger.debug("Could not save snapshot to Redis; continuing")

                        logger.info(f"Applied bundles SNAPSHOT; now have {len(get_all())} bundles")
                        if self._scheduler is not None:
                            try:
                                await self._scheduler.reconcile(reg)
                            except Exception:
                                logger.warning("Bundle scheduler reconcile failed after snapshot", exc_info=True)
                        continue

                    if evt.get("type") == "bundles.update":
                        from kdcube_ai_app.apps.chat.sdk.runtime.local_sidecars import stop_local_sidecars_for_bundle_ids
                        op = evt.get("op", "merge")
                        bundles_patch = evt.get("bundles") or {}
                        default_id = evt.get("default_bundle_id")

                        try:
                            current = await store_load(self.redis)
                        except Exception as e:
                            logger.error(f"Failed to load registry from Redis: {e}")
                            current = BundlesRegistry()

                        try:
                            reg = apply_update(current, op, bundles_patch, default_id)
                        except Exception as e:
                            logger.error(f"Ignoring invalid bundles.update: {e}")
                            continue

                        try:
                            await store_save(self.redis, reg)
                            await store_publish(self.redis, reg, op=op, actor=evt.get("updated_by") or None)
                        except Exception as e:
                            logger.error(f"Failed to persist/broadcast bundles: {e}")

                        await set_registry_async(
                            {bid: be.model_dump() for bid, be in reg.bundles.items()},
                            reg.default_bundle_id
                        )
                        new_env = serialize_to_env(get_all(), get_default_id())
                        try:
                            stopped_sidecars = stop_local_sidecars_for_bundle_ids(
                                bundle_ids={str(bid).strip() for bid in (bundles_patch or {}).keys() if str(bid).strip()},
                                tenant=tenant,
                                project=project,
                                terminate_timeout_sec=2.0,
                                kill_timeout_sec=1.0,
                            )
                            if stopped_sidecars:
                                logger.info(
                                    "Stopped local sidecars after bundles.update: tenant=%s project=%s count=%s bundles=%s",
                                    tenant,
                                    project,
                                    stopped_sidecars,
                                    list((bundles_patch or {}).keys()),
                                )
                        except Exception:
                            logger.warning("Failed to stop local sidecars after bundles.update", exc_info=True)
                        try:
                            clear_agentic_caches()
                        except Exception:
                            pass

                        logger.info(f"Applied bundles COMMAND (op={op}); now have {len(get_all())} bundles. New env = {new_env}")
                        if self._scheduler is not None:
                            try:
                                await self._scheduler.reconcile(reg)
                            except Exception:
                                logger.warning("Bundle scheduler reconcile failed after bundles.update", exc_info=True)
                        continue

                    if evt.get("type") == "bundles.cleanup":
                        from kdcube_ai_app.infra.plugin.agentic_loader import evict_inactive_specs, AgenticBundleSpec
                        from kdcube_ai_app.apps.chat.sdk.runtime.local_sidecars import stop_inactive_local_sidecars
                        from kdcube_ai_app.infra.plugin.bundle_store import (
                            _discover_example_bundle_ids,
                            cleanup_old_shared_example_bundles,
                        )
                        from kdcube_ai_app.infra.plugin.bundle_storage import (
                            cleanup_old_bundle_storage_async,
                            resolve_bundle_storage_root,
                            storage_for_spec,
                        )
                        from kdcube_ai_app.infra.plugin.git_bundle import (
                            cleanup_old_git_bundles_async,
                            resolve_bundles_root,
                            bundle_dir_for_git,
                        )
                        from kdcube_ai_app.infra.plugin.bundle_refs import get_active_paths
                        from types import SimpleNamespace

                        active_specs = []
                        for _bid, entry in (get_all() or {}).items():
                            try:
                                active_specs.append(AgenticBundleSpec(
                                    path=entry.get("path"),
                                    module=entry.get("module"),
                                    singleton=bool(entry.get("singleton")),
                                ))
                            except Exception:
                                continue
                        drop_sys_modules = bool(evt.get("drop_sys_modules", True))
                        result = evict_inactive_specs(
                            active_specs=active_specs,
                            drop_sys_modules=drop_sys_modules,
                        )
                        try:
                            stopped_sidecars = stop_inactive_local_sidecars(
                                active_bundle_ids={str(_bid).strip() for _bid in (get_all() or {}).keys() if str(_bid).strip()},
                                tenant=tenant,
                                project=project,
                                terminate_timeout_sec=2.0,
                                kill_timeout_sec=1.0,
                            )
                            if stopped_sidecars:
                                logger.info(
                                    "Stopped inactive local sidecars during bundles cleanup: tenant=%s project=%s count=%s",
                                    tenant,
                                    project,
                                    stopped_sidecars,
                                )
                        except Exception:
                            logger.warning("Failed to stop inactive local sidecars during bundles cleanup", exc_info=True)
                        # Git bundle cleanup (skip active refs from Redis)
                        try:
                            active_paths = await get_active_paths(
                                self.redis,
                                tenant=tenant,
                                project=project,
                            )
                            bundles = get_all() or {}
                            active_storage_paths = []
                            for _bid, entry in bundles.items():
                                try:
                                    spec = SimpleNamespace(
                                        id=_bid,
                                        git_commit=entry.get("git_commit"),
                                        ref=entry.get("ref"),
                                        version=entry.get("version"),
                                    )
                                    storage_path = storage_for_spec(
                                        spec=spec,
                                        tenant=tenant,
                                        project=project,
                                        ensure=False,
                                    )
                                    if storage_path is not None:
                                        active_storage_paths.append(str(storage_path))
                                except Exception:
                                    continue
                            for _bid, entry in bundles.items():
                                repo = entry.get("repo")
                                if not repo:
                                    pass
                                else:
                                    base_dir = bundle_dir_for_git(_bid, entry.get("ref"))
                                    await cleanup_old_git_bundles_async(
                                        bundle_id=base_dir,
                                        bundles_root=resolve_bundles_root(),
                                        active_paths=active_paths,
                                    )
                                await cleanup_old_bundle_storage_async(
                                    bundle_id=_bid,
                                    tenant=tenant,
                                    project=project,
                                    storage_root=resolve_bundle_storage_root(),
                                    active_paths=active_storage_paths,
                                )
                            for _bid in _discover_example_bundle_ids():
                                cleanup_old_shared_example_bundles(
                                    bundle_id=_bid,
                                    bundles_root=resolve_bundles_root(),
                                    active_paths=active_paths,
                                )
                        except Exception as e:
                            logger.warning(f"Git bundle cleanup failed: {e}")
                        logger.info(
                            "Applied bundles cleanup. "
                            f"evicted_modules={result.get('evicted_modules')}; "
                            f"evicted_singletons={result.get('evicted_singletons')}; "
                            f"sys_modules_deleted={result.get('sys_modules_deleted')}"
                        )
                        continue

                    if message.get("channel") in (
                        props_update_channel,
                        props_update_channel.encode(),
                    ):
                        if self._scheduler is not None:
                            try:
                                current = await store_load(self.redis)
                                await self._scheduler.reconcile(current)
                            except Exception:
                                logger.warning("Bundle scheduler reconcile failed after props update", exc_info=True)
                        continue

                    logger.debug("Ignoring unrelated pub/sub message on bundles channel")

                if self._stop_event.is_set():
                    break
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._last_config_poll_completed_at = time.monotonic()
                self._last_config_error = str(e)
                logger.error(f"Config listener error: {e}")
                await self._reset_shared_async_pool(f"config listener error: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
            finally:
                if pubsub:
                    try:
                        await pubsub.unsubscribe(update_channel, cleanup_channel, props_update_channel)
                        await pubsub.close()
                    except Exception:
                        pass

    # ---------------- Per-task execution ----------------

    @asynccontextmanager
    async def _lock_renewer(
            self,
            lock_key: str,
            *,
            extra_keys: Optional[Iterable[str]] = None,
            extra_ttl_sec: Optional[int] = None,
    ):
        lease_keys = tuple(key for key in (extra_keys or ()) if key)
        extra_key_ttl = int(extra_ttl_sec or self.lock_ttl_sec)

        async def renewer():
            try:
                while True:
                    await asyncio.sleep(self.lock_renew_sec)
                    ttl = await self.redis.ttl(lock_key)
                    if ttl is None or ttl < 0:
                        break
                    await self.redis.expire(lock_key, self.lock_ttl_sec)
                    for extra_key in lease_keys:
                        try:
                            extra_ttl = await self.redis.ttl(extra_key)
                            if extra_ttl is None or extra_ttl < 0:
                                continue
                            await self.redis.expire(extra_key, extra_key_ttl)
                        except Exception:
                            logger.debug("Failed to renew extra lease key %s", extra_key, exc_info=True)
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(renewer(), name=f"lock-renewer:{lock_key}")
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _process_task(self, task_data: Dict[str, Any]):
        lock_key = task_data.get("_lock_key")
        current_processor_task = asyncio.current_task()
        ephemeral_active_task_registration = False
        ephemeral_task_details = False

        # 1) Normalize payload
        try:
            payload = ChatTaskPayload.model_validate(task_data)
        except Exception as e:
            logger.error(f"Cannot normalize legacy task: {e}")
            logger.error(traceback.format_exc())
            try:
                await self._ack_claimed_task(task_data)
            finally:
                # Ensure load is released even for invalid payloads
                self._current_load = max(0, self._current_load - 1)
                return

        assert payload is not None

        if current_processor_task is not None:
            if current_processor_task not in self._active_tasks:
                self._active_tasks.add(current_processor_task)
                ephemeral_active_task_registration = True
            if current_processor_task not in self._active_task_details:
                claimed_at = _utc_now_iso()
                claimed_monotonic = time.monotonic()
                self._active_task_details[current_processor_task] = {
                    "task_id": payload.meta.task_id,
                    "queue_key": task_data.get("_ready_queue_key") or task_data.get("_queue_key"),
                    "inflight_queue_key": task_data.get("_inflight_queue_key"),
                    "claimed_at": claimed_at,
                    "claimed_monotonic": claimed_monotonic,
                    "started_at": None,
                    "started_execution": False,
                    "last_activity_at": claimed_at,
                    "last_activity_monotonic": claimed_monotonic,
                    "last_activity_kind": "processor.claimed",
                    "activity_count": 1,
                }
                ephemeral_task_details = True

        # 2) Build contexts
        task_id = payload.meta.task_id
        request_id, svc, conv, comm = self._build_runtime_context(payload)
        processor_task = asyncio.current_task()
        tracked_comm = _ActivityTrackingCommunicator(
            comm,
            touch=lambda kind, _task=processor_task: self._touch_task_activity(kind, task=_task),
        )

        # 3) accounting + storage
        from kdcube_ai_app.infra.accounting.envelope import AccountingEnvelope, bind_accounting
        from kdcube_ai_app.infra.accounting import with_accounting

        envelope = AccountingEnvelope.from_dict(payload.accounting.envelope)
        _settings = get_settings()
        storage_backend = create_storage_backend(_settings.STORAGE_PATH, **{})

        queue_wait_ms = task_data.get("_queue_wait_ms")
        if queue_wait_ms is None:
            created_at = None
            try:
                created_at = float(getattr(payload.meta, "created_at", None))
            except Exception:
                created_at = None
            if created_at:
                queue_wait_ms = int((time.time() - created_at) * 1000)
        if queue_wait_ms is not None:
            logger.info(
                f"Starting task {task_id} queue_wait_ms={queue_wait_ms} current_load={self._current_load}"
            )
        msg = (
            (payload.request.message[:100] + "...")
            if payload.request.message and len(payload.request.message) > 100
            else (payload.request.message or f"operation={payload.request.operation}")
        )

        success = False
        task_cancelled = False
        exec_started_at = None
        continuation_source = self._continuation_source_for(payload)
        try:
            protection_label = f"task_id={task_id}"
            async with self._task_scale_in_protection.hold(label=protection_label):
                started_key = await self._mark_task_started(task_data, payload, request_id)
                self._mark_task_execution_started()
                if started_key:
                    self._touch_task_activity("processor.started_marker")
                async with self._lock_renewer(
                        lock_key=lock_key,
                        extra_keys=[started_key] if started_key else None,
                        extra_ttl_sec=self.started_marker_ttl_sec if started_key else None,
                ):
                    exec_started_at = time.monotonic()
                    await tracked_comm.start(message=msg, queue_stats={})
                    await tracked_comm.step(
                        step="workflow_start",
                        status="started",
                        title="Workflow Start",
                        data={"default_model": (payload.config.values or {}).get("selected_model"), "task_id": task_id},
                    )

                    async with bind_accounting(envelope, storage_backend, enabled=True):
                        async with with_accounting("chat.orchestrator",
                                                   app_bundle_id=payload.routing.bundle_id,
                                                   conversation_id=payload.routing.conversation_id,
                                                   turn_id=payload.routing.turn_id,
                                                   metadata={
                            "task_id": task_id,
                            "conversation_id": payload.routing.conversation_id,
                            "turn_id": payload.routing.turn_id,
                        }):
                            with bind_current_request_context(payload, comm=tracked_comm):
                                with bind_current_conversation_continuation_source(continuation_source):
                                    result = await self._run_handler_with_watchdog(payload)

                result = result or {}
                success = True
                await tracked_comm.complete(data=result)

        except asyncio.CancelledError:
            task_cancelled = True
            logger.warning(
                "Task %s was cancelled; keeping inflight claim for recovery",
                task_id,
            )
            raise
        except _TaskExecutionWatchdogTimeout as exc:
            tb = str(exc)
            await tracked_comm.error(
                message=tb,
                data={
                    "task_id": task_id,
                    "error_type": "task_watchdog_timeout",
                    "timeout_kind": exc.timeout_kind,
                    "limit_sec": exc.limit_sec,
                    "wall_age_sec": exc.wall_age_sec,
                    "idle_age_sec": exc.idle_age_sec,
                    "last_activity_kind": exc.last_activity_kind,
                    "last_activity_at": exc.last_activity_at,
                },
            )
            success = False
        except Exception:
            tb = traceback.format_exc()
            try:
                await tracked_comm.error(message=tb, data={"task_id": task_id})
            except Exception:
                logger.debug("Failed to emit processor error for %s", task_id, exc_info=True)
            success = False
        finally:
            exec_ms = None
            promoted_continuation = None
            if exec_started_at is not None:
                try:
                    exec_ms = int((time.monotonic() - exec_started_at) * 1000)
                except Exception:
                    exec_ms = None
            try:
                if not task_cancelled:
                    await self._ack_claimed_task(task_data)
            finally:
                self._current_load = max(0, self._current_load - 1)
            if not task_cancelled:
                try:
                    promoted_continuation = await self._promote_next_continuation(payload)
                except Exception:
                    logger.exception(
                        "Failed to promote next continuation for conversation=%s",
                        payload.routing.conversation_id,
                    )
            if not task_cancelled:
                if self.queue_analytics_updater:
                    try:
                        user_type = payload.user.user_type.value if hasattr(payload.user.user_type, "value") else str(payload.user.user_type)
                        wait_seconds = (float(queue_wait_ms) / 1000.0) if queue_wait_ms is not None else None
                        await self.queue_analytics_updater(user_type.lower(), wait_time=wait_seconds, processed=True)
                    except Exception:
                        logger.debug("Failed to update queue analytics", exc_info=True)
                try:
                    tenant_id = payload.actor.tenant_id
                    project_id = payload.actor.project_id
                    if queue_wait_ms is not None:
                        await record_metric(
                            self.redis,
                            base=REDIS.METRICS.TASK_QUEUE_WAIT_MS,
                            tenant=tenant_id,
                            project=project_id,
                            component="proc",
                            value=float(queue_wait_ms),
                        )
                    if exec_ms is not None:
                        await record_metric(
                            self.redis,
                            base=REDIS.METRICS.TASK_EXEC_MS,
                            tenant=tenant_id,
                            project=project_id,
                            component="proc",
                            value=float(exec_ms),
                        )
                except Exception:
                    logger.debug("Failed to record task latency metrics", exc_info=True)
                try:
                    if promoted_continuation is not None:
                        next_payload = promoted_continuation["payload"]
                        next_request_id, next_svc, next_conv, _ = self._build_runtime_context(next_payload)
                        res = await self.conversation_ctx.set_conversation_state(
                            tenant=next_payload.actor.tenant_id,
                            project=next_payload.actor.project_id,
                            user_id=next_payload.user.user_id,
                            conversation_id=next_payload.routing.conversation_id,
                            new_state="in_progress",
                            by_instance=f"{self.middleware.instance_id}:{self.process_id}",
                            request_id=next_request_id,
                            last_turn_id=next_payload.routing.turn_id,
                            require_not_in_progress=False,
                            user_type=next_payload.user.user_type,
                            bundle_id=next_payload.routing.bundle_id,
                        )
                        await self._relay.emit_conv_status(
                            next_svc,
                            next_conv,
                            routing=next_payload.routing,
                            state="in_progress",
                            updated_at=res["updated_at"],
                            current_turn_id=res.get("current_turn_id"),
                            completion="queued_next",
                            target_sid=None,
                        )
                    else:
                        res = await self.conversation_ctx.set_conversation_state(
                            tenant=payload.actor.tenant_id, project=payload.actor.project_id, user_id=payload.user.user_id, conversation_id=payload.routing.conversation_id,
                            new_state=("idle" if success else "error"),
                            by_instance=f"{self.middleware.instance_id}:{self.process_id}",
                            request_id=request_id,
                            last_turn_id=payload.routing.turn_id,
                            require_not_in_progress=False,
                            user_type=payload.user.user_type,
                            bundle_id=payload.routing.bundle_id,
                        )
                        # broadcast to session
                        await self._relay.emit_conv_status(svc, conv,
                                                         routing=payload.routing,
                                                         state=("idle" if success else "error"),
                                                         updated_at=res["updated_at"],
                                                         current_turn_id=res.get("current_turn_id"),
                                                         completion="success" if success else "error",
                                                         target_sid=None)
                except Exception as ex:
                    logger.error(traceback.format_exc())
            if ephemeral_task_details and current_processor_task is not None:
                self._active_task_details.pop(current_processor_task, None)
            if ephemeral_active_task_registration and current_processor_task is not None:
                self._active_tasks.discard(current_processor_task)
