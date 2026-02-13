# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/v2/proto.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List, Callable, Awaitable


@dataclass
class RuntimeSessionConfig:
    # Cache TTL pruning (prompt cache)
    cache_ttl_seconds: Optional[int] = 300
    cache_ttl_prune_buffer_seconds: int = 10
    cache_truncation_max_text_chars: int = 4000
    cache_truncation_max_field_chars: int = 1000
    cache_truncation_max_list_items: int = 50
    cache_truncation_max_dict_keys: int = 80
    cache_truncation_max_base64_chars: int = 4000
    cache_truncation_keep_recent_images: int = 2
    cache_truncation_max_image_pdf_b64_sum: int = 1_000_000
    keep_recent_turns: int = 10
    keep_recent_intact_turns: int = 2

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "cache_ttl_prune_buffer_seconds": self.cache_ttl_prune_buffer_seconds,
            "cache_truncation_max_text_chars": self.cache_truncation_max_text_chars,
            "cache_truncation_max_field_chars": self.cache_truncation_max_field_chars,
            "cache_truncation_max_list_items": self.cache_truncation_max_list_items,
            "cache_truncation_max_dict_keys": self.cache_truncation_max_dict_keys,
            "cache_truncation_max_base64_chars": self.cache_truncation_max_base64_chars,
            "cache_truncation_keep_recent_images": self.cache_truncation_keep_recent_images,
            "cache_truncation_max_image_pdf_b64_sum": self.cache_truncation_max_image_pdf_b64_sum,
            "keep_recent_turns": self.keep_recent_turns,
            "keep_recent_intact_turns": self.keep_recent_intact_turns,
        }


@dataclass
class RuntimeCacheConfig:
    # Limits for react.hide (editable tail window).
    editable_tail_size_in_tokens: int = 2000

    def to_dict(self) -> Dict[str, Any]:
        return {
            "editable_tail_size_in_tokens": self.editable_tail_size_in_tokens,
        }


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
    session: RuntimeSessionConfig = field(default_factory=RuntimeSessionConfig)
    cache: RuntimeCacheConfig = field(default_factory=RuntimeCacheConfig)
    # Legacy cache fields (prefer RuntimeCtx.session).
    cache_ttl_seconds: Optional[int] = None
    cache_truncation_max_text_chars: Optional[int] = None
    cache_truncation_max_field_chars: Optional[int] = None
    cache_truncation_max_list_items: Optional[int] = None
    cache_truncation_max_dict_keys: Optional[int] = None
    cache_truncation_max_base64_chars: Optional[int] = None
    cache_truncation_keep_recent_images: Optional[int] = None
    cache_truncation_max_image_pdf_b64_sum: Optional[int] = None
    # Optional hooks to inject blocks right before/after assistant completion is added to timeline.
    # These are runtime-only and should not be serialized.
    on_before_completion_contribution: Optional[Callable[[], Any]] = None
    on_after_completion_contribution: Optional[Callable[[], Any]] = None

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
            "session": self.session.to_dict() if self.session else {},
            "cache": self.cache.to_dict() if self.cache else {},
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "cache_truncation_max_text_chars": self.cache_truncation_max_text_chars,
            "cache_truncation_max_field_chars": self.cache_truncation_max_field_chars,
            "cache_truncation_max_list_items": self.cache_truncation_max_list_items,
            "cache_truncation_max_dict_keys": self.cache_truncation_max_dict_keys,
            "cache_truncation_max_base64_chars": self.cache_truncation_max_base64_chars,
            "cache_truncation_keep_recent_images": self.cache_truncation_keep_recent_images,
            "cache_truncation_max_image_pdf_b64_sum": self.cache_truncation_max_image_pdf_b64_sum,
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


class ToolCallView:
    """
    Base class for tool call/result truncation views.
    Subclasses should override build_call_replacement/build_result_replacement.
    """
    tool_id: str = ""

    def __init__(self, tool_id: Optional[str] = None) -> None:
        if tool_id:
            self.tool_id = tool_id

    def build_call_replacement(
        self,
        *,
        tool_call_block: Dict[str, Any],
        payload: Dict[str, Any],
        cfg: Optional[Any] = None,
    ) -> str:
        raise NotImplementedError

    def build_result_replacement(
        self,
        *,
        tool_result_block: Dict[str, Any],
        payload: Any,
        cfg: Optional[Any] = None,
    ) -> str:
        raise NotImplementedError
