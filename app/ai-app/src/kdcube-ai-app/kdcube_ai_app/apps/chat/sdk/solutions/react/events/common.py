# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}


def event_source_pipeline_enabled(owner: Any = None) -> bool:
    """Return whether ReAct should use the alternate event-source pipeline.

    The default is intentionally off. Runtime code may opt in through
    `RuntimeCtx.event_source_pipeline_enabled`, a matching attribute on a
    ReAct runtime object, or `KDCUBE_REACT_EVENT_SOURCE_PIPELINE=1` for local
    experiments.
    """

    def _bool_attr(obj: Any) -> bool | None:
        if obj is None:
            return None
        value = getattr(obj, "event_source_pipeline_enabled", None)
        if value is not None:
            return bool(value)
        runtime_ctx = getattr(obj, "runtime_ctx", None)
        if runtime_ctx is not None:
            value = getattr(runtime_ctx, "event_source_pipeline_enabled", None)
            if value is not None:
                return bool(value)
        ctx_browser = getattr(obj, "ctx_browser", None)
        if ctx_browser is not None:
            runtime_ctx = getattr(ctx_browser, "runtime_ctx", None)
            value = getattr(runtime_ctx, "event_source_pipeline_enabled", None)
            if value is not None:
                return bool(value)
        return None

    explicit = _bool_attr(owner)
    if explicit is not None:
        return bool(explicit)
    raw = (
        os.getenv("AI_REACT_EVENT_SOURCE_PIPELINE_ENABLED")
        or os.getenv("KDCUBE_REACT_EVENT_SOURCE_PIPELINE")
        or ""
    )
    return str(raw).strip().lower() in _TRUE_VALUES


def event_identity_fields(
    *,
    event_source_id: str,
    event_id: str,
    story_id: str | None = None,
) -> dict[str, str]:
    """Build the common policy identity fields for timeline blocks."""
    fields = {
        "event_source_id": str(event_source_id or "").strip(),
        "event_id": str(event_id or "").strip(),
    }
    story = str(story_id or "").strip()
    if story:
        fields["story_id"] = story
    return {k: v for k, v in fields.items() if v}


def block_event_source_id(
    block: Mapping[str, Any] | None,
    *,
    call_meta: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    """Resolve the policy source id for a timeline block.

    Tool-backed blocks do not need durable `event_source_id` duplication:
    their event source is the existing `tool_id`, and result/file blocks can
    recover that tool id from `call_id` plus the caller-provided call metadata.
    Non-tool events use explicit `event_source_id`.
    """
    if not isinstance(block, Mapping):
        return ""
    explicit = str(block.get("event_source_id") or "").strip()
    if explicit:
        return explicit
    tool_id = str(block.get("tool_id") or "").strip()
    if tool_id:
        return tool_id
    meta = block.get("meta") if isinstance(block.get("meta"), Mapping) else {}
    tool_id = str(meta.get("tool_id") or "").strip()
    if tool_id:
        return tool_id
    call_id = str(block.get("call_id") or meta.get("tool_call_id") or "").strip()
    if call_id and isinstance(call_meta, Mapping):
        row = call_meta.get(call_id)
        if isinstance(row, Mapping):
            return str(row.get("tool_id") or "").strip()
    return ""


def block_event_id(block: Mapping[str, Any] | None) -> str:
    """Resolve the occurrence id for a timeline block.

    Tool-backed blocks use the existing `call_id` / `tool_call_id`; non-tool
    events use explicit `event_id`.
    """
    if not isinstance(block, Mapping):
        return ""
    explicit = str(block.get("event_id") or "").strip()
    if explicit:
        return explicit
    meta = block.get("meta") if isinstance(block.get("meta"), Mapping) else {}
    return str(block.get("call_id") or meta.get("tool_call_id") or "").strip()


def block_matches_event_source(
    block: Mapping[str, Any] | None,
    event_source_id: str,
    *,
    call_meta: Mapping[str, Mapping[str, Any]] | None = None,
) -> bool:
    """Return true when a block belongs to the given policy source."""
    source_id = str(event_source_id or "").strip()
    return bool(source_id and block_event_source_id(block, call_meta=call_meta) == source_id)


def stamp_event_identity(
    block: MutableMapping[str, Any],
    *,
    event_source_id: str,
    event_id: str,
    story_id: str | None = None,
) -> MutableMapping[str, Any]:
    """Attach event-source identity to one already-shaped timeline block.

    This does not choose the physical block shape. Existing renderers still use
    `block["type"]`; event-source policy lookup uses `event_source_id`; and
    occurrence grouping uses `event_id`.
    """
    block.update(event_identity_fields(
        event_source_id=event_source_id,
        event_id=event_id,
        story_id=story_id,
    ))
    return block


def stamp_event_identity_many(
    blocks: Iterable[MutableMapping[str, Any]],
    *,
    event_source_id: str,
    event_id: str,
    story_id: str | None = None,
) -> list[MutableMapping[str, Any]]:
    """Attach the same event identity to each block in an occurrence group."""
    return [
        stamp_event_identity(
            block,
            event_source_id=event_source_id,
            event_id=event_id,
            story_id=story_id,
        )
        for block in blocks
    ]
