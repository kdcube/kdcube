# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter


# chat/processor.py
from __future__ import annotations

import asyncio
import json
import os
import random
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Iterable, List

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.external_events import build_conversation_external_event_source
from kdcube_ai_app.apps.chat.processor_scheduler_backend import (
    build_processor_scheduler_backend,
    normalize_processor_scheduler_backend,
)
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    bind_current_request_context,
    bind_current_task_activity_touch,
)
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    bind_bundle_operation_caller,
    make_local_bundle_operation_caller,
)
from kdcube_ai_app.infra.availability.health_and_heartbeat import MultiprocessDistributedMiddleware, logger
from kdcube_ai_app.infra.aws.ecs_container_instance_drain import (
    build_ecs_container_instance_drain_detector,
)
from kdcube_ai_app.infra.aws.task_protection import build_task_scale_in_protection
from kdcube_ai_app.infra.metrics.rolling_stats import record_metric
from kdcube_ai_app.infra.namespaces import REDIS
from kdcube_ai_app.infra.plugin.git_bundle import (
    ensure_git_bundle,
    GitBundleCooldown,
    compute_git_bundle_paths,
    git_bundle_cache_status,
    resolve_managed_bundles_root,
)
from kdcube_ai_app.storage.storage import create_storage_backend
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ExternalEventAccounting,
    ExternalEventActor,
    ExternalEventConfig,
    ExternalEvent,
    ExternalEventLaneWakeup,
    ExternalEventMeta,
    ExternalEventPayload,
    ExternalEventRequest,
    ExternalEventRouting,
    ExternalEventUser,
    ConversationCtx,
    ServiceCtx,
    external_event_request_start_label,
)
from kdcube_ai_app.apps.chat.sdk.event_identity import DEFAULT_REACT_AGENT_ID, normalize_agent_id, safe_event_lane_part
from kdcube_ai_app.apps.chat.sdk.events.event_bus import ExternalEventLaneWakeIgnored
from kdcube_ai_app.apps.chat.sdk.events.event_bus.orchestrator import ConversationEventBusOrchestrator
from kdcube_ai_app.apps.chat.sdk.events.event_bus.state import event_timestamp, timestamp_lte
from kdcube_ai_app.infra.jobs.stream import (
    BACKGROUND_JOB_OPERATION,
    BACKGROUND_JOB_QUEUE_ORDER,
    RedisBackgroundJobStream,
)
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


async def _cleanup_turn_browser_sessions_for_payload(payload: ExternalEventPayload, *, reason: str) -> None:
    try:
        from types import SimpleNamespace

        from kdcube_ai_app.apps.chat.sdk.tools.backends.browser_backend import (
            close_browser_sessions_for_current_context,
        )

        actor = getattr(payload, "actor", None)
        user = getattr(payload, "user", None)
        routing = getattr(payload, "routing", None)
        request = getattr(payload, "request", None)
        bound_context = SimpleNamespace(
            tenant=getattr(actor, "tenant_id", None),
            project=getattr(actor, "project_id", None),
            user_id=getattr(user, "user_id", None) or getattr(user, "fingerprint", None),
            conversation_id=getattr(routing, "conversation_id", None) or getattr(routing, "session_id", None),
            turn_id=getattr(routing, "turn_id", None),
            request_id=getattr(request, "request_id", None),
            bundle_id=getattr(routing, "bundle_id", None),
        )
        result = await close_browser_sessions_for_current_context(
            bound_context=bound_context,
            reason=reason,
        )
        closed_count = int(result.get("closed_count") or 0) if isinstance(result, dict) else 0
        matched_count = int(result.get("matched_count") or 0) if isinstance(result, dict) else 0
        total_before = int(result.get("total_sessions_before") or 0) if isinstance(result, dict) else 0
        total_after = int(result.get("total_sessions_after") or 0) if isinstance(result, dict) else 0
        task_id = getattr(getattr(payload, "meta", None), "task_id", None)
        turn_id = getattr(routing, "turn_id", None)
        session_labels = [
            str(item.get("session_label") or "")
            for item in (result.get("matched_sessions") or [])
            if isinstance(item, dict)
        ][:5] if isinstance(result, dict) else []
        log_message = (
            "Browser session cleanup after processor task finalization: "
            "task_id=%s turn_id=%s reason=%s total_before=%s matched=%s closed=%s total_after=%s labels=%s"
        )
        if matched_count or closed_count:
            logger.info(
                log_message,
                task_id,
                turn_id,
                reason,
                total_before,
                matched_count,
                closed_count,
                total_after,
                session_labels,
            )
        else:
            logger.debug(
                log_message,
                task_id,
                turn_id,
                reason,
                total_before,
                matched_count,
                closed_count,
                total_after,
                session_labels,
            )
    except Exception:
        logger.debug("Failed to cleanup browser sessions after processor task finalization", exc_info=True)


