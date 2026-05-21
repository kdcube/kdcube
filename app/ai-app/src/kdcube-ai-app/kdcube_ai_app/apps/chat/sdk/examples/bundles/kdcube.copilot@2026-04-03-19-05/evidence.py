# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import pathlib
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from kdcube_ai_app.storage.observed_file_locks import observed_file_lock


DEFAULT_LIMIT = 100
DEFAULT_RETENTION = 500
EVENTS_DIR = "evidence"
EVENTS_FILE = "copilot-events.json"
EVENTS_LOCK = ".copilot-events.lock"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


def _events_path(storage_root: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(storage_root) / EVENTS_DIR / EVENTS_FILE


def _lock_path(storage_root: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(storage_root) / EVENTS_DIR / EVENTS_LOCK


def _read_events_unlocked(path: pathlib.Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = raw.get("events") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, dict)]


def _write_events_unlocked(path: pathlib.Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"events": events}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    tmp.replace(path)


def _service_context(record: dict[str, Any]) -> dict[str, Any]:
    service = record.get("service") if isinstance(record.get("service"), dict) else {}
    conversation = record.get("conversation") if isinstance(record.get("conversation"), dict) else {}
    return {
        "tenant": service.get("tenant"),
        "project": service.get("project"),
        "user": service.get("user"),
        "request_id": service.get("request_id"),
        "session_id": conversation.get("session_id"),
        "conversation_id": conversation.get("conversation_id"),
        "turn_id": conversation.get("turn_id"),
    }


def event_from_comm_record(record: dict[str, Any], *, bundle_id: str) -> dict[str, Any]:
    event = record.get("event") if isinstance(record.get("event"), dict) else {}
    data = record.get("data") if isinstance(record.get("data"), dict) else {}
    metrics = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}
    return {
        "event_id": str(record.get("record_id") or f"commrec_{uuid.uuid4().hex}"),
        "timestamp": record.get("recorded_at_ms") or int(time.time() * 1000),
        "timestamp_iso": _now_iso(),
        "bundle_id": bundle_id,
        "source": "comm.record",
        "type": str(record.get("type") or ""),
        "socket_event": record.get("socket_event"),
        "route": record.get("route"),
        "agent": event.get("agent"),
        "step": event.get("step"),
        "status": event.get("status"),
        "title": event.get("title"),
        "data": _json_safe(data),
        "metrics": _json_safe(metrics),
        "context": _service_context(record),
        "privacy": _json_safe(record.get("privacy") or {}),
    }


def direct_event(
    *,
    bundle_id: str,
    source: str,
    event_type: str,
    status: str,
    title: str,
    data: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "event_id": f"copilot_evt_{uuid.uuid4().hex}",
        "timestamp": int(time.time() * 1000),
        "timestamp_iso": _now_iso(),
        "bundle_id": bundle_id,
        "source": source,
        "type": event_type,
        "socket_event": None,
        "route": None,
        "agent": "kdcube.copilot",
        "step": source,
        "status": status,
        "title": title,
        "data": _json_safe(data or {}),
        "metrics": {},
        "context": _json_safe(context or {}),
        "privacy": {"contains_content": False, "data_redacted": False},
    }


def append_events(
    *,
    storage_root: str | pathlib.Path,
    events: Iterable[dict[str, Any]],
    retention: int = DEFAULT_RETENTION,
) -> int:
    root = pathlib.Path(storage_root)
    path = _events_path(root)
    lock = _lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    incoming = [dict(item) for item in events if isinstance(item, dict)]
    if not incoming:
        return 0
    with observed_file_lock(
        lock_path=lock,
        resource_id=f"kdcube.copilot.events:{root}",
        operation="kdcube.copilot.events.append",
        wait_seconds=30,
    ):
        existing = _read_events_unlocked(path)
        seen = {str(item.get("event_id")) for item in existing if item.get("event_id")}
        appended: list[dict[str, Any]] = []
        for item in incoming:
            event_id = str(item.get("event_id") or "")
            if event_id and event_id in seen:
                continue
            appended.append(item)
            if event_id:
                seen.add(event_id)
        if not appended:
            return 0
        merged = [*existing, *appended]
        keep = max(1, int(retention or DEFAULT_RETENTION))
        _write_events_unlocked(path, merged[-keep:])
        return len(appended)


def append_comm_records(
    *,
    storage_root: str | pathlib.Path,
    bundle_id: str,
    records: Iterable[dict[str, Any]],
    retention: int = DEFAULT_RETENTION,
) -> int:
    events = [event_from_comm_record(record, bundle_id=bundle_id) for record in records if isinstance(record, dict)]
    return append_events(storage_root=storage_root, events=events, retention=retention)


def build_widget_payload(
    *,
    storage_root: str | pathlib.Path | None,
    bundle_id: str,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    if storage_root is None:
        return {
            "ok": False,
            "bundle_id": bundle_id,
            "events": [],
            "count": 0,
            "error": "Bundle storage backend is not configured.",
        }
    path = _events_path(pathlib.Path(storage_root))
    events = _read_events_unlocked(path)
    events = [item for item in events if str(item.get("bundle_id") or "") == bundle_id]
    events.sort(key=lambda item: int(item.get("timestamp") or 0), reverse=True)
    selected = events[: max(1, int(limit or DEFAULT_LIMIT))]
    by_type: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for item in events:
        by_type[str(item.get("type") or "unknown")] = by_type.get(str(item.get("type") or "unknown"), 0) + 1
        by_source[str(item.get("source") or "unknown")] = by_source.get(str(item.get("source") or "unknown"), 0) + 1
    return {
        "ok": True,
        "bundle_id": bundle_id,
        "events": selected,
        "count": len(events),
        "limit": len(selected),
        "by_type": by_type,
        "by_source": by_source,
        "storage_path": str(path),
    }
