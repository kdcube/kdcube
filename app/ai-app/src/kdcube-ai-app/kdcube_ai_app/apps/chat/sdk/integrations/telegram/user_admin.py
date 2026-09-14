from __future__ import annotations

import asyncio
import hmac
import inspect
import logging
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable, Dict

from connection_hub.authority_projection import (
    authority_has_platform_privilege,
)
from fastapi import HTTPException
from kdcube_ai_app.apps.chat.ids import new_turn_id
from kdcube_ai_app.apps.chat.ingress.ingress_core import IngressConfig, RawAttachment
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.event_identity import (
    DEFAULT_REACT_AGENT_ID,
    build_event_logical_path,
    normalize_agent_id,
)
from kdcube_ai_app.apps.chat.sdk.identity_authority import resolve_platform_authority
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.connection_edges import (
    ConnectionEdgesClient,
)
from kdcube_ai_app.apps.chat.sdk.integrations.integration_config import (
    configured_integrations,
    integration_definition_value,
    integration_secret_value,
    select_integration,
)
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    TelegramActivityStreamer,
    TelegramMessage,
    deliver_turn_to_telegram,
    hydrate_telegram_attachments,
    send_telegram_messages,
    summarize_telegram_update,
)
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    raw_attachments_from_telegram as sdk_raw_attachments_from_telegram,
)
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    telegram_command_kind_and_text as sdk_telegram_command_kind_and_text,
)
from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bundle_registry import (
    configured_bundle_id,
    register_config,
    resolve_config,
)
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    get_current_bundle_id,
    get_current_request_context,
)
from kdcube_ai_app.auth.sessions import RequestContext, UserSession, UserType

BUNDLE_ID = ""
log = logging.getLogger(__name__)

_storage_factory: Callable[[Any], Any] | None = None
_storage_root_or_error: Callable[[Any], Any] | None = None
_migrate_telegram_user_to_kdcube_scope: Callable[..., Any] | None = None
_CONFIGS: Dict[str, Dict[str, Any]] = {}


def configure_telegram_user_admin(
    *,
    storage_factory: Callable[[Any], Any],
    storage_root_or_error: Callable[[Any], Any] | None = None,
    migrate_telegram_user_to_kdcube_scope: Callable[..., Any] | None = None,
    bundle_id: str = "",
) -> None:
    """Bind bundle-owned Telegram user registry storage and migration hooks."""
    global BUNDLE_ID, _storage_factory, _storage_root_or_error, _migrate_telegram_user_to_kdcube_scope
    BUNDLE_ID = str(bundle_id or "").strip()
    _storage_factory = storage_factory
    _storage_root_or_error = storage_root_or_error
    _migrate_telegram_user_to_kdcube_scope = migrate_telegram_user_to_kdcube_scope
    register_config(
        _CONFIGS,
        bundle_id=BUNDLE_ID,
        config={
            "storage_factory": storage_factory,
            "storage_root_or_error": storage_root_or_error,
            "migrate_telegram_user_to_kdcube_scope": migrate_telegram_user_to_kdcube_scope,
        },
    )


def _config(entrypoint: Any = None) -> Dict[str, Any]:
    return resolve_config(_CONFIGS, entrypoint=entrypoint, label="telegram user admin integration")


def _authority_runtime_label(authority: Dict[str, Any]) -> str:
    if not str(authority.get("platform_user_id") or "").strip():
        return "external"
    if authority_has_platform_privilege(authority.get("platform_roles") or []):
        return "privileged"
    return "registered"


def _edge_platform_user_id(edge: Dict[str, Any] | None) -> str:
    if not isinstance(edge, dict):
        return ""
    target = edge.get("to")
    if not isinstance(target, dict):
        return ""
    return str(target.get("user_id") or target.get("subject") or "").strip()


async def _telegram_platform_authority(
    entrypoint: Any,
    *,
    actor_user_id: str,
    telegram_user_id: str,
    local_role: str,
    source: str,
) -> Dict[str, Any]:
    platform_user_id = ""
    try:
        resolved = await ConnectionEdgesClient(entrypoint).resolve_identity(
            provider="telegram",
            provider_subject=telegram_user_id,
        )
        principal = resolved.get("principal") if isinstance(resolved.get("principal"), dict) else {}
        edge = resolved.get("connection_edge") if isinstance(resolved.get("connection_edge"), dict) else {}
        platform_user_id = str(principal.get("platform_user_id") or "").strip() or _edge_platform_user_id(edge)
    except Exception as exc:
        log.info(
            "[%s] telegram authority edge unresolved | telegram_user_id=%s error=%s",
            _bundle_id(entrypoint),
            telegram_user_id,
            exc,
        )
    return await resolve_platform_authority(
        entrypoint,
        actor_user_id=actor_user_id,
        platform_user_id=platform_user_id,
        provider="telegram",
        provider_subject=telegram_user_id,
        local_role=local_role,
        source=source,
    )


