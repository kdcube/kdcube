# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping
from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.react.events.common import block_matches_event_source

EVENT_RENDER_POLICY_META_KEY = "event_render_policy"
EVENT_RENDER_POLICY_SKIP_META_KEY = "event_render_policy_skip"
TIMELINE_SEGMENT_META_KEY = "_react_timeline_segment"


def _parse_json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _compact(value: Any, *, max_chars: int = 280) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            value = str(value)
    text = " ".join(str(value or "").replace("\n", " ").split())
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip() + "..."


def _payload_from_block(block: Mapping[str, Any]) -> dict[str, Any]:
    parsed = _parse_json_object(block.get("text"))
    meta = block.get("meta") if isinstance(block.get("meta"), Mapping) else {}
    payload = dict(parsed)
    for key in (
        "event_id",
        "event_source_id",
        "event_type",
        "logical_path",
        "hosted_uri",
        "story_id",
        "reactive",
    ):
        if payload.get(key) in (None, "") and meta.get(key) not in (None, ""):
            payload[key] = meta.get(key)
    return payload


def _ret_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    ret = payload.get("ret")
    return ret if isinstance(ret, Mapping) else {}


def _surface_counts(payload: Mapping[str, Any]) -> list[str]:
    surfaces = payload.get("surfaces")
    if not isinstance(surfaces, Mapping):
        return []
    lines: list[str] = []
    for key in (
        "source_rows",
        "artifact_rows",
        "declared_file_items",
        "snapshot_refs",
        "announce_candidates",
        "notice_rows",
    ):
        value = surfaces.get(key)
        if isinstance(value, list) and value:
            lines.append(f"{key}: {len(value)}")
    return lines


def _event_refs(payload: Mapping[str, Any], ret: Mapping[str, Any]) -> dict[str, str]:
    refs: dict[str, str] = {}
    for out_key, candidates in {
        "event_ref": (payload.get("event_ref"), ret.get("event_ref")),
        "snapshot_ref": (ret.get("snapshot_ref"), ret.get("artifact_ref")),
        "canvas_ref": (ret.get("canvas_ref"), ret.get("latest_ref")),
    }.items():
        for value in candidates:
            value_str = str(value or "").strip()
            if value_str:
                refs[out_key] = value_str
                break
    return refs


def build_event_projection_text(
    block: Mapping[str, Any],
    *,
    label: str,
    semantic: str = "",
    include_ret_preview: bool = True,
    ret_preview_limit: int = 900,
) -> str:
    payload = _payload_from_block(block)
    meta = block.get("meta") if isinstance(block.get("meta"), Mapping) else {}
    ret = _ret_payload(payload)
    lines: list[str] = [label]

    ts = str(block.get("ts") or payload.get("ts") or "").strip()
    if ts:
        lines.append(f"[ts: {ts}]")
    path = str(block.get("path") or payload.get("logical_path") or meta.get("logical_path") or "").strip()
    if path:
        lines.append(f"[path: {path}]")

    event_type = str(payload.get("event_type") or block.get("type") or "event.external").strip()
    event_source_id = str(payload.get("event_source_id") or meta.get("event_source_id") or "").strip()
    event_id = str(payload.get("event_id") or meta.get("event_id") or block.get("event_id") or "").strip()
    status = str(payload.get("status") or ("error" if payload.get("error") else "success")).strip()
    story_id = str(payload.get("story_id") or meta.get("story_id") or "").strip()
    hosted_uri = str(payload.get("hosted_uri") or meta.get("hosted_uri") or "").strip()
    reactive = payload.get("reactive", meta.get("reactive"))
    segment = str(meta.get(TIMELINE_SEGMENT_META_KEY) or "").strip()

    for key, value in (
        ("event_type", event_type),
        ("event_source_id", event_source_id),
        ("event_id", event_id),
        ("status", status),
        ("story_id", story_id),
        ("hosted_uri", hosted_uri),
        ("segment", segment),
    ):
        if value not in ("", None):
            lines.append(f"{key}: {value}")
    if reactive is not None:
        lines.append(f"reactive: {'true' if bool(reactive) else 'false'}")

    for key, value in _event_refs(payload, ret).items():
        lines.append(f"{key}: {value}")

    summary = ""
    for key in ("summary", "title", "request", "message"):
        value = ret.get(key) if isinstance(ret, Mapping) else None
        if value:
            summary = _compact(value, max_chars=360)
            break
    if not summary and payload.get("error"):
        summary = _compact(payload.get("error"), max_chars=360)
    if summary:
        lines.append(f"summary: {summary}")

    counts = _surface_counts(payload)
    if counts:
        lines.append("surfaces:")
        lines.extend(f"- {line}" for line in counts)

    if semantic:
        lines.append(f"semantics: {semantic}")

    if include_ret_preview and ret:
        preview = _compact(ret, max_chars=ret_preview_limit)
        if preview and preview != summary:
            lines.append("payload_preview: " + preview)

    return "\n".join(lines).strip()


