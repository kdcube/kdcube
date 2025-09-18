# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/codegen/team.py

"""
Once logs are JSON, your _history_digest (and any “previous programs” retrieval) can pick the best prior execution by action and targets, not brittle headings. E.g., “find the latest run with action in {"edit","create"} and sections_added contains 'Security'”.
"""

from typing import Dict, Any, List, Optional, Literal
from pydantic import BaseModel, Field, conlist
from datetime import datetime, timezone
import json

from kdcube_ai_app.apps.chat.sdk.inventory import ModelServiceBase
from kdcube_ai_app.apps.chat.sdk.streaming.streaming import _add_3section_protocol, _stream_agent_sections_to_json

def _today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()

# ---------- Codegen schema ----------
class CodeFile(BaseModel):
    path: str = Field(..., description="Relative path inside the package, e.g., 'main.py'")
    content: str = Field(..., description="UTF-8 source")

class OutputSpec(BaseModel):
    filename: str = Field(..., description="Relative filename the program MUST write into OUTPUT_DIR")
    kind: Literal["json", "text", "binary"] = "json"
    key: Optional[str] = Field(default=None, description="Scratchpad key suggestion for this output")

class SolverCodegenOut(BaseModel):
    entrypoint: str = Field(..., description="Shell command to run the program, e.g., 'python main.py'")
    files: conlist(CodeFile, min_length=1)
    outputs: conlist(OutputSpec, min_length=1)
    notes: str = ""
    # short guidance for the final answer generator about how to read artifacts from this run
    result_interpretation_instruction: str = Field(
        default="",
        description="≤120 words. Explain how to interpret the deliverables of the code you generate (the solution) produced this run (by slot/type), "
                    "that they are system-provided context (not user-authored), how to cite new sources, "
                    "and how to refer to artifacts prduced by code, for example, any files (PDF, PPTX, CSV, etc.), in the final answer presented to a user."
    )

