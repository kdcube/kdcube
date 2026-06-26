# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Execution identity authority helpers.

Surfaces can prove local identities such as ``telegram:<id>``. Runtime and
economics authority must come from the linked platform principal, when one is
known. This module is the SDK boundary that turns "actor X is linked to
platform user Y" into execution-context fields carried by detached jobs.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.auth.AuthManager import PRIVILEGED_ROLES
from kdcube_ai_app.auth.sessions import SessionManager, UserType


def _str(value: Any) -> str:
    return str(value or "").strip()


def _list(values: Iterable[Any] | None) -> list[str]:
    if isinstance(values, str):
        values = [values]
    return [_str(value) for value in (values or ()) if _str(value)]


def _normal_user_type(value: Any, default: str = "registered") -> str:
    raw = getattr(value, "value", value)
    text = _str(raw).lower() or _str(default).lower() or "registered"
    if text == "admin":
        return "privileged"
    if text in {"anonymous", "registered", "paid", "privileged"}:
        return text
    return _str(default).lower() or "registered"


def user_type_from_roles(*, roles: Iterable[Any] | None, fallback: Any = "registered") -> str:
    role_set = set(_list(roles))
    if role_set & PRIVILEGED_ROLES:
        return "privileged"
    return _normal_user_type(fallback)


def platform_user_type_from_session(session: Any, *, default: str = "registered") -> str:
    return user_type_from_roles(
        roles=getattr(session, "roles", None) or [],
        fallback=getattr(session, "user_type", default),
    )


async def resolve_platform_authority(
    entrypoint: Any,
    *,
    actor_user_id: str,
    platform_user_id: str = "",
    default_user_type: str = "registered",
    provider: str = "",
    provider_subject: str = "",
    local_role: str = "",
    source: str = "",
) -> Dict[str, Any]:
    """Resolve execution authority fields for a linked platform user.

    The actor remains the surface/app identity. ``economics_user_id`` is the
    linked platform principal when available, so funding/subscription lookup is
    platform-owned. Privileged roles are attached only when the platform session
    store proves them.
    """

    actor = _str(actor_user_id)
    platform = _str(platform_user_id)
    authority: Dict[str, Any] = {
        "actor_user_id": actor,
        "storage_user_id": actor,
        "identity_source": _str(source),
        "identity_provider": _str(provider),
        "identity_provider_subject": _str(provider_subject),
        "local_role": _str(local_role),
        "user_type": _normal_user_type(default_user_type),
    }
    if not platform:
        authority.update(
            {
                "economics_user_id": actor,
                "platform_authority_resolved": False,
                "platform_authority_error": "platform_user_not_linked",
            }
        )
        return {k: v for k, v in authority.items() if v not in ("", None, [])}

    authority.update(
        {
            "platform_user_id": platform,
            "economics_user_id": platform,
        }
    )
    settings = getattr(entrypoint, "settings", None) or get_settings()
    tenant = _str(getattr(getattr(entrypoint, "config", None), "tenant", "") or getattr(settings, "TENANT", ""))
    project = _str(getattr(getattr(entrypoint, "config", None), "project", "") or getattr(settings, "PROJECT", ""))
    redis_url = _str(getattr(settings, "REDIS_URL", "") or get_settings().REDIS_URL)
    if not (tenant and project and redis_url):
        authority.update(
            {
                "economics_user_type": authority["user_type"],
                "platform_authority_resolved": False,
                "platform_authority_error": "session_store_unavailable",
            }
        )
        return {k: v for k, v in authority.items() if v not in ("", None, [])}

    manager = SessionManager(redis_url, tenant=tenant, project=project)
    session = await manager.get_session_by_user_id(platform)
    if session is None:
        authority.update(
            {
                "economics_user_type": authority["user_type"],
                "platform_authority_resolved": False,
                "platform_authority_error": "active_platform_session_not_found",
            }
        )
        return {k: v for k, v in authority.items() if v not in ("", None, [])}

    user_type = platform_user_type_from_session(session, default=default_user_type)
    authority.update(
        {
            "user_type": user_type,
            "platform_user_type": user_type,
            "economics_user_type": user_type,
            "platform_roles": _list(getattr(session, "roles", None) or []),
            "platform_permissions": _list(getattr(session, "permissions", None) or []),
            "platform_authority_resolved": True,
            "platform_authority_source": "session_manager",
        }
    )
    return {k: v for k, v in authority.items() if v not in ("", None, [])}


def authority_from_source(source: Dict[str, Any] | None) -> Dict[str, Any]:
    src = source or {}
    return {
        "actor_user_id": _str(src.get("actor_user_id") or src.get("storage_user_id")),
        "economics_user_id": _str(src.get("economics_user_id") or src.get("platform_user_id")),
        "user_type": _str(src.get("economics_user_type") or src.get("platform_user_type") or src.get("user_type")),
        "roles": _list(src.get("platform_roles") or src.get("roles") or []),
        "permissions": _list(src.get("platform_permissions") or src.get("permissions") or []),
        "source": src,
    }


def normalize_execution_authority(
    source: Dict[str, Any] | None,
    *,
    actor_user_id: str = "",
    economics_user_id: str = "",
    user_type: str = "",
    roles: Iterable[Any] | None = None,
    permissions: Iterable[Any] | None = None,
) -> Dict[str, Any]:
    """Return the authority envelope that detached execution contexts carry.

    Surface/app identity stays in ``actor_user_id`` / ``storage_user_id``. Role
    and funding checks should read the already-resolved authority fields from
    this envelope instead of asking every surface how to reinterpret the actor.
    """

    out: Dict[str, Any] = {
        key: value for key, value in (source or {}).items() if value not in ("", None, [])
    }
    actor = _str(actor_user_id or out.get("actor_user_id") or out.get("storage_user_id"))
    economics_user = _str(economics_user_id or out.get("economics_user_id") or out.get("platform_user_id") or actor)
    resolved_roles = _list(roles) or _list(out.get("platform_roles") or out.get("roles") or [])
    resolved_permissions = _list(permissions) or _list(out.get("platform_permissions") or out.get("permissions") or [])
    effective_user_type = _normal_user_type(
        user_type or out.get("economics_user_type") or out.get("platform_user_type") or out.get("user_type"),
        default=out.get("user_type") or "registered",
    )
    effective_user_type = user_type_from_roles(roles=resolved_roles, fallback=effective_user_type)
    if actor:
        out["actor_user_id"] = actor
        out.setdefault("storage_user_id", actor)
    if economics_user:
        out["economics_user_id"] = economics_user
    if economics_user and actor and economics_user != actor:
        out.setdefault("platform_user_id", economics_user)
    if effective_user_type:
        out["user_type"] = effective_user_type
        out["economics_user_type"] = effective_user_type
    if resolved_roles:
        out["platform_roles"] = resolved_roles
    if resolved_permissions:
        out["platform_permissions"] = resolved_permissions
    return {k: v for k, v in out.items() if v not in ("", None, [])}


def apply_authority_to_comm_context(comm_context: Any, *, source: Dict[str, Any] | None) -> None:
    """Attach resolved authority role details to an execution comm_context."""

    user = getattr(comm_context, "user", None)
    if user is None:
        return
    authority = authority_from_source(source)
    if authority.get("user_type"):
        user.user_type = authority["user_type"]
    if authority.get("roles"):
        user.roles = authority["roles"]
    if authority.get("permissions"):
        user.permissions = authority["permissions"]


__all__ = [
    "apply_authority_to_comm_context",
    "authority_from_source",
    "normalize_execution_authority",
    "platform_user_type_from_session",
    "resolve_platform_authority",
    "user_type_from_roles",
]
