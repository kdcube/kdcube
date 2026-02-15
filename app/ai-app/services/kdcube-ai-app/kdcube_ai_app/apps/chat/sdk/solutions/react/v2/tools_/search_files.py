from __future__ import annotations

import os
import re
from typing import Dict, Iterator, List, Optional


def _iter_files(root: str, *, max_files: int = 2000) -> Iterator[str]:
    count = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Skip hidden dirs
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            if fname.startswith("."):
                continue
            yield os.path.join(dirpath, fname)
            count += 1
            if count >= max_files:
                return


def search_files(
        *,
        root: str,
        name_regex: Optional[str] = None,
        content_regex: Optional[str] = None,
        max_files: int = 2000,
        max_bytes: int = 1_000_000,
        max_hits: int = 200,
) -> List[Dict[str, object]]:
    """
    Safe file search under a root directory.
    - no symlink following
    - hidden files/dirs skipped
    - file size capped per file
    """
    if not root:
        return []
    name_re = re.compile(name_regex) if name_regex else None
    content_re = re.compile(content_regex) if content_regex else None
    hits: List[Dict[str, object]] = []
    for path in _iter_files(root, max_files=max_files):
        if name_re and not name_re.search(os.path.basename(path)):
            continue
        if content_re:
            try:
                size = os.path.getsize(path)
            except Exception:
                continue
            if size > max_bytes:
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read(max_bytes)
            except Exception:
                continue
            if not content_re.search(text):
                continue
        hits.append({"path": path})
        if len(hits) >= max_hits:
            break
    return hits
