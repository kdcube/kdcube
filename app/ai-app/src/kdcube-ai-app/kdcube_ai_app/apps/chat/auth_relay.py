# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Short-lived auth relay for proc-side user-scoped companion sockets.

The queued chat task must not inline cookies or auth tokens.  Ingress stores the
resolved user auth material under a TTL key and passes only an opaque reference
to proc.  Proc can consume the reference once and then open a second
user-scoped Socket.IO connection to ingress using the same auth semantics as the
original user connection.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from http.cookies import SimpleCookie
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.auth.sessions import RequestContext, UserSession

AUTH_RELAY_KIND = "user_auth_relay"
AUTH_RELAY_KEY_PREFIX = "kdcube:chat:auth-relay:"


def _ttl_seconds() -> int:
    try:
        return max(30, min(3600, int(os.getenv("KDCUBE_AUTH_RELAY_TTL_SEC", "900"))))
    except Exception:
        return 900


def _bearer_from_auth_header(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.lower().startswith("bearer "):
        return raw.split(" ", 1)[1].strip() or None
    return raw


def _token_types(
    *,
    bearer_token: str | None,
    id_token: str | None,
    delegated_auth_cookie_header: str | None,
) -> list[str]:
    out: list[str] = []
    if bearer_token:
        out.append("bearer_token")
    if id_token:
        out.append("id_token")
    if delegated_auth_cookie_header:
        out.append("delegated_auth_cookie")
    return out


def _key(ref: str) -> str:
    return f"{AUTH_RELAY_KEY_PREFIX}{ref}"


def _masqueraded_cookie_name() -> str:
    return str(get_settings().AUTH.MASQUERADED_TOKEN_COOKIE_NAME or "__Secure-LMTC")


def _masqueraded_cookie_names() -> list[str]:
    primary = _masqueraded_cookie_name()
    names = [primary]
    if primary.startswith("__Secure-"):
        names.append(primary.removeprefix("__Secure-"))
    else:
        names.append(f"__Secure-{primary}")
    return names


def delegated_auth_cookie_header_from_mapping(cookies: Mapping[str, str] | None) -> str | None:
    """Return the existing ProxyLogin masquerade cookie as a Cookie header pair."""

    if not cookies:
        return None
    for name in _masqueraded_cookie_names():
        value = cookies.get(name)
        if value:
            return f"{name}={value}"
    return None


def delegated_auth_cookie_header_from_cookie_header(cookie_header: str | None) -> str | None:
    """Extract the existing ProxyLogin masquerade cookie from a raw Cookie header."""

    raw = str(cookie_header or "").strip()
    if not raw:
        return None
    cookies = SimpleCookie()
    try:
        cookies.load(raw)
    except Exception:
        return None
    for name in _masqueraded_cookie_names():
        morsel = cookies.get(name)
        if morsel and morsel.value:
            return f"{morsel.key}={morsel.value}"
    return None


async def create_user_auth_relay(
    *,
    redis: Any,
    request_context: RequestContext,
    session: UserSession,
    tenant: str | None,
    project: str | None,
    bundle_id: str | None,
    conversation_id: str | None,
    turn_id: str | None,
    ingress_transport: str | None,
    delegated_auth_cookie_header: str | None = None,
) -> dict[str, Any] | None:
    """Store resolved auth tokens under a TTL key and return safe metadata."""

    if redis is None or request_context is None or session is None:
        return None

    bearer_token = _bearer_from_auth_header(getattr(request_context, "authorization_header", None))
    id_token = str(getattr(request_context, "id_token", None) or "").strip() or None
    delegated_auth_cookie_header = str(delegated_auth_cookie_header or "").strip() or None
    types = _token_types(
        bearer_token=bearer_token,
        id_token=id_token,
        delegated_auth_cookie_header=delegated_auth_cookie_header,
    )
    if not types:
        return None

    ref = f"ar_{uuid.uuid4().hex}"
    ttl = _ttl_seconds()
    now = time.time()
    payload = {
        "kind": AUTH_RELAY_KIND,
        "version": 1,
        "ref": ref,
        "created_at": now,
        "expires_at": now + ttl,
        "session_id": session.session_id,
        "user_id": session.user_id,
        "user_type": session.user_type.value if getattr(session, "user_type", None) else None,
        "tenant": tenant,
        "project": project,
        "bundle_id": bundle_id,
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "ingress_transport": ingress_transport,
        "bearer_token": bearer_token,
        "id_token": id_token,
        "delegated_auth_cookie_header": delegated_auth_cookie_header,
    }
    await redis.set(_key(ref), json.dumps(payload, ensure_ascii=False, separators=(",", ":")), ex=ttl)
    return {
        "kind": AUTH_RELAY_KIND,
        "ref": ref,
        "session_id": session.session_id,
        "tenant": tenant,
        "project": project,
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "expires_at": payload["expires_at"],
        "expires_in_sec": ttl,
        "token_types": types,
    }


async def consume_user_auth_relay(
    *,
    redis: Any,
    ref: str,
    expected_session_id: str | None = None,
    expected_conversation_id: str | None = None,
    delete: bool = True,
) -> dict[str, Any] | None:
    """Load and optionally delete a relay entry for proc-side connection setup."""

    if redis is None:
        return None
    clean_ref = str(ref or "").strip()
    if not clean_ref:
        return None
    raw = await redis.get(_key(clean_ref))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "ignore")
    data = json.loads(str(raw))
    if data.get("kind") != AUTH_RELAY_KIND:
        return None
    if expected_session_id and data.get("session_id") != expected_session_id:
        return None
    if expected_conversation_id and data.get("conversation_id") != expected_conversation_id:
        return None
    if delete:
        await redis.delete(_key(clean_ref))
    return data


def socket_auth_from_relay(
    relay_payload: Mapping[str, Any],
    *,
    client_role: str = "proc_reverse",
    include_tokens: bool = True,
) -> dict[str, Any]:
    """Build Socket.IO auth payload equivalent to user auth plus proc role."""

    auth = {
        "user_session_id": relay_payload.get("session_id"),
        "tenant": relay_payload.get("tenant"),
        "project": relay_payload.get("project"),
        "conversation_id": relay_payload.get("conversation_id"),
        "turn_id": relay_payload.get("turn_id"),
        "bundle_id": relay_payload.get("bundle_id"),
        "client_role": client_role,
    }
    if include_tokens:
        auth["bearer_token"] = relay_payload.get("bearer_token")
        auth["id_token"] = relay_payload.get("id_token")
    return auth


def socket_connect_kwargs_from_relay(
    relay_payload: Mapping[str, Any],
    *,
    client_role: str = "proc_reverse",
    prefer_delegated_cookie: bool = True,
) -> dict[str, Any]:
    """Build Socket.IO auth + HTTP headers for a proc-side user-scoped socket."""

    delegated_cookie = str(relay_payload.get("delegated_auth_cookie_header") or "").strip()
    use_cookie = bool(prefer_delegated_cookie and delegated_cookie)
    return {
        "auth": socket_auth_from_relay(
            relay_payload,
            client_role=client_role,
            include_tokens=not use_cookie,
        ),
        "headers": {"Cookie": delegated_cookie} if delegated_cookie else {},
        "uses_delegated_cookie": use_cookie,
    }
