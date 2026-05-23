from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from croniter import croniter

from kdcube_ai_app.infra.jobs.stream import RedisBackgroundJobStream
from .async_storage import AsyncTaskStorage, list_task_user_ids


WORK_KIND_TASK_EXECUTION_DUE = "task.execution.due"

_storage_root_or_error = None
_task_operations = None


def configure_due_tasks(
    *,
    storage_root_or_error: Any,
    task_operations_module: Any,
) -> None:
    """Bind bundle-specific storage and task operation hooks."""
    global _storage_root_or_error, _task_operations
    _storage_root_or_error = storage_root_or_error
    _task_operations = task_operations_module


def _storage_root(entrypoint: Any) -> str:
    for method_name in ("task_storage_root", "storage_root_or_error"):
        resolver = getattr(entrypoint, method_name, None)
        if callable(resolver):
            return str(resolver())
    if not callable(_storage_root_or_error):
        raise RuntimeError("task due scheduler is not configured: storage_root_or_error is missing")
    return str(_storage_root_or_error(entrypoint))


def _operations() -> Any:
    if _task_operations is not None:
        return _task_operations
    from . import operations

    return operations


def _safe_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or "").strip() or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _task_recurring(task: Dict[str, Any]) -> bool:
    schedule = task.get("schedule") if isinstance(task.get("schedule"), dict) else {}
    value = schedule.get("recurring")
    if value is None:
        return True
    if type(value) is bool:
        return value
    raise ValueError("schedule.recurring must be a boolean")


def _task_due_slot(task: Dict[str, Any], *, now_utc: datetime, min_interval_seconds: int) -> str:
    schedule = task.get("schedule") if isinstance(task.get("schedule"), dict) else {}
    expr = str(schedule.get("cron") or "").strip()
    if not expr:
        return ""
    zone = _safe_zone(str(schedule.get("timezone") or "UTC"))
    now_local = now_utc.astimezone(zone)
    if not croniter.is_valid(expr):
        return ""
    previous = croniter(expr, now_local).get_prev(datetime)
    previous_utc = previous.astimezone(timezone.utc)
    previous_previous = croniter(expr, previous).get_prev(datetime).astimezone(timezone.utc)
    interval_seconds = (previous_utc - previous_previous).total_seconds()
    if interval_seconds and interval_seconds < max(1, int(min_interval_seconds or 1)):
        return ""
    return previous_utc.isoformat()


async def _already_queued_or_ran(storage: AsyncTaskStorage, *, task_id: str, due_slot: str) -> bool:
    for execution in await storage.list_executions(task_id=task_id, limit=50):
        source = execution.get("source") if isinstance(execution.get("source"), dict) else {}
        if source.get("due_slot") == due_slot and execution.get("trigger") == "scheduled":
            return True
    return False


async def _already_scheduled_once(storage: AsyncTaskStorage, *, task_id: str) -> bool:
    for execution in await storage.list_executions(task_id=task_id, limit=50):
        if execution.get("trigger") == "scheduled":
            return True
    return False


async def _disable_one_shot_task(storage: AsyncTaskStorage, *, task_id: str, due_slot: str) -> None:
    await storage.update_task(
        task_id=task_id,
        status="disabled",
        metadata_patch={
            "one_shot_completed_due_slot": due_slot,
            "one_shot_disabled_at": datetime.now(timezone.utc).isoformat(),
        },
        revision_mode="in_place",
    )


