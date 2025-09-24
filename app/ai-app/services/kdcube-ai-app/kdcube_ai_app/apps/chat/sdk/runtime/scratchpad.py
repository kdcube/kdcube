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

        self.turn_artifact = None
        self.context_stack = []
        self.turn_stack = []

        # exact-reference
        self.relevant_turn_ids: List[str] = []

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
        self.short_artifacts.append({"kind": kind, "title": title, "content": content, **{"structured_content": structured_content if structured_content else {}}})

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
        if self.level != "info": base += f"  !{self.level}"
        return base

LINE_RE = re.compile(r'^(?P<time>\d{2}:\d{2}:\d{2})\s+\[(?P<tag>[^\]]+)\]\s*(?P<content>.*)$')

# ---- Data model for a compressed turn ----
@dataclass
class CompressedTurn:
    time_user: Optional[str] = None
    user_text: str = ""
    objective: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    solver: Dict[str, str] = field(default_factory=dict)  # e.g., {"solvability": "...", "done": "...", ...}
    summary: Optional[str] = None
    answer: Optional[str] = None
    suggestions: List[str] = field(default_factory=list)
    raw: List[str] = field(default_factory=list)  # optional provenance
    insights: Optional[str] = None  # optional consolidated insights

    # ---- NEW: backing storage for lazy extractions ----
    _source_text: Optional[str] = field(default=None, repr=False)
    _ctx_used_bullets_all_cache: Optional[List[str]] = field(default=None, repr=False)
    _summary_match_cache: Optional[tuple] = field(default=None, repr=False)  # (time:str|None, content:str)

    # ---- NEW: lazy properties ----
    @property
    def ctx_used_bullets(self) -> str:
        """
        First [ctx.used] block as a single bullet string (each physical line → '- ...').
        Returns '' if not present.
        """
        all_blocks = self.ctx_used_bullets_all
        return all_blocks[0] if all_blocks else ""

    @property
    def ctx_used_bullets_all(self) -> List[str]:
        """
        All [ctx.used] blocks, each returned as a single bullet string.
        """
        if self._ctx_used_bullets_all_cache is not None:
            return self._ctx_used_bullets_all_cache

        text = self._source_text or ""
        lines = text.splitlines()
        results: List[str] = []
        in_ctx = False
        current: List[str] = []

        def flush():
            nonlocal current
            if current:
                results.append("\n".join(f"- {b}" for b in current))
                current = []

        for raw in lines:
            m = LINE_RE.match(raw)
            if m:
                tag = m.group("tag").strip().lower()
                content = m.group("content").strip().lower()
                if in_ctx:
                    # next tagged line ends the current ctx.used block
                    flush()
                    in_ctx = False
                # a ctx.used block starts on a [note] line that mentions [ctx.used]
                if tag.startswith("note") and "ctx.used" in content:
                    in_ctx = True
                continue

            if in_ctx:
                clean = raw.strip()
                if not clean:
                    continue
                # strip any pre-existing bullet symbols
                clean = re.sub(r'^[\s\u2022•\-\*]+', "", clean)
                if clean:
                    current.append(clean)

        if in_ctx:
            flush()

        self._ctx_used_bullets_all_cache = results
        return results

    @property
    def summary_time(self) -> Optional[str]:
        """
        Returns the HH:MM:SS of the [summary] line, or None.
        """
        t, _ = self._lazy_summary_match()
        return t

    @property
    def summary_content(self) -> str:
        """
        Returns the content that comes after [summary] on that line ('' if absent).
        """
        _, content = self._lazy_summary_match()
        return content

    # ---- helpers for lazy extraction ----
    def _lazy_summary_match(self) -> Optional[tuple]:
        if self._summary_match_cache is not None:
            return self._summary_match_cache
        time_part: Optional[str] = None
        content_part = ""
        text = self._source_text or ""
        for raw in text.splitlines():
            m = LINE_RE.match(raw.strip())
            if not m:
                continue
            tag = m.group("tag").strip().lower()
            if tag == "summary":
                time_part = m.group("time")
                content_part = (m.group("content") or "").strip()
                break
        self._summary_match_cache = (time_part, content_part)
        return self._summary_match_cache