async def prefetch_git_bundles(registry: Optional[Any] = None) -> dict[str, str]:
    """
    Resolve configured git-backed bundles into the local bundle store once.

    Returns a mapping of bundle_id -> error string for bundles that could not be
    prepared. Existing bundle paths are skipped unless BUNDLE_GIT_ALWAYS_PULL is
    enabled.
    """
    errors: dict[str, str] = {}
    if registry is None:
        reg = {}
    elif hasattr(registry, "bundles"):
        reg = {
            bid: entry.model_dump() if hasattr(entry, "model_dump") else dict(entry or {})
            for bid, entry in (getattr(registry, "bundles", {}) or {}).items()
        }
    else:
        reg = dict(registry or {})
    force_pull = get_settings().PLATFORM.APPLICATIONS.GIT.BUNDLE_GIT_ALWAYS_PULL

    for bid, entry in reg.items():
        repo = entry.get("repo")
        if not repo:
            continue

        path_val = (entry.get("path") or "").strip()
        cache_status = None
        if not path_val:
            try:
                paths = compute_git_bundle_paths(
                    bundle_id=bid,
                    git_url=repo,
                    git_ref=entry.get("ref"),
                    git_subdir=entry.get("subdir"),
                    bundles_root=resolve_managed_bundles_root(),
                )
                path_val = str(paths.bundle_root)
            except Exception:
                path_val = ""

        if path_val and not force_pull:
            try:
                cache_status = await git_bundle_cache_status(
                    bundle_id=bid,
                    git_url=repo,
                    git_ref=entry.get("ref"),
                    git_subdir=entry.get("subdir"),
                    bundles_root=resolve_managed_bundles_root(),
                )
                if cache_status.current:
                    logger.info(
                        "[Bundles] Git prefetch skip current: id=%s ref=%s subdir=%s path=%s",
                        bid,
                        entry.get("ref") or "head",
                        entry.get("subdir") or "",
                        path_val,
                    )
                    continue
            except Exception:
                logger.exception("[Bundles] Git prefetch cache validation failed: id=%s path=%s", bid, path_val)
            if cache_status is not None:
                logger.warning(
                    "[Bundles] Git prefetch cache not current: id=%s reason=%s ref=%s subdir=%s path=%s",
                    bid,
                    cache_status.reason,
                    entry.get("ref") or "head",
                    entry.get("subdir") or "",
                    path_val,
                )

        try:
            logger.info(
                "[Bundles] Git prefetch materialize start: id=%s ref=%s subdir=%s path=%s force_pull=%s",
                bid,
                entry.get("ref") or "head",
                entry.get("subdir") or "",
                path_val,
                bool(force_pull),
            )
            await ensure_git_bundle(
                bundle_id=bid,
                git_url=repo,
                git_ref=entry.get("ref"),
                git_subdir=entry.get("subdir"),
                bundles_root=resolve_managed_bundles_root(),
                atomic=get_settings().PLATFORM.APPLICATIONS.GIT.BUNDLE_GIT_ATOMIC,
            )
            logger.info(
                "[Bundles] Git prefetch materialize complete: id=%s ref=%s subdir=%s",
                bid,
                entry.get("ref") or "head",
                entry.get("subdir") or "",
            )
        except GitBundleCooldown as e:
            errors[bid] = str(e)
            logger.warning("[Bundles] Git prefetch cooldown: id=%s error=%s", bid, e)
        except Exception as e:
            errors[bid] = str(e)
            logger.exception("[Bundles] Git prefetch failed: id=%s", bid)

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
            scheduler_backend: Optional[str] = None,
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
        _svc = get_settings().PLATFORM.SERVICE
        self.scheduler_backend_name = normalize_processor_scheduler_backend(
            scheduler_backend or _svc.CHAT_SCHEDULER_BACKEND,
        )
        self._task_scheduler_backend = build_processor_scheduler_backend(self.scheduler_backend_name)
        legacy_task_timeout_sec = max(
            1,
            task_timeout_sec or _svc.CHAT_TASK_TIMEOUT_SEC,
        )
        self.task_timeout_sec = legacy_task_timeout_sec
        self.task_idle_timeout_sec = max(
            1,
            task_idle_timeout_sec or _svc.CHAT_TASK_IDLE_TIMEOUT_SEC or legacy_task_timeout_sec,
        )
        self.task_max_wall_time_sec = max(
            self.task_idle_timeout_sec,
            task_max_wall_time_sec or _svc.CHAT_TASK_MAX_WALL_TIME_SEC or max(legacy_task_timeout_sec, legacy_task_timeout_sec * 4),
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
        self._scheduler_reconcile_task: Optional[asyncio.Task] = None
        self._scheduler: Optional[Any] = None
        self._data_bus_manager: Optional[Any] = None
        self._active_tasks: set[asyncio.Task] = set()
        self._active_task_details: dict[asyncio.Task, Dict[str, Any]] = {}
        self._current_load = 0
        self._stop_event = asyncio.Event()
        self._queue_idx = 0
        self._work_queue_idx = 0
        self._background_queue_idx = 0
        self._work_queue_order = ("chat", "background")
        self._background_jobs = RedisBackgroundJobStream(
            self.redis,
            tenant=get_settings().TENANT,
            project=get_settings().PROJECT,
        )
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
            get_settings().PLATFORM.SERVICE.CHAT_TASK_WATCHDOG_POLL_INTERVAL_SEC,
        )
        _apps = getattr(getattr(get_settings(), "PLATFORM", None), "APPLICATIONS", None)
        self._bundle_scheduler_reconcile_interval_sec = max(
            0.0,
            float(getattr(_apps, "BUNDLE_SCHEDULER_RECONCILE_INTERVAL_SECONDS", 60) or 0),
        )
        self._host_draining = False
        self._host_draining_since: Optional[str] = None

    # ---------------- Public API ----------------

    async def start_processing(self):
        self._stop_event.clear()
        if self._processor_task and not self._processor_task.done():
            return
        self._task_scheduler_backend.validate_startup(self)
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
            _settings = get_settings()
            self._scheduler = BundleSchedulerManager(
                redis=self.redis,
                redis_url=getattr(_settings, "REDIS_URL", None),
                tenant=_settings.TENANT,
                project=_settings.PROJECT,
                instance_id=_settings.INSTANCE_ID,
            )
            try:
                await self._reconcile_bundle_scheduler_from_authority("startup")
            except Exception:
                logger.warning("Initial bundle scheduler reconcile failed; will retry from listener or periodic reconcile", exc_info=True)
        if self._data_bus_manager is None:
            from kdcube_ai_app.apps.chat.sdk.config import get_settings
            from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.worker import DataBusRuntimeManager
            _settings = get_settings()
            self._data_bus_manager = DataBusRuntimeManager(
                redis=self.redis,
                redis_url=getattr(_settings, "REDIS_URL", None),
                tenant=_settings.TENANT,
                project=_settings.PROJECT,
                instance_id=_settings.INSTANCE_ID,
            )
            try:
                await self._reconcile_bundle_scheduler_from_authority("data-bus-startup")
            except Exception:
                logger.warning("Initial Data Bus reconcile failed; will retry from listener or periodic reconcile", exc_info=True)
        if (
            self._bundle_scheduler_reconcile_interval_sec > 0
            and (not self._scheduler_reconcile_task or self._scheduler_reconcile_task.done())
        ):
            self._scheduler_reconcile_task = asyncio.create_task(
                self._bundle_scheduler_reconcile_loop(),
                name="bundle-scheduler-periodic-reconcile-loop",
            )

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
        await self._await_background_task_exit(
            self._scheduler_reconcile_task,
            name="bundle-scheduler-periodic-reconcile-loop",
        )
        self._scheduler_reconcile_task = None
        if self._scheduler is not None:
            await self._scheduler.shutdown()
            self._scheduler = None
        if self._data_bus_manager is not None:
            await self._data_bus_manager.shutdown()
            self._data_bus_manager = None
        await self.wait_for_active_tasks()

    def get_current_load(self) -> int:
        return self._current_load

    def get_runtime_metadata(self) -> Dict[str, Any]:
        now = time.monotonic()
        oldest_active_task_wall_age_sec = 0.0
        max_active_task_idle_age_sec = 0.0
        active_task_details: List[Dict[str, Any]] = []
        for task in list(self._active_tasks):
            info = self._active_task_details.get(task) or {}
            ages = self._task_age_snapshot(info, now=now)
            wall_age = ages.get("wall_age_sec")
            idle_age = ages.get("idle_age_sec")
            if wall_age is not None:
                oldest_active_task_wall_age_sec = max(oldest_active_task_wall_age_sec, float(wall_age))
            if idle_age is not None:
                max_active_task_idle_age_sec = max(max_active_task_idle_age_sec, float(idle_age))
            detail = {
                "task_id": info.get("task_id"),
                "queue_key": info.get("queue_key"),
                "inflight_queue_key": info.get("inflight_queue_key"),
                "started_execution": bool(info.get("started_execution")),
                "started_at": info.get("started_at"),
                "claimed_at": info.get("claimed_at"),
                "last_activity_at": info.get("last_activity_at"),
                "last_activity_kind": info.get("last_activity_kind"),
                "activity_count": int(info.get("activity_count") or 0),
                "wall_age_sec": wall_age,
                "idle_age_sec": idle_age,
            }
            if info.get("bundle_id"):
                detail["bundle_id"] = info.get("bundle_id")
            active_task_details.append(detail)
        active_task_details.sort(
            key=lambda row: (
                float(row.get("idle_age_sec") or 0.0),
                str(row.get("task_id") or ""),
            ),
            reverse=True,
        )
        metadata = {
            "current_load": self._current_load,
            "active_tasks": len(self._active_tasks),
            "active_task_details": active_task_details,
            "draining": self._stop_event.is_set(),
            "host_draining": self._host_draining,
            "host_draining_since": self._host_draining_since,
            "accepting_new_tasks": not self._stop_event.is_set() and not self._host_draining,
            "scheduler_backend": self.scheduler_backend_name,
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

    async def _run_handler_with_watchdog(self, payload: ExternalEventPayload):
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

    async def _reconcile_bundle_scheduler_from_authority(self, reason: str) -> None:
        if self._scheduler is None and self._data_bus_manager is None:
            return
        from kdcube_ai_app.infra.plugin.bundle_store import load_registry

        settings = get_settings()
        reg = await load_registry(self.redis, settings.TENANT, settings.PROJECT)
        if self._scheduler is not None:
            await self._scheduler.reconcile(reg)
        if self._data_bus_manager is not None:
            await self._data_bus_manager.reconcile(reg)
        logger.info(
            "Bundle runtime reconcile complete: reason=%s bundles=%s default=%s scheduler=%s data_bus=%s",
            reason,
            len(getattr(reg, "bundles", {}) or {}),
            getattr(reg, "default_bundle_id", None),
            self._scheduler is not None,
            self._data_bus_manager is not None,
        )

    async def _bundle_scheduler_reconcile_loop(self) -> None:
        interval = self._bundle_scheduler_reconcile_interval_sec
        logger.info(
            "Bundle scheduler periodic reconcile enabled: interval_sec=%s",
            interval,
        )
        while not self._stop_event.is_set():
            try:
                jitter = random.uniform(0.0, min(interval * 0.1, 5.0)) if interval > 0 else 0.0
                await asyncio.sleep(interval + jitter)
                if self._stop_event.is_set():
                    break
                await self._reconcile_bundle_scheduler_from_authority("periodic")
            except asyncio.CancelledError:
                break
            except Exception:
                logger.warning("Periodic bundle scheduler reconcile failed", exc_info=True)

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

    @staticmethod
    def _task_bundle_id(task_dict: Dict[str, Any]) -> Optional[str]:
        routing = task_dict.get("routing")
        bundle_id = routing.get("bundle_id") if isinstance(routing, dict) else None
        bundle_id = str(bundle_id or "").strip()
        return bundle_id or None

    def _task_lock_key(self, logical_id: str) -> str:
        return f"{self.middleware.LOCK_PREFIX}:{logical_id}"

    def _task_started_key(self, logical_id: str) -> str:
        return f"{self.middleware.LOCK_PREFIX}:started:{logical_id}"

    def _task_conversation_lock_key(self, task_dict: Dict[str, Any]) -> Optional[str]:
        routing = task_dict.get("routing") if isinstance(task_dict.get("routing"), dict) else {}
        actor = task_dict.get("actor") if isinstance(task_dict.get("actor"), dict) else {}
        user = task_dict.get("user") if isinstance(task_dict.get("user"), dict) else {}
        conversation_id = str(routing.get("conversation_id") or routing.get("session_id") or "").strip()
        if not conversation_id:
            return None
        tenant = actor.get("tenant_id") or actor.get("tenant") or ""
        project = actor.get("project_id") or actor.get("project") or ""
        user_id = user.get("user_id") or user.get("fingerprint") or ""
        parts = (
            safe_event_lane_part(tenant, default="_"),
            safe_event_lane_part(project, default="_"),
            safe_event_lane_part(user_id, default="_"),
            safe_event_lane_part(conversation_id, default="_"),
        )
        return f"{self.middleware.LOCK_PREFIX}:conversation:{':'.join(parts)}"

    def _processor_lock_token(self, logical_id: str) -> str:
        return f"{self.middleware.instance_id}:{self.process_id}:{logical_id}"

    async def _release_redis_lock(self, key: Optional[str], token: Optional[str] = None) -> bool:
        if not key:
            return False
        if token:
            evaluator = getattr(self.redis, "eval", None)
            if callable(evaluator):
                return bool(await evaluator(
                    """
                    if redis.call('GET', KEYS[1]) == ARGV[1] then
                        return redis.call('DEL', KEYS[1])
                    end
                    return 0
                    """,
                    1,
                    key,
                    token,
                ))
            current = await self.redis.get(key)
            if isinstance(current, bytes):
                current = current.decode("utf-8")
            if str(current or "") != str(token):
                return False
        return bool(await self.redis.delete(key))

    async def _renew_redis_lock(self, key: Optional[str], token: Optional[str], ttl_seconds: int) -> bool:
        if not key:
            return False
        ttl_seconds = int(ttl_seconds or self.lock_ttl_sec)
        if token:
            evaluator = getattr(self.redis, "eval", None)
            if callable(evaluator):
                return bool(await evaluator(
                    """
                    if redis.call('GET', KEYS[1]) == ARGV[1] then
                        return redis.call('EXPIRE', KEYS[1], ARGV[2])
                    end
                    return 0
                    """,
                    1,
                    key,
                    token,
                    str(ttl_seconds),
                ))
            current = await self.redis.get(key)
            if isinstance(current, bytes):
                current = current.decode("utf-8")
            if str(current or "") != str(token):
                return False
        ttl = await self.redis.ttl(key)
        if ttl is None or ttl < 0:
            return False
        await self.redis.expire(key, ttl_seconds)
        return True

    async def _started_marker_exists(self, logical_id: str) -> bool:
        ttl = await self.redis.ttl(self._task_started_key(logical_id))
        return ttl is not None and ttl >= -1

    async def _mark_task_started(self, task_data: Dict[str, Any], payload: ExternalEventPayload, request_id: str) -> Optional[str]:
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

    def _build_runtime_context(self, payload: ExternalEventPayload):
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

    def _external_event_source_for(self, payload: ExternalEventPayload):
        event_ctx = getattr(payload, "event", None)
        if event_ctx is None:
            user_id = ""
            agent_id = DEFAULT_REACT_AGENT_ID
        else:
            try:
                user_id = payload.user.user_id or payload.user.fingerprint or ""
            except Exception:
                user_id = ""
            try:
                agent_id = normalize_agent_id(getattr(event_ctx, "agent_id", None))
            except Exception:
                agent_id = DEFAULT_REACT_AGENT_ID
        # Conversation event lanes are partitioned by agent_id. Promotion reads
        # the same lane that ingress wrote, keeping internal agent streams
        # independent for the same conversation.
        return build_conversation_external_event_source(
            redis=self.redis,
            tenant=payload.actor.tenant_id,
            project=payload.actor.project_id,
            conversation_id=payload.routing.conversation_id or payload.routing.session_id,
            user_id=user_id,
            agent_id=agent_id,
        )

    def _external_event_source_for_wakeup(self, wakeup: ExternalEventLaneWakeup):
        lane = wakeup.event_lane
        return build_conversation_external_event_source(
            redis=self.redis,
            tenant=lane.tenant or wakeup.actor.tenant_id,
            project=lane.project or wakeup.actor.project_id,
            conversation_id=lane.conversation_id or wakeup.routing.conversation_id or wakeup.routing.session_id,
            user_id=lane.user_id or wakeup.user.user_id or wakeup.user.fingerprint or "",
            agent_id=normalize_agent_id(lane.agent_id, default=DEFAULT_REACT_AGENT_ID),
        )

    @staticmethod
    def _is_external_event_lane_wakeup(task_data: Dict[str, Any]) -> bool:
        kind = str(task_data.get("kind") or "").strip()
        return kind == "external_event_lane_wakeup" or isinstance(task_data.get("event_lane"), dict)

    @staticmethod
    def _payload_for_lane_wakeup(
        payload: ExternalEventPayload,
        *,
        wakeup: ExternalEventLaneWakeup,
        event: Any,
    ) -> ExternalEventPayload:
        resolved = payload.model_copy(deep=True)
        if resolved.request is None:
            resolved.request = ExternalEventRequest(request_id=str(getattr(getattr(resolved, "meta", None), "task_id", "") or ""))
        if resolved.event is None:
            resolved.event = ExternalEvent()
        resolved.event.kind = str(getattr(event, "kind", "") or resolved.event.kind or "message")
        # Rehydrate the processor envelope from the retained lane occurrence.
        # Bundle @on_reactive_event handlers can read this as the canonical
        # target agent for internal dispatch.
        resolved.event.agent_id = normalize_agent_id(
            getattr(event, "agent_id", None) or wakeup.event_lane.agent_id,
            default=DEFAULT_REACT_AGENT_ID,
        )
        resolved.event.event_source_id = str(
            getattr(event, "event_source_id", "") or resolved.event.event_source_id or ""
        ) or None
        resolved.event.event_id = str(getattr(event, "message_id", "") or wakeup.event_lane.event_id or "") or None
        resolved.event.sequence = int(getattr(event, "sequence", 0) or wakeup.event_lane.sequence or 0) or None
        resolved.event.source = "processor.external_event_lane_wakeup"
        resolved.bundle_call_context = dict(getattr(resolved, "bundle_call_context", {}) or {})
        resolved.bundle_call_context["event_lane_wakeup"] = wakeup.model_dump()
        return resolved

    async def _resolve_queue_item_payload(self, task_data: Dict[str, Any]) -> ExternalEventPayload:
        if not self._is_external_event_lane_wakeup(task_data):
            return ExternalEventPayload.model_validate(task_data)

        wakeup = ExternalEventLaneWakeup.model_validate(task_data)
        source = self._external_event_source_for_wakeup(wakeup)
        event_id = str(wakeup.event_lane.event_id or "").strip()
        if not event_id:
            raise RuntimeError("External event lane wakeup is missing event_id")
        event = await source.get_event(event_id)
        if event is None:
            raise RuntimeError(f"External event lane wakeup event not found: {event_id}")
        orchestrator = ConversationEventBusOrchestrator.for_source(source)
        wake_ts = event_timestamp(event)
        state = await orchestrator.state()
        if timestamp_lte(wake_ts, state.last_processed_reactive_event_timestamp):
            raise ExternalEventLaneWakeIgnored("wake_already_processed")
        payload = event.task_payload_model()
        decision = await orchestrator.schedule_consumer_from_wake(wake_event_timestamp=wake_ts)
        if not decision.scheduled:
            raise ExternalEventLaneWakeIgnored(decision.reason)
        return self._payload_for_lane_wakeup(payload, wakeup=wakeup, event=event)

    async def _mark_task_interrupted(self, task_dict: Dict[str, Any], *, reason: str) -> None:
        try:
            payload = await self._resolve_queue_item_payload(task_dict)
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
            logger.warning(
                "Queue claim timed out after %.2fs on %s->%s; leaving shared pool intact",
                self.queue_call_timeout_sec,
                ready_queue_key,
                inflight_queue_key,
            )
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
            lock_token: Optional[str] = None,
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
                await self._release_redis_lock(lock_key, lock_token)
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
            lock_token: Optional[str] = None,
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
                await self._release_redis_lock(lock_key, lock_token)
            if started_key:
                await self.redis.delete(started_key)
            return bool(removed)
        except Exception:
            logger.exception("Failed to requeue claimed payload: %s", reason)
            return False

    async def _ack_claimed_task(self, task_data: Dict[str, Any]) -> None:
        background_claim = task_data.get("_background_job_claim")
        if background_claim is not None:
            try:
                await self._background_jobs.ack(background_claim)
            finally:
                lock_key = task_data.get("_lock_key")
                lock_token = task_data.get("_lock_token")
                started_key = task_data.get("_started_key")
                if lock_key:
                    await self._release_redis_lock(lock_key, lock_token)
                if started_key:
                    await self.redis.delete(started_key)
            return
        await self._drop_claimed_payload(
            inflight_queue_key=task_data.get("_inflight_queue_key"),
            raw_payload=task_data.get("_raw_payload"),
            lock_key=task_data.get("_lock_key"),
            lock_token=task_data.get("_lock_token"),
            started_key=task_data.get("_started_key"),
            reason=f"task-finished:{self._task_logical_id(task_data) or 'unknown'}",
        )

    async def _inflight_recovery_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                reclaimed = await self._task_scheduler_backend.recover_stale_claims(self)
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

    async def _legacy_requeue_stale_inflight_tasks(self) -> int:
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

    async def _requeue_stale_inflight_tasks(self) -> int:
        return await self._legacy_requeue_stale_inflight_tasks()

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

                task_data = await self._claim_next_work_item()
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
                    "bundle_id": self._task_bundle_id(task_data),
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

    async def _claim_next_work_item(self) -> Optional[Dict[str, Any]]:
        for _ in range(len(self._work_queue_order)):
            kind = self._work_queue_order[self._work_queue_idx]
            self._work_queue_idx = (self._work_queue_idx + 1) % len(self._work_queue_order)
            if kind == "background":
                claimed = await self._claim_next_background_job()
            else:
                claimed = await self._task_scheduler_backend.claim_next_task(self)
            if claimed:
                return claimed
        return None

    def _next_background_queue_order(self) -> tuple[str, ...]:
        order = tuple(BACKGROUND_JOB_QUEUE_ORDER)
        if not order:
            return ("registered",)
        idx = self._background_queue_idx % len(order)
        self._background_queue_idx = (self._background_queue_idx + 1) % len(order)
        return order[idx:] + order[:idx]

    @staticmethod
    def _metadata_list(value: Any) -> list[str]:
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value if str(item or "").strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _background_job_to_chat_task(self, claim) -> Dict[str, Any]:
        job = claim.job
        job_payload = job.payload or {}
        metadata = job.metadata or {}
        conversation_id = str(
            metadata.get("conversation_id")
            or job_payload.get("conversation_id")
            or f"job_{job.job_id}"
        ).strip()
        turn_id = str(
            metadata.get("turn_id")
            or job_payload.get("turn_id")
            or f"job_turn_{job.job_id}"
        ).strip()
        request_id = str(metadata.get("request_id") or job.job_id or uuid.uuid4()).strip()
        message = str(
            metadata.get("text")
            or job_payload.get("text")
            or f"Run background job {job.work_kind} ({job.job_id})"
        ).strip()
        actor_tenant = job.tenant or get_settings().TENANT
        actor_project = job.project or get_settings().PROJECT
        user_type = job.user_type or job.queue or "registered"
        user_id = job.user_id or None
        timezone = metadata.get("timezone")
        payload = ExternalEventPayload(
            meta=ExternalEventMeta(
                task_id=job.job_id,
                created_at=float(job.created_at or time.time()),
                instance_id=self.middleware.instance_id,
            ),
            routing=ExternalEventRouting(
                bundle_id=job.bundle_id,
                session_id=conversation_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
            ),
            actor=ExternalEventActor(
                tenant_id=actor_tenant,
                project_id=actor_project,
            ),
            user=ExternalEventUser(
                user_type=user_type,
                user_id=user_id,
                username=str(metadata.get("username") or "") or None,
                email=str(metadata.get("email") or "") or None,
                fingerprint=str(metadata.get("fingerprint") or "") or None,
                roles=self._metadata_list(metadata.get("roles")),
                permissions=self._metadata_list(metadata.get("permissions")),
                timezone=timezone,
            ),
            request=ExternalEventRequest(
                operation=BACKGROUND_JOB_OPERATION,
                invocation="async",
                payload={
                    "job": job.to_dict(),
                    "job_id": job.job_id,
                    "work_kind": job.work_kind,
                    "metadata": metadata,
                    "payload": job_payload,
                    "text": message,
                },
                request_id=request_id,
            ),
            config=ExternalEventConfig(values={}),
            accounting=ExternalEventAccounting(
                envelope={
                    "user_id": user_id,
                    "session_id": conversation_id,
                    "user_type": user_type,
                    "tenant_id": actor_tenant,
                    "project_id": actor_project,
                    "request_id": request_id,
                    "component": job.bundle_id or "chat.orchestrator",
                    "app_bundle_id": job.bundle_id,
                    "timezone": timezone,
                    "metadata": {
                        "job_id": job.job_id,
                        "work_kind": job.work_kind,
                        "conversation_id": conversation_id,
                        "turn_id": turn_id,
                    },
                    "seed_system_resources": [],
                }
            ),
            event=ExternalEvent(
                kind="background_job",
                agent_id=DEFAULT_REACT_AGENT_ID,
                event_source_id=f"job.{job.work_kind or 'background'}",
                reactive=True,
                source="processor.background_job",
            ),
            bundle_call_context={
                "kind": "background_job",
                "job_id": job.job_id,
                "work_kind": job.work_kind,
                "source": dict(job.source or {}),
                "metadata": metadata,
                "payload": job_payload,
            },
        )
        task_data = payload.model_dump()
        task_data["_background_job_claim"] = claim
        task_data["_queue_key"] = claim.stream_key
        task_data["_ready_queue_key"] = claim.stream_key
        task_data["_inflight_queue_key"] = None
        task_data["_raw_payload"] = claim.stream_id
        return task_data

    async def _claim_next_background_job(self) -> Optional[Dict[str, Any]]:
        consumer_name = f"{self.middleware.instance_id}:{self.process_id}"
        claim = await self._background_jobs.claim_next(
            consumer_name=consumer_name,
            queue_order=self._next_background_queue_order(),
            count=1,
            block_ms=1,
        )
        if claim is None:
            return None
        task_dict = self._background_job_to_chat_task(claim)
        logical_id = self._task_logical_id(task_dict)
        if not logical_id:
            await self._background_jobs.ack(claim)
            logger.error("Background job missing job_id; acked stream=%s id=%s", claim.stream_key, claim.stream_id)
            return None
        lock_key = self._task_lock_key(logical_id)
        lock_token = self._processor_lock_token(logical_id)
        acquired = await self.redis.set(
            lock_key,
            lock_token,
            nx=True,
            ex=self.lock_ttl_sec,
        )
        if not acquired:
            logger.info("Background job lock held; leaving pending for retry: job_id=%s", logical_id)
            return None
        if self._stop_event.is_set() or self._host_draining:
            await self._release_redis_lock(lock_key, lock_token)
            return None
        self._current_load += 1
        created_at = (task_dict.get("meta") or {}).get("created_at")
        queue_wait_ms = None
        if created_at:
            try:
                queue_wait_ms = int((time.time() - float(created_at)) * 1000)
            except Exception:
                queue_wait_ms = None
        task_dict["_queue_wait_ms"] = queue_wait_ms
        task_dict["_lock_key"] = lock_key
        task_dict["_lock_token"] = lock_token
        logger.info(
            "Process %s acquired background job %s (%s) stream=%s id=%s%s",
            self.process_id,
            logical_id,
            claim.job.queue,
            claim.stream_key,
            claim.stream_id,
            f" queue_wait_ms={queue_wait_ms}" if queue_wait_ms is not None else "",
        )
        return task_dict

    async def _legacy_pop_any_queue_fair(self) -> Optional[Dict[str, Any]]:
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
            lock_token = self._processor_lock_token(logical_id)
            acquired = await self.redis.set(
                lock_key,
                lock_token,
                nx=True,
                ex=self.lock_ttl_sec,
            )
            if acquired:
                conversation_lock_key = self._task_conversation_lock_key(task_dict)
                conversation_lock_token = ""
                if conversation_lock_key:
                    conversation_lock_token = self._processor_lock_token(logical_id)
                    conversation_acquired = await self.redis.set(
                        conversation_lock_key,
                        conversation_lock_token,
                        nx=True,
                        ex=self.lock_ttl_sec,
                    )
                    if not conversation_acquired:
                        await self._requeue_claimed_payload(
                            ready_queue_key=queue_key,
                            inflight_queue_key=inflight_queue_key,
                            raw_payload=raw_payload,
                            lock_key=lock_key,
                            lock_token=lock_token,
                            reason=f"conversation-lock-not-acquired:{logical_id}",
                        )
                        continue
                    task_dict["_conversation_lock_key"] = conversation_lock_key
                    task_dict["_conversation_lock_token"] = conversation_lock_token
                if self._stop_event.is_set() or self._host_draining:
                    if conversation_lock_key:
                        await self._release_redis_lock(conversation_lock_key, conversation_lock_token)
                    await self._requeue_claimed_payload(
                        ready_queue_key=queue_key,
                        inflight_queue_key=inflight_queue_key,
                        raw_payload=raw_payload,
                        lock_key=lock_key,
                        lock_token=lock_token,
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
                task_dict["_lock_token"] = lock_token
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

    async def _pop_any_queue_fair(self) -> Optional[Dict[str, Any]]:
        return await self._legacy_pop_any_queue_fair()

    # ---------------- Config loop ----------------
    async def _config_listener_loop(self):
        import kdcube_ai_app.infra.namespaces as namespaces
        from kdcube_ai_app.apps.chat.sdk.config import get_settings
        from kdcube_ai_app.infra.plugin.bundle_registry import set_registry_async
        from kdcube_ai_app.infra.plugin.bundle_loader import (
            BundleSpec,
            evict_bundle_scope,
            invalidate_static_bundle_entrypoint_loads,
        )
        from kdcube_ai_app.infra.plugin.bundle_store import (
            load_registry as store_load,
            BundlesRegistry
        )

        settings = get_settings()
        tenant = settings.TENANT
        project = settings.PROJECT
        update_channel = namespaces.CONFIG.BUNDLES.UPDATE_CHANNEL.format(tenant=tenant, project=project)
        cleanup_channel = namespaces.CONFIG.BUNDLES.CLEANUP_CHANNEL.format(tenant=tenant, project=project)
        props_update_channel = namespaces.CONFIG.BUNDLES.PROPS_UPDATE_CHANNEL.format(tenant=tenant, project=project)
        secrets_update_channel = namespaces.CONFIG.BUNDLES.SECRETS_UPDATE_CHANNEL.format(tenant=tenant, project=project)

        def _invalidate_config_secret_cache(
                *,
                reason: str,
                bundle_id: str | None = None,
                user_id: str | None = None,
                keys: list[str] | set[str] | tuple[str, ...] | None = None,
        ) -> None:
            try:
                from kdcube_ai_app.apps.chat.sdk.config_cache import clear_secret_cache

                cleared = clear_secret_cache(
                    tenant=tenant,
                    project=project,
                    bundle_id=bundle_id,
                    user_id=user_id,
                    keys=keys,
                )
                logger.info(
                    "Invalidated central secret cache: reason=%s tenant=%s project=%s pid=%s bundle=%s user=%s keys=%s cleared=%s",
                    reason,
                    tenant,
                    project,
                    os.getpid(),
                    bundle_id,
                    user_id,
                    sorted(str(key) for key in (keys or [])),
                    cleared,
                )
            except Exception:
                logger.warning(
                    "Failed to invalidate central secret cache: reason=%s tenant=%s project=%s bundle=%s user=%s",
                    reason,
                    tenant,
                    project,
                    bundle_id,
                    user_id,
                    exc_info=True,
                )

        async def _catch_up_runtime_snapshot(reason: str, changed_bundle_ids: Optional[set[str]] = None) -> None:
            current = await store_load(self.redis, tenant, project)
            await set_registry_async(
                {bid: be.model_dump() for bid, be in (current.bundles or {}).items()},
                current.default_bundle_id,
                source=reason,
            )
            normalized_changed_bundle_ids = sorted(
                str(bid).strip() for bid in (changed_bundle_ids or set()) if str(bid).strip()
            )
            logger.info(
                "Bundle runtime catch-up started: reason=%s tenant=%s project=%s pid=%s changed_bundles=%s",
                reason,
                tenant,
                project,
                os.getpid(),
                normalized_changed_bundle_ids,
            )
            evictions: dict[str, dict[str, int]] = {}
            if normalized_changed_bundle_ids:
                for bundle_id in normalized_changed_bundle_ids:
                    entry = (current.bundles or {}).get(bundle_id)
                    if entry is None:
                        logger.warning(
                            "Bundle runtime catch-up skipped missing changed bundle: reason=%s tenant=%s project=%s pid=%s bundle=%s",
                            reason,
                            tenant,
                            project,
                            os.getpid(),
                            bundle_id,
                        )
                        continue
                    try:
                        spec = BundleSpec(
                            path=entry.path,
                            module=entry.module,
                            singleton=bool(getattr(entry, "singleton", False)),
                        )
                        evictions[bundle_id] = evict_bundle_scope(spec, drop_sys_modules=True)
                        invalidate_static_bundle_entrypoint_loads(
                            bundle_id=bundle_id,
                            tenant=tenant,
                            project=project,
                        )
                    except Exception:
                        logger.warning(
                            "Failed to evict changed bundle code during runtime catch-up: bundle=%s reason=%s",
                            bundle_id,
                            reason,
                            exc_info=True,
                        )
            else:
                logger.info(
                    "Bundle runtime catch-up has no changed bundle ids; preserving local loader caches: "
                    "reason=%s tenant=%s project=%s pid=%s",
                    reason,
                    tenant,
                    project,
                    os.getpid(),
                )
            if evictions:
                logger.info(
                    "Evicted changed bundle code during runtime catch-up: reason=%s tenant=%s project=%s pid=%s bundles=%s",
                    reason,
                    tenant,
                    project,
                    os.getpid(),
                    evictions,
                )
            if normalized_changed_bundle_ids:
                try:
                    from kdcube_ai_app.apps.chat.sdk.runtime.local_sidecars import stop_local_sidecars_for_bundle_ids

                    stopped_sidecars = stop_local_sidecars_for_bundle_ids(
                        bundle_ids=set(normalized_changed_bundle_ids),
                        tenant=tenant,
                        project=project,
                        terminate_timeout_sec=2.0,
                        kill_timeout_sec=1.0,
                    )
                    if stopped_sidecars:
                        logger.info(
                            "Stopped local sidecars after bundle runtime catch-up: tenant=%s project=%s count=%s bundles=%s",
                            tenant,
                            project,
                            stopped_sidecars,
                            normalized_changed_bundle_ids,
                        )
                except Exception:
                    logger.warning("Failed to stop local sidecars after bundle runtime catch-up", exc_info=True)
            try:
                from kdcube_ai_app.apps.chat.sdk.runtime.local_sidecars import stop_inactive_local_sidecars

                stopped_sidecars = stop_inactive_local_sidecars(
                    active_bundle_ids={str(bid).strip() for bid in (current.bundles or {}).keys() if str(bid).strip()},
                    tenant=tenant,
                    project=project,
                    terminate_timeout_sec=2.0,
                    kill_timeout_sec=1.0,
                )
                if stopped_sidecars:
                    logger.info(
                        "Stopped inactive local sidecars after bundle runtime catch-up: tenant=%s project=%s count=%s",
                        tenant,
                        project,
                        stopped_sidecars,
                    )
            except Exception:
                logger.warning("Failed to stop inactive local sidecars after bundle runtime catch-up", exc_info=True)
            if self._scheduler is not None:
                await self._scheduler.reconcile(current)
            if self._data_bus_manager is not None:
                await self._data_bus_manager.reconcile(current)
            logger.info(
                "Bundle runtime catch-up complete: reason=%s tenant=%s project=%s pid=%s bundles=%s default=%s changed_bundles=%s evicted_bundles=%s",
                reason,
                tenant,
                project,
                os.getpid(),
                len(current.bundles or {}),
                current.default_bundle_id,
                normalized_changed_bundle_ids,
                sorted(evictions.keys()),
            )

        backoff = 0.5
        while not self._stop_event.is_set():
            pubsub = None
            try:
                pubsub = self.redis.pubsub()
                await pubsub.subscribe(
                    update_channel,
                    cleanup_channel,
                    props_update_channel,
                    secrets_update_channel,
                )
                logger.info(
                    "Subscribed to bundles channels: "
                    f"{update_channel}, {cleanup_channel}, {props_update_channel}, {secrets_update_channel}"
                )
                try:
                    await _catch_up_runtime_snapshot("config-listener.subscribe")
                except Exception:
                    logger.warning("Bundle runtime catch-up after subscribe failed", exc_info=True)
                backoff = 0.5
                self._last_config_error = None

                # Catch-up reconcile: pubsub does not buffer, so any
                # bundles.update / bundles.props.update event published while
                # this listener was disconnected was lost. Sync managed bundle
                # runtime state to current Redis registry/props on every
                # (re)subscribe.
                if self._scheduler is not None or self._data_bus_manager is not None:
                    try:
                        current_reg = await store_load(self.redis)
                        if self._scheduler is not None:
                            await self._scheduler.reconcile(current_reg)
                        if self._data_bus_manager is not None:
                            await self._data_bus_manager.reconcile(current_reg)
                    except Exception:
                        logger.warning(
                            "Catch-up bundle runtime reconcile after (re)subscribe failed",
                            exc_info=True,
                        )

                while not self._stop_event.is_set():
                    try:
                        message = await asyncio.wait_for(
                            pubsub.get_message(
                                ignore_subscribe_messages=True,
                                timeout=self.config_get_message_timeout_sec,
                            ),
                            timeout=self.config_call_timeout_sec,
                        )
                    except asyncio.TimeoutError:
                        self._last_config_poll_completed_at = time.monotonic()
                        self._last_config_error = (
                            f"Config listener get_message exceeded {self.config_call_timeout_sec:.2f}s"
                        )
                        logger.warning("%s; leaving shared pool intact", self._last_config_error)
                        await asyncio.sleep(0.1)
                        continue
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
                        raw_registry = evt.get("registry") or {}
                        raw_bundles = raw_registry.get("bundles") if isinstance(raw_registry, dict) else {}
                        changed_bundle_ids = {
                            str(bid).strip()
                            for bid in ((raw_bundles or {}).keys() if isinstance(raw_bundles, dict) else [])
                            if str(bid).strip()
                        }
                        try:
                            await _catch_up_runtime_snapshot("bundles.snapshot", changed_bundle_ids=changed_bundle_ids)
                        except Exception:
                            logger.warning("Bundle runtime catch-up failed after snapshot", exc_info=True)
                        continue

                    if evt.get("type") == "bundles.update":
                        bundles_patch = evt.get("bundles") or {}
                        changed_raw = evt.get("changed_bundle_ids")
                        if isinstance(changed_raw, list):
                            changed_bundle_ids = {
                                str(bid).strip()
                                for bid in changed_raw
                                if str(bid).strip()
                            }
                        else:
                            changed_bundle_ids = {
                                str(bid).strip()
                                for bid in ((bundles_patch or {}).keys() if isinstance(bundles_patch, dict) else [])
                                if str(bid).strip()
                            }
                        logger.info(
                            "Received bundles.update broadcast: tenant=%s project=%s pid=%s changed_bundles=%s default=%s updated_by=%s ts=%s",
                            tenant,
                            project,
                            os.getpid(),
                            sorted(changed_bundle_ids),
                            evt.get("default_bundle_id"),
                            evt.get("updated_by"),
                            evt.get("ts"),
                        )
                        for bundle_id in changed_bundle_ids:
                            _invalidate_config_secret_cache(
                                reason="bundles.update",
                                bundle_id=bundle_id,
                            )
                        try:
                            await _catch_up_runtime_snapshot("bundles.update", changed_bundle_ids=changed_bundle_ids)
                        except Exception:
                            logger.warning("Bundle runtime catch-up failed after bundles.update", exc_info=True)
                        continue

                    if evt.get("type") == "bundles.cleanup":
                        from kdcube_ai_app.infra.plugin.bundle_loader import evict_inactive_specs, BundleSpec
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
                            resolve_managed_bundles_root,
                            bundle_dir_for_git,
                        )
                        from kdcube_ai_app.infra.plugin.bundle_refs import get_active_paths
                        from types import SimpleNamespace

                        try:
                            current_reg = await store_load(self.redis, tenant, project)
                        except Exception:
                            current_reg = BundlesRegistry()

                        active_specs = []
                        for _bid, entry in (current_reg.bundles or {}).items():
                            try:
                                active_specs.append(BundleSpec(
                                    path=entry.path,
                                    module=entry.module,
                                    singleton=bool(entry.singleton),
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
                                active_bundle_ids={str(_bid).strip() for _bid in (current_reg.bundles or {}).keys() if str(_bid).strip()},
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
                        # Git bundle cleanup (skip active refs from active registry)
                        try:
                            active_paths = await get_active_paths(
                                self.redis,
                                tenant=tenant,
                                project=project,
                            )
                            bundles = current_reg.bundles or {}
                            active_storage_paths = []
                            for _bid, entry in bundles.items():
                                try:
                                    spec = SimpleNamespace(
                                        id=_bid,
                                        git_commit=getattr(entry, "git_commit", None),
                                        ref=getattr(entry, "ref", None),
                                        version=getattr(entry, "version", None),
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
                                repo = getattr(entry, "repo", None)
                                if not repo:
                                    pass
                                else:
                                    base_dir = bundle_dir_for_git(_bid, getattr(entry, "ref", None))
                                    await cleanup_old_git_bundles_async(
                                        bundle_id=base_dir,
                                        bundles_root=resolve_managed_bundles_root(),
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
                                    bundles_root=resolve_managed_bundles_root(),
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
                        secrets_update_channel,
                        secrets_update_channel.encode(),
                    ):
                        bundle_id = str(evt.get("bundle_id") or "").strip() or None
                        raw_keys = evt.get("keys") or []
                        keys = [str(key) for key in raw_keys if str(key)]
                        _invalidate_config_secret_cache(
                            reason=str(evt.get("type") or "bundles.secrets.update"),
                            bundle_id=bundle_id,
                            user_id=str(evt.get("user_id") or "").strip() or None,
                            keys=keys,
                        )
                        logger.info(
                            "Received bundles.secrets.update broadcast: tenant=%s project=%s pid=%s bundle=%s scope=%s mode=%s key_count=%s updated_by=%s ts=%s",
                            tenant,
                            project,
                            os.getpid(),
                            bundle_id,
                            evt.get("scope"),
                            evt.get("mode"),
                            len(keys),
                            evt.get("updated_by"),
                            evt.get("ts"),
                        )
                        continue

                    if message.get("channel") in (
                        props_update_channel,
                        props_update_channel.encode(),
                    ):
                        evt = {}
                        try:
                            evt = json.loads((message.get("data") or b"{}").decode("utf-8"))
                        except Exception:
                            evt = {}
                        if self._scheduler is not None or self._data_bus_manager is not None:
                            try:
                                current = await store_load(self.redis)
                                if self._scheduler is not None:
                                    await self._scheduler.reconcile(current)
                                if self._data_bus_manager is not None:
                                    await self._data_bus_manager.reconcile(current)
                            except Exception:
                                logger.warning("Bundle runtime reconcile failed after props update", exc_info=True)
                        bundle_id = str(evt.get("bundle_id") or "").strip()
                        if bundle_id:
                            _invalidate_config_secret_cache(
                                reason="bundles.props.update",
                                bundle_id=bundle_id,
                            )
                            try:
                                from kdcube_ai_app.infra.plugin.bundle_loader import (
                                    BundleSpec,
                                    notify_cached_bundle_props_changed,
                                )

                                current = await store_load(self.redis)
                                entry = (current.bundles or {}).get(bundle_id) if current else None
                                if entry is not None:
                                    spec = BundleSpec(
                                        path=entry.path,
                                        module=entry.module,
                                        singleton=bool(getattr(entry, "singleton", False)),
                                    )
                                    notified = await notify_cached_bundle_props_changed(
                                        spec,
                                        bundle_id=bundle_id,
                                        tenant=tenant,
                                        project=project,
                                        updated_by=evt.get("updated_by"),
                                        source=evt.get("source"),
                                        pg_pool=getattr(self.middleware, "pg_pool", None),
                                        redis=self.redis,
                                    )
                                    if notified:
                                        logger.info(
                                            "Applied on_props_changed to cached bundle singleton: tenant=%s project=%s bundle=%s",
                                            tenant,
                                            project,
                                            bundle_id,
                                        )
                            except Exception:
                                logger.warning("Cached bundle on_props_changed failed after props update", exc_info=True)
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
                        await pubsub.unsubscribe(update_channel, cleanup_channel, props_update_channel, secrets_update_channel)
                        await pubsub.close()
                    except Exception:
                        pass

    # ---------------- Per-task execution ----------------

    @asynccontextmanager
    async def _lock_renewer(
            self,
            lock_key: str,
            *,
            lock_token: Optional[str] = None,
            extra_keys: Optional[Iterable[str]] = None,
            extra_locks: Optional[Dict[str, tuple[Optional[str], int]]] = None,
            extra_ttl_sec: Optional[int] = None,
    ):
        lease_keys = tuple(key for key in (extra_keys or ()) if key)
        token_locks = {
            str(key): value
            for key, value in dict(extra_locks or {}).items()
            if key
        }
        extra_key_ttl = int(extra_ttl_sec or self.lock_ttl_sec)

        async def renewer():
            try:
                while True:
                    await asyncio.sleep(self.lock_renew_sec)
                    renewed = await self._renew_redis_lock(lock_key, lock_token, self.lock_ttl_sec)
                    if not renewed:
                        break
                    for extra_key in lease_keys:
                        try:
                            extra_ttl = await self.redis.ttl(extra_key)
                            if extra_ttl is None or extra_ttl < 0:
                                continue
                            await self.redis.expire(extra_key, extra_key_ttl)
                        except Exception:
                            logger.debug("Failed to renew extra lease key %s", extra_key, exc_info=True)
                    for extra_key, (extra_token, extra_ttl) in token_locks.items():
                        try:
                            await self._renew_redis_lock(extra_key, extra_token, int(extra_ttl or self.lock_ttl_sec))
                        except Exception:
                            logger.debug("Failed to renew extra token lock %s", extra_key, exc_info=True)
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
        lock_token = task_data.get("_lock_token")
        conversation_lock_key = task_data.get("_conversation_lock_key")
        conversation_lock_token = task_data.get("_conversation_lock_token")
        current_processor_task = asyncio.current_task()
        ephemeral_active_task_registration = False
        ephemeral_task_details = False

        # 1) Normalize payload
        try:
            payload = await self._resolve_queue_item_payload(task_data)
        except ExternalEventLaneWakeIgnored as e:
            logger.info("Ignoring external event lane wakeup: %s", e.reason)
            try:
                await self._ack_claimed_task(task_data)
            finally:
                self._current_load = max(0, self._current_load - 1)
                if conversation_lock_key:
                    try:
                        await self._release_redis_lock(conversation_lock_key, conversation_lock_token)
                    except Exception:
                        logger.debug("Failed to release conversation lock after ignored external event lane wakeup", exc_info=True)
                return
        except Exception as e:
            logger.error(f"Cannot normalize processor queue item: {e}")
            logger.error(traceback.format_exc())
            try:
                await self._ack_claimed_task(task_data)
            finally:
                # Ensure load is released even for invalid payloads
                self._current_load = max(0, self._current_load - 1)
                if conversation_lock_key:
                    try:
                        await self._release_redis_lock(conversation_lock_key, conversation_lock_token)
                    except Exception:
                        logger.debug("Failed to release conversation lock after invalid payload", exc_info=True)
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
                    "bundle_id": payload.routing.bundle_id,
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
        if str(getattr(payload.request, "operation", "") or "") != BACKGROUND_JOB_OPERATION:
            try:
                await self.conversation_ctx.set_conversation_state(
                    tenant=payload.actor.tenant_id,
                    project=payload.actor.project_id,
                    user_id=payload.user.user_id,
                    conversation_id=payload.routing.conversation_id,
                    new_state="in_progress",
                    by_instance=f"{self.middleware.instance_id}:{self.process_id}",
                    request_id=request_id,
                    last_turn_id=payload.routing.turn_id,
                    require_not_in_progress=False,
                    user_type=payload.user.user_type,
                    bundle_id=payload.routing.bundle_id,
                )
            except Exception:
                logger.debug(
                    "Failed to refresh in-progress conversation state at task start: conversation=%s task_id=%s",
                    payload.routing.conversation_id,
                    task_id,
                    exc_info=True,
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
        # Send full text derived from accepted event bodies. Clients treat
        # chat.start.data.message as the authoritative user-bubble text when
        # the SSE event wins a race against the POST ack.
        start_message = external_event_request_start_label(payload.request)

        success = False
        task_cancelled = False
        finalization_reason = "task_completed"
        exec_started_at = None
        try:
            protection_label = f"task_id={task_id}"
            async with self._task_scale_in_protection.hold(label=protection_label):
                started_key = await self._mark_task_started(task_data, payload, request_id)
                self._mark_task_execution_started()
                if started_key:
                    self._touch_task_activity("processor.started_marker")
                async with self._lock_renewer(
                        lock_key=lock_key,
                        lock_token=lock_token,
                        extra_keys=[key for key in (started_key,) if key],
                        extra_locks={
                            conversation_lock_key: (conversation_lock_token, self.lock_ttl_sec)
                        } if conversation_lock_key and conversation_lock_token else None,
                        extra_ttl_sec=self.started_marker_ttl_sec if started_key else None,
                ):
                    exec_started_at = time.monotonic()
                    await tracked_comm.start(message=start_message, queue_stats={})
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
                            peer_bundle_caller = make_local_bundle_operation_caller(
                                redis=self.redis,
                                pg_pool=getattr(self.middleware, "pg_pool", None),
                                comm_context=payload,
                            )
                            with bind_current_request_context(payload, comm=tracked_comm), bind_bundle_operation_caller(
                                peer_bundle_caller
                            ):
                                with bind_current_task_activity_touch(
                                        lambda kind, _task=processor_task: self._touch_task_activity(
                                            kind,
                                            task=_task,
                                        )
                                ):
                                    result = await self._run_handler_with_watchdog(payload)

                result = result or {}
                success = True
                finalization_reason = "task_completed"
                await tracked_comm.complete(data=result)

        except asyncio.CancelledError:
            task_cancelled = True
            finalization_reason = "task_cancelled"
            logger.warning(
                "Task %s was cancelled; keeping inflight claim for recovery",
                task_id,
            )
            raise
        except _TaskExecutionWatchdogTimeout as exc:
            finalization_reason = f"task_watchdog_timeout:{exc.timeout_kind}"
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
            finalization_reason = "task_error"
            tb = traceback.format_exc()
            try:
                await tracked_comm.error(message=tb, data={"task_id": task_id})
            except Exception:
                logger.debug("Failed to emit processor error for %s", task_id, exc_info=True)
            success = False
        finally:
            exec_ms = None
            if exec_started_at is not None:
                try:
                    exec_ms = int((time.monotonic() - exec_started_at) * 1000)
                except Exception:
                    exec_ms = None
            await _cleanup_turn_browser_sessions_for_payload(
                payload,
                reason=finalization_reason,
            )
            try:
                if not task_cancelled:
                    await self._ack_claimed_task(task_data)
            finally:
                self._current_load = max(0, self._current_load - 1)
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
                    res = await self.conversation_ctx.set_conversation_state(
                        tenant=payload.actor.tenant_id,
                        project=payload.actor.project_id,
                        user_id=payload.user.user_id,
                        conversation_id=payload.routing.conversation_id,
                        new_state=("idle" if success else "error"),
                        by_instance=f"{self.middleware.instance_id}:{self.process_id}",
                        request_id=request_id,
                        last_turn_id=payload.routing.turn_id,
                        require_not_in_progress=False,
                        user_type=payload.user.user_type,
                        bundle_id=payload.routing.bundle_id,
                    )
                    # broadcast to session
                    await self._relay.emit_conv_status(
                        svc,
                        conv,
                        routing=payload.routing,
                        state=("idle" if success else "error"),
                        updated_at=res["updated_at"],
                        current_turn_id=res.get("current_turn_id"),
                        completion="success" if success else "error",
                        target_sid=None,
                    )
                except Exception as ex:
                    logger.error(traceback.format_exc())
            if ephemeral_task_details and current_processor_task is not None:
                self._active_task_details.pop(current_processor_task, None)
            if ephemeral_active_task_registration and current_processor_task is not None:
                self._active_tasks.discard(current_processor_task)
            if conversation_lock_key:
                try:
                    await self._release_redis_lock(conversation_lock_key, conversation_lock_token)
                except Exception:
                    logger.debug("Failed to release conversation lock for task %s", task_id, exc_info=True)
