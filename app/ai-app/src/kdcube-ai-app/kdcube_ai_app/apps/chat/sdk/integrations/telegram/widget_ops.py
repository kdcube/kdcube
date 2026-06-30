from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Dict, Optional

from fastapi import HTTPException

from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bundle_registry import (
    configured_bundle_id,
    register_config,
    resolve_config,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.connection_edges import (
    ConnectionEdgesClient,
    request_origin,
)
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_bundle_id, get_current_request_context
from kdcube_ai_app.apps.chat.sdk.runtime.http_ops import BundleBinaryResponse

BUNDLE_ID = ""
TELEGRAM_WEBAPP_DOWNLOAD_ORIGIN = "https://web.telegram.org"
LOGGER = logging.getLogger("kdcube.telegram.widget_ops")

automation_operations: Any = None
telegram_user_admin: Any = None
telegram_widget_auth: Any = None
webapp: Any = None
_CONFIGS: Dict[str, Dict[str, Any]] = {}


def configure_telegram_widget_ops(
    *,
    automation_operations_module: Any,
    telegram_user_admin_module: Any,
    telegram_widget_auth_module: Any,
    webapp_module: Any,
    bundle_id: str = "",
) -> None:
    """Bind bundle-owned automation, Telegram admin, auth, and webapp modules."""
    global BUNDLE_ID, automation_operations, telegram_user_admin, telegram_widget_auth, webapp
    BUNDLE_ID = str(bundle_id or "").strip()
    automation_operations = automation_operations_module
    telegram_user_admin = telegram_user_admin_module
    telegram_widget_auth = telegram_widget_auth_module
    webapp = webapp_module
    register_config(
        _CONFIGS,
        bundle_id=BUNDLE_ID,
        config={
            "automation_operations": automation_operations_module,
            "telegram_user_admin": telegram_user_admin_module,
            "telegram_widget_auth": telegram_widget_auth_module,
            "webapp": webapp_module,
        },
    )


def _config(entrypoint: Any = None) -> Dict[str, Any]:
    cfg = resolve_config(_CONFIGS, entrypoint=entrypoint, label="telegram widget operations integration")
    if not cfg.get("automation_operations") or not cfg.get("telegram_user_admin") or not cfg.get("telegram_widget_auth") or not cfg.get("webapp"):
        raise RuntimeError("telegram widget operations integration is not configured")
    return cfg


def _bundle_id(entrypoint: Any = None) -> str:
    current = str(get_current_bundle_id() or "").strip()
    if current:
        return current
    current_ctx = get_current_request_context()
    current_ctx_id = str(getattr(getattr(current_ctx, "routing", None), "bundle_id", None) or "").strip()
    if current_ctx_id:
        return current_ctx_id
    entrypoint_ctx_id = str(
        getattr(getattr(getattr(entrypoint, "comm_context", None), "routing", None), "bundle_id", None) or ""
    ).strip()
    if entrypoint_ctx_id:
        return entrypoint_ctx_id
    spec = getattr(getattr(entrypoint, "config", None), "ai_bundle_spec", None)
    spec_id = str(getattr(spec, "id", None) or "").strip()
    if spec_id:
        return spec_id
    return configured_bundle_id(_config(entrypoint)) or BUNDLE_ID


def _webapp_auth_context_config(entrypoint: Any = None, *, integration_id: str = "") -> Dict[str, Any]:
    app = _config(entrypoint).get("webapp")
    fn = getattr(app, "_auth_context_config", None)
    if not callable(fn):
        return {}
    try:
        return fn(entrypoint, integration_id=integration_id)
    except TypeError:
        return fn(entrypoint)
    except Exception:
        return {}


def _webapp_connection_hub_bundle_id(entrypoint: Any = None) -> str:
    app = _config(entrypoint).get("webapp")
    fn = getattr(app, "_connection_hub_bundle_id", None)
    if not callable(fn):
        return "connection-hub@1-0"
    try:
        value = str(fn(entrypoint) or "").strip()
    except Exception:
        value = ""
    return value or "connection-hub@1-0"


async def _identity(entrypoint: Any, *, request: Any = None, telegram_init_data: str = ""):
    identity = await _profile_identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    connection = await _telegram_connection_status(
        entrypoint,
        request=request,
        telegram_init_data=telegram_init_data,
    )
    if not connection.get("linked"):
        raise HTTPException(status_code=403, detail="Connect this Telegram account to KDCube first")
    return identity


async def _profile_identity(entrypoint: Any, *, request: Any = None, telegram_init_data: str = ""):
    cfg = _config(entrypoint)
    return await cfg["telegram_widget_auth"].resolve_identity(
        entrypoint,
        request=request,
        telegram_init_data=telegram_init_data,
        allowed_roles=(),
        create_if_missing=True,
    )


async def _admin_identity(entrypoint: Any, *, request: Any = None, telegram_init_data: str = ""):
    cfg = _config(entrypoint)
    return await cfg["telegram_widget_auth"].resolve_identity(
        entrypoint,
        request=request,
        telegram_init_data=telegram_init_data,
        allowed_roles=("admin",),
        create_if_missing=False,
    )


def _tenant_project(entrypoint: Any) -> tuple[str, str]:
    ctx = getattr(entrypoint, "comm_context", None)
    actor = getattr(ctx, "actor", None)
    tenant = str(getattr(actor, "tenant_id", "") or "").strip()
    project = str(getattr(actor, "project_id", "") or "").strip()
    return tenant, project


def _platform_user_from_edge(edge: Dict[str, Any] | None) -> str:
    if not isinstance(edge, dict):
        return ""
    target = edge.get("to")
    if not isinstance(target, dict):
        return ""
    return str(target.get("user_id") or target.get("subject") or "").strip()


async def _telegram_connection_status(
    entrypoint: Any,
    *,
    request: Any = None,
    telegram_init_data: str = "",
) -> Dict[str, Any]:
    init_data = str(telegram_init_data or "").strip()
    if not init_data:
        try:
            init_data = str(getattr(request, "headers", {}).get("X-Telegram-Init-Data") or "").strip()
        except Exception:
            init_data = ""
    try:
        result = await ConnectionEdgesClient(entrypoint).telegram_edge_status(
            telegram_init_data=init_data,
            public_origin=request_origin(request),
        )
    except Exception as exc:
        LOGGER.warning("[telegram.connection] status failed error=%s", exc)
        return {
            "ok": False,
            "linked": False,
            "required": True,
            "error": "connection_hub_status_failed",
            "message": str(exc),
        }
    edge = result.get("edge") if isinstance(result.get("edge"), dict) else {}
    principal = result.get("principal") if isinstance(result.get("principal"), dict) else {}
    platform_user_id = (
        str(principal.get("platform_user_id") or "").strip()
        or _platform_user_from_edge(edge)
    )
    linked = bool(result.get("linked") or platform_user_id)
    LOGGER.info(
        "[telegram.connection] status provider=%s provider_subject=%s linked=%s platform_user_id=%s error=%s",
        result.get("provider") or "telegram",
        result.get("provider_subject") or "",
        linked,
        platform_user_id,
        result.get("error") or "",
    )
    return {
        "ok": bool(result.get("ok", True)),
        "linked": linked,
        "required": not linked,
        "provider": result.get("provider") or "telegram",
        "provider_subject": result.get("provider_subject") or "",
        "platform_user_id": platform_user_id,
        "edge": edge,
        "principal": principal,
        "error": result.get("error") or "",
        "message": result.get("message") or "",
    }


def _with_telegram_download_headers(result: Any):
    if not isinstance(result, BundleBinaryResponse):
        return result
    headers = dict(result.headers or {})
    lower_keys = {str(key).lower() for key in headers}
    if "access-control-allow-origin" not in lower_keys:
        headers["Access-Control-Allow-Origin"] = TELEGRAM_WEBAPP_DOWNLOAD_ORIGIN
    if "access-control-expose-headers" not in lower_keys:
        headers["Access-Control-Expose-Headers"] = "Content-Disposition"
    return replace(result, headers=headers)


async def profile(
    entrypoint: Any,
    *,
    request: Any = None,
    telegram_init_data: str = "",
) -> Dict[str, Any]:
    identity = await _profile_identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    role = str(identity.role or "anonymous").strip().lower() or "anonymous"
    connection = await _telegram_connection_status(
        entrypoint,
        request=request,
        telegram_init_data=telegram_init_data,
    )
    allowed = bool(connection.get("linked"))
    mapped_kdcube_user_id = str(identity.mapping.get("kdcube_user_id") or "").strip()
    LOGGER.info(
        "[telegram.profile] actor_user_id=%s telegram_user_id=%s linked=%s platform_user_id=%s can_use_widget=%s",
        identity.user_id,
        identity.telegram_user_id,
        allowed,
        connection.get("platform_user_id") or "",
        allowed,
    )
    return {
        "ok": True,
        "bundle_id": _bundle_id(entrypoint),
        "auth_surface": "telegram_webapp",
        "user_id": identity.user_id,
        "fingerprint": identity.fingerprint,
        "telegram": {
            "user_id": identity.telegram_user_id,
            "username": identity.telegram_username,
            "role": role,
            "allowed": allowed,
            "is_admin": role == "admin",
            "conversation_id": str(identity.mapping.get("conversation_id") or "").strip(),
        },
        "kdcube": {
            "user_id": identity.user_id,
            "mapped_user_id": mapped_kdcube_user_id,
            "role": role,
            "user_type": "registered" if allowed else "external",
            "mapped": bool(mapped_kdcube_user_id),
        },
        "connection": connection,
        "authContext": _webapp_auth_context_config(entrypoint, integration_id="telegram.kdcube_ref"),
        "permissions": {
            "can_use_chatbot": allowed,
            "can_use_widget": allowed,
            "show_admin_component": role == "admin",
        },
    }


async def list_conversations(
    entrypoint: Any,
    *,
    request: Any = None,
    telegram_init_data: str = "",
) -> Dict[str, Any]:
    identity = await _identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    admin = _config(entrypoint)["telegram_user_admin"]
    result = admin.storage(entrypoint).list_conversations(
        telegram_user_id=identity.telegram_user_id,
        telegram_chat_id=identity.telegram_chat_id,
        telegram_username=identity.telegram_username,
        create_if_missing=False,
    )
    return {
        "ok": True,
        "active_conversation_id": result.get("active_conversation_id") or "",
        "conversations": result.get("conversations") or [],
        "count": len(result.get("conversations") or []),
        "telegram_user_id": identity.telegram_user_id,
    }


async def create_conversation(
    entrypoint: Any,
    *,
    request: Any = None,
    telegram_init_data: str = "",
    title: str = "",
) -> Dict[str, Any]:
    identity = await _identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    admin = _config(entrypoint)["telegram_user_admin"]
    result = admin.storage(entrypoint).create_conversation(
        telegram_user_id=identity.telegram_user_id,
        telegram_chat_id=identity.telegram_chat_id,
        telegram_username=identity.telegram_username,
        title=title,
    )
    return {
        "ok": True,
        "active_conversation_id": result.get("active_conversation_id") or "",
        "conversations": result.get("conversations") or [],
        "count": len(result.get("conversations") or []),
        "telegram_user_id": identity.telegram_user_id,
    }


async def switch_conversation(
    entrypoint: Any,
    *,
    conversation_id: str,
    request: Any = None,
    telegram_init_data: str = "",
) -> Dict[str, Any]:
    identity = await _identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    admin = _config(entrypoint)["telegram_user_admin"]
    result = admin.storage(entrypoint).switch_conversation(
        telegram_user_id=identity.telegram_user_id,
        conversation_id=conversation_id,
    )
    if not result.get("ok", True):
        return result
    return {
        "ok": True,
        "active_conversation_id": result.get("active_conversation_id") or "",
        "conversations": result.get("conversations") or [],
        "count": len(result.get("conversations") or []),
        "telegram_user_id": identity.telegram_user_id,
    }


async def delete_conversation(
    entrypoint: Any,
    *,
    conversation_id: str,
    request: Any = None,
    telegram_init_data: str = "",
    delete_history: bool = True,
) -> Dict[str, Any]:
    identity = await _identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    admin = _config(entrypoint)["telegram_user_admin"]
    registry_result = admin.storage(entrypoint).delete_conversation(
        telegram_user_id=identity.telegram_user_id,
        conversation_id=conversation_id,
    )
    if not registry_result.get("ok", True):
        return {
            "ok": False,
            "deleted": False,
            "deleted_conversation_id": "",
            "deleted_blobs": {},
            "active_conversation_id": registry_result.get("active_conversation_id") or "",
            "conversations": registry_result.get("conversations") or [],
            "count": len(registry_result.get("conversations") or []),
            "telegram_user_id": identity.telegram_user_id,
            "error": registry_result.get("error") or {"code": "conversation_delete_failed", "message": "Conversation could not be deleted."},
        }
    deleted_blobs: Dict[str, int] = {}
    if registry_result.get("deleted") and delete_history:
        tenant, project = _tenant_project(entrypoint)
        store = admin._conversation_store(entrypoint)
        if tenant and project and store:
            deleted_blobs = await store.delete_conversation(
                tenant=tenant,
                project=project,
                user_type=identity.role,
                user_or_fp=identity.user_id,
                conversation_id=conversation_id,
            )
    return {
        "ok": True,
        "deleted": bool(registry_result.get("deleted")),
        "deleted_conversation_id": registry_result.get("deleted_conversation_id") or "",
        "deleted_blobs": deleted_blobs,
        "active_conversation_id": registry_result.get("active_conversation_id") or "",
        "conversations": registry_result.get("conversations") or [],
        "count": len(registry_result.get("conversations") or []),
        "telegram_user_id": identity.telegram_user_id,
    }


async def webapp_data(
    entrypoint: Any,
    *,
    request: Any = None,
    telegram_init_data: str = "",
    mark_memory_seen: bool = False,
    widget_path: str = "",
    path: str = "",
) -> Dict[str, Any]:
    identity = await _profile_identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    connection = await _telegram_connection_status(
        entrypoint,
        request=request,
        telegram_init_data=telegram_init_data,
    )
    app = _config(entrypoint)["webapp"]
    if not connection.get("linked"):
        return {
            "ok": True,
            "auth_surface": "telegram_webapp",
            "bundle_id": _bundle_id(entrypoint),
            "connections": {
                "connection_hub": {
                    "bundle_id": _webapp_connection_hub_bundle_id(entrypoint),
                },
            },
            "authContext": _webapp_auth_context_config(entrypoint, integration_id="telegram.kdcube_ref"),
            "connection": connection,
            "telegram_identity": identity.as_payload(),
            "memory": None,
            "conversations": None,
        }
    payload = await app.payload(
        entrypoint,
        user_id=identity.user_id,
        fingerprint=identity.fingerprint,
        mark_memory_seen=mark_memory_seen,
        widget_path=widget_path or path,
        telegram_identity=identity.as_payload(),
        include_admin=False,
    )
    payload["auth_surface"] = "telegram_webapp"
    payload["connection"] = connection
    return payload


async def admin_payload(
    entrypoint: Any,
    *,
    request: Any = None,
    telegram_init_data: str = "",
) -> Dict[str, Any]:
    identity = await _admin_identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    admin = _config(entrypoint)["telegram_user_admin"]
    payload = admin.payload(entrypoint)
    payload["auth_surface"] = "telegram_webapp"
    payload["telegram_user_id"] = identity.telegram_user_id
    payload["kdcube_user_id"] = identity.user_id
    payload["current_kdcube_user_id"] = identity.user_id
    payload["current_user"] = {
        "user_id": identity.user_id,
        "username": identity.telegram_username,
        "roles": [identity.role],
    }
    return payload


async def admin_upsert(
    entrypoint: Any,
    *,
    telegram_user_id: str,
    request: Any = None,
    telegram_init_data: str = "",
    telegram_chat_id: str = "",
    telegram_username: str = "",
    kdcube_user_id: str = "",
    role: str = "anonymous",
    conversation_id: str = "",
    notes: str = "",
) -> Dict[str, Any]:
    identity = await _admin_identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    admin = _config(entrypoint)["telegram_user_admin"]
    payload = admin.upsert(
        entrypoint,
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        telegram_username=telegram_username,
        kdcube_user_id=kdcube_user_id,
        role=role,
        conversation_id=conversation_id,
        notes=notes,
    )
    payload["notification"] = await admin.notify_access_change(entrypoint, result=payload)
    payload["auth_surface"] = "telegram_webapp"
    payload["telegram_user_id"] = identity.telegram_user_id
    payload["kdcube_user_id"] = identity.user_id
    return payload


async def admin_delete(
    entrypoint: Any,
    *,
    telegram_user_id: str,
    request: Any = None,
    telegram_init_data: str = "",
) -> Dict[str, Any]:
    identity = await _admin_identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    admin = _config(entrypoint)["telegram_user_admin"]
    payload = admin.delete(entrypoint, telegram_user_id=telegram_user_id)
    payload["auth_surface"] = "telegram_webapp"
    payload["telegram_user_id"] = identity.telegram_user_id
    payload["kdcube_user_id"] = identity.user_id
    return payload


async def list_automations(
    entrypoint: Any,
    *,
    request: Any = None,
    telegram_init_data: str = "",
    query: str = "",
    status: str = "",
    limit: int = 50,
    execution_limit: int = 3,
) -> Dict[str, Any]:
    identity = await _identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    automations = _config(entrypoint)["automation_operations"]
    return await automations.list_automations(
        entrypoint,
        query=query,
        status=status,
        limit=limit,
        execution_limit=execution_limit,
        user_id=identity.user_id,
        fingerprint=identity.fingerprint,
        public=True,
    )


async def get_automation(
    entrypoint: Any,
    *,
    automation_id: str,
    request: Any = None,
    telegram_init_data: str = "",
    execution_limit: int = 10,
) -> Dict[str, Any]:
    identity = await _identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    automations = _config(entrypoint)["automation_operations"]
    return await automations.get_automation(
        entrypoint,
        automation_id=automation_id,
        execution_limit=execution_limit,
        user_id=identity.user_id,
        fingerprint=identity.fingerprint,
        public=True,
    )


async def create_automation(
    entrypoint: Any,
    *,
    title: str,
    request: Any = None,
    telegram_init_data: str = "",
    description: str = "",
    schedule_cron: str = "",
    timezone: str = "UTC",
    recurring: bool = True,
    labels: str = "",
    source: str = "telegram_widget",
    conversation_id: str = "",
) -> Dict[str, Any]:
    identity = await _identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    automations = _config(entrypoint)["automation_operations"]
    return await automations.create_automation(
        entrypoint,
        title=title,
        description=description,
        schedule_cron=schedule_cron,
        timezone=timezone,
        recurring=recurring,
        labels=labels,
        source=source,
        conversation_id=conversation_id,
        user_id=identity.user_id,
        fingerprint=identity.fingerprint,
    )


async def update_automation(
    entrypoint: Any,
    *,
    automation_id: str,
    request: Any = None,
    telegram_init_data: str = "",
    title: Optional[str] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
    schedule_cron: Optional[str] = None,
    timezone: Optional[str] = None,
    recurring: Optional[bool] = None,
    labels: Optional[str] = None,
    conversation_id: Optional[str] = None,
    execution_conversation_id: Optional[str] = None,
    metadata_patch: Optional[Dict[str, Any]] = None,
    context_patch: Optional[Dict[str, Any]] = None,
    relations_patch: Optional[Dict[str, Any]] = None,
    revision_mode: str = "auto",
) -> Dict[str, Any]:
    identity = await _identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    automations = _config(entrypoint)["automation_operations"]
    return await automations.update_automation(
        entrypoint,
        automation_id=automation_id,
        title=title,
        description=description,
        status=status,
        schedule_cron=schedule_cron,
        timezone=timezone,
        recurring=recurring,
        labels=labels,
        conversation_id=conversation_id,
        execution_conversation_id=execution_conversation_id,
        metadata_patch=metadata_patch,
        context_patch=context_patch,
        relations_patch=relations_patch,
        revision_mode=revision_mode,
        user_id=identity.user_id,
        fingerprint=identity.fingerprint,
    )


async def delete_automation(
    entrypoint: Any,
    *,
    automation_id: str,
    request: Any = None,
    telegram_init_data: str = "",
    hard: bool = False,
) -> Dict[str, Any]:
    identity = await _identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    automations = _config(entrypoint)["automation_operations"]
    return await automations.delete_automation(
        entrypoint,
        automation_id=automation_id,
        hard=hard,
        user_id=identity.user_id,
        fingerprint=identity.fingerprint,
    )


async def search_automations(
    entrypoint: Any,
    *,
    request: Any = None,
    telegram_init_data: str = "",
    query: str = "",
    status: str = "",
    limit: int = 20,
    execution_limit: int = 3,
) -> Dict[str, Any]:
    identity = await _identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    automations = _config(entrypoint)["automation_operations"]
    return await automations.search_automations(
        entrypoint,
        query=query,
        status=status,
        limit=limit,
        execution_limit=execution_limit,
        user_id=identity.user_id,
        fingerprint=identity.fingerprint,
        public=True,
    )


async def list_executions(
    entrypoint: Any,
    *,
    request: Any = None,
    telegram_init_data: str = "",
    automation_id: str = "",
    status: str = "",
    limit: int = 50,
) -> Dict[str, Any]:
    identity = await _identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    automations = _config(entrypoint)["automation_operations"]
    return await automations.list_executions(
        entrypoint,
        automation_id=automation_id,
        status=status,
        limit=limit,
        user_id=identity.user_id,
        fingerprint=identity.fingerprint,
        public=True,
    )


async def search_executions(
    entrypoint: Any,
    *,
    request: Any = None,
    telegram_init_data: str = "",
    query: str = "",
    automation_id: str = "",
    status: str = "",
    limit: int = 50,
) -> Dict[str, Any]:
    identity = await _identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    automations = _config(entrypoint)["automation_operations"]
    return await automations.search_executions(
        entrypoint,
        query=query,
        automation_id=automation_id,
        status=status,
        limit=limit,
        user_id=identity.user_id,
        fingerprint=identity.fingerprint,
        public=True,
    )


async def get_execution(
    entrypoint: Any,
    *,
    execution_id: str,
    request: Any = None,
    telegram_init_data: str = "",
    automation_id: str = "",
) -> Dict[str, Any]:
    identity = await _identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    automations = _config(entrypoint)["automation_operations"]
    return await automations.get_execution(
        entrypoint,
        execution_id=execution_id,
        automation_id=automation_id,
        user_id=identity.user_id,
        fingerprint=identity.fingerprint,
        public=True,
    )


async def download_execution_artifact(
    entrypoint: Any,
    *,
    artifact_ref: str,
    request: Any = None,
    telegram_init_data: str = "",
    execution_id: str = "",
    download_token: str = "",
    automation_id: str = "",
):
    identity = None if download_token else await _identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    automations = _config(entrypoint)["automation_operations"]
    result = await automations.download_execution_artifact(
        entrypoint,
        artifact_ref=artifact_ref,
        execution_id=execution_id,
        automation_id=automation_id,
        user_id=identity.user_id if identity else None,
        fingerprint=identity.fingerprint if identity else None,
        download_token=download_token,
    )
    return _with_telegram_download_headers(result)


async def run_automation_now(
    entrypoint: Any,
    *,
    automation_id: str,
    request: Any = None,
    telegram_init_data: str = "",
    conversation_id: str = "",
) -> Dict[str, Any]:
    identity = await _identity(entrypoint, request=request, telegram_init_data=telegram_init_data)
    automations = _config(entrypoint)["automation_operations"]
    return await automations.run_automation_now(
        entrypoint,
        automation_id=automation_id,
        conversation_id=conversation_id,
        user_id=identity.user_id,
        fingerprint=identity.fingerprint,
    )