def _push_note(target: List[str], content: str):
    content = content.strip()
    if content:
        target.append(content)

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

    # convenience shorthands
    def objective(self, msg: str, **kw): self.add("objective", msg, **kw)
    def user(self, msg: str, **kw): self.add("user", msg, **kw)
    def attachments(self, msg: str, **kw): self.add("attachments", msg, **kw)
    def solver(self, msg: str, **kw): self.add("solver", msg, **kw)
    def answer(self, msg: str, **kw): self.add("answer", msg, **kw)
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
        order = ["objective", "prefs", "assumptions", "done", "not_done", "risks", "notes"]
        turn_summary_entries = []
        def process_o(o):
            if o in turn_summary and turn_summary[o]:
                if o == "objective" and not self.objective_entry:
                    self.objective(turn_summary[o])
                    turn_summary_entries.append(f"• objective: {turn_summary[o]} ")
                elif o == "done":
                    done = "; ".join(map(str, turn_summary[o][:6]))
                    self.solver("done: " + done)
                    turn_summary_entries.append(f"• done: {done} ")
                elif o == "not_done":
                    open = "; ".join(map(str, turn_summary[o][:6]))
                    self.solver("open: " + open)
                    turn_summary_entries.append(f"• open: {open} ")
                elif o == "assumptions":
                    assumptions = "; ".join(map(str, turn_summary[o][:6]))
                    self.note("assumptions: " + assumptions)
                    turn_summary_entries.append(f"• assumptions: {assumptions} ")
                elif o == "risks":
                    risks = "; ".join(map(str, turn_summary[o][:6]))
                    self.note("risks: " + "; ".join(map(str, turn_summary[o][:6])))
                    turn_summary_entries.append(f"• risks: {risks} ")
                elif o == "notes":
                    notes = turn_summary[o]
                    self.answer(notes)
                    turn_summary_entries.append(f"• notes: {notes} ")
                elif o == "prefs":
                    turn_prefs = turn_summary[o]
                    try:
                        compact_prefs = []
                        for a in (turn_prefs.get("assertions") or [])[:6]:
                            compact_prefs.append(f"{a.get('key')}={a.get('value')} {'(avoid)' if not a.get('desired', True) else ''}")
                        for e in (turn_prefs.get("exceptions") or [])[:3]:
                            compact_prefs.append(f"EXC[{e.get('rule_key')}]: {e.get('value')}")
                        if compact_prefs:
                            cp =  "; ".join(compact_prefs)
                            self.note("prefs: " + cp)
                            turn_summary_entries.append(f"• prefs: {cp}")
                    except Exception:
                        pass
        try:
            if isinstance(turn_summary, dict):
                for o in order:
                    process_o(o)
            if turn_summary_entries:
                self.add("summary", "".join(turn_summary_entries))
        except Exception:
            pass

    def note(self, msg: str, **kw): self.add("note", msg, **kw)

    @property
    def user_entry(self):
        return next((d.model_dump_json() for d in self.entries if d.area == "user"), None)

    @property
    def objective_entry(self):
        return next((d.model_dump_json() for d in self.entries if d.area == "objective"), None)

    def to_markdown(self, header: str="[turn_log]") -> str:
        lines = [header]
        lines += [e.to_line() for e in self.entries]
        return "\n".join(lines)

    def to_payload(self) -> Dict[str, Any]:
        return json.loads(self.model_dump_json())

    @staticmethod
    def to_compressed_turn_obj(text: str) -> CompressedTurn:
        """
        Parse a single [turn_log] block into a list of CompressedTurn, each beginning with a [user] line.
        """
        lines = []
        in_block = False
        for raw in text.splitlines():
            if raw.strip() == "[turn_log]":
                in_block = True
                continue
            if not in_block:
                continue
            m = LINE_RE.match(raw.strip())
            if m:
                lines.append((m.group("time"), m.group("tag").strip(), m.group("content").rstrip()))
            # else ignore non-matching lines (robust to bullets/continuations)

        turn = None

        # Walk through tagged lines, split into turns on [user]
        for i, (t, tag, content) in enumerate(lines):
            if tag == "user":
                # Start new turn
                turn = CompressedTurn(time_user=t, user_text=content, raw=[f"{t} [user] {content}"])
                continue
            if turn is None:
                # If content appears before any [user], put it in a synthetic first turn
                turn = CompressedTurn()

            turn.raw.append(f"{t} [{tag}] {content}")

            low = tag.lower()
            if low == "objective":
                turn.objective = content.strip()
            elif low.startswith("note"):
                _push_note(turn.notes, content)
            elif low.startswith("solver"):
                # Crude routing: put main key → value by searching known keywords in content
                # You can enrich this by recognizing 'solvability', 'done', 'open', 'risks' etc.
                if "solvability" in content:
                    turn.solver["solvability"] = content
                elif "done:" in content:
                    turn.solver["done"] = content
                elif "open:" in content:
                    turn.solver["open"] = content
                elif "risks" in content:
                    turn.solver["risks"] = content
                else:
                    # Keep as generic solver-notes
                    blob = turn.solver.get("notes", "")
                    turn.solver["notes"] = (blob + ("\n" if blob else "") + content).strip()
            elif low == "summary":
                turn.summary = content.strip()
            elif low == "answer":
                turn.answer = content.strip()
            elif low == "suggestions":
                # split by semicolons if present
                parts = [p.strip() for p in content.split(";") if p.strip()]
                turn.suggestions.extend(parts)
        turn._source_text = text
        return turn


