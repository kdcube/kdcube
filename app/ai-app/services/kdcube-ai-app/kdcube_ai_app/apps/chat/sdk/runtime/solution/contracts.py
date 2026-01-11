# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/contracts.py

from __future__ import annotations
from pydantic import BaseModel, Field
import json, logging
import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Literal

from kdcube_ai_app.apps.chat.sdk.runtime.solution.presentation import SolverPresenter, SolverPresenterConfig, \
    build_runtime_inventory_from_artifact, ProgramBrief, program_brief_from_contract
from kdcube_ai_app.apps.chat.sdk.tools.citations import normalize_url, enrich_sources_pool_with_favicons
from kdcube_ai_app.infra.service_hub.cache import create_namespaced_kv_cache
from kdcube_ai_app.apps.chat.sdk.util import _to_jsonable

log = logging.getLogger(__name__)

SERVICE_LOG_SLOT = "project_log"

def _service_log_contract_entry() -> dict:
    # Hidden service slot: text-only, markdown, filled by set_progress()
    return {
        "type": "inline",
        "description": "Live run log",
        "format": "markdown",
        "content_guidance": "",
        "_hidden": True,
    }

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
    # structured content inventory hint/payload for this slot
    content_inventorization: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional structured content inventory/schema for this slot.",
    )


@dataclass
class SolutionPlan:
    mode: str                 # "codegen" | "react_loop" | "llm_only" | "clarification_only"
    tools: List[PlannedTool]  # subset of candidates with concrete params
    confidence: float
    reasoning: str
    clarification_questions: Optional[List[str]] = None
    instructions_for_downstream: Optional[str] = None
    instructions_for_downstream_compact: Optional[str] = None
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
    sources_pool: Optional[List[Dict[str, Any]]] = None

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
        presenter = SolverPresenter(self, codegen_run_id=self.run_id(), file_path_prefix="")
        return presenter.full_view(include_reasoning=True, extended=False)

    @property
    def program_presentation_ext(self):
        presenter = SolverPresenter(self, codegen_run_id=self.run_id(), file_path_prefix="")
        return presenter.full_view(include_reasoning=True, extended=True)

    def program_brief(self, rehosted_files: List[dict]) -> Tuple[str, ProgramBrief]:
        return program_brief_from_contract(self, rehosted_files=rehosted_files)

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

    def sources_pool(self) -> List[Dict[str, Any]]:
        """
        Raw sources_pool from the per-turn execution.
        """
        exec_ = self.execution
        if exec_ and getattr(exec_, "sources_pool", None):
            pool = exec_.sources_pool or []
            return [c for c in pool if isinstance(c, dict)]
        return []

    def sources_pool_map(self) -> Dict[int, Dict[str, Any]]:
        """
        Convenience map: sid -> source dict.
        """
        out: Dict[int, Dict[str, Any]] = {}
        for rec in self.sources_pool():
            sid = rec.get("sid")
            if isinstance(sid, int) and sid not in out:
                out[sid] = rec
        return out

    def _collect_used_from_deliverables(self) -> Tuple[set, set]:
        """
        Returns (used_sids, used_urls) from sources_pool only.
        We no longer infer usage from deliverables.
        """
        used_sids, used_urls = set(), set()
        for rec in self.sources_pool():
            if not isinstance(rec, dict):
                continue
            if not rec.get("used"):
                continue
            sid = rec.get("sid")
            if isinstance(sid, int):
                used_sids.add(sid)
            url_raw = (rec.get("url") or "").strip()
            if url_raw:
                used_urls.add(normalize_url(url_raw))

        log.info(
            "citations/used: from sources_pool → sids=%d urls=%d",
            len(used_sids), len(used_urls),
        )
        return used_sids, used_urls

    def citations_with_usage(self) -> List[Dict[str, Any]]:
        """
        Unified view: all canonical citations, each annotated with used: True/False.

        - All citations = execution.sources_pool.
        - Used = sources_pool entries marked with used=True.
        """
        all_canonical = self.sources_pool()
        used_sids, used_urls = self._collect_used_from_deliverables()

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

        Simply the sources_pool list (execution.sources_pool),
        already normalized and sorted.
        """
        return self.sources_pool()

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
        This modifies the sources_pool list directly, so all views
        (citations(), citations_used_only(), etc.) will reflect the enrichment.

        Returns:
            Number of sources that were newly enriched
        """
        canonical = self.sources_pool()
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

        # Enrich in-place using shared instance (with cache if available)
        cache = None
        try:
            cache = create_namespaced_kv_cache()
        except Exception:
            cache = None
        return await enrich_sources_pool_with_favicons(sources_to_enrich, log=log, cache=cache)



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
        sources_pool=_sl(d.get("sources_pool")),
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
    normalized_deliverables = normalize_deliverables_map(sr.deliverables_map() or {})
    execution = _build_execution_payload(sr.execution, normalized_deliverables)

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