def _telegram_connect_answer(turn_id: str) -> Dict[str, Any]:
    text = (
        "Connect your Telegram account to KDCube first. "
        "Open the Mini App and use the Connect tab to link this Telegram account."
    )
    return {
        "answer": text,
        "followups": [],
        "timeline": {
            "blocks": [
                {
                    "path": f"conv:tc:{turn_id}.telegram.connect_required",
                    "type": "answer",
                    "text": text,
                }
            ],
            "sources_pool": [],
        },
        "authorization": "connection_required",
    }


def _telegram_ingress_unavailable_answer(turn_id: str) -> Dict[str, Any]:
    text = "KDCube cannot accept this message right now. Please try again shortly."
    return {
        "answer": text,
        "followups": [],
        "timeline": {
            "blocks": [
                {
                    "path": f"conv:tc:{turn_id}.telegram.ingress_unavailable",
                    "type": "answer",
                    "text": text,
                }
            ],
            "sources_pool": [],
        },
        "error": "chat_ingress_unavailable",
    }


def _telegram_external_events(
    *,
    text: str,
    attachments: list[Dict[str, Any]],
    turn_id: str,
    text_event_type: str = "event.user.prompt",
    agent_id: str = DEFAULT_REACT_AGENT_ID,
) -> list[Dict[str, Any]]:
    events: list[Dict[str, Any]] = []

    def _event_path(event_id: str) -> str:
        return build_event_logical_path(turn_id=turn_id, event_path=event_id)

    def _timestamp() -> str:
        return datetime.utcnow().isoformat() + "Z"

    normalized_type = str(text_event_type or "event.user.prompt").strip() or "event.user.prompt"
    target_agent_id = normalize_agent_id(agent_id, default=DEFAULT_REACT_AGENT_ID)
    # A steer may carry no text: an empty steer is the "stop" control. Mirror the web
    # client, which allows an empty body only for event.user.steer; every other type
    # still needs text to produce an event.
    if text or normalized_type == "event.user.steer":
        event_suffix = normalized_type.rsplit(".", 1)[-1] or "prompt"
        event_id = f"telegram.{event_suffix}.{uuid.uuid4().hex[:12]}"
        events.append(
            {
                "event_id": event_id,
                "type": normalized_type,
                "event_source_id": f"telegram.user.{event_suffix}",
                "logical_path": _event_path(event_id),
                "reactive": True,
                "agent_id": target_agent_id,
                "timestamp": _timestamp(),
                "payload": {
                    "mime": "text/plain",
                    "event": {"text": text},
                },
            }
        )
    for raw in attachments or []:
        if not isinstance(raw, dict):
            continue
        event_id = f"telegram.attachment.{uuid.uuid4().hex[:12]}"
        hosted_uri = str(raw.get("hosted_uri") or "").strip() or None
        events.append(
            {
                "event_id": event_id,
                "type": "event.user.attachment",
                "event_source_id": "telegram.user.attachment",
                "logical_path": _event_path(event_id),
                "hosted_uri": hosted_uri,
                "reactive": bool(not text),
                "agent_id": target_agent_id,
                "timestamp": _timestamp(),
                "payload": {
                    "mime": str(raw.get("mime") or raw.get("mime_type") or "application/octet-stream"),
                    "event": dict(raw),
                },
            }
        )
    return events


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


def _storage_root(entrypoint: Any) -> Any:
    storage_root_or_error = _config(entrypoint).get("storage_root_or_error")
    if storage_root_or_error is None:
        raise RuntimeError("telegram user admin integration is not configured: storage_root_or_error is missing")
    return storage_root_or_error(entrypoint)


def _attachment_log_items(attachments: list[Dict[str, Any]] | None) -> list[Dict[str, Any]]:
    items: list[Dict[str, Any]] = []
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "type": item.get("type"),
                "filename": item.get("filename") or item.get("file_name"),
                "mime_type": item.get("mime_type") or item.get("mime"),
                "size_bytes": item.get("size_bytes") or item.get("size"),
                "file_id": item.get("file_id"),
                "hosted_uri": item.get("hosted_uri"),
                "key": item.get("key"),
                "rn": item.get("rn"),
                "error": item.get("error"),
            }
        )
    return items


