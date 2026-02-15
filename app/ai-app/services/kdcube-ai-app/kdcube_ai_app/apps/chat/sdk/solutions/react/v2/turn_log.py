from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class TurnLog:
    """
    Minimal turn log: timestamp + ordered timeline blocks for this turn.
    """
    turn_id: str
    ts: str
    blocks: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "ts": self.ts,
            "blocks": list(self.blocks or []),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "TurnLog":
        return cls(
            turn_id=str(payload.get("turn_id") or ""),
            ts=str(payload.get("ts") or ""),
            blocks=list(payload.get("blocks") or []),
        )

    def to_markdown(self) -> str:
        md = f"## Turn {self.turn_id} (timestamp: {self.ts})\n\n"
        return md
