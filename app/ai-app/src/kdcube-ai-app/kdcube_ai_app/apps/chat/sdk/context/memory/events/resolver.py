# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
from __future__ import annotations

from typing import Any, Mapping

from ..models import MemoryRecord, MemoryScope, normalize_scope_filter
from ..store import UserMemoryStore


MEMORY_RESOLVER_NAME = "sdk.memory"
MEMORY_OBJECT_NAMESPACE = "mem"


def memory_id_from_ref(ref: str) -> str:
    value = str(ref or "").strip()
    if value.startswith("mem:"):
        return value.split(":", 1)[1].split("?", 1)[0].split("#", 1)[0].strip("/")
    return ""


def memory_ref_capabilities() -> dict[str, bool]:
    return {"preview": True, "open": True, "download": False, "rehost": False}


def memory_record_to_object_payload(record: MemoryRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "object_ref": f"mem:{record.id}",
        "scope": {
            "tenant": record.scope.tenant,
            "project": record.scope.project,
            "user_id": record.scope.user_id,
            "bundle_id": record.scope.bundle_id,
        },
        "bundle_id": record.scope.bundle_id,
        "memory": record.memory,
        "context": record.context,
        "kind": record.kind,
        "status": record.status,
        "visibility": record.visibility,
        "labels": list(record.labels),
        "keywords": list(record.keywords),
        "tier": record.tier,
        "pinned": bool(getattr(record, "pinned", False)),
        "confidence_score": record.confidence_score,
        "importance_score": record.importance_score,
        "salience_score": record.salience_score,
        "evidence_count": record.evidence_count,
        "update_count": record.update_count,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "last_event_at": record.last_event_at.isoformat(),
        "revision": record.revision,
    }


def _base_response(*, ref: str, action: str, scope: MemoryScope | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": True,
        "action": action,
        "ref": ref,
        "object_ref": ref,
        "namespace": MEMORY_OBJECT_NAMESPACE,
        "resolver": MEMORY_RESOLVER_NAME,
        "resolver_status": "implemented",
        "capabilities": memory_ref_capabilities(),
    }
    if scope is not None:
        out["user_id"] = scope.user_id
    return out


async def resolve_memory_ref_action(
    payload: Mapping[str, Any],
    *,
    store: UserMemoryStore,
    scope: MemoryScope,
    scope_filter: str = "current_bundle",
) -> dict[str, Any]:
    """
    Resolve an object action for `mem:` refs.

    This module belongs to the memory subsystem. Canvas, chat, task tracker, or
    any future surface may call it, but memory remains the owner of the object
    schema, permissions, preview shape, and open-event semantics.
    """

    ref = str(
        payload.get("object_ref")
        or payload.get("ref")
        or payload.get("logical_path")
        or ""
    ).strip()
    action = str(payload.get("action") or "capabilities").strip().lower()
    scope = scope.normalized()
    base = _base_response(ref=ref, action=action, scope=scope)
    if action in {"capabilities", "describe"}:
        return base

    memory_id = memory_id_from_ref(ref)
    if not memory_id:
        return {**base, "ok": False, "error": "memory_ref_required", "status": 400}

    record = await store.get_memory(
        scope=scope,
        memory_id=memory_id,
        visible_to_user=True,
        scope_filter=normalize_scope_filter(scope_filter),
    )
    if record is None:
        return {**base, "ok": False, "error": "memory_not_found", "status": 404}

    memory_payload = memory_record_to_object_payload(record)
    if action == "preview":
        return {
            **base,
            "memory": memory_payload,
            "title": record.memory[:120],
            "summary": record.context[:500] or record.memory[:500],
            "mime": "application/json",
        }
    if action == "open":
        return {
            **base,
            "memory": memory_payload,
            "title": record.memory[:120],
            "summary": record.context[:500] or record.memory[:500],
            "mime": "application/json",
            "ui_event": {
                "type": "kdcube.ui.object.open.requested",
                "subject": "ui.object.open.requested",
                "source": "object_resolver",
                "object_ref": ref,
                "target_surface": "sdk.memory.viewer",
                "mode": "focus",
                "memory_id": record.id,
                "title": record.memory[:120],
            },
        }
    return {**base, "ok": False, "error": "unsupported_memory_object_action", "status": 400}


__all__ = [
    "MEMORY_OBJECT_NAMESPACE",
    "MEMORY_RESOLVER_NAME",
    "memory_id_from_ref",
    "memory_record_to_object_payload",
    "memory_ref_capabilities",
    "resolve_memory_ref_action",
]
