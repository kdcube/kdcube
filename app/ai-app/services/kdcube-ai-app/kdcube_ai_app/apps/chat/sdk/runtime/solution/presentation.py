# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/presentation.py

# ============================================================================
# UNIFIED SOLVER PRESENTATION API
# ============================================================================
import itertools
import json
from dataclasses import dataclass
from typing import Literal, Optional, Dict, Any, Set, List, Union, Tuple
from pydantic import BaseModel, Field

from kdcube_ai_app.apps.chat.sdk.util import _to_jsonable

SERVICE_LOG_SLOT = "project_log"   # a.k.a. "turn_log" inside Solver module (normalized to project_log)


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

    # structured content inventory attached to this deliverable
    content_inventorization: Optional[Dict[str, Any]] = None


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


def _artifact_text(art: Dict[str, Any]) -> str:
    """
    Unified way to extract a text representation from an artifact.

    - Prefer art["output"]["text"] when present.
    - Fall back to art["text"].
    - If it's not a string but some structured object, JSON-dump it.
    """
    if not isinstance(art, dict):
        return ""

    output = art.get("output") or {}
    text = output.get("text", None)

    if text is None and art.get("text") is not None:
        text = art.get("text")

    if isinstance(text, str):
        return text

    if text is None:
        return ""

    try:
        return json.dumps(text, ensure_ascii=False, indent=2)
    except Exception:
        return str(text)

# ---------- Program presentation pieces ----------

@dataclass
class ProgramPresentationParts:
    """
    Structured pieces of the per-turn program presentation.

    All *_md fields are markdown snippets that ALREADY contain their own
    section headings (## ...). `as_markdown()` adds the global heading and
    stitches them together.
    """
    reasoning_md: str = ""
    project_log_md: str = ""
    produced_slots_md: str = ""
    interpretation_md: str = ""
    notes_md: str = ""
    citations_md: str = ""
    # optional meta for header (e.g. codegen_run_id / execution_id)
    run_id: Optional[str] = None

    def as_markdown(self, *, include_reasoning: bool = True, extended: bool = False) -> str:
        """
        Stitch all sections into a single markdown document.

        Args:
            include_reasoning: if False, reasoning_md is omitted entirely.
            extended: currently informational only; the caller decides
                      how verbose pieces are when building them.
        """
        lines: list[str] = ["# Program Presentation"]

        if self.run_id:
            lines.append(f"_Run ID: `{self.run_id}`_")

        if include_reasoning and (self.reasoning_md or "").strip():
            lines.append("")
            lines.append(self.reasoning_md.strip())

        for part in [
            self.project_log_md,
            self.produced_slots_md,
            self.interpretation_md,
            self.notes_md,
            self.citations_md,
        ]:
            if part and part.strip():
                lines.append("")
                lines.append(part.strip())

        # we don't need to do anything special with `extended` here:
        # callers build different parts for extended/non-extended views.
        return "\n".join(lines).strip()

@dataclass
class SolverPresenterConfig:
    """
    Configuration for solver result presentation.

    Covers all use cases from context/presentation.py and other clients.
    """
    # --- What sections to include ---
    include_reasoning: bool = False
    include_project_log: bool = False
    include_deliverables: bool = True
    include_interpretation: bool = False
    include_notes: bool = False
    include_citations: bool = False

    # --- Deliverables view style ---
    deliverables_grouping: Literal["status", "flat", "none"] = "status"
    # "status" → grouped by ❌ missing / ⚠️ draft / ✅ completed (uses _format_produced_slots_grouped_by_status)
    # "flat" → simple list without grouping (uses format_deliverables_section)
    # "none" → skip deliverables section entirely

    # --- Content detail level ---
    deliverables_content_len: int = 0
    # -1 = full content
    #  0 = no content (metadata only)
    # >0 = truncate to N chars

    project_log_limit: Optional[int] = None  # None = full, int = truncate
    reasoning_limit: Optional[int] = None

    # --- Which deliverable attributes to show ---
    deliverable_attrs: Optional[Set[str]] = None
    # None = auto (rich set for extended, minimal for base)
    # Supported: "description", "content_guidance", "format", "mime",
    #            "tool_id", "citable", "sources_used_sids",
    #            "gaps", "summary", "slot_value_inventorization", "artifact_id", "mapped_from"

    # --- Filters ---
    exclude_slots: Optional[List[str]] = None
    include_unused_citations: bool = False

    # --- Output format ---
    output_format: Literal["markdown", "parts"] = "markdown"
    # "markdown" → stitched string
    # "parts" → ProgramPresentationParts object