def _adapters_public_view(adapters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Strip any runtime-only fields and keep exactly what the model should see:
      - id, import, call_template, doc (purpose, args, returns, constraints, examples)
    """
    cleaned: List[Dict[str, Any]] = []
    for a in adapters or []:
        cleaned.append({
            "id": a.get("id"),
            "import": a.get("import"),
            "call_template": a.get("call_template"),
            "is_async": bool(a.get("is_async")),
            "doc": {
                "purpose": (a.get("doc") or {}).get("purpose", ""),
                "args": (a.get("doc") or {}).get("args", {}),             # {"name": "type & rules"} strings
                "returns": (a.get("doc") or {}).get("returns", ""),       # short description/shape
                "constraints": (a.get("doc") or {}).get("constraints", []), # ["k in 1..10", "query non-empty", ...]
                "examples": (a.get("doc") or {}).get("examples", []),     # ["fn(a=..., b=...)", ...]
            }
        })
    return cleaned

async def solver_codegen_stream(
        svc: ModelServiceBase,
        *,
        task: Dict[str, Any],
        adapters: List[Dict[str, Any]],
        solvability: Optional[Dict[str, Any]] = None,
        on_thinking_delta=None,
        ctx: Optional[str] = "solver_codegen",
) -> Dict[str, Any]:
    """
    Generates a self-contained Python 3.11 program that:
      - imports & calls ONLY the adapters we provide (your real libs) WITHIN documented usage
      - reads INPUTS from OUTPUT_DIR/context.json and OUTPUT_DIR/task.json if present
      - writes results to OUTPUT_DIR/<files specified in outputs[]>
      - prints nothing (silent), robust error handling
    """

    today = _today_str()

    # pull optional decision & constraints coming from the planner/ToolManager
    decision = (solvability or {})  # may include tools_to_use, reasoning, output_contract_dyn
    constraints = (task or {}).get("constraints") or {}

    # reasonable defaults
    line_budget = int(constraints.get("line_budget", 80))
    prefer_single_call = bool(constraints.get("prefer_single_call", True) or constraints.get("prefer_direct_tools_exec", False))
    minimize_logic = bool(constraints.get("minimize_logic", True))
    concise = bool(constraints.get("concise", True))

    # ---------- System prompt (authoritative; no ambiguity) ----------
    sys = (
        "# Codegen — single Python program\n"
        "\n"
        "## Authoritative inputs for **this** run\n"
        "- The **dynamic output contract** `output_contract_dyn` is provided **in THIS prompt**. Treat it as the single source of truth.\n"
        "- output_contract_dyn contract contains ONLY dict, a mapping of slot name -> its description, never actual content\n"
        "- If the downstream instruction says 'use history / edit existing', call `context_tools.fetch_working_set(select='latest')` to retrieve the materials to start with.\n"
        "- Use `ws['existing_project_canvas']` as the editable base and `ws.existing_sources` as `prior_sources`.\n"                                                                                                                          
        "## Language & syntax\n"
        "- Python 3.11. Use `True/False/None`. Build JSON with `json.dumps(...)`.\n"
        "\n"
        "## Imports & calls (hard rules)\n"
        "- Paste adapter imports **exactly** as provided; do not alter module paths or aliases.\n"
        "- Call functions exactly per the provided `call_template`.\n"
        "- Import the infra wrapper: `from io_tools import tools as agent_io_tools`.\n"
        "- **Wrap every adapter call** using the wrapper (logging + indexing):\n"
        "  `res = await agent_io_tools.tool_call(\\n"
        "       fn=<alias>.<fn>,\\n"
        "       params_json=json.dumps({<kwargs>}),\\n"
        "       call_reason=\"<5–12 words why this call is needed>\",\\n"
        "       tool_id=\"<qualified id exactly as in ADAPTERS list>\"\\n"
        "  )`\n"
        "\n"
        "## Runtime contract\n"
        "- A global `OUTPUT_DIR` is injected at runtime. Do **not** redefine it.\n"
        "- Write **all** files into `OUTPUT_DIR`.\n"
        "\n"
        "## Persistence (required)\n"
        "- Finish by writing the final result: `await agent_io_tools.save_ret(data=json.dumps(result))`.\n"
        "- On success include: `ok=true`, `objective` and `out_dyn` (filled **exactly** per output_contract_dyn).\n"
        "\n"
        "## Slot Contract Fulfillment\n"
        "- The `output_contract_dyn` provided below defines what slots you must fill in `out_dyn`\n"
        "- Use exactly those slot names as keys in `out_dyn`\n"
        "- Each slot description tells you what content to generate for that slot\n"
        "- **FILE** slots (e.g., `pdf_file`): `{\"file\":\"<OUTPUT_DIR-relative>\", \"mime\":\"<mime>\", \"description\":\"...\"}`.\n"
        "- **TEXT/STRUCT** slots (e.g., `project_canvas`, `summary_md`, `data_json`):\n"
        "  `{\"description\":\"...\", \"value\":\"<stringified>\", \"format\":\"markdown|plain_text|json|yaml|object\"}`.\n"
        "- The `resource_id` for each artifact is the slot name (infra will prefix with `slot:` automatically).\n"
        "- Paths stored in `out_dyn` **must** be `OUTPUT_DIR`-relative (never absolute).\n"
        "\n"
        "## Content Generation Rules (CRITICAL)\n"
        "- You MUST use tools to generate ALL content dynamically - never pre-write content\n"
        "- You can only write instructions/prompts for LLM tools - never write the final content yourself\n"
        "- Let the tools generate the actual content based on your instructions\n"
        "- Use tools in this typical flow:\n"
        "  1) Search/gather information (relevant search tool(s))\n"
        "  2) Generate/structure content (llm tools)\n"
        "  3) Populate canvas with tool-generated content\n"
        "  5) Generate files only if contract output_contract_dyn requires them\n"
        "## Required Tool Flow\n"
        "- Typical flow: search/gather → process/generate → structure/edit → output\n"
        "  ✓ Use search tools to find information\n"
        "  ✓ Use summarize_llm to create structured explanation  \n"
        "  ✓ Use edit_text_llm to refine and format\n"
        "  ✗ Don't use calc for trivial multiplication\n"
        "  ✗ Don't pre-write explanations in out_dyn\n"
        "## Mandatory slots (critical)\n"
        "### project_canvas\n"
        "- Contains the final user-facing **Markdown** content representing the whole objective solution.\n"
        "### project_log\n"
        "- Represent shortly 'log' of objectives that were achieved step by step in the program. Your program must initialize and append to this string-log when some step is accomplished. \n"
        "## Reference pipeline\n"
        '1) Editor or fresh start: '
        ' - call `ws = context_tools.fetch_working_set(select="latest")` when editing is requested to get the existing project_canvas.\n'
        ' and use ws["existing_project_canvas"] as starting "project_canvas" and ws["sources"] as existing sources (probably mentioned in the starting canvas)\n'
        '- If not an editing mode in the objective, start fresh.\n'
        '2) Source Management Rules:'
        " - When using multiple source tools such as *_search, ALWAYS use context_tools.merge_sources\n"
        # '`new_sources_1 = new_sources_1; new_sources_2 = ...;  unified_sources=context_tools.merge_sources(json.dumps([prior_sources, json.loads(new_sources_1), json.loads(new_sources_2), ..]))`.\n'
        "- Pattern: `unified_sources = context_tools.merge_sources(source_collections=json.dumps([sources1, sources2, sources3]))`\n"
        "3) If llm tool is needed, prepare the GUIDANCE to LLM tool which defines what to do with the current inputs. GUIDANCE can come from other llm tool which you asked to generate one ot or you write it based on the current purpose.\n"
        "In instruction, you can provide the instruction to LLM tool. Example:\n"
        "4) `edited = generic_tools.edit_text_llm(text=existing_pc+GUIDANCE, instruction='Apply GUIDANCE to text; keep structure; no invented facts; add [[S:n]] only on NEW/CHANGED claims; REMOVE GUIDANCE block.', keep_formatting=True, sources_json=json.dumps(unified_sources), cite_sources=True, forbid_new_facts_without_sources=True)`.\n"
        "..\n"
        "5) If rendering PDF/PPTX etc., pass the SAME `unified_sources` to `write_pdf`/`write_pptx / etc.` with `resolve_citations=True`.\n"
        "6) Make sure you fill all the slots according to output_contract_dyn contract.\n"
        " `out_dyn[\"project_canvas\"] = {\"description\":\"Updated project canvas (Markdown)\", \"value\": edited, \"format\":\"markdown\"}`.\n"
        " `out_dyn[\"project_log\"] = {\"description\":\"Added .. to project log\", \"value\": <your log>, \"format\":\"markdown\"}`.\n"
        "## File/path rules\n"
        "- All files must physically live in `OUTPUT_DIR`.\n"
        "- Store `OUTPUT_DIR`-relative paths in `out_dyn` (e.g., `\"rust_advances.pdf\"`).\n"
        "\n"
        "## Error handling\n"
        "- A runtime helper `fail(...)` is injected into your program and available globally.\n"
        "- Managed errors: call `await fail(\"<short description>\", where=\"<stage>\", details=\"<why>\")` and return immediately.\n"
        "- Unhandled exceptions: wrap `main()` in try/except; in except, call `await fail(\"Unhandled exception\", where=\"main\", error=str(e), details=type(e).__name__, managed=False)`.\n"
        "- The helper writes a normalized failure envelope to `result.json` (including `contract`, `objective`, and optional `out_dyn`).\n"
        "\n"
        "## Async\n"
        "- If any adapter is async, implement `async def main()` and run with `asyncio.run(main())`.\n"
        "\n"
        "## Style & behavior\n"
        "- Linear and concise. No prints.\n"
        "- USER-FACING STATUS: two short lines (objective; plan). Do **not** name tools/providers/models.\n"
        "\n"
    )
    sys += (
        f"• Keep main.py ≤ {line_budget} lines.\n"
        f"Assume today={today} (UTC).\n"
    )

    # ---------- Strict 3-section protocol ----------
    sys = _add_3section_protocol(
        sys,
        "{"
        "  \"entrypoint\": \"python main.py\","
        "  \"files\": [ {\"path\": \"main.py\", \"content\": \"...\"} ],"
        "  \"outputs\": [ {\"filename\": \"result.json\", \"kind\": \"json\", \"key\": \"worker_output\"} ],"
        "  \"notes\": \"<=40 words\","
        "  \"result_interpretation_instruction\": \"<=120 words, tool-agnostic, concise\""
        "}"
    )

    # ---------- Message: task + adapters with DOCS ----------
    adapters_for_llm = _adapters_public_view(adapters)

    contract_dyn = (decision or {}).get("output_contract_dyn") or {}

    msg = (
        "TASK (objective + constraints for this program):\n"
        f"{json.dumps(task or {}, ensure_ascii=False, indent=2)}\n\n"
        "SOLVABILITY / DECISION (read-only hints):\n"
        f"{json.dumps(decision or {}, ensure_ascii=False, indent=2)}\n\n"
        "DYNAMIC OUTPUT CONTRACT YOU MUST FULFILL - output_contract_dyn (slot → description):\n"
        f"{json.dumps(contract_dyn, ensure_ascii=False, indent=2)}\n\n"
        "ADAPTERS — imports, call templates, is_async:\n"
        f"{json.dumps([{k: v for k,v in a.items() if k in ('id','import','call_template','is_async')} for a in adapters_for_llm], ensure_ascii=False, indent=2)}\n\n"
        "TOOL DOCS (purpose/args/returns/constraints/examples):\n"
        f"{json.dumps([{ 'id': a['id'], 'doc': a.get('doc', {}) } for a in adapters_for_llm], ensure_ascii=False, indent=2)}\n\n"
        "Produce the three sections as instructed."
    )

    # ---------- Stream ----------
    return await _stream_agent_sections_to_json(
        svc,
        client_name="solver_codegen",
        client_role="solver_codegen",
        sys_prompt=sys,
        user_msg=msg,
        schema_model=SolverCodegenOut,
        on_thinking_delta=on_thinking_delta,
        ctx=ctx,
        max_tokens=6000,
    )

# ====================== TOOL ROUTER (topic- & domain-aware) ======================
class ToolCandidate(BaseModel):
    name: str                              # e.g., "vuln_db"
    reason: str = ""
    confidence: float = Field(0.6, ge=0.0, le=1.0)
    parameters: Dict[str, Any] = Field(default_factory=dict)

class ToolRouterOut(BaseModel):
    candidates: List[ToolCandidate] = Field(default_factory=list)
    notes: str = ""                        # short commentary

async def tool_router_stream(
        svc: ModelServiceBase,
        user_text: str,
        policy_summary: str = "",
        context_hint: str = "",
        topic_hint: str = "",
        prefs_hint: Dict[str, Any] | None = None,
        *,
        topics: Optional[List[str]] = None,
        tool_catalog: Optional[List[Dict[str, Any]]] = None,
        on_thinking_delta=None,
        max_tokens=None,
) -> Dict[str, Any]:
    today = _today_str()
    sys = (
    "You are a Tool Router. Using the TOOL CATALOG (id, purpose, args), select at most 5 tools that materially help.\n"
    "If none helps, return [].\n"
    f"Assume today={today} (UTC).\n"
    "\nHARD RULES:\n"
    "• Do NOT solve the task. Do NOT run tools. Do NOT invent facts, URLs, dates, or long free text.\n"
    "• Only select tools present in the catalog. For each selection, 'name' MUST equal the catalog 'id'.\n"
    "• ONLY suggest file generation tools (write_pdf, write_pptx, etc.) if the user EXPLICITLY requests a document, report, or file output.\n"
    "• For simple questions seeking information or guidance, prefer text-based tools and summaries over file generation.\n"
    "## Smart Tool Selection\n"
    "• For explanatory/educational requests: prioritize knowledge search tools + text generation/editing (llm tools)\n"
    "• For calculation requests: use calc only for actual mathematical computations, not trivial arithmetic\n"
    "• For content creation: include edit_text_llm to structure and refine generated content\n"
    "• Avoid selecting tools that don't materially contribute to the user's request\n"
    "• Example: for 'explain how to compute..' → select relevant search tool to gather the context + relevant llm tool, NOT calc\n"
    "\nPARAMETER FILL POLICY (Scaffolding only):\n"
    "• Provide MINIMAL, PLAUSIBLE scaffolding for parameters (booleans, enums, small numerics, simple flags).\n"
    "• For any contentful parameter (e.g., text, content_md/markdown, sources_json, url lists, file bodies):\n"
    "    - Do NOT invent content. Use a short placeholder like \"<TBD at runtime>\" or omit the param.\n"
    "• Safe defaults are ok (e.g., n=5, max_tokens=300, style='brief'), but never prewrite summaries or links.\n"
    "\nCLOSED-PLAN COMPOSABILITY CHECK (CRITICAL):\n"
    "• The selected set must form a *closed plan* that can produce the user's requested deliverable end-to-end in a single run,\n"
    "  with NO human-in-the-loop and NO external steps. If a tool requires an input (e.g., content_md for a PDF renderer), ensure that\n"
    "  input is either provided by the user/context OR produced by another selected tool. Otherwise add the missing generator/transformer tool.\n"
    "• If the goal requires *text construction/transformation* (e.g., summary, outline, caption, extraction), include a text/LLM transformer tool from the catalog.\n"
    "• Minimize redundancy: avoid overlapping tools when one suffices; prefer the smallest closed set that keeps the plan feasible.\n"
    "\nAUTO-SELECTION RULES:\n"
    "• If multiple source-generating tools are selected, ALWAYS include ctx_tools.merge_sources\n"
    "• Source tools include *_search tools and any tool that claim returning sources/citations\n"
    "• merge_sources is required to prevent citation conflicts and ensure proper source deduplication\n"
    "\nSEQUENCING ASSUMPTION:\n"
    "• It is acceptable if outputs must flow between selected tools; the downstream solver will orchestrate (likely via codegen). Your job is to pick a feasible set.\n"
    "CONTEXT-AWARE SEARCH RULE:\n"
    "• If the user intent is to *add / expand / modify / improve*, and there's no strict anti-recommendation for adding a web/search tool, include such tools if relevant.\n"
    "\nOUTPUT FORMAT:\n"
    "• Return up to 5 candidates with reasons and minimal parameters.\n"
    "\nINTERNAL THINKING (STATUS): tiny.\n"
    "USER-FACING (STATUS): 2 short lines (focus; minimal plan). No tool/provider/model names.\n"
    )
    ToolRouterOut.model_json_schema()
    sys += (
        "\nREUSE CONTEXT:\n"
        "• Downstream code will read prior runs from OUTPUT_DIR/context.json → program_history[]. "
        "Prefer selecting tools that can EDIT or UPDATE existing deliverables when appropriate (e.g., LLM editor, file writer), "
        "instead of rebuilding everything from scratch. Only applicable if the new request applies to a past program in the context.\n"
    )
    sys = _add_3section_protocol(
        sys,
        "{ \"candidates\": ["
        "  {\"name\": \"<tool_id>\", \"reason\": \"...\", \"confidence\": 0..1, \"parameters\": {\"a\": \"hello\"}}"
        "], \"notes\": \"(<=25 words)\" }"
    )

    catalog_str = ""
    if tool_catalog:
        preview = [
            {"id": t.get("id"),
             "purpose": (t.get("doc") or {}).get("purpose",""),
             "args": (t.get("doc") or {}).get("args", {})}
            for t in tool_catalog
        ]
        catalog_str = f"TOOL CATALOG:\n{json.dumps(preview, ensure_ascii=False, indent=2)}\n\n"

    msg = (
            catalog_str +
            f"User question:\n{user_text}\n\n"
            f"{'Topics: ' + ', '.join(topics[:6]) if topics else f'Topics (hint): {topic_hint}'}\n"
            f"Policy/context hints:\n{policy_summary[:800]}\n\n"
            f"Preferences hint (assertions/exceptions; treat as constraints when selecting tools):\n{json.dumps((prefs_hint or {}), ensure_ascii=False)[:1200]}\n\n"
            f"Conversation cue:\n{context_hint}\n\n"
            "Produce the three sections as instructed."
    )

    out = await _stream_agent_sections_to_json(
        svc, client_name="tool_router", client_role="tool_router",
        sys_prompt=sys, user_msg=msg, schema_model=ToolRouterOut,
        on_thinking_delta=on_thinking_delta,
        max_tokens=max_tokens
    )
    out = out or {}
    return out


# ====================== SOLVABILITY (with optional domain/topics) ======================
# ---------- Output contract schema ----------
from typing import Literal

class ContractItem(BaseModel):
    rid: str = Field(..., description="Stable resource id the program MUST use in result.out[].")
    type: Literal["inline","file"] = "inline"
    # For inline
    format: Optional[Literal["markdown","text","json","url"]] = None
    # For file
    mime: Optional[str] = None
    filename_hint: Optional[str] = None

    description: str = ""
    citable: Optional[bool] = None
    source_hint: Optional[str] = Field(default=None, description="Either 'program' or an adapter id to prefer (e.g., 'generic_tools.write_pdf').")
    require_text_fallback: bool = Field(default=False, description="If type='file', require a paired inline description/content.")
    fallback_rid: Optional[str] = Field(default=None, description="Rid to use for the text fallback when required.")
    min_count: int = 1
    max_count: Optional[int] = 1

class SolvabilityOut(BaseModel):
    solvable: bool
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    reasoning: str = ""
    tools_to_use: List[str] = Field(default_factory=list)
    clarifying_questions: List[str] = Field(default_factory=list)

    # NOTE: unified names — no 'single_call' anymore
    solver_mode: Literal["direct_tools_exec","codegen","llm_only"] = "llm_only"
    solver_instruction: str = ""  # short justification

    # Dynamic contract the program must fulfill iff solver_mode='codegen'
    # map: slot name -> human description of what should be produced
    output_contract_dyn: Optional[Dict[str, str]] = Field(default_factory=dict)

    context_use: bool = True
    project_canvas_slot: str = "project_canvas"                  # the slot codegen MUST populate for text representation of solution
    project_log_slot: str = "project_log"
    history_select: str = "latest"                      # 'latest' | 'by_mention' | 'by_similarity'
    history_select_hint: str = ""                       # short instruction when not 'latest'
    citations_source_path: str = "program_history[].<exec>.web_links_citations.items"
    instructions_for_downstream: str = ""               # ≤80 words, concrete steps to read context and pick version, or other special instructions


async def assess_solvability_stream(
        svc: ModelServiceBase,
        user_text: str,
        candidates: List[Dict[str, Any]],
        policy_summary: str = "",
        prefs_hint: Dict[str, Any] | None = None,
        *,
        is_spec_domain: Optional[bool] = None,
        topics: Optional[List[str]] = None,
        on_thinking_delta=None,
        max_tokens=None,
) -> Dict[str, Any]:

    today = _today_str()
    sys = (
        "# Solvability Checker\n"
        "\n"
        "## Goal\n"
        "- Decide if the request is answerable **now** using ONLY the provided tool candidates.\n"
        "- If solvable, emit a minimal **DYNAMIC OUTPUT CONTRACT** (output_contract_dyn) the downstream program MUST produce.\n"
        "- output_contract_dyn format is the str -> str: key-value mapping of the slot name to the detailed text description of what this slot represents.\n"
        "- output_contract_dyn must include slots for all user-facing products that will be created to satisfy user request, such as files.\n"
        "\n"
        "## Hard rules\n"
        "- Do **not** solve the task. Do **not** invent tools or content, dates, URLs, or section lists.\n"
        "- **Feasibility gate:** every contract slot must be producible with the selected tools; otherwise change tools or set `solvable=false`.\n"
        "- **Contract minimality:** include **only** what the user objective requires.\n"
        "\n"
        "## Modes\n"
        "- `llm_only` — no tools needed or the task cannot be solved by codegen / tools.\n"
        "- `direct_tools_exec` — allowed **only** when exactly one tool is selected.\n"
        "- `codegen` — choose when multiple tools are needed or when outputs must flow between tools.\n"
        "\n"
        "## Closed-plan requirement\n"
        "- Any input needed by a selected tool must come from user/context **or** be produced by another selected tool.\n"
        "\n"
        "## Editing detection & context use\n"
        "- If the user request implies EDIT/UPDATE/EXTEND/APPEND/REVISE/IMPROVE/CONTINUE, set:\n"
        "solver_mode='codegen'\n"
        "context_use=true\n"
        "instructions_for_downstream: 'This is an editing turn. Use ctx_tools.fetch_working_set(select=\"latest\") to get the prior canvas and citations, then edit in place using the editor; merge prior + new sources and render deliverables.\n"
        "## Project canvas (critical)\n"
        "- ALWAYS include a slot named **`project_canvas`** in `output_contract_dyn`. This is the single editable, textual mirror of the project.\n"
        "- The canvas must contain the actual user-facing Markdown content, including any `[[S:n]]` tokens.\n"
        "- If the request modifies a prior project, prefer **edit/update** over full regeneration.\n"
        "\n"
        "## Project log (critical)\n"
        "- ALWAYS include a slot named **`project_log`** in `output_contract_dyn`. This is the continuous slot which is filled during project by its internal editors.\n"
        "- The project log must contain the description of objective, actual edits to take and the user current intent or preferences on this turn\n"
        "## Output Contract Rules\n"
        "- ALWAYS include: project_canvas (editable text), project_log (change history)\n"
        "- Contract slots must contain ONLY high-level descriptions of what to produce\n"
        "- Contract slots must NOT contain actual content, examples, or pre-written text\n"
        "- Example: 'project_canvas': 'Guide explaining risk scoring methodology' NOT 'This report provides...'\n"
        "- CONDITIONALLY include file slots (pdf_file, slides_pptx, etc.) only when:\n"
        "  • User explicitly requests a document, report, presentation, or file\n"
        "  • User uses words like 'create a report', 'generate document', 'make a presentation'\n"
        "  • User asks for something 'to download', 'to share', or 'as a file'\n"
        "  • User is continuing work on an existing document/report and needs it rendered\n"
        "- For informational queries seeking guidance or explanations, use only text slots\n"
        "- Default to minimal contracts that match user intent, not comprehensive deliverables\n"
        "\n"
        "## Slot naming\n"
        "- snake_case.\n"
        "- Files: `pdf_file`, `slides_pptx`, `image_png`, `csv_file`, `zip_bundle`.\n"
        "- Text/struct: `project_canvas`, `project_log`, `summary_md`, `outline_md`, `table_md`, `data_json`, `plan_md`.\n"
        "- Only add a separate `sources_md` slot if the user explicitly asks for sources outside the file render so they are needed separately of internal project canvas.\n"
        "## Instruction for downstream agent\n"
        "`instructions_for_downstream` must include the instruction for downstream agent.\n"
        " If mode == 'llm_only', describe why the solution cannot be solved by solver (no such tools or not enough data, or the solution cannot be solved completely) so the llm decide how to answer to a user w/o solver, including the confessing the uncertainty\n"
        " If the mode == 'direct_tools_exec', this message also will be shown to the final answer generator so that it will know how to interpret the solver results (produced by selected tool).\n"
        " If the mode == 'codegen', this instruction will be shared to codegen LLM so that it produces the code which solves the task.\n"
        "\n"
        "## Clarifying questions\n"
        "- Ask ≤2 only if ambiguity **blocks** progress.\n"
        "\n"
    )
    sys += (
        f"Assume today={today} (UTC).\n"
        "\n"
        "INTERNAL THINKING: very concise.\n"
        "USER-FACING STATUS: two short lines (assessment; next action). No tool/provider names.\n"
    )
    sys = _add_3section_protocol(
        sys,
        "{"
        "  \"solvable\": bool,"
        "  \"confidence\": 0..1,"
        "  \"reasoning\": \"(<=25 words)\","
        "  \"tools_to_use\": [\"<tool_id>\"],"
        "  \"context_use\": bool,"
        "  \"clarifying_questions\": [\"...\",\"...\"],"
        "  \"solver_mode\": \"direct_tools_exec\"|\"codegen\"|\"llm_only\","
        "  \"instructions_for_downstream\": \"(<=45 words)\","
        "  \"output_contract_dyn\": {\"<slot>\": \"<description>\"}"
        "}"
    )
    topic_line = f"Topics: {', '.join(topics[:6])}" if topics else ""
    # domain_line = f"is_spec_domain={is_spec_domain!s}"
    msg = (
        # f"{domain_line}\n{topic_line}\n"
        f"{topic_line}\n"
        f"User question:\n{user_text}\n"
        f"Policy/context summary:\n{policy_summary[:800]}\n"
        f"Preferences hint (use to constrain what to produce/avoid):\n{json.dumps((prefs_hint or {}), ensure_ascii=False)[:1200]}\n"
        f"Candidates:\n{json.dumps(candidates, ensure_ascii=False)}\n"
        "Produce the three sections as instructed."
    )

    out = await _stream_agent_sections_to_json(
        svc,
        client_name="solvability",
        client_role="solvability",
        sys_prompt=sys,
        user_msg=msg,
        schema_model=SolvabilityOut,
        on_thinking_delta=on_thinking_delta,
        ctx="solvability",
        max_tokens=max_tokens
    )
    out = out or {}
    agent_response = out.setdefault("agent_response", {})
    elog = out.setdefault("log", {})
    internal_thinking = out.get("internal_thinking")
    error = elog.get("error")

    agent_response_tools_to_use = agent_response.get("tools_to_use") or []
    __service = {
        "internal_thinking": internal_thinking,
        "raw_data": elog.get("raw_data")
    }
    out["__service"] = __service
    if error:
        agent_response["solver_mode"] = "llm_only"
        __service["error"] = error

    # constrain to provided candidates
    cand_names = {c.get("name") for c in (candidates or []) if c.get("name")}
    tools = [t for t in agent_response_tools_to_use if t in cand_names]
    agent_response["tools_to_use"] = tools

    if (agent_response.get("solver_mode") == "direct_tools_exec"
            and len(tools) > 1):
        agent_response["solver_mode"] = "codegen"

    # --- Robust defaults if model is sparse/quiet ---
    if not cand_names:
        # Can still be solvable without tools
        agent_response.setdefault("solver_mode", "llm_only")
    else:
        # If exactly one candidate selected → prefer direct_tools_exec; else codegen
        if not agent_response.get("solver_mode"):
            agent_response["solver_mode"] = "direct_tools_exec" if len(tools) == 1 else "codegen"

    return out

