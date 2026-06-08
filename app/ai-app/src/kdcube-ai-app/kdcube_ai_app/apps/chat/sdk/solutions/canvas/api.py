from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Sequence

from .ids import timestamp_slug_id
from .storage_utils import safe_storage_segment
from .events.resolver import CanvasObjectResolverRegistry, CanvasPinResolver, search_canvas_cards
from .storage import CanvasStore


LOGGER = logging.getLogger("kdcube.sdk.solutions.canvas.api")


def _canvas_target(target: Mapping[str, Any] | None = None) -> Dict[str, str]:
    target = dict(target or {})
    return {
        "agent_id": str(target.get("agent_id") or "canvas"),
        "surface": str(target.get("surface") or "canvas"),
        "story_kind": str(target.get("story_kind") or "canvas"),
        "conversation_role": str(target.get("conversation_role") or "canvas"),
    }


def upload_attachments(
    *,
    payload: Mapping[str, Any],
    uploaded_files: Sequence[Any],
    store: CanvasStore,
    user_id: str,
    story_id: str,
) -> Dict[str, Any]:
    canvas_name = store.canvas_name(payload.get("canvas_name") or payload.get("name"))
    canvas_id = store.canvas_id(canvas_name=canvas_name, canvas_id=payload.get("canvas_id"))
    if not uploaded_files:
        return {
            "ok": False,
            "user_id": user_id,
            "story_id": story_id,
            "canvas_id": canvas_id,
            "canvas_name": canvas_name,
            "error": "No uploaded files were provided",
        }
    metadata_rows = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
    attachments: list[Dict[str, Any]] = []
    cards: list[Dict[str, Any]] = []
    for idx, file_obj in enumerate(uploaded_files):
        filename = str(getattr(file_obj, "filename", None) or "attachment.bin")
        content_type = str(getattr(file_obj, "content_type", None) or "application/octet-stream")
        content = getattr(file_obj, "content", b"") or b""
        raw_meta = metadata_rows[idx] if idx < len(metadata_rows) and isinstance(metadata_rows[idx], Mapping) else {}
        default_card_id = timestamp_slug_id("ua")
        card_id = safe_storage_segment(
            str(raw_meta.get("card_id") or raw_meta.get("attachment_id") or default_card_id),
            default=default_card_id,
        )
        try:
            artifact = store.host_attachment_bytes(
                canvas_id=canvas_id,
                canvas_name=canvas_name,
                story_id=story_id,
                card_id=card_id,
                filename=filename,
                content=content,
                mime=content_type,
                version=int(raw_meta.get("version") or 1),
            )
        except Exception as exc:
            LOGGER.exception("Canvas attachment write failed canvas_id=%s filename=%s", canvas_id, filename)
            return {
                "ok": False,
                "user_id": user_id,
                "story_id": story_id,
                "canvas_id": canvas_id,
                "canvas_name": canvas_name,
                "error": f"Canvas attachment storage failed: {exc}",
            }
        card = {
            "id": card_id,
            "kind": "user.attachment",
            "title": filename,
            "mime": artifact["mime"],
            "logical_path": artifact["logical_path"],
            "storage_ref": artifact["storage_ref"],
            "version": artifact["version"],
            "placement": str(raw_meta.get("placement") or "floating"),
            "created_by": "user",
            "size": artifact["size"],
        }
        if isinstance(raw_meta.get("rect"), Mapping):
            card["rect"] = raw_meta["rect"]
            card["placement"] = "placed"
        attachments.append(artifact)
        cards.append(card)
    return {
        "ok": True,
        "user_id": user_id,
        "story_id": story_id,
        "canvas_name": canvas_name,
        "canvas_id": canvas_id,
        "attachments": attachments,
        "cards": cards,
    }


def read_pin(
    *,
    payload: Mapping[str, Any],
    store: CanvasStore,
    user_id: str,
    story_id: str,
) -> Dict[str, Any]:
    ref = str(payload.get("ref") or payload.get("logical_path") or payload.get("storage_ref") or "").strip()
    mime = str(payload.get("mime") or "").strip()
    try:
        max_text_chars = int(payload.get("max_text_chars") or 20000)
    except Exception:
        max_text_chars = 20000
    try:
        result = CanvasPinResolver(store).read_ref(ref, mime=mime, max_text_chars=max_text_chars)
    except Exception as exc:
        return {"ok": False, "user_id": user_id, "story_id": story_id, "ref": ref, "error": str(exc)}
    return {"user_id": user_id, "story_id": story_id, **result}