def storage(entrypoint: Any) -> Any:
    storage_factory = _config(entrypoint).get("storage_factory")
    if storage_factory is None:
        raise RuntimeError("telegram user admin integration is not configured: storage_factory is missing")
    return storage_factory(entrypoint)


def payload(entrypoint: Any) -> Dict[str, Any]:
    registry = storage(entrypoint)
    roles = getattr(type(registry), "ALLOWED_ROLES", None) or getattr(registry, "ALLOWED_ROLES", ())
    ctx_user = getattr(getattr(entrypoint, "comm_context", None), "user", None)
    current_user_id = str(
        getattr(ctx_user, "user_id", None)
        or getattr(getattr(entrypoint, "comm", None), "user_id", None)
        or ""
    ).strip()
    current_username = str(getattr(ctx_user, "username", None) or "").strip()
    current_roles = [
        str(item or "").strip()
        for item in (getattr(ctx_user, "roles", None) or [])
        if str(item or "").strip()
    ]
    return {
        "ok": True,
        "bundle_id": _bundle_id(entrypoint),
        "roles": list(roles),
        "users": registry.list_users(),
        "storage_path": str(registry.path),
        "current_kdcube_user_id": current_user_id,
        "current_user": {
            "user_id": current_user_id,
            "username": current_username,
            "roles": current_roles,
        },
    }


def upsert(
    entrypoint: Any,
    *,
    telegram_user_id: str,
    telegram_chat_id: str = "",
    telegram_username: str = "",
    kdcube_user_id: str = "",
    role: str = "anonymous",
    conversation_id: str = "",
    notes: str = "",
) -> Dict[str, Any]:
    registry = storage(entrypoint)
    existing = registry.resolve_telegram_user(
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        telegram_username=telegram_username,
        create_if_missing=False,
    )
    old_kdcube_user_id = str(existing.get("kdcube_user_id") or "").strip()
    old_role = str(existing.get("role") or "anonymous").strip().lower() or "anonymous"
    stored_chat_id = str(telegram_chat_id or existing.get("telegram_chat_id") or "").strip()
    stored_username = str(telegram_username or existing.get("telegram_username") or "").strip()
    stored_kdcube_user_id = str(kdcube_user_id or existing.get("kdcube_user_id") or "").strip()
    stored_conversation_id = str(conversation_id or existing.get("conversation_id") or "").strip()
    user = registry.upsert_user(
        telegram_user_id=telegram_user_id,
        telegram_chat_id=stored_chat_id,
        telegram_username=stored_username,
        kdcube_user_id=stored_kdcube_user_id,
        role=role,
        conversation_id=stored_conversation_id,
        notes=notes,
    )
    migration = None
    new_kdcube_user_id = str(user.get("kdcube_user_id") or "").strip()
    migrate_telegram_user_to_kdcube_scope = _config(entrypoint).get("migrate_telegram_user_to_kdcube_scope")
    bundle_id = _bundle_id(entrypoint)
    if new_kdcube_user_id and migrate_telegram_user_to_kdcube_scope is not None:
        migration = migrate_telegram_user_to_kdcube_scope(
            _storage_root(entrypoint),
            telegram_user_id=str(user.get("telegram_user_id") or telegram_user_id),
            old_kdcube_user_id=old_kdcube_user_id,
            new_kdcube_user_id=new_kdcube_user_id,
            bundle_id=bundle_id,
        )
        log.info(
            "[%s] telegram user scope migration | telegram_user_id=%s old_kdcube_user_id=%s new_kdcube_user_id=%s result=%s",
            bundle_id,
            user.get("telegram_user_id") or telegram_user_id,
            old_kdcube_user_id,
            new_kdcube_user_id,
            migration,
        )
    new_role = str(user.get("role") or "anonymous").strip().lower() or "anonymous"
    approval_transition = {
        "from_role": old_role,
        "to_role": new_role,
        "approved": (not _role_allows_access(old_role)) and _role_allows_access(new_role),
    }
    return {
        "ok": True,
        "user": user,
        "migration": migration,
        "approval_transition": approval_transition,
        "users": registry.list_users(),
    }


