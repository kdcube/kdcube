# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/runtime/scratchpad.py

from __future__ import annotations
import asyncio, copy
from dataclasses import dataclass, field
from typing import List, Literal, Optional, Dict, Any, Iterable
from pydantic import BaseModel, Field
from datetime import datetime
import json, re

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import Ticket

LINE_RE = re.compile(r'^(?P<time>\d{2}:\d{2}:\d{2})\s+\[(?P<tag>[^\]]+)\]\s*(?P<content>.*)$')

# ============================================================================
# SharedScratchpad - In-memory turn state
# ============================================================================

class SharedScratchpad:
    """
    Per-turn, in-memory shared pad:
      - "turn": coordinator-initialized context (summary_ctx, policy_summary, topics, etc.)
      - "workers": { <section>: { <key>: <value>, ... } }
    Workers only write to their own section.
    No persistence here — the coordinator persists aggregated facts/exceptions/artifacts via TurnScratchpad.
    """
    def __init__(self):
        self._lock = asyncio.Lock()
        self._data: Dict[str, Any] = {"turn": {}, "workers": {}}

    async def init_turn(self, **turn_fields):
        async with self._lock:
            self._data["turn"] = {**(self._data.get("turn") or {}), **turn_fields}

    def _ensure_section_unlocked(self, section: str) -> Dict[str, Any]:
        w = self._data.setdefault("workers", {})
        return w.setdefault(section, {})

    async def write(self, section: str, **kv) -> None:
        async with self._lock:
            sect = self._ensure_section_unlocked(section)
            sect.update(kv)

    async def append_list(self, section: str, key: str, items: Iterable[Any]) -> None:
        async with self._lock:
            sect = self._ensure_section_unlocked(section)
            arr = sect.setdefault(key, [])
            arr.extend(list(items or []))

    async def read(self, section: str, *keys: str) -> Dict[str, Any]:
        async with self._lock:
            if section == "turn":
                src = self._data.get("turn") or {}
            else:
                src = (self._data.get("workers") or {}).get(section) or {}
            if not keys:
                return copy.deepcopy(src)
            return {k: copy.deepcopy(src[k]) for k in keys if k in src}

    async def have_keys(self, section: str, *keys: str) -> bool:
        async with self._lock:
            if section == "turn":
                src = self._data.get("turn") or {}
            else:
                src = (self._data.get("workers") or {}).get(section) or {}
            return all(k in src for k in keys)

    async def snapshot(self) -> Dict[str, Any]:
        async with self._lock:
            return copy.deepcopy(self._data)


class TurnScratchpad:
    def __init__(self, user, conversation_id, turn_id, text, attachments=None):

        self.user = user
        self.conversation_id = conversation_id
        self.turn_id = turn_id

        self.timings = []

        # User section
        self.user_text = text
        self.uvec = None
        self.user_attachments = attachments

        self.tlog = new_turn_log(user_id=user, conversation_id=conversation_id, turn_id=turn_id)

        # Answer section
        self.answer = None
        self.avec = None
        self.turn_summary = None
        self.final_internal_thinking = None

        # User memory
        self.user_memory = None

        # same as filtered_guess_ctx_str but as list
        self.context_log_history = None

        self.solver_result_interpretation_instruction = ""
        self.past_turn_interpretation_instruction = ""

        # solver outcome
        self.turn_artifact = None
        self.solver_status = None
        self.context_stack = []
        self.turn_stack = []

        # exact-reference
        self.relevant_turn_ids: List[str] = []

        # ticket flow
        self.open_ticket: Optional[dict|Ticket] = None
        self.ticket_answer_text: Optional[str] = None
        self.ticket_resolved: bool = False
        self.ticket_resolved_with_answer: bool = False
        self.history_depth_bonus: int = 0

        self.objective = None

        self.conversation_title = None
        self.is_new_conversation = False
        self.active_set = None    # last known active set (reconciled)
        self.active_set_trimmed = None # minified version of active set (for LLMs)

        # current turn
        self.proposed_facts: List[Dict[str, Any]] = []
        self.exceptions: List[Dict[str, Any]] = []
        self.short_artifacts: List[Dict[str, Any]] = []

        # clarification flow
        self.clarification_questions: List[str] = []
        self.user_shortcuts: List[str] = []

        # preferences and policies
        self.conversation_snapshot: Dict[str, Any] = {}
        self.extracted_prefs: Dict[str, Any] = {"assertions": [], "exceptions": []}
        self.policy = None
        self.policy_summary = None
        self.pref_view = None
        # previous turn conversation
        self.previous_turn_conversation_metadata: Optional[Dict[str, Any]] = None

        # Feedback extracted from current user message about a previous turn (if any)
        self.detected_feedback: Optional[dict] = None

        # citations
        self.citations: List[Dict] = []

        self.started_at = datetime.utcnow().isoformat() + "Z"

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