async def enqueue_due_tasks(entrypoint: Any) -> Dict[str, Any]:
    root = Path(_storage_root(entrypoint))
    tenant = str(getattr(entrypoint.config, "tenant", "") or getattr(entrypoint.settings, "TENANT", "") or "")
    project = str(getattr(entrypoint.config, "project", "") or getattr(entrypoint.settings, "PROJECT", "") or "")
    bundle_id = str(getattr(getattr(entrypoint.config, "ai_bundle_spec", None), "id", "") or "task-and-memo-app@1-0")
    redis = getattr(entrypoint, "redis", None)
    if redis is None:
        return {"ok": False, "error": {"code": "redis_unavailable", "message": "Redis is required to enqueue due tasks."}}

    max_due = int(entrypoint.bundle_prop("tasks.scheduler.max_due_tasks_per_tick", 10) or 10)
    min_interval_seconds = int(entrypoint.bundle_prop("tasks.scheduler.min_interval_seconds", 300) or 300)
    default_user_type = str(entrypoint.bundle_prop("tasks.scheduler.default_user_type", "registered") or "registered")
    now_utc = datetime.now(timezone.utc)
    stream = RedisBackgroundJobStream(redis, tenant=tenant, project=project)
    enqueued: List[Dict[str, Any]] = []
    skipped = 0

    for user_id in await list_task_user_ids(root):
        storage = AsyncTaskStorage(root, user_id=user_id)
        for task in await storage.list_tasks(status="enabled", limit=1000):
            task_id = str(task.get("id") or "").strip()
            if not task_id:
                skipped += 1
                continue
            due_slot = _task_due_slot(task, now_utc=now_utc, min_interval_seconds=min_interval_seconds)
            if not due_slot:
                skipped += 1
                continue
            recurring = _task_recurring(task)
            if not recurring and await _already_scheduled_once(storage, task_id=task_id):
                await _disable_one_shot_task(storage, task_id=task_id, due_slot=due_slot)
                skipped += 1
                continue
            if await _already_queued_or_ran(storage, task_id=task_id, due_slot=due_slot):
                if not recurring:
                    await _disable_one_shot_task(storage, task_id=task_id, due_slot=due_slot)
                skipped += 1
                continue

            conversation_id = f"task_job_{uuid.uuid4().hex}"
            source = {
                "surface": "scheduler",
                "operation": "scheduled_task_due_scan",
                "due_slot": due_slot,
                "cron": (task.get("schedule") or {}).get("cron") or "",
            }
            execution = await storage.create_execution(
                task_id=task_id,
                status="queued",
                trigger="scheduled",
                source=source,
                conversation_id=conversation_id,
                summary="Queued for scheduled execution.",
                metadata={"agent_surface": "task_job", "due_slot": due_slot},
            )
            turn_id = f"turn_{execution['id']}"
            result = await stream.enqueue(
                work_kind=WORK_KIND_TASK_EXECUTION_DUE,
                bundle_id=bundle_id,
                user_id=str(task.get("owner_user_id") or user_id),
                user_type=default_user_type,
                queue=default_user_type,
                job_id=f"job_{execution['id']}",
                dedupe_key=f"{bundle_id}:{user_id}:{task_id}:{due_slot}",
                source=source,
                metadata={
                    "conversation_id": conversation_id,
                    "turn_id": turn_id,
                    "text": f"Run scheduled task: {task.get('title') or task_id}",
                },
                payload={
                    "task_id": task_id,
                    "execution_id": execution["id"],
                    "due_slot": due_slot,
                },
            )
            if result.enqueued:
                if not recurring:
                    await _disable_one_shot_task(storage, task_id=task_id, due_slot=due_slot)
                enqueued.append(
                    {
                        "task_id": task_id,
                        "execution_id": execution["id"],
                        "job_id": result.job_id,
                        "stream_id": result.stream_id,
                        "due_slot": due_slot,
                    }
                )
            else:
                await storage.update_execution(
                    execution_id=execution["id"],
                    task_id=task_id,
                    status="cancelled",
                    summary=f"Duplicate scheduled job was not enqueued ({result.reason}).",
                )
                skipped += 1
            if len(enqueued) >= max(1, max_due):
                return {"ok": True, "enqueued": enqueued, "skipped": skipped, "limit_reached": True}

    return {"ok": True, "enqueued": enqueued, "skipped": skipped, "limit_reached": False}


async def handle_job(entrypoint: Any, *, job: Dict[str, Any]) -> Dict[str, Any]:
    task_operations = _operations()

    work_kind = str(job.get("work_kind") or "").strip()
    if work_kind not in {WORK_KIND_TASK_EXECUTION_DUE, task_operations.WORK_KIND_TASK_RUN_NOW}:
        return {
            "ok": False,
            "error": {"code": "unsupported_job", "message": f"Unsupported job kind {work_kind!r}."},
        }
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        return {"ok": False, "error": {"code": "missing_task_id", "message": "Job payload must include task_id."}}
    return await task_operations.run_task_execution(
        entrypoint,
        task_id=task_id,
        trigger="scheduled" if work_kind == WORK_KIND_TASK_EXECUTION_DUE else "manual",
        source=job.get("source") if isinstance(job.get("source"), dict) else {},
        execution_id=str(payload.get("execution_id") or ""),
        run_conversation_id=str(metadata.get("conversation_id") or ""),
        user_id=str(job.get("user_id") or ""),
    )
