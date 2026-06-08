from __future__ import annotations

import json
from typing import Any, Dict, Mapping

from kdcube_ai_app.apps.chat.sdk.solutions.react.events import canonicalize_event_ref_for_context

from .ids import timestamp_id
from .storage import CanvasStore


DEFAULT_CANVAS_TOOL_EVENT_SOURCE_DESCRIPTIONS: Dict[str, str] = {
    "patch": (
        "Patch a named collaborative canvas. The tool persists a new canvas "
        "revision, emits the UI patch event, and returns the event.canvas "
        "occurrence that represents it. Producing an artifact does not pin it; "
        "call this tool with a new_card that points at the produced canonical ref. "
        "Focused context is only user intent; use card ids from ANNOUNCE/read when "
        "suggesting content edits. Agents do not manage card positions."
    ),
    "read": (
        "Read a canvas by canvas: URI and return agent_view plus exact state. "
        "Use this only when ANNOUNCE map/legend or focused-context previews are insufficient."
    ),
}


def parse_canvas_operations(value: Any) -> list[Dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    if isinstance(value, str) and value.strip():
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [dict(item) for item in parsed if isinstance(item, Mapping)]
        if isinstance(parsed, Mapping):
            return [dict(parsed)]
    if isinstance(value, Mapping):
        return [dict(value)]
    return []


def agent_visible_canvas_operations(value: Any) -> list[Dict[str, Any]]:
    operations = parse_canvas_operations(value)
    layout_ops = {"move_card", "resize_card"}
    forbidden = [
        str(operation.get("op") or "")
        for operation in operations
        if str(operation.get("op") or "") in layout_ops
    ]
    if forbidden:
        raise ValueError("canvas.patch is content-only for agents; move_card/resize_card are UI layout operations")
    return operations


def canonicalize_canvas_card_refs(card: Mapping[str, Any], *, conversation_id: str) -> Dict[str, Any]:
    out = dict(card)
    for key in ("logical_path", "storage_ref", "artifact_ref", "ref", "hosted_uri"):
        if key in out:
            out[key] = canonicalize_event_ref_for_context(out.get(key), conversation_id=conversation_id)
    source_refs = out.get("source_refs")
    if isinstance(source_refs, list):
        out["source_refs"] = [
            canonicalize_event_ref_for_context(item, conversation_id=conversation_id)
            for item in source_refs
        ]
    return out


def canonicalize_canvas_operations_for_context(
    operations: list[Dict[str, Any]],
    *,
    conversation_id: str,
) -> list[Dict[str, Any]]:
    if not conversation_id:
        return operations
    out: list[Dict[str, Any]] = []
    for operation in operations:
        next_operation = dict(operation)
        raw_card = next_operation.get("card")
        if isinstance(raw_card, Mapping):
            next_operation["card"] = canonicalize_canvas_card_refs(raw_card, conversation_id=conversation_id)
        elif str(next_operation.get("op") or "") == "new_card":
            next_operation = canonicalize_canvas_card_refs(next_operation, conversation_id=conversation_id)
        updates = next_operation.get("set")
        if isinstance(updates, Mapping):
            next_operation["set"] = canonicalize_canvas_card_refs(updates, conversation_id=conversation_id)
        out.append(next_operation)
    return out


async def publish_canvas_patch_via_data_bus(
    tool_scope: Mapping[str, Any],
    *,
    bundle_id: str,
    subject: str,
    payload: Mapping[str, Any],
    object_ref: str,
    source: str = "react.tool.canvas.patch",
    tool_id: str = "canvas.patch",
) -> dict[str, Any] | None:
    comm = tool_scope.get("comm") if isinstance(tool_scope, Mapping) else None
    data_bus = getattr(comm, "data_bus", None)
    publish_and_wait = getattr(data_bus, "publish_and_wait", None)
    if not callable(publish_and_wait):
        return None
    message_id = timestamp_id("dbmsg")
    result = await publish_and_wait(
        bundle_id=bundle_id,
        subject=subject,
        object_ref=object_ref,
        idempotency_key=message_id,
        message_id=message_id,
        payload=dict(payload),
        reply=True,
        trace={
            "source": source,
            "tool_id": tool_id,
        },
    )
    status = str(result.get("status") or "")
    if status == "ok":
        data = result.get("data")
        return dict(data or {}) if isinstance(data, Mapping) else {}
    if status == "conflict":
        data = result.get("data")
        conflict = dict(data or {}) if isinstance(data, Mapping) else {}
        conflict.setdefault("ok", False)
        conflict.setdefault("error", "canvas_revision_conflict")
        return conflict
    err = result.get("error") if isinstance(result.get("error"), Mapping) else {}
    raise RuntimeError(str(err.get("message") or err.get("code") or f"Data Bus canvas patch failed: {status}"))


async def patch_canvas_for_agent(
    *,
    tool_scope: Mapping[str, Any],
    store: CanvasStore,
    bundle_id: str,
    data_bus_subject: str,
    operations: Any,
    canvas_name: str,
    canvas_id: str = "",
    story_id: str = "",
    base_revision: int | None = None,
    reason: str = "",
    actor: str = "agent",
    event_agent_id: str = "canvas",
    event_surface: str = "canvas",
) -> Dict[str, Any]:
    name = store.canvas_name(canvas_name)
    cid = store.canvas_id(canvas_name=name, canvas_id=canvas_id)
    patch_payload: Dict[str, Any] = {
        "schema": "kdcube.canvas.patch.v1",
        "operations": canonicalize_canvas_operations_for_context(
            agent_visible_canvas_operations(operations),
            conversation_id=str(tool_scope.get("conversation_id") or ""),
        ),
        "reason": reason,
    }
    if base_revision is not None:
        patch_payload["base_revision"] = base_revision
    operation_payload = {
        "story_id": str(story_id or ""),
        "canvas_name": name,
        "canvas_id": cid,
        "patch": patch_payload,
        "actor": str(actor or "agent"),
    }
    result = await publish_canvas_patch_via_data_bus(
        tool_scope,
        bundle_id=bundle_id,
        subject=data_bus_subject,
        payload=operation_payload,
        object_ref=cid,
    )
    if result is None:
        result = store.patch(
            story_id=str(story_id or ""),
            canvas_name=name,
            canvas_id=cid,
            patch=patch_payload,
            actor=str(actor or "agent"),
        )
    if not result.get("ok"):
        return {
            "ok": False,
            "error": str(result.get("error") or "canvas patch failed"),
            "canvas_name": name,
            "canvas_id": cid,
            "result": result,
        }
    event = store.state_event(
        canvas=result["canvas"],
        canvas_ref=result["canvas_ref"],
        latest_ref=result["latest_ref"],
        agent_id=event_agent_id,
        surface=event_surface,
    )
    return {
        "ok": True,
        "canvas_name": name,
        "canvas_id": result["canvas"].get("canvas_id"),
        "revision": int(result["canvas"].get("revision") or 0),
        "canvas_ref": result["canvas_ref"],
        "latest_ref": result["latest_ref"],
        "changed": result.get("changed") or [],
        "changed_cards": result.get("changed_cards") or [],
        "projection": result.get("projection") or {},
        "ui_event": result.get("ui_event") or {},
        "event": event,
    }


def read_canvas_for_agent(
    *,
    store: CanvasStore,
    uri: str = "",
    canvas_name: str = "",
    canvas_id: str = "",
    story_id: str = "",
    revision: int | None = None,
) -> Dict[str, Any]:
    name = store.canvas_name(canvas_name)
    cid = store.canvas_id(canvas_name=name, canvas_id=canvas_id)
    if str(uri or "").strip():
        return store.read_uri(
            uri=str(uri or "").strip(),
            story_id=str(story_id or ""),
            canvas_name=name,
            canvas_id=cid if canvas_id else "",
        )
    return store.read(story_id=str(story_id or ""), canvas_name=name, canvas_id=cid, revision=revision)


__all__ = [
    "DEFAULT_CANVAS_TOOL_EVENT_SOURCE_DESCRIPTIONS",
    "agent_visible_canvas_operations",
    "canonicalize_canvas_card_refs",
    "canonicalize_canvas_operations_for_context",
    "parse_canvas_operations",
    "patch_canvas_for_agent",
    "publish_canvas_patch_via_data_bus",
    "read_canvas_for_agent",
]