# ============================================================================
# TurnLog - Structured logging
# ============================================================================

LogArea = Literal["objective", "user", "attachments", "solver", "answer", "note", "summary"]
LogLevel = Literal["info", "warn", "error"]

class TurnLogEntry(BaseModel):
    t: str = Field(..., description="time-part like HH:MM:SS")
    area: LogArea
    msg: str
    level: LogLevel = "info"
    data: Optional[Dict[str, Any]] = None

    def to_line(self) -> str:
        base = f"{self.t} [{self.area}] {self.msg}"
        if self.level != "info":
            base += f"  !{self.level}"
        return base


class TurnLog(BaseModel):
    user_id: str
    conversation_id: str
    turn_id: str
    started_at_iso: str
    ended_at_iso: Optional[str] = None
    entries: List[TurnLogEntry] = Field(default_factory=list)
    state: Optional[Dict[str, Any]] = Field(default_factory=dict)

    def _nowt(self) -> str:
        return datetime.utcnow().strftime("%H:%M:%S")

    def add(self, area: LogArea, msg: str, *, level: LogLevel="info", data: Dict[str, Any] | None=None):
        self.entries.append(TurnLogEntry(t=self._nowt(), area=area, msg=msg, level=level, data=data or {}))

    # Convenience shorthands
    def objective(self, msg: str, **kw): self.add("objective", msg, **kw)
    def user(self, msg: str, **kw): self.add("user", msg, **kw)
    def attachments(self, msg: str, **kw): self.add("attachments", msg, **kw)
    def solver(self, msg: str, **kw): self.add("solver", msg, **kw)
    def answer(self, msg: str, **kw): self.add("answer", msg, **kw)
    def note(self, msg: str, **kw): self.add("note", msg, **kw)

    def prefs(self, prefs):
        try:
            compact_prefs = []
            for a in (prefs.get("assertions") or [])[:6]:
                compact_prefs.append(f"{a.get('key')}={a.get('value')} {'(avoid)' if not a.get('desired', True) else ''}")
            for e in (prefs.get("exceptions") or [])[:3]:
                compact_prefs.append(f"EXC[{e.get('rule_key')}]: {e.get('value')}")
            if compact_prefs:
                self.note("extracted prefs: " + "; ".join(compact_prefs))
        except Exception:
            pass

    def feedback(self, feedback: str):
        self.note("user feedback: " + feedback)

    def policy(self, policy):
        _tlog_policy = {
            "do": policy.get("do", {}),
            "avoid": policy.get("avoid", {}),
            "allow_if": policy.get("allow_if", {}),
            "reasons": (policy.get("reasons") or [])[:6]
        }
        self.note("policy: " + json.dumps(_tlog_policy, ensure_ascii=False))

    def turn_summary(self, turn_summary: dict, **kw):
        """Log turn summary to structured entries and create summary line."""
        order = [
            "user_inquiry", "objective", "complexity", "domain", "query_type",
            "prefs", "assumptions", "done", "not_done", "risks", "notes", "assistant_answer"
        ]
        turn_summary_entries = []

        def process_o(o):
            if o not in turn_summary or not turn_summary[o]:
                return

            if o == "objective" and not self.objective_entry:
                self.objective(turn_summary[o])
                turn_summary_entries.append(f"• objective: {turn_summary[o]} ")

            elif o == "complexity":
                complexity = turn_summary[o]
                if isinstance(complexity, dict):
                    level = complexity.get("level", "unknown")
                    factors = complexity.get("factors", [])
                    factors_str = ", ".join(factors) if factors else "none"
                    complexity_text = f"level={level}; factors=[{factors_str}]"
                    turn_summary_entries.append(f"• complexity: {complexity_text} ")

            elif o == "domain":
                domain = turn_summary[o]
                if isinstance(domain, str):
                    domain_text = domain
                elif isinstance(domain, list):
                    domain_text = ", ".join(str(d) for d in domain)
                else:
                    domain_text = str(domain)
                turn_summary_entries.append(f"• domain: {domain_text} ")

            elif o == "query_type":
                query_type = turn_summary[o]
                if isinstance(query_type, str):
                    qtype_text = query_type
                elif isinstance(query_type, list):
                    qtype_text = ", ".join(str(qt) for qt in query_type)
                else:
                    qtype_text = str(query_type)
                turn_summary_entries.append(f"• query_type: {qtype_text} ")

            elif o == "done":
                done = "; ".join(map(str, turn_summary[o][:6]))
                turn_summary_entries.append(f"• done: {done} ")

            elif o == "not_done":
                open_items = "; ".join(map(str, turn_summary[o][:6]))
                turn_summary_entries.append(f"• open: {open_items} ")

            elif o == "assumptions":
                assumptions = "; ".join(map(str, turn_summary[o][:6]))
                turn_summary_entries.append(f"• assumptions: {assumptions} ")

            elif o == "risks":
                risks = "; ".join(map(str, turn_summary[o][:6]))
                turn_summary_entries.append(f"• risks: {risks} ")

            elif o == "notes":
                notes = turn_summary[o]
                turn_summary_entries.append(f"• notes: {notes} ")

            elif o == "prefs":
                turn_prefs = turn_summary[o]
                try:
                    compact_prefs = []
                    for a in (turn_prefs.get("assertions") or [])[:6]:
                        compact_prefs.append(
                            f"{a.get('key')}={a.get('value')} {'(avoid)' if not a.get('desired', True) else ''}"
                        )
                    for e in (turn_prefs.get("exceptions") or [])[:3]:
                        compact_prefs.append(f"EXC[{e.get('rule_key')}]: {e.get('value')}")
                    if compact_prefs:
                        cp = "; ".join(compact_prefs)
                        turn_summary_entries.append(f"• prefs: {cp}")
                except Exception:
                    pass

            elif o == "user_inquiry":
                answer = turn_summary[o]
                turn_summary_entries.append(f"• user prompt summary: {answer} ")

            elif o == "assistant_answer":
                answer = turn_summary[o]
                # self.answer(answer)
                turn_summary_entries.append(f"• assistant answer summary: {answer} ")

        try:
            if isinstance(turn_summary, dict):
                for o in order:
                    process_o(o)
            if turn_summary_entries:
                self.add("summary", "".join(turn_summary_entries))
        except Exception:
            pass

    @property
    def user_entry(self):
        return next((d.model_dump_json() for d in self.entries if d.area == "user"), None)

    @property
    def objective_entry(self):
        return next((d.model_dump_json() for d in self.entries if d.area == "objective"), None)

    @property
    def memories(self) -> Dict[str, Any]:
        try:
            st = self.state or {}
            mem = st.get("memories")
            if isinstance(mem, dict):
                return mem
            # derive once from entries and cache
            import re
            out = {"topics": [], "suggestions": [], "notes": []}
            for e in (self.entries or []):
                area = (e.area if hasattr(e, "area") else (e.get("area") if isinstance(e, dict) else "")) or ""
                msg = (e.msg if hasattr(e, "msg") else (e.get("msg") if isinstance(e, dict) else "")) or ""
                if area not in {"note", "summary"}:
                    continue
                if area == "note" and isinstance(msg, str) and msg.startswith("topics:"):
                    payload = msg.split("topics:", 1)[1].strip()
                    toks = [t.strip() for t in re.split(r"[;,]", payload) if t.strip()]
                    out["topics"].extend(toks)
                    continue
                if area == "note" and isinstance(msg, str) and msg.startswith("suggestions:"):
                    payload = msg.split("suggestions:", 1)[1].strip()
                    sugs = [s.strip() for s in payload.split(";") if s.strip()]
                    out["suggestions"].extend(sugs)
                    continue
                if isinstance(msg, str) and msg:
                    out["notes"].append(msg)
            # cache
            self.state = self.state or {}
            self.state["memories"] = out
            return out
        except Exception:
            return {"topics": [], "suggestions": [], "notes": []}

    def to_markdown(self, header: str="[turn_log]") -> str:
        lines = [header]
        lines += [e.to_line() for e in self.entries]
        return "\n".join(lines)

    def to_payload(self) -> Dict[str, Any]:
        return json.loads(self.model_dump_json())


