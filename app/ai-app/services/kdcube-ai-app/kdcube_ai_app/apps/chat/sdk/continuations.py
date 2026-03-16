# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, Literal


ContinuationKind = Literal["regular", "followup", "steer"]


@dataclass
class ContinuationEnvelope:
    message_id: str
    kind: ContinuationKind
    created_at: float
    sequence: int
    explicit: bool = False
    target_turn_id: Optional[str] = None
    active_turn_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "kind": self.kind,
            "created_at": float(self.created_at),
            "sequence": int(self.sequence),
            "explicit": bool(self.explicit),
            "target_turn_id": self.target_turn_id,
            "active_turn_id": self.active_turn_id,
            "payload": dict(self.payload or {}),
        }

    @classmethod
    def from_any(cls, raw: Any) -> "ContinuationEnvelope":
        if isinstance(raw, cls):
            return raw
        if not isinstance(raw, dict):
            raise TypeError(f"Unsupported continuation envelope payload: {type(raw)!r}")
        return cls(
            message_id=str(raw.get("message_id") or ""),
            kind=str(raw.get("kind") or "followup"),
            created_at=float(raw.get("created_at") or 0.0),
            sequence=int(raw.get("sequence") or 0),
            explicit=bool(raw.get("explicit")),
            target_turn_id=raw.get("target_turn_id"),
            active_turn_id=raw.get("active_turn_id"),
            payload=dict(raw.get("payload") or {}),
        )

    def task_payload(self):
        from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload

        return ChatTaskPayload.model_validate(self.payload or {})


class ConversationContinuationSource(Protocol):
    async def has_pending(self) -> bool: ...

    async def pending_count(self) -> int: ...

    async def peek_next(self) -> Optional[ContinuationEnvelope]: ...

    async def take_next(self) -> Optional[ContinuationEnvelope]: ...


_current_continuation_source: ContextVar[Optional[ConversationContinuationSource]] = ContextVar(
    "chat_current_continuation_source",
    default=None,
)


def get_current_conversation_continuation_source() -> Optional[ConversationContinuationSource]:
    return _current_continuation_source.get()


@contextmanager
def bind_current_conversation_continuation_source(source: Optional[ConversationContinuationSource]):
    token = _current_continuation_source.set(source)
    try:
        yield source
    finally:
        _current_continuation_source.reset(token)