async def object_action(
    *,
    payload: Mapping[str, Any],
    registry: CanvasObjectResolverRegistry,
    user_id: str,
    story_id: str,
) -> Dict[str, Any]:
    action = str(payload.get("action") or "capabilities").strip().lower()
    result = await registry.object_action(payload, user_id=user_id, story_id=story_id)
    return {"user_id": user_id, "story_id": story_id, "action": action, **result}


def search(
    *,
    payload: Mapping[str, Any],
    store: CanvasStore,
    user_id: str,
    story_id: str,
) -> Dict[str, Any]:
    query = str(payload.get("query") or "").strip()
    namespaces = payload.get("namespaces") if isinstance(payload.get("namespaces"), list) else []
    try:
        limit = int(payload.get("limit") or 20)
    except Exception:
        limit = 20
    canvas_name = store.canvas_name(payload.get("canvas_name") or payload.get("name"))
    canvas_id = store.canvas_id(canvas_name=canvas_name, canvas_id=payload.get("canvas_id"))
    try:
        _, canvas = store.read_document(canvas_id=canvas_id, story_id=story_id, canvas_name=canvas_name)
        result = search_canvas_cards(canvas, query=query, namespaces=namespaces, limit=limit)
    except Exception as exc:
        return {"ok": False, "user_id": user_id, "story_id": story_id, "query": query, "error": str(exc)}
    return {"user_id": user_id, "story_id": story_id, **result}


def list_canvases(*, store: CanvasStore, user_id: str, story_id: str) -> Dict[str, Any]:
    try:
        return store.list_canvases(story_id=story_id)
    except Exception as exc:
        return {"ok": False, "user_id": user_id, "story_id": story_id, "error": str(exc)}


def read(
    *,
    payload: Mapping[str, Any],
    store: CanvasStore,
    user_id: str,
    story_id: str,
) -> Dict[str, Any]:
    uri = str(payload.get("uri") or payload.get("canvas_uri") or "").strip()
    canvas_name = store.canvas_name(payload.get("canvas_name") or payload.get("name"))
    canvas_id = store.canvas_id(canvas_name=canvas_name, canvas_id=payload.get("canvas_id"))
    if uri:
        return store.read_uri(uri=uri, story_id=story_id, canvas_name=canvas_name, canvas_id=payload.get("canvas_id") or "")
    revision = payload.get("revision")
    try:
        revision_value = int(revision) if revision is not None and str(revision).strip() else None
    except Exception:
        return {
            "ok": False,
            "user_id": user_id,
            "story_id": story_id,
            "canvas_id": canvas_id,
            "canvas_name": canvas_name,
            "error": "revision must be an integer",
        }
    return store.read(story_id=story_id, canvas_name=canvas_name, canvas_id=canvas_id, revision=revision_value)


