from __future__ import annotations

from typing import Any, Dict, Optional

BUNDLE_ID = ""

memory_widgets: Any = None
settings_widgets: Any = None
task_widgets: Any = None
telegram_user_admin: Any = None


def configure_telegram_webapp(
    *,
    memory_widgets_module: Any,
    settings_widgets_module: Any,
    task_widgets_module: Any,
    telegram_user_admin_module: Any,
    bundle_id: str = "",
) -> None:
    """Bind bundle-owned widget modules used to build the common webapp payload."""
    global BUNDLE_ID, memory_widgets, settings_widgets, task_widgets, telegram_user_admin
    BUNDLE_ID = str(bundle_id or "").strip()
    memory_widgets = memory_widgets_module
    settings_widgets = settings_widgets_module
    task_widgets = task_widgets_module
    telegram_user_admin = telegram_user_admin_module


def _require_configured() -> None:
    if memory_widgets is None or settings_widgets is None or task_widgets is None or telegram_user_admin is None:
        raise RuntimeError("telegram webapp integration is not configured")


def _active_tab(widget_path: str = "") -> str:
    first = str(widget_path or "").strip("/").split("/", 1)[0].strip().lower()
    aliases = {
        "": "tasks",
        "task": "tasks",
        "tasks": "tasks",
        "memory": "memory",
        "memories": "memory",
        "settings": "settings",
        "chat": "conversations",
        "chats": "conversations",
        "conversation": "conversations",
        "conversations": "conversations",
        "admin": "telegram_admin",
        "telegram": "telegram_admin",
        "telegram-admin": "telegram_admin",
        "telegram_admin": "telegram_admin",
    }
    return aliases.get(first, "tasks")


def _effective_user_id(entrypoint: Any, user_id: Optional[str] = None) -> str:
    explicit = str(user_id or "").strip()
    if explicit:
        return explicit
    comm = getattr(entrypoint, "comm", None)
    return str(getattr(comm, "user_id", "") or "").strip()


def _mapping_required(user_id: str = "") -> Dict[str, Any]:
    return {
        "ok": False,
        "telegram_user_id": "",
        "kdcube_user_id": user_id,
        "active_conversation_id": "",
        "conversations": [],
        "count": 0,
        "auth_surface": "kdcube_widget",
        "error": {
            "code": "telegram_mapping_required",
            "message": "No Telegram user is linked to this KDCube user yet. Add the mapping in Telegram Admin first.",
        },
    }


