# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/v2/proto.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List, Callable, Awaitable


@dataclass
class RuntimeCtx:
    tenant: Optional[str] = None
    project: Optional[str] = None
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None
    user_type: Optional[str] = None
    turn_id: Optional[str] = None
    bundle_id: Optional[str] = None
    timezone: Optional[str] = None
    max_tokens: Optional[int] = None
    max_iterations: Optional[int] = None
    workdir: Optional[str] = None
    outdir: Optional[str] = None
    model_service: Optional[Any] = None
    on_before_compaction: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
    on_after_compaction: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
    save_summary: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
    started_at: Optional[str] = ""
    debug_log_announce: bool = True
    debug_log_sources_pool: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant": self.tenant,
            "project": self.project,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "user_type": self.user_type,
            "turn_id": self.turn_id,
            "bundle_id": self.bundle_id,
            "timezone": self.timezone,
            "max_tokens": self.max_tokens,
            "max_iterations": self.max_iterations,
            "workdir": self.workdir,
            "outdir": self.outdir,
            "started_at": self.started_at,
            "debug_log_announce": bool(self.debug_log_announce),
            "debug_log_sources_pool": bool(self.debug_log_sources_pool),
        }


@dataclass
class SlotSpec:
    """
    Minimal slot spec used by react v2 mapping only.
    This is intentionally tiny and local to react v2.
    """
    description: str = ""
    mime: Optional[str] = None
    format: Optional[str] = None
    type: str = "inline"

    @classmethod
    def from_any(cls, raw: Any) -> Optional["SlotSpec"]:
        if isinstance(raw, SlotSpec):
            return raw
        if isinstance(raw, dict):
            return cls(
                description=str(raw.get("description") or ""),
                mime=raw.get("mime"),
                format=raw.get("format"),
                type=str(raw.get("type") or raw.get("kind") or "inline"),
            )
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "mime": self.mime,
            "format": self.format,
            "type": self.type,
        }


@dataclass
class ReactResult:
    ok: bool = True
    out: List[Dict[str, Any]] = field(default_factory=list)
    sources_pool: List[Dict[str, Any]] = field(default_factory=list)
    final_answer: Optional[str] = None
    suggested_followups: List[str] = field(default_factory=list)
    error: Any = None
    round_timings: List[Dict[str, Any]] = field(default_factory=list)
    total_runtime_sec: float = 0.0
    run_id: str = ""
    outdir: str = ""
    workdir: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "out": list(self.out or []),
            "sources_pool": list(self.sources_pool or []),
            "final_answer": self.final_answer,
            "suggested_followups": list(self.suggested_followups or []),
            "error": self.error,
            "round_timings": list(self.round_timings or []),
            "total_runtime_sec": float(self.total_runtime_sec or 0.0),
            "run_id": self.run_id or "",
            "outdir": self.outdir or "",
            "workdir": self.workdir or "",
        }

@dataclass
class ReactStateSnapshot:
    iteration: int = 0
    max_iterations: int = 0
    exit_reason: str = ""
    error: Any = None
    decision_retries: int = 0
    plan_steps: List[str] = field(default_factory=list)
    plan_status: Dict[str, str] = field(default_factory=dict)
    plans: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iteration": int(self.iteration or 0),
            "max_iterations": int(self.max_iterations or 0),
            "exit_reason": self.exit_reason or "",
            "error": self.error,
            "decision_retries": int(self.decision_retries or 0),
            "plan_steps": list(self.plan_steps or []),
            "plan_status": dict(self.plan_status or {}),
            "plans": list(self.plans or []),
        }

    @classmethod
    def from_any(cls, raw: Any) -> Optional["ReactStateSnapshot"]:
        if isinstance(raw, ReactStateSnapshot):
            return raw
        if isinstance(raw, dict):
            return cls(
                iteration=int(raw.get("iteration") or 0),
                max_iterations=int(raw.get("max_iterations") or 0),
                exit_reason=str(raw.get("exit_reason") or ""),
                error=raw.get("error"),
                decision_retries=int(raw.get("decision_retries") or 0),
                plan_steps=list(raw.get("plan_steps") or []),
                plan_status=dict(raw.get("plan_status") or {}),
            )
        return None

    @classmethod
    def from_state(cls, state: Dict[str, Any]) -> "ReactStateSnapshot":
        plans = list(state.get("plans") or [])
        if not plans:
            raw = state.get("plan_history") or []
            plans = []
            for p in raw:
                if hasattr(p, "to_dict"):
                    plans.append(p.to_dict())
                elif isinstance(p, dict):
                    plans.append(p)
        return cls(
            iteration=int(state.get("iteration") or 0),
            max_iterations=int(state.get("max_iterations") or 0),
            exit_reason=state.get("exit_reason") or "",
            error=state.get("error"),
            decision_retries=int(state.get("decision_retries") or 0),
            plan_steps=list(state.get("plan_steps") or []),
            plan_status=dict(state.get("plan_status") or {}),
            plans=plans,
        )