def new_turn_log(user_id: str, conversation_id: str, turn_id: str) -> TurnLog:
    return TurnLog(user_id=user_id, conversation_id=conversation_id, turn_id=turn_id, started_at_iso=datetime.utcnow().isoformat()+"Z")

def _turn_id_from_tags_safe(tags: List[str]) -> Optional[str]:
    for t in tags or []:
        if isinstance(t, str) and t.startswith("turn:"):
            return t.split(":",1)[1]
    return None

def _render_user_payload(turn: CompressedTurn, *, include_objective=True, include_insights=True) -> str:
    """
    User side: the human's text + an ultra-compact non-authored block
    with only objective and insights (memories). Nothing else.
    """
    prefix = f"[{turn.time_user}]\n" if turn.time_user else ""
    bits = []
    if include_objective and turn.objective:
        bits.append(f"Objective: {turn.objective}")
    if include_insights and turn.insights:
        bits.append(f"Memories: {turn.insights}")
    ctx = ""
    if bits:
        ctx = "\n\nContext — not authored by the user\n" + "\n".join(bits)
    return (prefix + (turn.user_text or "").strip() + ctx).strip()


def _render_assistant_from_log(
        turn: CompressedTurn,
        *,
        max_solver_lines: int = 3,
        force_short: bool = True,
        max_sentences: int = 2
) -> str:
    """
    Assistant side: prefer [answer], else [summary]; add up to a few solver bullets.
    Optionally force to N sentences for brevity.
    """
    base = (turn.answer or "").strip()
    if not base:
        base = (turn.summary or "").strip() or "(no direct answer recorded for this turn)"

    # Distill solver to a few compact bullets
    solver_lines = []
    for key in ("done", "open", "risks"):
        v = (turn.solver.get(key) or "").strip()
        if v:
            # strip leading "key:" if present
            v = re.sub(rf"^{key}\s*:\s*", "", v, flags=re.I)
            solver_lines.append(f"- {key}: {v}")

    if solver_lines:
        solver_lines = solver_lines[:max_solver_lines]
        base = base + "\n\n" + "\n".join(solver_lines)

    if force_short:
        base = _limit_to_n_sentences(base, n=max_sentences)

    return base


_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+')

def _limit_to_n_sentences(text: str, n: int) -> str:
    parts = _SENT_SPLIT.split(text.strip())
    if len(parts) <= n:
        return text.strip()
    return " ".join(parts[:n]).strip()