def _linked_telegram_user(entrypoint: Any, *, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    _require_configured()
    target = _effective_user_id(entrypoint, user_id)
    if not target:
        return None
    for item in telegram_user_admin.storage(entrypoint).list_users():
        if str(item.get("kdcube_user_id") or "").strip() == target:
            return item
    return None


def _conversation_result_for_user(entrypoint: Any, *, user_id: Optional[str] = None) -> Dict[str, Any]:
    _require_configured()
    effective_user_id = _effective_user_id(entrypoint, user_id)
    user = _linked_telegram_user(entrypoint, user_id=effective_user_id)
    if not user:
        return _mapping_required(effective_user_id)
    listing = telegram_user_admin.storage(entrypoint).list_conversations(
        telegram_user_id=str(user.get("telegram_user_id") or ""),
        telegram_chat_id=str(user.get("telegram_chat_id") or ""),
        telegram_username=str(user.get("telegram_username") or ""),
        create_if_missing=False,
    )
    return {
        "ok": True,
        "telegram_user_id": str(user.get("telegram_user_id") or ""),
        "kdcube_user_id": effective_user_id,
        "active_conversation_id": listing.get("active_conversation_id") or "",
        "conversations": listing.get("conversations") or [],
        "count": len(listing.get("conversations") or []),
        "auth_surface": "kdcube_widget",
    }


def _payload_conversations(result: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "active_conversation_id": result.get("active_conversation_id") or "",
        "items": result.get("conversations") or [],
        "count": int(result.get("count") or 0),
        "telegram_user_id": result.get("telegram_user_id") or "",
        "kdcube_user_id": result.get("kdcube_user_id") or "",
    }
    if result.get("ok") is False:
        payload["error"] = result.get("error") or {
            "code": "conversation_unavailable",
            "message": "Conversations are unavailable.",
        }
    return payload


def _tenant_project(entrypoint: Any) -> tuple[str, str]:
    actor = getattr(getattr(entrypoint, "comm_context", None), "actor", None)
    return (
        str(getattr(actor, "tenant_id", "") or ""),
        str(getattr(actor, "project_id", "") or ""),
    )


async def list_conversations(
    entrypoint: Any,
    *,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    del fingerprint
    return _conversation_result_for_user(entrypoint, user_id=user_id)


async def create_conversation(
    entrypoint: Any,
    *,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    title: str = "",
) -> Dict[str, Any]:
    _require_configured()
    del fingerprint
    effective_user_id = _effective_user_id(entrypoint, user_id)
    user = _linked_telegram_user(entrypoint, user_id=effective_user_id)
    if not user:
        return _mapping_required(effective_user_id)
    result = telegram_user_admin.storage(entrypoint).create_conversation(
        telegram_user_id=str(user.get("telegram_user_id") or ""),
        telegram_chat_id=str(user.get("telegram_chat_id") or ""),
        telegram_username=str(user.get("telegram_username") or ""),
        title=title,
    )
    result["ok"] = True
    result["telegram_user_id"] = str(user.get("telegram_user_id") or "")
    result["kdcube_user_id"] = effective_user_id
    result["count"] = len(result.get("conversations") or [])
    result["auth_surface"] = "kdcube_widget"
    return result


async def switch_conversation(
    entrypoint: Any,
    *,
    conversation_id: str,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    _require_configured()
    del fingerprint
    effective_user_id = _effective_user_id(entrypoint, user_id)
    user = _linked_telegram_user(entrypoint, user_id=effective_user_id)
    if not user:
        return _mapping_required(effective_user_id)
    result = telegram_user_admin.storage(entrypoint).switch_conversation(
        telegram_user_id=str(user.get("telegram_user_id") or ""),
        conversation_id=conversation_id,
    )
    result.setdefault("ok", True)
    result["telegram_user_id"] = str(user.get("telegram_user_id") or "")
    result["kdcube_user_id"] = effective_user_id
    result["count"] = len(result.get("conversations") or [])
    result["auth_surface"] = "kdcube_widget"
    return result


async def delete_conversation(
    entrypoint: Any,
    *,
    conversation_id: str,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    delete_history: bool = True,
) -> Dict[str, Any]:
    _require_configured()
    del fingerprint
    effective_user_id = _effective_user_id(entrypoint, user_id)
    user = _linked_telegram_user(entrypoint, user_id=effective_user_id)
    if not user:
        result = _mapping_required(effective_user_id)
        result.update({"deleted": False, "deleted_conversation_id": "", "deleted_blobs": {}})
        return result
    registry_result = telegram_user_admin.storage(entrypoint).delete_conversation(
        telegram_user_id=str(user.get("telegram_user_id") or ""),
        conversation_id=conversation_id,
    )
    registry_result.setdefault("ok", True)
    deleted_blobs: Dict[str, int] = {}
    if registry_result.get("ok", True) and registry_result.get("deleted") and delete_history:
        tenant, project = _tenant_project(entrypoint)
        store = telegram_user_admin._conversation_store(entrypoint)
        if tenant and project and store:
            deleted_blobs = await store.delete_conversation(
                tenant=tenant,
                project=project,
                user_type=str(user.get("role") or "anonymous"),
                user_or_fp=effective_user_id or f"telegram_{user.get('telegram_user_id')}",
                conversation_id=conversation_id,
            )
    registry_result["telegram_user_id"] = str(user.get("telegram_user_id") or "")
    registry_result["kdcube_user_id"] = effective_user_id
    registry_result["count"] = len(registry_result.get("conversations") or [])
    registry_result["deleted_blobs"] = deleted_blobs
    registry_result["auth_surface"] = "kdcube_widget"
    return registry_result


async def payload(
    entrypoint: Any,
    *,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    mark_memory_seen: bool = False,
    widget_path: str = "",
    telegram_identity: Optional[Dict[str, Any]] = None,
    include_admin: bool = True,
) -> Dict[str, Any]:
    _require_configured()
    tabs = [
        {"id": "conversations", "label": "Chats"},
        {"id": "tasks", "label": "Tasks"},
        {"id": "memory", "label": "Memory"},
        {"id": "settings", "label": "Settings"},
    ]
    if include_admin:
        tabs.append({"id": "telegram_admin", "label": "Telegram Admin"})
    active_tab = _active_tab(widget_path)
    if active_tab == "telegram_admin" and not include_admin:
        active_tab = "tasks"
    data = {
        "ok": True,
        "bundle_id": BUNDLE_ID,
        "active_tab": active_tab,
        "path": str(widget_path or "").strip("/"),
        "tabs": tabs,
        "tasks": await task_widgets.payload(
            entrypoint,
            user_id=user_id,
            fingerprint=fingerprint,
        ),
        "memory": memory_widgets.payload(
            entrypoint,
            user_id=user_id,
            fingerprint=fingerprint,
            mark_seen=mark_memory_seen,
        ),
        "settings": settings_widgets.payload(
            entrypoint,
            user_id=user_id,
            fingerprint=fingerprint,
            telegram_identity=telegram_identity,
        ),
    }
    if telegram_identity:
        listing = telegram_user_admin.storage(entrypoint).list_conversations(
            telegram_user_id=str(telegram_identity.get("telegram_user_id") or ""),
            telegram_chat_id=str(telegram_identity.get("telegram_chat_id") or ""),
            telegram_username=str(telegram_identity.get("telegram_username") or ""),
            create_if_missing=False,
        )
        data["conversations"] = {
            "active_conversation_id": listing.get("active_conversation_id") or "",
            "items": listing.get("conversations") or [],
            "count": len(listing.get("conversations") or []),
            "telegram_user_id": str(telegram_identity.get("telegram_user_id") or ""),
            "kdcube_user_id": str(telegram_identity.get("mapped_user_id") or user_id or ""),
        }
    else:
        data["conversations"] = _payload_conversations(
            _conversation_result_for_user(entrypoint, user_id=user_id)
        )
    if include_admin:
        data["telegram_admin"] = {
            "roles": telegram_user_admin.payload(entrypoint).get("roles") or [],
            "data_operation": "telegram_user_admin_data",
            "upsert_operation": "telegram_user_admin_upsert",
            "delete_operation": "telegram_user_admin_delete",
        }
    return data


def render(
    entrypoint: Any,
    *,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    widget_path: str = "",
) -> str:
    del entrypoint, user_id, fingerprint, widget_path
    return (
        "<div style=\"font-family:system-ui,sans-serif;padding:16px\">"
        "Task And Memo web app is served from the built widget source folder."
        "</div>"
    )
