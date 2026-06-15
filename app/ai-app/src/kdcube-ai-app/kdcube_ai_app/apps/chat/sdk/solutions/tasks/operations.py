from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from contextlib import nullcontext
from typing import Any, Awaitable, Callable, Dict, Optional

from kdcube_ai_app.apps.chat.sdk.config import get_secret
from kdcube_ai_app.apps.chat.sdk.infra.bundle_urls import bundle_operation_url
from kdcube_ai_app.infra.jobs.stream import RedisBackgroundJobStream
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context
from kdcube_ai_app.apps.chat.sdk.runtime.http_ops import BundleBinaryResponse
from .async_storage import AsyncTaskStorage
from .execution_artifacts import (
    artifact_ref_for_execution_artifact,
    downloadable_execution_artifacts,
    execution_id_from_artifact_ref,
    read_execution_artifact_for_download,
)
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    TelegramMessage,
    TelegramUserAdminStorage,
    render_telegram_messages_from_timeline,
    send_telegram_messages,
)


logger = logging.getLogger(__name__)

BUNDLE_ID = ""
WORK_KIND_TASK_RUN_NOW = "task.execution.manual"

_storage_root_or_error = None
_target_user_id = None


def configure_task_operations(
    *,
    storage_root_or_error: Any,
    target_user_id: Any,
    bundle_id: str = "",
) -> None:
    """Bind bundle-specific storage and user scope resolution."""
    global BUNDLE_ID, _storage_root_or_error, _target_user_id
    BUNDLE_ID = str(bundle_id or "").strip()
    _storage_root_or_error = storage_root_or_error
    _target_user_id = target_user_id


def _storage_root(entrypoint: Any) -> str:
    for method_name in ("task_storage_root", "storage_root_or_error"):
        resolver = getattr(entrypoint, method_name, None)
        if callable(resolver):
            return str(resolver())
    if not callable(_storage_root_or_error):
        raise RuntimeError("task operations are not configured: storage_root_or_error is missing")
    return str(_storage_root_or_error(entrypoint))


def _target_user(entrypoint: Any, *, user_id: Optional[str] = None, fingerprint: Optional[str] = None) -> str:
    resolver = getattr(entrypoint, "target_task_user_id", None)
    if callable(resolver):
        return str(resolver(user_id=user_id, fingerprint=fingerprint))
    if callable(_target_user_id):
        return str(_target_user_id(entrypoint, user_id=user_id, fingerprint=fingerprint))
    value = str(user_id or fingerprint or "").strip()
    if value:
        return value
    comm_context = getattr(entrypoint, "comm_context", None)
    comm_user = getattr(comm_context, "user", None)
    return str(getattr(comm_user, "user_id", "") or "anonymous")


