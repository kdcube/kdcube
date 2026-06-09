from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping
from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies import (
    announce_event_policy,
    block_production_policy,
    compaction_event_policy,
    timeline_projection_policy,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies.rendering_common import (
    EVENT_RENDER_POLICY_META_KEY,
)


DEFAULT_CANVAS_STATE_SOURCE_ID = "canvas.state"
DEFAULT_CANVAS_META_KEY = "canvas"
DEFAULT_CANVAS_ANNOUNCE_PATH_PREFIX = "announce:canvas"
DEFAULT_CANVAS_TOOL_SOURCE_IDS = ("canvas.read", "canvas.patch")


def _block_meta(block: Mapping[str, Any]) -> Mapping[str, Any]:
    meta = block.get("meta")
    return meta if isinstance(meta, Mapping) else {}


def _block_source_id(block: Mapping[str, Any]) -> str:
    meta = _block_meta(block)
    return str(block.get("event_source_id") or meta.get("event_source_id") or "").strip()


def _compact(value: Any, *, max_chars: int = 240) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, sort_keys=True, default=str)
        except Exception:
            value = str(value)
    text = " ".join(str(value or "").replace("\n", " ").split())
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip() + "..."


def _parse_block_json(block: Mapping[str, Any]) -> dict[str, Any]:
    text = block.get("text")
    if isinstance(text, str) and text.strip():
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _ret_mapping(target: Mapping[str, Any]) -> Mapping[str, Any]:
    ret = target.get("ret")
    if isinstance(ret, Mapping):
        return ret
    raw = target.get("raw")
    if isinstance(raw, Mapping):
        raw_ret = raw.get("ret")
        if isinstance(raw_ret, Mapping):
            return raw_ret
    return {}


