# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/contracts.py

from __future__ import annotations
from pydantic import BaseModel, Field
import json, logging
import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Literal

from kdcube_ai_app.apps.chat.sdk.tools.citations import normalize_url, enrich_canonical_sources_with_favicons
from kdcube_ai_app.apps.chat.sdk.util import _to_jsonable

log = logging.getLogger(__name__)

SERVICE_LOG_SLOT = "project_log"   # a.k.a. "turn_log" (normalized to project_log)

def _service_log_contract_entry() -> dict:
    # Hidden service slot: text-only, markdown, filled by set_progress()
    return {
        "type": "inline",
        "description": "Live run log",
        "format": "markdown",
        "content_guidance": "",
        "_hidden": True,
    }

class ProgramInputs(BaseModel):
    objective: str = ""
    topics: List[str] = Field(default_factory=list)
    policy_summary: str = ""
    constraints: Dict[str, object] = Field(default_factory=dict)
    tools_selected: List[str] = Field(default_factory=list)

class Deliverable(BaseModel):
    # Contract slot id (snake_case)
    slot: str
    description: str = ""
    content_guidance: Optional[str] = None

    # Contract typing
    _type: Optional[str] = None             # "inline" | "file" (normalized)
    format: Optional[str] = None            # inline only (markdown|text|json|url|xml|yaml|mermaid)
    mime: Optional[str] = None              # file only

    # Artifact text surrogate (mandatory for files in runtime)
    text: Optional[str] = None

    # Optional tool provenance for files
    tool_id: Optional[str] = None

class FileRef(Deliverable):
    filename: str
    filename_hint: Optional[str] = None
    key: Optional[str] = None               # conversation-store key (if rehosted)
    size: Optional[int] = None
    _type: str = "file"

class InlineRef(Deliverable):
    citable: bool = False
    value_preview: str = ""                 # short preview for indexing/UX
    _type: str = "inline"


class ProgramBrief(BaseModel):
    title: str = "Codegen Program"
    language: str = "python"
    codegen_run_id: Optional[str] = None
    inputs: ProgramInputs = Field(default_factory=ProgramInputs)
    deliverables: List[Deliverable] = Field(default_factory=list)
    notes: List[Any] = Field(default_factory=list)

# ---------- Data ----------

@dataclass
class PlannedTool:
    id: str
    # purpose: str
    # params: Dict[str, Any]
    reason: str
    confidence: float

class SlotSpec(BaseModel):
    # IMPORTANT: 'name' must equal the dict key under output_contract
    name: str
    type: Literal["inline", "file"] = "inline"
    description: str = ""
    # inline:
    format: Optional[Literal["markdown","text","json","url","xml","yaml","mermaid", "html", "csv"]] = None
    # file:
    mime: Optional[str] = None
    filename_hint: Optional[str] = None
    # all:
    content_guidance: Optional[str] = Field(
        default=None,
        description=(
            "Single place for tone/sections/size/examples; "
            "source-text & rendering hints; citation policy."
        ),
    )

@dataclass
class SolutionPlan:
    mode: str                 # "codegen" | "react_loop" | "llm_only" | "clarification_only"
    tools: List[PlannedTool]  # subset of candidates with concrete params
    confidence: float
    reasoning: str
    clarification_questions: Optional[List[str]] = None
    instructions_for_downstream: Optional[str] = None
    error: Optional[str] = None
    failure_presentation: Optional[dict] = None
    tool_router_notes: Optional[str] = None
    output_contract: Optional[Dict[str, any]] = None  # slot -> description
    service: Optional[dict] = None
    solvable: Optional[bool] = None

    @property
    def tool_selector_internal_thinking(self) -> str:
        tr_service = (self.service or {}).get("tool_router") or {}
        return tr_service.get("internal_thinking")

    @property
    def solvability_internal_thinking(self) -> str:
        sv_service = (self.service or {}).get("solvability") or {}
        return sv_service.get("internal_thinking")

    @property
    def tool_selector_raw_data(self) -> str:
        tr_service = (self.service or {}).get("tool_router") or {}
        return tr_service.get("raw_data")

    @property
    def solvability_raw_data(self) -> str:
        sv_service = (self.service or {}).get("solvability") or {}
        return sv_service.get("raw_data")

    @property
    def tool_selector_error(self) -> str:
        tr_service = (self.service or {}).get("tool_router") or {}
        return tr_service.get("error")

    @property
    def solvability_error(self) -> str:
        sv_service = (self.service or {}).get("solvability") or {}
        return sv_service.get("error")

    def result_interpretation_instruction(self, solved: bool) -> str:
        if not self.solvable:
            return f"The objective is considered as not solvable.\nReasoning: {self.reasoning}.\nMessage for answer consolidator: {self.instructions_for_downstream}"
        if not solved:
            return f"The objective was considered solvable with reasoning: {self.reasoning}.\nIt was not solved (see solver errors). \nMessage for answer consolidator: {self.instructions_for_downstream}"
        return ""