def format_inventory(inv: Any) -> str:
    """
    Render a `content_inventorization` payload as markdown.

    It does **not**:
    - rename fields
    - guess semantics
    - inject extra attributes

    It only turns the structure into human/LLM-friendly markdown.
    """
    if not inv:
        return ""

    lines: list[str] = []

    def _render_scalar(val: Any) -> str:
        if isinstance(val, (int, float, bool)):
            return str(val)
        if isinstance(val, str):
            return val
        try:
            return json.dumps(val, ensure_ascii=False)
        except Exception:
            return str(val)

    if isinstance(inv, dict):
        for section_name, value in inv.items():
            lines.append(f"**{section_name}**")
            if isinstance(value, list):
                if value and all(isinstance(it, dict) for it in value):
                    # list of dicts → table
                    all_keys: list[str] = []
                    for it in value:
                        for k in it.keys():
                            if k not in all_keys:
                                all_keys.append(k)

                    if all_keys:
                        header = "| # | " + " | ".join(all_keys) + " |"
                        sep    = "|---|" + "|".join(["---"] * len(all_keys)) + "|"
                        lines.append(header)
                        lines.append(sep)
                        for idx, it in enumerate(value, 1):
                            row_cells = [_render_scalar(it.get(k)) for k in all_keys]
                            lines.append("| " + str(idx) + " | " + " | ".join(row_cells) + " |")
                else:
                    for idx, it in enumerate(value, 1):
                        lines.append(f"- ({idx}) {_render_scalar(it)}")
            elif isinstance(value, dict):
                lines.append("| key | value |")
                lines.append("|-----|--------|")
                for k, v in value.items():
                    lines.append(f"| {k} | {_render_scalar(v)} |")
            else:
                lines.append(_render_scalar(value))
            lines.append("")  # blank line after each section

    elif isinstance(inv, list):
        if inv and all(isinstance(it, dict) for it in inv):
            all_keys: list[str] = []
            for it in inv:
                for k in it.keys():
                    if k not in all_keys:
                        all_keys.append(k)

            if all_keys:
                lines.append("| # | " + " | ".join(all_keys) + " |")
                lines.append("|---|" + "|".join(["---"] * len(all_keys)) + "|")
                for idx, it in enumerate(inv, 1):
                    row_cells = [_render_scalar(it.get(k)) for k in all_keys]
                    lines.append("| " + str(idx) + " | " + " | ".join(row_cells) + " |")
        else:
            for idx, it in enumerate(inv, 1):
                lines.append(f"- ({idx}) {_render_scalar(it)}")
    else:
        lines.append(_render_scalar(inv))

    return "\n".join(lines).rstrip()

def _slot_status_for_presentation(
        slot: str,
        dmap: Dict[str, Any],
) -> Literal["missing", "draft", "completed"]:
    """
    Classify a slot using the execution.deliverables map.

    - missing   → no artifact dict under value
    - draft     → artifact dict with draft=True
    - completed → artifact dict with draft=False
    """
    spec = (dmap or {}).get(slot) or {}
    art = spec.get("value") if isinstance(spec, dict) else None
    if not isinstance(art, dict):
        return "missing"
    if art.get("draft"):
        return "draft"
    return "completed"

def _last_non_empty_note(notes) -> str:
    if isinstance(notes, list):
        for s in reversed(notes):
            if isinstance(s, str) and s.strip():
                return s.strip()
    if isinstance(notes, str):
        return notes.strip()
    return ""

def build_runtime_inventory_from_artifact(art: dict) -> dict:
    """
    Runtime view of structured content inventory for a deliverable artifact.

    This is intentionally **shape-preserving**:
    - If art["content_inventorization"] is a dict → return a shallow copy.
    - Otherwise → return {}.

    We do **not** inject draft/gaps/summary/char_count/kind here.
    Those remain first-class artifact fields and are rendered separately.
    """
    if not isinstance(art, dict):
        return {}

    raw_inv = art.get("content_inventorization")
    return dict(raw_inv) if isinstance(raw_inv, dict) else {}

