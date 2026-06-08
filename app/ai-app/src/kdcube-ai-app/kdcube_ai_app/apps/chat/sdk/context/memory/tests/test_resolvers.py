from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from kdcube_ai_app.apps.chat.sdk.context.memory.models import MemoryRecord, MemoryScope
from kdcube_ai_app.apps.chat.sdk.context.memory.events.resolver import (
    memory_id_from_ref,
    memory_ref_capabilities,
    resolve_memory_ref_action,
)


class _Store:
    def __init__(self, record: MemoryRecord | None) -> None:
        self.record = record
        self.calls = []

    async def get_memory(self, **kwargs):
        self.calls.append(kwargs)
        return self.record


def _record(memory_id: str = "mem_1") -> MemoryRecord:
    now = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
    scope = MemoryScope(tenant="demo", project="project", user_id="user_1", bundle_id="bundle@1")
    return MemoryRecord(
        id=memory_id,
        scope=scope,
        memory="Use concise status updates",
        context="The user prefers short progress notes.",
        kind="preference",
        status="active",
        visibility="user",
        labels=("communication",),
        keywords=("status",),
        tier=1,
        pinned=False,
        confidence_score=0.9,
        importance_score=0.8,
        freshness_score=1.0,
        salience_score=0.85,
        confirmation_rate=1.0,
        evidence_count=2,
        update_count=0,
        confirmation_count=1,
        contradiction_count=0,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        revision=3,
    )


def test_memory_ref_helpers_are_namespace_owned():
    assert memory_id_from_ref("mem:mem_123#x") == "mem_123"
    assert memory_ref_capabilities() == {"preview": True, "open": True, "download": False, "rehost": False}


def test_memory_resolver_preview_reads_memory_store():
    record = _record()
    store = _Store(record)
    result = asyncio.run(resolve_memory_ref_action(
        {"object_ref": "mem:mem_1", "action": "preview"},
        store=store,  # type: ignore[arg-type]
        scope=record.scope,
    ))

    assert result["ok"] is True
    assert result["resolver"] == "sdk.memory"
    assert result["namespace"] == "mem"
    assert result["memory"]["id"] == "mem_1"
    assert result["title"] == "Use concise status updates"
    assert store.calls[0]["memory_id"] == "mem_1"
    assert store.calls[0]["visible_to_user"] is True


def test_memory_resolver_open_returns_ui_open_event():
    record = _record()
    result = asyncio.run(resolve_memory_ref_action(
        {"object_ref": "mem:mem_1", "action": "open"},
        store=_Store(record),  # type: ignore[arg-type]
        scope=record.scope,
    ))

    assert result["ok"] is True
    assert result["ui_event"]["target_surface"] == "sdk.memory.viewer"
    assert result["ui_event"]["memory_id"] == "mem_1"


def test_memory_resolver_missing_ref_is_bounded_error():
    scope = MemoryScope(tenant="demo", project="project", user_id="user_1", bundle_id="bundle@1")
    result = asyncio.run(resolve_memory_ref_action(
        {"object_ref": "not-memory", "action": "preview"},
        store=_Store(None),  # type: ignore[arg-type]
        scope=scope,
    ))

    assert result["ok"] is False
    assert result["error"] == "memory_ref_required"
