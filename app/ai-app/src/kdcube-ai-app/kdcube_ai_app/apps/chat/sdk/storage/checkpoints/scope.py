"""Explicit trusted scope and the three native checkpoint primary keys.

Callers supply resolved platform identity on each operation. An opaque legacy
thread string cannot be used to recover identity or authorize a cold lookup.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Literal

from kdcube_ai_app.apps.chat.sdk.storage.checkpoints.errors import CheckpointIntegrityError


def _text(value: str, name: str, *, empty: bool = False) -> None:
    if not isinstance(value, str) or "\x00" in value or (not empty and not value.strip()):
        raise ValueError(f"{name} must be an explicit string")


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


@dataclass(frozen=True)
class CheckpointScope:
    tenant: str
    project: str
    bundle_id: str
    agent_id: str
    user_id: str
    conversation_id: str
    thread_id: str

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            _text(value, name)

    def bind(self, key: PayloadKey) -> None:
        if self.thread_id != key.thread_id:
            raise CheckpointIntegrityError("checkpoint thread does not match trusted scope")


@dataclass(frozen=True)
class PayloadKey:
    family: Literal["checkpoint", "blob", "write"]
    thread_id: str
    checkpoint_ns: str = ""
    checkpoint_id: str = ""
    channel: str = ""
    version: str = ""
    task_id: str = ""
    idx: int | None = None

    def __post_init__(self) -> None:
        _text(self.thread_id, "thread_id")
        _text(self.checkpoint_ns, "checkpoint_ns", empty=True)
        if self.family not in ("checkpoint", "blob", "write"):
            raise ValueError("unknown checkpoint payload family")
        required = {"checkpoint": ("checkpoint_id",), "blob": ("channel", "version"),
                    "write": ("checkpoint_id", "task_id")}[self.family]
        for name in ("checkpoint_id", "channel", "version", "task_id"):
            value = getattr(self, name)
            _text(value, name, empty=name not in required)
            if name not in required and value:
                raise ValueError(f"{name} is not part of the {self.family} primary key")
        if self.family == "write":
            if type(self.idx) is not int:
                raise ValueError("a write key requires an integer idx")
        elif self.idx is not None:
            raise ValueError("idx is only part of a write key")

    def digest(self, scope: CheckpointScope) -> str:
        scope.bind(self)
        return sha256(canonical_json({"scope": asdict(scope), "key": asdict(self)})).hexdigest()
