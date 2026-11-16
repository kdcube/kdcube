# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
from __future__ import annotations

from typing import Any, Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.context.memory.models import MemoryRecord, MemoryScope, normalize_scope_filter
from kdcube_ai_app.apps.chat.sdk.context.memory.store import UserMemoryStore


MEMORY_RESOLVER_NAME = "sdk.memory"
MEMORY_OBJECT_NAMESPACE = "mem"
MEMORY_RECORD_OBJECT_KIND = "memory.record"
MEMORY_RECORD_MIME = "application/vnd.kdcube.memory.record+json;version=1"
MEMORY_RECORD_REF_PREFIX = "mem:record:"


def memory_id_from_ref(ref: str) -> str:
    value = str(ref or "").strip()
    value = value.split("?", 1)[0].split("#", 1)[0].strip("/")
    if value.startswith(MEMORY_RECORD_REF_PREFIX):
        return value[len(MEMORY_RECORD_REF_PREFIX):].strip("/")
    if value.startswith("mem:"):
        return value.split(":", 1)[1].strip("/")
    if value.startswith("me:"):
        return value.split(":", 1)[1].strip("/")
    return ""


def memory_ref(memory_id: str) -> str:
    clean = str(memory_id or "").strip().split("?", 1)[0].split("#", 1)[0].strip("/")
    return f"{MEMORY_RECORD_REF_PREFIX}{clean}" if clean else ""


def canonical_memory_ref(ref_or_id: str, *, allow_bare: bool = False) -> str:
    raw = str(ref_or_id or "").strip()
    memory_id = memory_id_from_ref(ref_or_id)
    if not memory_id and allow_bare:
        memory_id = raw.split("?", 1)[0].split("#", 1)[0].strip("/")
    return memory_ref(memory_id)


def memory_ref_capabilities() -> dict[str, bool]:
    return {"preview": True, "open": True, "download": False, "rehost": False}


def memory_record_to_object_payload(record: MemoryRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "object_ref": memory_ref(record.id),
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
        "object_kind": MEMORY_RECORD_OBJECT_KIND,
        "resolver": MEMORY_RESOLVER_NAME,
        "resolver_status": "implemented",
        "mime": MEMORY_RECORD_MIME,
        "capabilities": memory_ref_capabilities(),
        "default_open_effect_action": "open",
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
    user_ids: Sequence[str] | None = None,
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

    memory_id = memory_id_from_ref(ref)
    canonical_ref = memory_ref(memory_id) if memory_id else ref
    base = _base_response(ref=canonical_ref, action=action, scope=scope)
    if action in {"capabilities", "describe"}:
        return base

    if not memory_id:
        return {**base, "ok": False, "error": "object_ref_required", "status": 400}

    # Read with the SAME semantics as the provider's object.get: the
    # identity-family read scope (`user_ids`) must apply here too. A memory
    # created under a linked identity (e.g. a messenger-linked account) is
    # visible in every family read — resolving its preview/open through a
    # single-actor lookup would answer memory_not_found for a record the
    # user demonstrably sees.
    record = await store.get_memory(
        scope=scope,
        memory_id=memory_id,
        visible_to_user=True,
        scope_filter=normalize_scope_filter(scope_filter),
        user_ids=user_ids,
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
                "object_ref": memory_ref(record.id),
                "target_surface": "sdk.memory.viewer",
                "mode": "focus",
                "memory_id": record.id,
                "title": record.memory[:120],
            },
        }
    return {**base, "ok": False, "error": "unsupported_object_action", "status": 400}


__all__ = [
    "MEMORY_OBJECT_NAMESPACE",
    "MEMORY_RECORD_MIME",
    "MEMORY_RECORD_OBJECT_KIND",
    "MEMORY_RECORD_REF_PREFIX",
    "MEMORY_RESOLVER_NAME",
    "canonical_memory_ref",
    "memory_id_from_ref",
    "memory_ref",
    "memory_record_to_object_payload",
    "memory_ref_capabilities",
    "resolve_memory_ref_action",
]