def write(
    *,
    payload: Mapping[str, Any],
    store: CanvasStore,
    user_id: str,
    story_id: str,
    target: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    canvas_input = payload.get("canvas") if isinstance(payload.get("canvas"), Mapping) else payload.get("document")
    canvas_name = store.canvas_name(
        payload.get("canvas_name")
        or (canvas_input.get("canvas_name") if isinstance(canvas_input, Mapping) else None)
        or payload.get("name")
    )
    canvas_id = store.canvas_id(
        canvas_name=canvas_name,
        canvas_id=payload.get("canvas_id") or (canvas_input.get("canvas_id") if isinstance(canvas_input, Mapping) else None),
    )
    if not isinstance(canvas_input, Mapping):
        return {
            "ok": False,
            "user_id": user_id,
            "story_id": story_id,
            "canvas_id": canvas_id,
            "canvas_name": canvas_name,
            "error": "canvas document is required",
        }
    try:
        result = store.write(
            story_id=story_id,
            canvas_name=canvas_name,
            canvas_id=canvas_id,
            canvas_input=canvas_input,
            base_revision=payload.get("base_revision"),
        )
    except Exception as exc:
        return {"ok": False, "user_id": user_id, "story_id": story_id, "canvas_id": canvas_id, "canvas_name": canvas_name, "error": str(exc)}
    if not result.get("ok"):
        return result
    resolved_target = _canvas_target(target)
    event = store.state_event(
        canvas=result["canvas"],
        canvas_ref=result["canvas_ref"],
        latest_ref=result["latest_ref"],
        agent_id=resolved_target["agent_id"],
        surface=resolved_target["surface"],
    )
    return {
        "ok": True,
        "user_id": user_id,
        "story_id": story_id,
        "canvas_name": canvas_name,
        "canvas_id": result["canvas"].get("canvas_id"),
        "revision": int(result["canvas"].get("revision") or 0),
        "canvas_ref": result["canvas_ref"],
        "latest_ref": result["latest_ref"],
        "storage_uri": result["storage_uri"],
        "canvas": result["canvas"],
        "projection": result.get("projection") or store.projection(result["canvas"]),
        "message_payload": {
            "target": {
                "agent_id": resolved_target["agent_id"],
                "surface": resolved_target["surface"],
                "story_kind": resolved_target["story_kind"],
                "story_id": story_id,
                "conversation_role": resolved_target["conversation_role"],
            },
            "external_events": [event],
        },
    }


def patch(
    *,
    payload: Mapping[str, Any],
    store: CanvasStore,
    user_id: str,
    story_id: str,
    target: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    canvas_name = store.canvas_name(payload.get("canvas_name") or payload.get("name"))
    canvas_id = store.canvas_id(canvas_name=canvas_name, canvas_id=payload.get("canvas_id"))
    patch_payload = payload.get("patch") if isinstance(payload.get("patch"), Mapping) else payload
    actor = str(payload.get("actor") or user_id or "user")
    try:
        result = store.patch(
            story_id=story_id,
            canvas_name=canvas_name,
            canvas_id=canvas_id,
            patch=patch_payload,
            actor=actor,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "user_id": user_id,
            "story_id": story_id,
            "canvas_id": canvas_id,
            "canvas_name": canvas_name,
        }
    if not result.get("ok"):
        return result
    resolved_target = _canvas_target(target)
    event = store.state_event(
        canvas=result["canvas"],
        canvas_ref=result["canvas_ref"],
        latest_ref=result["latest_ref"],
        agent_id=resolved_target["agent_id"],
        surface=resolved_target["surface"],
    )
    ui_event = dict(result.get("ui_event") or {})
    ui_event.update({
        "type": str(ui_event.get("type") or store.ui_event_type),
        "source": str(ui_event.get("source") or "canvas.patch"),
        "story_id": story_id,
        "canvas_name": canvas_name,
        "canvas_id": result["canvas"].get("canvas_id"),
        "revision": int(result["canvas"].get("revision") or 0),
        "canvas_ref": result["canvas_ref"],
        "latest_ref": result["latest_ref"],
        "changed": ui_event.get("changed") or result.get("changed") or [],
        "changed_cards": ui_event.get("changed_cards") or result.get("changed_cards") or [],
        "projection": ui_event.get("projection") or result.get("projection") or store.projection(result["canvas"]),
    })
    if result.get("canvas_uri") and not ui_event.get("canvas_uri"):
        ui_event["canvas_uri"] = result["canvas_uri"]
    return {
        "ok": True,
        "user_id": user_id,
        "story_id": story_id,
        "canvas_name": canvas_name,
        "canvas_id": result["canvas"].get("canvas_id"),
        "revision": int(result["canvas"].get("revision") or 0),
        "canvas_ref": result["canvas_ref"],
        "latest_ref": result["latest_ref"],
        "storage_uri": result["storage_uri"],
        "changed": result.get("changed") or [],
        "changed_cards": result.get("changed_cards") or [],
        "noop": bool(result.get("noop")),
        "canvas": result["canvas"],
        "projection": result.get("projection") or store.projection(result["canvas"]),
        "ui_event": ui_event,
        "message_payload": {
            "target": {
                "agent_id": resolved_target["agent_id"],
                "surface": resolved_target["surface"],
                "story_kind": resolved_target["story_kind"],
                "story_id": story_id,
                "conversation_role": resolved_target["conversation_role"],
            },
            "external_events": [event],
        },
    }
