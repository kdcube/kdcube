# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class PlanSnapshot:
    plan_id: str = ""
    steps: List[str] = field(default_factory=list)
    status: Dict[str, str] = field(default_factory=dict)
    created_ts: str = ""
    last_ts: str = ""
    origin_turn_id: str = ""
    last_turn_id: str = ""
    last_ack_turn_id: str = ""
    last_ack_ts: str = ""
    closed_ts: str = ""
    closed_turn_id: str = ""
    superseded_ts: str = ""
    superseded_turn_id: str = ""
    superseded_by_plan_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "steps": list(self.steps or []),
            "status": dict(self.status or {}),
            "created_ts": self.created_ts,
            "last_ts": self.last_ts,
            "origin_turn_id": self.origin_turn_id,
            "last_turn_id": self.last_turn_id,
            "last_ack_turn_id": self.last_ack_turn_id,
            "last_ack_ts": self.last_ack_ts,
            "closed_ts": self.closed_ts,
            "closed_turn_id": self.closed_turn_id,
            "superseded_ts": self.superseded_ts,
            "superseded_turn_id": self.superseded_turn_id,
            "superseded_by_plan_id": self.superseded_by_plan_id,
        }

    @classmethod
    def from_any(cls, raw: Any) -> Optional["PlanSnapshot"]:
        if isinstance(raw, PlanSnapshot):
            return raw
        if isinstance(raw, dict):
            return cls(
                plan_id=str(raw.get("plan_id") or ""),
                steps=list(raw.get("steps") or []),
                status=dict(raw.get("status") or {}),
                created_ts=str(raw.get("created_ts") or ""),
                last_ts=str(raw.get("last_ts") or ""),
                origin_turn_id=str(raw.get("origin_turn_id") or ""),
                last_turn_id=str(raw.get("last_turn_id") or ""),
                last_ack_turn_id=str(raw.get("last_ack_turn_id") or ""),
                last_ack_ts=str(raw.get("last_ack_ts") or ""),
                closed_ts=str(raw.get("closed_ts") or ""),
                closed_turn_id=str(raw.get("closed_turn_id") or ""),
                superseded_ts=str(raw.get("superseded_ts") or ""),
                superseded_turn_id=str(raw.get("superseded_turn_id") or ""),
                superseded_by_plan_id=str(raw.get("superseded_by_plan_id") or ""),
            )
        return None

    def touch(
        self,
        *,
        ts: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> None:
        if ts:
            self.last_ts = ts
        if turn_id:
            self.last_turn_id = str(turn_id)

    def update_status(
        self,
        updates: Dict[str, str],
        *,
        ts: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> None:
        if not isinstance(updates, dict) or not updates:
            return
        for k, v in updates.items():
            if v:
                self.status[str(k)] = v
        self.touch(ts=ts, turn_id=turn_id)
        if ts:
            self.last_ack_ts = ts
        if turn_id:
            self.last_ack_turn_id = self.last_turn_id

    def status_mark(self, idx: int) -> str:
        status = self.status.get(str(idx), "")
        if status == "done":
            return "✓"
        if status == "failed":
            return "✗"
        if status == "in_progress":
            return "…"
        return "□"

    def plan_summary(self) -> Dict[str, int | bool]:
        done_steps = len([k for k, v in (self.status or {}).items() if v == "done"])
        failed_steps = len([k for k, v in (self.status or {}).items() if v == "failed"])
        total_steps = len(self.steps or [])
        pending_steps = max(0, total_steps - done_steps - failed_steps)
        plan_complete = (pending_steps == 0) if total_steps else False
        return {
            "done": done_steps,
            "failed": failed_steps,
            "pending": pending_steps,
            "complete": plan_complete,
        }

    def is_closed(self) -> bool:
        return bool((self.closed_ts or "").strip())

    def is_superseded(self) -> bool:
        return bool((self.superseded_ts or "").strip())

    def is_complete(self) -> bool:
        return bool(self.plan_summary().get("complete"))

    def is_active(self) -> bool:
        return not self.is_closed() and not self.is_superseded() and not self.is_complete()

    def close(self, *, ts: Optional[str] = None, turn_id: Optional[str] = None) -> None:
        self.touch(ts=ts, turn_id=turn_id)
        if ts:
            self.closed_ts = ts
        if turn_id:
            self.closed_turn_id = str(turn_id)

    def supersede(
        self,
        *,
        ts: Optional[str] = None,
        turn_id: Optional[str] = None,
        by_plan_id: Optional[str] = None,
    ) -> None:
        self.touch(ts=ts, turn_id=turn_id)
        if ts:
            self.superseded_ts = ts
        if turn_id:
            self.superseded_turn_id = str(turn_id)
        if by_plan_id:
            self.superseded_by_plan_id = str(by_plan_id)

    def format_plan_block(self, *, current: bool = False) -> str:
        header = "[REACT.PLAN]"
        if self.created_ts:
            header += f" ts={self.created_ts}"
        if current:
            header += " current=true"
        if self.is_superseded():
            header += " superseded=true"
        if self.is_closed():
            header += " closed=true"
        lines = [header]
        for i, step in enumerate(self.steps or [], start=1):
            lines.append(f"{self.status_mark(i)} [{i}] {step}")
        return "\n".join(lines).strip()

    @staticmethod
    def extract_status_updates(notes_text: str, total: int) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if not notes_text:
            return out
        for raw in notes_text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if not (line.startswith("✓") or line.startswith("□") or line.startswith("✗") or line.startswith("…")):
                continue
            mark = line[0]
            rest = line[1:].strip()
            num = ""
            if rest.startswith("["):
                idx = rest.find("]")
                if idx != -1:
                    num = rest[1:idx].strip()
            if not num:
                parts = rest.split(None, 1)
                if parts:
                    num = parts[0].strip().rstrip(".").rstrip(":")
            if not num.isdigit():
                continue
            n = int(num)
            if n <= 0 or (total and n > total):
                continue
            if mark == "□":
                status = "pending"
            elif mark == "✓":
                status = "done"
            elif mark == "✗":
                status = "failed"
            else:
                status = "in_progress"
            out[str(n)] = status
        return out


def create_plan_snapshot(*, plan: Any, turn_id: str, created_ts: str) -> PlanSnapshot:
    steps = list(getattr(plan, "steps", None) or (plan.get("steps") if isinstance(plan, dict) else []) or [])
    plan_id = (getattr(plan, "plan_id", None) or (plan.get("plan_id") if isinstance(plan, dict) else "") or "").strip()
    if not plan_id:
        plan_id = f"plan:{turn_id}:{uuid.uuid4().hex[:8]}" if turn_id else f"plan:{uuid.uuid4().hex[:8]}"
    return PlanSnapshot(
        plan_id=plan_id,
        steps=steps,
        status={},
        created_ts=created_ts,
        last_ts=created_ts,
        origin_turn_id=turn_id,
        last_turn_id=turn_id,
    )


def build_plan_block(*, snap: PlanSnapshot, turn_id: str, ts: str) -> Dict[str, Any]:
    payload = snap.to_dict()
    return {
        "type": "react.plan",
        "author": "react",
        "turn_id": turn_id,
        "ts": ts,
        "mime": "application/json",
        "path": f"ar:{turn_id}.react.plan.{snap.plan_id}" if snap.plan_id else f"ar:{turn_id}.react.plan",
        "text": json.dumps(payload, ensure_ascii=False, indent=2),
        "meta": {
            "plan_id": snap.plan_id,
            "origin_turn_id": snap.origin_turn_id,
            "created_ts": snap.created_ts,
            "last_turn_id": snap.last_turn_id,
            "last_ack_turn_id": snap.last_ack_turn_id,
            "last_ack_ts": snap.last_ack_ts,
            "last_ts": snap.last_ts,
            "closed_ts": snap.closed_ts,
            "closed_turn_id": snap.closed_turn_id,
            "superseded_ts": snap.superseded_ts,
            "superseded_turn_id": snap.superseded_turn_id,
            "superseded_by_plan_id": snap.superseded_by_plan_id,
        },
    }


def build_plan_carry_block(*, snap: PlanSnapshot, turn_id: str, ts: str) -> Dict[str, Any]:
    payload = snap.to_dict()
    return {
        "type": "react.plan",
        "author": "react",
        "turn_id": turn_id,
        "ts": ts,
        "mime": "application/json",
        "path": f"ar:{turn_id}.react.plan.{snap.plan_id}.carry" if snap.plan_id else f"ar:{turn_id}.react.plan.carry",
        "text": json.dumps(payload, ensure_ascii=False, indent=2),
        "meta": {
            "plan_id": snap.plan_id,
            "origin_turn_id": snap.origin_turn_id,
            "created_ts": snap.created_ts,
            "last_turn_id": snap.last_turn_id,
            "last_ack_turn_id": snap.last_ack_turn_id,
            "last_ack_ts": snap.last_ack_ts,
            "last_ts": snap.last_ts,
            "closed_ts": snap.closed_ts,
            "closed_turn_id": snap.closed_turn_id,
            "superseded_ts": snap.superseded_ts,
            "superseded_turn_id": snap.superseded_turn_id,
            "superseded_by_plan_id": snap.superseded_by_plan_id,
            "preserved_by_compaction": True,
        },
    }


def build_plan_ack_block(
    *,
    ack_items: List[Dict[str, Any]],
    turn_id: str,
    ts: str,
    iteration: int,
) -> Dict[str, Any]:
    lines = []
    for it in ack_items:
        sym = "✓" if it.get("status") == "done" else ("✗" if it.get("status") == "failed" else "…")
        step = it.get("step")
        text = it.get("text") or ""
        lines.append(f"{sym} {step}. {text}".strip())
    return {
        "type": "react.plan.ack",
        "author": "react",
        "turn_id": turn_id,
        "ts": ts,
        "mime": "text/markdown",
        "path": f"ar:{turn_id}.react.plan.ack.{iteration}",
        "text": "\n".join(lines).strip(),
        "meta": {
            "iteration": iteration,
        },
    }


def apply_plan_status_updates(
    *,
    updates: Dict[str, str],
    plan_steps: List[str],
    status_map: Dict[str, str],
    timeline_blocks: List[Dict[str, Any]],
    turn_id: str,
    iteration: int,
    ts: Optional[str] = None,
) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    blocks: List[Dict[str, Any]] = []
    if not updates:
        return status_map, blocks

    new_updates: Dict[str, str] = {}
    for k, v in updates.items():
        if status_map.get(k) != v:
            new_updates[k] = v

    if not new_updates:
        return status_map, blocks

    status_map = dict(status_map)
    status_map.update(new_updates)

    # Ack block
    ack_items = []
    for k, v in new_updates.items():
        idx = int(k) - 1
        step_text = plan_steps[idx] if 0 <= idx < len(plan_steps) else ""
        ack_items.append({
            "step": int(k),
            "status": v,
            "text": step_text,
        })
    if ack_items:
        blocks.append(build_plan_ack_block(
            ack_items=ack_items,
            turn_id=turn_id,
            ts=ts or time.time(),
            iteration=iteration,
        ))

    # Updated plan snapshot block (latest open plan)
    snap = latest_active_plan_snapshot(timeline_blocks)
    if snap and snap.is_active():
        snap.update_status(
            dict(status_map),
            ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            turn_id=str(turn_id or ""),
        )
        blocks.append(build_plan_block(
            snap=snap,
            turn_id=turn_id,
            ts=ts or time.time(),
        ))

    return status_map, blocks


def apply_plan_updates(
    *,
    notes: str,
    plan_steps: List[str],
    status_map: Dict[str, str],
    timeline_blocks: List[Dict[str, Any]],
    turn_id: str,
    iteration: int,
    ts: Optional[str] = None,
) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Parse notes, update status map, and return blocks to contribute (ack + updated plan)."""
    updates = PlanSnapshot.extract_status_updates(notes, len(plan_steps or []))
    return apply_plan_status_updates(
        updates=updates,
        plan_steps=plan_steps,
        status_map=status_map,
        timeline_blocks=timeline_blocks,
        turn_id=turn_id,
        iteration=iteration,
        ts=ts,
    )


def collect_plan_snapshots(blocks: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    plans_by_id: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for b in (blocks or []):
        if not isinstance(b, dict):
            continue
        if (b.get("type") or "") != "react.plan":
            continue
        payload = None
        text = b.get("text")
        if isinstance(text, str) and text.strip():
            try:
                payload = json.loads(text)
            except Exception:
                payload = None
        if not isinstance(payload, dict):
            payload = {}
        meta = b.get("meta") if isinstance(b.get("meta"), dict) else {}
        if meta:
            for k in (
                "plan_id",
                "origin_turn_id",
                "created_ts",
                "last_ts",
                "last_turn_id",
                "last_ack_turn_id",
                "last_ack_ts",
                "closed_ts",
                "closed_turn_id",
                "superseded_ts",
                "superseded_turn_id",
                "superseded_by_plan_id",
            ):
                if k in meta and k not in payload:
                    payload[k] = meta.get(k)
        pid = str(payload.get("plan_id") or "").strip()
        if not pid:
            continue
        if pid not in plans_by_id:
            order.append(pid)
        plans_by_id[pid] = payload
    return plans_by_id, order


def plan_snapshot_ref(plan_id: str) -> str:
    return f"ar:plan.latest:{plan_id}" if plan_id else ""


def _plan_sort_key(snap: PlanSnapshot) -> Tuple[str, str]:
    return (
        (snap.last_ts or snap.created_ts or "").strip(),
        snap.plan_id,
    )


def latest_plan_snapshot_by_id(
    blocks: List[Dict[str, Any]],
    plan_id: str,
    *,
    include_preserved: bool = False,
) -> Optional[PlanSnapshot]:
    wanted = str(plan_id or "").strip()
    if not wanted:
        return None
    for b in reversed(blocks or []):
        snap = _plan_snapshot_from_block(b, include_preserved=include_preserved)
        if snap and snap.plan_id == wanted:
            return snap
    return None


def latest_plan_block_by_id(
    blocks: List[Dict[str, Any]],
    plan_id: str,
    *,
    include_preserved: bool = False,
) -> Optional[Dict[str, Any]]:
    wanted = str(plan_id or "").strip()
    if not wanted:
        return None
    for b in reversed(blocks or []):
        snap = _plan_snapshot_from_block(b, include_preserved=include_preserved)
        if snap and snap.plan_id == wanted:
            return b
    return None


def latest_active_plan_snapshot(blocks: List[Dict[str, Any]]) -> Optional[PlanSnapshot]:
    plans_by_id, order = collect_plan_snapshots(blocks)
    candidates: List[PlanSnapshot] = []
    for pid in order:
        snap = PlanSnapshot.from_any(plans_by_id.get(pid) or {})
        if snap and snap.is_active():
            candidates.append(snap)
    if not candidates:
        return None
    candidates.sort(key=_plan_sort_key)
    return candidates[-1]


def latest_plan_snapshot(blocks: List[Dict[str, Any]]) -> Optional[PlanSnapshot]:
    plans_by_id, order = collect_plan_snapshots(blocks)
    snapshots: List[PlanSnapshot] = []
    for pid in order:
        snap = PlanSnapshot.from_any(plans_by_id.get(pid) or {})
        if snap:
            snapshots.append(snap)
    if not snapshots:
        return None
    snapshots.sort(key=_plan_sort_key)
    return snapshots[-1]


def close_latest_plan_snapshot(
    *,
    blocks: List[Dict[str, Any]],
    turn_id: str,
    ts: str,
) -> Optional[PlanSnapshot]:
    snap = latest_active_plan_snapshot(blocks)
    if not snap or not snap.is_active():
        return None
    snap.close(ts=ts, turn_id=turn_id)
    return snap


def close_plan_snapshot(
    *,
    blocks: List[Dict[str, Any]],
    plan_id: str,
    turn_id: str,
    ts: str,
) -> Optional[PlanSnapshot]:
    snap = latest_plan_snapshot_by_id(blocks, plan_id)
    if not snap or not snap.is_active():
        return None
    snap.close(ts=ts, turn_id=turn_id)
    return snap


def activate_plan_snapshot(
    *,
    blocks: List[Dict[str, Any]],
    plan_id: str,
    turn_id: str,
    ts: str,
) -> Optional[PlanSnapshot]:
    snap = latest_plan_snapshot_by_id(blocks, plan_id)
    if not snap or not snap.is_active():
        return None
    snap.touch(ts=ts, turn_id=turn_id)
    return snap


def supersede_plan_snapshot(
    *,
    blocks: List[Dict[str, Any]],
    plan_id: str,
    turn_id: str,
    ts: str,
    by_plan_id: str,
) -> Optional[PlanSnapshot]:
    snap = latest_plan_snapshot_by_id(blocks, plan_id)
    if not snap or not snap.is_active():
        return None
    snap.supersede(ts=ts, turn_id=turn_id, by_plan_id=by_plan_id)
    return snap


def build_active_plan_blocks(
    *,
    blocks: List[Dict[str, Any]],
    current_turn_id: str,
    current_ts: str,
) -> List[Dict[str, Any]]:
    plans_by_id, order = collect_plan_snapshots(blocks)
    if not plans_by_id:
        return []
    pid = order[-1]
    p = plans_by_id.get(pid) or {}
    snap = PlanSnapshot.from_any(p)
    if snap and not snap.is_active():
        return []
    if snap:
        header = f"[ACTIVE PLAN] id={snap.plan_id}"
        if snap.origin_turn_id:
            header += f" origin={snap.origin_turn_id}"
        if snap.last_ack_ts:
            header += f" last_ack={snap.last_ack_ts}"
        text = header + "\n" + snap.format_plan_block(current=True)
    else:
        text = json.dumps(p, ensure_ascii=False, indent=2)
    return [{
        "type": "react.plan.active",
        "author": "react",
        "turn_id": current_turn_id,
        "ts": current_ts,
        "mime": "text/markdown",
        "path": f"ar:{current_turn_id}.react.plan.active.{pid}" if pid else "",
        "text": text,
        "meta": {
            "plan_id": pid,
            "origin_turn_id": p.get("origin_turn_id"),
            "last_turn_id": p.get("last_turn_id"),
            "last_ack_turn_id": p.get("last_ack_turn_id"),
            "last_ack_ts": p.get("last_ack_ts"),
            "last_ts": p.get("last_ts"),
            "closed_ts": p.get("closed_ts"),
            "closed_turn_id": p.get("closed_turn_id"),
            "superseded_ts": p.get("superseded_ts"),
            "superseded_turn_id": p.get("superseded_turn_id"),
            "superseded_by_plan_id": p.get("superseded_by_plan_id"),
        },
    }]


def _plan_snapshot_from_block(
    block: Dict[str, Any],
    *,
    include_preserved: bool = False,
) -> Optional[PlanSnapshot]:
    if not isinstance(block, dict):
        return None
    btype = (block.get("type") or "").strip()
    allowed = {"react.plan"}
    if include_preserved:
        allowed.add("react.plan.preserved")
    if btype not in allowed:
        return None
    payload = None
    text = block.get("text")
    if isinstance(text, str) and text.strip():
        try:
            payload = json.loads(text)
        except Exception:
            payload = None
    if not isinstance(payload, dict):
        payload = {}
    meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
    if meta:
        for k in (
            "plan_id",
            "origin_turn_id",
            "created_ts",
            "last_ts",
            "last_turn_id",
            "last_ack_turn_id",
            "last_ack_ts",
            "closed_ts",
            "closed_turn_id",
            "superseded_ts",
            "superseded_turn_id",
            "superseded_by_plan_id",
        ):
            if k in meta and k not in payload:
                payload[k] = meta.get(k)
    snap = PlanSnapshot.from_any(payload)
    if snap and snap.plan_id:
        return snap
    return None


def _clone_preserved_plan_block(
    *,
    block: Dict[str, Any],
    preserved_type: str,
    preserved_path: str,
    plan_id: str,
    source_path: str,
) -> Dict[str, Any]:
    cloned = dict(block or {})
    meta = dict(cloned.get("meta") or {})
    meta.pop("replacement_text", None)
    meta["plan_id"] = plan_id
    meta["source_path"] = source_path
    meta["preserved_by_compaction"] = True
    cloned["type"] = preserved_type
    cloned["path"] = preserved_path
    cloned["hidden"] = True
    cloned.pop("replacement_text", None)
    cloned["meta"] = meta
    return cloned


def build_compacted_plan_history_blocks(
    *,
    blocks: List[Dict[str, Any]],
    turn_id: str,
    ts: str,
    exclude_plan_ids: Optional[set[str]] = None,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    excluded = {pid for pid in (exclude_plan_ids or set()) if pid}
    entries: Dict[str, Dict[str, Any]] = {}
    current_plan_id = ""

    for blk in (blocks or []):
        if not isinstance(blk, dict):
            continue
        btype = (blk.get("type") or "").strip()
        meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}

        snap = _plan_snapshot_from_block(blk, include_preserved=True)
        if snap and snap.plan_id:
            current_plan_id = snap.plan_id
            if snap.plan_id in excluded:
                continue
            entry = entries.setdefault(snap.plan_id, {"plan_id": snap.plan_id})
            entry["snapshot"] = snap
            entry["snapshot_block"] = blk
            continue

        plan_id = str(meta.get("plan_id") or current_plan_id or "").strip()
        if not plan_id or plan_id in excluded:
            continue
        entry = entries.setdefault(plan_id, {"plan_id": plan_id})
        if btype in {"react.plan.ack", "react.plan.ack.preserved"}:
            entry["ack_block"] = blk
            continue
        if btype in {"react.notes", "react.notes.preserved"}:
            entry["note_block"] = blk
            continue

    if not entries:
        return None, []

    ordered = list(entries.values())
    ordered.sort(
        key=lambda item: (
            (
                (
                    (item.get("snapshot").last_ts if isinstance(item.get("snapshot"), PlanSnapshot) else "")
                    or (item.get("snapshot").created_ts if isinstance(item.get("snapshot"), PlanSnapshot) else "")
                    or ((item.get("snapshot_block") or {}).get("ts") or "")
                    or ((item.get("ack_block") or {}).get("ts") or "")
                    or ((item.get("note_block") or {}).get("ts") or "")
                )
            ),
            item.get("plan_id") or "",
        ),
    )
    ordered.reverse()

    lines = [
        "[COMPACTED PLAN HISTORY]",
        "Older plans were compacted out of the main visible stream.",
        "Use react.read([...]) on the refs below if one becomes relevant again.",
    ]
    preserved_blocks: List[Dict[str, Any]] = []

    for idx, entry in enumerate(ordered, start=1):
        snap = entry.get("snapshot")
        if not isinstance(snap, PlanSnapshot):
            continue
        plan_id = str(entry.get("plan_id") or "").strip()
        if not plan_id:
            continue
        tags: List[str] = []
        if snap.is_closed():
            tags.append("closed")
        elif snap.is_superseded():
            tags.append("superseded")
        elif snap.is_complete():
            tags.append("complete")
        else:
            tags.append("unfinished")
        last_ts = snap.last_ts or snap.created_ts or ""
        header = f"- plan #{idx} id={plan_id} ({', '.join(tags)})"
        if last_ts:
            header += f" last={last_ts}"
        lines.append(header)
        for step_idx, step in enumerate(snap.steps or [], start=1):
            lines.append(f"  {snap.status_mark(step_idx)} [{step_idx}] {step}")

        snapshot_block = entry.get("snapshot_block") if isinstance(entry.get("snapshot_block"), dict) else None
        if snapshot_block:
            snapshot_ref = plan_snapshot_ref(plan_id)
            source_path = (snapshot_block.get("path") or "").strip()
            lines.append(f"  snapshot_ref: {snapshot_ref}")
            preserved_blocks.append(
                _clone_preserved_plan_block(
                    block=snapshot_block,
                    preserved_type="react.plan.preserved",
                    preserved_path=snapshot_ref,
                    plan_id=plan_id,
                    source_path=source_path,
                )
            )

        note_block = entry.get("note_block") if isinstance(entry.get("note_block"), dict) else None
        if note_block:
            note_text = (note_block.get("text") or "").strip() if isinstance(note_block.get("text"), str) else ""
            if note_text:
                preview = note_text.splitlines()[0].strip()
                if len(preview) > 160:
                    preview = preview[:157] + "..."
                lines.append(f"  latest_note_preview: {preview}")

    if len(lines) <= 3:
        return None, []

    history_block = {
        "type": "react.plan.history",
        "author": "react",
        "turn_id": turn_id,
        "ts": ts,
        "mime": "text/markdown",
        "path": f"ar:{turn_id}.react.plan.history" if turn_id else "",
        "text": "\n".join(lines).strip(),
        "meta": {
            "preserved_by_compaction": True,
        },
    }
    return history_block, preserved_blocks
