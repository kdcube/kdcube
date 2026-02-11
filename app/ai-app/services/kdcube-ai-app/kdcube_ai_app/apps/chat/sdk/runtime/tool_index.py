# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import json
import pathlib
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, List

try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover
    fcntl = None

INDEX_FILE = "tool_calls_index.json"
LOCK_FILE = ".tool_calls_index.lock"


@contextmanager
def _index_lock(outdir: pathlib.Path):
    lock_path = outdir / LOCK_FILE
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as fh:
        if fcntl is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except Exception:
                pass
        try:
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass


def read_index(outdir: pathlib.Path) -> Dict[str, List[str]]:
    p = outdir / INDEX_FILE
    if not p.exists():
        return {}
    try:
        m = json.loads(p.read_text(encoding="utf-8")) or {}
        return {k: list(v or []) for k, v in m.items() if isinstance(v, list)}
    except Exception:
        return {}


def _write_index(outdir: pathlib.Path, m: Dict[str, List[str]]) -> None:
    (outdir / INDEX_FILE).write_text(
        json.dumps(m, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _timestamp_suffix(existing: List[str]) -> str:
    base = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")[:17]  # ms precision
    if not existing:
        return base
    used = set(existing)
    if not any(base in name for name in used):
        return base
    i = 1
    while True:
        candidate = f"{base}-{i}"
        if not any(candidate in name for name in used):
            return candidate
        i += 1


def reserve_tool_call_filename(
    outdir: pathlib.Path,
    tool_id: str,
    safe_tool_id: str,
    *,
    ext: str = "json",
) -> str:
    """
    Allocate a filename for a tool call and persist it to the index.
    Thread/process-safe via a lock file.
    """
    with _index_lock(outdir):
        idx = read_index(outdir)
        existing = idx.get(tool_id, [])
        suffix = _timestamp_suffix(existing)
        rel = f"{safe_tool_id}-{suffix}.{ext}"
        idx.setdefault(tool_id, []).append(rel)
        _write_index(outdir, idx)
        return rel


def ensure_index_entry(outdir: pathlib.Path, tool_id: str, filename: str) -> None:
    """
    Idempotently ensure a filename is present in the index for a tool.
    """
    with _index_lock(outdir):
        idx = read_index(outdir)
        arr = idx.setdefault(tool_id, [])
        if filename not in arr:
            arr.append(filename)
            _write_index(outdir, idx)
