# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Callable, Optional
import mimetypes

DEFAULT_IGNORE_NAMES = {
    "delta_aggregates.json",
}


def should_skip_relpath(rel: str) -> bool:
    if not rel:
        return True
    if rel.startswith("logs/"):
        return True
    name = Path(rel).name
    if name in DEFAULT_IGNORE_NAMES:
        return True
    if name.startswith("exec_result_") and name.endswith(".json"):
        return True
    return False


def snapshot_outdir(
    outdir: Path,
    *,
    ignore_fn: Optional[Callable[[str], bool]] = None,
) -> Dict[str, Dict[str, float]]:
    snapshot: Dict[str, Dict[str, float]] = {}
    if not outdir.exists():
        return snapshot
    for path in outdir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(outdir).as_posix()
        if ignore_fn and ignore_fn(rel):
            continue
        if not ignore_fn and should_skip_relpath(rel):
            continue
        stat = path.stat()
        snapshot[rel] = {
            "size": float(stat.st_size),
            "mtime": float(stat.st_mtime),
        }
    return snapshot


def diff_snapshots(
    before: Dict[str, Dict[str, float]],
    after: Dict[str, Dict[str, float]],
) -> Dict[str, List[Dict[str, Any]]]:
    created: List[Dict[str, Any]] = []
    modified: List[Dict[str, Any]] = []
    deleted: List[Dict[str, Any]] = []

    for path, meta in after.items():
        if path not in before:
            created.append({"path": path, **meta})
        else:
            prev = before[path]
            if meta.get("size") != prev.get("size") or meta.get("mtime") != prev.get("mtime"):
                modified.append({"path": path, "before": prev, "after": meta})

    for path, meta in before.items():
        if path not in after:
            deleted.append({"path": path, **meta})

    return {"created": created, "modified": modified, "deleted": deleted}


def format_diff(diff: Dict[str, List[Dict[str, Any]]]) -> str:
    created = diff.get("created") or []
    modified = diff.get("modified") or []
    deleted = diff.get("deleted") or []
    if not (created or modified or deleted):
        return "No out/ changes detected."

    lines: List[str] = []
    if created:
        lines.append("Created:")
        for item in created:
            size = int(item.get("size") or 0)
            lines.append(f"- {item.get('path')} ({size} bytes)")
    if modified:
        lines.append("Modified:")
        for item in modified:
            before = item.get("before") or {}
            after = item.get("after") or {}
            lines.append(
                f"- {item.get('path')} ({int(before.get('size') or 0)} -> {int(after.get('size') or 0)} bytes)"
            )
    if deleted:
        lines.append("Deleted:")
        for item in deleted:
            lines.append(f"- {item.get('path')}")
    return "\n".join(lines).strip()


def _is_text_mime(mime: str) -> bool:
    if not mime:
        return False
    if mime.startswith("text/"):
        return True
    return mime in {
        "application/json",
        "application/xml",
        "application/x-yaml",
        "application/yaml",
        "application/csv",
        "text/csv",
    }


def build_items_from_diff(outdir: Path, diff: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for bucket in ("created", "modified"):
        for entry in diff.get(bucket, []):
            rel = entry.get("path")
            if not rel:
                continue
            abs_path = outdir / rel
            if not abs_path.exists() or not abs_path.is_file():
                continue
            mime, _ = mimetypes.guess_type(abs_path.name)
            mime = mime or "application/octet-stream"
            text = ""
            if _is_text_mime(mime):
                try:
                    data = abs_path.read_bytes()
                    text = data.decode("utf-8", errors="ignore")
                except Exception:
                    text = ""
            items.append({
                "artifact_id": abs_path.stem,
                "artifact_kind": "file",
                "summary": f"{bucket} via side-effects",
                "output": {
                    "type": "file",
                    "path": rel,
                    "filename": abs_path.name,
                    "mime": mime,
                    "text": text,
                    "description": f"Side-effects file ({bucket})",
                    "size_bytes": int(abs_path.stat().st_size),
                },
            })
    return items


def build_deleted_notices(diff: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    notices: List[Dict[str, Any]] = []
    for entry in diff.get("deleted", []):
        path = entry.get("path") or ""
        if not path:
            continue
        notices.append({
            "artifact_id": f"deleted:{path}",
            "artifact_kind": "display",
            "summary": "deleted via side-effects",
            "output": {
                "type": "inline",
                "format": "text",
                "description": "Side-effects deletion",
                "value": f"Deleted: {path}",
            },
        })
    return notices