@dataclass
class SolutionExecution:
    error: Optional[str] = None
    failure_presentation: Optional[dict] = None
    out: Optional[dict] = None
    citations: Optional[List[dict]] = None
    calls: Optional[List[dict]] = None  # tool calls made
    deliverables: Optional[Dict[str, Any]] = None  # slot -> {description, value}
    result_interpretation_instruction: Optional[str] = None
    canonical_sources: Optional[List[Dict[str, Any]]] = None

@dataclass
class SolveResult:
    raw: Dict[str, Any]
    _round0: Dict[str, Any] = field(default_factory=dict, init=False)

    # ----- core blocks -----
    @property
    def codegen(self) -> Dict[str, Any]:
        return (self.raw or {}).get("codegen") or {}

    @property
    def plan(self) -> SolutionPlan:
        return self.raw.get("plan")

    @property
    def execution(self) -> SolutionExecution:
        return self.raw.get("execution")

    @property
    def failure(self):
        """Error message if the solve failed."""
        return self.plan.error or (not self.execution and self.plan.mode not in ["llm_only", "clarification_only"]) or (self.execution and self.execution.error)

    @property
    def partial_failure(self) -> bool:
        """
        True if solver completed SOME deliverables but not all contract slots.
        Indicates recoverable partial success where completed artifacts should be preserved.
        """
        if not self.execution or not self.execution.deliverables:
            return False

        contract = (self.plan.output_contract if self.plan else {}) or {}
        if not contract:
            return False

        # Count completed (non-draft, non-missing) slots
        complete = [
            slot for slot, spec in self.execution.deliverables.items()
            if isinstance((spec or {}).get("value"), dict) and not (spec or {}).get("value", {}).get("draft")
        ]

        # Partial = some completed AND some missing
        return len(complete) > 0 and len(complete) < len(contract)

    @staticmethod
    def status(sr) -> Optional[Literal["success", "partial", "failed", "llm_only", "not_solvable"]]:
        if not sr:
            return None
        if sr.plan:
            if sr.plan.mode == "llm_only":
                return "llm_only"
            if sr.plan.solvable is False:
                return "not_solvable"

        has_delivs = bool(sr.execution and sr.execution.deliverables)

        if has_delivs:
            if sr.partial_failure:
                return "partial"
            # 🔧 if there is an error flag but deliverables exist, keep them by reporting partial
            if sr.failure:
                return "partial"
            return "success"

        # no deliverables → failed if any failure signal
        if sr.failure:
            return "failed"
        return None

    @property
    def program_presentation(self):
        return _build_program_presentation_for_answer_agent(
            sr=self,
            citations=None,  # self.citations(),
            codegen_run_id=self.run_id(),
            include_unused_citations=False,  # ← Filter unused
        )

    @property
    def program_presentation_ext(self):
        return _build_program_presentation_for_answer_agent(
            sr=self,
            citations=None,
            codegen_run_id=self.run_id(),
            extended=True,
            include_unused_citations=False,  # ← Filter unused
        )

    def program_brief(self, rehosted_files: List[dict]) -> Tuple[str, ProgramBrief]:
        return _program_brief_from_contract(self, rehosted_files=rehosted_files)

    @property
    def failure_presentation(self):
        """Structured failure payload (if any) from plan or execution."""
        if self.plan and self.plan.failure_presentation:
            return self.plan.failure_presentation
        exec_ = self.execution
        return exec_.failure_presentation if exec_ and exec_.failure_presentation else None

    def rounds(self) -> List[Dict[str, Any]]:
        return self.codegen.get("rounds") or []

    def result_json(self) -> Optional[Dict[str, Any]]:
        r = self._first_round()
        try:
            items = ((r.get("outputs") or {}).get("items") or [])
            for it in items:
                if it.get("filename") == "result.json" and isinstance(it.get("data"), dict):
                    return it.get("data")
        except Exception:
            pass
        return None

    # ----- out accessors (result.json as source of truth) -----
    def out_items(self) -> List[Dict[str, Any]]:
        """Everything that codegen produced under result.json['out']."""
        rj = self.result_json() or {}
        arr = rj.get("out") or []
        return arr if isinstance(arr, list) else []

    def execution_id(self) -> Optional[str]:
        """Execution/session id emitted by codegen (if present in result.json)."""
        rj = self.result_json() or {}
        eid = rj.get("execution_id")
        if isinstance(eid, str) and eid.strip():
            return eid.strip()
        # fallback to codegen run_id if not present
        return self.run_id()

    # ----- contract/deliverables -----
    def deliverables_map(self) -> Dict[str, Any]:
        """
        Standardized structure returned by CodegenToolManager.solve():
          { slot_name: { "description": str, "value": <artifact dict or None> }, ... }
        NOTE: 'value' is a SINGLE artifact dict for the slot (not a list).
        """
        exec_ = self.execution
        return {} if exec_ is None or exec_.deliverables is None else exec_.deliverables

    def deliverables_out(self) -> List[Dict[str, Any]]:
        """Flattened SINGLE artifact per slot (if present)."""
        out: List[Dict[str, Any]] = []
        for _, spec in (self.deliverables_map() or {}).items():
            val = (spec or {}).get("value")
            if isinstance(val, dict):
                out.append(val)
        return out

    # ----- reasoning & hints from the first codegen round -----
    def interpretation_instruction(self) -> str:
        exec_instruction = self.execution.result_interpretation_instruction if self.execution else ""
        if exec_instruction:
            return exec_instruction
        return self.plan.result_interpretation_instruction(False) if self.plan.mode not in ["llm_only", "clarification_only"] else ""

    def round_reasoning(self) -> str:
        r = self._first_round()
        return r.get("internal_thinking") or ""

    def round_notes(self) -> str:
        """Return the last non-empty note string from the first round, if any."""
        r = self._first_round()
        notes = r.get("notes")
        if isinstance(notes, list):
            for s in reversed(notes):
                if isinstance(s, str) and s.strip():
                    return s.strip()
        if isinstance(notes, str):
            return notes.strip()
        return ""

    # ----- derived fields -----
    def run_id(self) -> Optional[str]:
        cg = self.codegen
        rid = cg.get("run_id") if cg else None
        if rid:
            return rid
        r = self._first_round()
        return r.get("run_id") if isinstance(r, dict) else None

    def outdir_workdir(self) -> Tuple[Optional[str], Optional[str]]:
        r = self._first_round()
        return (r.get("outdir"), r.get("workdir"))

    def canonical_sources(self) -> List[Dict[str, Any]]:
        """
        Raw canonical sources.

        NEW: prefer execution.canonical_sources (thin snapshot),
        fall back to result.json['canonical_sources'] for older payloads.
        """
        exec_ = self.execution
        if exec_ and getattr(exec_, "canonical_sources", None):
            canon = exec_.canonical_sources or []
            return [c for c in canon if isinstance(c, dict)]

        # Legacy path (old stored payloads)
        rj = self.result_json() or {}
        canon = rj.get("canonical_sources") or []
        return [c for c in canon if isinstance(c, dict)]

    def canonical_sources_map(self) -> Dict[int, Dict[str, Any]]:
        """
        Convenience map: sid -> canonical source dict.
        """
        out: Dict[int, Dict[str, Any]] = {}
        for rec in self.canonical_sources():
            sid = rec.get("sid")
            if isinstance(sid, int) and sid not in out:
                out[sid] = rec
        return out

    def _collect_used_from_deliverables(self) -> Tuple[set, set]:
        """
        Returns (used_sids, used_urls) gathered from deliverables.
        - Prefer `sources_used` objects (grab sid + url).
        - Also consider `sources_used_sids` (SIDs only).
        """
        dmap = self.deliverables_map() or {}
        used_sids, used_urls = set(), set()
        slots = 0

        for _, spec in (dmap.items() if isinstance(dmap, dict) else []):
            slots += 1
            art = (spec or {}).get("value") or {}

            # objects w/ sid+url
            for rec in (art.get("sources_used") or []):
                if not isinstance(rec, dict):
                    continue

                sid = rec.get("sid")
                if sid is not None:
                    try:
                        used_sids.add(int(sid))
                    except Exception:
                        pass

                url_raw = (rec.get("url") or "").strip()
                if url_raw:
                    used_urls.add(normalize_url(url_raw))

            # bare sid list
            for sid in (art.get("sources_used_sids") or []):
                try:
                    used_sids.add(int(sid))
                except Exception:
                    pass

        log.info(
            "citations/used: from deliverables → slots=%d sids=%d urls=%d",
            slots, len(used_sids), len(used_urls),
        )
        return used_sids, used_urls

    def citations_with_usage(self) -> List[Dict[str, Any]]:
        """
        Unified view: all canonical citations, each annotated with used: True/False.

        - All citations = result.json['canonical_sources'].
        - Used = deliverables' sources_used / sources_used_sids.
        """
        all_canonical = self.canonical_sources()
        used_sids, used_urls = (
            self._collect_used_from_deliverables()
            if self.deliverables_map()
            else (set(), set())
        )

        out: List[Dict[str, Any]] = []
        used_count = unused_count = 0

        for rec in all_canonical:
            # copy so we don't mutate the original canonical entry
            row = dict(rec)
            sid = row.get("sid")
            url_raw = row.get("url") or ""
            # normalize only for comparison, do not write back
            url_norm = normalize_url(url_raw) if url_raw else ""

            used = False
            if isinstance(sid, int) and sid in used_sids:
                used = True
            elif url_norm and url_norm in used_urls:
                used = True

            row["used"] = used
            out.append(row)
            used_count += int(used)
            unused_count += int(not used)

        if unused_count:
            for r in out:
                if not r.get("used"):
                    sid = r.get("sid")
                    title = r.get("title") or r.get("url") or "(untitled)"
                    log.info("citations/unused: SID=%s Title=%s URL=%s", sid, title, r.get("url"))

        log.info(
            "citations/with_usage: canonical=%d used=%d unused=%d",
            len(all_canonical), used_count, unused_count,
        )
        return out

    def citations(self) -> List[Dict[str, Any]]:
        """
        All citations for this turn, deliverables-agnostic.

        Simply the canonical_sources list (result.json['canonical_sources']),
        already normalized and sorted.
        """
        return self.canonical_sources()

    def citations_used_only(self) -> List[Dict[str, Any]]:
        """
        Only citations actually referenced by deliverables (used=True).
        """
        rows = [c for c in (self.citations_with_usage() or []) if c.get("used")]
        log.debug("citations_used_only: %d", len(rows))
        return rows

    def indexable_tool_ids(self) -> Set[str]:
        """
        Tools we might want to index (tags-only) from result.json style items.
        Keep it conservative and only pick obvious search-like tools.
        """
        def _is_search(tid: str) -> bool:
            tid = (tid or "").lower()
            return tid.endswith(".web_search") or tid.endswith(".kb_search")
        return {it.get("tool_id") for it in self.out_items() if _is_search(it.get("tool_id") or "")} - {None}

    # ----- helpers -----
    def _first_round(self) -> Dict[str, Any]:
        if self._round0:
            return self._round0
        rr = self.rounds()
        self._round0 = (rr[0] if rr else {}) if isinstance(rr, list) else {}
        return self._round0

    async def enrich_used_citations_with_favicons(self) -> int:
        """
        Enrich canonical sources (used ones) with favicons in-place.

        Uses the shared module-level link preview instance automatically.
        This modifies the canonical_sources list directly, so all views
        (citations(), citations_used_only(), etc.) will reflect the enrichment.

        Returns:
            Number of sources that were newly enriched
        """
        canonical = self.canonical_sources()
        if not canonical:
            return 0

        # Filter to only used sources
        used_sids, used_urls = self._collect_used_from_deliverables()

        sources_to_enrich = []
        for src in canonical:
            sid = src.get("sid")
            url_raw = src.get("url") or ""
            url_norm = normalize_url(url_raw) if url_raw else ""

            is_used = False
            if isinstance(sid, int) and sid in used_sids:
                is_used = True
            elif url_norm and url_norm in used_urls:
                is_used = True

            if is_used:
                sources_to_enrich.append(src)

        if not sources_to_enrich:
            log.debug("enrich_used_citations_with_favicons: no used sources to enrich")
            return 0

        # Enrich in-place using shared instance
        return await enrich_canonical_sources_with_favicons(sources_to_enrich, log=log)

