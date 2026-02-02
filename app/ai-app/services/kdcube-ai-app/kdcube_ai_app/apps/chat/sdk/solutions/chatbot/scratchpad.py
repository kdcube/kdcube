# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/solutions/chatbot/scratchpad.py
from __future__ import annotations

import json
from datetime import datetime

from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import TurnLog, BaseTurnView
import re

from typing import Optional, List, Dict, Any, Type


from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import TurnScratchpad
from kdcube_ai_app.apps.chat.sdk.runtime.solution.gate.gate_contract import GateOut
from kdcube_ai_app.apps.chat.sdk.runtime.solution.presentation import (
    SolverPresenter, SolverPresenterConfig
)
from kdcube_ai_app.apps.chat.sdk.runtime.solution.protocol import UnifiedCoordinatorOut
from kdcube_ai_app.apps.chat.sdk.tools.summary.contracts import TurnSummaryOut
from kdcube_ai_app.apps.chat.sdk.util import _to_jsonable, _truncate
from kdcube_ai_app.apps.chat.sdk.runtime.solution.contracts import solve_result_from_full_payload, SolveResult

class CTurnScratchpad(TurnScratchpad):

    def __init__(
            self,
            user,
            conversation_id,
            turn_id,
            text,
            attachments=None,
            gate_out_class: Optional[Type] = None,
    ):
        super().__init__(user, conversation_id, turn_id, text, attachments)
        self.user_blocks = []

        # Thematic routing rules (if any)
        self.routing_rules: Optional[dict] = None

        # Policy / product hints visible to answer generator and solver
        self.product_policy = None

        self.gate_out_class = gate_out_class or GateOut
        self.gate: Optional[GateOut] = None

    @property
    def turn_view(self):
        solve_result = self.solver_result
        if not solve_result:
            if "solver.unified-planner" in self.agents_responses:
                coordinator = self.agents_responses["solver.unified-planner"]
                if isinstance(coordinator, dict):
                    coordinator = UnifiedCoordinatorOut.model_validate(coordinator)
                plan = coordinator.to_plan()
                solve_result = {
                    "plan":  plan
                }
        if solve_result:
            solve_result = _to_jsonable(solve_result)
        turn_log = self.turn_log
        timestamp = (self.started_at or datetime.utcnow().isoformat() + "Z").replace(':', '-')
        return {
            "turn_id": self.turn_id,
            "conversation_id": self.conversation_id,
            "user_id": self.user,
            "timestamp": timestamp,
            "payload": {
                **turn_log,
                "solver_result": solve_result
            },
        }

def _sd(d: Any) -> Dict[str, Any]:
    return d if isinstance(d, dict) else {}

def _payload_unwrap(rec: Dict[str, Any]) -> Dict[str, Any]:
    """unwrap artifact payloads where needed (ctx store style)"""
    if not isinstance(rec, dict):
        return {}
    pay = rec.get("payload") or {}
    if isinstance(pay, dict) and isinstance(pay.get("payload"), dict):
        return pay["payload"]
    return pay if isinstance(pay, dict) else {}

