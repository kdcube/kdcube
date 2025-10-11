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

from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase
from kdcube_ai_app.apps.chat.sdk.streaming.streaming import _stream_agent_two_sections_to_json as _stream_agent_sections_to_json
from kdcube_ai_app.apps.chat.sdk.streaming.streaming import _add_3section_protocol, \
    _add_2section_protocol


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
        program_playbook: Optional[str] = None,
        on_thinking_delta=None,
        ctx: Optional[str] = "solver_codegen",
        current_tool_imports: Optional[str] = None,
        code_packages: Optional[str] = None,

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
    decision = (solvability or {})  # may include selected_tools, reasoning, output_contract_dyn
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
        "## Contract & Inputs\n"
        "- `output_contract_dyn` (JSON in this prompt) is authoritative: slot_name → {\"type\":\"inline\"|\"file\", \"description\":str, ...}\n"
        "- It describes exactly what slots this program must produce. No extra slots\n"
        "\n"
        "## Program History & Artifact Retrieval\n"
        "\n"
        "### Context Available\n"
        "- PROGRAM HISTORY PLAYBOOK lists earlier turns with status, user/assistant messages, and solver deliverable previews.\n"
        "- Use those TURN_IDs with `ctx_tools.fetch_turn_artifacts` to pull exact artifacts.\n"
        "- Turn labels: `[CURRENT TURN]` = this execution, `[HISTORICAL]` = prior runs\n"
        "\n"
        "### When to Fetch\n"
        "- ALWAYS for edit/update/extend/continue tasks\n"
        "- When the playbook shows relevant prior work\n"
        "- When building on prior deliverables or user/assistant messages\n"
        "- Never paste large artifact excerpts into code or logs; fetch and operate directly.\n"
        "\n"
        "### Access Patterns\n"
        "```python\n"
        "# Fetch turns (see tool doc for full signature)\n"
        "turns = json.loads(await io_tools.tool_call(\n"
        "    fn=ctx_tools.fetch_turn_artifacts,\n"
        "    params_json=json.dumps({\"turn_ids\": '[\"<TURN_ID-1>\", \"<TURN_ID-2>\"]'}),\n"
        "    call_reason=\"Load prior artifacts for continuation\",\n"
        "    tool_id=\"ctx_tools.fetch_turn_artifacts\"\n"
        "))\n"
        "\n"
        "# Access artifacts\n"
        "t = turns[\"<TURN_ID>\"]\n"
        "status           = t.get(\"status\")  # success | failed: solver_error | answered_by_assistant | no_activity\n"
        "program_log_md   = (t.get(\"program_log\") or {}).get(\"text\", \"\")\n"
        "user_msg         = t.get(\"user_msg\", \"\")\n"
        "assistant_msg    = t.get(\"assistant_msg\", \"\")\n"
        "deliverables     = t.get(\"deliverables\", {})\n"
        "existing_text    = deliverables.get(\"<slot>\", {}).get(\"text\", \"\")\n"
        "existing_sources = deliverables.get(\"<slot>\", {}).get(\"sources_used\", [])\n"
        "\n"
        "# For editing: ALWAYS merge sources so [[S:n]] remain stable\n"
        "unified = json.loads(await io_tools.tool_call(\n"
        "    fn=ctx_tools.merge_sources,\n"
        "    params_json=json.dumps({\"source_collections\": json.dumps([existing_sources, new_sources])}),\n"
        "    call_reason=\"Merge sources to preserve SIDs\",\n"
        "    tool_id=\"ctx_tools.merge_sources\"\n"
        "))\n"
        "```\n"
        "\n"
        "### Key Rules for Artifact Retrieval\n"
        "- `text` holds content with [[S:n]] tokens using stable, global SIDs.\n"
        "- `sources_used` is the authoritative list for those SIDs.\n"
        "- When editing: ALWAYS merge `sources_used` and new sources via `ctx_tools.merge_sources` before regenerating so citations remain valid\n"
        "- For structured artifacts in `text`, parse (strip code fences if needed).\n"
        "\n"
        "## Chat/User Message Access\n"
        "- Current user message: `turns['<current_turn_id>']['user_msg']` (from fetch_turn_artifacts)\n"
        "- Use minimal excerpts only; never dump whole messages into logs/files.\n"
        "\n"
        "## Language & Syntax\n"
        "- Python 3.11. Use `True/False/None`. Build JSON with `json.dumps(...)`.\n"
        "\n"
        "## Imports & Calls (hard rules)\n"
        "- Use the wrappers exactly as given. Do not change module paths/aliases.\n"
        "- Call functions exactly per provided `call_template`.\n"
        "- Do NOT import tool modules yourself. The runtime **already imports** and exposes:\n"
        f"{current_tool_imports}\n"
        "- You must call tools via the wrapper:\n"
        "  ```python\n"
        "  ret = await agent_io_tools.tool_call(\n"
        "      fn=<alias>.<fn>,\n"
        "      params_json=json.dumps({<kwargs>}),\n"
        "      call_reason=\"<5–12 words why this call is needed>\",\n"
        "      tool_id=\"<qualified id exactly as in ADAPTERS list>\"\n"
        "  )\n"
        "  ```\n"
        "\n"
        f"{code_packages}\n\n"
        "## Runtime Contract\n"
        "- Two globals are injected at runtime. Do **not** redefine them:\n"
        "  - `OUTPUT_DIR`: str path to the output directory.\n"
        "  - `OUTPUT`: `pathlib.Path` pointing to the same directory (use this for path arithmetic).\n"
        "- Write **all** files by joining with `OUTPUT`, then pass `str(...)` to write tools:\n"
        "  `python\n"
        "  pdf_rel = \"report.pdf\"\n"
        "  abs_path = str(OUTPUT / pdf_rel)  # <-- correct: use OUTPUT (Path), not OUTPUT_DIR\n"
        "  `\n"
        "- Store **OUTPUT_DIR-relative** paths (strings) in slots (e.g., 'report.pdf').\n"
        "- Create files via write tools (e.g., `generic_tools.write_*`). Files written directly may be lost.\n"
        "\n"
        "## Live Progress & Checkpoints (CRITICAL)\n"
        "- The runtime injects `set_progress(...)`, `done()`, and `fail(...)` - all async.\n"
        "- **Call await `set_progress(...)` after each milestone** and whenever any slot becomes available.\n"
        "- Use `flush=True` only at step boundaries to persist a checkpoint immediately.\n"
        "- If the program is terminated (timeout/kill), the runtime writes a **partial** `result.json` from the last `set_progress(...)` state.\n"
        "\n"
        "## Slot values (`out_dyn_patch`)\n"
        "- Use exactly the slot names from `output_contract_dyn`.\n"
        "- Inline slot shape:\n"
        "  `{ \"type\":\"inline\", \"format\": <per contract>, \"description\":\"...\", \"value\": <text or JSON> }`\n"
        "- File slot shape:\n"
        "  `{ \"type\":\"file\", \"path\":\"<OUTPUT_DIR-relative>\", \"mime\":\"<per contract>\", \"description\":\"...\", \"text\":\"<faithful textual surrogate>\" }`\n"
        "- **Text surrogate is mandatory for every file slot.** If the file came from text (e.g., MD→PDF), use that source text.\n"        "\n"        
        "## Content generation flow\n"
        "- search/gather → generate/structure → assemble → (optionally) render files\n"
        "- Use LLM tools to generate content; do not hardcode content in the program body.\n"
        "- Typical sequence:\n"
        "  1) Search with the appropriate tools\n"
        "  2) Generate/format with `llm_tools.generate_content_llm`\n"
        "  3) Update completed slots via `set_progress(out_dyn_patch=...)`\n"
        "  4) Update progress via `set_progress(story_append=...)`\n"
        "  5) If the contract needs files, render them via write tools and update the file slot\n"
        "\n"
        "### Success and Failure\n"
        "- On success, call `await done()` exactly once after all slots are set.\n"
        "- On a managed failure, call:\n"
        "  `await fail(\"<what failed>\", where=\"<stage>\", error=str(e), out_dyn=_PROGRESS[\"out_dyn\"])` and return.\n"
        "\n"
        "### Example skeleton\n"
        "``python\n"
        "import json, asyncio\n"
        "\n"
        "async def main():\n"
        "    objective = \"Two-page brief on agent sandboxing\"\n"
        "    await set_progress(objective=objective, story_append=\"Started. Planned gather → generate → render.\", flush=True)\n"
        "\n"
        "    try:\n"
        "        await set_progress(story_append=\"Searching sources.\")\n"
        "        search_json = await agent_io_tools.tool_call(\n"
        "            fn=generic_tools.web_search,\n"
        "            params_json=json.dumps({\"queries\":[\"agent sandboxing 2024 2025\", \"agent isolated run 2024 2025\"], \"n\":8}),\n"
        "            call_reason=\"Find recent material\",\n"
        "            tool_id=\"generic_tools.web_search\"\n"
        "        )\n"
        "        sources = json.loads(search_json)\n"
        "        await set_progress(story_append=f\"Found {len(sources)} sources.\", flush=True)\n"
        "\n"
        "        await set_progress(story_append=\"Generating draft from sources.\")\n"
        "        gen = json.loads(await agent_io_tools.tool_call(\n"
        "            fn=llm_tools.generate_content_llm,\n"
        "            params_json=json.dumps({\n"
        "                \"agent_name\":\"Writer\",\n"
        "                \"instruction\":\"Produce a concise two-page explainer with citations.\",\n"
        "                \"sources_json\": json.dumps(sources),\n"
        "                \"cite_sources\": True,\n"
        "                \"target_format\":\"markdown\"\n"
        "            }),\n"
        "            call_reason=\"Draft explainer from sources\",\n"
        "            tool_id=\"llm_tools.generate_content_llm\"\n"
        "        ))\n"
        "        if not gen.get(\"ok\"):\n"
        "            await fail(\"Generation failed\", where=\"generate\", error=gen.get(\"reason\",\"\"), out_dyn=_PROGRESS[\"out_dyn\"])\n"
        "            return\n"
        "\n"
        "        draft_md = gen[\"content\"]\n"
        "        await set_progress(\n"
        "            story_append=\"Inline slot `analysis_md` ready.\",\n"
        "            out_dyn_patch={\n"
        "                \"analysis_md\": {\n"
        "                    \"type\":\"inline\", \"format\":\"markdown\", \"description\":\"Explainer draft\", \"value\": draft_md\n"
        "                }\n"
        "            },\n"
        "            flush=True\n"
        "        )\n"
        "\n"
        "        await set_progress(story_append=\"Rendering PDF.\")\n"
        "        pdf_rel = \"sandboxing_brief.pdf\"\n"
        "        await agent_io_tools.tool_call(\n"
        "            fn=generic_tools.write_pdf,\n"
        "            params_json=json.dumps({\"path\": str(OUTPUT / pdf_rel), \"content\": draft_md, \"format\":\"markdown\", \"title\":\"Agent Sandboxing\"}),\n"
        "            call_reason=\"Render PDF deliverable\",\n"
        "            tool_id=\"generic_tools.write_pdf\"\n"
        "        )\n"
        "        await set_progress(\n"
        "            story_append=\"File slot `report_pdf` ready.\",\n"
        "            out_dyn_patch={\n"
        "                \"report_pdf\": {\n"
        "                    \"type\":\"file\", \"path\": pdf_rel, \"mime\":\"application/pdf\",\n"
        "                    \"description\":\"Two-page brief\", \"text\": draft_md\n"
        "                }\n"
        "            },\n"
        "            status=\"Completed\",\n"
        "            flush=True\n"
        "        )\n"
        "\n"
        "        await done()  # writes ok:true; runtime finalizes project_log from progress\n"
        "    except Exception as e:\n"
        "        await fail(\"Unhandled\", where=\"main\", error=f\"{type(e).__name__}: {e}\", managed=False, out_dyn=_PROGRESS[\"out_dyn\"])\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    asyncio.run(main())\n"
        "``\n"
        "\n"
        "### Paths and files\n"
        "- Write files to `OUTPUT_DIR` (use `OUTPUT / \"<name.ext>\"`).\n"
        "- Store OUTPUT_DIR-relative paths in slots (e.g., 'report.pdf').\n"
        "\n"
        "### Privacy & brevity\n"
        "- Keep `story_append` lines short, past tense, and tool-agnostic.\n"
        "- Do not paste large texts into logs; slot content belongs in slots, not the story.\n"
        "\n"
        "## Style & behavior\n"
        "- Linear and concise. No prints. Keep the code within the line budget.\n"
        #"- USER-FACING status (if any) must be short and provider-agnostic.\n"
        "- THINKING part must be provider-agnostic, we show it to a user.\n"
    )
    sys += (
        f"• Keep main.py ≤ {line_budget} lines.\n"
        f"Assume today={today} (UTC).\n"
    )

    # ---------- Strict 3-section protocol ----------
    json_shape_hint = ("{"
    "  \"entrypoint\": \"python main.py\","
    "  \"files\": [ {\"path\": \"main.py\", \"content\": \"...\"} ],"
    "  \"outputs\": [ {\"filename\": \"result.json\", \"kind\": \"json\", \"key\": \"worker_output\"} ],"
    "  \"notes\": \"<=40 words\","
    "  \"result_interpretation_instruction\": \"<=120 words, tool-agnostic, concise\""
    "}")
    # sys = _add_3section_protocol(
    #     sys,
    #     json_shape_hint
    # )
    sys = _add_2section_protocol(sys, json_shape_hint=json_shape_hint)

    # ---------- Message: task + adapters with DOCS ----------
    adapters_for_llm = _adapters_public_view(adapters)
    contract_dyn = (decision or {}).get("output_contract_dyn") or {}

    msg_parts = [
        "TASK (objective + constraints for this program):",
        json.dumps(task or {}, ensure_ascii=False, indent=2),
        "",
        "SOLVABILITY / DECISION (read-only hints):",
        json.dumps(decision or {}, ensure_ascii=False, indent=2),
        "",
        "DYNAMIC OUTPUT CONTRACT YOU MUST FULFILL - output_contract_dyn (slot → description):",
        json.dumps(contract_dyn, ensure_ascii=False, indent=2),
        "",
    ]

    # ✅ INSERT PLAYBOOK HERE (if provided)
    if program_playbook and program_playbook.strip():
        msg_parts.extend([
            "=" * 80,
            "PROGRAM HISTORY PLAYBOOK",
            "=" * 80,
            "",
            program_playbook.strip(),
            "",
            "=" * 80,
            "",
        ])

    msg_parts.extend([
        "ADAPTERS — imports, call templates, is_async:",
        json.dumps([{k: v for k,v in a.items() if k in ('id','import','call_template','is_async')} for a in adapters_for_llm], ensure_ascii=False, indent=2),
        "",
        "TOOL DOCS (purpose/args/returns/constraints/examples):",
        json.dumps([{ 'id': a['id'], 'doc': a.get('doc', {}) } for a in adapters_for_llm], ensure_ascii=False, indent=2),
        "",
        "Produce the three sections as instructed."
    ])

    msg = "\n".join(msg_parts)

    # ---------- Stream ----------
    return await _stream_agent_sections_to_json(
        svc,
        client_name="solver_codegen",
        client_role="solver_codegen",
        sys_prompt=sys,
        user_msg=msg,
        schema_model=SolverCodegenOut,
        # on_thinking_delta=on_thinking_delta,
        on_progress_delta=on_thinking_delta,
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
    "\n"
    "HARD RULES:\n"
    "• Do NOT solve the task. Do NOT run tools. Do NOT invent facts, URLs, dates, or long free text.\n"
    "• Only select tools present in the catalog. For each selection, 'name' MUST equal the catalog 'id'.\n"
    "• ONLY suggest file generation tools (write_pdf, write_pptx, etc.) when:\n"
    "  1) The user explicitly requests a document, report, or file in THIS turn, OR\n"
    "  2) There is an open non-decayed historically request from recent conversation that remains unfulfilled (see CONVERSATIONAL STATE AWARENESS below)\n"
    "• For simple questions seeking information or guidance, prefer text-based tools and summaries over file generation.\n"
    "\n"
    "SMART TOOL SELECTION:\n"
    "• For explanatory/educational requests: prioritize knowledge search tools + text generation/editing (llm tools)\n"
    "• For calculation requests: use calc only for actual mathematical computations, not trivial arithmetic\n"
    "• For content creation: include edit_text_llm to structure and refine generated content\n"
    "• Avoid selecting tools that don't materially contribute to the user's request\n"
    "• Example: for 'explain how to compute..' → select relevant search tool to gather the context + relevant llm tool, NOT calc\n"
    "\n"
    "PARAMETER FILL POLICY (Scaffolding only):\n"
    "• Provide MINIMAL, PLAUSIBLE scaffolding for parameters (booleans, enums, small numerics, simple flags).\n"
    "• For any contentful parameter (e.g., text, content_md/markdown, sources_json, url lists, file bodies):\n"
    "    - Do NOT invent content. Use a short placeholder like \"<TBD at runtime>\" or omit the param.\n"
    "• Safe defaults are ok (e.g., n=5, max_tokens=300, style='brief'), but never prewrite summaries or links.\n"
    "\n"
    "CLOSED-PLAN COMPOSABILITY CHECK (CRITICAL):\n"
    "• The selected set must form a *closed plan* that can produce the user's requested deliverable end-to-end in a single run,\n"
    "  with NO human-in-the-loop and NO external steps. If a tool requires an input (e.g., content_md for a PDF renderer), ensure that\n"
    "  input is either provided by the user/context OR produced by another selected tool. Otherwise add the missing generator/transformer tool.\n"
    "• If the goal requires *text construction/transformation* (e.g., summary, outline, caption, extraction), include a text/LLM transformer tool from the catalog.\n"
    "• Minimize redundancy: avoid overlapping tools when one suffices; prefer the smallest closed set that keeps the plan feasible.\n"
    "\n"
    "AUTO-SELECTION RULES:\n"
    "• If multiple source-generating tools are selected, ALWAYS include ctx_tools.merge_sources\n"
    "• Source tools include *_search tools and any tool that claim returning sources/citations\n"
    "• merge_sources is required to prevent citation conflicts and ensure proper source deduplication\n"
    "\n"
    "SEQUENCING ASSUMPTION:\n"
    "• It is acceptable if outputs must flow between selected tools; the downstream solver will orchestrate (likely via codegen). Your job is to pick a feasible set.\n"
    "\n"
    "CONTEXT-AWARE SEARCH RULE:\n"
    "• If the user intent is to *add / expand / modify / improve*, and there's no strict anti-recommendation for adding a web/search tool, include such tools if relevant.\n"
    "\n"
    "CONVERSATIONAL STATE AWARENESS:\n"
    "• Understand the conversation as a continuous negotiation with open and closed requests.\n"
    "• An open request remains open until fulfilled (deliverable provided), not just because the conversation moved forward.\n"
    "• If the conversation shows:\n"
    "  - A request was made (user asked for something specific)\n"
    "  - Clarifications were exchanged (back-and-forth to understand requirements)\n"
    "  - No fulfillment occurred (no indication the deliverable was produced/delivered)\n"
    "  - Current query is topically related to that original request\n"
    "  → The request is STILL OPEN and is not seems decayed historically. Select tools to fulfill it, even if the current wording differs.\n"
    "• The user may:\n"
    "  - Rephrase the same request (\"report\" → \"summary\")\n"
    "  - Express impatience or simplify their ask\n"
    "  - Provide the final piece of information you were waiting for\n"
    "• All of these signal: \"proceed with what we've been discussing.\"\n"
    "\n"
    "OUTPUT FORMAT:\n"
    "• Return up to 5 candidates with reasons and minimal parameters.\n"
    "\n"
    "THINKING PART: No tool/provider/model names revealed - we show thinking to a customer.\n"
    # "INTERNAL THINKING (STATUS): tiny.\n"
    # "USER-FACING (STATUS): 2 short lines (focus; minimal plan). No tool/provider/model names.\n"
)
    ToolRouterOut.model_json_schema()
    sys += (
        "\nREUSE CONTEXT:\n"
        "• Downstream code will read prior runs from OUTPUT_DIR/context.json → program_history[]. "
        "Prefer selecting tools that can EDIT or UPDATE existing deliverables when appropriate (e.g., LLM editor, file writer), "
        "instead of rebuilding everything from scratch. Only applicable if the new request applies to a past program in the context.\n"
    )
    json_shape_hint = ("{ \"candidates\": ["
                       "  {\"name\": \"<tool_id>\", \"reason\": \"...\", \"confidence\": 0..1, \"parameters\": {\"a\": \"hello\"}}"
                       "], \"notes\": \"(<=25 words)\" }"
                       )
    # sys = _add_3section_protocol(
    #     sys,
    #     json_shape_hint=json_shape_hint
    # )

    sys = _add_2section_protocol(sys, json_shape_hint=json_shape_hint)

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
        # on_thinking_delta=on_thinking_delta,
        on_progress_delta=on_thinking_delta,
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
    format: Optional[Literal["markdown","text","json","url","xml","yaml"]] = None
    # For file
    mime: Optional[str] = None
    filename_hint: Optional[str] = None

    description: str = ""
    citable: Optional[bool] = None
    source_hint: Optional[str] = Field(default=None, description="Either 'program' or an adapter id to prefer (e.g., 'generic_tools.write_pdf').")
    min_count: int = 1
    max_count: Optional[int] = 1

