from __future__ import annotations

import uuid
from datetime import datetime, timezone
import inspect
from pathlib import Path
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

from croniter import croniter

from kdcube_ai_app.infra.jobs.stream import RedisBackgroundJobStream
from kdcube_ai_app.apps.chat.sdk.identity_authority import normalize_execution_authority
from .async_storage import AsyncAutomationStorage, list_automation_user_ids


WORK_KIND_AUTOMATION_EXECUTION_DUE = "automation.execution.due"

_storage_root_or_error = None
_automation_operations = None
_scheduler_identity_resolver = None


def configure_due_automations(
    *,
    storage_root_or_error: Any,
    automation_operations_module: Any,
    scheduler_identity_resolver: Any = None,
) -> None:
    """Bind bundle-specific storage and automation operation hooks."""
    global _storage_root_or_error, _automation_operations, _scheduler_identity_resolver
    _storage_root_or_error = storage_root_or_error
    _automation_operations = automation_operations_module
    _scheduler_identity_resolver = scheduler_identity_resolver


def _storage_root(entrypoint: Any) -> str:
    for method_name in ("automation_storage_root", "storage_root_or_error"):
        resolver = getattr(entrypoint, method_name, None)
        if callable(resolver):
            return str(resolver())
    if not callable(_storage_root_or_error):
        raise RuntimeError("automation due scheduler is not configured: storage_root_or_error is missing")
    return str(_storage_root_or_error(entrypoint))


def _operations() -> Any:
    if _automation_operations is not None:
        return _automation_operations
    from . import operations

    return operations


def _safe_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or "").strip() or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _automation_recurring(automation: Dict[str, Any]) -> bool:
    schedule = automation.get("schedule") if isinstance(automation.get("schedule"), dict) else {}
    value = schedule.get("recurring")
    if value is None:
        return True
    if type(value) is bool:
        return value
    raise ValueError("schedule.recurring must be a boolean")


def _automation_due_slot(automation: Dict[str, Any], *, now_utc: datetime, min_interval_seconds: int) -> str:
    schedule = automation.get("schedule") if isinstance(automation.get("schedule"), dict) else {}
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


async def _already_queued_or_ran(storage: AsyncAutomationStorage, *, automation_id: str, due_slot: str) -> bool:
    for execution in await storage.list_executions(automation_id=automation_id, limit=50):
        source = execution.get("source") if isinstance(execution.get("source"), dict) else {}
        if source.get("due_slot") == due_slot and execution.get("trigger") == "scheduled":
            return True
    return False


async def _already_scheduled_once(storage: AsyncAutomationStorage, *, automation_id: str) -> bool:
    for execution in await storage.list_executions(automation_id=automation_id, limit=50):
        if execution.get("trigger") == "scheduled":
            return True
    return False


async def _disable_one_shot_automation(storage: AsyncAutomationStorage, *, automation_id: str, due_slot: str) -> None:
    await storage.update_automation(
        automation_id=automation_id,
        status="disabled",
        metadata_patch={
            "one_shot_completed_due_slot": due_slot,
            "one_shot_disabled_at": datetime.now(timezone.utc).isoformat(),
        },
        revision_mode="in_place",
    )


def _normalize_queue_label(value: Any, default: str = "registered") -> str:
    raw = getattr(value, "value", value)
    queue_label = str(raw or default or "registered").strip().lower() or "registered"
    if queue_label == "admin":
        return "privileged"
    if queue_label in {"anonymous", "registered", "paid", "privileged"}:
        return queue_label
    return str(default or "registered").strip().lower() or "registered"


def _scheduler_identity_from_result(
    resolved: Any,
    *,
    owner_user_id: str,
    default_queue_label: str,
) -> Tuple[str, Dict[str, Any]]:
    if not isinstance(resolved, dict):
        queue_label = _normalize_queue_label(resolved, default_queue_label)
        return queue_label, {"actor_user_id": owner_user_id, "storage_user_id": owner_user_id}

    queue_label = _normalize_queue_label(
        resolved.get("queue_label"),
        default_queue_label,
    )
    identity = {
        key: value
        for key, value in resolved.items()
        if value is not None and value != ""
    }
    identity.setdefault("actor_user_id", owner_user_id)
    identity.setdefault("storage_user_id", owner_user_id)
    if identity.get("platform_user_id") and not identity.get("economics_user_id"):
        identity["economics_user_id"] = identity["platform_user_id"]
    return queue_label, identity