def _extract_memories_from_entries(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    out = {"topics": [], "suggestions": [], "notes": []}
    for e in entries or []:
        area = (e.get("area") or "").strip()
        msg = (e.get("msg") or "").strip()
        if area not in {"note", "summary"}:
            continue
        if area == "note" and msg.startswith("topics:"):
            payload = msg.split("topics:", 1)[1].strip()
            toks = [t.strip() for t in re.split(r"[;,]", payload) if t.strip()]
            out["topics"].extend(toks)
            continue
        if area == "note" and msg.startswith("suggestions:"):
            payload = msg.split("suggestions:", 1)[1].strip()
            sugs = [s.strip() for s in payload.split(";") if s.strip()]
            out["suggestions"].extend(sugs)
            continue
        out["notes"].append(msg)
    return out

class TurnView(BaseTurnView):
    """
    Book of the turn — holds REAL objects:
      - gate: GateOut
      - solver: SolveResult
      - turn_summary: TurnSummaryOut
      - turn_log: TurnLog   (augmented with .memories via TurnLog.state)
    """
    gate_out_class = GateOut

    def __init__(
            self,
            *,
            timestamp: Optional[str] = None,
            gate: Optional[GateOut],
            solver: Optional[SolveResult],
            turn_summary: Optional[TurnSummaryOut],
            turn_log: Optional[TurnLog],
            objective: Optional[str] = None,
            user_prompt_artifact: Optional[Dict[str, Any]] = None,
            assistant_completion_artifact: Optional[Dict[str, Any]] = None,
            user_attachments: Optional[List[Dict[str, Any]]] = None,
            assistant_files: Optional[List[Dict[str, Any]]] = None,
            turn_id: Optional[str] = None,
            user_id: Optional[str] = None,
            conversation_id: Optional[str] = None,
            gate_out_class: Optional[Type] = None,
    ):
        self.turn_id = turn_id
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.timestamp = timestamp

        self.gate_out_class = gate_out_class or self.gate_out_class
        self.gate: Optional[GateOut] = gate
        self.solver: Optional[SolveResult] = solver
        self.turn_summary: Optional[TurnSummaryOut] = turn_summary
        self.turn_log: Optional[TurnLog] = turn_log
        self.objective: Optional[str] = (objective or "").strip() if isinstance(objective, str) else ""

        self.user_prompt_artifact = _sd(user_prompt_artifact)
        self.assistant_completion_artifact = _sd(assistant_completion_artifact)
        self.user_attachments = list(user_attachments or [])
        self.assistant_files = list(assistant_files or [])

        # augment tlog state with derived memories for dot-access
        if self.turn_log and isinstance(self.turn_log, TurnLog):
            try:
                mem = _extract_memories_from_entries(self.turn_log.entries or [])
                self.turn_log.state = self.turn_log.state or {}
                self.turn_log.state["memories"] = mem
            except Exception:
                pass

    def _get_user_prompt_text(self) -> str:
        text = self.user_prompt_artifact.get("text")
        return (text or "").strip()

    def _get_user_prompt_summary(self) -> str:
        summary = self.user_prompt_artifact.get("summary")
        return (summary or "").strip()

    def _get_assistant_text(self) -> str:
        text = self.assistant_completion_artifact.get("text")
        return (text or "").strip()

    def _get_assistant_summary(self) -> str:
        summary = self.assistant_completion_artifact.get("summary")
        return (summary or "").strip()

    def _format_assistant_files(self, *, is_current_turn: bool) -> List[str]:
        from kdcube_ai_app.apps.chat.sdk.util import _truncate

        lines: List[str] = []
        for f in self.assistant_files or []:
            if not isinstance(f, dict):
                continue
            filename = (f.get("filename") or "").strip()
            if not filename:
                continue
            disp = (f.get("artifact_name") or "").strip() or filename
            mime = (f.get("mime") or "").strip()
            size = f.get("size") or f.get("size_bytes")
            summary = (f.get("summary") or "").strip()
            path = filename if is_current_turn else f"{self.turn_id}/files/{filename}"
            artifact_path = f"{'current_turn' if is_current_turn else self.turn_id}.files.{disp}"
            parts = [filename]
            if mime:
                parts.append(f"mime={mime}")
            if size is not None:
                parts.append(f"size={size}")
            parts.append(f"path={path}")
            parts.append(f"artifact_path={artifact_path}")
            lines.append("- " + " | ".join(parts))
            if summary:
                lines.append(f"  { _truncate(summary, 300) }")
        return lines

    def _turn_summary_dict(self) -> dict:
        if not self.turn_summary:
            return {}

        summary_dict = {}
        if hasattr(self.turn_summary, 'model_dump'):
            try:
                summary_dict = self.turn_summary.model_dump()
            except Exception:
                pass
        elif hasattr(self.turn_summary, 'dict'):
            try:
                summary_dict = self.turn_summary.dict()
            except Exception:
                pass
        elif isinstance(self.turn_summary, dict):
            summary_dict = self.turn_summary
        return summary_dict or {}

    def _render_assistant_response_and_summary(
        self,
        *,
        include_assistant_response: bool,
        include_turn_summary: bool,
        assistant_answer_limit: Optional[int],
        ts: str,
    ) -> List[str]:
        from kdcube_ai_app.apps.chat.sdk.util import _truncate

        sections: List[str] = []
        summary_dict = self._turn_summary_dict()
        assistant_answer = summary_dict.get("assistant_answer")
        assistant_text = self._get_assistant_text()

        if include_assistant_response:
            if assistant_answer:
                header = f"[ASSISTANT RESPONSE STRUCTURAL/SEMANTIC INVENTORY]{' (' + ts + ')' if ts else ''}"
                sections.append("\n".join([
                    header,
                    f"path: {self.turn_id}.assistant.completion.summary",
                    str(assistant_answer),
                ]))
            elif assistant_text:
                truncated = False
                if assistant_answer_limit is None:
                    assistant_replica = assistant_text
                else:
                    if len(assistant_text) > assistant_answer_limit:
                        truncated = True
                    assistant_replica = _truncate(assistant_text, assistant_answer_limit)
                trunc_label = " (truncated)" if truncated else ""
                header = f"[ASSISTANT RESPONSE{trunc_label}]{' (' + ts + ')' if ts else ''}"
                sections.append("\n".join([
                    header,
                    f"path: {self.turn_id}.assistant.completion.text",
                    assistant_replica,
                ]))

        if assistant_answer:
            summary_dict.pop("assistant_answer", None)

        if include_turn_summary and self.turn_summary:
            header = f"[TURN SUMMARY]{' (' + ts + ')' if ts else ''}"
            summary_lines = [header]

            # Key fields in priority order
            priority_fields = ['objective', 'user_inquiry']
            secondary_fields = ['domain', 'complexity', 'done', 'not_done',
                                'assumptions', 'risks', 'notes']

            def format_value(key: str, val, max_len: int):
                """Format a summary field value."""
                if not val:
                    return None

                if isinstance(val, str):
                    return f"{key}: {val}"
                elif isinstance(val, (list, tuple)):
                    if not val:
                        return None
                    items = [str(v) for v in val[:5]]
                    return f"{key}: {'; '.join(items)}"
                elif isinstance(val, dict):
                    try:
                        json_str = json.dumps(val, ensure_ascii=False)
                        return f"{key}: {_truncate(json_str, max_len)}"
                    except Exception:
                        return f"{key}: {str(val)[:max_len]}"
                else:
                    return f"{key}: {str(val)[:max_len]}"

            summary_found = False
            for key in priority_fields:
                val = summary_dict.get(key)
                line = format_value(key, val, 400)
                if line:
                    summary_lines.append(line)
                    summary_found = True

            for key in secondary_fields:
                val = summary_dict.get(key)
                line = format_value(key, val, 200)
                if line:
                    summary_lines.append(line)
                    summary_found = True
            if not summary_found:
                summary_lines.append("  (no turn summary computed)")

            sections.append("\n".join(summary_lines))

        return sections

    def _attachment_tagged_value(self, summary: str, key: str) -> str:
        if not summary:
            return ""
        m = re.search(rf"(?:^|[|\\s]){re.escape(key)}:([^|\\s]+)", summary)
        if not m:
            return ""
        return (m.group(1) or "").strip()

    def _attachment_path_name(self, raw_name: str, used: Dict[str, int]) -> str:
        base = (raw_name or "").strip() or "attachment"
        base = re.sub(r"[\\s./:]+", "_", base)
        base = re.sub(r"[^A-Za-z0-9_-]+", "", base) or "attachment"
        base = base.lower()
        count = used.get(base, 0) + 1
        used[base] = count
        return base if count == 1 else f"{base}_{count}"

    def _iter_attachment_entries(self, *, is_current_turn: bool = False) -> List[Dict[str, Any]]:
        used: Dict[str, int] = {}
        out: List[Dict[str, Any]] = []
        for a in self.user_attachments or []:
            if not isinstance(a, dict):
                continue
            summary = (a.get("summary") or "").strip()
            raw_name = (a.get("artifact_name") or "").strip()
            if not raw_name:
                raw_name = (a.get("filename") or "").strip()
            if not raw_name:
                raw_name = self._attachment_tagged_value(summary, "artifact_name")
            if not raw_name:
                raw_name = self._attachment_tagged_value(summary, "filename")
            disp = self._attachment_path_name(raw_name, used)
            filename = (a.get("filename") or "").strip()
            if is_current_turn:
                filepath = f"current_turn/attachments/{filename}" if filename else ""
                artifact_prefix = "current_turn"
            else:
                filepath = f"{self.turn_id}/attachments/{filename}" if filename else ""
                artifact_prefix = self.turn_id
            out.append({
                "artifact_path": f"{artifact_prefix}.user.attachments.{disp}",
                "filepath": filepath,
                "mime": (a.get("mime") or a.get("mime_type") or "").strip(),
                "filename": filename,
                "size": a.get("size") or a.get("size_bytes"),
                "summary": summary,
            })
        return out

    # --------- constructors ---------

    @staticmethod
    def from_turn_dict(
            turn_dict: Dict[str, Any],
            gate_out_class: Optional[type] = None,
    ) -> "TurnView":
        """
        Construct TurnView directly from a turn dictionary (e.g., search hit).

        Expects structure:
        {
          "turn_id": "...",
          "payload": {
            "user_id": "...",
            "conversation_id": "...",
            "ts": "...",
            "payload": {  # inner payload (double-wrapped)
              "turn_log": {
                "user": {"prompt": { ...artifact... }, "attachments": [...]},
                "assistant": {"completion": { ...artifact... }, "files": [...]},
                ...
              },
              "gate": {...},
              "solver_result": {...},
              "turn_summary": {...}
            }
          }
        }

        Args:
            turn_dict: Turn dictionary from search or storage

        Returns:
            TurnView instance

        Example:
            >>> hits = await search_context_(...)
            >>> turn_views = [TurnView.from_turn_dict(hit) for hit in hits]
        """
        # Extract top-level fields
        turn_id = turn_dict.get("turn_id")

        # Navigate to outer payload
        outer_payload = turn_dict.get("payload") or {}
        user_id = outer_payload.get("user_id")
        conversation_id = outer_payload.get("conversation_id")
        timestamp = outer_payload.get("ts")

        # Extract tlog
        # tlog_data = outer_payload.get("turn_log") or {}

        # Use the full payload for the detailed construction
        return TurnView.from_saved_payload(
            turn_id=turn_id,
            user_id=user_id,
            conversation_id=conversation_id,
            # tlog=tlog_data,
            payload=turn_dict,
            gate_out_class=gate_out_class,
        )

    @classmethod
    def from_turn_log_dict(
            cls: "type[TurnView]",
            payload: Optional[Dict[str, Any]] = None,
            gate_out_class: Optional[Type] = None,
    ) -> "TurnView":
        """
        Construct TurnView from a turn-log payload wrapper.

        This method provides fine-grained control over construction. For simpler
        use cases where you have a complete turn dict, use `from_turn_dict` instead.

        Args:
            turn_id: Turn identifier
            user_id: User identifier
            conversation_id: Conversation identifier
            tlog: TurnLog instance or dict
            payload: Full payload dict

        Returns:
            TurnView instance
        """
        if payload is None:
            payload = {}

        return cls.from_saved_payload(
            turn_id=payload.get("turn_id"),
            user_id=payload.get("user_id"),
            conversation_id=payload.get("conversation_id"),
            # tlog=payload.get("turn_log"),
            payload=payload,
            gate_out_class=gate_out_class,
        )

    @classmethod
    def from_saved_payload(
            cls,
            *,
            turn_id: Optional[str] = None,
            user_id: Optional[str] = None,
            conversation_id: Optional[str] = None,
            # tlog: Any = None,                 # TurnLog instance or dict (from ctx store)
            payload: Optional[Dict[str, Any]] = None,   # the payload you saved in save_turn_log_as_artifact(..., payload=...)
            gate_out_class: Optional[Type] = None,
    ) -> "TurnView":
        """
        Construct TurnView from saved payload with explicit field extraction.

        This method provides fine-grained control over construction. For simpler
        use cases where you have a complete turn dict, use `from_turn_dict` instead.

        Args:
            turn_id: Turn identifier
            user_id: User identifier
            conversation_id: Conversation identifier
            tlog: TurnLog instance or dict
            payload: Full payload dict

        Returns:
            TurnView instance
        """
        if payload is None:
            payload = {}

        # unwrap payload if double-wrapped
        blob = _payload_unwrap(payload) if "payload" in payload else payload

        tlog = _sd(blob.get("turn_log"))

        # GateOut — rebuild from turn_log payload
        gate = None
        gate_cls = gate_out_class or getattr(cls, "gate_out_class", GateOut) or GateOut
        g = blob.get("gate")
        if isinstance(g, gate_cls):
            gate = g
        elif isinstance(g, dict):
            gate = gate_cls.model_validate(g)

        # TurnSummaryOut
        ts_dict = _sd(blob.get("turn_summary"))
        summary = TurnSummaryOut(**ts_dict) if ts_dict else None
        objective = ""
        if summary and getattr(summary, "objective", None):
            objective = (summary.objective or "").strip()
        if not objective:
            ctx_rec = blob.get("ctx.reconciler")
            if isinstance(ctx_rec, dict):
                objective = (ctx_rec.get("objective") or "").strip()

        # SolveResult — rebuild from turn_log payload
        solver = None
        sr_full = _sd(blob.get("solver_result"))
        if sr_full:
            solver = solve_result_from_full_payload(sr_full)

        user_attachments = _sd(blob.get("user")).get("attachments") or []
        user_prompt_artifact = _sd(_sd(blob.get("user")).get("prompt"))
        assistant_completion_artifact = _sd(_sd(blob.get("assistant")).get("completion"))
        assistant_files = _sd(blob.get("assistant")).get("files") or []

        # TurnLog — accept instance, else rebuild from dict
        if isinstance(tlog, TurnLog):
            log_obj = tlog
        elif isinstance(tlog, dict):
            # tlog ~ TurnLog.to_payload() shape
            log_obj = TurnLog.model_validate(tlog)
        else:
            log_obj = None

        # Timestamp handling - normalize to ISO string
        dt = payload.get("ts") or blob.get("ts")
        if dt is None:
            iso_timestamp = None
        elif isinstance(dt, str):
            # Already a string (ISO format from search results)
            iso_timestamp = dt
        elif hasattr(dt, "isoformat"):
            # datetime object (from recent results)
            iso_timestamp = dt.isoformat()
        else:
            # Unknown type - try to stringify
            iso_timestamp = str(dt) if dt else None

        return cls(
            turn_id=turn_id,
            user_id=user_id,
            conversation_id=conversation_id,
            timestamp=iso_timestamp,
            gate=gate,
            solver=solver,
            turn_summary=summary,
            turn_log=log_obj,
            objective=objective,
            user_prompt_artifact=user_prompt_artifact,
            assistant_completion_artifact=assistant_completion_artifact,
            user_attachments=user_attachments,
            assistant_files=assistant_files,
            gate_out_class=gate_cls,
        )

    def to_compressed_search_view(
            self,
            *,
            user_prompt_limit: int = 400,
            user_prompt_or_inventorization_summary: str = "inv", # user | inv
            attachment_text_limit: int = 300,
            deliverable_desc_limit: int = 200,
            include_assistant_response: bool = True,
            include_turn_summary: bool = True,
            include_context_used: bool = True,
            include_turn_info: bool = True,
            extended_coordinator_info = False,
            deliverables_detalization="summary", # digest|summary
            include_deliverables_section: bool = True
    ) -> str:
        """
        Generate a compressed view of this turn suitable for search result display.

        This format is designed for context reconciler search results, providing
        key information about the turn without full verbosity.

        Sections included:
        - Turn ID and timestamp (if include_turn_info=True)
        - Clarification indicator (if this turn asked clarification questions)
        - User prompt (truncated) with timestamp
        - User attachments (if any, with previews)
        - Context used (if any, and include_context_used=True) with timestamps
        - Gate objective with timestamp
        - Solver coordinator decision (mode, confidence, reasoning, tools) with timestamp
        - Solver deliverables (names, types, descriptions, status) with timestamp
        - Assistant response (summary if available, else full) with timestamp
        - Turn summary (key fields, if include_turn_summary=True) with timestamp

        Args:
            user_prompt_limit: Max characters for user prompt text
            attachment_text_limit: Max characters per attachment preview
            deliverable_desc_limit: Max characters per deliverable description
            include_assistant_response: Whether to include assistant response section
            include_turn_summary: Whether to include the turn summary section
            include_context_used: Whether to include context used from tlog
            include_turn_info: Whether to include turn ID and timestamp header

        Returns:
            Formatted string with compressed turn information
        """
        from kdcube_ai_app.apps.chat.sdk.util import _truncate
        import json

        sections = []

        # Extract timestamp map from tlog entries for temporal context
        ts_map = self._extract_timestamps_map()

        # --- TURN ID AND TIMESTAMP ---
        if include_turn_info:
            header_parts = []
            if hasattr(self, 'turn_id') and self.turn_id:
                header_parts.append(f"Turn ID: {self.turn_id}")
            if hasattr(self, 'timestamp') and self.timestamp:
                header_parts.append(f"Timestamp: {self.timestamp}")
            elif hasattr(self, 'created_at') and self.created_at:
                header_parts.append(f"Timestamp: {self.created_at}")

            if header_parts:
                sections.append("[TURN INFO]\n" + "\n".join(header_parts))

        # --- CLARIFICATION TURN INDICATOR ---
        clarification_questions = self._extract_clarification_questions()
        if clarification_questions:
            clar_lines = ["[CLARIFICATION TURN — BOT ASKED USER]"]
            clar_lines.append("Questions asked:")
            for i, q in enumerate(clarification_questions[:5], 1):
                clar_lines.append(f"  {i}. {q}")
            sections.append("\n".join(clar_lines))

        # --- USER PROMPT ---
        ts = ts_map.get('user', '')

        user_prompt_summary = self._get_user_prompt_summary()
        user_prompt_text = self._get_user_prompt_text()

        if not user_prompt_summary:
            user_prompt_or_inventorization_summary = "user"

        if user_prompt_or_inventorization_summary == "inv":
            header = f"[USER PROMPT STRUCTURAL/SEMANTIC INVENTORIZATION SUMMARY]{' (' + ts + ')' if ts else ''}"
            user_replica = user_prompt_summary
        else:
            truncated = False
            if user_prompt_limit is not None and len(user_prompt_text) > user_prompt_limit:
                truncated = True
                user_replica = _truncate(user_prompt_text, user_prompt_limit)
            else:
                user_replica = user_prompt_text
            trunc_label = " (truncated)" if truncated else ""
            header = f"[USER PROMPT{trunc_label}]{' (' + ts + ')' if ts else ''}"
        if user_replica:
            path = f"{self.turn_id}.user.prompt.summary" if user_prompt_or_inventorization_summary == "inv" else f"{self.turn_id}.user.prompt.text"
            sections.append(f"{header}\npath: {path}\n{user_replica}")

        # --- USER ATTACHMENTS ---
        attachment_entries = self._iter_attachment_entries(is_current_turn=extended_coordinator_info)
        if attachment_entries:
            att_lines = ["[USER ATTACHMENTS]"]
            for att in attachment_entries[:5]:
                artifact_path = att.get("artifact_path") or ""
                filepath = att.get("filepath") or ""
                mime = att.get("mime") or "unknown"
                filename = att.get("filename") or ""
                size = att.get("size")
                summary = att.get("summary") or ""
                parts = []
                if filepath:
                    parts.append(f"filepath=\"{filepath}\"")
                if artifact_path:
                    parts.append(f"artifact_path=\"{artifact_path}\"")
                parts.append(f"mime={mime}")
                if filename:
                    parts.append(f"filename=\"{filename}\"")
                if size is not None:
                    parts.append(f"size={size}")
                att_lines.append("• " + " | ".join(parts))
                if summary:
                    att_lines.append(f"  {summary}")

            sections.append("\n".join(att_lines))

        # --- CONTEXT USED ---
        if include_context_used:
            ctx_entries = self._extract_context_used_with_timestamps(ts_map)
            if ctx_entries:
                ctx_lines = ["[CONTEXT USED]"]
                for entry in ctx_entries:
                    ctx_lines.append(entry)
                sections.append("\n".join(ctx_lines))

        # --- OBJECTIVE ---
        if self.objective and isinstance(self.objective, str) and self.objective.strip():
            ts = ts_map.get('objective', '')
            header = f"[OBJECTIVE]{' (' + ts + ')' if ts else ''}"
            sections.append("\n".join([header, self.objective.strip()]))

        # --- GATE ROUTE ---
        if self.gate:
            gate_sections = []
            route = None
            if hasattr(self.gate, 'route'):
                route = self.gate.route
            elif isinstance(self.gate, dict):
                route = self.gate.get('route')
            if route:
                gate_sections.append(f"ROUTE: {route}")
            if gate_sections:
                ts = ts_map.get('objective', '')
                header = f"[GATE]{' (' + ts + ')' if ts else ''}"
                gate_sections.insert(0, f"{header}")
                sections.append("\n".join(gate_sections))

        # --- SOLVER COORDINATOR DECISION ---
        if self.solver and self.solver.plan:
            # Try to find coordinator or unified.plan timestamp
            ts = ts_map.get('solver_plan', ts_map.get('solver_coordinator', ''))
            plan = self.solver.plan
            header = f"[SOLVER.COORDINATOR DECISION]{' (' + ts + ')' if ts else ''}"
            if extended_coordinator_info:
                coord_lines = [header, plan.instructions_for_downstream]
                sections.append("\n".join(coord_lines))
            else:
                coord_lines = [header]
                mode = getattr(plan, 'mode', None)
                if mode:
                    coord_lines.append(f"mode: {mode}")

                # confidence = getattr(plan, 'confidence', None)
                # if confidence is not None:
                #     coord_lines.append(f"confidence: {confidence:.2f}")

                reasoning = getattr(plan, 'reasoning', None)
                if reasoning:
                    reasoning_text = _truncate(str(reasoning), 300)
                    coord_lines.append(f"reasoning: {reasoning_text}")

                solvable = getattr(plan, 'solvable', None)
                if solvable is not None:
                    coord_lines.append(f"solvable: {solvable}")

                # Tools selected
                tools = getattr(plan, 'tools', None)
                if tools and isinstance(tools, list):
                    tool_names = []
                    for t in tools[:5]:
                        if isinstance(t, dict):
                            tool_names.append(t.get('id', str(t)))
                        else:
                            tool_names.append(str(t))
                    if tool_names:
                        coord_lines.append(f"tools: {', '.join(tool_names)}")

                sections.append("\n".join(coord_lines))

        # --- SOLVER DELIVERABLES ---
        if include_deliverables_section and self.solver and self.solver.deliverables_map():
            ts = ts_map.get('solver_result', '')
            header = f"[SOLVER DELIVERABLES]{' (' + ts + ')' if ts else ''}"
            deliv_lines = [header]

            deliverables = self.solver.deliverables_map()
            if isinstance(deliverables, dict):
                count = 0
                for name, spec in deliverables.items():
                    if name == "project_log":
                        continue
                    if count >= 10:
                        break
                    count += 1
                    gaps = None
                    summary = None

                    # Extract deliverable info from spec
                    if isinstance(spec, dict):
                        typ = spec.get('type', 'unknown')
                        fmt = spec.get('format', '')
                        desc = spec.get('description', '')

                        # Check status from value
                        value = spec.get('value') or {}
                        has_value = isinstance(value, dict)
                        is_draft = has_value and value.get('draft', False)
                        gaps = value.get("gaps") if has_value else None
                        summary = value.get("summary") if has_value else None
                        if not has_value:
                            status = "❌ missing"
                        elif is_draft:
                            status = "⚠️ DRAFT"
                        else:
                            status = "✓ completed"
                    else:
                        typ = 'unknown'
                        fmt = ''
                        desc = ''
                        status = "❌ missing"

                    type_info = f"type: {typ}"
                    if fmt:
                        type_info += f", format: {fmt}"

                    deliv_lines.append(f"• {name} ({type_info})")
                    deliv_lines.append(f"  path: {self.turn_id}.slots.{name}")
                    if desc:
                        desc_truncated = _truncate(str(desc), deliverable_desc_limit)
                        deliv_lines.append(f"  Description: {desc_truncated}")
                    deliv_lines.append(f"  Status: {status}")
                    if gaps and deliverables_detalization == "summary":
                        deliv_lines.append(f"  Gaps: {gaps}")
                    if summary and deliverables_detalization == "summary":
                        deliv_lines.append(f"  Structural summary: {summary}")
                    deliv_lines.append("")

            # Add failure info if present
            if self.solver.failure:
                deliv_lines.append("\n⚠️ SOLVER FAILURE")
                failure = self.solver.failure
                if isinstance(failure, dict):
                    reason = failure.get('reason', '') or failure.get('error', '')
                    if reason:
                        deliv_lines.append(f"Reason: {_truncate(str(reason), 200)}")
                elif isinstance(failure, str):
                    deliv_lines.append(f"Reason: {_truncate(failure, 200)}")

            sections.append("\n".join(deliv_lines))

        # --- ASSISTANT RESPONSE + TURN SUMMARY ---
        if include_assistant_response or include_turn_summary:
            ts = ts_map.get('summary', '')
            sections.extend(self._render_assistant_response_and_summary(
                include_assistant_response=include_assistant_response,
                include_turn_summary=include_turn_summary,
                assistant_answer_limit=None,
                ts=ts,
            ))

        return "\n\n".join(sections)

    def _extract_timestamps_map(self) -> dict[str, str]:
        """
        Extract key timestamps from tlog entries for different phases of the turn.
        Returns a map of event_type -> timestamp (HH:MM:SS format).

        Event types:
        - user: User message/prompt
        - objective: Gate objective extraction
        - solver_plan: Solver planning (unified.plan or coordinator)
        - solver_coordinator: Specific coordinator timestamp
        - solver_result: Solver execution result
        - summary: Turn summary generation
        - ctx_queries: Context queries
        """
        ts_map = {}

        if not self.turn_log:
            return ts_map

        entries = []
        if isinstance(self.turn_log, dict):
            entries = self.turn_log.get("entries", [])
        elif hasattr(self.turn_log, "entries"):
            entries = list(getattr(self.turn_log, "entries", []))

        for entry in entries:
            # Handle both dict and object formats
            if isinstance(entry, dict):
                t = entry.get('t', '').strip()
                area = entry.get('area', '').strip()
                sub = (entry.get('sub') or entry.get('step') or entry.get('kind') or '').strip()
            else:
                t = getattr(entry, 't', '').strip()
                area = getattr(entry, 'area', '').strip()
                sub = (getattr(entry, 'sub', None) or
                       getattr(entry, 'step', None) or
                       getattr(entry, 'kind', None) or '').strip()

            if not t:
                continue

            # Map areas to event types (keep first occurrence for each type)
            if area in ('user', 'user.prompt') and 'user' not in ts_map:
                ts_map['user'] = t
            elif area == 'objective' and 'objective' not in ts_map:
                ts_map['objective'] = t
            elif area == 'solver':
                sub_lower = sub.lower().replace(' ', '')
                if sub_lower in ('unified.plan', 'unifiedplan') and 'solver_plan' not in ts_map:
                    ts_map['solver_plan'] = t
                elif sub_lower in ('coordinator', 'coord') and 'solver_coordinator' not in ts_map:
                    ts_map['solver_coordinator'] = t
                    if 'solver_plan' not in ts_map:  # Use coordinator as fallback for plan
                        ts_map['solver_plan'] = t
                elif sub_lower in ('solver.result', 'result') and 'solver_result' not in ts_map:
                    ts_map['solver_result'] = t
            elif area == 'summary' and 'summary' not in ts_map:
                ts_map['summary'] = t
            elif area in ('ctx.queries', 'gate.ctx_queries') and 'ctx_queries' not in ts_map:
                ts_map['ctx_queries'] = t

        return ts_map

    def _extract_context_used_with_timestamps(self, ts_map: dict[str, str]) -> list[str]:
        """
        Extract context used entries from tlog with their timestamps.
        Returns list of formatted strings with timestamps.
        """
        if not self.turn_log:
            return []

        ctx_entries = []
        entries = []

        if isinstance(self.turn_log, dict):
            entries = self.turn_log.get("entries", [])
        elif hasattr(self.turn_log, "entries"):
            entries = list(getattr(self.turn_log, "entries", []))

        for entry in entries:
            # Handle both dict and object formats
            if isinstance(entry, dict):
                t = entry.get('t', '').strip()
                area = entry.get('area', '')
                msg = entry.get('msg', '')
            else:
                t = getattr(entry, 't', '').strip()
                area = getattr(entry, 'area', '')
                msg = getattr(entry, 'msg', '')

            # Look for context used entries
            if area == 'note' and msg.startswith('[ctx.used]:'):
                # Extract the content after the prefix
                ctx_content = msg[len('[ctx.used]:'):].strip()
                # Format with timestamp if available
                if t:
                    ctx_entries.append(f"{t} | {ctx_content}")
                else:
                    ctx_entries.append(ctx_content)

        return ctx_entries

    def _extract_clarification_questions(self) -> list[str]:
        """
        Extract clarification questions from gate or tlog.
        Returns list of question strings.
        """
        questions = []

        # 1. Try gate first (most reliable source)
        if self.gate:
            gate_questions = None
            if isinstance(self.gate, dict):
                for key in ('clarification_questions', 'clarifications', 'questions'):
                    val = self.gate.get(key)
                    if isinstance(val, list):
                        gate_questions = val
                        break
            else:
                for key in ('clarification_questions', 'clarifications', 'questions'):
                    val = getattr(self.gate, key, None)
                    if isinstance(val, list):
                        gate_questions = val
                        break

            if gate_questions:
                for q in gate_questions:
                    if isinstance(q, str) and q.strip():
                        questions.append(q.strip())
                    elif isinstance(q, dict) and q.get('question'):
                        questions.append(str(q['question']).strip())

        # 2. If no gate questions, parse from tlog entries
        if not questions and self.turn_log:
            entries = []
            if isinstance(self.turn_log, dict):
                entries = self.turn_log.get("entries", [])
            elif hasattr(self.turn_log, "entries"):
                entries = list(getattr(self.turn_log, "entries", []))

            for entry in entries:
                # Handle both dict and object formats
                if isinstance(entry, dict):
                    area = entry.get('area', '')
                    msg = entry.get('msg', '')
                else:
                    area = getattr(entry, 'area', '')
                    msg = getattr(entry, 'msg', '')

                # Look for clarification flow entries
                if area == 'note' and 'Clarification Flow' in msg:
                    # Try to parse questions from the message
                    # Format: "Clarification Flow. Bot -> User: ask clarification questions: title=...; questions=['Q1', 'Q2']"
                    import re
                    match = re.search(r"questions=\[(.*?)\]", msg)
                    if match:
                        questions_str = match.group(1)
                        # Parse quoted strings
                        parsed = re.findall(r"['\"]([^'\"]+)['\"]", questions_str)
                        questions.extend([q.strip() for q in parsed if q.strip()])

        return questions[:5]  # Limit to 5 questions

    def _extract_context_used(self) -> list[str]:
        """Extract context used entries from tlog."""
        if not self.turn_log:
            return []

        ctx_entries = []
        entries = []

        if isinstance(self.turn_log, dict):
            entries = self.turn_log.get("entries", [])
        elif hasattr(self.turn_log, "entries"):
            entries = list(getattr(self.turn_log, "entries", []))

        for entry in entries:
            # Handle both dict and object formats
            if isinstance(entry, dict):
                area = entry.get('area', '')
                msg = entry.get('msg', '')
            else:
                area = getattr(entry, 'area', '')
                msg = getattr(entry, 'msg', '')

            # Look for context used entries
            if area == 'note' and msg.startswith('[ctx.used]:'):
                # Extract the content after the prefix
                ctx_content = msg[len('[ctx.used]:'):].strip()
                ctx_entries.append(ctx_content)

        return ctx_entries

    def generate_one_liner(self) -> str:
        """
        Generate a brief one-liner summary of this turn.
        Format: "<objective> — topics: <topics>"
        """
        objective = ""
        topics = []

        # Try to get objective from turn_summary or gate
        if self.turn_summary:
            if isinstance(self.turn_summary, dict):
                objective = self.turn_summary.get("objective", "")
                topics = self.turn_summary.get("topics", [])
            elif hasattr(self.turn_summary, "objective"):
                objective = getattr(self.turn_summary, "objective", "")
                topics = getattr(self.turn_summary, "topics", [])

        if not objective and self.objective:
            objective = self.objective

        # Extract topic names
        topic_names = []
        for t in topics:
            if isinstance(t, str):
                topic_names.append(t)
            elif isinstance(t, dict) and t.get("name"):
                topic_names.append(t["name"])
            elif hasattr(t, "name"):
                topic_names.append(t.name)

        topics_line = ", ".join(topic_names[:8])[:120]

        if objective and topics_line:
            return f"{objective} — topics: {topics_line}"
        elif objective:
            return objective
        elif topics_line:
            return f"topics: {topics_line}"
        return ""

    def compute_turn_outcome_status(self) -> str:
        """
        Determine and format the clear outcome status of this turn.

        Returns:
            Formatted status line (e.g., "✅ SOLVER SUCCESS — 3 deliverables completed")
        """
        # No solver → assistant answered directly or clarification
        if not self.solver:
            if self._get_assistant_text():
                return "[TURN OUTCOME]\n📝 ASSISTANT DIRECT ANSWER (no solver invoked). status=`answered_by_assistant`"
            return "[TURN OUTCOME]\n⚠️ NO ACTIVITY (no solver, no assistant response). status=`no_activity`"

        # Check plan mode
        plan = self.solver.plan
        mode = getattr(plan, 'mode', None) if plan else None

        if mode == "clarification_only":
            num_questions = 0
            if plan and hasattr(plan, 'clarification_questions'):
                num_questions = len(plan.clarification_questions or [])
            return f"[TURN OUTCOME]\nℹ️ CLARIFICATION MODE — asked {num_questions} question(s). status=clarification.answered_by_assistant"

        if mode == "llm_only":
            return "[TURN OUTCOME]\nℹ️ LLM ONLY MODE — coordinator decided no tools needed. status=`answered_by_assistant`"

        # Check solvability
        if plan and hasattr(plan, 'solvable') and plan.solvable is False:
            return "[TURN OUTCOME]\n🚫 NOT SOLVABLE — coordinator determined objective cannot be solved. status=`unsolvable.answered_by_assistant`"

        # Solver ran → check deliverables + failure
        deliverables_map = {}
        try:
            if hasattr(self.solver, "deliverables_map"):
                deliverables_map = self.solver.deliverables_map() or {}
        except Exception:
            pass

        # Count non-auxiliary deliverables
        payload_deliverables = {
            name: spec
            for name, spec in deliverables_map.items()
            if name not in {"project_log"}
        }

        # Count by status
        completed = 0
        draft = 0
        missing = 0

        for name, spec in payload_deliverables.items():
            if not isinstance(spec, dict):
                missing += 1
                continue

            value = spec.get("value")
            if not isinstance(value, dict):
                missing += 1
            elif value.get("draft"):
                draft += 1
            else:
                completed += 1

        total_slots = len(payload_deliverables)
        has_failure = bool(getattr(self.solver, 'failure', None))

        # Determine status
        if completed > 0 and not has_failure and draft == 0 and missing == 0:
            # Full success
            return f"[TURN OUTCOME]\n✅ SOLVER SUCCESS — {completed} deliverable(s) completed. status=`solver_success`"

        elif completed > 0 and (has_failure or draft > 0 or missing > 0):
            # Partial success
            parts = [f"{completed} completed"]
            if draft > 0:
                parts.append(f"{draft} draft")
            if missing > 0:
                parts.append(f"{missing} missing")
            if has_failure:
                parts.append("with errors")
            return f"[TURN OUTCOME]\n⚠️ SOLVER PARTIAL SUCCESS — {', '.join(parts)}.  status=`solver_partial_success`"

        elif has_failure and completed == 0:
            # Total failure
            failure = self.solver.failure
            reason = ""
            if isinstance(failure, dict):
                reason = failure.get('reason', '') or failure.get('error', '')
            elif isinstance(failure, str):
                reason = failure

            # reason_preview = f" — {reason[:100]}" if reason else ""
            reason_preview = f" — {reason}" if reason else ""
            return f"[TURN OUTCOME]\n❌ SOLVER FAILURE{reason_preview}. status=`solver_failure`"

        else:
            # Edge case: solver ran but no clear outcome
            return f"[TURN OUTCOME]\n⚠️ UNCLEAR OUTCOME — {total_slots} slot(s) in contract, none completed.  status=`solver_failure`"

    def to_solver_presentation(
            self,
            *,
            is_current_turn: bool = False,
            user_prompt_limit: Optional[int] = 10_000,
            assistant_answer_limit: Optional[int] = 10_000,
            user_prompt_or_inventorization_summary: str = "inv", # inv|user
            assistant_completion_or_inventorization_summary: str = "inv", # inv|assistant
            program_log_limit: Optional[int] = None,
            include_base_summary: bool = True,
            include_program_log: bool = True,
            include_deliverable_meta: bool = True,
            include_assistant_response: bool = True,
    ) -> str:
        """
        Solver-centric, *non-truncated* view of this turn.

        Designed for solver-module agents, not for general context search.
        - Optionally includes the compact search view (to_compressed_search_view)
        - Adds FULL program log (no truncation)
        - Adds detailed deliverable meta (status, guidance, inventarization)
        - Optionally includes the assistant response (summary if available, else full)
        - Always appends turn summary (if available) at the end

        No previews of deliverable content: either solver fetches full artifacts
        via ctx_tools.fetch_turn_artifacts([...]) or it reasons only from meta.
        """

        sections: list[str] = []

        # ========== TURN OUTCOME STATUS (clear indicator) ==========
        if not is_current_turn:
            status_section = self.compute_turn_outcome_status()
            if status_section:
                sections.append(status_section)

        # Base summary (compressed view of gate/context/summary)
        if include_base_summary:
            base = self.to_compressed_search_view(
                user_prompt_limit=user_prompt_limit,
                user_prompt_or_inventorization_summary=user_prompt_or_inventorization_summary,
                include_turn_info=False,
                extended_coordinator_info=is_current_turn,
                deliverables_detalization="digest",
                include_assistant_response=False,
                include_turn_summary=False,
                include_deliverables_section=False
            )
            if base.strip():
                if sections:
                    sections.append("")
                sections.append(base.strip())

        # Solver presentation - delegate to unified presenter
        if self.solver and not is_current_turn:
            file_path_prefix = "" if is_current_turn else f"{self.turn_id}/files"
            presenter = SolverPresenter(self.solver, file_path_prefix=file_path_prefix)

            config = SolverPresenterConfig(
                include_project_log=include_program_log,
                project_log_limit=program_log_limit,
                include_deliverables=include_deliverable_meta,
                deliverables_grouping="status",
                deliverable_attrs={
                    "description",
                    "gaps",
                    "summary",
                    "sources_used",
                    "filename",
                },
                exclude_slots=["project_canvas"],
                output_format="markdown",
            )

            solver_md = presenter.render(config)
            if solver_md.strip():
                if sections:
                    sections.append("")
                sections.append("[SOLVER LOG AND DELIVERABLES]")
                sections.append(solver_md.strip())

        if not is_current_turn:
            assistant_file_lines = self._format_assistant_files(is_current_turn=is_current_turn)
            if assistant_file_lines:
                if sections:
                    sections.append("")
                sections.append("[ASSISTANT FILES (HISTORICAL)]")
                sections.extend(assistant_file_lines)

        # Assistant response + turn summary
        ts_map = self._extract_timestamps_map()
        ts = ts_map.get('summary', '')
        response_sections = self._render_assistant_response_and_summary(
            include_assistant_response=include_assistant_response,
            include_turn_summary=True,
            assistant_answer_limit=assistant_answer_limit,
            ts=ts,
        )
        if response_sections:
            if sections:
                sections.append("")
            sections.extend(response_sections)

        return "\n".join(sections).strip()

    def to_final_answer_presentation(
            self,
            *,
            assistant_answer_limit: int = 1600,
    ) -> str:
        from kdcube_ai_app.apps.chat.sdk.util import _truncate
        from kdcube_ai_app.apps.chat.sdk.context.memory.presentation import (
            format_turn_memory_fingerprint,
            format_assistant_signals_for_turn,
        )

        sections: list[str] = []

        def _fallback_fingerprint_from_turn_log() -> Dict[str, Any]:
            summary_dict = self._turn_summary_dict()
            prefs = summary_dict.get("prefs") or {}
            facts = summary_dict.get("facts") or []
            topics = summary_dict.get("topics") or []
            if not isinstance(topics, list):
                topics = []
            return {
                "version": "v1",
                "turn_id": self.turn_id,
                "objective": (summary_dict.get("objective") or self.objective or "").strip(),
                "topics": topics,
                "assertions": list(prefs.get("assertions") or []),
                "exceptions": list(prefs.get("exceptions") or []),
                "facts": list(facts or []),
                "made_at": (summary_dict.get("made_at") or "").strip(),
            }

        def _normalize_program_presentation(text: str) -> str:
            lines = [ln.rstrip() for ln in (text or "").splitlines()]
            if lines and lines[0].strip() == "# Program Presentation":
                lines = lines[1:]
            normalized: list[str] = []
            for ln in lines:
                if ln.startswith("## "):
                    normalized.append(f"### {ln[3:]}")
                elif ln.startswith("### "):
                    normalized.append(f"#### {ln[4:]}")
                elif ln.startswith("#### "):
                    normalized.append(f"##### {ln[5:]}")
                else:
                    normalized.append(ln)
            return "\n".join(normalized).strip()

        # Solver status + program presentation (if any)
        status = "not_run"
        mode = ""
        failure = ""
        if self.solver and self.solver.plan:
            mode = (self.solver.plan.mode or "").strip()
            if mode == "llm_only":
                status = "llm_only"
            elif mode == "clarification_only":
                status = "not_run"
            else:
                status = "ran"
            if self.solver.failure:
                failure = str(self.solver.failure)

        if self.solver and self.solver.execution and self.solver.execution.deliverables:
            status = "ran"

        # Turn memory (fingerprint)
        fp = None
        if self.turn_log and isinstance(self.turn_log, TurnLog):
            fp = (self.turn_log.state or {}).get("fingerprint")
        if not isinstance(fp, dict):
            fp = _fallback_fingerprint_from_turn_log()
        mem_block = format_turn_memory_fingerprint(fp)
        if mem_block:
            sections.append("")
            sections.append(mem_block)

        sections.append("")
        sections.append("# Solver")
        sections.append(f"- status: {status}")
        if mode:
            sections.append(f"- mode: {mode}")
        if failure:
            sections.append(f"- failure: {failure}")

        if self.solver and self.solver.failure:
            failure_prez = getattr(self.solver, "failure_presentation", "") or ""
            sections.append("")
            sections.append("## Solver Failure")
            if failure_prez:
                sections.append(str(failure_prez).strip())
            else:
                sections.append(str(self.solver.failure))
        elif status == "ran" and self.solver:
            prez = self.solver.program_presentation or ""
            if prez.strip():
                sections.append("")
                sections.append("## Program Presentation")
                sections.append(_normalize_program_presentation(prez))

        # Clarification questions (if this was clarification-only or asked questions)
        clarifications = self._extract_clarification_questions()
        if clarifications:
            sections.append("")
            sections.append("## Clarification Questions Asked")
            for i, q in enumerate(clarifications[:6], 1):
                q_txt = str(q).strip()
                if q_txt:
                    sections.append(f"{i}) {q_txt}")

        # Assistant answer (truncate + summary if needed)
        summary_dict = self._turn_summary_dict()
        assistant_summary = (summary_dict.get("assistant_answer") or "").strip()
        assistant_text = (self._get_assistant_text() or "").strip()
        if assistant_text:
            if assistant_answer_limit and len(assistant_text) > assistant_answer_limit:
                sections.append("")
                sections.append("# Answer (shown to user, truncated)")
                sections.append(_truncate(assistant_text, assistant_answer_limit))
                if assistant_summary:
                    sections.append("**Answer summary (from turn summary):**")
                    sections.append(assistant_summary)
            else:
                sections.append("")
                sections.append("# Answer (shown to user)")
                sections.append(assistant_text)
        elif assistant_summary:
            sections.append("")
            sections.append("# Answer summary (from turn summary)")
            sections.append(assistant_summary)
        else:
            sections.append("")
            sections.append("# Answer (missing)")

        # Assistant signals (from fingerprint)
        if isinstance(fp, dict):
            sig_block = format_assistant_signals_for_turn(fp)
            if sig_block:
                sections.append("")
                sections.append(sig_block)

        return "\n".join([s for s in sections if s]).strip()
