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
CONVERSATION_FILE_OBJECT_KIND = "conversation.file"
TURN_MIME = "application/vnd.kdcube.conversation.turn+json;version=1"
CONVERSATION_MIME = "application/vnd.kdcube.conversation+json;version=1"
NAMED_SERVICE_OBJECT_SCHEMA = "kdcube.named_service.object.v1"

# A file artifact referenced by a turn, addressed through the conv namespace as
# `conv:fi:<fi-body>` — a conv-namespaced view of a `fi:` logical path.
CONV_FILE_REF_PREFIX = "conv:fi:"


def _text(value: Any) -> str:
    return str(value or "").strip()


def conversation_ref(conversation_id: str) -> str:
    return f"conv:conversation:{conversation_id}" if conversation_id else ""


def conversation_id_from_ref(value: Any) -> str:
    text = _text(value)
    if text.startswith("conv:conversation:"):
        return text[len("conv:conversation:"):].split("/", 1)[0].split("?", 1)[0]
    if text.startswith(CONV_FILE_REF_PREFIX):  # conv:fi: is a file ref, not a conversation
        return ""
    if text.startswith("conv:") and text.count(":") == 1:  # bare conv:<id>
        return text[len("conv:"):].split("/", 1)[0].split("?", 1)[0]
    if ":" not in text:  # plain id
        return text
    return ""


def _scope_fi_to_conversation(fi_path: str, conversation_id: str) -> str:
    """Ensure a `fi:` path self-describes its conversation (`fi:conv_<id>.turn_...`).

    External clients have no ambient conversation, so every emitted file ref must
    carry the conversation scope for the read side to resolve the turn.
    """
    raw = _text(fi_path)
    conv = _text(conversation_id)
    if not conv or not raw.startswith("fi:"):
        return raw
    body = raw[len("fi:"):]
    if body.startswith("conv_"):  # already scoped
        return raw
    return f"fi:conv_{conv}.{body}"


def conv_file_ref(fi_path: Any, conversation_id: str = "") -> str:
    """Round-trippable conv-namespaced handle for a `fi:` artifact path.

    `fi:[conv_<id>.]<body>` -> `conv:fi:[conv_<id>.]<body>`, scoped to its
    conversation when known so an external client can resolve it. An already-
    `conv:fi:` ref or a non-`fi:` value (e.g. an `ar:`/`tc:` handle) passes through.
    """
    raw = _scope_fi_to_conversation(_text(fi_path), conversation_id)
    if raw.startswith(CONV_FILE_REF_PREFIX):
        return raw
    if raw.startswith("fi:"):
        return f"{CONV_FILE_REF_PREFIX}{raw[len('fi:'):]}"
    return raw


def is_conv_file_ref(value: Any) -> bool:
    return _text(value).startswith(CONV_FILE_REF_PREFIX)


def fi_path_from_conv_ref(value: Any) -> str:
    """`conv:fi:<body>` -> `fi:<body>`. Tolerates a bare `fi:` ref; returns "" otherwise."""
    raw = _text(value)
    if raw.startswith(CONV_FILE_REF_PREFIX):
        return f"fi:{raw[len(CONV_FILE_REF_PREFIX):]}"
    if raw.startswith("fi:"):
        return raw
    return ""


def _compact(obj: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in obj.items() if value not in (None, "", [])}


def _present_snippet(sn: dict[str, Any], conversation_id: str = "") -> dict[str, Any]:
    """Lean snippet for a turn hit. The `path` handle is presented as a
    round-trippable, conversation-scoped `conv:fi:<...>` ref when it is a file
    artifact, so the client can pass it straight to object.get; non-file handles
    pass through unchanged."""
    out: dict[str, Any] = {}
    for key in ("role", "path", "text", "ts"):
        value = sn.get(key)
        if value in (None, ""):
            continue
        out[key] = conv_file_ref(value, conversation_id) if key == "path" else value
    return out


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
        "snippets": [_present_snippet(sn, conversation_id) for sn in snippets],
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


def conversation_file_to_object(
    *,
    ref: str,
    filename: str,
    mime: str,
    size: int,
    encoding: str = "text",
    content: Any = None,
    note: str = "",
    url: str = "",
    expires_at: int = 0,
) -> dict[str, Any]:
    """Shape a materialized `conv:fi:` file into a single named-service object.

    `encoding` is "text" (content is the decoded text), "base64" (content is a
    base64 string), "url" (bytes are fetched out-of-band over HTTP from `url` — the
    default for binaries so they never enter the model's context), or "none"
    (metadata only — too large / unreadable).
    """
    obj = {
        "schema": NAMED_SERVICE_OBJECT_SCHEMA,
        "ref": ref,
        "namespace": NAMESPACE,
        "object_kind": CONVERSATION_FILE_OBJECT_KIND,
        "title": filename or ref,
        "mime": mime,
        "identity": {
            "object_ref": ref,
            "object_kind": CONVERSATION_FILE_OBJECT_KIND,
            "namespace": NAMESPACE,
        },
        "body": _compact({
            "filename": filename,
            "mime": mime,
            "size": size,
            "encoding": encoding,
            "content": content,
            "url": url,
            "expires_at": expires_at or None,
            "note": note,
        }),
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
            CONVERSATION_FILE_OBJECT_KIND: {
                "canonical_ref": "conv:fi:<path>",
                "note": (
                    "A file artifact referenced by a turn — an uploaded attachment, a produced "
                    "output (e.g. a summary.md), a snapshot, or a pulled external attachment."
                ),
            },
        },
        "scope": {
            "mode": {"enum": list(scopes), "default": scopes[0] if scopes else "self"},
            "user_id": "selected platform user id (required for mode=user; admin, :any_user grants)",
        },
        "files": {
            "operation": "object.get",
            "ref": "conv:fi:<path>",
            "purpose": (
                "Turns reference files — uploaded attachments, produced outputs, snapshots, pulled "
                "external attachments. Search results present these as conv:fi:<path> handles, and "
                "fi:<path> refs also appear inside turn text (e.g. working summaries). To read a "
                "referenced file, call object.get with its conv:fi:<path> ref (an fi: path seen in "
                "text is the same artifact — address it as conv:fi:<that-path>)."
            ),
            "returns": (
                "the file: {ref, filename, mime, size, encoding, ...}. encoding=text -> content is "
                "the decoded text (inline); encoding=url -> fetch the bytes from `url` over HTTP (a "
                "short-lived link; the default for binaries so they stay out of context); "
                "encoding=base64 -> content is base64 (small binaries); encoding=none -> metadata "
                "only (too large / no link configured)."
            ),
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
    "CONVERSATION_FILE_OBJECT_KIND",
    "CONV_FILE_REF_PREFIX",
    "NAMED_SERVICE_OBJECT_SCHEMA",
    "NAMESPACE",
    "TURN_MIME",
    "TURN_OBJECT_KIND",
    "conv_file_ref",
    "conversation_file_to_object",
    "conversation_id_from_ref",
    "conversation_ref",
    "conversation_schema_payload",
    "conversation_summary_to_object",
    "conversation_to_object",
    "fi_path_from_conv_ref",
    "is_conv_file_ref",
    "turn_hit_to_object",
]
