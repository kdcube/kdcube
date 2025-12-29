# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Dict, List

_HEADER_RE = re.compile(r"^===== EXECUTION (?P<id>\\S+) START (?P<ts>.+) =====$")


def _read_blocks(text: str) -> List[Dict[str, str]]:
    blocks: List[Dict[str, str]] = []
    cur: Optional[Dict[str, str]] = None
    for line in (text or "").splitlines():
        m = _HEADER_RE.match(line.strip())
        if m:
            if cur:
                blocks.append(cur)
            cur = {"exec_id": m.group("id"), "ts": m.group("ts"), "text": ""}
            continue
        if cur is None:
            continue
        cur["text"] = (cur.get("text") or "") + line + "\n"
    if cur:
        blocks.append(cur)
    return blocks


def last_error_block(log_path: Path, exec_id: Optional[str] = None) -> Optional[Dict[str, str]]:
    if not log_path.exists():
        return None
    try:
        text = log_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    blocks = _read_blocks(text)
    if not blocks:
        return None
    if exec_id:
        for blk in reversed(blocks):
            if blk.get("exec_id") == exec_id:
                return blk
    return blocks[-1]


def errors_log_tail(log_path: Path, exec_id: Optional[str] = None, max_chars: int = 4000) -> Optional[str]:
    blk = last_error_block(log_path, exec_id=exec_id)
    if not blk:
        return None
    txt = (blk.get("text") or "").strip()
    if not txt:
        return None
    return txt[-max_chars:] if max_chars and max_chars > 0 else txt


def last_execution_error_summary(log_dir: Path, exec_id: Optional[str] = None, max_chars: int = 4000) -> Optional[str]:
    log_path = log_dir / "errors.log"
    return errors_log_tail(log_path, exec_id=exec_id, max_chars=max_chars)