def _canvas_payload_from_ret(ret: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(ret, Mapping):
        return {}
    event = ret.get("event") if isinstance(ret.get("event"), Mapping) else {}
    event_payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    event_data = event_payload.get("event") if isinstance(event_payload.get("event"), Mapping) else {}
    candidate = event_data or ret
    projection = candidate.get("projection") if isinstance(candidate.get("projection"), Mapping) else {}
    canvas_id = str(candidate.get("canvas_id") or projection.get("canvas_id") or "").strip()
    if not canvas_id and not projection:
        return {}
    return {
        "canvas_name": str(candidate.get("canvas_name") or projection.get("canvas_name") or "main"),
        "canvas_id": canvas_id,
        "canvas_uri": str(candidate.get("canvas_uri") or projection.get("canvas_uri") or "").strip(),
        "revision": candidate.get("revision") if candidate.get("revision") is not None else projection.get("revision"),
        "canvas_ref": str(candidate.get("canvas_ref") or ret.get("canvas_ref") or event_payload.get("event_ref") or "").strip(),
        "latest_ref": str(candidate.get("latest_ref") or ret.get("latest_ref") or "").strip(),
        "projection": dict(projection),
        "agent_view": str(candidate.get("agent_view") or ret.get("agent_view") or projection.get("text") or ""),
    }


def _compact_error(value: Any) -> Any:
    if not value:
        return None
    if isinstance(value, Mapping):
        return {
            key: value.get(key)
            for key in ("code", "message", "error")
            if value.get(key) not in (None, "")
        } or str(value)
    return str(value)


def _canvas_tool_fact(target: Mapping[str, Any], *, action: str, canvas: Mapping[str, Any]) -> dict[str, Any]:
    ret = _ret_mapping(target)
    projection = canvas.get("projection") if isinstance(canvas.get("projection"), Mapping) else {}
    changed = ret.get("changed") if isinstance(ret.get("changed"), list) else []
    error = target.get("error") or (ret.get("error") if isinstance(ret, Mapping) else None)
    ok = bool(target.get("ok") if target.get("ok") is not None else not error)
    fact = {
        "kind": "canvas_tool_result",
        "action": action,
        "status": "success" if ok else "error",
        "canvas_name": canvas.get("canvas_name") or projection.get("canvas_name"),
        "canvas_id": canvas.get("canvas_id") or projection.get("canvas_id"),
        "canvas_uri": canvas.get("canvas_uri") or projection.get("canvas_uri"),
        "revision": canvas.get("revision") if canvas.get("revision") is not None else projection.get("revision"),
        "cards_count": projection.get("cards_count"),
        "placed_count": projection.get("placed_count"),
        "floating_count": projection.get("floating_count"),
        "changed_count": len(changed),
    }
    final_params = target.get("final_params") if isinstance(target.get("final_params"), Mapping) else {}
    if action == "read":
        fact["requested_uri"] = str(
            final_params.get("uri")
            or final_params.get("canvas_uri")
            or final_params.get("path")
            or final_params.get("ref")
            or final_params.get("object_ref")
            or ""
        ).strip()
    if action == "patch":
        fact["base_revision"] = final_params.get("base_revision")
    compacted_error = _compact_error(error)
    if compacted_error:
        fact["error"] = compacted_error
    return {
        key: value
        for key, value in fact.items()
        if value is not None and (not isinstance(value, str) or value.strip())
    }


def append_canvas_tool_fact_block(
    target: MutableMapping[str, Any],
    *,
    action: str,
    canvas_meta_key: str = DEFAULT_CANVAS_META_KEY,
) -> MutableMapping[str, Any]:
    ret = _ret_mapping(target)
    canvas = _canvas_payload_from_ret(ret)
    fact = _canvas_tool_fact(target, action=action, canvas=canvas)
    tool_id = str(target.get("tool_id") or target.get("event_source_id") or f"canvas.{action}").strip()
    tool_call_id = str(target.get("tool_call_id") or target.get("event_id") or "").strip()
    turn_id = str(target.get("turn_id") or "").strip()
    path = str(target.get("tool_result_path") or "").strip()
    if action == "read":
        path = str(fact.get("requested_uri") or fact.get("canvas_uri") or "").strip()
    if not path and turn_id and tool_call_id:
        path = f"tc:{turn_id}.{tool_call_id}.result"
    blocks = target.setdefault("blocks", [])
    if isinstance(blocks, list):
        blocks.append(
            {
                "turn": turn_id,
                "type": "react.tool.result",
                "call_id": tool_call_id,
                "tool_id": tool_id,
                "event_source_id": tool_id,
                "mime": "application/json",
                "path": path,
                "text": json.dumps(fact, ensure_ascii=False, indent=2, default=str),
                "meta": {
                    "tool_call_id": tool_call_id,
                    "tool_id": tool_id,
                    "event_source_id": tool_id,
                    "canvas_action": action,
                    str(canvas_meta_key or DEFAULT_CANVAS_META_KEY): {
                        "retention": "turn",
                        "payload": canvas,
                    },
                },
            }
        )
    target["blocks_produced"] = True
    target["result_items"] = []
    target["result_items_produced"] = True
    target["declared_file_items"] = []
    target["declared_file_items_produced"] = True
    return target


def _event_payload_candidate(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    event = value.get("event") if isinstance(value.get("event"), Mapping) else {}
    if isinstance(event.get("payload"), Mapping):
        event_payload = event["payload"]
        if isinstance(event_payload.get("event"), Mapping):
            return dict(event_payload["event"])
    if isinstance(value.get("payload"), Mapping):
        event_payload = value["payload"]
        if isinstance(event_payload.get("event"), Mapping):
            return dict(event_payload["event"])
    if isinstance(value.get("ret"), Mapping):
        nested = _event_payload_candidate(value["ret"])
        if nested:
            return nested
        return dict(value["ret"])
    if event:
        return dict(event)
    if value.get("projection") and value.get("canvas_id"):
        return dict(value)
    return {}


def _block_turn_id(block: Mapping[str, Any]) -> str:
    return str(block.get("turn_id") or block.get("turn") or "").strip()


def _block_canvas_payload(
    block: Mapping[str, Any],
    *,
    canvas_meta_keys: tuple[str, ...] = (DEFAULT_CANVAS_META_KEY,),
) -> dict[str, Any]:
    meta = _block_meta(block)
    holder: Mapping[str, Any] = {}
    for key in canvas_meta_keys:
        candidate = meta.get(key)
        if isinstance(candidate, Mapping):
            holder = candidate
            break
    payload = holder.get("payload") if isinstance(holder.get("payload"), Mapping) else {}
    if payload:
        return dict(payload)
    parsed = _parse_block_json(block)
    return _event_payload_candidate(parsed)


def _latest_canvas_payload(
    timeline_blocks: list[MutableMapping[str, Any]],
    *,
    event_source_id: str,
    current_turn_id: str = "",
    accepted_source_ids: tuple[str, ...] = DEFAULT_CANVAS_TOOL_SOURCE_IDS,
    canvas_meta_keys: tuple[str, ...] = (DEFAULT_CANVAS_META_KEY,),
) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    accepted_sources = {event_source_id, *accepted_source_ids}
    for block in timeline_blocks:
        if not isinstance(block, Mapping) or _block_source_id(block) not in accepted_sources:
            continue
        if current_turn_id and _block_turn_id(block) and _block_turn_id(block) != current_turn_id:
            continue
        payload = _parse_block_json(block)
        candidate = _block_canvas_payload(block, canvas_meta_keys=canvas_meta_keys)
        if candidate:
            latest = {
                **candidate,
                "_event_id": payload.get("event_id") or _block_meta(block).get("event_id") or block.get("event_id"),
                "_logical_path": payload.get("logical_path") or _block_meta(block).get("logical_path") or block.get("path"),
                "_event_ref": payload.get("event_ref") or candidate.get("canvas_ref") or candidate.get("event_ref"),
            }
    return latest


@block_production_policy(
    event_policy_id="canvas.block_production.read_result",
    description="Persist only a compact canvas.read fact; keep board view for turn-scoped ANNOUNCE.",
)
def canvas_read_block_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    return append_canvas_tool_fact_block(target, action="read")


@block_production_policy(
    event_policy_id="canvas.block_production.patch_result",
    description="Persist only a compact canvas.patch fact; keep resulting board view for turn-scoped ANNOUNCE.",
)
def canvas_patch_block_policy(
    target: MutableMapping[str, Any],
    **_: Any,
) -> MutableMapping[str, Any]:
    return append_canvas_tool_fact_block(target, action="patch")


def _canvas_counts(projection: Mapping[str, Any]) -> tuple[int | None, int | None, int | None]:
    def _maybe_int(value: Any) -> int | None:
        try:
            return int(value)
        except Exception:
            return None

    return (
        _maybe_int(projection.get("cards_count")),
        _maybe_int(projection.get("placed_count")),
        _maybe_int(projection.get("floating_count")),
    )


def _selected_card_ids(projection: Mapping[str, Any]) -> list[str]:
    legend = projection.get("legend") if isinstance(projection.get("legend"), list) else []
    out: list[str] = []
    for row in legend:
        if isinstance(row, Mapping) and row.get("selected"):
            card_id = str(row.get("id") or "").strip()
            if card_id:
                out.append(card_id)
    return out


def _projection_stats_line(projection: Mapping[str, Any]) -> str:
    cards_count, placed_count, floating_count = _canvas_counts(projection)
    parts: list[str] = []
    if cards_count is not None:
        parts.append(f"cards={cards_count}")
    if placed_count is not None:
        parts.append(f"placed={placed_count}")
    if floating_count is not None:
        parts.append(f"floating={floating_count}")
    selected = _selected_card_ids(projection)
    if selected:
        parts.append("selected=" + ",".join(selected[:12]))
    return " ".join(parts)


def _set_projected_text(block: MutableMapping[str, Any], *, text: str, policy_id: str) -> None:
    meta = dict(_block_meta(block))
    meta[EVENT_RENDER_POLICY_META_KEY] = policy_id
    meta["render_as"] = "raw"
    block["meta"] = meta
    block["mime"] = "text/plain"
    block["text"] = text.strip()


def project_canvas_state_blocks(
    timeline: list[MutableMapping[str, Any]],
    *,
    source: Any,
    react_phase: str = "timeline_projection",
    policy_prefix: str = "canvas",
    default_event_source_id: str = DEFAULT_CANVAS_STATE_SOURCE_ID,
    canvas_meta_keys: tuple[str, ...] = (DEFAULT_CANVAS_META_KEY,),
    **_: Any,
) -> list[MutableMapping[str, Any]]:
    event_source_id = str(getattr(source, "event_source_id", "") or default_event_source_id)
    policy_id = f"{policy_prefix}.{react_phase}.canvas_state"
    for block in timeline or []:
        if not isinstance(block, MutableMapping) or _block_source_id(block) != event_source_id:
            continue
        if str(block.get("type") or "") != "event.canvas":
            continue
        canvas = _block_canvas_payload(block, canvas_meta_keys=canvas_meta_keys)
        projection = canvas.get("projection") if isinstance(canvas.get("projection"), Mapping) else {}
        parsed = _parse_block_json(block)
        canvas_name = str(canvas.get("canvas_name") or projection.get("canvas_name") or "main")
        canvas_id = str(canvas.get("canvas_id") or projection.get("canvas_id") or "").strip()
        canvas_uri = str(canvas.get("canvas_uri") or projection.get("canvas_uri") or "").strip()
        revision = canvas.get("revision") if canvas.get("revision") is not None else projection.get("revision")
        canvas_ref = str(canvas.get("canvas_ref") or parsed.get("event_ref") or parsed.get("hosted_uri") or "").strip()
        latest_ref = str(canvas.get("latest_ref") or "").strip()
        story_id = str(canvas.get("story_id") or parsed.get("story_id") or _block_meta(block).get("story_id") or "").strip()
        lines = [
            "[CANVAS STATE]",
            f"canvas_name: {canvas_name}",
        ]
        if canvas_id:
            lines.append(f"canvas_id: {canvas_id}")
        if canvas_uri:
            lines.append(f"canvas_uri: {canvas_uri}")
        if revision not in (None, ""):
            lines.append(f"revision: {revision}")
        stats = _projection_stats_line(projection)
        if stats:
            lines.append(stats)
        if story_id:
            lines.append(f"story_id: {story_id}")
        if canvas_ref:
            lines.append(f"canvas_ref: {canvas_ref}")
        if latest_ref:
            lines.append(f"latest_ref: {latest_ref}")
        event_id = str(parsed.get("event_id") or _block_meta(block).get("event_id") or block.get("event_id") or "").strip()
        if event_id:
            lines.append(f"event_id: {event_id}")
        lines.extend(
            [
                "timeline_semantics: canvas revision fact only; current board map and legend are in ANNOUNCE.",
                "edit_semantics: use canvas.patch with the announced revision; do not rewrite canvas JSON directly.",
            ]
        )
        _set_projected_text(block, text="\n".join(lines), policy_id=policy_id)
    return timeline


@compaction_event_policy(
    event_policy_id="canvas.compaction_projection.state",
    description="Render canvas state events as compact canvas revision facts for compaction.",
)
@timeline_projection_policy(
    event_policy_id="canvas.timeline_projection.state",
    description="Render canvas state events as compact canvas revision facts.",
)
def canvas_state_projection_policy(
    timeline: list[MutableMapping[str, Any]],
    *,
    source: Any,
    react_phase: str = "timeline_projection",
    **kwargs: Any,
) -> list[MutableMapping[str, Any]]:
    return project_canvas_state_blocks(timeline, source=source, react_phase=react_phase, **kwargs)


def project_canvas_tool_result_blocks(
    timeline: list[MutableMapping[str, Any]],
    *,
    source: Any,
    react_phase: str = "timeline_projection",
    policy_prefix: str = "canvas",
    accepted_source_ids: tuple[str, ...] = DEFAULT_CANVAS_TOOL_SOURCE_IDS,
    canvas_meta_keys: tuple[str, ...] = (DEFAULT_CANVAS_META_KEY,),
    **_: Any,
) -> list[MutableMapping[str, Any]]:
    source_id = str(getattr(source, "event_source_id", "") or "")
    if source_id not in set(accepted_source_ids):
        return timeline
    policy_id = f"{policy_prefix}.{react_phase}.canvas_tool_result"
    for block in timeline or []:
        if not isinstance(block, MutableMapping) or _block_source_id(block) != source_id:
            continue
        if str(block.get("type") or "") != "react.tool.result":
            continue
        parsed = _parse_block_json(block)
        if parsed.get("kind") != "canvas_tool_result":
            continue
        canvas: dict[str, Any] = {}
        meta = _block_meta(block)
        for key in canvas_meta_keys:
            holder = meta.get(key)
            if isinstance(holder, Mapping) and isinstance(holder.get("payload"), Mapping):
                canvas = dict(holder.get("payload") or {})
                break
        projection = canvas.get("projection") if isinstance(canvas.get("projection"), Mapping) else {}
        action = str(parsed.get("action") or _block_meta(block).get("canvas_action") or source_id.rsplit(".", 1)[-1])
        status = str(parsed.get("status") or "success")
        canvas_name = str(parsed.get("canvas_name") or canvas.get("canvas_name") or projection.get("canvas_name") or "main")
        canvas_id = str(parsed.get("canvas_id") or canvas.get("canvas_id") or projection.get("canvas_id") or "").strip()
        canvas_uri = str(canvas.get("canvas_uri") or projection.get("canvas_uri") or parsed.get("requested_uri") or "").strip()
        revision = parsed.get("revision") if parsed.get("revision") is not None else canvas.get("revision")
        lines = [
            "[CANVAS TOOL RESULT]",
            f"action: {action}",
            f"status: {status}",
            f"canvas_name: {canvas_name}",
        ]
        if canvas_id:
            lines.append(f"canvas_id: {canvas_id}")
        if canvas_uri:
            lines.append(f"canvas_uri: {canvas_uri}")
        if revision not in (None, ""):
            lines.append(f"revision: {revision}")
        stats = _projection_stats_line(projection)
        if stats:
            lines.append(stats)
        elif any(parsed.get(key) is not None for key in ("cards_count", "placed_count", "floating_count")):
            lines.append(
                " ".join(
                    part
                    for part in (
                        f"cards={parsed.get('cards_count')}" if parsed.get("cards_count") is not None else "",
                        f"placed={parsed.get('placed_count')}" if parsed.get("placed_count") is not None else "",
                        f"floating={parsed.get('floating_count')}" if parsed.get("floating_count") is not None else "",
                    )
                    if part
                )
            )
        changed_count = parsed.get("changed_count")
        if changed_count is not None:
            lines.append(f"changed_count: {changed_count}")
        error_value = parsed.get("error")
        if error_value:
            lines.append("error: " + _compact(error_value, max_chars=320))
        lines.append("announce_effect: latest canvas board projection is refreshed in ANNOUNCE for this turn.")
        _set_projected_text(block, text="\n".join(lines), policy_id=policy_id)
    return timeline


@compaction_event_policy(
    event_policy_id="canvas.compaction_projection.tool_result",
    description="Render canvas.read/canvas.patch tool results as compact canvas operation facts for compaction.",
)
@timeline_projection_policy(
    event_policy_id="canvas.timeline_projection.tool_result",
    description="Render canvas.read/canvas.patch tool results as compact canvas operation facts.",
)
def canvas_tool_projection_policy(
    timeline: list[MutableMapping[str, Any]],
    *,
    source: Any,
    react_phase: str = "timeline_projection",
    **kwargs: Any,
) -> list[MutableMapping[str, Any]]:
    return project_canvas_tool_result_blocks(timeline, source=source, react_phase=react_phase, **kwargs)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _card_prefix(kind: Any) -> str:
    value = str(kind or "").strip()
    if value == "user.attachment":
        return "A"
    if value == "user.text":
        return "U"
    if value == "agent.text":
        return "R"
    if value == "file":
        return "F"
    if value == "memory":
        return "M"
    if value in {"source", "search.result"}:
        return "S"
    if value in {"issue.ref", "story.ref", "task.ref"}:
        return "T"
    return "O"


def _labelled_legend(projection: Mapping[str, Any]) -> list[dict[str, Any]]:
    legend = projection.get("legend") if isinstance(projection.get("legend"), list) else []
    counters: dict[str, int] = {}
    labelled: list[dict[str, Any]] = []
    for row in legend:
        if not isinstance(row, Mapping):
            continue
        copy = dict(row)
        existing = str(copy.get("map_label") or copy.get("label") or "").strip()
        if existing:
            copy["map_label"] = existing
            labelled.append(copy)
            continue
        prefix = _card_prefix(copy.get("kind"))
        counters[prefix] = counters.get(prefix, 0) + 1
        copy["map_label"] = f"{prefix}{counters[prefix]}"
        labelled.append(copy)
    return labelled


CANVAS_ANNOUNCE_CARD_LIMIT = 12


def _card_sort_ts(row: Mapping[str, Any]) -> float:
    for key in ("updated_at", "updatedAt", "created_at", "createdAt", "ts"):
        value = row.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str) and value.strip():
            try:
                from datetime import datetime

                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except Exception:
                try:
                    return float(value)
                except Exception:
                    pass
    return 0.0


def _recent_legend(projection: Mapping[str, Any], *, limit: int = CANVAS_ANNOUNCE_CARD_LIMIT) -> list[dict[str, Any]]:
    legend = _labelled_legend(projection)
    if limit <= 0 or len(legend) <= limit:
        return legend
    indexed = list(enumerate(legend))
    recent = sorted(indexed, key=lambda item: (_card_sort_ts(item[1]), item[0]), reverse=True)[:limit]
    return [row for _, row in sorted(recent, key=lambda item: item[0])]


def _canvas_map(
    *,
    projection: Mapping[str, Any],
    legend: list[dict[str, Any]] | None = None,
    cols: int = 24,
    rows: int = 12,
) -> list[str]:
    bounds = projection.get("bounds") if isinstance(projection.get("bounds"), Mapping) else {}
    bx = _num(bounds.get("x"), 0.0)
    by = _num(bounds.get("y"), 0.0)
    bw = max(1.0, _num(bounds.get("w"), 1600.0))
    bh = max(1.0, _num(bounds.get("h"), 1000.0))
    grid = [[".." for _ in range(cols)] for _ in range(rows)]
    rows_to_render = legend if legend is not None else _labelled_legend(projection)
    for row in rows_to_render:
        if not isinstance(row, Mapping) or str(row.get("placement") or "") != "placed":
            continue
        rect = row.get("rect") if isinstance(row.get("rect"), Mapping) else {}
        if not rect:
            continue
        token = str(row.get("map_label") or row.get("label") or row.get("id") or "??").strip() or "??"
        x = _num(rect.get("x"), bx)
        y = _num(rect.get("y"), by)
        w = max(1.0, _num(rect.get("w"), 1.0))
        h = max(1.0, _num(rect.get("h"), 1.0))
        c0 = max(0, min(cols - 1, int(((x - bx) / bw) * cols)))
        r0 = max(0, min(rows - 1, int(((y - by) / bh) * rows)))
        c1 = max(c0 + 1, min(cols, int(((x + w - bx) / bw) * cols) + 1))
        r1 = max(r0 + 1, min(rows, int(((y + h - by) / bh) * rows) + 1))
        for rr in range(r0, r1):
            for cc in range(c0, c1):
                grid[rr][cc] = token
    return [" ".join(row) for row in grid]


def _legend_lines(legend: list[dict[str, Any]], *, total_count: int) -> list[str]:
    lines: list[str] = []
    for row in legend:
        if not isinstance(row, Mapping):
            continue
        label = str(row.get("map_label") or row.get("label") or row.get("id") or "?").strip() or "?"
        card_id = str(row.get("id") or "").strip()
        placement = str(row.get("placement") or "placed").strip() or "placed"
        bits = [
            f"- {label}",
            str(row.get("kind") or "note"),
        ]
        if card_id and card_id != label:
            bits.append(f"card_id={card_id}")
        if placement != "placed":
            bits.append(placement)
        if row.get("selected"):
            bits.append("selected")
        if row.get("suggested") or row.get("placement") == "suggested":
            bits.append("pending_suggestion")
        if row.get("locked"):
            bits.append("locked")
        title = str(row.get("title") or "").strip()
        if title:
            bits.append(f"title={title}")
        description = str(row.get("description") or "").strip().replace("\n", " ")
        if description:
            bits.append("has_description")
        try:
            comments_count = int(row.get("comments_count") or 0)
        except Exception:
            comments_count = 0
        if comments_count > 0:
            bits.append(f"comments={comments_count}")
        ref = str(row.get("logical_path") or "").strip()
        if ref:
            bits.append(f"ref={ref}")
        mime = str(row.get("mime") or "").strip()
        if mime:
            bits.append(f"mime={mime}")
        size = row.get("content_size")
        try:
            size_int = int(size or 0)
        except Exception:
            size_int = 0
        if size_int > 0:
            bits.append(f"bytes={size_int}")
        lines.append(" ".join(bits))
        preview = str(row.get("content_preview") or "").strip().replace("\n", " ")
        if preview:
            lines.append(f"  visible: {preview[:500]}")
        if description:
            lines.append(f"  description: {description[:500]}")
    if total_count > len(legend):
        lines.append(f"- ... {total_count - len(legend)} older cards omitted from ANNOUNCE; use react.pull on the cnv: board ref, then read the returned fi: path for exact full board state.")
    return lines


def produce_canvas_announce_blocks(
    target: list[MutableMapping[str, Any]],
    *,
    timeline_blocks: list[MutableMapping[str, Any]],
    source: Any,
    current_turn_id: str = "",
    default_event_source_id: str = DEFAULT_CANVAS_STATE_SOURCE_ID,
    accepted_source_ids: tuple[str, ...] = DEFAULT_CANVAS_TOOL_SOURCE_IDS,
    canvas_meta_keys: tuple[str, ...] = (DEFAULT_CANVAS_META_KEY,),
    announce_path_prefix: str = DEFAULT_CANVAS_ANNOUNCE_PATH_PREFIX,
    **_: Any,
) -> list[MutableMapping[str, Any]]:
    event_source_id = str(getattr(source, "event_source_id", "") or default_event_source_id)
    canvas = _latest_canvas_payload(
        timeline_blocks,
        event_source_id=event_source_id,
        current_turn_id=str(current_turn_id or ""),
        accepted_source_ids=accepted_source_ids,
        canvas_meta_keys=canvas_meta_keys,
    )
    projection = canvas.get("projection") if isinstance(canvas.get("projection"), Mapping) else {}
    if not projection:
        return target

    canvas_name = str(canvas.get("canvas_name") or projection.get("canvas_name") or "main")
    canvas_id = str(canvas.get("canvas_id") or projection.get("canvas_id") or "")
    revision = str(canvas.get("revision") or projection.get("revision") or "0")
    canvas_ref = str(canvas.get("_event_ref") or canvas.get("canvas_ref") or "")
    canvas_uri = str(canvas.get("canvas_uri") or projection.get("canvas_uri") or f"cnv:{canvas_name}@{revision}")

    bounds = projection.get("bounds") if isinstance(projection.get("bounds"), Mapping) else {}
    full_legend = _labelled_legend(projection)
    recent_legend = _recent_legend(projection)
    cards_count = len(full_legend)
    header = [
        "[CANVAS BOARD]",
        f"canvas_name: {canvas_name}",
        f"canvas_id: {canvas_id}",
        f"canvas_uri: {canvas_uri}",
        f"revision: {revision}",
    ]
    if bounds:
        header.append(f"bounds: x={bounds.get('x')} y={bounds.get('y')} w={bounds.get('w')} h={bounds.get('h')}")
    if cards_count > len(recent_legend):
        header.append(f"showing: latest {len(recent_legend)} of {cards_count} cards by updated_at")
    else:
        header.append(f"showing: {cards_count} cards")

    lines = [
        *header,
        "",
        "spatial_map:",
        *_canvas_map(projection=projection, legend=recent_legend),
        "",
        "legend:",
        *_legend_lines(recent_legend, total_count=cards_count),
    ]
    announce_path = f"{announce_path_prefix.rstrip('/')}/{canvas_id or canvas_name}"
    if any(isinstance(block, Mapping) and block.get("path") == announce_path for block in target):
        return target
    target.append({
        "type": "announce.canvas",
        "path": announce_path,
        "text": "\n".join(lines),
        "meta": {
            "event_source_id": event_source_id,
            "canvas_id": canvas_id,
            "canvas_name": canvas_name,
            "canvas_uri": canvas_uri,
            "revision": revision,
            "canvas_revision_ref": canvas_ref,
        },
    })
    return target


@announce_event_policy(
    event_policy_id="canvas.announce.board_map",
    description="Render the latest canvas state as an announce board map plus map-label legend.",
)
def canvas_announce_policy(
    target: list[MutableMapping[str, Any]],
    *,
    timeline_blocks: list[MutableMapping[str, Any]],
    source: Any,
    current_turn_id: str = "",
    **kwargs: Any,
) -> list[MutableMapping[str, Any]]:
    return produce_canvas_announce_blocks(
        target,
        timeline_blocks=timeline_blocks,
        source=source,
        current_turn_id=current_turn_id,
        **kwargs,
    )


__all__ = [
    "append_canvas_tool_fact_block",
    "canvas_announce_policy",
    "canvas_read_block_policy",
    "canvas_patch_block_policy",
    "canvas_state_projection_policy",
    "canvas_tool_projection_policy",
    "produce_canvas_announce_blocks",
    "project_canvas_state_blocks",
    "project_canvas_tool_result_blocks",
]
