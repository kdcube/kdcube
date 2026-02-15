# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import json
import pathlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.budget import BudgetStateV2


@dataclass
class ReactContext:
    """
    Canonical in-memory state for a ReAct session.

    - All artifacts + sources live in timeline.json (context browser writes it).
    """
    max_sid: int = 0

    # I/O
    outdir: Optional[pathlib.Path] = None

    # current turn
    timezone: Optional[str] = None
    _turn_id: Optional[str] = None
    _conversation_id: Optional[str] = None
    user_attachments: List[Dict[str, Any]] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    budget_state: BudgetStateV2 = field(default_factory=BudgetStateV2)

    # plan & progress
    plan_steps: List[str] = field(default_factory=list)
    plan_status: Dict[str, str] = field(default_factory=dict)
    plan_ts: Optional[str] = None

    def bind_storage(self, outdir: pathlib.Path) -> "ReactContext":
        self.outdir = outdir
        return self

    def persist(self) -> None:
        """No-op: timeline.json is persisted by ContextBrowser."""
        return

    # ---------- Turn identity ----------
    @property
    def turn_id(self) -> Optional[str]:
        return self._turn_id

    @turn_id.setter
    def turn_id(self, value: Optional[str]) -> None:
        self._turn_id = value

    @property
    def conversation_id(self) -> Optional[str]:
        return self._conversation_id

    @conversation_id.setter
    def conversation_id(self, value: Optional[str]) -> None:
        self._conversation_id = value

    @property
    def user_id(self) -> Optional[str]:
        return self._user_id

    @user_id.setter
    def user_id(self, value: Optional[str]) -> None:
        self._user_id = value

    # ---------- Decision blocks ----------
