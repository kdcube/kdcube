# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.react.events.common import block_event_source_id
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies.rendering_common import (
    apply_structural_event_render_defaults,
)


TIMELINE_SEGMENT_META_KEY = "_react_timeline_segment"
TimelineSegmentFn = Callable[[Mapping[str, Any]], str]


def event_source_ids_from_timeline(
    blocks: list[Mapping[str, Any]],
    *,
    call_meta: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    """Return event-source ids in first-seen timeline order.

    Tool-backed blocks resolve through existing `tool_id` / `call_id`
    semantics; non-tool event blocks resolve through explicit
    `event_source_id`.
    """
    seen: set[str] = set()
    out: list[str] = []
    for block in blocks or []:
        if not isinstance(block, Mapping):
            continue
        event_source_id = block_event_source_id(block, call_meta=call_meta)
        if not event_source_id or event_source_id in seen:
            continue
        seen.add(event_source_id)
        out.append(event_source_id)
    return out


def patch_timeline_segment_marks(
    blocks: list[MutableMapping[str, Any]],
    *,
    timeline_segment_fn: TimelineSegmentFn,
) -> list[MutableMapping[str, Any]]:
    """Attach temporary timeline segment metadata before policy projection.

    The mark is runtime-only. Callers must remove it after the handlers run.
    """
    if not callable(timeline_segment_fn):
        return blocks
    for block in blocks or []:
        if not isinstance(block, MutableMapping):
            continue
        segment = str(timeline_segment_fn(block) or "").strip()
        if not segment:
            continue
        meta = block.get("meta") if isinstance(block.get("meta"), MutableMapping) else {}
        meta = dict(meta)
        meta[TIMELINE_SEGMENT_META_KEY] = segment
        block["meta"] = meta
    return blocks


def clear_timeline_segment_marks(blocks: list[MutableMapping[str, Any]]) -> list[MutableMapping[str, Any]]:
    """Remove only temporary timeline segment marks left for policy handlers."""
    for block in blocks or []:
        if not isinstance(block, MutableMapping):
            continue
        meta = block.get("meta")
        if not isinstance(meta, MutableMapping) or TIMELINE_SEGMENT_META_KEY not in meta:
            continue
        meta = dict(meta)
        meta.pop(TIMELINE_SEGMENT_META_KEY, None)
        if meta:
            block["meta"] = meta
        else:
            block.pop("meta", None)
    return blocks


def apply_event_source_transformers(
    *,
    event_sources: Any,
    react_phase: str,
    timeline_blocks: list[MutableMapping[str, Any]],
    **context: Any,
) -> list[MutableMapping[str, Any]]:
    """Run one supported ReAct phase transformer over the mutable timeline/list.

    Handlers receive the same `timeline_blocks` object as their target and may
    patch it inline. This helper does not group, filter, or interpret blocks.
    """
    if event_sources is None or not timeline_blocks:
        return timeline_blocks
    call_meta = context.get("call_meta") if isinstance(context.get("call_meta"), Mapping) else None
    for event_source_id in event_source_ids_from_timeline(timeline_blocks, call_meta=call_meta):
        try:
            event_sources.apply_react_phase_policies(react_phase, event_source_id, timeline_blocks, **context)
        except Exception:
            continue
    if react_phase in {"timeline_projection", "compaction_projection"}:
        structural_context = dict(context)
        structural_context.pop("react_phase", None)
        structural_context["call_meta"] = call_meta
        apply_structural_event_render_defaults(
            timeline_blocks,
            react_phase=react_phase,
            **structural_context,
        )
    return timeline_blocks


def produce_event_source_announce_blocks(
    *,
    event_sources: Any,
    timeline_blocks: list[MutableMapping[str, Any]],
    **context: Any,
) -> list[MutableMapping[str, Any]]:
    """Let announce-production handlers append announce blocks."""
    if event_sources is None or not timeline_blocks:
        return []
    announce_blocks: list[MutableMapping[str, Any]] = []
    call_meta = context.get("call_meta") if isinstance(context.get("call_meta"), Mapping) else None
    for event_source_id in event_source_ids_from_timeline(timeline_blocks, call_meta=call_meta):
        try:
            event_sources.apply_react_phase_policies(
                "announce_production",
                event_source_id,
                announce_blocks,
                timeline_blocks=timeline_blocks,
                **context,
            )
        except Exception:
            continue
    return announce_blocks