def _format_produced_slots_grouped_by_status(
        *,
        dmap: Dict[str, Any],
        contract: Optional[Dict[str, Any]] = None,
        extended: bool = False,
        slot_attr_keys: Optional[Set[str]] = None,
        exclude_slots: Optional[List[str]] = None,
) -> Tuple[str, bool]:
    """
    Render a compact, status-grouped overview of produced/expected slots.

    Pareto / cache-friendly ordering:
      - Preserve contract insertion order first (stable across rounds).
      - Then append extra produced slots in dmap insertion order.
      - Group by status (missing, draft, completed) but preserve the stable slot order inside groups.

    Groups:
      - ❌ Missing   (declared in contract, no artifact)
      - ⚠️ Draft     (artifact exists with draft=True)
      - ✅ Completed (artifact exists, not draft)

    slot_attr_keys controls which per-slot metadata we show. Supported keys:
      "description", "content_guidance", "format", "mime",
      "tool_id", "citable", "sources_used_sids",
      "gaps", "summary", "slot_value_inventorization", "filename",
      "artifact_id", "mapped_from"

    Returns:
      (markdown, has_any_draft)
    """
    contract = contract or {}
    excluded = set(exclude_slots or [])

    # Default attributes: more compact for base view, richer for extended
    if slot_attr_keys is None:
        if extended:
            slot_attr_keys = {
                "description",
                "content_guidance",
                "format",
                "mime",
                "tool_id",
                "citable",
                "sources_used_sids",
                "gaps",
                "summary",
                "slot_value_inventorization",
                "artifact_id",
                "mapped_from",
            }
        else:
            slot_attr_keys = {
                "description",
                "content_guidance",
                "sources_used_sids",
                "slot_value_inventorization",
                "artifact_id",
                "mapped_from",
            }
    else:
        slot_attr_keys = set(slot_attr_keys)

    # ---- Pareto-friendly stable slot ordering ----
    # 1) contract order (stable)
    # 2) then any extra produced slots in dmap insertion order
    ordered_slots: List[str] = []
    seen: Set[str] = set()

    if isinstance(contract, dict):
        for k in contract.keys():  # preserves insertion order
            if k in excluded:
                continue
            ordered_slots.append(k)
            seen.add(k)

    if isinstance(dmap, dict):
        for k in dmap.keys():  # preserves insertion order
            if k in excluded:
                continue
            if k in {SERVICE_LOG_SLOT}:
                continue
            if k in seen:
                continue
            ordered_slots.append(k)
            seen.add(k)

    if not ordered_slots:
        return "\n".join([
            "## Produced slots",
            "",
            "_(no contract slots or deliverables)_",
        ]).rstrip(), False

    status_icon = {
        "missing": "❌",
        "draft": "⚠️",
        "completed": "✅",
    }

    slots_by_status: Dict[str, List[str]] = {"missing": [], "draft": [], "completed": []}
    for slot in ordered_slots:
        st = _slot_status_for_presentation(slot, dmap)
        slots_by_status[st].append(slot)

    has_any_draft = bool(slots_by_status["draft"])

    def _format_one_slot(slot: str, status: str) -> List[str]:
        spec = (dmap or {}).get(slot) or {}
        art = spec.get("value") if isinstance(spec, dict) else None
        art = art if isinstance(art, dict) else None

        aid = (
                (art or {}).get("artifact_id")
                or (art or {}).get("mapped_artifact_id")
                or (art or {}).get("id")
                or (art or {}).get("name")
                or ""
        )
        aid = str(aid).strip()

        c_spec = (contract or {}).get(slot) or {}
        desc = (
                (spec.get("description") if isinstance(spec, dict) else None)
                or c_spec.get("description")
                or ""
        )
        guidance = (
                (spec.get("content_guidance") if isinstance(spec, dict) else None)
                or c_spec.get("content_guidance")
                or ""
        )

        slot_type = (
                (art or {}).get("type")
                or (spec.get("type") if isinstance(spec, dict) else None)
                or c_spec.get("type")
                or "inline"
        )
        slot_type = str(slot_type).strip().lower() or "inline"

        fmt = (
                (art or {}).get("format")
                or (spec.get("format") if isinstance(spec, dict) else None)
                or c_spec.get("format")
        )
        mime = (
                (art or {}).get("mime")
                or (spec.get("mime") if isinstance(spec, dict) else None)
                or c_spec.get("mime")
        )

        # primary type descriptor
        if slot_type == "file":
            type_desc = f"file, {mime}" if mime else "file"
        else:
            type_desc = f"inline, {fmt}" if fmt else (slot_type or "inline")

        icon = status_icon.get(status, "•")
        lines: List[str] = [f"- {icon} `{slot}` ({type_desc})"]

        # artifact id (compact)
        if "artifact_id" in slot_attr_keys and aid:
            lines[0] = lines[0] + f" ← `{aid}`"

        # mapped_from (truncate left as-is; caller may truncate later)
        if "mapped_from" in slot_attr_keys:
            mp = (art or {}).get("mapped_from")
            if isinstance(mp, str) and mp.strip():
                lines.append(f"  - Mapped from: `{mp.strip()}`")

        if "filename" in slot_attr_keys and art is not None:
            fpath = (
                    (art or {}).get("path")
                    or (art or {}).get("filename")
                    or (spec.get("filename") if isinstance(spec, dict) else None)
                    or c_spec.get("filename")
            )
            if fpath:
                lines.append(f"  - Filename: {fpath}")

        # description / guidance
        if "description" in slot_attr_keys:
            lines.append(f"  - Description: {desc or '(none)'}")
        if "content_guidance" in slot_attr_keys and guidance:
            lines.append(f"  - Content guidance: {guidance}")

        # No further fields for missing slots
        if status == "missing" or art is None:
            return lines

        gaps = art.get("gaps")
        summary = art.get("summary")
        tool_id = art.get("tool_id") or (spec.get("tool_id") if isinstance(spec, dict) else None)
        citable = art.get("citable")
        sources_sids = art.get("sources_used_sids") or []

        if "gaps" in slot_attr_keys and gaps:
            lines.append(f"  - Gaps: {gaps}")
        if "summary" in slot_attr_keys and summary:
            lines.append(f"  - Summary: {summary}")
        if "tool_id" in slot_attr_keys and tool_id:
            lines.append(f"  - Tool: `{tool_id}`")
        if "citable" in slot_attr_keys and citable is not None:
            lines.append(f"  - Citable: {'yes' if citable else 'no'}")
        if "format" in slot_attr_keys and fmt:
            lines.append(f"  - Format: {fmt}")
        if "mime" in slot_attr_keys and mime:
            lines.append(f"  - MIME: {mime}")
        if "sources_used_sids" in slot_attr_keys and sources_sids:
            lines.append(f"  - Sources used (SIDs): {sources_sids}")

        if "slot_value_inventorization" in slot_attr_keys:
            inv = build_runtime_inventory_from_artifact(art)
            if inv:
                lines.append("  - Slot value inventorization info:")
                inv_md = format_inventory(inv)
                if inv_md:
                    for ln in inv_md.splitlines():
                        lines.append("    " + ln)

        return lines

    lines: List[str] = []
    lines.append("## Produced slots")
    lines.append("")
    lines.append("Grouped by status: ❌ missing →  ⚠️ draft → ✅ completed.")
    lines.append("")

    # Order requested: missing, draft, then completed
    for status, group_title in [
        ("missing", "❌ Missing slots"),
        ("draft", " ⚠️ Draft / incomplete slots"),
        ("completed", "✅ Completed slots"),
    ]:
        slots = slots_by_status[status]
        lines.append(f"### {group_title} ({len(slots)})")
        lines.append("")

        if slots:
            for slot in slots:
                for ln in _format_one_slot(slot, status):
                    lines.append(ln)
        else:
            lines.append("_(none)_")

        lines.append("")

    if has_any_draft:
        lines.append(
            "❗️Slots in the **draft / incomplete** group contain partial content "
            "and may require further work before being shown to end users."
        )

    return "\n".join(lines).rstrip(), has_any_draft

