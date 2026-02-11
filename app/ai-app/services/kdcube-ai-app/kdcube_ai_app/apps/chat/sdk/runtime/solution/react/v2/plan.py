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
    last_ack_turn_id: str = ""
    last_ack_ts: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "steps": list(self.steps or []),
            "status": dict(self.status or {}),
            "created_ts": self.created_ts,
            "last_ts": self.last_ts,
            "origin_turn_id": self.origin_turn_id,
            "last_ack_turn_id": self.last_ack_turn_id,
            "last_ack_ts": self.last_ack_ts,
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
                last_ack_turn_id=str(raw.get("last_ack_turn_id") or ""),
                last_ack_ts=str(raw.get("last_ack_ts") or ""),
            )
        return None

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
        if ts:
            self.last_ts = ts
            self.last_ack_ts = ts
        if turn_id:
            self.last_ack_turn_id = str(turn_id)

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

    def format_plan_block(self, *, current: bool = False) -> str:
        header = "[REACT.PLAN]"
        if self.created_ts:
            header += f" ts={self.created_ts}"
        if current:
            header += " current=true"
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
            "last_ack_turn_id": snap.last_ack_turn_id,
            "last_ack_ts": snap.last_ack_ts,
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
    }


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
    blocks: List[Dict[str, Any]] = []
    updates = PlanSnapshot.extract_status_updates(notes, len(plan_steps or []))
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

    # Updated plan snapshot block (latest plan)
    plans_by_id, order = collect_plan_snapshots(timeline_blocks)
    if order:
        last_payload = plans_by_id.get(order[-1]) or {}
        snap = PlanSnapshot.from_any(last_payload)
        if snap:
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
            for k in ("plan_id", "origin_turn_id", "last_ack_turn_id", "last_ack_ts"):
                if k in meta and k not in payload:
                    payload[k] = meta.get(k)
        pid = str(payload.get("plan_id") or "").strip()
        if not pid:
            continue
        if pid not in plans_by_id:
            order.append(pid)
        plans_by_id[pid] = payload
    return plans_by_id, order


def build_active_plan_blocks(
    *,
    blocks: List[Dict[str, Any]],
    current_turn_id: str,
    current_ts: str,
) -> List[Dict[str, Any]]:
    plans_by_id, order = collect_plan_snapshots(blocks)
    if not plans_by_id:
        return []
    announce_blocks: List[Dict[str, Any]] = []
    # Only show the latest plan snapshot as active
    pid = order[-1]
    p = plans_by_id.get(pid) or {}
    snap = PlanSnapshot.from_any(p)
    if snap:
        summary = snap.plan_summary()
        if summary.get("complete"):
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
        announce_blocks.append({
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
                "last_ack_turn_id": p.get("last_ack_turn_id"),
                "last_ack_ts": p.get("last_ack_ts"),
            },
        })
    return announce_blocks
