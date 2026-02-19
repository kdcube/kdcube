# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import hashlib
import json
import ast
from datetime import datetime
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class FeedbackItem:
    turn_id: str
    ts: str
    text: str
    origin: str
    reaction: Optional[str] = None
    confidence: Optional[float] = None
    from_turn_id: Optional[str] = None


class Feedback:
    """
    Feedback utilities for integrating explicit/machine feedback into the timeline
    and announce channel without breaking cache semantics.
    """

    def __init__(self, *, ctx_client: Any, logger_obj: Optional[Any] = None) -> None:
        self.ctx_client = ctx_client
        self.log = logger_obj or logger

    def _path_for(self, item: FeedbackItem) -> str:
        return f"ar:{item.turn_id}.feedback.latest"

    def _digest_items(self, items: Iterable[FeedbackItem]) -> str:
        payload = []
        for it in items:
            payload.append({
                "origin": it.origin,
                "reaction": it.reaction,
                "ts": it.ts,
                "text": it.text,
            })
        payload.sort(key=lambda d: (d.get("ts") or "", d.get("origin") or "", d.get("reaction") or "", d.get("text") or ""))
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()

    def format_reaction_text(self, reaction: Dict[str, Any]) -> str:
        try:
            return json.dumps(reaction, ensure_ascii=False)
        except Exception:
            return str(reaction)

    def build_turn_log_index_text(self, feedbacks: List[Dict[str, Any]]) -> str:
        if not feedbacks:
            return ""
        try:
            def _ts_key(val: str) -> float:
                try:
                    s = str(val or "").strip()
                    if s.endswith("Z"):
                        s = s[:-1] + "+00:00"
                    return datetime.fromisoformat(s).timestamp()
                except Exception:
                    try:
                        return float(val)
                    except Exception:
                        return 0.0
            latest = max(feedbacks, key=lambda fb: _ts_key(fb.get("ts")))
            compact = {
                "count": len(feedbacks),
                "last_ts": latest.get("ts"),
                "last_reaction": latest.get("reaction"),
                "last_origin": latest.get("origin"),
                "last_text": latest.get("text") or "",
            }
            return "[turn.log.feedbacks] " + json.dumps(compact, ensure_ascii=False)
        except Exception:
            return ""

    def _parse_reaction_text(self, text: str) -> Optional[Dict[str, Any]]:
        if not isinstance(text, str) or not text.strip():
            return None
        raw = text.strip()
        if raw.startswith("[turn.log.reaction]"):
            raw = raw[len("[turn.log.reaction]"):].strip()
        if not raw:
            return None
        # Prefer JSON
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        # Fallback to literal eval for older dict string format
        try:
            parsed = ast.literal_eval(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
        return None

    def _normalize_reaction_items(self, items: List[Dict[str, Any]]) -> List[FeedbackItem]:
        out: List[FeedbackItem] = []
        for r in (items or []):
            react: Optional[Dict[str, Any]] = None
            payload = r.get("payload")
            if isinstance(payload, dict):
                cand = payload.get("reaction")
                if isinstance(cand, dict):
                    react = cand
            if react is None:
                react = self._parse_reaction_text(r.get("text") or "")
            if not isinstance(react, dict):
                continue
            tid = (react.get("turn_id") or r.get("turn_id") or "").strip()
            if not tid:
                continue
            out.append(FeedbackItem(
                turn_id=tid,
                ts=str(react.get("ts") or r.get("ts") or ""),
                text=str(react.get("text") or ""),
                origin=str(react.get("origin") or "machine"),
                reaction=react.get("reaction"),
                confidence=react.get("confidence"),
                from_turn_id=react.get("turn_id"),
            ))
        return out

    def _latest_per_turn(self, items: Iterable[FeedbackItem]) -> Dict[str, FeedbackItem]:
        out: Dict[str, FeedbackItem] = {}
        for it in items:
            if not it.turn_id:
                continue
            prev = out.get(it.turn_id)
            if prev is None:
                out[it.turn_id] = it
                continue
            if self._ts_key(it.ts) >= self._ts_key(prev.ts):
                out[it.turn_id] = it
        return out

    def max_ts(self, items: Iterable[FeedbackItem]) -> str:
        max_val: float = 0.0
        max_ts: str = ""
        for it in items:
            val = self._ts_key(it.ts)
            if val >= max_val:
                max_val = val
                max_ts = it.ts or max_ts
        return max_ts

    def _ts_key(self, ts: str) -> float:
        if not ts:
            return 0.0
        try:
            s = ts.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s).timestamp()
        except Exception:
            try:
                return float(ts)
            except Exception:
                return 0.0

    async def collect_recent(
        self,
        *,
        user_id: str,
        conversation_id: str,
        turn_ids: List[str],
        since_ts: Optional[str] = None,
        days: int = 365,
    ) -> Dict[str, List[FeedbackItem]]:
        if not self.ctx_client or not user_id or not conversation_id or not turn_ids:
            return {}
        try:
            res = await self.ctx_client.fetch_latest_feedback_reactions(
                user_id=user_id,
                conversation_id=conversation_id,
                turn_ids=turn_ids,
                days=days,
                since_ts=since_ts,
                limit=max(1, len(turn_ids)),
            )
        except Exception as exc:
            try:
                self.log.log(f"[feedback] search reactions failed: {exc}", level="ERROR")
            except Exception:
                pass
            return {}

        items = self._normalize_reaction_items(res.get("items") or [])
        latest = self._latest_per_turn(items)
        out: Dict[str, List[FeedbackItem]] = {}
        for tid, item in latest.items():
            out[tid] = [item]
        return out

    async def collect_latest(
        self,
        *,
        user_id: str,
        conversation_id: str,
        turn_ids: List[str],
        days: int = 365,
    ) -> Dict[str, List[FeedbackItem]]:
        return await self.collect_recent(
            user_id=user_id,
            conversation_id=conversation_id,
            turn_ids=turn_ids,
            since_ts=None,
            days=days,
        )

    def diff_updates(
        self,
        *,
        feedbacks_by_turn: Dict[str, List[FeedbackItem]],
        seen_map: Dict[str, str],
    ) -> Tuple[List[FeedbackItem], Dict[str, str]]:
        updates: List[FeedbackItem] = []
        new_seen = dict(seen_map or {})
        for tid, items in feedbacks_by_turn.items():
            digest = self._digest_items(items)
            prev = new_seen.get(tid)
            if prev != digest:
                updates.extend(items)
            new_seen[tid] = digest
        return updates, new_seen

    def ensure_blocks(
        self,
        *,
        timeline: Any,
        feedbacks_by_turn: Dict[str, List[FeedbackItem]],
    ) -> bool:
        if not timeline or not feedbacks_by_turn:
            return False
        blocks = list(getattr(timeline, "blocks", []) or [])
        if not blocks:
            return False

        changed = False
        path_index: Dict[str, Dict[str, Any]] = {}
        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            if blk.get("type") != "turn.feedback":
                continue
            path = (blk.get("path") or "").strip()
            if path:
                path_index[path] = blk

        def _find_last_index(turn_id: str) -> Optional[int]:
            for idx in range(len(blocks) - 1, -1, -1):
                if (blocks[idx].get("turn_id") or "").strip() == turn_id:
                    return idx
            return None

        for tid, items in feedbacks_by_turn.items():
            # sort by ts for deterministic ordering
            items_sorted = sorted(items, key=lambda it: it.ts or "")
            for item in items_sorted:
                path = self._path_for(item)
                existing = path_index.get(path)
                meta = {
                    "origin": item.origin,
                    "reaction": item.reaction,
                    "confidence": item.confidence,
                    "from_turn_id": item.from_turn_id,
                }
                if existing:
                    updated = False
                    if (existing.get("text") or "") != (item.text or ""):
                        existing["text"] = item.text or ""
                        updated = True
                    if item.ts and (existing.get("ts") or "") != item.ts:
                        existing["ts"] = item.ts
                        updated = True
                    if meta:
                        meta_out = dict(existing.get("meta") or {})
                        meta_out.update({k: v for k, v in meta.items() if v is not None})
                        existing["meta"] = meta_out
                    if updated:
                        existing.pop("hidden", None)
                        meta_out = existing.get("meta") or {}
                        if isinstance(meta_out, dict):
                            meta_out.pop("hidden", None)
                            meta_out.pop("replacement_text", None)
                            existing["meta"] = meta_out
                        changed = True
                    continue

                insert_idx = _find_last_index(tid)
                if insert_idx is None:
                    continue
                blk = timeline.block(
                    type="turn.feedback",
                    author=("user" if item.origin == "user" else "system"),
                    turn_id=tid,
                    ts=item.ts,
                    mime="text/markdown",
                    text=item.text or "",
                    path=path,
                    meta={k: v for k, v in meta.items() if v is not None},
                )
                blocks.insert(insert_idx + 1, blk)
                path_index[path] = blk
                changed = True

        if changed:
            timeline.blocks = blocks
            try:
                timeline.update_timestamp()
            except Exception:
                pass
        return changed
