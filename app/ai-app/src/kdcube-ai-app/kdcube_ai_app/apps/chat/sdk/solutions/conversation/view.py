# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Lightweight, chronologically-interleaved conversation view.

`object.get conv:conversation:<id>` fetches the RICH per-turn artifacts (the same
data the ingress `/fetch` returns), then this module distills them into a compact,
time-ordered timeline for external clients: user messages, assistant thinking,
assistant responses, user attachments, assistant-produced files, produced
artifacts, and the sources/links used — interleaved in the order they happened.

Files (uploaded attachments and assistant outputs) are surfaced as `conv:fi:<path>`
refs, so the client can materialize their bytes with `object.get conv:fi:<path>`.
Heavy bodies (base64, raw HTML views, embeddings, full code/exec payloads) are
dropped; only handles + light metadata are kept.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.presentation import conv_file_ref


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ts_sort_key(ts: Any) -> tuple[int, float]:
    """(has_ts, epoch) — untimed events sort after timed ones, order otherwise stable."""
    raw = _text(ts)
    if not raw:
        return (1, 0.0)
    try:
        parsed = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        return (0, parsed.timestamp())
    except Exception:
        return (1, 0.0)


def _attachment_event(ts: Any, att: Dict[str, Any], conversation_id: str) -> Optional[Dict[str, Any]]:
    if not isinstance(att, dict):
        return None
    path = _text(att.get("artifact_path") or att.get("path") or att.get("logical_path"))
    filename = _text(att.get("filename") or att.get("name"))
    if not path and not filename:
        return None
    event: Dict[str, Any] = {"type": "user.attachment", "ts": ts}
    if filename:
        event["filename"] = filename
    if att.get("mime"):
        event["mime"] = _text(att.get("mime"))
    if path:
        event["ref"] = conv_file_ref(path, conversation_id)
    return event


def _classify(art: Dict[str, Any], conversation_id: str) -> List[Dict[str, Any]]:
    atype = _text(art.get("type"))
    ts = art.get("ts")
    data = art.get("data") if isinstance(art.get("data"), dict) else {}
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}

    if atype == "chat:user":
        out: List[Dict[str, Any]] = [{"type": "user.message", "ts": ts, "text": _text(data.get("text"))}]
        for att in (data.get("attachments") or []):
            ev = _attachment_event(ts, att, conversation_id)
            if ev:
                out.append(ev)
        return out

    if atype == "chat:assistant":
        return [{"type": "assistant.message", "ts": ts, "text": _text(data.get("text"))}]

    if atype == "artifact:assistant.file":
        path = _text(payload.get("artifact_path") or meta.get("artifact_path"))
        return [{
            "type": "assistant.file",
            "ts": ts,
            "filename": _text(payload.get("filename")),
            "mime": _text(payload.get("mime")),
            "ref": conv_file_ref(path, conversation_id),
        }]

    if atype == "artifact:conv.thinking.stream":
        items = [
            {"agent": _text(it.get("agent")), "text": _text(it.get("text"))}
            for it in (payload.get("items") or [])
            if isinstance(it, dict) and _text(it.get("text"))
        ]
        if not items:
            return []
        return [{"type": "assistant.thinking", "ts": ts, "items": items}]

    if atype == "artifact:solver.program.citables":
        items = [
            {k: it.get(k) for k in ("sid", "title", "url") if it.get(k) not in (None, "")}
            for it in (payload.get("items") or [])
            if isinstance(it, dict) and (it.get("url") or it.get("title"))
        ]
        if not items:
            return []
        return [{"type": "sources", "ts": ts, "items": items}]

    if atype == "artifact:conv.artifacts.stream":
        items = []
        for it in (payload.get("items") or []):
            if not isinstance(it, dict):
                continue
            name = _text(it.get("artifact_name"))
            if not name:
                continue
            entry = {"name": name}
            if it.get("title"):
                entry["title"] = _text(it.get("title"))
            if it.get("format"):
                entry["format"] = _text(it.get("format"))
            items.append(entry)
        if not items:
            return []
        return [{"type": "artifacts", "ts": ts, "items": items}]

    return []


def build_conversation_timeline(fetched: Dict[str, Any]) -> Dict[str, Any]:
    """Rich `fetch_conversation_artifacts` output -> lightweight per-turn view.

    Returns turns in order, each with its events time-ordered within the turn.
    """
    conversation_id = _text((fetched or {}).get("conversation_id"))
    turns_in = (fetched or {}).get("turns") or []
    turns_out: List[Dict[str, Any]] = []
    for turn in turns_in:
        if not isinstance(turn, dict):
            continue
        events: List[Dict[str, Any]] = []
        for art in (turn.get("artifacts") or []):
            if isinstance(art, dict):
                events.extend(_classify(art, conversation_id))
        events.sort(key=lambda e: _ts_sort_key(e.get("ts")))
        turns_out.append({"turn_id": _text(turn.get("turn_id")), "events": events})

    return {
        "conversation_id": _text(fetched.get("conversation_id")),
        "user_id": _text(fetched.get("user_id")),
        "title": _text(fetched.get("conversation_title")),
        "turn_count": len(turns_out),
        "turns": turns_out,
    }


__all__ = ["build_conversation_timeline"]