def turn_to_pair(turn: CompressedTurn) -> dict:
    """
    Build the canonical pair with the corrected personas:
      - user: user utterance + (only) objective/insights
      - assistant: answer/summary + compact solver
    """
    user_payload = _render_user_payload(turn)
    assistant_payload = _render_assistant_from_log(turn)
    return {"user": user_payload, "assistant": assistant_payload}


if __name__ == "__main__":
    sample = """[turn_log]
19:00:19 [user] What’s our current EDR coverage on Macs vs. Windows?
19:00:31 [objective] Analyze endpoint protection coverage status
19:00:31 [note] topics: endpoint_protection, security, infrastructure
19:00:31 [note] policy: {"do": {}, "avoid": {}, "allow_if": {}, "reasons": []}
19:00:31 [note] conversation route now: tools_security
19:00:44 [note] [ctx.used]
• assumptions: User is sharing current endpoint protection status; Falcon refers to CrowdStrike Falcon EDR/endpoint protection • notes: User provided endpoint counts - 154 Macs, 342 Windows protected by Falcon
• objective: acknowledge endpoint inventory information • notes: User provided endpoint count - 210 Macs, 380 Windows. No specific action requested, appears to be informational.
19:00:56 [note] [solver.tool_router]: Notes: Calculate coverage rates and provide structured analysis of EDR gaps. Selected tools=[{'id': 'generic_tools.calc', 'purpose': "Evaluate a safe math expression, e.g., '41*73+5' or 'sin(pi/4)**2'.", 'reason': 'Calculate exact coverage percentages for Mac (154/210) and Windows (342/380) endpoints', 'params_schema': {'expression': 'string, A Python math expression using allowed functions/constants.'}, 'suggested_parameters': {'expression': '154/210'}, 'confidence': 0.9}, {'id': 'llm_tools.summarize_llm', 'purpose': "Summarize either free text (input_mode='text') or a list of sources (input_mode='sources'). In sources mode, may add inline citation tokens [[S:<sid>]] to mark provenance.", 'reason': 'Structure the coverage analysis with insights about gaps and recommendations', 'params_schema': {'input_mode': 'string, text|sources (default=text)', 'text': "string, When input_mode='text': the text to summarize (≤10k chars). (default=)", 'sources_json': "string, When input_mode='sources': JSON array of {sid,int; title,str; url,str; text,str}. (default=[])", 'style': 'string, brief|bullets|one_line (default=brief)', 'cite_sources': 'boolean, In sources mode: insert [[S:<sid>]] tokens after claims. (default=False)', 'max_tokens': 'integer, LLM output cap. (default=300)'}, 'suggested_parameters': {'input_mode': 'text', 'text': '<TBD at runtime>', 'style': 'brief', 'max_tokens': 400}, 'confidence': 0.8}].
19:01:12 [solver] [solvability] decision: solving mode=llm_only, confidence=0.9, solvability_reasoning=No tools available to query endpoint protection systems or security infrastructure for current EDR coverage data, instructions_for_downstream=Cannot access security infrastructure data. Explain limitation and suggest manual data gathering from EDR console or endpoint management systems.,
19:01:30 [note] assumptions: Falcon data from yesterday is current; total endpoint counts are accurate
19:01:30 [solver] done: noted endpoint inventory; noted current Falcon protection status
19:01:30 [solver] open: access live EDR system data
19:01:30 [note] risks: coverage gaps may have changed since yesterday
19:01:30 [answer] User has provided both total endpoints and protected counts - can calculate coverage manually
19:01:30 [summary] • assumptions: Falcon data from yesterday is current; total endpoint counts are accurate • done: noted endpoint inventory; noted current Falcon protection status • open: access live EDR system data • risks: coverage gaps may have changed since yesterday • notes: User has provided both total endpoints and protected counts - can calculate coverage manually
19:01:30 [note] suggestions: Show me the breakdown of unprotected devices by department or location.;Generate a deployment plan to reach 95% coverage within 30 days.;Help me draft a report on endpoint security gaps for leadership.
"""
    turn = TurnLog.to_compressed_turn_obj(sample)
    print(turn.ctx_used_bullets)
    print(turn.summary_time)
    pair = turn_to_pair(turn)
    # print(pair)