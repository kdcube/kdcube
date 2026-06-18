from __future__ import annotations

import logging
import uuid
import asyncio
import base64
import binascii
import inspect
import threading
from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable, Dict

from kdcube_ai_app.apps.chat.ids import new_turn_id
from kdcube_ai_app.apps.chat.ingress.ingress_core import IngressConfig, RawAttachment
from kdcube_ai_app.apps.chat.sdk.event_identity import (
    DEFAULT_REACT_AGENT_ID,
    build_event_logical_path,
)
from kdcube_ai_app.auth.sessions import RequestContext, UserSession, UserType
from kdcube_ai_app.apps.chat.sdk.config import get_secret, get_settings
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bundle_registry import (
    configured_bundle_id,
    register_config,
    resolve_config,
)
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    bind_current_request_context,
    get_current_bundle_id,
    get_current_request_context,
)
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    TelegramMessage,
    TelegramActivityStreamer,
    deliver_react_turn_to_telegram,
    hydrate_telegram_attachments,
    raw_attachments_from_telegram as sdk_raw_attachments_from_telegram,
    role_to_user_type as sdk_role_to_user_type,
    send_telegram_messages,
    summarize_telegram_update,
    telegram_command_kind_and_text as sdk_telegram_command_kind_and_text,
)

BUNDLE_ID = ""
log = logging.getLogger(__name__)

_storage_factory: Callable[[Any], Any] | None = None
_storage_root_or_error: Callable[[Any], Any] | None = None
_migrate_telegram_user_to_kdcube_scope: Callable[..., Any] | None = None
_CONFIGS: Dict[str, Dict[str, Any]] = {}
_conversation_locks_guard = threading.Lock()
_conversation_locks: dict[str, asyncio.Lock] = {}


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