def new_turn_log(user_id: str, conversation_id: str, turn_id: str) -> TurnLog:
    return TurnLog(
        user_id=user_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        started_at_iso=datetime.utcnow().isoformat()+"Z"
    )


# ============================================================================
# CompressedTurn - Turn representation for context reconstruction
# ============================================================================

@dataclass
class CompressedTurn:
    """
    Compressed representation of a turn for building user/assistant message pairs.
    Built from structured sources: turn_log_entries + turn_summary.
    """
    # User side
    time_user: Optional[str] = None
    user_text: str = ""
    objective: Optional[str] = None
    user_inquiry_summary: Optional[str] = None

    # Metadata
    complexity: Optional[Dict[str, Any]] = None
    domain: Optional[str] = None
    query_type: Optional[str] = None
    topics: Optional[str] = None

    # Preferences
    prefs: Optional[Dict[str, Any]] = None

    # Assistant side
    time_assistant: Optional[str] = None
    assistant_answer_summary: Optional[str] = None
    solver_mode: Optional[str] = None
    solver_status: Optional[str] = None
    tools_used: Optional[str] = None
    done: List[str] = field(default_factory=list)
    not_done: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    notes: Optional[str] = None

    # Additional
    suggestions: List[str] = field(default_factory=list)
    insights: Optional[str] = None  # From fingerprint one-liner

    @staticmethod
    def from_structured(
        turn_log_entries: List[Dict[str, Any]],
        turn_summary: Dict[str, Any]
    ) -> 'CompressedTurn':
        """
        Build CompressedTurn from structured turn log entries and turn summary.
        This is the main constructor for new code.

        Args:
            turn_log_entries: List of TurnLogEntry dicts with fields: t, area, msg, level, data
            turn_summary: Turn summary dict with fields: objective, done, not_done, assumptions,
                         risks, notes, user_inquiry, assistant_answer, prefs, complexity, domain, query_type

        Returns:
            CompressedTurn instance
        """
        turn = CompressedTurn()

        # === USER SIDE ===

        # User message with timestamp
        user_entry = next((e for e in turn_log_entries if e.get("area") == "user"), None)
        if user_entry:
            turn.time_user = user_entry.get("t", "")
            turn.user_text = user_entry.get("msg", "").strip()

        # Objective from turn_summary
        turn.objective = turn_summary.get("objective", "")

        # User inquiry summary from turn_summary
        turn.user_inquiry_summary = turn_summary.get("user_inquiry", "")

        # Topics from turn_log_entries
        topics_entry = next(
            (e for e in turn_log_entries
             if e.get("area") == "note" and e.get("msg", "").startswith("topics:")),
            None
        )
        if topics_entry:
            turn.topics = topics_entry.get("msg", "").replace("topics:", "").strip()

        # Metadata from turn_summary
        turn.complexity = turn_summary.get("complexity")
        turn.domain = turn_summary.get("domain", "")
        turn.query_type = turn_summary.get("query_type", "")

        # Preferences from turn_summary
        turn.prefs = turn_summary.get("prefs")

        # === ASSISTANT SIDE ===

        # Timestamp from answer/summary entry
        answer_entry = next(
            (e for e in turn_log_entries if e.get("area") in ("answer", "summary")),
            None
        )
        if answer_entry:
            turn.time_assistant = answer_entry.get("t", "")

        # Assistant answer summary from turn_summary
        turn.assistant_answer_summary = turn_summary.get("assistant_answer", "")

        # Solver execution details from turn_log_entries
        solver_result_entry = next(
            (e for e in turn_log_entries
             if e.get("area") == "solver" and "[solver]" in e.get("msg", "") and "mode=" in e.get("msg", "")),
            None
        )
        if solver_result_entry:
            msg = solver_result_entry.get("msg", "")
            mode_match = re.search(r'mode=(\w+)', msg)
            status_match = re.search(r'status=(\w+)', msg)
            if mode_match:
                turn.solver_mode = mode_match.group(1)
            if status_match:
                turn.solver_status = status_match.group(1)

        # Tools used from turn_log_entries
        tools_entry = next(
            (e for e in turn_log_entries
             if e.get("area") == "solver" and "[tools.calls]" in e.get("msg", "")),
            None
        )
        if tools_entry:
            turn.tools_used = tools_entry.get("msg", "").replace("[tools.calls]:", "").strip()

        # Done/not_done/assumptions/risks/notes from turn_summary
        turn.done = turn_summary.get("done", [])
        turn.not_done = turn_summary.get("not_done", [])
        turn.assumptions = turn_summary.get("assumptions", [])
        turn.risks = turn_summary.get("risks", [])
        turn.notes = turn_summary.get("notes", "")

        # Suggestions from turn_log_entries
        suggestions_entry = next(
            (e for e in turn_log_entries
             if e.get("area") == "note" and e.get("msg", "").startswith("suggestions:")),
            None
        )
        if suggestions_entry:
            suggestions_text = suggestions_entry.get("msg", "").replace("suggestions:", "").strip()
            turn.suggestions = [s.strip() for s in suggestions_text.split(";") if s.strip()]

        return turn


