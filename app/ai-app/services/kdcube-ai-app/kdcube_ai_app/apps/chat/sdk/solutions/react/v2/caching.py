from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class CachePoints:
    additional_idx: Optional[int]
    tail_idx: Optional[int]


FINAL_ROUND_KEY = "__final__"
FINAL_ROUND_TYPES = {
    "assistant.completion",
    "stage.suggested_followups",
    "react.exit",
    "react.state",
}


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


def _tool_call_id_from_block(blk: Dict[str, object]) -> Optional[str]:
    if not isinstance(blk, dict):
        return None
    meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
    rid = (blk.get("call_id") or meta.get("tool_call_id") or "").strip()
    if rid:
        return rid
    path = (blk.get("path") or "").strip()
    if path.startswith("tc:"):
        tail = path[len("tc:"):]
        parts = [p for p in tail.split(".") if p]
        if len(parts) >= 2:
            return parts[1].strip() or None
    return None


def round_key_for_block(blk: Dict[str, object]) -> Optional[str]:
    rid = _tool_call_id_from_block(blk)
    if rid:
        return rid
    if not isinstance(blk, dict):
        return None
    btype = (blk.get("type") or "").strip()
    if btype in FINAL_ROUND_TYPES:
        return FINAL_ROUND_KEY
    return None


def _round_end_indices(blocks: List[Dict[str, object]]) -> List[int]:
    if not blocks:
        return []
    order: List[str] = []
    last_idx: Dict[str, int] = {}
    for idx, blk in enumerate(blocks):
        rid = round_key_for_block(blk)
        if not rid:
            continue
        if rid not in last_idx:
            order.append(rid)
        last_idx[rid] = idx
    return [last_idx[r] for r in order]


def _group_end(blocks: List[Dict[str, object]], idx: Optional[int]) -> Optional[int]:
    if idx is None or idx < 0 or idx >= len(blocks):
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


def cache_points_for_blocks(
    blocks: List[Dict[str, object]],
    *,
    min_rounds: int = 2,
    offset: int = 2,
) -> CachePoints:
    pts = compute_cache_points_rounds(blocks, min_rounds=min_rounds, offset=offset)
    return CachePoints(
        additional_idx=_group_end(blocks, pts.additional_idx),
        tail_idx=_group_end(blocks, pts.tail_idx),
    )


def compute_cache_points_rounds(
    blocks: List[Dict[str, object]],
    *,
    min_rounds: int = 2,
    offset: int = 2,
) -> CachePoints:
    if not blocks:
        return CachePoints(additional_idx=None, tail_idx=None)
    ends = _round_end_indices(blocks)
    if not ends:
        return compute_cache_points(total_blocks=len(blocks), min_blocks=min_rounds, offset=offset)
    tail_idx = ends[-1]
    if len(ends) < max(1, min_rounds):
        return CachePoints(additional_idx=None, tail_idx=tail_idx)
    add_round_idx = max(0, len(ends) - 1 - max(1, offset))
    add_idx = ends[add_round_idx]
    if add_idx == tail_idx:
        add_idx = None
    return CachePoints(additional_idx=add_idx, tail_idx=tail_idx)


def apply_cache_points_rounds(
    blocks: List[Dict[str, object]],
    *,
    min_rounds: int = 2,
    offset: int = 2,
) -> None:
    if not blocks:
        return
    for blk in blocks:
        if isinstance(blk, dict):
            blk.pop("cache", None)

    pts = cache_points_for_blocks(blocks, min_rounds=min_rounds, offset=offset)
    add_idx = pts.additional_idx
    tail_idx = pts.tail_idx
    if add_idx is not None and tail_idx is not None and add_idx == tail_idx:
        add_idx = None

    if add_idx is not None:
        blocks[add_idx]["cache"] = True
    if tail_idx is not None:
        blocks[tail_idx]["cache"] = True


def block_index_for_path(blocks: List[Dict[str, object]], path: str) -> Optional[int]:
    if not isinstance(path, str) or not path.strip():
        return None
    for idx in range(len(blocks) - 1, -1, -1):
        blk = blocks[idx]
        if not isinstance(blk, dict):
            continue
        if (blk.get("path") or "").strip() == path.strip():
            return idx
    return None


def is_before_pre_tail_cache(
    blocks: List[Dict[str, object]],
    path: str,
    *,
    min_rounds: int = 2,
    offset: int = 2,
) -> Optional[bool]:
    idx = block_index_for_path(blocks, path)
    if idx is None:
        return None
    pts = cache_points_for_blocks(blocks, min_rounds=min_rounds, offset=offset)
    if pts.additional_idx is None:
        return False
    return idx < pts.additional_idx


def tail_rounds_from_path(blocks: List[Dict[str, object]], path: str) -> Optional[int]:
    target_idx = block_index_for_path(blocks, path)
    if target_idx is None:
        return None
    seen: set[str] = set()
    rounds = 0
    for blk in blocks[target_idx:]:
        rid = round_key_for_block(blk)
        if rid and rid not in seen:
            seen.add(rid)
            rounds += 1
    return rounds
