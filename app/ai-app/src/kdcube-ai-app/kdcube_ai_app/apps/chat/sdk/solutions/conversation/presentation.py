# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Conversation named-service object presentation.

Owns the object grammar for the `conv` namespace: refs, object kinds, mimes, the
schema payload, and the shaping of turns (search hits) and whole conversations
into named-service objects. Kept separate so the provider stays a thin dispatcher.
"""

from __future__ import annotations

from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.instructions import (
    CONVERSATION_NAMED_SERVICE_NAMESPACE,
)

NAMESPACE = CONVERSATION_NAMED_SERVICE_NAMESPACE  # "conv"

TURN_OBJECT_KIND = "conversation.turn"
CONVERSATION_OBJECT_KIND = "conversation"
TURN_MIME = "application/vnd.kdcube.conversation.turn+json;version=1"
CONVERSATION_MIME = "application/vnd.kdcube.conversation+json;version=1"
NAMED_SERVICE_OBJECT_SCHEMA = "kdcube.named_service.object.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def conversation_ref(conversation_id: str) -> str:
    return f"conv:conversation:{conversation_id}" if conversation_id else ""


def conversation_id_from_ref(value: Any) -> str:
    text = _text(value)
    if text.startswith("conv:conversation:"):
        return text[len("conv:conversation:"):].split("/", 1)[0].split("?", 1)[0]
    if text.startswith("conv:") and text.count(":") == 1:  # bare conv:<id>
        return text[len("conv:"):].split("/", 1)[0].split("?", 1)[0]
    if ":" not in text:  # plain id
        return text
    return ""


def _compact(obj: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in obj.items() if value not in (None, "", [])}


def turn_hit_to_object(hit: dict[str, Any], *, namespace: str = NAMESPACE) -> dict[str, Any]:
    """Shape one rich search hit into a named-service turn object.

    The hit carries turn identity + snippets. Primary text is a compact preview
    from the first snippet; full snippet content stays in the body so callers can
    read it without an extra fetch.
    """
    turn_id = _text(hit.get("turn_id"))
    conversation_id = _text(hit.get("conversation_id"))
    snippets = [sn for sn in (hit.get("snippets") or []) if isinstance(sn, dict)]
    first_text = ""
    for sn in snippets:
        text = _text(sn.get("text"))
        if text:
            first_text = text
            break
    ref = f"conv:turn:{turn_id}" if turn_id else ""
    body = _compact({
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "turn_index_path": hit.get("turn_index_path"),
        "snippets": [
            {key: sn.get(key) for key in ("role", "path", "text", "ts") if sn.get(key) not in (None, "")}
            for sn in snippets
        ],
        "ordinal": hit.get("ordinal"),
        "total_turns": hit.get("total_turns"),
    })
    # A turn is a SEARCH HIT, never an individually-fetched object (turn refs are
    # not gettable) and never rehosted, so it carries only the actionable fields:
    # `ref` (the recovery handle), a snippet-derived title, the body (snippets +
    # recovery paths), and the score. The object envelope (`schema`/`mime`/
    # `namespace`/`object_kind`/`identity`) is only meaningful for single-object
    # responses and is intentionally omitted here.
    obj = {
        "ref": ref,
        "title": (first_text[:120] or turn_id),
        "body": body,
    }
    score = hit.get("score")
    if score is not None:
        obj["score"] = float(score)
    return _compact(obj)


def conversation_summary_to_object(summary: dict[str, Any]) -> dict[str, Any]:
    conversation_id = _text(summary.get("conversation_id"))
    ref = conversation_ref(conversation_id)
    obj = {
        "schema": NAMED_SERVICE_OBJECT_SCHEMA,
        "ref": ref,
        "namespace": NAMESPACE,
        "object_kind": CONVERSATION_OBJECT_KIND,
        "label": _text(summary.get("title")) or conversation_id,
        "title": _text(summary.get("title")) or conversation_id,
        "mime": CONVERSATION_MIME,
        "identity": {
            "object_ref": ref,
            "object_id": conversation_id,
            "object_kind": CONVERSATION_OBJECT_KIND,
            "namespace": NAMESPACE,
        },
        "body": {
            key: summary.get(key)
            for key in ("conversation_id", "user_id", "tenant", "project", "started_at", "last_at", "turn_count")
            if summary.get(key) not in (None, "")
        },
    }
    return _compact(obj)


def conversation_to_object(record: dict[str, Any]) -> dict[str, Any]:
    conversation_id = _text(record.get("conversation_id"))
    ref = conversation_ref(conversation_id)
    obj = {
        "schema": NAMED_SERVICE_OBJECT_SCHEMA,
        "ref": ref,
        "namespace": NAMESPACE,
        "object_kind": CONVERSATION_OBJECT_KIND,
        "label": _text(record.get("title")) or conversation_id,
        "title": _text(record.get("title")) or conversation_id,
        "mime": CONVERSATION_MIME,
        "identity": {
            "object_ref": ref,
            "object_id": conversation_id,
            "object_kind": CONVERSATION_OBJECT_KIND,
            "namespace": NAMESPACE,
        },
        "body": record,
    }
    return _compact(obj)


def conversation_schema_payload(
    *, grant_hints: dict[str, Any], scopes: list[str], search_filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "object_kinds": {
            CONVERSATION_OBJECT_KIND: {
                "mime": CONVERSATION_MIME,
                "canonical_ref": "conv:conversation:<conversation_id>",
                "summary_fields": ["conversation_id", "user_id", "title", "started_at", "last_at", "turn_count"],
                "full_fields": ["conversation_id", "tenant", "project", "user_id", "source", "started_at", "title", "turns"],
                "turn_fields": ["turn_id", "ts", "user", "assistant", "attachments", "citations"],
            },
            TURN_OBJECT_KIND: {"mime": TURN_MIME, "note": "conversation turn search hit (object.search)"},
        },
        "scope": {
            "mode": {"enum": list(scopes), "default": scopes[0] if scopes else "self"},
            "user_id": "selected platform user id (required for mode=user; admin, :any_user grants)",
        },
        "search": {
            "operation": "object.search",
            "purpose": (
                "Conversations are one of the user's memory realms - what was actually said in chat, "
                "alongside durable memories (mem) and context boards (cnv). Search what the USER said "
                "(prompts and follow-ups), what the ASSISTANT said (replies and working summaries), and "
                "the user's UPLOADED attachments (their indexed summaries). Reach for it whenever a look "
                "back would help: an explicit recall request, or when the user refers to something from "
                "before, says it was clearer earlier, can't re-locate something, or resumes a dropped thread."
            ),
            # Single source of truth: the provider's authoritative search filter schema
            # (same object surfaced via provider.about's search_scopes).
            "filters": dict(search_filters or {}),
            "behavior": {
                "topic": "set query -> hybrid semantic+lexical+recency search",
                "topic_in_window": "query + from/to",
                "date_window": "from/to, no query -> turns in that window",
                "overview": "no query, no bounds, targets=['summary'] -> working summaries",
            },
            "returns": (
                "turn hits: ref (conv:turn:<id>), title, body{conversation_id, turn_id, "
                "turn_index_path, snippets[{role,path,text,ts}]}, score."
            ),
            "recovery": (
                "read the returned snippet paths, or turn_index_path (ar:turn_<id>.react.turn.index), "
                "to recover full turn content."
            ),
        },
        "grant_hints": grant_hints,
    }


__all__ = [
    "CONVERSATION_MIME",
    "CONVERSATION_OBJECT_KIND",
    "NAMED_SERVICE_OBJECT_SCHEMA",
    "NAMESPACE",
    "TURN_MIME",
    "TURN_OBJECT_KIND",
    "conversation_id_from_ref",
    "conversation_ref",
    "conversation_schema_payload",
    "conversation_summary_to_object",
    "conversation_to_object",
    "turn_hit_to_object",
]