def project_event_blocks_as_text(
    timeline: list[MutableMapping[str, Any]],
    *,
    source: Any = None,
    block_types: set[str],
    label: str,
    semantic: str = "",
    policy_id: str,
    include_ret_preview: bool = True,
    ret_preview_limit: int = 900,
    call_meta: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[MutableMapping[str, Any]]:
    source_id = str(getattr(source, "event_source_id", "") or "").strip()
    for block in timeline or []:
        if not isinstance(block, MutableMapping):
            continue
        btype = str(block.get("type") or "").strip()
        if btype not in block_types:
            continue
        meta = block.get("meta") if isinstance(block.get("meta"), MutableMapping) else {}
        if meta.get(EVENT_RENDER_POLICY_SKIP_META_KEY):
            continue
        if source_id and not block_matches_event_source(block, source_id, call_meta=call_meta):
            continue
        if block.get("hidden") or meta.get("hidden"):
            continue
        text = block.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        parsed = _parse_json_object(text)
        if not parsed:
            continue
        if meta.get(EVENT_RENDER_POLICY_META_KEY) and (block.get("mime") or "") != "application/json":
            continue
        projected = build_event_projection_text(
            block,
            label=label,
            semantic=semantic,
            include_ret_preview=include_ret_preview,
            ret_preview_limit=ret_preview_limit,
        )
        if not projected:
            continue
        meta = dict(meta)
        meta[EVENT_RENDER_POLICY_META_KEY] = policy_id
        meta["render_as"] = "raw"
        block["meta"] = meta
        block["text"] = projected
        block["mime"] = "text/plain"
    return timeline


def apply_structural_event_render_defaults(
    timeline: list[MutableMapping[str, Any]],
    *,
    call_meta: Mapping[str, Mapping[str, Any]] | None = None,
    react_phase: str = "timeline_projection",
    **_: Any,
) -> list[MutableMapping[str, Any]]:
    """Apply SDK default render text to structural event blocks left as JSON.

    Registered event-source policies run first. This pass is the structural
    fallback for event blocks whose source has no render policy, or whose source
    is not registered in the runtime event-source subsystem.
    """
    project_event_blocks_as_text(
        timeline,
        block_types={"event.snapshot"},
        label="[SNAPSHOT EVENT]",
        semantic="read-only context event; exact snapshot content belongs in ANNOUNCE or behind the referenced artifact",
        policy_id=f"react.{react_phase}.snapshot_default.structural",
        include_ret_preview=False,
        call_meta=call_meta,
    )
    project_event_blocks_as_text(
        timeline,
        block_types={"event.canvas"},
        label="[CANVAS EVENT]",
        semantic="collaborative editable board event; current board projection belongs in ANNOUNCE",
        policy_id=f"react.{react_phase}.canvas_default.structural",
        include_ret_preview=False,
        call_meta=call_meta,
    )
    project_event_blocks_as_text(
        timeline,
        block_types={"event.external", "event.external.preserved"},
        label="[TIMELINE EVENT]",
        semantic="external event occurrence recorded on the ordered conversation lane",
        policy_id=f"react.{react_phase}.event_default.structural",
        include_ret_preview=True,
        ret_preview_limit=700,
        call_meta=call_meta,
    )
    return timeline