def _format_deliverables_flat_with_icons(
        *,
        dmap: Dict[str, Any],
        contract: Optional[Dict[str, Any]] = None,
        content_len: int = 0,
        slot_attr_keys: Optional[Set[str]] = None,
        exclude_slots: Optional[List[str]] = None,
        slot_order: Optional[List[str]] = None,
) -> Tuple[str, bool]:
    """
    Flat list with inline status icons.

    Used when grouping isn't needed but status indicators are still helpful.

    Format:
        ## Deliverables

        - ✅ `report_md` (inline, markdown) - Description: ...
        - ⚠️ `chart_png` (file, image/png) [DRAFT] - Description: ...
        - ❌ `summary_json` (inline, json) - Description: ... (missing)
    """
    contract = contract or {}
    excluded = set(exclude_slots or [])

    if slot_attr_keys is None:
        slot_attr_keys = {"description", "gaps", "summary"}

    status_icon = {
        "missing": "❌",
        "draft": "⚠️",
        "completed": "✅",
    }

    lines: List[str] = []
    lines.append("## Deliverables")
    lines.append("")

    if not dmap:
        lines.append("_(no deliverables)_")
        return "\n".join(lines).rstrip(), False

    any_draft = False

    order = slot_order or sorted(dmap.keys())
    for slot in order:
        if slot in excluded:
            continue

        spec = (dmap or {}).get(slot) or {}
        art = spec.get("value") if isinstance(spec, dict) else None
        art = art if isinstance(art, dict) else None

        # Determine status
        status = _slot_status_for_presentation(slot, dmap)
        any_draft = any_draft or (status == "draft")
        icon = status_icon.get(status, "•")

        # Extract metadata
        c_spec = (contract or {}).get(slot) or {}
        c_spec = _to_jsonable(c_spec)
        desc = (
                (spec.get("description") if isinstance(spec, dict) else None)
                or c_spec.get("description")
                or ""
        )

        slot_type = (
                (art or {}).get("type")
                or (spec.get("type") if isinstance(spec, dict) else None)
                or c_spec.get("type")
                or "inline"
        )
        slot_type = str(slot_type).strip().lower() or "inline"

        fmt = (
                (art or {}).get("format")
                or (spec.get("format") if isinstance(spec, dict) else None)
                or c_spec.get("format")
        )
        mime = (
                (art or {}).get("mime")
                or (spec.get("mime") if isinstance(spec, dict) else None)
                or c_spec.get("mime")
        )

        # Type descriptor
        if slot_type == "file":
            type_desc = f"file, {mime}" if mime else "file"
        else:
            type_desc = f"inline, {fmt}" if fmt else "inline"

        # Draft marker
        draft_marker = " [DRAFT]" if status == "draft" else ""
        status_suffix = " (missing)" if status == "missing" else ""

        # Main line
        main_line = f"- {icon} `{slot}` ({type_desc}){draft_marker}{status_suffix}"
        if status != "missing" and art is not None and "artifact_id" in slot_attr_keys:
            aid = (
                    (art or {}).get("artifact_id")
                    or (art or {}).get("mapped_artifact_id")
                    or (art or {}).get("id")
                    or (art or {}).get("name")
                    or ""
            )
            aid = str(aid).strip()
            if aid:
                main_line += f" ← `{aid}`"

        lines.append(main_line)

        if status != "missing" and art is not None and "mapped_from" in slot_attr_keys:
            mp = (art or {}).get("mapped_from")
            if isinstance(mp, str) and mp.strip():
                mp2 = mp.strip()
                # if len(mp2) > 160:
                #     mp2 = mp2[:157] + "…"
                lines.append(f"  Mapped from: `{mp2}`")

        if status != "missing" and art is not None and "filename" in slot_attr_keys:
            fpath = (
                (art or {}).get("path")
                or (art or {}).get("filename")
                or (spec.get("filename") if isinstance(spec, dict) else None)
                or c_spec.get("filename")
            )
            if fpath:
                lines.append(f"  Filename: {fpath}")

        # Description
        if "description" in slot_attr_keys and desc:
            lines.append(f"  {desc}")

        # Skip further details for missing slots
        if status == "missing" or art is None:
            continue

        # Optional attributes
        gaps = art.get("gaps")
        summary = art.get("summary")

        if "gaps" in slot_attr_keys and gaps:
            lines.append(f"  Gaps: {gaps}")
        if "summary" in slot_attr_keys and summary:
            lines.append(f"  Summary: {summary}")

        # Content preview (if requested)
        if content_len != 0:
            text = _artifact_text(art)
            if text:
                if content_len > 0:
                    preview = text[:content_len]
                    if len(text) > content_len:
                        preview += "..."
                    lines.append(f"  Preview: {preview}")
                elif content_len < 0:
                    lines.append(f"  Content: {text}")

        lines.append("")  # Blank line between slots

    return "\n".join(lines).rstrip(), any_draft