def _program_brief_from_contract(sr: SolveResult,
                                 rehosted_files: List[dict]) -> Tuple[str, ProgramBrief]:
    """
    Returns: (brief_text, ProgramBrief)

    - Reads artifacts exactly as produced by io_tools normalization (execution.deliverables[slot]['value']).
    - Uses plan.output_contract only for human-facing description and optional content_guidance.
    - Does NOT infer or require any coordinator/unified-only fields inside artifacts.
    """
    sr = sr or {}
    codegen = sr.codegen or {}
    plan = sr.plan
    execution = sr.execution

    contract = (plan.output_contract if plan else {}) or {}
    deliverables_map = (execution.deliverables if execution else {}) or {}

    # ---- program metadata
    program = (codegen.get("program") or {})
    title = (program.get("title") or "").strip() or "Codegen Program"
    language = (program.get("language") or "python").strip() or "python"

    rounds = (codegen.get("rounds") or [])
    latest_round = next((r for r in reversed(rounds) if isinstance(r, dict)), {}) if rounds else {}
    if not title:
        nt = (latest_round.get("notes") or "")
        if isinstance(nt, str) and nt.strip():
            title = nt.strip()[:120]

    inputs_raw = (latest_round.get("inputs") or {})
    inputs = ProgramInputs(
        objective=inputs_raw.get("objective") or "",
        topics=list(inputs_raw.get("topics") or []),
        policy_summary=inputs_raw.get("policy_summary") or "",
        constraints=dict(inputs_raw.get("constraints") or {}),
        tools_selected=list(inputs_raw.get("tools_selected") or []),
    )

    # ---- fold deliverables into the typed list for ProgramBrief (view only)
    struct_delivs: List[Deliverable] = []
    for slot, row in (deliverables_map.items() if isinstance(deliverables_map, dict) else []):
        spec = row or {}
        art = spec.get("value") or {}
        slot_type = (art.get("type") or spec.get("type") or "inline").strip().lower()
        desc = spec.get("description") or (contract.get(slot, {}) or {}).get("description") or ""
        guidance = spec.get("content_guidance") or (contract.get(slot, {}) or {}).get("content_guidance")

        # inline
        if slot_type == "inline":
            text = ((art.get("output") or {}).get("text") or "")
            struct_delivs.append(InlineRef(
                slot=slot,
                description=desc,
                content_guidance=guidance,
                citable=bool(art.get("citable")),
                value_preview=(str(text)[:280] if isinstance(text, str) else ""),
                text=str(text) if isinstance(text, (str, int, float)) else json.dumps(text, ensure_ascii=False),
                tool_id=art.get("tool_id"),
                mime="application/json",  # presentation value only
                _type="inline",
            ))
            continue

        # file
        if slot_type == "file":
            output = art.get("output") or {}
            path = output.get("path") or ""
            filename = path.split("/")[-1] if path else (art.get("filename") or slot)
            struct_delivs.append(FileRef(
                slot=slot,
                description=desc,
                content_guidance=guidance,
                tool_id=art.get("tool_id"),
                mime=art.get("mime"),
                text=output.get("text") or "",
                filename=filename,
                key=art.get("key"),
                size=art.get("size"),
                _type="file",
            ))
            continue

        # fallback (treat unknown as inline text)
        text = ((art.get("output") or {}).get("text") or "")
        struct_delivs.append(InlineRef(
            slot=slot,
            description=desc,
            content_guidance=guidance,
            citable=bool(art.get("citable")),
            value_preview=(str(text)[:280] if isinstance(text, str) else ""),
            text=str(text) if isinstance(text, (str, int, float)) else json.dumps(text, ensure_ascii=False),
            tool_id=art.get("tool_id"),
            mime="application/json",
            _type="inline",
        ))

    # (optional) include rehosted files metadata for UX/debug — kept as FileRef but this
    # does not alter the authoritative artifacts; it's additive for the brief only.
    for rf in (rehosted_files or []):
        struct_delivs.append(FileRef(
            slot=rf.get("slot") or "",
            description=rf.get("description") or "",
            content_guidance=rf.get("content_guidance"),
            tool_id=rf.get("tool_id"),
            mime=rf.get("mime"),
            text=rf.get("text") or "",
            filename=rf.get("filename") or "",
            key=rf.get("key"),
            size=rf.get("size"),
            _type="file",
        ))

    brief_struct = ProgramBrief(
        title=title[:120],
        language=language,
        codegen_run_id=codegen.get("run_id"),
        inputs=inputs,
        deliverables=struct_delivs,
        notes=list(latest_round.get("notes") or [] if isinstance(latest_round.get("notes"), list)
                   else ([latest_round.get("notes")] if latest_round.get("notes") else []))
    )

    # ---- compact, deterministic text
    lines: List[str] = []
    lines.append(f"# {brief_struct.title}")
    lines.append(f"- Language: {brief_struct.language}")
    if brief_struct.codegen_run_id:
        lines.append(f"- Run: {brief_struct.codegen_run_id}")

    # Inputs
    lines.append("- Objective:" + (f" {inputs.objective}" if inputs.objective else ""))
    lines.append("- Topics:")
    for t in inputs.topics:
        lines.append(f"  - {t}")
    lines.append("- Policy Summary:" + (f" {inputs.policy_summary}" if inputs.policy_summary else ""))
    lines.append("- Constraints:")
    if inputs.constraints:
        for k in sorted(inputs.constraints):
            v = inputs.constraints[k]
            try:
                vv = json.dumps(v, ensure_ascii=False) if not isinstance(v, (str, int, float, bool)) else v
            except Exception:
                vv = str(v)
            lines.append(f"  - {k}: {vv}")
    lines.append("- Tools Selected:")
    for tool in inputs.tools_selected:
        lines.append(f"  - {tool}")

    lines.append("\n## Notes:")
    for note in brief_struct.notes:
        lines.append(f"  - {note}")

    # Deliverables section (reflect what was actually produced)
    if deliverables_map:
        lines.append("\n## Deliverables")
        for slot, spec in deliverables_map.items():
            art = (spec or {}).get("value") or {}
            desc = (spec or {}).get("description") or ""
            slot_type = (art.get("type") or spec.get("type") or "inline")
            is_draft = bool(art.get("draft"))
            gap = art.get("gaps")
            has_gap = bool(gap)

            lines.append(f"- {slot}{' [DRAFT]' if is_draft else ''}: {desc}")

            guidance = (spec or {}).get("content_guidance")
            if guidance:
                lines.append(f"  - guidance: {guidance}")
            if has_gap:
                lines.append(f"  - gap: {gap}")

            if slot_type == "file":
                out = art.get("output") or {}
                fname = (out.get("path") or "").split("/")[-1]
                parts = []
                if fname:
                    parts.append(f"file={fname}")
                if art.get("mime"):
                    parts.append(f"mime={art.get('mime')}")
                if parts:
                    lines.append("  - " + "; ".join(parts))
            elif slot_type == "inline":
                fmt = art.get("format")
                if fmt:
                    lines.append(f"  - format: {fmt}")

    brief_text = "\n".join(lines).rstrip()
    return brief_text, brief_struct

