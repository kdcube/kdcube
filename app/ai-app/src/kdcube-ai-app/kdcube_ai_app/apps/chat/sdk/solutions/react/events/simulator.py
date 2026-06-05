# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Dry-run rendering helpers for authored ReAct external events.

The simulator mirrors the event-source block-production and rendering path
without writing conversation timeline state or invoking a ReAct model turn.
Bundles can use it to show users what a proposed `external_events[]` batch
would contribute to the model-facing timeline and non-cached ANNOUNCE tail.
"""

from __future__ import annotations

import json
import pathlib
import time
from collections.abc import Mapping
from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.react.events.common import stamp_event_identity_many
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.core import (
    REACT_USER_ATTACHMENT_EVENT_SOURCE_ID,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _event_text(event: Mapping[str, Any]) -> str:
    payload = _as_dict(event.get("payload"))
    body = payload.get("event")
    if isinstance(body, Mapping):
        return str(body.get("text") or body.get("message") or body.get("request") or "").strip()
    if body is not None:
        return str(body or "").strip()
    return str(event.get("text") or "").strip()


def _event_ret(event: Mapping[str, Any]) -> Any:
    payload = _as_dict(event.get("payload"))
    if "ret" in payload:
        return payload.get("ret")
    if "event" in payload:
        return payload.get("event")
    if payload.get("event_ref"):
        return {"event_ref": payload.get("event_ref")}
    return {}


def _event_attachment_row(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = _as_dict(event.get("payload"))
    body = _as_dict(payload.get("event"))
    name = str(body.get("filename") or body.get("name") or event.get("filename") or "attachment.bin").strip()
    mime = str(body.get("mime") or payload.get("mime") or event.get("mime") or "application/octet-stream").strip()
    row: dict[str, Any] = {
        "filename": pathlib.PurePosixPath(name).name or "attachment.bin",
        "mime": mime,
    }
    size = body.get("size") if body.get("size") is not None else event.get("size")
    if size is not None:
        row["size"] = size
    summary = body.get("summary") or event.get("summary")
    if summary:
        row["summary"] = str(summary)
    for key in ("hosted_uri", "rn", "key", "physical_path"):
        if event.get(key):
            row[key] = event.get(key)
    return row


def _logical_path(*, turn_id: str, event_id: str, event: Mapping[str, Any]) -> str:
    value = str(event.get("logical_path") or event.get("logicalPath") or "").strip()
    if value:
        return value
    return f"ev:{turn_id}.events/{event_id}" if turn_id and event_id else ""


def _block_path(*, turn_id: str, event_id: str, block_type: str, logical_path: str) -> str:
    if not turn_id:
        return logical_path
    if block_type == "event.user.prompt":
        return f"ar:{turn_id}.user.prompt.{event_id}" if event_id else f"ar:{turn_id}.user.prompt"
    if block_type == "event.user.followup":
        return f"ar:{turn_id}.external.followup.{event_id}" if event_id else f"ar:{turn_id}.external.followup"
    if block_type == "event.user.steer":
        return f"ar:{turn_id}.external.steer.{event_id}" if event_id else f"ar:{turn_id}.external.steer"
    return logical_path


async def _apply_default_event_block_policy(target: dict[str, Any]) -> None:
    from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies import (
        canvas_event_default_block_production_policy,
        external_event_default_block_production_policy,
        snapshot_event_default_block_production_policy,
        user_attachment_default_block_production_policy,
        user_followup_default_block_production_policy,
        user_prompt_default_block_production_policy,
        user_steer_default_block_production_policy,
    )

    block_type = str(target.get("block_type") or "").strip()
    if block_type == "event.snapshot":
        snapshot_event_default_block_production_policy(target)
    elif block_type == "event.canvas":
        canvas_event_default_block_production_policy(target)
    elif block_type == "event.user.prompt":
        user_prompt_default_block_production_policy(target)
    elif block_type == "event.user.followup":
        user_followup_default_block_production_policy(target)
    elif block_type == "event.user.steer":
        user_steer_default_block_production_policy(target)
    elif block_type.startswith("event.user.attachment"):
        user_attachment_default_block_production_policy(target)
    else:
        external_event_default_block_production_policy(target)


async def _produce_event_blocks(
    *,
    event_sources: Any,
    runtime: RuntimeCtx,
    timeline: Timeline,
    event: Mapping[str, Any],
    index: int,
    timestamp: str,
) -> list[dict[str, Any]]:
    turn_id = str(runtime.turn_id or "").strip()
    event_id = str(event.get("event_id") or event.get("id") or f"ev_{index + 1}").strip()
    event_source_id = str(event.get("event_source_id") or "").strip() or "react.external_event"
    block_type = str(event.get("type") or event.get("event_type") or "").strip() or "event.external"
    logical_path = _logical_path(turn_id=turn_id, event_id=event_id, event=event)
    block_path = _block_path(turn_id=turn_id, event_id=event_id, block_type=block_type, logical_path=logical_path)
    hosted_uri = str(event.get("hosted_uri") or event.get("hostedUri") or "").strip()
    story_id = str(event.get("story_id") or event.get("storyId") or "").strip()
    reactive = bool(event.get("reactive", False))
    text = _event_text(event)
    payload = _as_dict(event.get("payload"))
    meta = {
        "event_kind": "external_event",
        "event_type": block_type,
        "event_source_id": event_source_id,
        "event_id": event_id,
        "sequence": index + 1,
        "source": "dry_run",
        "reactive": reactive,
        "event": dict(event),
    }
    if hosted_uri:
        meta["hosted_uri"] = hosted_uri
    if logical_path:
        meta["logical_path"] = logical_path
    if story_id:
        meta["story_id"] = story_id

    target = {
        "event": dict(event),
        "event_source_id": event_source_id,
        "event_id": event_id,
        "block_type": block_type,
        "logical_path": logical_path,
        "hosted_uri": hosted_uri,
        "story_id": story_id,
        "reactive": reactive,
        "text": text,
        "path": block_path,
        "turn_id": turn_id,
        "ts": str(event.get("timestamp") or event.get("ts") or timestamp),
        "mime": str(payload.get("mime") or "text/markdown"),
        "author": "user",
        "ok": payload.get("ok", event.get("ok", True)),
        "error": payload.get("error", event.get("error")),
        "ret": _event_ret(event),
        "raw": {
            "ok": payload.get("ok", event.get("ok", True)),
            "ret": _event_ret(event),
            "error": payload.get("error", event.get("error")),
            "event": dict(event),
        },
        "meta": meta,
        "blocks": [],
        "block_factory": timeline.block,
    }

    source = None
    if event_sources is not None:
        by_event_source_id = getattr(event_sources, "by_event_source_id", None)
        if callable(by_event_source_id):
            try:
                source = by_event_source_id(event_source_id)
            except Exception:
                source = None
    if source is not None:
        await event_sources.apply_react_phase_policies_async(
            "block_production",
            event_source_id,
            target,
            runtime_ctx=runtime,
            ctx_browser=None,
            timeline=timeline,
        )
    if not target.get("blocks") and not target.get("blocks_produced"):
        await _apply_default_event_block_policy(target)

    blocks = [dict(block) for block in (target.get("blocks") or []) if isinstance(block, Mapping)]
    if block_type.startswith("event.user.attachment"):
        from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies import (
            user_attachment_default_block_production_policy,
        )

        attachment_target = {
            "event": dict(event),
            "event_source_id": REACT_USER_ATTACHMENT_EVENT_SOURCE_ID,
            "event_id": event_id,
            "block_type": "event.user.attachment",
            "logical_path": f"fi:{turn_id}.user.attachments/{event_id}",
            "story_id": story_id,
            "reactive": False,
            "turn_id": turn_id,
            "ts": str(event.get("timestamp") or event.get("ts") or timestamp),
            "attachments": [_event_attachment_row(event)],
            "path_root": f"fi:{turn_id}.user.attachments/{event_id}",
            "physical_root": f"{turn_id}/attachments/{event_id}",
            "meta_extra": {
                "event_kind": "external_event",
                "event_type": "event.user.attachment",
                "event_source_id": event_source_id,
                "message_id": event_id,
                "sequence": index + 1,
                "attachment_origin": "dry_run",
            },
            "blocks": [],
            "block_factory": timeline.block,
        }
        user_attachment_default_block_production_policy(attachment_target)
        blocks.extend(
            dict(block)
            for block in (attachment_target.get("blocks") or [])
            if isinstance(block, Mapping)
        )

    if blocks:
        stamp_event_identity_many(
            blocks,
            event_source_id=event_source_id,
            event_id=event_id,
            story_id=story_id or None,
        )
    return blocks


def _debug_text(timeline: Timeline, blocks: list[dict[str, Any]]) -> str:
    del timeline
    lines: list[str] = []
    cache_idx = 0
    for block in blocks or []:
        if not isinstance(block, Mapping):
            continue
        prefix = "   "
        if block.get("cache"):
            cache_idx += 1
            prefix = f"=>[{cache_idx}] "
        block_type = block.get("type") or "text"
        if block_type == "text":
            lines.append(prefix + str(block.get("text") or ""))
        else:
            lines.append(prefix + f"<{block_type}>")
    return "\n".join(lines).rstrip()


def _join_text(blocks: list[dict[str, Any]]) -> str:
    return "\n\n".join(str(block.get("text") or "") for block in blocks if isinstance(block, Mapping) and block.get("text"))


def _write_debug_files(
    *,
    debug_dir: str | pathlib.Path | None,
    prefix: str,
    timeline_text: str,
    announce_text: str,
    rendered_text: str,
) -> dict[str, str]:
    if not debug_dir:
        return {}
    root = pathlib.Path(debug_dir)
    root.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for name, text in (
        ("timeline", timeline_text),
        ("announce", announce_text),
        ("full", rendered_text),
    ):
        path = root / f"{prefix}-{name}.txt"
        path.write_text(text or "", encoding="utf-8")
        written[name] = str(path)
    return written


async def render_external_events_dry_run(
    *,
    external_events: list[Mapping[str, Any]],
    event_sources: Any = None,
    runtime: RuntimeCtx | None = None,
    tenant: str | None = None,
    project: str | None = None,
    user_id: str | None = None,
    conversation_id: str | None = None,
    turn_id: str | None = None,
    bundle_id: str | None = None,
    agent_id: str | None = None,
    debug_dir: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    """Render a proposed `external_events[]` batch without running ReAct.

    The returned `timeline_text` is the cached timeline view. `announce_text`
    is the volatile non-cached tail produced by announce policies. The durable
    `raw_blocks` are included for debugging only; callers should not persist
    them as conversation state.
    """

    timestamp = _utc_now()
    runtime = runtime or RuntimeCtx()
    runtime.tenant = runtime.tenant or tenant
    runtime.project = runtime.project or project
    runtime.user_id = runtime.user_id or user_id
    runtime.conversation_id = runtime.conversation_id or conversation_id or "dry_run_conversation"
    runtime.turn_id = runtime.turn_id or turn_id or f"turn_dry_run_{int(time.time())}"
    runtime.bundle_id = runtime.bundle_id or bundle_id
    runtime.agent_id = runtime.agent_id or agent_id or runtime.agent_id
    runtime.started_at = runtime.started_at or timestamp
    runtime.event_sources = runtime.event_sources or event_sources
    event_sources = event_sources or runtime.event_sources
    runtime.event_source_pipeline_enabled = True
    from kdcube_ai_app.apps.chat.sdk.solutions.react.timeline import Timeline

    timeline = Timeline(runtime=runtime)

    raw_blocks: list[dict[str, Any]] = []
    for index, event in enumerate(external_events or []):
        if not isinstance(event, Mapping):
            continue
        raw_blocks.extend(
            await _produce_event_blocks(
                event_sources=event_sources,
                runtime=runtime,
                timeline=timeline,
                event=event,
                index=index,
                timestamp=timestamp,
            )
        )
    timeline.blocks = list(raw_blocks)

    rendered_timeline = await timeline.render(
        cache_last=False,
        include_sources=False,
        include_announce=False,
        force_sanitize=False,
    )
    announce_blocks = timeline._produce_dynamic_announce_blocks(
        timeline_blocks=raw_blocks,
        render_blocks=rendered_timeline,
    )
    rendered_with_announce = await timeline.render(
        cache_last=False,
        include_sources=False,
        include_announce=True,
        force_sanitize=False,
    )
    timeline_text = _debug_text(timeline, rendered_timeline)
    announce_text = _join_text(announce_blocks)
    rendered_text = _debug_text(timeline, rendered_with_announce)
    debug_paths = _write_debug_files(
        debug_dir=debug_dir,
        prefix=(
            f"rendered-{runtime.conversation_id}-{runtime.turn_id}-"
            f"{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-dry-run"
        ),
        timeline_text=timeline_text,
        announce_text=announce_text,
        rendered_text=rendered_text,
    )
    return {
        "ok": True,
        "dry_run": True,
        "conversation_id": runtime.conversation_id,
        "turn_id": runtime.turn_id,
        "event_count": len([event for event in (external_events or []) if isinstance(event, Mapping)]),
        "block_count": len(raw_blocks),
        "announce_block_count": len(announce_blocks),
        "timeline_text": timeline_text,
        "announce_text": announce_text,
        "rendered_text": rendered_text,
        "debug_paths": debug_paths,
        "raw_blocks": raw_blocks,
        "announce_blocks": announce_blocks,
    }


__all__ = ["render_external_events_dry_run"]
