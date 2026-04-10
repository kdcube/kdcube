# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# apps/chat/sdk/runtime/bundle_scheduler.py
"""
Proc-owned bundle scheduler manager.

Responsibilities:
- Enumerate active bundles in the current tenant/project registry
- Load each bundle manifest and collect CronJobSpec entries
- Resolve each job's effective cron expression (expr_config wins over cron_expression)
- Create / cancel per-job asyncio tasks
- Rebind tasks when registry or props change (reconcile)
- Shut down cleanly when proc stops
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from croniter import croniter
from datetime import datetime, timezone

_log = logging.getLogger("kdcube.bundle.scheduler")

# Lock key shapes from the spec:
#   system:   bundle:cron:lock:{tenant}:{project}:{bundle_id}:{job_alias}
#   instance: bundle:cron:lock:{tenant}:{project}:{bundle_id}:{job_alias}:{instance_id}
_LOCK_KEY_SYSTEM = "bundle:cron:lock:{tenant}:{project}:{bundle_id}:{job_alias}"
_LOCK_KEY_INSTANCE = "bundle:cron:lock:{tenant}:{project}:{bundle_id}:{job_alias}:{instance_id}"

# How long (seconds) we hold the Redis lock — should cover the maximum expected job duration.
_LOCK_TTL_SECONDS = 3600
# How often (seconds) we renew the lock while the job is running.
_LOCK_RENEW_INTERVAL = 60


# ---------------------------------------------------------------------------
# Internal key used to identify a running task
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _JobKey:
    bundle_id: str
    job_alias: str


# ---------------------------------------------------------------------------
# Effective cron resolution
# ---------------------------------------------------------------------------

def resolve_effective_cron(
    cron_expression: Optional[str],
    expr_config: Optional[str],
    props: Dict[str, Any],
) -> Optional[str]:
    """
    Return the effective cron expression string, or None if the job should not
    be scheduled.

    Precedence:
    1. If expr_config is set — resolve dot-path against Redis bundle props first,
       then fall back to read_plain(expr_config) from bundles.yaml / assembly.yaml
       (configure via BUNDLES_YAML_DESCRIPTOR_PATH for local debug runs).
       - missing / blank / "disable" (case-insensitive) -> None (do not schedule)
       - do NOT fall back to cron_expression
    2. Else if cron_expression is set — use it directly.
    3. Else -> None (inert, do not schedule)
    """
    if expr_config:
        from kdcube_ai_app.infra.plugin.bundle_store import resolve_dot_path
        resolved = resolve_dot_path(props, expr_config)
        if not isinstance(resolved, str):
            # Not in Redis props — try bundles.yaml then assembly.yaml via read_plain.
            # This is the standard way to provide config for local debug runs
            # (set BUNDLES_YAML_DESCRIPTOR_PATH to point to your local bundles.yaml).
            try:
                from kdcube_ai_app.apps.chat.sdk.config import read_plain
                resolved = read_plain(f"b:{expr_config}", default=None)
                if not isinstance(resolved, str):
                    resolved = read_plain(expr_config, default=None)
            except Exception:
                resolved = None
        if not isinstance(resolved, str):
            return None
        resolved = resolved.strip()
        if not resolved or resolved.lower() == "disable":
            return None
        return resolved

    if cron_expression:
        return cron_expression

    return None


def _is_valid_cron(expr: str) -> bool:
    try:
        croniter(expr, datetime.now(timezone.utc))
        return True
    except Exception:
        return False


def _compute_next_run(expr: str, now: datetime) -> datetime:
    it = croniter(expr, now)
    return it.get_next(datetime)


# ---------------------------------------------------------------------------
# Per-job loop
# ---------------------------------------------------------------------------

async def _run_job_loop(
    *,
    bundle_id: str,
    job_alias: str,
    method_name: str,
    cron_expr: str,
    span: str,
    tenant: str,
    project: str,
    instance_id: str,
    redis: Any,
    bundle_spec: Any,
    bundle_config: Any,
) -> None:
    """
    Infinite loop for a single scheduled job. Sleeps until the next cron tick,
    then fires the job according to span semantics.
    """
    _log.info(
        "[scheduler] Job registered: bundle=%s alias=%s expr=%r span=%s",
        bundle_id, job_alias, cron_expr, span,
    )

    # For span="process": the job runs as a background task so the tick loop
    # stays on the absolute cron schedule. Overlap is detected at each tick by
    # checking whether the previous task is still running.
    _active_task: Optional[asyncio.Task] = None

    while True:
        now = datetime.now(timezone.utc)
        try:
            next_run = _compute_next_run(cron_expr, now)
        except Exception:
            _log.error(
                "[scheduler] Failed to compute next run for bundle=%s alias=%s expr=%r; stopping job loop",
                bundle_id, job_alias, cron_expr,
            )
            return

        sleep_seconds = (next_run - now).total_seconds()
        _log.debug(
            "[scheduler] bundle=%s alias=%s next tick in %.1fs",
            bundle_id, job_alias, sleep_seconds,
        )

        try:
            await asyncio.sleep(sleep_seconds)
        except asyncio.CancelledError:
            _log.info("[scheduler] Job cancelled: bundle=%s alias=%s", bundle_id, job_alias)
            if _active_task is not None and not _active_task.done():
                _active_task.cancel()
            return

        _log.info("[scheduler] Tick fired: bundle=%s alias=%s span=%s", bundle_id, job_alias, span)

        if span == "process":
            if _active_task is not None and not _active_task.done():
                _log.info(
                    "[scheduler] Skipping tick — previous run still active: bundle=%s alias=%s",
                    bundle_id, job_alias,
                )
                continue

            async def _run_safe(
                _bid: str = bundle_id,
                _alias: str = job_alias,
                _method: str = method_name,
                _spec: Any = bundle_spec,
                _cfg: Any = bundle_config,
            ) -> None:
                try:
                    await _invoke_job(
                        bundle_id=_bid,
                        method_name=_method,
                        bundle_spec=_spec,
                        bundle_config=_cfg,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _log.exception("[scheduler] Job failed: bundle=%s alias=%s", _bid, _alias)

            _active_task = asyncio.create_task(
                _run_safe(),
                name=f"cron-job:{bundle_id}:{job_alias}",
            )

        elif span in ("instance", "system"):
            await _run_with_redis_lock(
                bundle_id=bundle_id,
                job_alias=job_alias,
                method_name=method_name,
                span=span,
                tenant=tenant,
                project=project,
                instance_id=instance_id,
                redis=redis,
                bundle_spec=bundle_spec,
                bundle_config=bundle_config,
            )


async def _run_with_redis_lock(
    *,
    bundle_id: str,
    job_alias: str,
    method_name: str,
    span: str,
    tenant: str,
    project: str,
    instance_id: str,
    redis: Any,
    bundle_spec: Any,
    bundle_config: Any,
) -> None:
    if redis is None:
        _log.warning(
            "[scheduler] Redis unavailable — skipping tick for bundle=%s alias=%s span=%s "
            "(will not degrade to process)",
            bundle_id, job_alias, span,
        )
        return

    if span == "system":
        lock_key = _LOCK_KEY_SYSTEM.format(
            tenant=tenant, project=project,
            bundle_id=bundle_id, job_alias=job_alias,
        )
    else:  # instance
        lock_key = _LOCK_KEY_INSTANCE.format(
            tenant=tenant, project=project,
            bundle_id=bundle_id, job_alias=job_alias,
            instance_id=instance_id,
        )

    token = str(uuid.uuid4())

    try:
        got_lock = await redis.set(lock_key, token, ex=_LOCK_TTL_SECONDS, nx=True)
    except Exception:
        _log.warning(
            "[scheduler] Redis error acquiring lock for bundle=%s alias=%s — skipping tick",
            bundle_id, job_alias, exc_info=True,
        )
        return

    if not got_lock:
        _log.info(
            "[scheduler] Lock held by another; skipping tick: bundle=%s alias=%s span=%s key=%s",
            bundle_id, job_alias, span, lock_key,
        )
        return

    _log.info(
        "[scheduler] Lock acquired: bundle=%s alias=%s span=%s key=%s",
        bundle_id, job_alias, span, lock_key,
    )

    renewer_task: Optional[asyncio.Task] = None
    try:
        renewer_task = asyncio.create_task(
            _renew_lock_loop(redis=redis, lock_key=lock_key, token=token)
        )
        await _invoke_job(
            bundle_id=bundle_id,
            method_name=method_name,
            bundle_spec=bundle_spec,
            bundle_config=bundle_config,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        _log.exception(
            "[scheduler] Job failed: bundle=%s alias=%s", bundle_id, job_alias,
        )
    finally:
        if renewer_task is not None:
            renewer_task.cancel()
            try:
                await renewer_task
            except (asyncio.CancelledError, Exception):
                pass

        try:
            current_val = await redis.get(lock_key)
            if current_val is not None and current_val.decode() == token:
                await redis.delete(lock_key)
                _log.info(
                    "[scheduler] Lock released: bundle=%s alias=%s key=%s",
                    bundle_id, job_alias, lock_key,
                )
        except Exception:
            _log.exception(
                "[scheduler] Failed to release lock: bundle=%s alias=%s key=%s",
                bundle_id, job_alias, lock_key,
            )


async def _renew_lock_loop(*, redis: Any, lock_key: str, token: str) -> None:
    """Periodically extend the TTL of a held Redis lock."""
    while True:
        await asyncio.sleep(_LOCK_RENEW_INTERVAL)
        try:
            current_val = await redis.get(lock_key)
            if current_val is None or current_val.decode() != token:
                _log.warning("[scheduler] Lock lost during job run: key=%s", lock_key)
                return
            await redis.expire(lock_key, _LOCK_TTL_SECONDS)
        except Exception:
            _log.warning("[scheduler] Lock renewal failed: key=%s", lock_key, exc_info=True)


async def _invoke_job(
    *,
    bundle_id: str,
    method_name: str,
    bundle_spec: Any,
    bundle_config: Any,
) -> None:
    """Load the workflow instance and invoke the scheduled method."""
    from kdcube_ai_app.infra.plugin.agentic_loader import get_workflow_instance

    # Resolve pg_pool lazily — same singleton used by the rest of the proc process.
    pg_pool = None
    try:
        from kdcube_ai_app.apps.chat.ingress.resolvers import get_pg_pool
        pg_pool = await get_pg_pool()
    except Exception:
        _log.debug("[scheduler] pg_pool not available for bundle=%s; proceeding without", bundle_id)

    _log.info("[scheduler] Job started: bundle=%s method=%s", bundle_id, method_name)
    try:
        comm_context = getattr(bundle_config, "_headless_comm_context", bundle_config)
        instance, _ = get_workflow_instance(
            bundle_spec,
            bundle_config,
            comm_context=comm_context,
            redis=getattr(bundle_config, "redis", None),
            pg_pool=pg_pool,
        )
        # Ensure the instance has Redis-loaded bundle props overrides, not just
        # hardcoded defaults from configuration_defaults(). refresh_bundle_props
        # is normally called during execute(), but scheduled jobs bypass that path.
        refresh_fn = getattr(instance, "refresh_bundle_props", None)
        if callable(refresh_fn):
            try:
                await refresh_fn(state={
                    "tenant": getattr(bundle_config, "tenant", None),
                    "project": getattr(bundle_config, "project", None),
                })
            except Exception:
                _log.warning(
                    "[scheduler] refresh_bundle_props failed for bundle=%s; "
                    "proceeding with defaults",
                    bundle_id, exc_info=True,
                )

        fn = getattr(instance, method_name, None)
        if fn is None:
            _log.error(
                "[scheduler] Method not found on bundle instance: bundle=%s method=%s",
                bundle_id, method_name,
            )
            return
        if asyncio.iscoroutinefunction(fn):
            await fn()
        else:
            await asyncio.to_thread(fn)
        _log.info("[scheduler] Job completed: bundle=%s method=%s", bundle_id, method_name)
    except Exception:
        _log.exception("[scheduler] Job raised: bundle=%s method=%s", bundle_id, method_name)
        raise


# ---------------------------------------------------------------------------
# BundleSchedulerManager
# ---------------------------------------------------------------------------

class BundleSchedulerManager:
    """
    Central manager for all bundle-declared @cron jobs within one proc process.

    Usage::

        manager = BundleSchedulerManager(
            redis=redis, redis_url=settings.REDIS_URL,
            tenant=t, project=p, instance_id=iid,
        )
        await manager.reconcile(registry)   # call after startup and every registry/props update
        await manager.shutdown()            # call on proc stop

    ``redis`` is the processor's shared pool — used only for fast lock operations.
    ``redis_url`` is used to create a **dedicated** pool for bundle job invocations
    so that long-running jobs don't exhaust the processor's shared connection pool.
    """

    def __init__(
        self,
        *,
        redis: Any,
        redis_url: Optional[str] = None,
        tenant: str,
        project: str,
        instance_id: str,
    ) -> None:
        self._redis = redis
        self._tenant = tenant
        self._project = project
        self._instance_id = instance_id
        # Dedicated Redis client for bundle job invocations (separate from processor pool).
        # Uses decode_responses=False (default) which differs from the processor's
        # decode_responses=True pool — so get_async_redis_client returns a distinct pool.
        self._job_redis: Any = None
        if redis_url:
            try:
                from kdcube_ai_app.infra.redis.client import get_async_redis_client
                self._job_redis = get_async_redis_client(redis_url)
            except Exception:
                _log.warning(
                    "[scheduler] Could not create dedicated job Redis client; "
                    "falling back to shared pool",
                    exc_info=True,
                )
                self._job_redis = redis
        else:
            self._job_redis = redis
        # JobKey -> (asyncio.Task, effective_cron_expr)
        self._tasks: Dict[_JobKey, Tuple[asyncio.Task, str]] = {}
        _log.info(
            "[scheduler] Manager initialised: tenant=%s project=%s instance=%s",
            tenant, project, instance_id,
        )

    async def reconcile(self, registry: Any) -> None:
        """
        Diff running tasks against the current registry + props, cancel stale
        tasks, and start new ones.  Idempotent — safe to call repeatedly.
        """
        from kdcube_ai_app.infra.plugin.agentic_loader import (
            AgenticBundleSpec,
            load_bundle_manifest,
        )
        from kdcube_ai_app.infra.plugin.bundle_store import get_bundle_props

        desired: Dict[_JobKey, Tuple[str, str, str, Any, Any]] = {}
        # desired[key] = (cron_expr, method_name, span, bundle_spec, bundle_config)

        for bundle_id, entry in (registry.bundles or {}).items():
            path = entry.path if hasattr(entry, "path") else entry.get("path", "")
            module = entry.module if hasattr(entry, "module") else entry.get("module")
            singleton = (
                entry.singleton if hasattr(entry, "singleton") else entry.get("singleton", False)
            )
            if not path:
                continue

            spec = AgenticBundleSpec(path=path, module=module, singleton=bool(singleton))

            try:
                manifest = load_bundle_manifest(spec, bundle_id=bundle_id)
            except Exception:
                _log.warning(
                    "[scheduler] Failed to load manifest for bundle=%s; skipping",
                    bundle_id, exc_info=True,
                )
                continue

            if not manifest.scheduled_jobs:
                continue

            try:
                props = await get_bundle_props(
                    self._redis,
                    tenant=self._tenant,
                    project=self._project,
                    bundle_id=bundle_id,
                )
            except Exception:
                _log.warning(
                    "[scheduler] Failed to load props for bundle=%s; using empty props",
                    bundle_id, exc_info=True,
                )
                props = {}

            bundle_config = _make_headless_config(
                tenant=self._tenant,
                project=self._project,
                bundle_id=bundle_id,
                bundle_spec=spec,
                redis=self._job_redis,
                props=props,
            )

            for job_spec in manifest.scheduled_jobs:
                effective = resolve_effective_cron(
                    cron_expression=job_spec.cron_expression,
                    expr_config=job_spec.expr_config,
                    props=props,
                )

                key = _JobKey(bundle_id=bundle_id, job_alias=job_spec.alias)

                if effective is None:
                    _log.info(
                        "[scheduler] Job disabled (no valid cron): bundle=%s alias=%s "
                        "expr_config=%r cron_expression=%r",
                        bundle_id, job_spec.alias,
                        job_spec.expr_config, job_spec.cron_expression,
                    )
                    continue

                if not _is_valid_cron(effective):
                    _log.error(
                        "[scheduler] Invalid cron expression — not scheduling: "
                        "bundle=%s alias=%s expr=%r",
                        bundle_id, job_spec.alias, effective,
                    )
                    continue

                _log.debug(
                    "[scheduler] Resolved cron: bundle=%s alias=%s expr=%r span=%s",
                    bundle_id, job_spec.alias, effective, job_spec.span,
                )
                desired[key] = (effective, job_spec.method_name, job_spec.span, spec, bundle_config)

        # Cancel tasks that are no longer desired or whose cron changed
        for key in list(self._tasks.keys()):
            task, old_expr = self._tasks[key]
            if key not in desired:
                _log.info(
                    "[scheduler] Cancelling removed job: bundle=%s alias=%s",
                    key.bundle_id, key.job_alias,
                )
                task.cancel()
                del self._tasks[key]
            elif desired[key][0] != old_expr:
                _log.info(
                    "[scheduler] Cron changed — rescheduling: bundle=%s alias=%s old=%r new=%r",
                    key.bundle_id, key.job_alias, old_expr, desired[key][0],
                )
                task.cancel()
                del self._tasks[key]

        # Start new tasks
        for key, (cron_expr, method_name, span, spec, bundle_config) in desired.items():
            if key in self._tasks:
                continue
            _log.info(
                "[scheduler] Scheduling job: bundle=%s alias=%s expr=%r span=%s",
                key.bundle_id, key.job_alias, cron_expr, span,
            )
            task = asyncio.create_task(
                _run_job_loop(
                    bundle_id=key.bundle_id,
                    job_alias=key.job_alias,
                    method_name=method_name,
                    cron_expr=cron_expr,
                    span=span,
                    tenant=self._tenant,
                    project=self._project,
                    instance_id=self._instance_id,
                    redis=self._redis,
                    bundle_spec=spec,
                    bundle_config=bundle_config,
                ),
                name=f"cron:{key.bundle_id}:{key.job_alias}",
            )
            self._tasks[key] = (task, cron_expr)

    async def shutdown(self) -> None:
        """Cancel all running job tasks and wait for them to finish."""
        _log.info("[scheduler] Shutting down — cancelling %d job(s)", len(self._tasks))
        for key, (task, _) in list(self._tasks.items()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*[t for t, _ in self._tasks.values()], return_exceptions=True)
        self._tasks.clear()
        _log.info("[scheduler] Shutdown complete")


# ---------------------------------------------------------------------------
# Headless bundle config
# ---------------------------------------------------------------------------

def _make_headless_config(
    *,
    tenant: str,
    project: str,
    bundle_id: str,
    bundle_spec: Any,
    redis: Any,
    props: Dict[str, Any],
) -> Any:
    """
    Build a real Config object for headless (no user session) scheduled job invocation.

    Uses the real Config class so that bundle internals such as refresh_bundle_props
    can resolve self.config.ai_bundle_spec.id for Redis props lookup.
    """
    from types import SimpleNamespace
    from kdcube_ai_app.infra.service_hub.inventory import Config
    from kdcube_ai_app.infra.plugin.bundle_registry import BundleSpec

    config = Config()
    config.tenant = tenant
    config.project = project
    config.ai_bundle_spec = BundleSpec(
        id=bundle_id,
        path=getattr(bundle_spec, "path", ""),
        module=getattr(bundle_spec, "module", None),
        singleton=getattr(bundle_spec, "singleton", False),
    )

    # Pass Redis so bundle internals (self.redis, storage helpers) work in headless mode.
    config.redis = redis

    # Minimal comm_context so get_workflow_instance can extract tenant/project
    config._headless_comm_context = SimpleNamespace(
        actor=SimpleNamespace(tenant_id=tenant, project_id=project),
        meta=SimpleNamespace(tenant=tenant, project=project),
    )

    return config