class SolvabilityOut(BaseModel):
    solvable: bool
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    reasoning: str = ""
    selected_tools: List[str] = Field(default_factory=list)
    clarifying_questions: List[str] = Field(default_factory=list)

    solver_mode: Literal["direct_tools_exec","codegen","llm_only"] = "llm_only"
    solver_instruction: str = ""

    # DYNAMIC OUTPUT CONTRACT (typed):
    # map: slot_name -> { "type": "inline"|"file", "description": str, ... }
    # For "inline":  required: {"format": "markdown"|"text"|"json"|"url"}
    # For "file":    required: {"mime": "<mime>"}, optional: {"filename_hint": "<basename>"}
    output_contract_dyn: Optional[Dict[str, Dict[str, Any]]] = Field(default_factory=dict)

    context_use: bool = True
    # project_canvas_slot: str = "project_canvas"
    project_log_slot: str = "project_log"
    history_select: str = "latest"
    history_select_hint: str = ""
    citations_source_path: str = "program_history[].<exec>.web_links_citations.items"
    instructions_for_downstream: str = ""



async def assess_solvability_stream(
        svc: ModelServiceBase,
        user_text: str,
        candidates: List[Dict[str, Any]],
        policy_summary: str = "",
        prefs_hint: Dict[str, Any] | None = None,
        *,
        context_hint: Optional[str] = None,
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
        "- If solvable, emit a minimal **TYPED DYNAMIC OUTPUT CONTRACT** (`output_contract_dyn`) the downstream program MUST fulfil.\n"
        "- output_contract_dyn format is the str → {type, description, ...}: key-value mapping of the slot name to the detailed text description  of what this slot represents and the slot type.\n"
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
        # "## Project canvas (critical)\n"        
        # "- ALWAYS include a slot named **`project_canvas`** in `output_contract_dyn`.\n"
        # "- `project_canvas` is GLUE: user-facing Markdown that references artifacts by slot/filename and carries the narrative/notes linking them.\n"
        # "- If the request modifies a prior project, prefer **edit/update** over full regeneration. Canvas helps understand which resources (slots) to load for edit.\n"        
        # "- It MUST NOT embed full artifacts. The artifacts full texts are available in the slots, 'text' field.\n"
        # "\n"
        "## Project log (critical; single live doc)n"
        # "- ALWAYS include a slot named **`project_log`** in `output_contract_dyn`. This is the continuous slot which is filled during project by its internal editors.\n"
        'ALWAYS include a slot named **`project_log`** in `output_contract_dyn` with `format:"markdown"`.\n'
        "- The log is a **live, user-facing run log** and narrative:\n"
        "- shows **Objective**, **Status**, **Steps (this run)**, and **Produced slots** (by slot name, with filename/mime for files).\n"
        "- Do **NOT** embed slot values (no text previews, no “Text repr”). All values live in their slots.\n"
        # "- The project log must contain the description of objective, actual edits to take and the user current intent or preferences on this turn\n"
        "- When continuing work, edit/update the prior log rather than regenerating from scratch.\n"
        "## Output Contract Rules\n"
        # "- ALWAYS include: project_canvas, project_log (change history)\n"
        "- ALWAYS include: project_log (live narrative; format: \"markdown\").\n"
        "- ALL slots are final user-facing artifacts. Each slot MUST have a text representation. ★\n"
        "  • Inline slots: text is the slot's `value` (string or JSON).\n"
        "  • File slots: text MUST be provided via the slot's `text` attribute. Always. ★\n"
        "- Do NOT create extra inline slots to hold the file slot text. File slot text attribute is mandatory and contain the contents of this file, text representation\n"
        "  If the user wants the text as a separate deliverable, the contract will include a distinct inline slot explicitly. ★\n"
        "- FILE SLOT INVARIANT: If the user requests N distinct file formats, include exactly N file slots (one per format).\n"
        "- Contract slots must contain ONLY high-level descriptions (no content or excerpts).\n"
        "- Example: 'risk_summary_md': 'Guide explaining risk scoring methodology' NOT 'This report provides...'\n"
        "- CONDITIONALLY include file slots (pdf_file, slides_pptx, etc.) only when:\n"
        "  • User explicitly requests a document, report, presentation, or file\n"
        "  • User uses words like 'create a report', 'generate document', 'make a presentation'\n"
        "  • User asks for something 'to download', 'to share', or 'as a file'\n"
        "  • User is continuing work on an existing document/report and needs it rendered\n"
        "- For informational queries seeking guidance or explanations, use only inline slots\n"
        "- Default to minimal contracts that match user intent, not comprehensive deliverables\n"
        "\n"
        "## Slot naming\n"
        "- snake_case.\n"
        "- Files: `pdf_file`, `slides_pptx`, `image_png`, `csv_file`, `zip_bundle`.\n"
        "- Text/struct:  `summary_md`, `outline_md`, `table_md`, `data_json`, `plan_md`.\n"
        "- SLOT INVARIANT (critical): If the user requests N distinct 'artifacts', the contract MUST include exactly N slots (one per artifact). Do not collapse slots.\n"
        "- FILE SLOT (critical): If the slot is of type 'file', do not create extra content slots that merely mirror a file’s textual representation.\n"
        "- Surrogates: For FILE slots, the faithful textual surrogate MUST go in `text`. Do not add a twin inline slot for that surrogate.\n"
        " Only create a separate inline slot when the content is not associated with the file slot."
        "- Surrogates for file slots: `text` MUST contain the faithful textual representation. ★\n"
        "  • If the file was rendered from text (e.g., Markdown → PDF), put that exact source text into `text`. ★\n"
        "  • If the file is textual (CSV/JSON/Markdown/etc.), put its full textual content into `text`. ★\n"
        "  • If the file is binary (image, diagram, etc.), put a dense, faithful textual surrogate (caption + essential structure/metadata); include OCR if available. ★\n"
        "  • Never use a vague blurb; the surrogate must be sufficient for downstream reading/editing without opening the file. ★\n"
        
        
        "## Instruction for downstream agent\n"
        "`instructions_for_downstream` must include the instruction for downstream agent.\n"
        " If mode == 'llm_only', describe why the solution cannot be solved by solver (no such tools or not enough data, or the solution cannot be solved completely) so the final downstream llm decide how to answer to a user w/o solver, including the confessing the uncertainty\n"
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
        "INTERNAL THINKING: VERY concise and condensed. No real tool/provider names mentions. No any lengthy reasoning allowed.\n"
        # "USER-FACING STATUS: two short lines (assessment; next action). No tool/provider names.\n"
    )
    json_shape_hint = ("{"
                       "  \"solvable\": bool,"
                       "  \"confidence\": 0..1,"
                       "  \"reasoning\": \"(<=25 words)\","
                       "  \"selected_tools\": [\"<tool_id>\"],"
                       "  \"context_use\": bool,"
                       "  \"clarifying_questions\": [\"...\",\"...\"],"
                       "  \"solver_mode\": \"direct_tools_exec\"|\"codegen\"|\"llm_only\","
                       "  \"instructions_for_downstream\": \"(<=45 words)\","
                       "  \"output_contract_dyn\": {"
                       "      \"<slot>\": {\"type\":\"inline\",\"description\":\"...\",\"format\":\"markdown\"},"
                       "      \"<slot2>\": {\"type\":\"file\",\"description\":\"...\",\"mime\":\"application/pdf\",\"filename_hint\":\"report.pdf\"}"
                       "  }"
                       "}"
                       )
    # sys = _add_3section_protocol(
    #     sys,
    #     json_shape_hint
    # )
    sys = _add_2section_protocol(sys, json_shape_hint=json_shape_hint)
    topic_line = f"Topics: {', '.join(topics[:6])}" if topics else ""
    # domain_line = f"is_spec_domain={is_spec_domain!s}"
    msg = (
        # f"{domain_line}\n{topic_line}\n"
        f"{topic_line}\n"
        f"User question:\n{user_text}\n"
        f"Policy/context summary:\n{policy_summary[:800]}\n"
        f"Preferences hint (use to constrain what to produce/avoid):\n{json.dumps((prefs_hint or {}), ensure_ascii=False)[:1200]}\n"
        f"Conversation cue:\n{context_hint}\n\n"
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
        # on_thinking_delta=on_thinking_delta,
        on_progress_delta=on_thinking_delta,
        ctx="solvability",
        max_tokens=max_tokens,
    )
    out = out or {}
    agent_response = out.setdefault("agent_response", {})
    elog = out.setdefault("log", {})
    internal_thinking = out.get("internal_thinking")
    error = elog.get("error")

    agent_response_tools_to_use = agent_response.get("selected_tools") or []
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
    agent_response["selected_tools"] = tools

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

