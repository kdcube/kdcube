# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/result.py

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple


@dataclass
class SolverResult:
    """
    Minimal solver result wrapper.
    Stores raw payload and exposes only what the pipeline needs.
    """
    raw: Dict[str, Any]

    @classmethod
    def from_payload(cls, payload: Optional[Dict[str, Any]]) -> "SolverResult":
        return cls(raw=payload or {})

    def to_payload(self) -> Dict[str, Any]:
        return dict(self.raw or {})

    @property
    def plan(self) -> Dict[str, Any]:
        return (self.raw or {}).get("plan") or {}

    @property
    def execution(self) -> Dict[str, Any]:
        return (self.raw or {}).get("execution") or {}

    @property
    def mode(self) -> str:
        return (self.raw or {}).get("mode") or (self.plan.get("next_step") or "")

    def status(self) -> str:
        """
        Possible statuses:
        - failed
        - clarification_only
        - llm_only
        - success
        """
        mode = (self.mode or "").strip().lower()
        if mode in {"clarification_only", "llm_only"}:
            return mode
        exec_err = (self.execution or {}).get("error") or (self.raw.get("react") or {}).get("error")
        if exec_err:
            return "failed"
        return "success"

    def run_id(self) -> str:
        return (self.raw or {}).get("run_id") or ""

    def outdir_workdir(self) -> Tuple[str, str]:
        return (
            (self.raw or {}).get("outdir") or "",
            (self.raw or {}).get("workdir") or "",
        )
