from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class CachePoints:
    additional_idx: Optional[int]
    tail_idx: Optional[int]


def can_evict(block_idx: int, *, total_blocks: int, min_blocks: int = 4, offset: int = 2) -> bool:
    if block_idx < 0 or block_idx >= total_blocks:
        return False
    pts = compute_cache_points(total_blocks=total_blocks, min_blocks=min_blocks, offset=offset)
    if pts.additional_idx is None:
        return block_idx > 0
    return block_idx > pts.additional_idx


def compute_cache_points(*, total_blocks: int, min_blocks: int = 4, offset: int = 2) -> CachePoints:
    if total_blocks <= 0:
        return CachePoints(additional_idx=None, tail_idx=None)
    tail_idx = total_blocks - 1
    if total_blocks < max(1, min_blocks):
        return CachePoints(additional_idx=None, tail_idx=tail_idx)
    add_idx = max(0, tail_idx - max(1, offset))
    if add_idx == tail_idx:
        add_idx = None
    return CachePoints(additional_idx=add_idx, tail_idx=tail_idx)


def apply_cache_points(blocks: List[Dict[str, object]], *, min_blocks: int = 4, offset: int = 2) -> None:
    if not blocks:
        return
    pts = compute_cache_points(total_blocks=len(blocks), min_blocks=min_blocks, offset=offset)
    for blk in blocks:
        if isinstance(blk, dict):
            blk.pop("cache", None)

    # If blocks share the same path, place checkpoints at the last block for that path.
    def _group_end(idx: int) -> int:
        if idx < 0 or idx >= len(blocks):
            return idx
        blk = blocks[idx]
        if not isinstance(blk, dict):
            return idx
        path = (blk.get("path") or "").strip()
        if not path:
            return idx
        j = idx
        while j + 1 < len(blocks):
            nxt = blocks[j + 1]
            if not isinstance(nxt, dict):
                break
            if (nxt.get("path") or "").strip() != path:
                break
            j += 1
        return j

    add_idx = _group_end(pts.additional_idx) if pts.additional_idx is not None else None
    tail_idx = _group_end(pts.tail_idx) if pts.tail_idx is not None else None
    if add_idx is not None and tail_idx is not None and add_idx == tail_idx:
        add_idx = None

    if add_idx is not None:
        blocks[add_idx]["cache"] = True
    if tail_idx is not None:
        blocks[tail_idx]["cache"] = True
