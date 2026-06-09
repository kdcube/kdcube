# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
from __future__ import annotations

import json
import logging
from collections.abc import Mapping, MutableMapping
from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.react.events import (
    block_matches_event_source,
    block_production_policy,
    compaction_event_policy,
    timeline_projection_policy,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies.rendering_common import (
    EVENT_RENDER_POLICY_META_KEY,
)


MEMORY_CONTEXT_BLOCK_POLICY_ID = "memory.block_production.context_event"
MEMORY_CONTEXT_RENDER_POLICY_ID = "memory.timeline_projection.context_event"
MEMORY_CONTEXT_COMPACTION_POLICY_ID = "memory.compaction_projection.context_event"
MEMORY_READ_BLOCK_POLICY_ID = "memory.block_production.read_result"
LOGGER = logging.getLogger("kdcube.memory.events")


def _ret_mapping(target: Mapping[str, Any]) -> dict[str, Any]:
    ret = target.get("ret")
    if isinstance(ret, Mapping):
        return dict(ret)
    raw = target.get("raw")
    if isinstance(raw, Mapping):
        output = raw.get("output")
        if isinstance(output, Mapping):
            return dict(output)
    return {}


def _object_ref(payload: Mapping[str, Any]) -> str:
    ref = str(payload.get("object_ref") or payload.get("ref") or "").strip()
    if ref.startswith("mem:"):
        return ref
    memory = payload.get("memory")
    if isinstance(memory, Mapping):
        memory_id = str(memory.get("id") or "").strip()
        if memory_id:
            return f"mem:{memory_id}"
    return ref


def _event_mapping(target: Mapping[str, Any]) -> dict[str, Any]:
    event = target.get("event")
    return dict(event) if isinstance(event, Mapping) else {}


def _event_payload(target: Mapping[str, Any]) -> dict[str, Any]:
    event = _event_mapping(target)
    payload = event.get("payload")
    return dict(payload) if isinstance(payload, Mapping) else {}


def _event_data(target: Mapping[str, Any]) -> dict[str, Any]:
    payload = _event_payload(target)
    event_data = payload.get("event")
    if isinstance(event_data, Mapping):
        return dict(event_data)
    ret = target.get("ret")
    return dict(ret) if isinstance(ret, Mapping) else {}


def _context_object_ref(target: Mapping[str, Any], event_data: Mapping[str, Any]) -> str:
    payload = _event_payload(target)
    for value in (
        payload.get("event_ref"),
        target.get("hosted_uri"),
        _event_mapping(target).get("hosted_uri"),
        event_data.get("ref"),
        event_data.get("logical_path"),
        event_data.get("logicalPath"),
        event_data.get("id"),
    ):
        ref = str(value or "").strip()
        if ref.startswith("mem:"):
            return ref
    return ""


def _context_label(event_data: Mapping[str, Any], ref: str) -> str:
    for key in ("label", "title", "name", "memory"):
        value = str(event_data.get(key) or "").strip()
        if value:
            return value
    data = event_data.get("data")
    if isinstance(data, Mapping):
        for key in ("label", "title", "memory"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
    return ref


def _context_summary(event_data: Mapping[str, Any]) -> str:
    for key in ("summary", "context", "description"):
        value = str(event_data.get(key) or "").strip()
        if value:
            return value
    data = event_data.get("data")
    if isinstance(data, Mapping):
        for key in ("summary", "context", "description"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
    return ""


def _context_canvas_origin(event_data: Mapping[str, Any]) -> dict[str, Any]:
    data = event_data.get("data")
    if not isinstance(data, Mapping):
        return {}
    canvas_context = data.get("canvas_context")
    return dict(canvas_context) if isinstance(canvas_context, Mapping) else {}


def _compact_text(value: Any, *, limit: int = 360) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _parse_json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _memory_text(payload: Mapping[str, Any]) -> str:
    memory = payload.get("memory")
    if not isinstance(memory, Mapping):
        return json.dumps(dict(payload), ensure_ascii=False, indent=2, default=str)

    rows = {
        "object_ref": _object_ref(payload),
        "id": memory.get("id"),
        "memory": memory.get("memory"),
        "context": memory.get("context"),
        "kind": memory.get("kind"),
        "status": memory.get("status"),
        "labels": memory.get("labels") or [],
        "keywords": memory.get("keywords") or [],
        "tier": memory.get("tier"),
        "updated_at": memory.get("updated_at"),
    }
    events = payload.get("events")
    if isinstance(events, list):
        rows["events"] = events
    return json.dumps(
        {key: value for key, value in rows.items() if value not in (None, "", [])},
        ensure_ascii=False,
        indent=2,
        default=str,
    )


@block_production_policy(
    event_policy_id=MEMORY_CONTEXT_BLOCK_POLICY_ID,
    description="Render an attached mem:<id> context occurrence as a memory-owned timeline fact.",
)
def memory_context_block_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    if not isinstance(target, MutableMapping):
        return target
    blocks = target.setdefault("blocks", [])
    block_factory = target.get("block_factory")
    if not isinstance(blocks, list) or not callable(block_factory):
        return target

    accepted_event = _event_mapping(target)
    event_data = _event_data(target)
    object_ref = _context_object_ref(target, event_data)
    if not object_ref:
        return target
    label = _context_label(event_data, object_ref)
    summary = _context_summary(event_data)
    surface = str(target.get("surface") or accepted_event.get("surface") or event_data.get("surface") or "").strip()
    canvas_origin = _context_canvas_origin(event_data)
    event_source_id = str(target.get("event_source_id") or accepted_event.get("event_source_id") or "memory.context").strip()
    event_id = str(target.get("event_id") or accepted_event.get("event_id") or "").strip()
    event_type = str(target.get("block_type") or accepted_event.get("type") or "event.external").strip()
    logical_path = str(target.get("logical_path") or accepted_event.get("logical_path") or target.get("path") or "").strip()
    payload = {
        "event_id": event_id,
        "event_source_id": event_source_id,
        "event_type": event_type,
        "logical_path": logical_path,
        "hosted_uri": object_ref,
        "event_ref": object_ref,
        "status": "success",
        "object_ref": object_ref,
        "label": label,
        "summary": summary,
        "surface": surface,
        "canvas_context": canvas_origin,
    }
    payload = {
        key: value
        for key, value in payload.items()
        if value not in (None, "", {})
    }
    blocks.append(block_factory(
        type=event_type,
        author=str(target.get("author") or "user"),
        turn_id=str(target.get("turn_id") or ""),
        ts=str(target.get("ts") or accepted_event.get("timestamp") or accepted_event.get("ts") or ""),
        mime="application/json",
        text=json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        path=logical_path,
        meta={
            "event_id": event_id,
            "event_source_id": event_source_id,
            "event_type": event_type,
            "event_occurrence": True,
            "logical_path": logical_path,
            "hosted_uri": object_ref,
            "object_ref": object_ref,
            "surface": surface,
        },
    ))
    target["blocks_produced"] = True
    return target


@compaction_event_policy(
    event_policy_id=MEMORY_CONTEXT_COMPACTION_POLICY_ID,
    description="Render mem:<id> context occurrences compactly for compaction.",
)
@timeline_projection_policy(
    event_policy_id=MEMORY_CONTEXT_RENDER_POLICY_ID,
    description="Render mem:<id> context occurrences as memory-owned timeline facts.",
)
def memory_context_render_policy(
    timeline: list[MutableMapping[str, Any]],
    *,
    source: Any = None,
    call_meta: Mapping[str, Mapping[str, Any]] | None = None,
    react_phase: str = "timeline_projection",
    **_: Any,
) -> list[MutableMapping[str, Any]]:
    source_id = str(getattr(source, "event_source_id", "") or "memory.context").strip()
    for block in timeline or []:
        if not isinstance(block, MutableMapping):
            continue
        if str(block.get("type") or "") not in {"event.external", "event.external.preserved"}:
            continue
        if source_id and not block_matches_event_source(block, source_id, call_meta=call_meta):
            continue
        if block.get("hidden"):
            continue
        payload = _parse_json_object(block.get("text"))
        if not payload:
            continue
        object_ref = str(payload.get("object_ref") or payload.get("event_ref") or payload.get("hosted_uri") or "").strip()
        if not object_ref.startswith("mem:"):
            continue
        lines = ["[MEMORY CONTEXT]"]
        path = str(block.get("path") or payload.get("logical_path") or "").strip()
        if path:
            lines.append(f"[path: {path}]")
        lines.append(f"object_ref: {object_ref}")
        label = _compact_text(payload.get("label"), limit=220)
        if label and label != object_ref:
            lines.append(f"label: {label}")
        summary = _compact_text(payload.get("summary"), limit=480)
        if summary and summary != label:
            lines.append(f"summary: {summary}")
        canvas_context = payload.get("canvas_context")
        if isinstance(canvas_context, Mapping):
            canvas_name = str(canvas_context.get("canvas_name") or "").strip()
            card_id = str(canvas_context.get("card_id") or "").strip()
            revision = canvas_context.get("revision")
            origin = " ".join(part for part in (
                f"canvas={canvas_name}" if canvas_name else "",
                f"card={card_id}" if card_id else "",
                f"revision={revision}" if revision is not None else "",
            ) if part)
            if origin:
                lines.append(f"origin: {origin}")
        lines.append("semantics: attached durable memory object; use the visible preview when sufficient; pull object_ref into the workspace when exact content is needed")
        meta = dict(block.get("meta") if isinstance(block.get("meta"), Mapping) else {})
        meta[EVENT_RENDER_POLICY_META_KEY] = str(react_phase or MEMORY_CONTEXT_RENDER_POLICY_ID)
        meta["render_as"] = "raw"
        block["meta"] = meta
        block["mime"] = "text/plain"
        block["text"] = "\n".join(lines).strip()
    return timeline


@block_production_policy(
    event_policy_id=MEMORY_READ_BLOCK_POLICY_ID,
    description="Render memory.read_memory as a memory-owned mem:<id> fact block.",
)
def memory_read_block_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    if not isinstance(target, MutableMapping):
        return target
    payload = _ret_mapping(target)
    tool_id = str(target.get("tool_id") or target.get("event_source_id") or "memory.read_memory").strip()
    tool_call_id = str(target.get("tool_call_id") or target.get("event_id") or "").strip()
    turn_id = str(target.get("turn_id") or "").strip()
    path = _object_ref(payload)
    if not path:
        final_params = target.get("final_params") if isinstance(target.get("final_params"), Mapping) else {}
        path = str(final_params.get("object_ref") or final_params.get("path") or "").strip()
    LOGGER.info(
        "[memory.read_memory.policy] render tool_call_id=%s payload_keys=%s object_ref=%s ok=%s error=%s",
        tool_call_id,
        sorted(payload.keys()),
        path,
        payload.get("ok"),
        payload.get("error"),
    )
    blocks = target.setdefault("blocks", [])
    if isinstance(blocks, list):
        blocks.append({
            "turn": turn_id,
            "type": "react.tool.result",
            "call_id": tool_call_id,
            "tool_id": tool_id,
            "event_source_id": tool_id,
            "mime": "application/json",
            "path": path,
            "text": _memory_text(payload),
            "meta": {
                "tool_call_id": tool_call_id,
                "tool_id": tool_id,
                "event_source_id": tool_id,
                "object_ref": path,
                "render_as": "memory.read",
            },
        })
    target["blocks_produced"] = True
    target["result_items"] = []
    target["result_items_produced"] = True
    target["declared_file_items"] = []
    target["declared_file_items_produced"] = True
    return target


__all__ = [
    "MEMORY_CONTEXT_BLOCK_POLICY_ID",
    "MEMORY_CONTEXT_COMPACTION_POLICY_ID",
    "MEMORY_CONTEXT_RENDER_POLICY_ID",
    "MEMORY_READ_BLOCK_POLICY_ID",
    "memory_context_block_policy",
    "memory_context_render_policy",
    "memory_read_block_policy",
]