class SolverPresenter:
    """
    Unified presenter for SolveResult across all contexts.

    Single source of truth for solver result formatting.
    All clients (React playbook, Codegen playbook, TurnView, Answer generator) use this.

    Usage:
        presenter = SolverPresenter(solve_result)

        # For React/Codegen playbooks (compact, no reasoning)
        playbook_md = presenter.playbook_view(content_preview_len=150)

        # For answer generator (full presentation)
        full_md = presenter.full_view(include_reasoning=True, extended=False)

        # For custom views
        custom_md = presenter.render(SolverPresenterConfig(...))
    """

    def __init__(
            self,
            sr: "SolveResult",
            *,
            contract: Optional[Dict[str, Any]] = None,
            codegen_run_id: Optional[str] = None,
    ):
        self.sr = sr
        self.contract = contract or (sr.plan.output_contract if sr.plan else {}) or {}
        self.codegen_run_id = codegen_run_id or sr.run_id()

    # ========== Convenience views (common use cases) ==========

    def playbook_view(
            self,
            *,
            include_log: bool = True,
            content_preview_len: int = 150,
            exclude_slots: Optional[List[str]] = None,
    ) -> str:
        """
        Compact view for operational playbooks (React/Codegen).

        Used by:
        - build_react_playbook (prior turns)
        - build_program_playbook_codegen (historical turns)

        Shows:
        - Project log (truncated if include_log=True)
        - Deliverables (flat list, no grouping, short previews)
        - No reasoning, no citations
        """

        # Uses status grouping with icons by default
        config = SolverPresenterConfig(
            include_project_log=include_log,
            include_deliverables=True,
            deliverables_grouping="status",
            deliverables_content_len=content_preview_len,
            deliverable_attrs={
                "description",
                "gaps",
                "summary",
            },
            exclude_slots=exclude_slots or [SERVICE_LOG_SLOT, "project_canvas"],
            output_format="markdown",
        )
        return self.render(config)

    def full_view(
            self,
            *,
            include_reasoning: bool = True,
            extended: bool = False,
            include_unused_citations: bool = False,
    ) -> str:
        """
        Complete program presentation for answer generator.

        Used by:
        - SolveResult.program_presentation / program_presentation_ext
        - Answer consolidation agents

        Shows:
        - Reasoning (if include_reasoning=True)
        - Project log (full)
        - Deliverables (grouped by status, rich attributes)
        - Interpretation instructions
        - Notes
        - Citations (used only, unless include_unused_citations=True)
        """
        attrs = None  # Auto-select based on extended

        config = SolverPresenterConfig(
            include_reasoning=include_reasoning,
            include_project_log=True,
            include_deliverables=True,
            include_interpretation=True,
            include_notes=True,
            include_citations=True,
            deliverables_grouping="status",
            deliverables_content_len=0,  # Metadata only, no raw content
            deliverable_attrs=attrs,  # Will auto-select
            exclude_slots=[SERVICE_LOG_SLOT, "project_canvas"],
            include_unused_citations=include_unused_citations,
            output_format="markdown",
        )

        # Override attrs for extended
        if extended:
            config.deliverable_attrs = {
                "description",
                "content_guidance",
                "format",
                "mime",
                "tool_id",
                "citable",
                "sources_used_sids",
                "gaps",
                "summary",
                "slot_value_inventorization",
            }
        else:
            config.deliverable_attrs = {
                "description",
                "content_guidance",
                "sources_used_sids",
                "slot_value_inventorization",
            }

        return self.render(config)

    def brief_view(self) -> str:
        """
        Ultra-compact view showing only deliverable names and status.

        Used for:
        - Quick summaries
        - List views
        """
        config = SolverPresenterConfig(
            include_deliverables=True,
            deliverables_grouping="status",
            deliverables_content_len=0,
            deliverable_attrs={"description"},
            exclude_slots=[SERVICE_LOG_SLOT, "project_canvas"],
            output_format="markdown",
        )
        return self.render(config)

    # ========== Core rendering engine ==========

    def render(self, config: SolverPresenterConfig) -> Union[str, ProgramPresentationParts]:
        """
        Render solver result according to configuration.

        This is the single implementation that handles all cases.
        """
        raw_dmap = self.sr.deliverables_map() or {}
        dmap = _to_jsonable(raw_dmap)
        if not isinstance(dmap, dict):
            dmap = {}
        contract = _to_jsonable(self.contract or {})
        if not isinstance(contract, dict):
            contract = {}

        # --- Build parts ---
        parts = ProgramPresentationParts(run_id=self.codegen_run_id)

        # 1. Reasoning
        if config.include_reasoning:
            reasoning_text = self.sr.round_reasoning()
            if reasoning_text:
                if config.reasoning_limit:
                    reasoning_text = reasoning_text[:config.reasoning_limit]
                parts.reasoning_md = "\n".join([
                    "## Solver reasoning (for this turn)",
                    reasoning_text.strip(),
                ]).rstrip()

        # 2. Project log
        if config.include_project_log:
            log_spec = dmap.get(SERVICE_LOG_SLOT) or {}
            log_art = (log_spec.get("value") if isinstance(log_spec, dict) else None) or {}
            log_body = _artifact_text(log_art)

            if config.project_log_limit and log_body:
                log_body = log_body[:config.project_log_limit]

            parts.project_log_md = "\n".join([
                "## Solver project log",
                "```",
                log_body if log_body else "(empty)",
                "```"
            ]).rstrip()

        # 3. Deliverables
        if config.include_deliverables and config.deliverables_grouping != "none":
            if config.deliverables_grouping == "status":
                # Grouped by status with section headers and icons
                produced_md, _has_draft = _format_produced_slots_grouped_by_status(
                    dmap=dmap,
                    contract=contract,
                    extended=bool(config.deliverable_attrs and len(config.deliverable_attrs) > 4),
                    slot_attr_keys=config.deliverable_attrs,
                    exclude_slots=config.exclude_slots,
                )
                parts.produced_slots_md = produced_md

            elif config.deliverables_grouping == "flat":
                # Flat list WITH inline icons (enhanced version)
                flat_md, _has_draft = _format_deliverables_flat_with_icons(
                    dmap=dmap,
                    contract=contract,
                    slot_attr_keys=config.deliverable_attrs,
                    content_len=config.deliverables_content_len,
                    exclude_slots=config.exclude_slots,
                )
                parts.produced_slots_md = flat_md

        # 4. Interpretation instructions
        if config.include_interpretation:
            rii = self.sr.interpretation_instruction()
            if rii:
                parts.interpretation_md = "\n".join([
                    "## How to interpret these results",
                    rii.strip(),
                ]).rstrip()

        # 5. Notes
        if config.include_notes:
            def _first_round_note() -> str:
                r0 = (self.sr.rounds() or [{}])[0]
                return _last_non_empty_note((r0 or {}).get("notes"))

            last_note = _first_round_note()
            if last_note:
                parts.notes_md = "\n".join([
                    "## Solver notes",
                    last_note.strip(),
                ]).rstrip()

        # 6. Citations
        if config.include_citations:
            citations_to_show = self.sr.citations_with_usage()
            if not config.include_unused_citations:
                citations_to_show = [c for c in citations_to_show if c.get("used", True)]

            if citations_to_show:
                uniq: Dict[str, Dict[str, Any]] = {}
                for c in citations_to_show:
                    if not isinstance(c, dict):
                        continue
                    url = (c.get("url") or "").strip()
                    sid = c.get("sid")
                    if not url or sid is None:
                        continue
                    if url in uniq:
                        continue
                    uniq[url] = {
                        "title": (c.get("title") or "").strip(),
                        "sid": sid,
                    }

                if uniq:
                    lines: List[str] = ["## Citations"]
                    for url, info in itertools.islice(uniq.items(), 50):
                        title = info["title"] or url
                        sid = info["sid"]
                        lines.append(f"- [{title}]({url}) [[S:{sid}]]")
                    parts.citations_md = "\n".join(lines).rstrip()

        # --- Return ---
        if config.output_format == "parts":
            return parts
        else:
            # Determine if extended based on attributes
            extended = bool(config.deliverable_attrs and len(config.deliverable_attrs) > 4)
            return parts.as_markdown(
                include_reasoning=config.include_reasoning,
                extended=extended,
            )