def _last_non_empty_note(notes) -> str:
    if isinstance(notes, list):
        for s in reversed(notes):
            if isinstance(s, str) and s.strip():
                return s.strip()
    if isinstance(notes, str):
        return notes.strip()
    return ""

def _build_program_presentation_for_answer_agent(
        *,
        sr: SolveResult,
        citations: Optional[List[Dict[str, Any]]] = None,
        codegen_run_id: Optional[str] = None,
        include_reasoning: bool = True,
        extended: bool = False,
        include_unused_citations: bool = False,  # control unused citations
) -> str:
    def _artifact_text(art: Dict[str, Any]) -> str:
        if not isinstance(art, dict):
            return ""
        output = art.get("output") or {}
        return (output.get("text") or "").strip()

    def _first_round_note() -> str:
        r0 = (sr.rounds() or [{}])[0]
        return _last_non_empty_note(r0.get("notes"))

    lines: List[str] = []
    lines.append("# Program Presentation")
    if codegen_run_id:
        lines.append(f"_Run ID: `{codegen_run_id}`_")

    # Optional reasoning
    if include_reasoning:
        reasoning = sr.round_reasoning()
        if reasoning:
            lines.append("\n## Solver reasoning (for this turn)")
            lines.append(reasoning)

    # Pull deliverables map once
    dmap = sr.deliverables_map() or {}
    contract = (sr.plan.output_contract if sr.plan else {}) or {}

    # --- Project Log ---
    log_spec = dmap.get("project_log") or {}
    # TODO!!
    log_art = (log_spec.get("value") if isinstance(log_spec, dict) else None) or {}
    # log_art = (log_spec.get("output") if isinstance(log_spec, dict) else None) or {}
    log_body = _artifact_text(log_art)
    lines.append("\n## Solver project log")
    lines.append("```")
    lines.append(log_body if log_body else "(empty)")
    lines.append("```")

    # --- Produced Slots (exclude canvas/log) ---
    lines.append("\n### Produced slots")
    is_there_draft = False
    for slot, spec in (dmap.items() if isinstance(dmap, dict) else []):
        if slot in {"project_canvas", "project_log"}:
            continue
        spec = spec or {}
        desc = spec.get("description") or ""
        art = spec.get("value")
        art = art if isinstance(art, dict) else {}
        slot_type = (art.get("type") or spec.get("type") or "inline").strip().lower()
        is_draft = bool(art.get("draft"))  # Check draft status
        gap = art.get("gaps")
        has_gap = bool(gap)
        is_there_draft = is_there_draft or is_draft
        # optional planner-only guidance for display
        guidance = spec.get("content_guidance") or (contract.get(slot, {}) or {}).get("content_guidance") or ""

        # Mark drafts clearly in header
        draft_marker = " [DRAFT — INCOMPLETE]" if is_draft else ""
        lines.append(f"\n### {slot} ({slot_type}){draft_marker}")
        lines.append(f"Description: {desc}" if desc else "Description:")
        if guidance:
            lines.append(f"Content guidance: {guidance}")

        if slot_type == "file":
            fname = (art.get("path") or art.get("filename") or "").split("/")[-1]
            mime = art.get("mime") or ""
            if fname:
                lines.append(f"Filename: {fname}")
            if mime:
                lines.append(f"Mime: {mime}")
            # Note for drafts
            if is_draft:
                lines.append("Status: File rendering incomplete; text representation available below")
            if has_gap:
                lines.append(f"Gap: {gap}")
        else:
            if art.get("format"):
                lines.append(f"Format: {art.get('format')}")
        # Add draft explanation for answer agent
        if is_there_draft:
            lines.append("⚠️ Deliverables marked with [DRAFT — INCOMPLETE] mean that that deliverable is incomplete.** Its content represents partial progress; final rendering/processing was not completed.")

        if extended:
            lines.append("#### Text repr")
            body = _artifact_text(art)
            lines.append("```")
            lines.append(body if body else "(empty)")
            lines.append("```")

    # How to interpret
    rii = sr.interpretation_instruction()
    if rii:
        lines.append("\n## How to interpret these results")
        lines.append(rii.strip())

    # Notes
    last_note = _first_round_note()
    if last_note:
        lines.append("\n## Solver notes")
        lines.append(last_note)

    # Citations (up to 50) — keep as compact bullets for navigation
    # Citations - filter by usage
    citations_to_show = citations if citations else sr.citations_with_usage()
    # Filter out unused if requested
    if not include_unused_citations:
        citations_to_show = [c for c in citations_to_show if c.get("used", True)]

    if citations_to_show:
        uniq = {}
        for c in citations_to_show:
            u = (c or {}).get("url") or ""
            sid = c.get("sid")
            if u and u not in uniq and sid is not None:
                # Store both title and sid
                uniq[u] = {
                    "title": (c or {}).get("title") or "",
                    "sid": sid
                }
        if uniq:
            lines.append("\n## Citations")
            for url, info in itertools.islice(uniq.items(), 50):
                title = info["title"]
                sid = info["sid"]
                # Include the citation token so answer agent can reference it
                lines.append(f"- [{title}]({url}) [[S:{sid}]]")

    return "\n".join(lines).strip()

