# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Object resolver for `conv:` refs — pinned chat conversations.

The chat solution owns conversation identity, the preview shape, and the
open-event semantics. Canvas (or any other surface) pins a conversation as a
`conv:` ref and calls this resolver through the canvas object-action registry,
the same way `mem:` refs route to the memory resolver.

Conversation metadata (title + start date) comes from the same conversation
service the chat list uses; this module stays free of pg/storage coupling by
taking an injected `fetch_details` callable, mirroring how the memory resolver
takes an injected `store`.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping, Optional


CONVERSATION_RESOLVER_NAME = "sdk.chat.conversation"
CONVERSATION_OBJECT_NAMESPACE = "conv"

# (user_id, conversation_id, bundle_id) -> conversation details mapping.
# The details mapping is expected to expose `conversation_title` and
# `conversation_started_at` (the shape returned by
# ContextRAGClient.get_conversation_details).
ConversationDetailsFetcher = Callable[
    [str, str, Optional[str]],
    Awaitable[Optional[Mapping[str, Any]]],
]

# Ordered positional fields packed into a conv: ref body.
_REF_FIELDS = ("tenant", "project", "user_id", "bundle_id", "agent", "conversation_id")


def conversation_ref_capabilities() -> dict[str, bool]:
    return {"preview": True, "open": True, "download": False, "rehost": False}


def parse_conversation_ref(ref: str) -> dict[str, str]:
    """Parse `conv:<tenant>/<project>/<user>/<bundle>/<agent>/<conversation_id>`.

    The conversation id is always the last segment, so a short legacy ref of
    just `conv:<conversation_id>` still resolves; the leading positional
    fields are filled from the front when present.
    """
    tail = str(ref or "").strip()
    if tail.startswith("conv:"):
        tail = tail[len("conv:"):]
    out: dict[str, str] = {field: "" for field in _REF_FIELDS}
    parts = [segment for segment in tail.split("/")] if tail else []
    if not parts:
        return out
    out["conversation_id"] = parts[-1].strip()
    for index, value in enumerate(parts[:-1]):
        if index < len(_REF_FIELDS) - 1:
            out[_REF_FIELDS[index]] = value.strip()
    return out


def conversation_id_from_ref(ref: str) -> str:
    return parse_conversation_ref(ref).get("conversation_id", "")


def _base_response(*, ref: str, action: str) -> dict[str, Any]:
    return {
        "ok": True,
        "action": action,
        "ref": ref,
        "object_ref": ref,
        "namespace": CONVERSATION_OBJECT_NAMESPACE,
        "resolver": CONVERSATION_RESOLVER_NAME,
        "resolver_status": "implemented",
        "capabilities": conversation_ref_capabilities(),
    }


async def resolve_conversation_ref_action(
    payload: Mapping[str, Any],
    *,
    user_id: str = "",
    fetch_details: ConversationDetailsFetcher,
) -> dict[str, Any]:
    """Resolve an object action for a `conv:` ref.

    `fetch_details` is injected by the host bundle (it owns pg/storage); this
    keeps the chat solution the owner of conversation semantics without
    binding it to a particular runtime.
    """
    ref = str(
        payload.get("object_ref")
        or payload.get("ref")
        or payload.get("logical_path")
        or ""
    ).strip()
    action = str(payload.get("action") or "capabilities").strip().lower()
    coords = parse_conversation_ref(ref)
    conversation_id = coords.get("conversation_id") or ""
    base = _base_response(ref=ref, action=action)

    if action == "capabilities":
        return base
    if not conversation_id:
        return {**base, "ok": False, "error": "object_ref_required", "status": 400}

    title = ""
    started_at = ""
    fetch_user = (user_id or coords.get("user_id") or "").strip()
    try:
        details = await fetch_details(fetch_user, conversation_id, coords.get("bundle_id") or None)
        if details:
            title = str(details.get("conversation_title") or "")
            started_at = str(details.get("conversation_started_at") or "")
    except Exception as exc:  # noqa: BLE001 - degrade to id-only metadata
        if action in {"describe", "preview"}:
            return {
                **base,
                "title": "",
                "summary": f"Conversation {conversation_id}",
                "detail": str(exc),
            }

    label = title or f"Conversation {conversation_id}"
    summary = f"{label} · started {started_at}" if started_at else label

    if action in {"describe", "preview"}:
        return {**base, "title": label, "started_at": started_at, "summary": summary}
    if action == "open":
        return {
            **base,
            "title": label,
            "summary": summary,
            "ui_event": {
                "type": "kdcube.ui.object.open.requested",
                "subject": "ui.object.open.requested",
                "source": "object_resolver",
                "object_ref": ref,
                "target_surface": "sdk.chat.viewer",
                "mode": "focus",
                "conversation_id": conversation_id,
                "tenant": coords.get("tenant"),
                "project": coords.get("project"),
                "user_id": coords.get("user_id"),
                "bundle_id": coords.get("bundle_id"),
                "agent": coords.get("agent"),
                "title": label,
            },
        }
    return {**base, "ok": False, "error": "unsupported_object_action", "status": 400}


__all__ = [
    "CONVERSATION_OBJECT_NAMESPACE",
    "CONVERSATION_RESOLVER_NAME",
    "ConversationDetailsFetcher",
    "conversation_id_from_ref",
    "conversation_ref_capabilities",
    "parse_conversation_ref",
    "resolve_conversation_ref_action",
]
