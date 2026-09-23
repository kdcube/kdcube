# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


def request_context_storage_record(
    context: Any,
    *,
    include_network_identity: bool = False,
) -> dict[str, Any] | None:
    """Return credential-free request metadata suitable for shared storage."""

    if context is None:
        return None

    def value(name: str) -> Any:
        if isinstance(context, Mapping):
            return context.get(name)
        return getattr(context, name, None)

    return {
        "client_ip": str(value("client_ip") or "") if include_network_identity else "",
        "user_agent": (
            str(value("user_agent") or "") if include_network_identity else ""
        ),
        "user_timezone": value("user_timezone"),
        "user_utc_offset_min": value("user_utc_offset_min"),
    }


def user_session_storage_record(session: Any) -> dict[str, Any]:
    """Serialize the durable UserSession fields without request credentials."""

    user_type = getattr(session, "user_type", None)
    return {
        "session_id": str(getattr(session, "session_id", "") or ""),
        "user_type": getattr(user_type, "value", user_type),
        "fingerprint": getattr(session, "fingerprint", None),
        "user_id": getattr(session, "user_id", None),
        "username": getattr(session, "username", None),
        "roles": list(getattr(session, "roles", None) or []),
        "permissions": list(getattr(session, "permissions", None) or []),
        "created_at": float(getattr(session, "created_at", 0) or 0),
        "last_seen": float(getattr(session, "last_seen", 0) or 0),
        "email": getattr(session, "email", None),
        "email_verified": getattr(session, "email_verified", None),
        "timezone": getattr(session, "timezone", None),
        "request_context": request_context_storage_record(
            getattr(session, "request_context", None)
        ),
        "identity_authority": deepcopy(
            getattr(session, "identity_authority", None)
        ),
        "rate_limit_subject": getattr(session, "rate_limit_subject", None),
    }