def _sd(d: Any) -> Dict[str, Any]:
    return d if isinstance(d, dict) else {}

def _sl(v: Any) -> List[Any]:
    return v if isinstance(v, list) else []

def _mk_planned_tool(d: Dict[str, Any]) -> PlannedTool:
    return PlannedTool(
        id=d.get("id") or "",
        # purpose=d.get("purpose") or "",
        # params=_sd(d.get("params")),
        reason=d.get("reason") or "",
        confidence=float(d.get("confidence") or 0.0),
    )

def ensure_contract_dict(output_contract: dict | None) -> dict:
    """
    Normalize output_contract to a plain dict-of-dicts:
      { slot: {type, description, format?, mime?, filename_hint?, content_guidance?}, ... }
    Accepts values that may be pydantic models (SlotSpec), dataclasses, or plain dicts.
    """
    out: dict[str, dict] = {}
    for k, v in (output_contract or {}).items():
        if hasattr(v, "model_dump"):
            out[k] = v.model_dump()
        elif isinstance(v, dict):
            out[k] = v
        else:
            try:
                # dataclass?
                from dataclasses import is_dataclass, asdict
                out[k] = asdict(v) if is_dataclass(v) else dict(v)  # may raise
            except Exception:
                # last resort: best-effort projection
                out[k] = {
                    "type": getattr(v, "type", "inline"),
                    "description": getattr(v, "description", "") or "",
                    "format": getattr(v, "format", None),
                    "mime": getattr(v, "mime", None),
                    "filename_hint": getattr(v, "filename_hint", None),
                    "content_guidance": getattr(v, "content_guidance", None),
                }
    return out