async def enqueue_due_automations(entrypoint: Any) -> Dict[str, Any]:
    root = Path(_storage_root(entrypoint))
    tenant = str(getattr(entrypoint.config, "tenant", "") or getattr(entrypoint.settings, "TENANT", "") or "")
    project = str(getattr(entrypoint.config, "project", "") or getattr(entrypoint.settings, "PROJECT", "") or "")
    bundle_id = str(getattr(getattr(entrypoint.config, "ai_bundle_spec", None), "id", "") or "task-and-memo-app@1-0")
    redis = getattr(entrypoint, "redis", None)
    if redis is None:
        return {"ok": False, "error": {"code": "redis_unavailable", "message": "Redis is required to enqueue due automations."}}

    max_due = int(entrypoint.bundle_prop("automations.scheduler.max_due_automations_per_tick", 10) or 10)
    min_interval_seconds = int(entrypoint.bundle_prop("automations.scheduler.min_interval_seconds", 300) or 300)
    default_queue_label = str(entrypoint.bundle_prop("automations.scheduler.default_queue_label", "registered") or "registered")
    now_utc = datetime.now(timezone.utc)
    stream = RedisBackgroundJobStream(redis, tenant=tenant, project=project)
    enqueued: List[Dict[str, Any]] = []
    skipped = 0

    for user_id in await list_automation_user_ids(root):
        storage = AsyncAutomationStorage(root, user_id=user_id)
        for automation in await storage.list_automations(status="enabled", limit=1000):
            automation_id = str(automation.get("id") or "").strip()
            if not automation_id:
                skipped += 1
                continue
            due_slot = _automation_due_slot(automation, now_utc=now_utc, min_interval_seconds=min_interval_seconds)
            if not due_slot:
                skipped += 1
                continue
            recurring = _automation_recurring(automation)
            if not recurring and await _already_scheduled_once(storage, automation_id=automation_id):
                await _disable_one_shot_automation(storage, automation_id=automation_id, due_slot=due_slot)
                skipped += 1
                continue
            if await _already_queued_or_ran(storage, automation_id=automation_id, due_slot=due_slot):
                if not recurring:
                    await _disable_one_shot_automation(storage, automation_id=automation_id, due_slot=due_slot)
                skipped += 1
                continue

            conversation_id = f"automation_job_{uuid.uuid4().hex}"
            owner_user_id = str(automation.get("owner_user_id") or user_id)
            queue_label = default_queue_label
            identity_context: Dict[str, Any] = {
                "actor_user_id": owner_user_id,
                "storage_user_id": owner_user_id,
            }
            if callable(_scheduler_identity_resolver):
                resolved = _scheduler_identity_resolver(
                    entrypoint,
                    user_id=owner_user_id,
                    automation=automation,
                    default_queue_label=default_queue_label,
                )
                if inspect.isawaitable(resolved):
                    resolved = await resolved
                queue_label, identity_context = _scheduler_identity_from_result(
                    resolved,
                    owner_user_id=owner_user_id,
                    default_queue_label=default_queue_label,
                )
            else:
                queue_label = _normalize_queue_label(queue_label, default_queue_label)
            source = {
                "surface": "scheduler",
                "operation": "scheduled_automation_due_scan",
                "due_slot": due_slot,
                "cron": (automation.get("schedule") or {}).get("cron") or "",
                "queue_label": queue_label,
                **identity_context,
            }
            source["identity_authority"] = normalize_execution_authority(
                identity_context,
                actor_user_id=owner_user_id,
                economics_user_id=str(identity_context.get("economics_user_id") or ""),
            )
            execution = await storage.create_execution(
                automation_id=automation_id,
                status="queued",
                trigger="scheduled",
                source=source,
                conversation_id=conversation_id,
                summary="Queued for scheduled execution.",
                metadata={"agent_surface": "automation_job", "due_slot": due_slot},
            )
            turn_id = f"turn_{execution['id']}"
            result = await stream.enqueue(
                work_kind=WORK_KIND_AUTOMATION_EXECUTION_DUE,
                bundle_id=bundle_id,
                user_id=owner_user_id,
                queue_label=queue_label,
                job_id=f"job_{execution['id']}",
                dedupe_key=f"{bundle_id}:{user_id}:{automation_id}:{due_slot}",
                source=source,
                identity_authority=source.get("identity_authority") if isinstance(source.get("identity_authority"), dict) else {},
                metadata={
                    "conversation_id": conversation_id,
                    "turn_id": turn_id,
                    "text": f"Run scheduled automation: {automation.get('title') or automation_id}",
                },
                payload={
                    "automation_id": automation_id,
                    "execution_id": execution["id"],
                    "due_slot": due_slot,
                },
            )
            if result.enqueued:
                if not recurring:
                    await _disable_one_shot_automation(storage, automation_id=automation_id, due_slot=due_slot)
                enqueued.append(
                    {
                        "automation_id": automation_id,
                        "execution_id": execution["id"],
                        "job_id": result.job_id,
                        "stream_id": result.stream_id,
                        "due_slot": due_slot,
                    }
                )
            else:
                await storage.update_execution(
                    execution_id=execution["id"],
                    automation_id=automation_id,
                    status="cancelled",
                    summary=f"Duplicate scheduled job was not enqueued ({result.reason}).",
                )
                skipped += 1
            if len(enqueued) >= max(1, max_due):
                return {"ok": True, "enqueued": enqueued, "skipped": skipped, "limit_reached": True}

    return {"ok": True, "enqueued": enqueued, "skipped": skipped, "limit_reached": False}


async def handle_job(entrypoint: Any, *, job: Dict[str, Any]) -> Dict[str, Any]:
    automation_operations = _operations()

    work_kind = str(job.get("work_kind") or "").strip()
    if work_kind not in {WORK_KIND_AUTOMATION_EXECUTION_DUE, automation_operations.WORK_KIND_AUTOMATION_RUN_NOW}:
        return {
            "ok": False,
            "error": {"code": "unsupported_job", "message": f"Unsupported job kind {work_kind!r}."},
        }
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    automation_id = str(payload.get("automation_id") or "").strip()
    if not automation_id:
        return {"ok": False, "error": {"code": "missing_automation_id", "message": "Job payload must include automation_id."}}
    return await automation_operations.run_automation_execution(
        entrypoint,
        automation_id=automation_id,
        trigger="scheduled" if work_kind == WORK_KIND_AUTOMATION_EXECUTION_DUE else "manual",
        source=job.get("source") if isinstance(job.get("source"), dict) else {},
        execution_id=str(payload.get("execution_id") or ""),
        run_conversation_id=str(metadata.get("conversation_id") or ""),
        user_id=str(job.get("user_id") or ""),
    )