def storage_for(
    entrypoint: Any,
    *,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> tuple[AsyncTaskStorage, str]:
    target_user = _target_user(entrypoint, user_id=user_id, fingerprint=fingerprint)
    return AsyncTaskStorage(_storage_root(entrypoint), user_id=target_user), target_user


def _bundle_route_parts(entrypoint: Any) -> tuple[str, str, str]:
    settings = getattr(entrypoint, "settings", None)
    tenant = str(getattr(getattr(entrypoint, "config", None), "tenant", "") or getattr(settings, "TENANT", "") or "").strip()
    project = str(getattr(getattr(entrypoint, "config", None), "project", "") or getattr(settings, "PROJECT", "") or "").strip()
    bundle_id = str(
        getattr(getattr(getattr(entrypoint, "config", None), "ai_bundle_spec", None), "id", "")
        or BUNDLE_ID
    ).strip()
    return tenant, project, bundle_id


def _execution_scope(entrypoint: Any, *, user_id: str) -> Dict[str, Any]:
    tenant, project, _bundle_id = _bundle_route_parts(entrypoint)
    return {
        "tenant": tenant,
        "project": project,
        "user_id": user_id,
        # NOTE: user_type here scopes ARTIFACT storage paths (execution_artifacts),
        # not economics. It is intentionally left constant so artifact read/write
        # paths stay consistent across executions regardless of the funding role.
        # Economics uses an independently resolved role via _task_econ_subject.
        "user_type": "registered",
        "storage_root": _storage_root(entrypoint),
    }


# ---------------------------------------------------------------------------
# Economics enforcement (Option A: estimate + post-run settle, funding_flow parity)
# ---------------------------------------------------------------------------
def _task_economics_enabled(entrypoint: Any) -> bool:
    return bool(
        getattr(entrypoint, "cp_manager", None)
        and getattr(entrypoint, "rl", None)
        and getattr(entrypoint, "budget_limiter", None)
    )


def _task_reservation_usd(entrypoint: Any) -> float:
    """Feasibility estimate for a task pipeline (bundle-configurable).

    Tasks verify economic feasibility at the start (economic_preflight); this
    only sizes the estimate the preflight admits against. The task's actual cost
    is metered by the inner ReAct turn (self.run()), not reserved/settled here.
    """
    try:
        return float(_bundle_prop(entrypoint, "economics.task.reservation_amount_dollars", 0.50) or 0.50)
    except Exception:
        return 0.50


async def _task_econ_subject(entrypoint: Any, *, target_user: str, source: Dict[str, Any]):
    """Build an EconomicsSubject for a task execution.

    privileged/admin is preserved from the carried (enqueue-time) role; the
    carried role rides in the job source (enqueue_task_job persists user_type),
    falling back to the worker comm_context. paid/registered is re-resolved at
    run time from economics state via RoleResolver.
    """
    from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import (
        EconomicsSubject,
        RoleResolver,
    )

    tenant, project, _bundle_id = _bundle_route_parts(entrypoint)
    comm_context = getattr(entrypoint, "comm_context", None)
    comm_user = getattr(comm_context, "user", None)
    carried_role = str(
        (source or {}).get("user_type")
        or getattr(comm_user, "user_type", "")
        or "registered"
    ).strip() or "registered"
    role = carried_role
    try:
        pg_pool = getattr(entrypoint, "pg_pool", None)
        if pg_pool is not None and tenant and project and target_user:
            resolver = RoleResolver(pg_pool=pg_pool, tenant=tenant, project=project)
            role = await resolver.resolve(user_id=target_user, carried_role=carried_role)
    except Exception as exc:
        logger.warning(
            "[tasks.economics] role resolve failed; using carried role: user=%s carried=%s err=%s",
            target_user, carried_role, exc,
        )
        role = carried_role
    timezone = str((source or {}).get("timezone") or getattr(comm_user, "timezone", "") or "") or None
    return EconomicsSubject(
        tenant=tenant, project=project, user_id=target_user,
        user_type=role, timezone=timezone,
    )


async def _task_verify_economics(
    entrypoint: Any,
    *,
    target_user: str,
    source: Dict[str, Any],
):
    """Verify the task pipeline is economically feasible for the carried user.

    Returns (subject, decision) on success; raises EconomicsLimitException when
    the user's quota/funding cannot support the pipeline. Returns (None, None)
    when economics is not configured (task runs unmetered, as before).

    Verify-only (economic_preflight): NO reservation, NO settlement. The task's
    ReAct work routes through self.run(), which already reserves+settles the real
    cost under the task's user identity; an outer guard would only duplicate it.
    This gates the START of the flow and surfaces the economic limits for logging.
    """
    if not _task_economics_enabled(entrypoint):
        return None, None
    from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import (
        economic_preflight,
        EconomicsEstimate,
        FlowPolicy,
    )

    subject = await _task_econ_subject(entrypoint, target_user=target_user, source=source)
    if not (subject.tenant and subject.project and subject.user_id):
        return None, None
    decision = await economic_preflight(
        entrypoint,
        subject=subject,
        estimate=EconomicsEstimate(reservation_usd=_task_reservation_usd(entrypoint)),
        flow="tasks",
        policy=FlowPolicy(enforce_concurrency=False, emit_user_events=False),
    )
    return subject, decision


def _task_economics_metadata(decision) -> Dict[str, Any]:
    """Compact economic decision + limits for the execution journal/metadata."""
    if decision is None:
        return {}
    admit = getattr(decision, "admit", None)
    limits = (getattr(admit, "snapshot", None) or {}) if admit is not None else {}
    return {
        "economics": {
            "verified": True,
            "lane": getattr(decision, "lane", None),
            "plan_id": getattr(decision, "plan_id", None),
            "funding_source": getattr(decision, "funding_source", None),
            "est_turn_usd": getattr(decision, "est_turn_usd", None),
            "limits": limits,
        }
    }


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


async def _download_token_secret(entrypoint: Any) -> bytes:
    _tenant, _project, bundle_id = _bundle_route_parts(entrypoint)
    secret = str(
        _bundle_prop(entrypoint, "integrations.telegram.artifact_download_secret", "")
        or await get_secret("b:integrations.telegram.artifact_download_secret")
        or await get_secret(f"bundles.{bundle_id}.secrets.integrations.telegram.artifact_download_secret")
        or await _telegram_bot_token()
        or ""
    ).strip()
    return secret.encode("utf-8")


def _download_token_ttl(entrypoint: Any) -> int:
    try:
        ttl = int(_bundle_prop(entrypoint, "integrations.telegram.artifact_download_token_ttl_seconds", 900) or 900)
    except Exception:
        ttl = 900
    return max(60, min(ttl, 86400))


async def _make_download_token(
    entrypoint: Any,
    *,
    artifact_ref: str,
    user_id: str,
) -> tuple[str, int] | None:
    secret = await _download_token_secret(entrypoint)
    if not secret:
        return None
    expires_at = int(time.time()) + _download_token_ttl(entrypoint)
    payload = {
        "v": 1,
        "artifact_ref": str(artifact_ref or "").strip(),
        "user_id": str(user_id or "").strip(),
        "exp": expires_at,
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64url_encode(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}", expires_at


async def _verify_download_token(entrypoint: Any, *, artifact_ref: str, download_token: str) -> Dict[str, Any]:
    secret = await _download_token_secret(entrypoint)
    if not secret:
        raise ValueError("artifact download token signing secret is not configured")
    try:
        body, sig = str(download_token or "").strip().split(".", 1)
    except ValueError:
        raise ValueError("artifact download token is malformed")
    expected = _b64url_encode(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise ValueError("artifact download token signature is invalid")
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except Exception as exc:
        raise ValueError("artifact download token payload is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("artifact download token payload is invalid")
    if str(payload.get("artifact_ref") or "") != str(artifact_ref or ""):
        raise ValueError("artifact download token does not match artifact_ref")
    try:
        expires_at = int(payload.get("exp") or 0)
    except Exception:
        expires_at = 0
    if expires_at < int(time.time()):
        raise ValueError("artifact download token is expired")
    user_id = str(payload.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("artifact download token does not include a user scope")
    return payload


def _artifact_download_url(entrypoint: Any, *, artifact_ref: str, public: bool, download_token: str = "") -> str:
    tenant, project, bundle_id = _bundle_route_parts(entrypoint)
    route = "public" if public else "operations"
    alias = "telegram_task_execution_artifact_download" if public else "task_execution_artifact_download"
    query_payload = {"artifact_ref": artifact_ref}
    if download_token:
        query_payload["download_token"] = download_token
    return bundle_operation_url(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        route=route,
        operation=alias,
        query=query_payload,
        strict=True,
    )


async def _decorate_execution_artifacts(
    entrypoint: Any,
    execution: Dict[str, Any] | None,
    *,
    user_id: str,
    public: bool = False,
) -> Dict[str, Any] | None:
    if not execution:
        return execution
    enriched = dict(execution)
    sc = _execution_scope(entrypoint, user_id=user_id)
    try:
        artifacts = await downloadable_execution_artifacts(enriched, sc=sc)
    except Exception:
        artifacts = []
    out: list[Dict[str, Any]] = []
    for index, artifact in enumerate(artifacts):
        item = dict(artifact)
        artifact_ref = str(item.get("artifact_ref") or "").strip() or artifact_ref_for_execution_artifact(enriched, item, index=index)
        signed = await _make_download_token(entrypoint, artifact_ref=artifact_ref, user_id=user_id) if public else None
        download_token = signed[0] if signed else ""
        download_url = _artifact_download_url(entrypoint, artifact_ref=artifact_ref, public=public, download_token=download_token)
        item["artifact_ref"] = artifact_ref
        item["download_url"] = download_url
        auth_kind = "telegram_download_token" if public and download_token else ("telegram_init_data_header" if public else "kdcube_platform_auth")
        item["download"] = {
            "method": "GET",
            "url": download_url,
            "filename": item.get("filename") or "",
            "mime_type": item.get("mime_type") or item.get("mime") or "",
            "auth": auth_kind,
            "headers": [] if auth_kind == "telegram_download_token" else (["X-Telegram-Init-Data"] if public else []),
        }
        if signed:
            item["download"]["expires_at"] = signed[1]
        out.append(item)
    enriched["artifacts"] = out
    enriched["artifact_count"] = len(out)
    return enriched


async def _decorate_executions(
    entrypoint: Any,
    executions: list[Dict[str, Any]],
    *,
    user_id: str,
    public: bool = False,
) -> list[Dict[str, Any]]:
    out = []
    for execution in executions:
        decorated = await _decorate_execution_artifacts(entrypoint, execution, user_id=user_id, public=public)
        if decorated:
            out.append(decorated)
    return out


async def list_tasks(
    entrypoint: Any,
    *,
    query: str = "",
    status: str = "",
    limit: int = 50,
    execution_limit: int = 3,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    public: bool = False,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    tasks = await storage.list_tasks(query=query, status=status, limit=limit)
    tasks = await storage.attach_execution_history(tasks, execution_limit=execution_limit)
    for task in tasks:
        if isinstance(task.get("executions"), list):
            task["executions"] = await _decorate_executions(entrypoint, task["executions"], user_id=target_user, public=public)
        if isinstance(task.get("last_execution"), dict):
            task["last_execution"] = await _decorate_execution_artifacts(entrypoint, task["last_execution"], user_id=target_user, public=public)
    return {"ok": True, "user_id": target_user, "count": len(tasks), "tasks": tasks}


async def search_tasks(
    entrypoint: Any,
    *,
    query: str = "",
    status: str = "",
    limit: int = 20,
    execution_limit: int = 3,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    public: bool = False,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    tasks = await storage.search_tasks(query=query, status=status, limit=limit)
    tasks = await storage.attach_execution_history(tasks, execution_limit=execution_limit)
    for task in tasks:
        if isinstance(task.get("executions"), list):
            task["executions"] = await _decorate_executions(entrypoint, task["executions"], user_id=target_user, public=public)
        if isinstance(task.get("last_execution"), dict):
            task["last_execution"] = await _decorate_execution_artifacts(entrypoint, task["last_execution"], user_id=target_user, public=public)
    index_path = await storage.ensure_search_index()
    return {
        "ok": True,
        "user_id": target_user,
        "count": len(tasks),
        "index_path": str(index_path),
        "tasks": tasks,
    }


async def get_task(
    entrypoint: Any,
    *,
    task_id: str,
    execution_limit: int = 10,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    public: bool = False,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    task = await storage.get_task(task_id)
    if task:
        task = (await storage.attach_execution_history([task], execution_limit=execution_limit))[0]
        if isinstance(task.get("executions"), list):
            task["executions"] = await _decorate_executions(entrypoint, task["executions"], user_id=target_user, public=public)
        if isinstance(task.get("last_execution"), dict):
            task["last_execution"] = await _decorate_execution_artifacts(entrypoint, task["last_execution"], user_id=target_user, public=public)
    return {
        "ok": task is not None,
        "user_id": target_user,
        "task": task,
        "error": None if task else {"code": "task_not_found", "message": f"Task {task_id!r} was not found."},
    }


async def create_task(
    entrypoint: Any,
    *,
    title: str,
    description: str = "",
    schedule_cron: str = "",
    timezone: str = "UTC",
    recurring: bool = True,
    labels: str = "",
    source: str = "widget",
    conversation_id: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    task = await storage.create_task(
        title=title,
        description=description,
        schedule_cron=schedule_cron,
        timezone_name=timezone,
        recurring=recurring,
        labels=labels,
        source=source,
        conversation_id=conversation_id or None,
    )
    return {"ok": True, "user_id": target_user, "task": task}


async def update_task(
    entrypoint: Any,
    *,
    task_id: str,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    schedule_cron: str | None = None,
    timezone: str | None = None,
    recurring: bool | None = None,
    labels: str | None = None,
    conversation_id: str | None = None,
    execution_conversation_id: str | None = None,
    metadata_patch: Dict[str, Any] | None = None,
    context_patch: Dict[str, Any] | None = None,
    relations_patch: Dict[str, Any] | None = None,
    revision_mode: str = "auto",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    task = await storage.update_task(
        task_id=task_id,
        title=title,
        description=description,
        status=status,
        schedule_cron=schedule_cron,
        timezone_name=timezone,
        recurring=recurring,
        labels=labels,
        conversation_id=conversation_id,
        execution_conversation_id=execution_conversation_id,
        metadata_patch=metadata_patch,
        context_patch=context_patch,
        relations_patch=relations_patch,
        revision_mode=revision_mode,
    )
    return {"ok": True, "user_id": target_user, "task": task}


async def delete_task(
    entrypoint: Any,
    *,
    task_id: str,
    hard: bool = False,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    task = await storage.delete_task(task_id=task_id, hard=hard)
    return {"ok": task is not None, "user_id": target_user, "deleted": task is not None, "task": task}


async def list_executions(
    entrypoint: Any,
    *,
    task_id: str = "",
    status: str = "",
    limit: int = 50,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    public: bool = False,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    executions = await storage.list_executions(task_id=task_id, status=status, limit=limit)
    executions = await _decorate_executions(entrypoint, executions, user_id=target_user, public=public)
    return {"ok": True, "user_id": target_user, "count": len(executions), "executions": executions}


async def search_executions(
    entrypoint: Any,
    *,
    query: str = "",
    task_id: str = "",
    status: str = "",
    limit: int = 50,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    public: bool = False,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    executions = await storage.search_executions(query=query, task_id=task_id, status=status, limit=limit)
    executions = await _decorate_executions(entrypoint, executions, user_id=target_user, public=public)
    index_path = await storage.ensure_execution_search_index()
    return {
        "ok": True,
        "user_id": target_user,
        "count": len(executions),
        "index_path": str(index_path),
        "executions": executions,
    }


async def get_execution(
    entrypoint: Any,
    *,
    execution_id: str,
    task_id: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    public: bool = False,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    execution = await storage.get_execution(task_id=task_id, execution_id=execution_id)
    execution = await _decorate_execution_artifacts(entrypoint, execution, user_id=target_user, public=public)
    return {
        "ok": execution is not None,
        "user_id": target_user,
        "execution": execution,
        "error": None if execution else {"code": "execution_not_found", "message": f"Execution {execution_id!r} was not found."},
    }


async def download_execution_artifact(
    entrypoint: Any,
    *,
    artifact_ref: str,
    execution_id: str = "",
    task_id: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    download_token: str = "",
) -> BundleBinaryResponse | Dict[str, Any]:
    if download_token:
        try:
            token_payload = await _verify_download_token(entrypoint, artifact_ref=artifact_ref, download_token=download_token)
        except Exception as exc:
            return {
                "ok": False,
                "error": {"code": "execution_artifact_download_token_invalid", "message": str(exc)},
            }
        storage, target_user = storage_for(entrypoint, user_id=str(token_payload.get("user_id") or ""), fingerprint=None)
    else:
        storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    selected_execution_id = str(execution_id or "").strip()
    if not selected_execution_id:
        selected_execution_id = execution_id_from_artifact_ref(artifact_ref)
    execution = await storage.get_execution(task_id=task_id, execution_id=selected_execution_id)
    if not execution:
        return {
            "ok": False,
            "error": {"code": "execution_not_found", "message": f"Execution {selected_execution_id!r} was not found."},
        }
    try:
        payload = await read_execution_artifact_for_download(
            artifact_ref=artifact_ref,
            execution=execution,
            sc=_execution_scope(entrypoint, user_id=target_user),
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": {"code": "execution_artifact_not_found", "message": str(exc)},
        }
    return BundleBinaryResponse(
        content=payload["content"],
        filename=payload.get("filename") or "artifact.bin",
        media_type=payload.get("mime_type") or "application/octet-stream",
    )


async def upsert_execution(
    entrypoint: Any,
    *,
    task_id: str,
    execution_id: str = "",
    status: str = "",
    trigger: str = "agent",
    conversation_id: str = "",
    turn_id: str = "",
    summary: str = "",
    result: Dict[str, Any] | None = None,
    error: str = "",
    log_excerpt: str = "",
    artifacts: list[Dict[str, Any]] | None = None,
    metadata: Dict[str, Any] | None = None,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    if execution_id:
        execution = await storage.update_execution(
            execution_id=execution_id,
            task_id=task_id,
            status=status or None,
            conversation_id=conversation_id,
            turn_id=turn_id,
            summary=summary,
            result=result,
            error=error,
            log_excerpt=log_excerpt,
            artifacts=artifacts,
            metadata_patch=metadata,
        )
    else:
        execution = await storage.create_execution(
            task_id=task_id,
            status=status or "queued",
            trigger=trigger,
            conversation_id=conversation_id,
            turn_id=turn_id,
            summary=summary,
            result=result,
            error=error,
            log_excerpt=log_excerpt,
            artifacts=artifacts,
            metadata=metadata,
        )
    return {"ok": True, "user_id": target_user, "execution": execution}


async def delete_execution(
    entrypoint: Any,
    *,
    execution_id: str,
    task_id: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    execution = await storage.delete_execution(execution_id=execution_id, task_id=task_id)
    return {"ok": execution is not None, "user_id": target_user, "deleted": execution is not None, "execution": execution}


def _run_prompt(*, task: Dict[str, Any], execution: Dict[str, Any], trigger: str) -> str:
    return "\n".join(
        [
            "Run this saved task as a fresh job execution.",
            "",
            f"Task id: {task.get('id')}",
            f"Execution id: {execution.get('id')}",
            f"Execution trigger: {trigger}",
            f"Title: {task.get('title')}",
            "",
            "Instructions:",
            str(task.get("body") or task.get("description") or "").strip(),
            "",
            "Use task_job.get_current_task if you need to inspect the task or linked task definitions.",
            "Use job_memory.search_memo only when durable user context materially changes this execution.",
            "Use email.process_user_emails for email-processing tasks; pass the concrete connected email address as account, the specific mailbox rule as instruction, and a bounded search_query when the task has a date/topic/sender/label constraint.",
            "If email.process_user_emails returns email_processor_failed in a saved task, retry the same email tool call if rounds remain; otherwise record a task failure. Do not treat it as zero new emails or process web/search fallback.",
            "Use delivery.send_report when the task explicitly asks to deliver the generated report by email, Telegram, or both; pass generated artifact physical paths as attachments.",
            "Do not invent or pass task id, execution id, or task definition to email tools; those are injected from the bundle call context.",
            "Never ask for email passwords or raw credentials.",
            "Use task_job.update_execution_journal for substantial progress, errors, result data, and produced artifacts.",
            "Call task_job tools directly as ReAct tool calls, not from inside exec_tools.execute_code_python code.",
            "At the end, call task_job.update_execution_journal directly with the final status and then summarize the outcome briefly.",
        ]
    ).strip()


def _result_answer(result: Any) -> str:
    if isinstance(result, dict):
        return str(
            result.get("final_answer")
            or result.get("answer")
            or result.get("summary")
            or ""
        ).strip()
    return str(result or "").strip()


def _result_status(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    status = str(result.get("execution_status") or result.get("status") or "").strip().lower()
    return status if status in {"success", "failed", "cancelled"} else ""


def _build_task_scoped_context(
    entrypoint: Any,
    *,
    target_user: str,
    run_conversation_id: str,
    turn_id: str,
    bundle_call_context: Dict[str, Any],
    resolved_user_type: Optional[str] = None,
):
    comm_context = getattr(entrypoint, "comm_context", None)
    if comm_context is None or not hasattr(comm_context, "model_copy"):
        raise RuntimeError("task execution requires a request context with comm_context")

    scoped_ctx = comm_context.model_copy(deep=True)
    scoped_ctx.routing.session_id = run_conversation_id
    scoped_ctx.routing.conversation_id = run_conversation_id
    scoped_ctx.routing.turn_id = turn_id
    scoped_ctx.user.user_id = target_user
    # Carry the economics-resolved role so the inner run() applies the correct
    # plan/limits + funding for this user (the worker comm_context may carry a
    # stale/default role for scheduled tasks).
    if resolved_user_type:
        scoped_ctx.user.user_type = resolved_user_type
    scoped_ctx.bundle_call_context = bundle_call_context
    return scoped_ctx


async def _run_with_entrypoint_request_context(
    entrypoint: Any,
    scoped_ctx: Any,
    runner: Callable[[], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    binder = getattr(entrypoint, "bind_request_context", None)
    if callable(binder):
        with binder(comm_context=scoped_ctx):
            with bind_current_request_context(scoped_ctx, comm=getattr(entrypoint, "comm", None)):
                return await runner()

    entrypoint.rebind_request_context(comm_context=scoped_ctx)
    with bind_current_request_context(scoped_ctx, comm=getattr(entrypoint, "comm", None)):
        return await runner()


async def _run_default_react_task_job(
    entrypoint: Any,
    *,
    task: Dict[str, Any],
    execution: Dict[str, Any],
    trigger: str,
    target_user: str,
    run_conversation_id: str,
    turn_id: str,
    bundle_call_context: Dict[str, Any],
    resolved_user_type: Optional[str] = None,
) -> Dict[str, Any]:
    scoped_ctx = _build_task_scoped_context(
        entrypoint,
        target_user=target_user,
        run_conversation_id=run_conversation_id,
        turn_id=turn_id,
        bundle_call_context=bundle_call_context,
        resolved_user_type=resolved_user_type,
    )

    async def _run_scoped() -> Dict[str, Any]:
        state = entrypoint.create_initial_state(
            {
                "request_id": getattr(scoped_ctx.request, "request_id", "") or str(uuid.uuid4()),
                "tenant": scoped_ctx.actor.tenant_id,
                "project": scoped_ctx.actor.project_id,
                "user": target_user,
                "user_type": scoped_ctx.user.user_type,
                "session_id": run_conversation_id,
                "conversation_id": run_conversation_id,
                "turn_id": turn_id,
                "text": _run_prompt(task=task, execution=execution, trigger=trigger),
                "attachments": [],
            }
        )
        state["turn_id"] = turn_id
        state["agent_surface"] = "task_job"
        state["task_execution"] = {
            "task_id": task.get("id"),
            "execution_id": execution["id"],
            "trigger": trigger,
            "conversation_id": run_conversation_id,
            "turn_id": turn_id,
            "task_definition": bundle_call_context["task_definition"],
        }
        run_task_job_turn = getattr(entrypoint, "run_task_job_turn", None)
        if not callable(run_task_job_turn):
            raise RuntimeError(
                "Task execution requires entrypoint.execute_task_job(...) or entrypoint.run_task_job_turn(...)."
            )
        result = await run_task_job_turn(state=state)
        return result if isinstance(result, dict) else {"final_answer": str(result or "")}

    return await _run_with_entrypoint_request_context(entrypoint, scoped_ctx, _run_scoped)


async def _execute_task_job(
    entrypoint: Any,
    *,
    task: Dict[str, Any],
    execution: Dict[str, Any],
    storage: AsyncTaskStorage,
    target_user: str,
    trigger: str,
    source: Dict[str, Any],
    run_conversation_id: str,
    turn_id: str,
    bundle_call_context: Dict[str, Any],
    resolved_user_type: Optional[str] = None,
) -> Dict[str, Any]:
    executor = getattr(entrypoint, "execute_task_job", None)
    if callable(executor):
        async def _run_custom() -> Dict[str, Any]:
            result = await executor(
                task=task,
                execution=execution,
                storage=storage,
                user_id=target_user,
                trigger=trigger,
                source=source,
                conversation_id=run_conversation_id,
                turn_id=turn_id,
                bundle_call_context=bundle_call_context,
            )
            return result if isinstance(result, dict) else {"answer": str(result or "")}

        try:
            scoped_ctx = _build_task_scoped_context(
                entrypoint,
                target_user=target_user,
                run_conversation_id=run_conversation_id,
                turn_id=turn_id,
                bundle_call_context=bundle_call_context,
                resolved_user_type=resolved_user_type,
            )
        except RuntimeError:
            return await _run_custom()

        return await _run_with_entrypoint_request_context(entrypoint, scoped_ctx, _run_custom)

    return await _run_default_react_task_job(
        entrypoint,
        task=task,
        execution=execution,
        trigger=trigger,
        target_user=target_user,
        run_conversation_id=run_conversation_id,
        turn_id=turn_id,
        bundle_call_context=bundle_call_context,
        resolved_user_type=resolved_user_type,
    )


async def _telegram_bot_token() -> str:
    bundle_id = BUNDLE_ID or "task-and-memo-app@1-0"
    return (
        await get_secret("b:integrations.telegram.bot_token")
        or await get_secret(f"bundles.{bundle_id}.secrets.integrations.telegram.bot_token")
        or ""
    )


def _bundle_prop(entrypoint: Any, path: str, default: Any = None) -> Any:
    getter = getattr(entrypoint, "bundle_prop", None)
    if callable(getter):
        return getter(path, default)
    return default


def _telegram_recipient_for_user(entrypoint: Any, *, user_id: str) -> Dict[str, Any] | None:
    target = str(user_id or "").strip()
    if not target:
        return None
    registry = TelegramUserAdminStorage(_storage_root(entrypoint))
    for row in registry.list_users():
        role = str(row.get("role") or "").strip().lower()
        if role not in {"registered", "admin"}:
            continue
        telegram_user_id = str(row.get("telegram_user_id") or "").strip()
        kdcube_user_id = str(row.get("kdcube_user_id") or "").strip()
        bundle_user_scope = kdcube_user_id or (f"telegram_{telegram_user_id}" if telegram_user_id else "")
        if bundle_user_scope != target:
            continue
        chat_id = str(row.get("telegram_chat_id") or telegram_user_id or "").strip()
        if not chat_id:
            return None
        return {
            "chat_id": chat_id,
            "telegram_user_id": telegram_user_id,
            "role": role,
            "conversation_id": str(row.get("conversation_id") or "").strip(),
        }
    return None


def _telegram_delivery_kind_for_artifact(artifact: Dict[str, Any]) -> str:
    mime_type = str(artifact.get("mime_type") or artifact.get("mime") or "").strip().lower()
    return "photo" if mime_type.startswith("image/") else "document"


def _execution_artifact_messages(execution: Dict[str, Any]) -> list[TelegramMessage]:
    messages: list[TelegramMessage] = []
    artifacts = execution.get("artifacts") if isinstance(execution.get("artifacts"), list) else []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        text = str(artifact.get("description") or artifact.get("filename") or "").strip()
        messages.append(
            TelegramMessage(
                kind=_telegram_delivery_kind_for_artifact(artifact),
                text=text,
                files=(artifact,),
            )
        )
    return messages


def _dedupe_telegram_messages(messages: list[TelegramMessage]) -> list[TelegramMessage]:
    seen: set[str] = set()
    out: list[TelegramMessage] = []
    for message in messages:
        file_key = ""
        if message.files:
            file_item = message.files[0]
            file_key = str(
                file_item.get("id")
                or file_item.get("hosted_uri")
                or file_item.get("url")
                or file_item.get("physical_path")
                or file_item.get("local_path")
                or file_item.get("logical_path")
                or file_item.get("filename")
                or ""
            )
        key = f"{message.kind}:{file_key}:{message.text}"
        if key in seen:
            continue
        seen.add(key)
        out.append(message)
    return out


async def _deliver_execution_to_telegram(
    entrypoint: Any,
    *,
    user_id: str,
    execution: Dict[str, Any],
    react_result: Dict[str, Any],
    answer: str,
) -> Dict[str, Any] | None:
    if not _bundle_prop(entrypoint, "integrations.telegram.enabled", False):
        return None
    if not _bundle_prop(entrypoint, "integrations.telegram.send_responses", True):
        return None
    recipient = _telegram_recipient_for_user(entrypoint, user_id=user_id)
    if not recipient:
        return None

    messages = render_telegram_messages_from_timeline(
        timeline=(
            react_result.get("turn_log")
            if isinstance(react_result.get("turn_log"), dict) and react_result.get("turn_log")
            else react_result.get("timeline") if isinstance(react_result.get("timeline"), dict) else None
        ),
        react_turn={"answer": answer or execution.get("summary") or "Task execution completed."},
    )
    messages.extend(_execution_artifact_messages(execution))
    messages = _dedupe_telegram_messages(messages)
    if not messages:
        messages = [TelegramMessage(kind="text", text=str(execution.get("summary") or "Task execution completed."))]

    delivery = await send_telegram_messages(
        bot_token=await _telegram_bot_token(),
        chat_id=recipient["chat_id"],
        messages=messages,
    )
    return {
        "surface": "telegram",
        "recipient": recipient,
        "message_count": len(messages),
        "delivery": delivery,
    }


async def run_task_execution(
    entrypoint: Any,
    *,
    task_id: str,
    trigger: str,
    source: Dict[str, Any] | None = None,
    execution_id: str = "",
    run_conversation_id: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    task = await storage.get_task(task_id)
    if not task:
        return {
            "ok": False,
            "user_id": target_user,
            "error": {"code": "task_not_found", "message": f"Task {task_id!r} was not found."},
            "execution": None,
        }
    task_status = str(task.get("status") or "").strip().lower()
    trigger_norm = str(trigger or "").strip().lower()
    if task_status in {"archived", "deleted"} or (trigger_norm == "scheduled" and task_status != "enabled"):
        execution = await storage.get_execution(execution_id=execution_id, task_id=task_id) if execution_id else None
        summary = (
            f"Scheduled execution skipped because task status is {task_status or 'unknown'}."
            if trigger_norm == "scheduled"
            else f"Task execution skipped because task status is {task_status or 'unknown'}."
        )
        if execution:
            execution = await storage.update_execution(
                execution_id=str(execution.get("id") or execution_id),
                task_id=task_id,
                status="cancelled",
                summary=summary,
                log_excerpt=summary,
            )
        return {
            "ok": True,
            "skipped": True,
            "reason": "task_not_runnable",
            "user_id": target_user,
            "task": task,
            "execution": execution,
            "answer": summary,
        }

    run_conversation_id = str(run_conversation_id or "").strip() or f"task_job_{uuid.uuid4().hex}"
    execution = await storage.get_execution(execution_id=execution_id, task_id=task_id) if execution_id else None
    if execution:
        execution = await storage.update_execution(
            execution_id=str(execution.get("id") or execution_id),
            task_id=task_id,
            status="running",
            conversation_id=run_conversation_id,
            summary="Execution started.",
            metadata_patch={"agent_surface": "task_job"},
        )
    else:
        execution = await storage.create_execution(
            task_id=task_id,
            status="running",
            trigger=trigger,
            source=source or {},
            conversation_id=run_conversation_id,
            summary="Execution started.",
            metadata={"agent_surface": "task_job"},
        )
    turn_id = f"turn_{execution['id']}"
    task_definition = {
        "id": task.get("id"),
        "title": task.get("title"),
        "body": task.get("body"),
        "description": task.get("description"),
        "schedule": task.get("schedule"),
        "relations": task.get("relations"),
        "metadata": task.get("metadata"),
    }
    bundle_call_context = {
        "kind": "task_execution",
        "task_id": task_id,
        "execution_id": execution["id"],
        "trigger": trigger,
        "conversation_id": run_conversation_id,
        "turn_id": turn_id,
        "source": source or {},
        "task": task_definition,
        "task_definition": json.dumps(task_definition, sort_keys=True, ensure_ascii=True),
    }

    from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException
    econ_subject = None
    econ_decision = None
    try:
        # Carry the user identity and verify the task pipeline is economically
        # feasible BEFORE running it (verify-only). The ReAct work routes through
        # self.run(), which reserves+settles the real cost under the same user, so
        # we do NOT reserve/settle here. Denial -> execution cancelled, no run.
        econ_subject, econ_decision = await _task_verify_economics(
            entrypoint, target_user=target_user, source=source or {},
        )
    except EconomicsLimitException as exc:
        code = getattr(exc, "code", "rate_limited")
        execution = await storage.update_execution(
            execution_id=execution["id"],
            task_id=task_id,
            status="cancelled",
            conversation_id=run_conversation_id,
            turn_id=turn_id,
            summary=f"Task execution denied by economics ({code}).",
            error=str(exc),
            log_excerpt=str(exc)[:1000],
            metadata_patch={"economics": {
                "denied": True, "code": code, "message": str(exc),
                "data": getattr(exc, "data", {}) or {},
            }},
        )
        logger.warning(
            "[tasks.economics] execution denied: execution_id=%s user=%s code=%s",
            execution.get("id"), target_user, code,
        )
        return {
            "ok": False,
            "denied": True,
            "user_id": target_user,
            "task": await storage.get_task(task_id),
            "execution": execution,
            "error": {"code": "economics_denied", "message": str(exc)},
        }
    if econ_decision is not None:
        logger.info(
            "[tasks.economics] preflight ok: execution_id=%s user=%s role=%s lane=%s funding=%s plan=%s",
            execution["id"], target_user, getattr(econ_subject, "user_type", None),
            econ_decision.lane, econ_decision.funding_source, econ_decision.plan_id,
        )
    # Point (2): propagate the resolved role so the inner ReAct run() applies the
    # correct plan/limits for this user (esp. scheduled tasks enqueued in a system
    # context where the carried role may default to "registered").
    resolved_user_type = getattr(econ_subject, "user_type", None)

    try:
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_bundle_call_context
        except Exception:
            bind_current_bundle_call_context = None  # type: ignore[assignment]
        context_binding = (
            bind_current_bundle_call_context(bundle_call_context)
            if callable(bind_current_bundle_call_context)
            else nullcontext()
        )
        with context_binding:
            result = await _execute_task_job(
                entrypoint,
                task=task,
                execution=execution,
                storage=storage,
                target_user=target_user,
                trigger=trigger,
                source=source or {},
                run_conversation_id=run_conversation_id,
                turn_id=turn_id,
                bundle_call_context=bundle_call_context,
                resolved_user_type=resolved_user_type,
            )
        answer = _result_answer(result)
        current_execution = await storage.get_execution(execution_id=execution["id"], task_id=task_id) or execution
        current_status = str(current_execution.get("status") or "").strip().lower()
        requested_status = _result_status(result)
        final_status = current_status if current_status in {"success", "failed", "cancelled"} else (requested_status or "success")
        artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else None
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else None
        if econ_decision is not None:
            metadata = {**(metadata or {}), **_task_economics_metadata(econ_decision)}
        execution = await storage.update_execution(
            execution_id=execution["id"],
            task_id=task_id,
            status=final_status,
            conversation_id=run_conversation_id,
            turn_id=turn_id,
            summary=answer or str(current_execution.get("summary") or "").strip() or "Task execution completed.",
            result=result or {},
            log_excerpt=answer[:1000],
            artifacts=artifacts,
            metadata_patch=metadata,
        )
        delivery = await _deliver_execution_to_telegram(
            entrypoint,
            user_id=target_user,
            execution=execution,
            react_result=result or {},
            answer=answer,
        )
        if delivery:
            execution = await storage.update_execution(
                execution_id=execution["id"],
                task_id=task_id,
                metadata_patch={"last_delivery": delivery},
            )
        return {
            "ok": True,
            "user_id": target_user,
            "task": await storage.get_task(task_id),
            "execution": execution,
            "answer": answer,
        }
    except Exception as exc:
        execution = await storage.update_execution(
            execution_id=execution["id"],
            task_id=task_id,
            status="failed",
            conversation_id=run_conversation_id,
            turn_id=turn_id,
            summary="Task execution failed.",
            error=str(exc),
            log_excerpt=str(exc)[:1000],
        )
        return {
            "ok": False,
            "user_id": target_user,
            "task": await storage.get_task(task_id),
            "execution": execution,
            "error": {"code": "run_task_failed", "message": str(exc)},
        }


async def enqueue_task_job(
    entrypoint: Any,
    *,
    work_kind: str,
    task_id: str,
    trigger: str,
    source: Dict[str, Any],
    conversation_id: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    task = await storage.get_task(task_id)
    if not task:
        return {
            "ok": False,
            "user_id": target_user,
            "error": {"code": "task_not_found", "message": f"Task {task_id!r} was not found."},
            "execution": None,
        }
    run_conversation_id = str(conversation_id or "").strip() or f"task_job_{uuid.uuid4().hex}"
    execution = await storage.create_execution(
        task_id=task_id,
        status="queued",
        trigger=trigger,
        source=source,
        conversation_id=run_conversation_id,
        summary="Queued for task execution.",
        metadata={"agent_surface": "task_job"},
    )
    turn_id = f"turn_{execution['id']}"
    tenant = str(getattr(entrypoint.config, "tenant", "") or getattr(entrypoint.settings, "TENANT", "") or "")
    project = str(getattr(entrypoint.config, "project", "") or getattr(entrypoint.settings, "PROJECT", "") or "")
    bundle_id = str(getattr(getattr(entrypoint.config, "ai_bundle_spec", None), "id", "") or "task-and-memo-app@1-0")
    if getattr(entrypoint, "redis", None) is None:
        execution = await storage.update_execution(
            execution_id=execution["id"],
            task_id=task_id,
            status="cancelled",
            summary="Task job could not be queued because Redis is unavailable.",
        )
        return {
            "ok": False,
            "user_id": target_user,
            "task": task,
            "execution": execution,
            "error": {"code": "redis_unavailable", "message": "Redis is required to enqueue task jobs."},
        }
    comm_context = getattr(entrypoint, "comm_context", None)
    comm_user = getattr(comm_context, "user", None)
    user_type = str(source.get("user_type") or getattr(comm_user, "user_type", "registered") or "registered")
    stream = RedisBackgroundJobStream(entrypoint.redis, tenant=tenant, project=project)
    enqueue = await stream.enqueue(
        work_kind=work_kind,
        bundle_id=bundle_id,
        user_id=target_user,
        user_type=user_type,
        queue=user_type,
        job_id=f"job_{execution['id']}",
        dedupe_key=str(source.get("dedupe_key") or ""),
        source=source,
        metadata={
            "conversation_id": run_conversation_id,
            "turn_id": turn_id,
            "text": f"Run task: {task.get('title') or task_id}",
        },
        payload={
            "task_id": task_id,
            "execution_id": execution["id"],
        },
    )
    if not enqueue.enqueued:
        execution = await storage.update_execution(
            execution_id=execution["id"],
            task_id=task_id,
            status="cancelled",
            summary=f"Task job was not enqueued ({enqueue.reason}).",
        )
    return {
        "ok": bool(enqueue.enqueued),
        "user_id": target_user,
        "task": task,
        "execution": execution,
        "job": {
            "job_id": enqueue.job_id,
            "stream_key": enqueue.stream_key,
            "stream_id": enqueue.stream_id,
            "reason": enqueue.reason,
        },
    }


async def run_task_now(
    entrypoint: Any,
    *,
    task_id: str,
    conversation_id: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    source: Dict[str, Any] = {"surface": "operation", "operation": "run_task_now"}
    if str(conversation_id or "").strip():
        source["requested_conversation_id"] = str(conversation_id or "").strip()
        source["conversation_policy"] = "ignored_fresh_job_conversation"
    return await enqueue_task_job(
        entrypoint,
        work_kind=WORK_KIND_TASK_RUN_NOW,
        task_id=task_id,
        trigger="manual",
        source=source,
        user_id=user_id,
        fingerprint=fingerprint,
    )