def _mk_solution_plan(d: Dict[str, Any]) -> SolutionPlan:
    tools = [_mk_planned_tool(x) for x in _sl(d.get("tools"))]
    # 🔽 normalize contract coming from persisted payloads
    raw_contract = _sd(d.get("output_contract"))
    return SolutionPlan(
        mode=d.get("mode") or "llm_only",
        tools=tools,
        confidence=float(d.get("confidence") or 0.0),
        reasoning=d.get("reasoning") or "",
        clarification_questions=_sl(d.get("clarification_questions")),
        instructions_for_downstream=d.get("instructions_for_downstream"),
        error=d.get("error"),
        failure_presentation=_sd(d.get("failure_presentation")),
        tool_router_notes=d.get("tool_router_notes"),
        output_contract=ensure_contract_dict(raw_contract),  # ← always dict-of-dict
        service=_sd(d.get("service")),
        solvable=d.get("solvable"),
    )

def _mk_solution_execution(d: Dict[str, Any]) -> SolutionExecution:
    return SolutionExecution(
        error=d.get("error"),
        failure_presentation=_sd(d.get("failure_presentation")),
        out=_sd(d.get("out")),
        citations=_sl(d.get("citations")),
        calls=_sl(d.get("calls")),
        deliverables=_sd(d.get("deliverables")),
        result_interpretation_instruction=d.get("result_interpretation_instruction"),
        canonical_sources=_sl(d.get("canonical_sources")),
    )