def program_brief_from_contract(sr: "SolveResult",
                                rehosted_files: List[dict]) -> Tuple[str, ProgramBrief]:
    """
    Returns: (brief_text, ProgramBrief)

    - Reads artifacts exactly as produced by io_tools normalization (execution.deliverables[slot]['value']).
    - Uses plan.output_contract only for human-facing description and optional content_guidance.
    - Treats content_inventorization as a **runtime** concept attached to the artifact:
        - Prefer art['content_inventorization'] if present.
        - Otherwise, fall back to a summary derived from the artifact text.
        - Always reflect draft/gaps in that inventory (without overwriting if already present).
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

        # Normalize type / description / guidance
        slot_type = (art.get("type") or spec.get("type") or "inline").strip().lower()
        desc = spec.get("description") or (contract.get(slot, {}) or {}).get("description") or ""
        guidance = spec.get("content_guidance") or (contract.get(slot, {}) or {}).get("content_guidance")

        # ---- Runtime text extraction (for inventory + inline text) ----
        output = art.get("output") or {}
        raw_text = output.get("text")
        # Fallback for shapes that kept text at top-level
        if raw_text is None and art.get("text") is not None:
            raw_text = art.get("text")

        # Build a string version for inline deliverables
        if isinstance(raw_text, (str, int, float)):
            text_str = str(raw_text)
        elif raw_text is not None:
            # structured content → JSON string
            try:
                text_str = json.dumps(raw_text, ensure_ascii=False)
            except Exception:
                text_str = str(raw_text)
        else:
            text_str = ""

        # ---- Runtime inventory: from artifact only ----
        inv = build_runtime_inventory_from_artifact(art)
        # ---- Build typed deliverables for the brief ----

        if slot_type == "inline":
            # text = ((art.get("output") or {}).get("text") or "")
            struct_delivs.append(InlineRef(
                slot=slot,
                description=desc,
                content_guidance=guidance,
                content_inventorization=inv,
                citable=bool(art.get("citable")),
                value_preview=(text_str[:280] if isinstance(text_str, str) else ""),
                text=text_str,
                tool_id=art.get("tool_id"),
                mime="application/json",  # presentation value only
                _type="inline",
            ))
            continue

        # file
        if slot_type == "file":
            output = art.get("output") or {}
            path = output.get("path") or art.get("path") or ""
            filename = path.split("/")[-1] if path else (art.get("filename") or slot)
            struct_delivs.append(FileRef(
                slot=slot,
                description=desc,
                content_guidance=guidance,
                content_inventorization=inv,
                tool_id=art.get("tool_id"),
                mime=art.get("mime"),
                text=output.get("text") or art.get("text") or "",
                filename=filename,
                key=art.get("key"),
                size=art.get("size"),
                _type="file",
            ))
            continue

        # fallback (treat unknown as inline text)
        struct_delivs.append(InlineRef(
            slot=slot,
            description=desc,
            content_guidance=guidance,
            content_inventorization=inv,
            citable=bool(art.get("citable")),
            value_preview=(text_str[:280] if isinstance(text_str, str) else ""),
            text=text_str,
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
            # rehosted files don't carry inventory; leave None
            content_inventorization=None,
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

    # ---- compact, deterministic text ----
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
        presenter = SolverPresenter(sr, contract=contract)
        deliverables_md = presenter.render(SolverPresenterConfig(
            include_deliverables=True,
            deliverables_grouping="flat",
            deliverables_content_len=0,  # No content in brief
            deliverable_attrs={"description", "slot_value_inventorization"},
            exclude_slots=[SERVICE_LOG_SLOT, "project_canvas"],
            output_format="markdown",
        ))

        if deliverables_md.strip():
            lines.append("")
            lines.append(deliverables_md)

    brief_text = "\n".join(lines).rstrip()
    return brief_text, brief_struct

def format_live_slots(
        *,
        slots: Dict[str, Any],
        contract: Optional[Dict[str, Any]] = None,
        grouping: Literal["status", "flat"] = "flat",
        slot_attrs: Optional[Set[str]] = None,
) -> str:
    """
    Format current/live slots that don't yet have a SolveResult wrapper.

    Used by build_react_playbook for Current Snapshot section.

    Args:
        slots: context.current_slots (artifact dicts directly, not wrapped)
        contract: output_contract for additional metadata
        grouping: "status" (grouped by ❌/⚠️/✅) or "flat" (simple list)
        slot_attrs: which attributes to show

    Returns:
        Formatted markdown string
    """
    # Normalize slots to deliverables_map shape expected by formatters
    # current_slots[slot] = {...artifact...}
    # deliverables_map[slot] = {"value": {...artifact...}, "description": "..."}

    dmap: Dict[str, Any] = {}
    for slot, art in (slots or {}).items():
        if not isinstance(art, dict):
            continue

        # Wrap artifact in the expected shape
        dmap[slot] = {
            "value": art,
            "description": art.get("description", ""),
        }

    if not dmap:
        return "_(no slots yet)_"

    slot_order = list(dmap.keys())  # preserves insertion order of context.current_slots

    # Default attributes for live view
    if slot_attrs is None:
        slot_attrs = {
            "description",
            "gaps",
            "summary",
            "filename",
        }

    # Use existing formatters
    if grouping == "status":
        md, _has_draft = _format_produced_slots_grouped_by_status(
            dmap=dmap,
            contract=contract,
            extended=False,
            slot_attr_keys=slot_attrs,
            exclude_slots=["project_log", "project_canvas"],
        )
    else:  # flat
        md, _has_draft = _format_deliverables_flat_with_icons(
            dmap=dmap,
            contract=contract,
            content_len=0,  # No content preview in live view
            slot_attr_keys=slot_attrs,
            exclude_slots=["project_log", "project_canvas"],
            slot_order=slot_order,
        )

    return md