def _telegram_external_events(
    *,
    text: str,
    attachments: list[Dict[str, Any]],
    turn_id: str,
    text_event_type: str = "event.user.prompt",
) -> list[Dict[str, Any]]:
    events: list[Dict[str, Any]] = []

    def _event_path(event_id: str) -> str:
        return build_event_logical_path(turn_id=turn_id, event_path=event_id)

    def _timestamp() -> str:
        return datetime.utcnow().isoformat() + "Z"

    if text:
        normalized_type = str(text_event_type or "event.user.prompt").strip() or "event.user.prompt"
        event_suffix = normalized_type.rsplit(".", 1)[-1] or "prompt"
        event_id = f"telegram.{event_suffix}.{uuid.uuid4().hex[:12]}"
        events.append(
            {
                "event_id": event_id,
                "type": normalized_type,
                "event_source_id": f"telegram.user.{event_suffix}",
                "logical_path": _event_path(event_id),
                "reactive": True,
                "agent_id": DEFAULT_REACT_AGENT_ID,
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
                "agent_id": DEFAULT_REACT_AGENT_ID,
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


def _scope_prefix(entrypoint: Any) -> str:
    comm_context = getattr(entrypoint, "comm_context", None)
    tenant = str(getattr(getattr(comm_context, "actor", None), "tenant_id", "") or get_settings().TENANT or "").strip()
    project = str(getattr(getattr(comm_context, "actor", None), "project_id", "") or get_settings().PROJECT or "").strip()
    return f"{tenant or 'tenant'}:{project or 'project'}"


def _telegram_conversation_lock_key(entrypoint: Any, summary: Dict[str, Any]) -> str:
    chat_id = str(summary.get("chat_id") or "unknown").strip()
    telegram_user_id = str(summary.get("user_id") or chat_id or "anonymous").strip()
    conversation_id = str(summary.get("conversation_id") or "").strip()
    if not conversation_id:
        try:
            identity = storage(entrypoint).resolve_telegram_user(
                telegram_user_id=telegram_user_id,
                telegram_chat_id=chat_id,
                telegram_username=str(summary.get("username") or "").strip(),
                create_if_missing=False,
            )
            conversation_id = str(identity.get("conversation_id") or "").strip()
        except Exception:
            conversation_id = ""
    return f"{_scope_prefix(entrypoint)}:{conversation_id or f'telegram_chat_{chat_id}'}"


def _telegram_conversation_lock(key: str) -> asyncio.Lock:
    loop_key = f"{id(asyncio.get_running_loop())}:{key}"
    with _conversation_locks_guard:
        lock = _conversation_locks.get(loop_key)
        if lock is None:
            lock = asyncio.Lock()
            _conversation_locks[loop_key] = lock
        return lock


def _decode_inline_attachment_bytes(item: Dict[str, Any]) -> bytes | None:
    value = item.get("base64")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return base64.b64decode(value, validate=False)
    except (binascii.Error, ValueError):
        return None


def _conversation_store(entrypoint: Any) -> Any:
    store = getattr(entrypoint, "store", None) or getattr(entrypoint, "_store", None)
    if store:
        return store
    settings = getattr(entrypoint, "settings", None) or get_settings()
    storage_path = getattr(settings, "STORAGE_PATH", None)
    if not storage_path:
        return None
    store = ConversationStore(storage_path)
    try:
        setattr(entrypoint, "_store", store)
    except Exception:
        pass
    return store


async def _host_telegram_attachments(
    entrypoint: Any,
    *,
    attachments: list[Dict[str, Any]],
    tenant: str,
    project: str,
    user_id: str,
    user_type: str,
    conversation_id: str,
    turn_id: str,
) -> list[Dict[str, Any]]:
    """Persist Telegram upload bytes into conversation attachment storage before React sees them."""
    if not attachments:
        return []
    bundle_id = _bundle_id(entrypoint)
    store = _conversation_store(entrypoint)
    if not store:
        raise RuntimeError("telegram attachment hosting failed: conversation store is unavailable")

    hosted: list[Dict[str, Any]] = []
    log.info(
        "[%s] telegram attachments host start | conversation_id=%s turn_id=%s attachments=%s",
        bundle_id,
        conversation_id,
        turn_id,
        _attachment_log_items(attachments),
    )
    for index, raw in enumerate(attachments):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if item.get("hosted_uri") or item.get("key") or item.get("rn"):
            hosted.append(item)
            continue
        data = _decode_inline_attachment_bytes(item)
        if data is None:
            item["error"] = item.get("error") or "telegram_attachment_bytes_missing"
            hosted.append(item)
            continue

        filename = str(item.get("filename") or item.get("file_name") or "").strip() or f"telegram_attachment_{index + 1}.bin"
        mime = str(item.get("mime_type") or item.get("mime") or "").strip() or "application/octet-stream"
        uri, key, rn = await store.put_attachment(
            tenant=tenant,
            project=project,
            user=user_id,
            fingerprint=None,
            conversation_id=conversation_id,
            turn_id=turn_id,
            role="user",
            filename=filename,
            data=data,
            mime=mime,
            user_type=user_type,
            origin="user",
        )
        item.update(
            {
                "filename": filename,
                "mime": mime,
                "mime_type": mime,
                "size": len(data),
                "size_bytes": len(data),
                "hosted_uri": uri,
                "key": key,
                "rn": rn,
                "role": "user",
                "origin": "telegram",
            }
        )
        item.pop("base64", None)
        item.pop("file_name", None)
        hosted.append(item)
    log.info(
        "[%s] telegram attachments host finished | conversation_id=%s turn_id=%s attachments=%s",
        bundle_id,
        conversation_id,
        turn_id,
        _attachment_log_items(hosted),
    )
    return hosted


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
            bot_token=await _bot_token_value(entrypoint),
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


async def bot_token(entrypoint: Any = None) -> str:
    bundle_id = _bundle_id(entrypoint)
    return (
        await get_secret("b:integrations.telegram.bot_token")
        or await get_secret(f"bundles.{bundle_id}.secrets.integrations.telegram.bot_token")
        or ""
    )


async def _bot_token_value(entrypoint: Any = None) -> str:
    try:
        value = bot_token(entrypoint)
    except TypeError:
        value = bot_token()
    if inspect.isawaitable(value):
        value = await value
    return str(value or "")


def _role_to_user_type(role: str) -> UserType:
    return sdk_role_to_user_type(role)


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
        "attachments": _attachment_log_items(list(summary.get("attachments") or [])),
    }


def _bundle_prop(entrypoint: Any, path: str, default: Any = None) -> Any:
    fn = getattr(entrypoint, "bundle_prop", None)
    return fn(path, default) if callable(fn) else default


async def submit_react_turn(entrypoint: Any, *, summary: Dict[str, Any]) -> Dict[str, Any] | None:
    chat_submitter = getattr(entrypoint, "chat_submitter", None)
    submit = getattr(chat_submitter, "submit", None)
    if not callable(submit):
        return None
    bundle_id = _bundle_id(entrypoint)

    text = str(summary.get("text") or "").strip()
    attachments = list(summary.get("attachments") or [])
    if not text and attachments:
        text = (
            "The user sent Telegram attachment(s) without text. "
            "Inspect the attachment(s), describe what is present, and ask a focused follow-up if the user intent is unclear."
        )
    if not text and not attachments:
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
    if role not in {"registered", "admin"}:
        return None

    comm_context = getattr(entrypoint, "comm_context", None)
    if not comm_context:
        return None

    tenant = str(getattr(getattr(comm_context, "actor", None), "tenant_id", "") or get_settings().TENANT or "").strip()
    project = str(getattr(getattr(comm_context, "actor", None), "project_id", "") or get_settings().PROJECT or "").strip()
    conversation_id = (
        str(telegram_identity.get("conversation_id") or "").strip()
        or f"telegram_chat_{chat_id}"
    )
    turn_id = new_turn_id()
    telegram_command_type, processed_text = _telegram_command_kind_and_text(text)
    user_id = kdcube_user_id or f"telegram_{telegram_user_id}"
    request_context = RequestContext(
        client_ip="telegram",
        user_agent="Telegram Bot API",
        user_timezone=str(getattr(getattr(comm_context, "user", None), "timezone", "") or "UTC"),
    )
    session = UserSession(
        session_id=conversation_id,
        user_type=_role_to_user_type(role),
        user_id=user_id,
        username=str(summary.get("username") or "").strip(),
        roles=[role],
        permissions=[],
        timezone=request_context.user_timezone,
        request_context=request_context,
    )
    telegram_payload = _telegram_payload_summary(summary)
    telegram_payload.update(
        {
            "kdcube_user_id": kdcube_user_id,
            "role": role,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
        }
    )
    payload: Dict[str, Any] = {
        "source": "telegram",
        "telegram": telegram_payload,
    }
    message_data: Dict[str, Any] = {
        "tenant": tenant,
        "project": project,
        "bundle_id": bundle_id,
        "conversation_id": conversation_id,
        "turn_id": turn_id,
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
        "ingress": result_payload,
    }


async def run_react_turn(entrypoint: Any, *, summary: Dict[str, Any]) -> Dict[str, Any] | None:
    text = str(summary.get("text") or "").strip()
    attachments = list(summary.get("attachments") or [])
    comm_context = getattr(entrypoint, "comm_context", None)
    bundle_id = _bundle_id(entrypoint)
    if not text and not attachments:
        return None

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
    conversation_id = str(telegram_identity.get("conversation_id") or "").strip() or f"telegram_chat_{chat_id}"
    turn_id = new_turn_id()
    log.info(
        "[%s] telegram react turn resolved | update_id=%s chat_id=%s telegram_user_id=%s kdcube_user_id=%s role=%s conversation_id=%s turn_id=%s text_chars=%s attachments=%s",
        bundle_id,
        update_id,
        chat_id,
        telegram_user_id,
        kdcube_user_id or "",
        role,
        conversation_id,
        turn_id,
        len(text),
        _attachment_log_items(attachments),
    )
    if role not in {"registered", "admin"}:
        return {
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "telegram_identity": telegram_identity,
            "answer": (
                "Your Telegram user has been recorded. "
                "An admin must allow it in the Telegram Admin panel before this bot can process requests."
            ),
            "followups": [],
            "timeline": {
                "blocks": [
                    {
                        "path": f"tc:{turn_id}.telegram.access_pending",
                        "type": "answer",
                        "text": (
                            "Your Telegram user has been recorded. "
                            "An admin must allow it in the Telegram Admin panel before this bot can process requests."
                        ),
                    }
                ],
                "sources_pool": [],
            },
            "authorization": "pending_admin_approval",
        }
    if not comm_context:
        return None
    telegram_command_type, processed_text = _telegram_command_kind_and_text(text)
    text_event_type = (
        "event.user.steer"
        if telegram_command_type == "steer"
        else "event.user.followup"
        if telegram_command_type == "followup"
        else "event.user.prompt"
    )

    scoped_ctx = comm_context.model_copy(deep=True)
    scoped_ctx.routing.session_id = conversation_id
    scoped_ctx.routing.conversation_id = conversation_id
    scoped_ctx.routing.turn_id = turn_id
    scoped_ctx.user.user_id = kdcube_user_id or f"telegram_{telegram_user_id}"
    scoped_ctx.user.username = str(summary.get("username") or scoped_ctx.user.username or "")
    scoped_ctx.user.user_type = role

    async def _run_scoped_telegram_turn() -> Dict[str, Any]:
        nonlocal attachments
        if attachments:
            attachments = await _host_telegram_attachments(
                entrypoint,
                attachments=attachments,
                tenant=scoped_ctx.actor.tenant_id,
                project=scoped_ctx.actor.project_id,
                user_id=scoped_ctx.user.user_id,
                user_type=scoped_ctx.user.user_type,
                conversation_id=conversation_id,
                turn_id=turn_id,
            )
            summary["attachments"] = attachments
        external_events = _telegram_external_events(
            text=processed_text,
            attachments=attachments,
            turn_id=turn_id,
            text_event_type=text_event_type,
        )

        state = entrypoint.create_initial_state(
            {
                "request_id": scoped_ctx.request.request_id or str(uuid.uuid4()),
                "tenant": scoped_ctx.actor.tenant_id,
                "project": scoped_ctx.actor.project_id,
                "user": scoped_ctx.user.user_id,
                "user_type": scoped_ctx.user.user_type,
                "session_id": conversation_id,
                "conversation_id": conversation_id,
                "turn_id": turn_id,
                "external_events": external_events,
                "message_kind": telegram_command_type,
            }
        )
        state["turn_id"] = turn_id
        entrypoint.set_state(state)
        stream_enabled = bool(
            _bundle_prop(entrypoint, "integrations.telegram.stream_activity", True)
            and _bundle_prop(entrypoint, "integrations.telegram.send_responses", True)
        )
        async with TelegramActivityStreamer(
            comm=getattr(entrypoint, "comm", None),
            bot_token=await _bot_token_value(entrypoint),
            chat_id=chat_id,
            turn_id=turn_id,
            enabled=stream_enabled,
        ) as telegram_streamer:
            result = await entrypoint.run(external_events=external_events)
        delivered_file_keys = telegram_streamer.delivered_file_keys() if telegram_streamer else set()
        progress_message_id = telegram_streamer.progress_message_id() if telegram_streamer else None
        progress_summary = telegram_streamer.progress_summary() if telegram_streamer else ""
        turn_log = (result or {}).get("turn_log") if isinstance((result or {}).get("turn_log"), dict) else {}
        timeline = (result or {}).get("timeline") if isinstance((result or {}).get("timeline"), dict) else {}
        log.info(
            "[%s] telegram react turn completed | update_id=%s conversation_id=%s turn_id=%s answer_chars=%s followups=%s turn_log_blocks=%s timeline_blocks=%s timeline_sources=%s",
            bundle_id,
            update_id,
            conversation_id,
            turn_id,
            len(str((result or {}).get("final_answer") or "")),
            len((result or {}).get("followups") or []),
            len(turn_log.get("blocks") or []) if isinstance(turn_log, dict) else 0,
            len(timeline.get("blocks") or []) if isinstance(timeline, dict) else 0,
            len(timeline.get("sources_pool") or []) if isinstance(timeline, dict) else 0,
        )
        return {
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "telegram_identity": telegram_identity,
            "answer": (result or {}).get("final_answer") or "",
            "followups": (result or {}).get("followups") or [],
            "turn_log": turn_log,
            "timeline": timeline,
            "telegram_delivered_file_keys": sorted(delivered_file_keys),
            "telegram_progress_message_id": progress_message_id,
            "telegram_progress_summary": progress_summary,
        }

    binder = getattr(entrypoint, "bind_request_context", None)
    if callable(binder):
        with binder(comm_context=scoped_ctx):
            with bind_current_request_context(scoped_ctx, comm=getattr(entrypoint, "comm", None)):
                return await _run_scoped_telegram_turn()

    entrypoint.rebind_request_context(comm_context=scoped_ctx)
    with bind_current_request_context(scoped_ctx, comm=getattr(entrypoint, "comm", None)):
        return await _run_scoped_telegram_turn()


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
    update_id = str(telegram_meta.get("update_id") or "").strip()
    comm_context = getattr(entrypoint, "comm_context", None)
    turn_id = str(
        telegram_meta.get("turn_id")
        or getattr(getattr(comm_context, "routing", None), "turn_id", "")
        or ""
    ).strip()
    lock_key = _telegram_conversation_lock_key(entrypoint, telegram_meta)
    stream_enabled = bool(
        _bundle_prop(entrypoint, "integrations.telegram.stream_activity", True)
        and _bundle_prop(entrypoint, "integrations.telegram.send_responses", True)
    )
    async with _telegram_conversation_lock(lock_key):
        async with TelegramActivityStreamer(
            comm=getattr(entrypoint, "comm", None),
            bot_token=await _bot_token_value(entrypoint),
            chat_id=chat_id,
            turn_id=turn_id,
            enabled=stream_enabled,
        ) as telegram_streamer:
            result = await runner()
        if not isinstance(result, dict):
            result = {}
        delivery = await deliver_react_turn_to_telegram(
            bundle_id=bundle_id,
            bot_token=await _bot_token_value(entrypoint),
            chat_id=chat_id,
            update_id=update_id,
            react_turn=result,
            delivered_file_keys=telegram_streamer.delivered_file_keys() if telegram_streamer else set(),
            progress_message_id=telegram_streamer.progress_message_id() if telegram_streamer else None,
            progress_summary=telegram_streamer.progress_summary() if telegram_streamer else "",
            send_responses=bool(_bundle_prop(entrypoint, "integrations.telegram.send_responses", True)),
        )
    result["telegram"] = {
        "queued_delivery": True,
        "chat_id": chat_id,
        "update_id": update_id,
        **delivery,
    }
    return result


async def handle_webhook(entrypoint: Any, **update) -> Dict[str, Any]:
    summary = summarize_telegram_update(update)
    telegram_store = storage(entrypoint)
    update_id = str(summary.get("update_id") or "").strip()
    bundle_id = _bundle_id(entrypoint)
    log.info(
        "[%s] telegram update extracted | update_id=%s type=%s chat_id=%s user_id=%s username=%s text_chars=%s attachments=%s",
        bundle_id,
        summary.get("update_id"),
        summary.get("update_type"),
        summary.get("chat_id"),
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

    lock_key = _telegram_conversation_lock_key(entrypoint, summary)
    turn_lock = _telegram_conversation_lock(lock_key)
    await turn_lock.acquire()
    lock_held = True
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
                bot_token=await _bot_token_value(entrypoint),
                message_id=summary.get("message_id"),
            )
            log.info(
                "[%s] telegram attachments hydrate finished | update_id=%s attachments=%s",
                bundle_id,
                update_id,
                _attachment_log_items(list(summary.get("attachments") or [])),
            )
        submitted_turn = await submit_react_turn(entrypoint, summary=summary)
        if submitted_turn is not None:
            ingress = submitted_turn.get("ingress") if isinstance(submitted_turn.get("ingress"), dict) else {}
            stage = "webhook-ack"
            if submitted_turn.get("mode") == "submitted":
                stage = (
                    "telegram-continuation"
                    if str(ingress.get("reason") or "").endswith("_accepted")
                    and bool(ingress.get("is_continuation"))
                    else "queued-react-turn" if submitted_turn.get("accepted") else "submit-rejected"
                )
            result_payload = {
                "ok": True,
                "accepted": bool(submitted_turn.get("accepted", True)),
                "stage": stage,
                "summary": _telegram_payload_summary(summary),
                "react_turn": None,
                "chat_ingress": submitted_turn,
                "telegram_response": None,
                "telegram_delivery": None,
            }
            turn_lock.release()
            lock_held = False
            await asyncio.to_thread(telegram_store.complete_telegram_update, update_id=update_id, result=result_payload)
            return result_payload
        react_turn = await run_react_turn(entrypoint, summary=summary)
        telegram_delivery = None
        telegram_messages: list[Dict[str, Any]] = []
        if react_turn:
            delivery_result = await deliver_react_turn_to_telegram(
                bundle_id=bundle_id,
                bot_token=await _bot_token_value(entrypoint),
                chat_id=summary.get("chat_id") or "",
                update_id=update_id,
                react_turn=react_turn,
                delivered_file_keys=set(react_turn.get("telegram_delivered_file_keys") or []) if isinstance(react_turn, dict) else set(),
                progress_message_id=react_turn.get("telegram_progress_message_id") if isinstance(react_turn, dict) else None,
                progress_summary=react_turn.get("telegram_progress_summary") if isinstance(react_turn, dict) else "",
                send_responses=bool(entrypoint.bundle_prop("integrations.telegram.send_responses", True)),
            )
            telegram_delivery = delivery_result.get("telegram_delivery")
            telegram_messages = list(delivery_result.get("messages") or [])
    except Exception as exc:
        if lock_held:
            turn_lock.release()
            lock_held = False
        await asyncio.to_thread(telegram_store.fail_telegram_update, update_id=update_id, error=str(exc))
        raise
    log.info(
        "[%s] telegram update accepted | update_id=%s type=%s chat_id=%s user_id=%s attachments=%s",
        bundle_id,
        summary.get("update_id"),
        summary.get("update_type"),
        summary.get("chat_id"),
        summary.get("user_id"),
        len(summary.get("attachments") or []),
    )
    result_payload = {
        "ok": True,
        "accepted": True,
        "stage": (
            "access-pending"
            if isinstance(react_turn, dict) and react_turn.get("authorization") == "pending_admin_approval"
            else "react-turn" if react_turn else "webhook-ack"
        ),
        "summary": summary,
        "react_turn": react_turn,
        "telegram_response": (
            {
                "text": telegram_messages[0].get("text") if telegram_messages else "",
                "messages": telegram_messages,
                "files": [
                    file_item
                    for message in telegram_messages
                    for file_item in (message.get("files") or [])
                ],
            }
            if react_turn
            else None
        ),
        "telegram_delivery": telegram_delivery,
    }
    if lock_held:
        turn_lock.release()
        lock_held = False
    await asyncio.to_thread(telegram_store.complete_telegram_update, update_id=update_id, result=result_payload)
    return result_payload