def turn_to_user_message(turn: CompressedTurn) -> str:
    """
    Format the USER side of a turn for LLM context.

    Includes:
    - User's actual message with timestamp
    - Context section (objective, topics, prefs, user_inquiry summary, metadata)

    Args:
        turn: CompressedTurn instance

    Returns:
        Formatted string for user message
    """
    parts = []

    # 1. User message with timestamp
    if turn.time_user:
        parts.append(f"[{turn.time_user}]")
    if turn.user_text:
        parts.append(turn.user_text)

    # 2. Context section (not authored by user)
    context_lines = []

    # Objective
    if turn.objective:
        context_lines.append(f"• Inferred objective: {turn.objective}")

    # Topics
    if turn.topics:
        context_lines.append(f"• Topics: {turn.topics}")

    # User inquiry summary
    if turn.user_inquiry_summary:
        context_lines.append(f"• User request summary: {turn.user_inquiry_summary}")

    # Preferences (compact)
    if turn.prefs:
        assertions = turn.prefs.get("assertions", [])
        if assertions:
            pref_strs = []
            for a in assertions[:3]:  # Top 3
                key = a.get("key", "")
                value = a.get("value", "")
                desired = a.get("desired", True)
                if key:
                    marker = "" if desired else " (avoid)"
                    pref_strs.append(f"{key}={value}{marker}")
            if pref_strs:
                context_lines.append(f"• Preferences: {'; '.join(pref_strs)}")

    # Complexity
    if turn.complexity and isinstance(turn.complexity, dict):
        level = turn.complexity.get("level", "")
        factors = turn.complexity.get("factors", [])
        if level:
            factors_str = f"; factors: {', '.join(factors)}" if factors else ""
            context_lines.append(f"• Complexity: {level}{factors_str}")

    # Domain
    if turn.domain:
        context_lines.append(f"• Domain: {turn.domain}")

    # Query type
    if turn.query_type:
        context_lines.append(f"• Query type: {turn.query_type}")

    # Insights (if available)
    if turn.insights:
        context_lines.append(f"• Turn insights: {turn.insights}")

    # Assemble context block
    if context_lines:
        parts.append("\nContext — not authored by user")
        parts.extend(context_lines)

    return "\n".join(parts)


