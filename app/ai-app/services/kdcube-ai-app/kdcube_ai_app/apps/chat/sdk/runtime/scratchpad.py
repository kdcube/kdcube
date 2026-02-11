# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/runtime/scratchpad.py

from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import Ticket
from kdcube_ai_app.apps.chat.sdk.util import _shorten

@dataclass
class TurnPhase:
    name: str                      # "gate", "router", "tools", "answer", ...
    agent: Optional[str] = None    # "precheck_gate", "router", "answer_generator", ...
    meta: Dict[str, Any] = field(default_factory=dict)

class TurnPhaseError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.data = data or {}

class TurnScratchpad:
    def __init__(self, user, conversation_id, turn_id, text, attachments=None, gate_out_class=None):

        self._user = user
        self._conversation_id = conversation_id
        self._turn_id = turn_id

        self.timings = [] # USED

        if text is None:
            text = ""
        # User section
        self.user_text = text.strip()
        self.user_input_summary = ""
        self.uvec = None
        self.user_ts = None
        self.user_attachments = attachments # USED

        self.tlog = None

        # Answer section
        self.answer = None
        self.answer_raw = None
        self.answer_used_sids: List[int] = []
        self.answer_ts = None
        self.avec = None
        self.service_error = None
        self.turn_summary = None
        self.final_internal_thinking = None

        # User memory
        self.user_memory = None

        # same as filtered_historical_context_str but as list
        self.context_log_history = None

        self.solver_result = None

        self.context_stack = []
        self.turn_stack = []

        self.objective = None

        self.conversation_title = None # USED
        self.is_new_conversation = False

        # current turn
        self.proposed_facts: List[Dict[str, Any]] = []
        self.exceptions: List[Dict[str, Any]] = []
        self.short_artifacts: List[Dict[str, Any]] = []

        # clarification flow
        self.clarification_questions: List[str] = [] # USED
        self.suggested_followups: List[str] = []

        self.extracted_prefs: Dict[str, Any] = {"assertions": [], "exceptions": []}

        # Feedback extracted from current user message about a previous turn (if any)
        self.detected_feedback: Optional[dict] = None

        self.started_at = datetime.utcnow().isoformat() + "Z"

        self.agents_responses = dict()
        self.current_phase: Optional[TurnPhase] = None

        # Shortened preview of user text (used in logging / streaming headers)
        self.short_text = _shorten(self.user_text or "", 1000)

        # Routing & gate-related, but not tied to GateOut type itself
        self.route: Optional[str] = None # USED

        # Gate output (optional)
        self.gate_out_class = gate_out_class
        self.gate = None

        # Topics for this turn (plain + rich)
        self.turn_topics_plain: Optional[List[str]] = None
        self.topic_tags: Optional[List[str]] = None
        self.turn_topics_with_confidence: Optional[List[dict]] = None

        # Long-lived objective memories (reconciled every N turns)
        self.objective_memories: Optional[dict] = None

        # Plan history snapshots for this turn (stored into turn_log assistant.react_state.plans)
        self.react_plans: List[Dict[str, Any]] = []
        # React end-of-turn summary snapshot (stored into turn_log assistant.react_state)
        self.react_state: Optional[Dict[str, Any]] = None

        # Assistant-originated signals across this conversation
        self.assistant_signals_user_level: Optional[List[dict]] = None

    @property
    def user(self) -> str:
        return self._user

    @user.setter
    def user(self, value: str) -> None:
        self._user = value

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @conversation_id.setter
    def conversation_id(self, value: str) -> None:
        self._conversation_id = value

    @property
    def turn_id(self) -> str:
        return self._turn_id

    @turn_id.setter
    def turn_id(self, value: str) -> None:
        self._turn_id = value

    def set_phase(self, name: str, *, agent: str | None = None, **meta):
        self.current_phase = TurnPhase(name=name, agent=agent, meta=meta)

    @contextmanager
    def phase(self, name: str, *, agent: str | None = None, **meta):
        prev = self.current_phase
        self.current_phase = TurnPhase(name=name, agent=agent, meta=meta)
        try:
            yield
        finally:
            self.current_phase = prev

    def propose_fact(
            self,
            *,
            key: str,
            value: Any,
            desired: bool = True,
            scope: str = "conversation",
            confidence: float = 0.6,
            ttl_days: int = 365,
            reason: str = "turn-proposed",
    ):
        self.proposed_facts.append({
            "key": key,
            "value": value,
            "desired": bool(desired),
            "scope": scope,
            "confidence": float(confidence),
            "ttl_days": int(ttl_days),
            "reason": reason,
        })

    def add_exception(self, *, rule_key: str, value: Any, scope: str = "conversation", reason: str = "turn-exception"):
        self.exceptions.append({"rule_key": rule_key, "value": value, "scope": scope, "reason": reason})

    def add_artifact(self, *, kind: str, title: str, content: str, structured_content: dict = None):
        self.short_artifacts.append({
            "kind": kind,
            "title": title,
            "content": content,
            **({"structured_content": structured_content} if structured_content else {})
        })

    def register_agentic_response(self,
                                  agent_name: str,
                                  response: Any,):
        self.agents_responses[agent_name] = response

CTurnScratchpad = TurnScratchpad