def solve_result_from_full_payload(full: Dict[str, Any]) -> SolveResult:
    """
    Recreate SolveResult (original dataclasses) from the dict you persisted via build_full_solver_payload().
    """
    full = _sd(full)

    # Prefer the already-structured inner blocks when present
    plan_dict = _sd(full.get("plan"))
    exec_dict = _sd(full.get("execution"))
    # codegen = _sd(full.get("codegen"))

    plan = _mk_solution_plan(plan_dict) if plan_dict else None
    execution = _mk_solution_execution(exec_dict) if exec_dict else None

    # SolveResult expects raw={"codegen":..., "plan": SolutionPlan, "execution": SolutionExecution}
    raw = {
        # "codegen": codegen,
        "plan": plan,
        "execution": execution,
    }
    return SolveResult(raw=raw)


def build_full_solver_payload(sr) -> Dict[str, Any]:
    """
    Produce a 'solver' block that embeds the full, JSON-safe SolveResult,
    plus denormalized helpers for UI. No cutting of project_log / tlog.
    """
    # Core
    if not sr:
        return {}

    plan = _to_jsonable(sr.plan)
    execution = _to_jsonable(sr.execution)
    # Make sure execution.deliverables no longer has `sources_used`
    execution = _filter_execution(execution)

    failure_presentation = _to_jsonable(sr.failure_presentation)
    failure = _to_jsonable(sr.failure)

    # Derived / convenience
    program_presentation = sr.program_presentation            # full markdown

    exec_id = sr.execution_id()
    run_id = sr.run_id()
    outdir, workdir = sr.outdir_workdir()
    interpretation_instruction = sr.interpretation_instruction()
    indexable_tool_ids = sorted(sr.indexable_tool_ids())

    return {
        "version": 1,
        "ok": not bool(failure),
        "meta": {
            "run_id": run_id,
            "execution_id": exec_id,
            "outdir": outdir or "",
            "workdir": workdir or "",
            "indexable_tool_ids": indexable_tool_ids,
        },
        # Raw/structured core (JSON-safe)
        "plan": plan,
        "execution": execution,

        # Denormalized helpers for views
        "failure": failure,                                 # truthy on any failure
        "failure_presentation": failure_presentation,       # structured failure payload
        "interpretation_instruction": interpretation_instruction,

        # Presentations (inline, not pointers)
        "program_presentation": program_presentation,
    }

def _filter_execution(execution: Any) -> Any:
    """
    Given JSON-safe `execution`, return a copy where each deliverable's
    value.sources_used is removed, but sources_used_sids is preserved.

    Safe to call on the result of _to_jsonable(sr.execution);
    does not mutate sr.execution.
    """
    if not isinstance(execution, dict):
        return execution

    exec_copy = dict(execution)

    deliverables = exec_copy.get("deliverables")
    if not isinstance(deliverables, dict):
        return exec_copy

    new_delivs: Dict[str, Any] = {}

    for slot, spec in deliverables.items():
        if not isinstance(spec, dict):
            new_delivs[slot] = spec
            continue

        spec_copy = dict(spec)
        art = spec_copy.get("value")

        if isinstance(art, dict):
            art_copy = dict(art)
            # Drop heavy duplication; keep SIDs
            art_copy.pop("sources_used", None)
            spec_copy["value"] = art_copy

        new_delivs[slot] = spec_copy

    exec_copy["deliverables"] = new_delivs
    if "calls" in exec_copy:
        del exec_copy["calls"]
    return exec_copy