def turn_to_assistant_message(turn: CompressedTurn) -> str:
    """
    Format the ASSISTANT side of a turn for LLM context.

    Includes:
    - Assistant answer summary with timestamp
    - Solver execution details (if solver ran)
    - Assumptions and risks

    Args:
        turn: CompressedTurn instance

    Returns:
        Formatted string for assistant message
    """
    parts = []

    # 1. Timestamp
    if turn.time_assistant:
        parts.append(f"[{turn.time_assistant}]")

    # 2. Assistant answer summary
    if turn.assistant_answer_summary:
        parts.append(f"Assistant response summary: {turn.assistant_answer_summary}")

    # 3. Solver execution (if solver ran)
    if turn.solver_mode or turn.solver_status or turn.tools_used or turn.done or turn.not_done:
        solver_lines = ["\nSolver execution"]

        # Mode and status
        if turn.solver_mode and turn.solver_status:
            solver_lines.append(f"• Mode: {turn.solver_mode}; Status: {turn.solver_status}")

        # Tools used
        if turn.tools_used:
            solver_lines.append(f"• Tools used: {turn.tools_used}")

        # Done items
        if turn.done:
            solver_lines.append(f"• Completed: {', '.join(turn.done[:5])}")

        # Not done / open items
        if turn.not_done:
            solver_lines.append(f"• Open: {', '.join(turn.not_done[:5])}")

        # Notes
        if turn.notes:
            solver_lines.append(f"• Notes: {turn.notes}")

        parts.extend(solver_lines)

    # 4. Assumptions (if any)
    if turn.assumptions:
        parts.append("\nAssumptions")
        for assumption in turn.assumptions[:3]:  # Top 3
            parts.append(f"• {assumption}")

    # 5. Risks (if any)
    if turn.risks:
        parts.append("\nRisks")
        for risk in turn.risks[:3]:  # Top 3
            parts.append(f"• {risk}")

    return "\n".join(parts)


def turn_to_pair(turn: CompressedTurn) -> Dict[str, str]:
    """
    Convert CompressedTurn to user/assistant message pair.

    This is the main entry point for creating LLM context from turn data.

    Args:
        turn: CompressedTurn instance

    Returns:
        {"user": str, "assistant": str}
    """
    return {
        "user": turn_to_user_message(turn),
        "assistant": turn_to_assistant_message(turn)
    }

