from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from contextlib import nullcontext
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from kdcube_ai_app.apps.chat.sdk.config import get_secret
from kdcube_ai_app.infra.jobs.stream import RedisBackgroundJobStream
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
    if not callable(_storage_root_or_error):
        raise RuntimeError("task operations are not configured: storage_root_or_error is missing")
    return str(_storage_root_or_error(entrypoint))


def _target_user(entrypoint: Any, *, user_id: Optional[str] = None, fingerprint: Optional[str] = None) -> str:
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
        "user_type": "registered",
        "storage_root": _storage_root(entrypoint),
    }


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _download_token_secret(entrypoint: Any) -> bytes:
    _tenant, _project, bundle_id = _bundle_route_parts(entrypoint)
    secret = str(
        _bundle_prop(entrypoint, "integrations.telegram.artifact_download_secret", "")
        or get_secret("b:integrations.telegram.artifact_download_secret")
        or get_secret(f"bundles.{bundle_id}.secrets.integrations.telegram.artifact_download_secret")
        or _telegram_bot_token()
        or ""
    ).strip()
    return secret.encode("utf-8")


def _download_token_ttl(entrypoint: Any) -> int:
    try:
        ttl = int(_bundle_prop(entrypoint, "integrations.telegram.artifact_download_token_ttl_seconds", 900) or 900)
    except Exception:
        ttl = 900
    return max(60, min(ttl, 86400))


def _make_download_token(
    entrypoint: Any,
    *,
    artifact_ref: str,
    user_id: str,
) -> tuple[str, int] | None:
    secret = _download_token_secret(entrypoint)
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


def _verify_download_token(entrypoint: Any, *, artifact_ref: str, download_token: str) -> Dict[str, Any]:
    secret = _download_token_secret(entrypoint)
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
    query = urlencode(query_payload)
    return f"/api/integrations/bundles/{tenant}/{project}/{bundle_id}/{route}/{alias}?{query}"


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
        signed = _make_download_token(entrypoint, artifact_ref=artifact_ref, user_id=user_id) if public else None
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
            token_payload = _verify_download_token(entrypoint, artifact_ref=artifact_ref, download_token=download_token)
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
            "At the end, call task_job.update_execution_journal with the final status and then summarize the outcome briefly.",
        ]
    ).strip()


def _telegram_bot_token() -> str:
    bundle_id = BUNDLE_ID or "task-and-memo-app@1-0"
    return (
        get_secret("b:integrations.telegram.bot_token")
        or get_secret(f"bundles.{bundle_id}.secrets.integrations.telegram.bot_token")
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


def _message_kind_for_artifact(artifact: Dict[str, Any]) -> str:
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
                kind=_message_kind_for_artifact(artifact),
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
        bot_token=_telegram_bot_token(),
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

    try:
        comm_context = getattr(entrypoint, "comm_context", None)
        if comm_context is None or not hasattr(comm_context, "model_copy"):
            raise RuntimeError("task execution requires a request context with comm_context")

        scoped_ctx = comm_context.model_copy(deep=True)
        scoped_ctx.routing.session_id = run_conversation_id
        scoped_ctx.routing.conversation_id = run_conversation_id
        scoped_ctx.routing.turn_id = turn_id
        scoped_ctx.user.user_id = target_user
        scoped_ctx.bundle_call_context = bundle_call_context
        entrypoint.rebind_request_context(comm_context=scoped_ctx)

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
            "task_id": task_id,
            "execution_id": execution["id"],
            "trigger": trigger,
            "conversation_id": run_conversation_id,
            "turn_id": turn_id,
            "task_definition": bundle_call_context["task_definition"],
        }
        run_task_job_turn = getattr(entrypoint, "run_task_job_turn", None)
        if not callable(run_task_job_turn):
            raise RuntimeError("Task execution requires entrypoint.run_task_job_turn(...).")
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
            result = await run_task_job_turn(state=state)
        answer = str((result or {}).get("final_answer") or "").strip()
        current_execution = await storage.get_execution(execution_id=execution["id"], task_id=task_id) or execution
        current_status = str(current_execution.get("status") or "").strip().lower()
        final_status = current_status if current_status in {"success", "failed", "cancelled"} else "success"
        execution = await storage.update_execution(
            execution_id=execution["id"],
            task_id=task_id,
            status=final_status,
            conversation_id=run_conversation_id,
            turn_id=turn_id,
            summary=answer or str(current_execution.get("summary") or "").strip() or "Task execution completed.",
            result=result or {},
            log_excerpt=answer[:1000],
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