def _normalize_sources_used(sources: Any) -> List[int]:
    out: List[int] = []
    if isinstance(sources, list):
        for s in sources:
            if isinstance(s, (int, float)) and int(s) not in out:
                out.append(int(s))
            elif isinstance(s, dict):
                sid = s.get("sid")
                if isinstance(sid, (int, float)) and int(sid) not in out:
                    out.append(int(sid))
    return out

def _normalize_deliverable_value(val: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(val, dict):
        return None

    def _copy_if_present(dst: Dict[str, Any], src: Dict[str, Any], keys: List[str]) -> None:
        for k in keys:
            if k in src and src.get(k) is not None:
                dst[k] = src.get(k)

    if val.get("type") == "file":
        v_orig = (val.get("output") or {}).get("text") or val.get("text") or ""
        fname = (
            (val.get("output") or {}).get("path")
            or val.get("path")
            or val.get("filename")
            or ""
        )
        fname = fname.split("/")[-1] if isinstance(fname, str) else ""
        hosted_uri = val.get("hosted_uri")
        hosted_key = val.get("key")
        value_obj: Dict[str, Any] = {
            "type": "file",
            "filename": fname,
            "text": v_orig or val.get("text"),
            "mime": val.get("mime"),
            "path": hosted_key or hosted_uri or val.get("path"),
            "key": hosted_key,
            "rn": val.get("rn"),
            "hosted_uri": hosted_uri,
            "description": val.get("description") or "",
            "tool_id": val.get("tool_id") or "",
            "summary": val.get("summary"),
            "sources_used": _normalize_sources_used(val.get("sources_used") or []),
            "citable": val.get("citable") or False,
        }
        _copy_if_present(value_obj, val, ["draft", "gaps", "artifact_id", "mapped_from", "format"])
        return value_obj

    v_orig = (val.get("output") or {}).get("text") or val.get("text") or ""
    if not isinstance(v_orig, str):
        try:
            v_orig = json.dumps(v_orig, ensure_ascii=False)
        except Exception:
            v_orig = str(v_orig)

    value_obj = {
        "type": "inline",
        "mime": val.get("mime"),
        "value": v_orig,
        "text": v_orig or val.get("text"),
        "description": val.get("description") or "",
        "tool_id": val.get("tool_id") or "",
        "summary": val.get("summary"),
        "sources_used": _normalize_sources_used(val.get("sources_used") or []),
        "format": val.get("format") or "",
        "citable": val.get("citable") or False,
    }
    _copy_if_present(value_obj, val, ["draft", "gaps", "artifact_id", "mapped_from"])
    return value_obj

def normalize_deliverables_map(dmap: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize deliverables to a stable, artifact-first representation.

    Output shape:
      { slot: { description, content_guidance?, type?, value }, ... }
    """
    out: Dict[str, Any] = {}
    for slot_name, spec in (dmap or {}).items():
        if not isinstance(spec, dict):
            out[slot_name] = {"description": "", "value": None}
            continue

        desc = spec.get("description") or ""
        content_guidance = spec.get("content_guidance") or ""
        slot_type = spec.get("type") or ""
        value_obj = _normalize_deliverable_value(spec.get("value"))

        spec_out: Dict[str, Any] = {
            "description": desc,
            "value": value_obj,
        }
        if content_guidance:
            spec_out["content_guidance"] = content_guidance
        if slot_type:
            spec_out["type"] = slot_type
        out[slot_name] = spec_out
    return out

def deliverables_items_from_map(dmap: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert normalized deliverables map to list payload used by artifacts.
    """
    items: List[Dict[str, Any]] = []
    for slot_name, spec in (dmap or {}).items():
        if not isinstance(spec, dict):
            items.append({"slot": slot_name, "description": "", "value": None})
            continue
        item = {"slot": slot_name}
        item.update(spec)
        items.append(item)
    return items

def _build_execution_payload(execution: Any, deliverables: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build execution payload with normalized deliverables and without redundant fields.
    """
    if execution is None:
        return {"deliverables": deliverables}

    def _jsonable_detached(val: Any) -> Any:
        return _to_jsonable(val, _seen=set())

    return {
        "error": _jsonable_detached(getattr(execution, "error", None)),
        "failure_presentation": _jsonable_detached(getattr(execution, "failure_presentation", None)),
        "deliverables": deliverables,
        "result_interpretation_instruction": _jsonable_detached(
            getattr(execution, "result_interpretation_instruction", None)
        ),
    }