async def notify_access_change(entrypoint: Any, *, result: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort chat notification when an admin approves a pending Telegram user."""
    if not isinstance(result, dict):
        return {"ok": False, "sent": 0, "skipped": "invalid_result"}
    transition = result.get("approval_transition") if isinstance(result.get("approval_transition"), dict) else {}
    if not transition.get("approved"):
        return {"ok": True, "sent": 0, "skipped": "not_newly_approved"}
    user = result.get("user") if isinstance(result.get("user"), dict) else {}
    chat_id = str(user.get("telegram_chat_id") or user.get("telegram_user_id") or "").strip()
    if not chat_id:
        return {"ok": False, "sent": 0, "skipped": "telegram_chat_id_missing"}
    role = str(user.get("role") or transition.get("to_role") or "").strip().lower() or "registered"
    text = (
        "Your KDCube bot access has been approved. "
        "Open the Mini App again, or send a message here to continue."
    )
    if role == "admin":
        text = (
            "Your KDCube bot access has been approved with admin permissions. "
            "Open the Mini App again, or send a message here to continue."
        )
    try:
        delivery = await send_telegram_messages(
            bot_token=await _bot_token_value(
                entrypoint,
                integration_id=str(user.get("integration_id") or user.get("auth_integration_id") or ""),
            ),
            chat_id=chat_id,
            messages=[TelegramMessage(kind="text", text=text)],
        )
    except Exception as exc:
        log.warning(
            "[%s] telegram approval notification failed | telegram_user_id=%s chat_id=%s error=%s",
            _bundle_id(entrypoint),
            user.get("telegram_user_id") or "",
            chat_id,
            exc,
        )
        return {"ok": False, "sent": 0, "error": str(exc)}
    return delivery


def delete(entrypoint: Any, *, telegram_user_id: str) -> Dict[str, Any]:
    registry = storage(entrypoint)
    deleted = registry.delete_user(telegram_user_id=telegram_user_id)
    return {
        "ok": True,
        "deleted": deleted,
        "users": registry.list_users(),
    }


async def bot_token(entrypoint: Any = None, *, integration_id: str = "") -> str:
    return await integration_secret_value(
        entrypoint,
        provider="telegram",
        field="bot_token",
        integration_id=integration_id,
    )


async def _bot_token_value(entrypoint: Any = None, *, integration_id: str = "") -> str:
    try:
        value = bot_token(entrypoint, integration_id=integration_id)
    except TypeError:
        value = bot_token()
    if inspect.isawaitable(value):
        value = await value
    return str(value or "")


def _request_header(request: Any, name: str) -> str:
    headers = getattr(request, "headers", None)
    if headers is not None:
        try:
            return str(headers.get(name) or "").strip()
        except Exception:
            pass
    if isinstance(request, dict):
        raw_headers = request.get("headers")
        if isinstance(raw_headers, dict):
            for key, value in raw_headers.items():
                if str(key or "").lower() == name.lower():
                    return str(value or "").strip()
    return ""


def _request_query_value(request: Any, *names: str) -> str:
    query = getattr(request, "query_params", None)
    for name in names:
        if query is not None:
            try:
                value = str(query.get(name) or "").strip()
                if value:
                    return value
            except Exception:
                pass
        if isinstance(request, dict):
            raw_query = request.get("query") or request.get("query_params")
            if isinstance(raw_query, dict):
                for key, value in raw_query.items():
                    if str(key or "").lower() == name.lower() and str(value or "").strip():
                        return str(value or "").strip()
    return ""


def _webhook_integration_id(request: Any) -> str:
    return (
        _request_header(request, "X-KDCube-Auth-Integration-ID")
        or _request_query_value(request, "integration_id", "auth_integration_id", "kdcube_auth_integration_id")
    )


async def _resolve_webhook_integration_id(entrypoint: Any = None, *, request: Any) -> str:
    header_name = "X-Telegram-Bot-Api-Secret-Token"
    provided = _request_header(request, header_name)
    if not provided:
        raise HTTPException(status_code=401, detail="telegram_webhook_secret_missing")
    integration_value = _webhook_integration_id(request)
    if integration_value:
        rows = [select_integration(entrypoint, provider="telegram", integration_id=integration_value)]
    else:
        rows = configured_integrations(entrypoint, provider="telegram")
    configured = False
    for row in rows:
        if not row or row.get("enabled") is False:
            continue
        row_id = str(row.get("id") or "").strip()
        if not row_id:
            continue
        value = await integration_secret_value(
            entrypoint,
            provider="telegram",
            field="webhook_secret",
            integration_id=row_id,
        )
        if value:
            configured = True
            if hmac.compare_digest(provided, value):
                return row_id
    if not configured:
        log.warning(
            "[%s] telegram webhook rejected: no webhook secrets configured integration_id=%s",
            _bundle_id(entrypoint),
            integration_value or "<fallback>",
        )
        raise HTTPException(status_code=503, detail="telegram_webhook_secret_not_configured")
    log.warning(
        "[%s] telegram webhook rejected: invalid webhook secret integration_id=%s",
        _bundle_id(entrypoint),
        integration_value or "<fallback>",
    )
    raise HTTPException(status_code=401, detail="telegram_webhook_secret_invalid")


def _role_allows_access(role: str) -> bool:
    return str(role or "").strip().lower() in {"registered", "admin"}


def _telegram_command_kind_and_text(text: str) -> tuple[str, str]:
    return sdk_telegram_command_kind_and_text(text)


def _raw_attachments_from_telegram(attachments: list[Dict[str, Any]]) -> list[RawAttachment]:
    return sdk_raw_attachments_from_telegram(attachments)


def _telegram_payload_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "update_id": summary.get("update_id"),
        "update_type": summary.get("update_type"),
        "message_id": summary.get("message_id"),
        "chat_id": summary.get("chat_id"),
        "chat_type": summary.get("chat_type"),
        "user_id": summary.get("user_id"),
        "username": summary.get("username"),
        "message_thread_id": summary.get("message_thread_id"),
        "is_topic_message": bool(summary.get("is_topic_message")),
        "topic_event": summary.get("topic_event"),
        "topic_name": summary.get("topic_name"),
        "integration_id": summary.get("integration_id"),
        "attachments": _attachment_log_items(list(summary.get("attachments") or [])),
    }


def _telegram_definition_prop(entrypoint: Any, key: str, default: Any = None, *, integration_id: str = "") -> Any:
    return integration_definition_value(
        entrypoint,
        provider="telegram",
        key=key,
        default=default,
        integration_id=integration_id,
    )


def _telegram_target_agent_id(entrypoint: Any) -> str:
    bundle_prop = getattr(entrypoint, "bundle_prop", None)
    configured = ""
    if callable(bundle_prop):
        configured = str(bundle_prop("surfaces.as_consumer.default_agent", "") or "").strip()
    return normalize_agent_id(configured, default=DEFAULT_REACT_AGENT_ID)


async def submit_telegram_turn(entrypoint: Any, *, summary: Dict[str, Any]) -> Dict[str, Any]:
    chat_submitter = getattr(entrypoint, "chat_submitter", None)
    submit = getattr(chat_submitter, "submit", None)
    bundle_id = _bundle_id(entrypoint)

    text = str(summary.get("text") or "").strip()
    attachments = list(summary.get("attachments") or [])
    if not text and attachments:
        text = (
            "The user sent Telegram attachment(s) without text. "
            "Inspect the attachment(s), describe what is present, and ask a focused follow-up if the user intent is unclear."
        )
    if not text and not attachments and not summary.get("message_thread_id"):
        return {"mode": "empty", "accepted": True}

    chat_id = str(summary.get("chat_id") or "unknown").strip()
    update_id = str(summary.get("update_id") or uuid.uuid4().hex).strip()
    telegram_user_id = str(summary.get("user_id") or chat_id or "anonymous").strip()
    telegram_identity = storage(entrypoint).resolve_telegram_user(
        telegram_user_id=telegram_user_id,
        telegram_chat_id=chat_id,
        telegram_username=str(summary.get("username") or "").strip(),
    )
    kdcube_user_id = str(telegram_identity.get("kdcube_user_id") or "").strip()
    role = str(telegram_identity.get("role") or "anonymous").strip().lower() or "anonymous"
    conversation_resolution = storage(entrypoint).resolve_conversation_for_message(
        telegram_user_id=telegram_user_id,
        telegram_chat_id=chat_id,
        telegram_username=str(summary.get("username") or "").strip(),
        message_thread_id=summary.get("message_thread_id"),
        integration_id=str(summary.get("integration_id") or "").strip(),
        topic_name=str(summary.get("topic_name") or "").strip(),
    )
    if not conversation_resolution.get("ok", False):
        raise RuntimeError(
            str(
                (conversation_resolution.get("error") or {}).get("message")
                or "Telegram conversation could not be resolved."
            )
        )
    conversation_id = str(conversation_resolution.get("conversation_id") or "").strip()
    if not conversation_id:
        conversation_id = (
            str(telegram_identity.get("conversation_id") or "").strip()
            or f"telegram_chat_{chat_id}"
        )
    if not text and not attachments:
        return {
            "mode": "topic_event",
            "accepted": True,
            "conversation_id": conversation_id,
            "telegram_identity": telegram_identity,
            "topic_bound": bool(conversation_resolution.get("topic_bound")),
        }
    turn_id = new_turn_id()
    actor_user_id = f"telegram_{telegram_user_id}"
    identity_authority = await _telegram_platform_authority(
        entrypoint,
        actor_user_id=actor_user_id,
        telegram_user_id=telegram_user_id,
        local_role=role,
        source="telegram.webhook",
    )
    if not str(identity_authority.get("platform_user_id") or "").strip():
        return {
            "mode": "connection_required",
            "accepted": True,
            "reason": "telegram_platform_identity_unresolved",
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "telegram_identity": telegram_identity,
        }
    if not callable(submit):
        return {
            "mode": "unavailable",
            "accepted": False,
            "reason": "chat_submitter_unavailable",
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "telegram_identity": telegram_identity,
        }

    comm_context = getattr(entrypoint, "comm_context", None)
    if not comm_context:
        return {
            "mode": "unavailable",
            "accepted": False,
            "reason": "comm_context_unavailable",
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "telegram_identity": telegram_identity,
        }

    tenant = str(getattr(getattr(comm_context, "actor", None), "tenant_id", "") or get_settings().TENANT or "").strip()
    project = str(getattr(getattr(comm_context, "actor", None), "project_id", "") or get_settings().PROJECT or "").strip()
    telegram_command_type, processed_text = _telegram_command_kind_and_text(text)
    agent_id = _telegram_target_agent_id(entrypoint)
    runtime_label = _authority_runtime_label(identity_authority)
    request_context = RequestContext(
        client_ip="telegram",
        user_agent="Telegram Bot API",
        user_timezone=str(getattr(getattr(comm_context, "user", None), "timezone", "") or "UTC"),
    )
    session = UserSession(
        session_id=conversation_id,
        user_type=UserType(runtime_label),
        user_id=actor_user_id,
        username=str(summary.get("username") or "").strip(),
        roles=list(identity_authority.get("platform_roles") or []),
        permissions=list(identity_authority.get("platform_permissions") or []),
        timezone=request_context.user_timezone,
        request_context=request_context,
        identity_authority=identity_authority,
    )
    telegram_payload = _telegram_payload_summary(summary)
    telegram_payload.update(
        {
            "kdcube_user_id": kdcube_user_id,
            "role": role,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "topic_bound": bool(conversation_resolution.get("topic_bound")),
        }
    )
    payload: Dict[str, Any] = {
        "source": "telegram",
        "telegram": telegram_payload,
        "agent_id": agent_id,
    }
    message_data: Dict[str, Any] = {
        "tenant": tenant,
        "project": project,
        "bundle_id": bundle_id,
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "agent_id": agent_id,
        "payload": payload,
    }
    text_event_type = (
        "event.user.steer"
        if telegram_command_type == "steer"
        else "event.user.followup"
        if telegram_command_type == "followup"
        else "event.user.prompt"
    )
    message_data["external_events"] = _telegram_external_events(
        text=processed_text,
        attachments=attachments,
        turn_id=turn_id,
        text_event_type=text_event_type,
        agent_id=agent_id,
    )

    ingress = IngressConfig(
        transport="telegram",
        entrypoint="/telegram/webhook",
        component="chat.telegram",
        instance_id=str(getattr(getattr(comm_context, "meta", None), "instance_id", "") or "telegram-webhook"),
        stream_id=None,
        metadata={
            "source": "telegram",
            "chat_id": chat_id,
            "update_id": update_id,
            "message_id": summary.get("message_id"),
            "message_thread_id": summary.get("message_thread_id"),
            "entrypoint": "/telegram/webhook",
        },
    )
    raw_attachments = _raw_attachments_from_telegram(attachments)
    result = await submit(
        session=session,
        request_context=request_context,
        message_data=message_data,
        message_text=processed_text,
        ingress=ingress,
        raw_attachments=raw_attachments,
    )
    result_payload = asdict(result)
    log.info(
        "[%s] telegram submitter result | update_id=%s conversation_id=%s turn_id=%s ok=%s reason=%s error_type=%s is_continuation=%s attachments=%s",
        bundle_id,
        update_id,
        conversation_id,
        turn_id,
        result.ok,
        result.reason or "",
        result.error_type or "",
        bool(result.is_continuation),
        len(raw_attachments),
    )
    return {
        "mode": "submitted",
        "accepted": bool(result.ok),
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "telegram_identity": telegram_identity,
        "topic_bound": bool(conversation_resolution.get("topic_bound")),
        "ingress": result_payload,
    }


def _queued_telegram_meta(entrypoint: Any) -> Dict[str, Any]:
    comm_context = getattr(entrypoint, "comm_context", None)
    request_payload = getattr(getattr(comm_context, "request", None), "payload", None)
    if not isinstance(request_payload, dict):
        return {}
    telegram = request_payload.get("telegram")
    if not isinstance(telegram, dict):
        return {}
    chat_id = str(telegram.get("chat_id") or "").strip()
    if not chat_id:
        return {}
    return telegram


async def run_with_queued_telegram_delivery(entrypoint: Any, *, runner: Any) -> Dict[str, Any]:
    telegram_meta = _queued_telegram_meta(entrypoint)
    if not telegram_meta:
        return await runner()
    bundle_id = _bundle_id(entrypoint)

    chat_id = str(telegram_meta.get("chat_id") or "").strip()
    message_thread_id = telegram_meta.get("message_thread_id")
    update_id = str(telegram_meta.get("update_id") or "").strip()
    integration_id = str(telegram_meta.get("integration_id") or "").strip()
    comm_context = getattr(entrypoint, "comm_context", None)
    turn_id = str(
        telegram_meta.get("turn_id")
        or getattr(getattr(comm_context, "routing", None), "turn_id", "")
        or ""
    ).strip()
    stream_enabled = bool(
        _telegram_definition_prop(entrypoint, "stream_activity", True, integration_id=integration_id)
        and _telegram_definition_prop(entrypoint, "send_responses", True, integration_id=integration_id)
    )
    stream_show_progress = bool(
        _telegram_definition_prop(entrypoint, "stream_activity_display", True, integration_id=integration_id)
    )
    async with TelegramActivityStreamer(
        comm=getattr(entrypoint, "comm", None),
        bot_token=await _bot_token_value(entrypoint, integration_id=integration_id),
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        turn_id=turn_id,
        enabled=stream_enabled,
        show_progress=stream_show_progress,
    ) as telegram_streamer:
        result = await runner()
    if not isinstance(result, dict):
        result = {}
    delivery = await deliver_turn_to_telegram(
        bundle_id=bundle_id,
        bot_token=await _bot_token_value(entrypoint, integration_id=integration_id),
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        update_id=update_id,
        turn_result=result,
        delivered_file_keys=telegram_streamer.delivered_file_keys() if telegram_streamer else set(),
        progress_message_id=telegram_streamer.progress_message_id() if telegram_streamer else None,
        progress_summary=telegram_streamer.progress_summary() if telegram_streamer else "",
        send_responses=bool(_telegram_definition_prop(entrypoint, "send_responses", True, integration_id=integration_id)),
    )
    result["telegram"] = {
        "queued_delivery": True,
        "chat_id": chat_id,
        "message_thread_id": message_thread_id,
        "update_id": update_id,
        **delivery,
    }
    return result


async def handle_webhook(entrypoint: Any, request: Any = None, **update) -> Dict[str, Any]:
    integration_id = ""
    if request is not None:
        integration_id = await _resolve_webhook_integration_id(entrypoint, request=request)
    summary = summarize_telegram_update(update)
    if integration_id:
        summary["integration_id"] = integration_id
    telegram_store = storage(entrypoint)
    update_id = str(summary.get("update_id") or "").strip()
    bundle_id = _bundle_id(entrypoint)
    log.info(
        "[%s] telegram update extracted | update_id=%s type=%s chat_id=%s message_thread_id=%s user_id=%s username=%s text_chars=%s attachments=%s",
        bundle_id,
        summary.get("update_id"),
        summary.get("update_type"),
        summary.get("chat_id"),
        summary.get("message_thread_id"),
        summary.get("user_id"),
        summary.get("username") or "",
        len(str(summary.get("text") or "")),
        _attachment_log_items(list(summary.get("attachments") or [])),
    )
    claim = await asyncio.to_thread(telegram_store.claim_telegram_update, update_id=update_id)
    if not claim.get("claimed"):
        log.info(
            "[%s] telegram update ignored | update_id=%s claim_status=%s",
            bundle_id,
            update_id,
            claim.get("status"),
        )
        return {
            "ok": True,
            "accepted": True,
            "stage": "duplicate-update",
            "summary": summary,
            "idempotency": claim,
        }

    # Webhook preparation and submission deliberately do not use a process-local
    # conversation lock. Scaled replicas coordinate admission through shared chat
    # ingress, conversation-state CAS, and the retained event lane. In particular,
    # `/stop` must be able to reach ingress while the current turn is still live.
    try:
        if summary.get("attachments"):
            log.info(
                "[%s] telegram attachments hydrate start | update_id=%s attachments=%s",
                bundle_id,
                update_id,
                _attachment_log_items(list(summary.get("attachments") or [])),
            )
            summary["attachments"] = await hydrate_telegram_attachments(
                attachments=list(summary.get("attachments") or []),
                bot_token=await _bot_token_value(entrypoint, integration_id=str(summary.get("integration_id") or "")),
                message_id=summary.get("message_id"),
            )
            log.info(
                "[%s] telegram attachments hydrate finished | update_id=%s attachments=%s",
                bundle_id,
                update_id,
                _attachment_log_items(list(summary.get("attachments") or [])),
            )
        submitted_turn = await submit_telegram_turn(entrypoint, summary=summary)
    except Exception as exc:
        await asyncio.to_thread(telegram_store.fail_telegram_update, update_id=update_id, error=str(exc))
        raise

    mode = str(submitted_turn.get("mode") or "").strip()
    if mode in {"connection_required", "unavailable"}:
        turn_id = str(submitted_turn.get("turn_id") or new_turn_id()).strip()
        response_turn = (
            _telegram_connect_answer(turn_id)
            if mode == "connection_required"
            else _telegram_ingress_unavailable_answer(turn_id)
        )
        try:
            delivery_result = await deliver_turn_to_telegram(
                bundle_id=bundle_id,
                bot_token=await _bot_token_value(entrypoint, integration_id=str(summary.get("integration_id") or "")),
                chat_id=summary.get("chat_id") or "",
                message_thread_id=summary.get("message_thread_id"),
                update_id=update_id,
                turn_result=response_turn,
                send_responses=bool(
                    _telegram_definition_prop(
                        entrypoint,
                        "send_responses",
                        True,
                        integration_id=str(summary.get("integration_id") or ""),
                    )
                ),
            )
        except Exception as exc:
            await asyncio.to_thread(telegram_store.fail_telegram_update, update_id=update_id, error=str(exc))
            raise
        telegram_messages = list(delivery_result.get("messages") or [])
        result_payload = {
            "ok": mode == "connection_required",
            "accepted": bool(submitted_turn.get("accepted")),
            "stage": "connection-required" if mode == "connection_required" else "chat-ingress-unavailable",
            "reason": submitted_turn.get("reason"),
            "summary": _telegram_payload_summary(summary),
            "chat_ingress": None,
            "telegram_response": {
                "text": telegram_messages[0].get("text") if telegram_messages else "",
                "messages": telegram_messages,
                "files": [],
            },
            "telegram_delivery": delivery_result.get("telegram_delivery"),
        }
        log.warning(
            "[%s] telegram update not submitted | update_id=%s mode=%s reason=%s",
            bundle_id,
            update_id,
            mode,
            submitted_turn.get("reason") or "",
        )
        await asyncio.to_thread(telegram_store.complete_telegram_update, update_id=update_id, result=result_payload)
        return result_payload

    ingress = submitted_turn.get("ingress") if isinstance(submitted_turn.get("ingress"), dict) else {}
    stage = "webhook-ack"
    if mode == "topic_event":
        stage = "topic-bound"
    elif mode == "submitted":
        ingress_reason = str(ingress.get("reason") or "")
        if ingress_reason.startswith("active_turn_control_"):
            stage = "telegram-control-noop"
        elif ingress_reason.endswith("_accepted") and bool(ingress.get("is_continuation")):
            stage = "telegram-continuation"
        else:
            stage = "queued-turn" if submitted_turn.get("accepted") else "submit-rejected"
    result_payload = {
        "ok": True,
        "accepted": bool(submitted_turn.get("accepted", True)),
        "stage": stage,
        "summary": _telegram_payload_summary(summary),
        "chat_ingress": submitted_turn,
        "telegram_response": None,
        "telegram_delivery": None,
    }
    await asyncio.to_thread(telegram_store.complete_telegram_update, update_id=update_id, result=result_payload)
    return result_payload