def compressed_turn_from_tlog(tlog: Dict[str, Any]) -> Dict[str, Any]:
    sv = tlog.get("solver", {})
    meta = sv.get("meta", {})
    brief = sv.get("brief", {})
    inputs = (brief.get("inputs") or {})
    return {
        "turn_id": tlog.get("turn_id"),
        "ok": sv.get("ok"),
        "mode": (sv.get("plan") or {}).get("mode"),
        "objective": inputs.get("objective", ""),
        "topics": inputs.get("topics", []),
        "deliverables": sv.get("deliverables", {}),
        "citations": sv.get("citations", {}),
        "program_presentation": sv.get("program_presentation", ""),
        "failure": sv.get("failure"),
        "failure_presentation": sv.get("failure_presentation"),
        "meta": meta,
    }

if __name__ == "__main__":

    # Example usage
    sample_entries = [
        {"t": "12:11:28", "area": "user", "msg": "We use redis, postgres, dynamo, s3 in our stack. We run in docker on ec2.\n\nCould you search for recent issues in thise stack", "level": "info", "data": {}},
        {"t": "12:11:39", "area": "objective", "msg": "Search for recent security issues and CVEs across Redis, PostgreSQL, DynamoDB, S3, Docker, and EC2 stack.", "level": "info", "data": {}},
        {"t": "12:11:39", "area": "note", "msg": "topics: infrastructure security, vulnerability assessment, stack security", "level": "info", "data": {}},
        {"t": "12:15:08", "area": "solver", "msg": "[solver] mode=codegen; status=ok; complete=['issues_summary_md']; drafts=[]; missing=[]", "level": "info", "data": {}},
        {"t": "12:15:08", "area": "solver", "msg": "[tools.calls]: program;generic_tools.web_search;llm_tools.generate_content_llm;", "level": "info", "data": {}},
        {"t": "12:15:54", "area": "summary", "msg": "• user prompt summary: User disclosed tech stack • done: issues_summary_md • assistant answer summary: Consolidated markdown report covering CVEs", "level": "info", "data": {}},
        {"t": "12:15:54", "area": "note", "msg": "suggestions: Draft a 48-hour patch prioritization plan for these CVEs.;Generate a compliance impact memo for the Redis RCE vulnerability.", "level": "info", "data": {}},
    ]

    sample_summary = {
        "objective": "Search for recent security issues and CVEs across Redis, PostgreSQL, DynamoDB, S3, Docker, and EC2 stack",
        "done": ["issues_summary_md"],
        "not_done": [],
        "assumptions": ["User needs actionable intelligence for patching/mitigation planning"],
        "risks": [],
        "notes": "Solver executed web search across 12 targeted queries",
        "user_inquiry": "User disclosed tech stack (Redis, PostgreSQL, DynamoDB, S3, Docker on EC2) and requested search for recent issues affecting these components",
        "assistant_answer": "Consolidated markdown report covering: Critical CVEs (CVE-2025-49844 Redis RCE 10.0 CVSS, PostgreSQL CVE-2024-10979/10980, Docker CVE-2024-41110)",
        "prefs": {"assertions": [{"key": "needs_stack_security_intel", "value": True, "desired": True}], "exceptions": []},
        "complexity": {"level": "complex", "factors": ["multi_agent", "codegen", "tool_usage_3"]},
        "domain": "infrastructure, security",
        "query_type": "analytical, procedural"
    }

    # Create CompressedTurn from structured data
    turn = CompressedTurn.from_structured(sample_entries, sample_summary)

    # Convert to message pair
    pair = turn_to_pair(turn)

    print("USER MESSAGE:")
    print(pair["user"])
    print("\n" + "="*80 + "\n")
    print("ASSISTANT MESSAGE:")
    print(pair["assistant"])

    sample = """
[turn_log]
12:11:28 [user] We use redis, postgres, dynamo, s3 in our stack. We run in docker on ec2. 

Could you search for recent issues in thise stack
12:11:39 [objective] Search for recent security issues and CVEs across Redis, PostgreSQL, DynamoDB, S3, Docker, and EC2 stack.
12:11:39 [note] topics: infrastructure security, vulnerability assessment, stack security
12:11:39 [note] policy: {"do": {}, "avoid": {}, "allow_if": {}, "reasons": []}
12:11:39 [note] conversation route now: tools_general
12:11:47 [note] [ctx.used]: short digest of past turns
turn_1760961994162_6wiqo7: • user prompt summary: User clarified priority dimensions (cyber threats, supply chain disruption) and requested all three diagram types (timeline, risk matrix, scenario tree) for Ukraine-Russia war analysis • prefs: prefers_comprehensive_visuals=True ; security_focus_cyber_supply_chain=True • assumptions: User needs visual analysis tools for executive security budget presentations; Focus on 2025-2027 timeframe aligns with typical enterprise planning cycles; Diagrams should highlight actionable risk categories rather than granular technical details • done: cyber_threat_timeline_2025_2027; supply_chain_risk_matrix; war_escalation_scenario_tree • notes: Solver generated three Mermaid diagrams from current threat intelligence and rendered to PNG. All requested deliverables complete. • assistant answer summary: Three security planning diagrams delivered: (1) Cyber threat timeline 2025-2027 showing escalation phases from persistent APT campaigns to potential critical infrastructure attacks, (2) Supply chain risk matrix plotting likelihood vs. impact for semiconductor shortages, energy disruptions, logistics bottlenecks, (3) War escalation scenario tree mapping four pathways (frozen conflict, negotiated settlement, Russian tactical nuclear use, NATO involvement) with security budget implications 
turn_1760961874427_zhkwry: • user prompt summary: User needs 2-year Ukraine-Russia war analysis for security budget planning; requests research of prognoses and diagram creation • prefs: needs_geopolitical_analysis=True ; prefers_visual_outputs=True • open: research_prognoses; create_diagram; risk_analysis • risks: User may need all security dimensions covered; Diagram format choice affects analysis depth • notes: Clarification-only turn per instructions • assistant answer summary: Asked two clarification questions: (1) priority security dimensions (cyber/supply chain/personnel/geopolitical), (2) preferred diagram format (timeline/risk matrix/scenario tree) 
turn_1760906421585_yzv0w2: • user prompt summary: User provided turn log JSON schema and requested Python code to convert schema objects to beautiful markdown format • prefs: prefers_beautiful_output=True • assumptions: User wants a standalone function that handles all schema fields; Markdown output should use headers, bullets, and tables for readability; Empty lists should be handled gracefully (show placeholder or skip); Function should include docstring with usage example • done: python_code • notes: Solver generated complete Python function via LLM with formatting instructions for all schema fields including nested prefs structure • assistant answer summary: Complete Python function schema_to_markdown(obj: dict) -> str with docstring, handling all fields (objective, done, not_done, assumptions, risks, notes, user_inquiry, assistant_answer, prefs with assertions/exceptions tables), graceful empty-list handling 
12:12:17 [note] [solver.tool_router]: Notes: Two-tool closed plan: search recent stack issues → synthesize into cited markdown summary. No file generation; inline deliverable.. Selected tools=[{'id': 'generic_tools.web_search', 'purpose': 'Search the web using multiple synonymous/rephrase queries; returns a JSON list of {sid, title, url, body}. Results are interleaved across queries and deduplicated by URL. The total number of results is capped by `n`.', 'reason': 'Search for recent CVEs, breaking changes, and operational issues across Redis, PostgreSQL, DynamoDB, S3, Docker, and EC2 (2024–2025). Multiple query variants to capture security vulnerabilities and known issues.', 'params_schema': {'queries': 'string, JSON array of rephrases/synonyms, e.g. ["israeli ncd supply chain directive", "national cyber directorate vendor risk guidance", "israel municipality procurement cybersecurity policy"]. You may also pass a single string; it will be treated as one query.', 'n': 'integer, Maximum total results across all queries (1–20). (default=8)'}, 'suggested_parameters': {'queries': '["Redis CVE 2024 2025 security vulnerability", "PostgreSQL breaking changes deprecation 2024 2025", "DynamoDB S3 AWS issues 2024 2025", "Docker EC2 security issues 2024 2025", "Redis PostgreSQL DynamoDB S3 Docker EC2 stack vulnerabilities"]', 'n': 15}, 'confidence': 0.95}, {'id': 'llm_tools.generate_content_llm', 'purpose': 'Generate HTML/Markdown/JSON/YAML (or plain text) with multi-round continuation and format/schema validation. Citations are OPTIONAL and applied only when cite_sources=true; sidecar citations are supported for JSON/YAML. Returns a JSON envelope with status and final content.The result always includes used_sources (list of {sid,url,title,text}), even when the artifact has no inline tokens. Downstream code must persist this list into the target slot as sources_used.', 'reason': 'Synthesize search results into structured markdown summary with sections: Critical CVEs (with IDs, CVSS, affected versions), Deprecations/Breaking Changes, Performance Issues. Include mitigation links and prioritize 2024–2025 findings.', 'params_schema': {'agent_name': 'string, Name of this content creator, short, to distinguish this author in the sequence of generative calls.', 'instruction': 'string, What to produce (goal/contract).', 'input_context': 'string, Optional base text or data to use. (default=)', 'target_format': 'string, html|markdown|json|yaml|text (default=markdown)', 'schema_json': 'string, Optional JSON Schema (for json/yaml). (default=)', 'sources_json': 'string, JSON array of sources: {sid:int, title:str, url?:str, text:str}. (default=[])', 'cite_sources': 'boolean, If true and sources provided, require citations (inline for Markdown/HTML; sidecar for JSON/YAML). (default=False)', 'citation_embed': 'string, auto|inline|sidecar|none (default=auto)', 'citation_container_path': 'string, JSON Pointer for sidecar path (json/yaml). (default=/_citations)', 'allow_inline_citations_in_strings': 'boolean, Permit [[S:n]] tokens inside JSON/YAML string fields. (default=False)', 'max_tokens': 'integer, Per-round token cap. (default=7000)', 'max_rounds': 'integer, Max generation/repair rounds. (default=4)', 'code_fences': 'boolean, Allow triple-backtick fenced blocks in output. (default=True)', 'continuation_hint': 'string, Optional extra hint used on continuation rounds. (default=)', 'strict': 'boolean, Require format OK and (if provided) schema OK and citations (if requested). (default=True)'}, 'suggested_parameters': {'agent_name': 'stack_issues_summarizer', 'instruction': 'Synthesize recent issues (2024–2025) across Redis, PostgreSQL, DynamoDB, S3, Docker, EC2 into a structured markdown summary. Sections: (1) Critical CVEs (list CVE ID, component, CVSS score, affected versions, mitigation link), (2) Deprecations & Breaking Changes (component, change, impact, migration path), (3) Performance/Operational Issues (component, issue, workaround). Use bullet format; ≤25 words per bullet. Prioritize security and breaking changes. Include inline citations [[S:n]] for each finding.', 'target_format': 'markdown', 'cite_sources': True, 'max_tokens': 2000, 'max_rounds': 2, 'code_fences': False, 'strict': True}, 'confidence': 0.92}].
12:12:25 [solver] [solvability] decision: solving mode=codegen, confidence=0.94, solvability_reasoning=Web search + LLM synthesis available; closed plan: search recent stack issues → consolidate into structured markdown summary., tools=['generic_tools.web_search', 'llm_tools.generate_content_llm'], When solved, these slots must be filled: contract_dyn={'issues_summary_md': {'type': 'inline', 'description': 'Consolidated markdown summary of recent issues (2024–2025) across Redis, PostgreSQL, DynamoDB, S3, Docker, EC2. Sections: Critical CVEs (CVE ID, CVSS score, affected versions, mitigation links), Deprecations & Breaking Changes (component, impact, migration path), Performance/Operational Issues (component, workaround). Bullet format, ≤25 words per item; inline citations [[S:n]].', 'format': 'markdown'}}. If the slots are not filled, the user request is not solved.instructions_for_downstream=Search for 2024–2025 CVEs, breaking changes, and operational issues across Redis, PostgreSQL, DynamoDB, S3, Docker, EC2. Synthesize results into structured markdown: Critical CVEs (ID, CVSS, versions, mitigation), Deprecations/Breaking Changes, Performance Issues. Use bullets ≤25 words; include inline citations., 
12:15:08 [solver] [solver] mode=codegen; status=ok; complete=['issues_summary_md']; drafts=[]; missing=[]; result_interpretation_instruction=The issues_summary_md slot contains a consolidated markdown report of recent (2024-2025) security vulnerabilities, breaking changes, and operational issues across Redis, PostgreSQL, DynamoDB, S3, Docker, and EC2. Review Critical CVEs section first for immediate security concerns. Citations link to source materials for verification.
12:15:08 [solver] [tools.calls]: program;generic_tools.web_search;llm_tools.generate_content_llm;
12:15:54 [summary] • user prompt summary: User disclosed tech stack (Redis, PostgreSQL, DynamoDB, S3, Docker on EC2) and requested search for recent issues affecting these components • prefs: needs_stack_security_intel=True • assumptions: User needs actionable intelligence for patching/mitigation planning; Focus on 2024-2025 timeframe aligns with 'recent' request; Critical CVEs take priority over minor operational issues • done: issues_summary_md • notes: Solver executed web search across 12 targeted queries and synthesized findings into structured markdown with inline citations • assistant answer summary: Consolidated markdown report covering: Critical CVEs (CVE-2025-49844 Redis RCE 10.0 CVSS, PostgreSQL CVE-2024-10979/10980, Docker CVE-2024-41110), breaking changes (PostgreSQL 17 deprecations, Redis 8.0 command changes), operational issues (S3 event notification delays, EC2 metadata service timeouts). Each finding includes severity, affected versions, mitigation links, and inline citations. 
12:15:54 [note] suggestions: Draft a 48-hour patch prioritization plan for these CVEs.;Generate a compliance impact memo for the Redis RCE vulnerability.;Summarize Docker overlay2 performance workarounds for our EC2 fleet.
"""

    print()
    # print(pair)