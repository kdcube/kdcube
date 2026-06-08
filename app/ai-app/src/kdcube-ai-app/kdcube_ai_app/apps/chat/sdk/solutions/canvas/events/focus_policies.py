from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping
from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies import (
    announce_event_policy,
    compaction_event_policy,
    timeline_projection_policy,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies.rendering_common import (
    EVENT_RENDER_POLICY_META_KEY,
)


DEFAULT_CANVAS_FOCUS_SOURCE_ID = "canvas.focus"
DEFAULT_CANVAS_FOCUS_ANNOUNCE_PATH_PREFIX = "announce:canvas-focus"
RESOLVER_REF_PREFIXES = ("ext:", "fi:", "mem:", "so:", "task:", "ar:", "ev:", "tc:")


def _block_meta(block: Mapping[str, Any]) -> Mapping[str, Any]:
    meta = block.get("meta")
    return meta if isinstance(meta, Mapping) else {}


def _block_source_id(block: Mapping[str, Any]) -> str:
    meta = _block_meta(block)
    return str(block.get("event_source_id") or meta.get("event_source_id") or "").strip()


def _block_turn_id(block: Mapping[str, Any]) -> str:
    return str(block.get("turn_id") or block.get("turn") or "").strip()


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


def _payload_candidate(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    ret = value.get("ret")
    if isinstance(ret, Mapping):
        return dict(ret)
    payload = value.get("payload")
    if isinstance(payload, Mapping):
        event_payload = payload.get("event")
        if isinstance(event_payload, Mapping):
            return dict(event_payload)
        return dict(payload)
    event = value.get("event")
    if isinstance(event, Mapping):
        nested = event.get("payload")
        if isinstance(nested, Mapping):
            nested_event = nested.get("event")
            if isinstance(nested_event, Mapping):
                return dict(nested_event)
            return dict(nested)
        return dict(event)
    return dict(value)


def _focused_cards(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = (
        payload.get("focused_cards")
        or payload.get("focused_pins")
        or payload.get("cards")
        or payload.get("items")
        or []
    )
    out: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping):
                out.append(dict(item))
            elif isinstance(item, str) and item.strip():
                out.append({"logical_path": item.strip()})
    refs = payload.get("focused_refs") or payload.get("refs")
    if isinstance(refs, list):
        for item in refs:
            if isinstance(item, str) and item.strip():
                out.append({"logical_path": item.strip()})
            elif isinstance(item, Mapping):
                out.append(dict(item))
    return out


def _card_line(card: Mapping[str, Any]) -> str:
    card_id = str(card.get("card_id") or card.get("id") or "?").strip() or "?"
    kind = str(card.get("kind") or card.get("type") or "ref").strip() or "ref"
    bits = [f"- {card_id}", kind]
    if card.get("selected"):
        bits.append("selected")
    title = str(card.get("title") or card.get("name") or "").strip()
    if title:
        bits.append(f"title={title}")
    ref = str(
        card.get("logical_path")
        or card.get("ref")
        or card.get("artifact_ref")
        or card.get("event_ref")
        or ""
    ).strip()
    if ref:
        bits.append(f"ref={ref}")
    mime = str(card.get("mime") or "").strip()
    if mime:
        bits.append(f"mime={mime}")
    preview = _compact(card.get("content_preview") or card.get("summary") or "", max_chars=180)
    if preview and preview != ref and not preview.startswith(RESOLVER_REF_PREFIXES):
        bits.append(f"preview={preview}")
    return " ".join(bits)


def _selection_line(payload: Mapping[str, Any]) -> str:
    selection = payload.get("selection") if isinstance(payload.get("selection"), Mapping) else {}
    if not selection:
        return ""
    parts: list[str] = []
    mode = str(selection.get("mode") or "").strip()
    reason = str(selection.get("reason") or "").strip()
    if mode:
        parts.append(f"mode={mode}")
    if reason:
        parts.append(f"reason={reason}")
    rect = selection.get("rect") if isinstance(selection.get("rect"), Mapping) else None
    if rect:
        parts.append("area=selected")
    return " ".join(parts)


def _set_projected_text(block: MutableMapping[str, Any], *, text: str, policy_id: str) -> None:
    meta = dict(_block_meta(block))
    meta[EVENT_RENDER_POLICY_META_KEY] = policy_id
    meta["render_as"] = "raw"
    block["meta"] = meta
    block["mime"] = "text/plain"
    block["text"] = text.strip()


def _focus_payload_from_block(block: Mapping[str, Any]) -> dict[str, Any]:
    parsed = _parse_block_json(block)
    candidate = _payload_candidate(parsed)
    if not candidate:
        return {}
    meta = _block_meta(block)
    candidate.setdefault("_event_id", parsed.get("event_id") or meta.get("event_id") or block.get("event_id"))
    candidate.setdefault("_logical_path", parsed.get("logical_path") or meta.get("logical_path") or block.get("path"))
    return candidate


def _focus_projection_lines(payload: Mapping[str, Any]) -> list[str]:
    canvas_name = str(payload.get("canvas_name") or "main").strip() or "main"
    canvas_id = str(payload.get("canvas_id") or "").strip()
    canvas_uri = str(payload.get("canvas_uri") or "").strip()
    revision = payload.get("revision")
    focused = _focused_cards(payload)
    lines = [
        "[CANVAS FOCUS]",
        f"canvas_name: {canvas_name}",
    ]
    if canvas_id:
        lines.append(f"canvas_id: {canvas_id}")
    if canvas_uri:
        lines.append(f"canvas_uri: {canvas_uri}")
    if revision not in (None, ""):
        lines.append(f"revision: {revision}")
    selection = _selection_line(payload)
    if selection:
        lines.append(f"selection: {selection}")
    event_id = str(payload.get("_event_id") or "").strip()
    if event_id:
        lines.append(f"event_id: {event_id}")
    logical_path = str(payload.get("_logical_path") or "").strip()
    if logical_path:
        lines.append(f"event_logical_path: {logical_path}")
    if focused:
        lines.append("focused_cards:")
        lines.extend(_card_line(card) for card in focused[:40])
        if len(focused) > 40:
            lines.append(f"- ... {len(focused) - 40} more focused cards")
    else:
        lines.append("focused_cards: none")
    return lines


def project_canvas_focus_blocks(
    timeline: list[MutableMapping[str, Any]],
    *,
    source: Any,
    react_phase: str = "timeline_projection",
    policy_prefix: str = "canvas",
    default_event_source_id: str = DEFAULT_CANVAS_FOCUS_SOURCE_ID,
    **_: Any,
) -> list[MutableMapping[str, Any]]:
    event_source_id = str(getattr(source, "event_source_id", "") or default_event_source_id)
    policy_id = f"{policy_prefix}.{react_phase}.canvas_focus"
    for block in timeline or []:
        if not isinstance(block, MutableMapping) or _block_source_id(block) != event_source_id:
            continue
        if str(block.get("type") or "") not in {"event.external", "event.canvas.focus"}:
            continue
        payload = _focus_payload_from_block(block)
        if not payload:
            continue
        _set_projected_text(
            block,
            text="\n".join(_focus_projection_lines(payload)),
            policy_id=policy_id,
        )
    return timeline


@compaction_event_policy(
    event_policy_id="canvas.compaction_projection.focus",
    description="Render canvas focus events as compact focused-context facts for compaction.",
)
@timeline_projection_policy(
    event_policy_id="canvas.timeline_projection.focus",
    description="Render canvas focus events as compact focused-context facts.",
)
def canvas_focus_projection_policy(
    timeline: list[MutableMapping[str, Any]],
    *,
    source: Any,
    react_phase: str = "timeline_projection",
    **kwargs: Any,
) -> list[MutableMapping[str, Any]]:
    return project_canvas_focus_blocks(timeline, source=source, react_phase=react_phase, **kwargs)


def _latest_focus_payload(
    timeline_blocks: list[MutableMapping[str, Any]],
    *,
    event_source_id: str,
    current_turn_id: str = "",
) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for block in timeline_blocks or []:
        if not isinstance(block, Mapping) or _block_source_id(block) != event_source_id:
            continue
        if current_turn_id and _block_turn_id(block) and _block_turn_id(block) != current_turn_id:
            continue
        payload = _focus_payload_from_block(block)
        if payload:
            latest = payload
    return latest


def produce_canvas_focus_announce_blocks(
    target: list[MutableMapping[str, Any]],
    *,
    timeline_blocks: list[MutableMapping[str, Any]],
    source: Any,
    current_turn_id: str = "",
    default_event_source_id: str = DEFAULT_CANVAS_FOCUS_SOURCE_ID,
    announce_path_prefix: str = DEFAULT_CANVAS_FOCUS_ANNOUNCE_PATH_PREFIX,
    **_: Any,
) -> list[MutableMapping[str, Any]]:
    event_source_id = str(getattr(source, "event_source_id", "") or default_event_source_id)
    focus = _latest_focus_payload(
        timeline_blocks,
        event_source_id=event_source_id,
        current_turn_id=str(current_turn_id or ""),
    )
    if not focus:
        return target
    focused = _focused_cards(focus)
    if not focused:
        return target
    lines = ["[CANVAS FOCUSED CONTEXT]"]
    lines.extend(_focus_projection_lines(focus)[1:])
    canvas_id = str(focus.get("canvas_id") or focus.get("canvas_name") or "main")
    path = f"{announce_path_prefix.rstrip('/')}/{canvas_id}"
    if any(isinstance(block, Mapping) and block.get("path") == path for block in target):
        return target
    target.append(
        {
            "type": "announce.canvas_focus",
            "path": path,
            "text": "\n".join(lines),
            "meta": {
                "event_source_id": event_source_id,
                "canvas_id": focus.get("canvas_id"),
                "canvas_name": focus.get("canvas_name"),
                "canvas_uri": focus.get("canvas_uri"),
                "revision": focus.get("revision"),
                "focused_count": len(focused),
            },
        }
    )
    return target


@announce_event_policy(
    event_policy_id="canvas.announce.focus",
    description="Render the latest canvas focus selection as turn-local announce context.",
)
def canvas_focus_announce_policy(
    target: list[MutableMapping[str, Any]],
    *,
    timeline_blocks: list[MutableMapping[str, Any]],
    source: Any,
    current_turn_id: str = "",
    **kwargs: Any,
) -> list[MutableMapping[str, Any]]:
    return produce_canvas_focus_announce_blocks(
        target,
        timeline_blocks=timeline_blocks,
        source=source,
        current_turn_id=current_turn_id,
        **kwargs,
    )


__all__ = [
    "canvas_focus_announce_policy",
    "canvas_focus_projection_policy",
    "produce_canvas_focus_announce_blocks",
    "project_canvas_focus_blocks",
]
