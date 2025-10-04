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
        program_playbook: Optional[str] = None,
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
        "## Contract & Inputs\n"
        "- `output_contract_dyn` in this prompt is authoritative: slot_name → {\"type\":\"inline\"|\"file\", \"description\":str, ...}\n"
        "- It describes exactly what slots this program must produce\n"
        "\n"
        "## Program History & Artifact Retrieval\n"
        "\n"
        "### Context Available\n"
        "- PROGRAM HISTORY PLAYBOOK below shows: turn_ids, dates, user requests, deliverable previews with sizes\n"
        "- Truncation signals: `...(...N more chars)` = incomplete preview, fetch for full content\n"
        "- Fetch warnings: `⚠ Full content available via fetch_turn_artifacts([\"turn_id\"])` = artifact > 300 chars\n"
        "- Turn labels: `[CURRENT TURN]` = this execution, `[HISTORICAL]` = prior runs\n"
        "\n"
        "### When to Fetch\n"
        "Call `ctx_tools.fetch_turn_artifacts`:\n"
        "- ALWAYS for edit/update/extend/continue tasks\n"
        "- When the playbook shows relevant prior work\n"
        "- When building on prior deliverables\n"
        "- When building on user/assistant message\n"
        "Never restate the lengthy parts of the artifacts, instead generate the instruction which fetch that and provide directly to LLM-consumer\n"
        "\n"
        "### Access Patterns\n"
        "```python\n"
        "# Fetch turns (see tool doc for full signature)\n"
        "turns = json.loads(await agent_io_tools.tool_call(\n"
        "    fn=ctx_tools.fetch_turn_artifacts,\n"
        "    params_json=json.dumps({\"turn_ids\": '[\"cg-abc123\", \"cg-xyz789\"]'}),\n"
        "    call_reason=\"Load prior work\",\n"
        "    tool_id=\"ctx_tools.fetch_turn_artifacts\"\n"
        "))\n"
        "\n"
        "# Access artifacts\n"
        "turn = turns['cg-abc123']\n"
        "existing_text = turn['deliverables']['slot_name']['text']           # Full text with [[S:n]]\n"
        "existing_sources = turn['deliverables']['slot_name']['sources_used'] # [{sid, url, title, text}]\n"
        "prior_log = turn['program_log']['text']                              # Previous run log\n"
        "user_request = turn['user_msg']                                      # Original user message\n"
        "\n"
        "# For editing: ALWAYS merge sources (keeps SIDs stable)\n"
        "unified = json.loads(await agent_io_tools.tool_call(\n"
        "    fn=ctx_tools.merge_sources,\n"
        "    params_json=json.dumps({\"source_collections\": json.dumps([existing_sources, new_sources])}),\n"
        "    call_reason=\"Merge sources - preserves [[S:n]]\",\n"
        "    tool_id=\"ctx_tools.merge_sources\"\n"
        "))\n"
        "```\n"
        "\n"
        "### Key Rules for Artifact Retrieval\n"
        "- `text` field contains the content with [[S:n]] tokens using stable, global SIDs\n"
        "- `sources_used` provides full source objects matching those SIDs\n"
        "- When editing: ALWAYS merge `sources_used` + new sources via `ctx_tools.merge_sources`\n"
        "- `merge_sources` preserves old SIDs - existing citations remain valid\n"
        "- For code/JSON/structured content: parse `text` field, strip code fences if needed\n"
        "- Never renumber [[S:n]] from fetched content\n"
        "\n"
        "## Chat/User Message Access\n"
        "- Current user message: `turns['<current_turn_id>']['user_msg']` (from fetch_turn_artifacts)\n"
        "- Use minimal excerpts only; never dump full messages into logs/files\n"
        "- Never log full user or assistant text; store offsets/keys instead\n"
        "\n"
        "## Language & Syntax\n"
        "- Python 3.11. Use `True/False/None`. Build JSON with `json.dumps(...)`.\n"
        "\n"
        "## Imports & Calls (hard rules)\n"
        "- Paste adapter imports EXACTLY as provided; do not alter module paths/aliases.\n"
        "- Call functions exactly per provided `call_template`.\n"
        "- Import wrapper: `from io_tools import tools as agent_io_tools`.\n"
        "- **Wrap EVERY adapter call with the wrapper** (logging + indexing):\n"
        "  ```python\n"
        "  res = await agent_io_tools.tool_call(\n"
        "      fn=<alias>.<fn>,\n"
        "      params_json=json.dumps({<kwargs>}),\n"
        "      call_reason=\"<5–12 words why this call is needed>\",\n"
        "      tool_id=\"<qualified id exactly as in ADAPTERS list>\"\n"
        "  )\n"
        "  ```\n"
        "\n"
        "## Runtime Contract\n"
        "- A global `OUTPUT_DIR` is injected at runtime. Do **not** redefine it.\n"
        "- Write **all** files into `OUTPUT_DIR`.\n"
        "- You never write files directly, only via the generic_tools.write_* tools! Files you write directly will be lost\n"
        "\n"
        "## Persistence (required)\n"
        "- Finish by writing the final result: `await agent_io_tools.save_ret(data=json.dumps(result))`.\n"
        "- On success include: `ok=true`, `objective` and `out_dyn` (filled **exactly** per output_contract_dyn).\n"
        "\n"
        "## Slot contract fulfillment\n"
        "- Use exactly the slot names from `output_contract_dyn` as keys in `out_dyn`.\n"
        "- For INLINE slots: produce `{\"type\":\"inline\", \"format\": <from contract>, \"description\":\"...\", \"value\": <text or json>}`.\n"
        "- For FILE slots: produce `{\"type\":\"file\", \"path\":\"<OUTPUT_DIR-relative>\", \"mime\":\"<from contract>\", \"description\":\"...\", \"text\":\"<faithful textual surrogate>\"}`.\n"
        "\n"
        "**Rules:**\n"
        "- TEXT REPRESENTATION (TEXT SURROGATE) IS MANDATORY FOR EVERY SLOT. ★\n"
        "  • Inline: the `value` string/JSON is the text.\n"
        "  • File:   `text` MUST be present. Always. ★\n"        
        "- How to set FILE `text`: ★\n"
        "  • If you rendered the file from text (e.g., Markdown → PDF, PPTX from outline), assign that exact source text to the file's `text`.\n"
        "  • If the file program produces is textual (CSV/JSON/Markdown), assign the full textual content to `text`.\n"
        "  • If the file is binary (image/diagram/etc.), write a dense surrogate (caption + structure/sections/labels; include OCR if available). Avoid vague blurbs.\n"
        "- No shadow inline slots: Do NOT create a separate inline slot just to hold the file's text unless the contract explicitly includes it. ★\n"
        "- `resource_id` equals the slot name (infra prefixes with `slot:`). Store OUTPUT_DIR-relative paths.\n"
        "\n"
        "## Content generation rules (CRITICAL)\n"
        "- You MUST use tools to generate ALL content dynamically - never pre-write content\n"
        "- You can only write instructions/prompts for LLM tools - never write the final content yourself\n"
        "- Let the tools generate the actual content based on your instructions\n"
        "- Use tools in this typical flow:\n"
        "  1) Search/gather information (relevant search tool(s))\n"
        "  2) Generate/structure content (llm tools)\n"
        "  3) Edit/assemble one or more slot texts with tool-generated content\n"
        "  4) Generate files only if contract output_contract_dyn requires them\n"
        "\n"
        "## Required tool flow (guidance)\n"
        "- search/gather → process/generate → structure/edit → output\n"
        "  ✓ Use search tools to find information\n"
        "  ✓ Use generate_content_llm to create/refine/summarize/format/structure/structured explanation\n"
        "  ✗ Don't use calc for trivial arithmetic\n"
        "  ✗ Don't pre-write explanations directly into out_dyn\n"
        "\n"
        "## Mandatory Slot: project_log (SEMANTIC STORY)\n"
        "Live Semantic narrative of THIS run. Do NOT embed any slot values or previews (no excerpts, no \"Text repr\"). Values live in slots. Tool calls are captured elsewhere.\n"
        "\n"
        "Structure\n"
        "```markdown\n"
        "# Project Log\n"
        "## Objective\n"
        "<1 sentence>\n"
        "## Status\n"
        "<'In progress' | 'Completed' | 'Failed: reason'>\n"
        "## Story\n"
        "<Dense narrative. Past tense. What happened and why.>\n"
        "## Produced Slots\n"
        "### slot_name (inline|file)\n"
        "<Contract description>\n"
        "**Format/Mime:** ...\n"
        "**Filename:** <if file>\n"
        "```\n"
        "\n"
        "Implementation\n"
        "```python\n"
        "story = []\n"
        "\n"
        "# If editing, fetch prior work\n"
        "if is_edit_task:\n"
        "    story.append(\"Fetching prior work from Turn cg-abc123.\")\n"
        "    turns = json.loads(await agent_io_tools.tool_call(\n"
        "        fn=ctx_tools.fetch_turn_artifacts,\n"
        "        params_json=json.dumps({\"turn_ids\": '[\"cg-abc123\"]'}),\n"
        "        call_reason=\"Load prior analysis\",\n"
        "        tool_id=\"ctx_tools.fetch_turn_artifacts\"\n"
        "    ))\n"
        "    existing = turns['cg-abc123']['deliverables']['analysis_md']['text']\n"
        "    story.append(\"Loaded `analysis_md` from previous run.\")\n"
        "\n"
        "story.append(\"Searching for Q4 market data.\")\n"
        "sources = await tool_call(...)\n"
        "story.append(f\"Found {len(sources)} sources.\")\n"
        "\n"
        "story.append(\"Generating risk assessment.\")\n"
        "analysis = await tool_call(...)\n"
        "story.append(\"Slot `risk_analysis` ready.\")\n"
        "\n"
        "try:\n"
        "    pdf = await tool_call(...)\n"
        "    story.append(\"Slot `report_pdf` ready - file: q4_report.pdf\")\n"
        "    status = \"Completed\"\n"
        "except Exception as e:\n"
        "    story.append(f\"PDF failed: {str(e)[:50]}\")\n"
        "    status = \"Partial: PDF failed\"\n"
        "\n"
        "# Build slots section (metadata only)\n"
        "slots_md = \"\"\n"
        "for name, data in out_dyn.items():\n"
        "    if name == \"project_log\": continue\n"
        "    slots_md += f\"\\n### {name} ({data['type']})\\n{data['description']}\\n\"\n"
        "    slots_md += f\"**Format:** {data['format']}\\n\" if data['type']=='inline' else f\"**Mime:** {data['mime']}\\n**Filename:** {data['path']}\\n\"\n"
        "\n"
        "log = f\"\"\"# Project Log\\n\\n## Objective\\n{objective}\\n\\n## Status\\n{status}\\n\\n## Story\\n{' '.join(story)}\\n\\n## Produced Slots\\n{slots_md}\"\"\"\n"
        "\n"
        "out_dyn[\"project_log\"] = {\"type\":\"inline\", \"format\":\"markdown\", \"description\":\"Semantic story\", \"value\":log.strip()}\n"
        "```\n"
        "\n"
        "Rules\n"
        "1. Write as you go - past tense, dense prose\n"
        "2. Say WHY: 'need current data', 'building on prior work'\n"
        "3. Say WHAT: 'Slot X ready' or 'Slot X failed: reason'\n"
        "4. Keep ≤500 words total\n"
        "\n"

        "5) **Fill all slots:**\n"
        "   - Fill all slots exactly per output_contract_dyn contract.\n"
        "   - Example: `out_dyn[\"project_log\"] = {\"type\":\"inline\", \"format\":\"markdown\", \"description\":\"Semantic story for this run\", \"value\": project_log_md}`\n"
        "\n"
        "## File/path rules\n"
        "- All files must physically live in `OUTPUT_DIR`.\n"
        "- Store `OUTPUT_DIR`-relative paths in `out_dyn` (e.g., `\"rust_advances.pdf\"`).\n"
        "\n"
        "## Error handling\n"
        "- `fail(...)` is injected globally. Use it to preserve partial work.\n"
        "- Pattern for managed failures:\n"
        "  ```python\n"
        "  try:\n"
        "      # risky operation\n"
        "  except Exception as e:\n"
        "      story.append(f\"Failed at X: {str(e)[:50]}\")\n"
        "      await fail(\"X failed\", where=\"stage_name\", error=str(e), out_dyn=out_dyn)\n"
        "      return\n"
        "  ```\n"
        "- Final safety net:\n"
        "  ```python\n"
        "  try:\n"
        "      # ... all work ...\n"
        "  except Exception as e:\n"
        "      await fail(\"Unhandled\", where=\"main\", error=str(e), managed=False, out_dyn=out_dyn)\n"
        "  ```\n"
        "- CRITICAL: Always pass `out_dyn` to preserve filled slots.\n"
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
        on_thinking_delta=on_thinking_delta,
        ctx=ctx,
        max_tokens=6000,
    )

# async def solver_codegen_stream(
#         svc: ModelServiceBase,
#         *,
#         task: Dict[str, Any],
#         adapters: List[Dict[str, Any]],
#         solvability: Optional[Dict[str, Any]] = None,
#         on_thinking_delta=None,
#         ctx: Optional[str] = "solver_codegen",
# ) -> Dict[str, Any]:
#     """
#     Generates a self-contained Python 3.11 program that:
#       - imports & calls ONLY the adapters we provide (your real libs) WITHIN documented usage
#       - reads INPUTS from OUTPUT_DIR/context.json and OUTPUT_DIR/task.json if present
#       - writes results to OUTPUT_DIR/<files specified in outputs[]>
#       - prints nothing (silent), robust error handling
#     """
#
#     today = _today_str()
#
#     # pull optional decision & constraints coming from the planner/ToolManager
#     decision = (solvability or {})  # may include tools_to_use, reasoning, output_contract_dyn
#     constraints = (task or {}).get("constraints") or {}
#
#     # reasonable defaults
#     line_budget = int(constraints.get("line_budget", 80))
#     prefer_single_call = bool(constraints.get("prefer_single_call", True) or constraints.get("prefer_direct_tools_exec", False))
#     minimize_logic = bool(constraints.get("minimize_logic", True))
#     concise = bool(constraints.get("concise", True))
#
#     # ---------- System prompt (authoritative; no ambiguity) ----------
#     sys = (
#         "# Codegen — single Python program\n"
#         "\n"
#         "## Authoritative inputs for **this** run\n"
#         "- The **dynamic output contract** `output_contract_dyn` is provided **in THIS prompt**. Treat it as the single source of truth.\n"
#         "- output_contract_dyn contract is SOLELY a dict: slot_name → {\"type\":\"inline\"|\"file\", \"description\":str, ...} (never actual content)\n"
#         "\n"
#         "## When to read context/chat\n"
#         "- Call `ctx_tools.fetch_working_set(select=\"latest\")` at the START of any edit/update/extend turn, OR whenever you need the full current user message.\n"
#         "- Use `ws['existing_project_log']` as the previous working log and `ws['existing_sources']` as `prior_sources`.\n"
#         "- Chat slice (use minimal excerpts only):\n"
#         "  • `ws['current_user']['text']` — this turn's user message\n"
#         "  • `ws.get('previous_user', {}).get('text')` — relevant prior user message (if any)\n"
#         "  • `ws.get('previous_assistant', {}).get('text')` — relevant prior assistant reply (if any)\n"
#         "- Do not restate the user request. Read `ws['current_user']['text']` when needed for downstream extraction/analysis.\n"
#         "- Only extract minimal spans necessary; never dump full messages into logs/files.\n"
#         "- Never log full user or assistant text; store offsets/keys instead.\n"
#         "- Previous solver deliverables (slots): `ws['previous_deliverables'][<slot_name>].text` for slot text representation.\n"
#         "\n"
#         "## Language & syntax\n"
#         "- Python 3.11. Use `True/False/None`. Build JSON with `json.dumps(...)`.\n"
#         "\n"
#         "## Imports & calls (hard rules)\n"
#         "- Paste adapter imports **exactly** as provided; do not alter module paths or aliases.\n"
#         "- Call functions exactly per the provided `call_template`.\n"
#         "- Import the infra wrapper: `from io_tools import tools as agent_io_tools`.\n"
#         "- **Wrap EVERY adapter call with the wrapper** (logging + indexing):\n"
#         "  ```python\n"
#         "  res = await agent_io_tools.tool_call(\n"
#         "      fn=<alias>.<fn>,\n"
#         "      params_json=json.dumps({<kwargs>}),\n"
#         "      call_reason=\"<5–12 words why this call is needed>\",\n"
#         "      tool_id=\"<qualified id exactly as in ADAPTERS list>\"\n"
#         "  )\n"
#         "  ```\n"
#         "\n"
#         "## Runtime contract\n"
#         "- A global `OUTPUT_DIR` is injected at runtime. Do **not** redefine it.\n"
#         "- Write **all** files into `OUTPUT_DIR`.\n"
#         "\n"
#         "## Persistence (required)\n"
#         "- Finish by writing the final result: `await agent_io_tools.save_ret(data=json.dumps(result))`.\n"
#         "- On success include: `ok=true`, `objective` and `out_dyn` (filled **exactly** per output_contract_dyn).\n"
#         "\n"
#         "## Slot contract fulfillment\n"
#         "- Use exactly the slot names from `output_contract_dyn` as keys in `out_dyn`.\n"
#         "- For INLINE slots: produce `{\"type\":\"inline\", \"format\": <from contract>, \"description\":\"...\", \"value\": <text or json>}`.\n"
#         "- For FILE slots: produce `{\"type\":\"file\", \"path\":\"<OUTPUT_DIR-relative>\", \"mime\":\"<from contract>\", \"description\":\"...\", \"text\":\"<faithful textual surrogate>\"}`.\n"
#         "\n"
#         "**Rules:**\n"
#         "- TEXT REPRESENTATION (TEXT SURROGATE) IS MANDATORY FOR EVERY SLOT. ★\n"
#         "  • Inline: the `value` string/JSON is the text.\n"
#         "  • File:   `text` MUST be present. Always. ★\n"
#         "- How to set FILE `text`: ★\n"
#         "  • If you rendered the file from text (e.g., Markdown → PDF, PPTX from outline), assign that exact source text to the file’s `text`.\n"
#         "  • If the file is textual (CSV/JSON/Markdown), read and assign the full textual content to `text`.\n"
#         "  • If the file is binary (image/diagram/etc.), write a dense surrogate (caption + structure/sections/labels; include OCR if available). Avoid vague blurbs.\n"
#         "- No shadow inline slots: Do NOT create a separate inline slot just to hold the file’s text unless the contract explicitly includes it. ★\n"
#         #"- `resource_id` for each artifact equals the slot name (infra prefixes with `slot:`).\n"
#         #"- All paths in `out_dyn` are `OUTPUT_DIR`-relative.\n"
#         "- `resource_id` equals the slot name (infra prefixes with `slot:`). Store OUTPUT_DIR-relative paths.\n"
#         "\n"
#         "## Content generation rules (CRITICAL)\n"
#         "- You MUST use tools to generate ALL content dynamically - never pre-write content\n"
#         "- You can only write instructions/prompts for LLM tools - never write the final content yourself\n"
#         "- Let the tools generate the actual content based on your instructions\n"
#         "- Use tools in this typical flow:\n"
#         "  1) Search/gather information (relevant search tool(s))\n"
#         "  2) Generate/structure content (llm tools)\n"
#         "  3) Edit/assemble one or more slot texts with tool-generated content\n"
#         "  4) Generate files only if contract output_contract_dyn requires them\n"
#         "\n"
#         "## Required tool flow (guidance)\n"
#         "- search/gather → process/generate → structure/edit → output\n"
#         "  ✓ Use search tools to find information\n"
#         "  ✓ Use summarize_llm to create structure/structured explanation\n"
#         "  ✓ Use edit_text_llm to refine/format\n"
#         "  ✗ Don't use calc for trivial arithmetic\n"
#         "  ✗ Don't pre-write explanations directly into out_dyn\n"
#         "\n"
#         "## Mandatory slot: project_log (SEMANTIC STORY)\n"
#         "\n"
#         "Live Semantic narrative of THIS run. Do NOT embed any slot values or previews (no excerpts, no \"Text repr\"). Values live in slots. Tool calls are captured elsewhere.\n"
#         "\n"
#         "### Structure\n"
#         "```markdown\n"
#         "# Project Log\n"
#         "## Objective\n"
#         "<1 sentence>\n"
#         "## Status\n"
#         "<'In progress' | 'Completed' | 'Failed: reason'>\n"
#         "## Story\n"
#         "<Dense narrative. Past tense. What happened and why.>\n"
#         "## Produced Slots\n"
#         "### slot_name (inline|file)\n"
#         "<Contract description>\n"
#         "**Format/Mime:** ...\n"
#         "**Filename:** <if file>\n"
#         "```\n"
#         "\n"
#         "### Implementation\n"
#         "```python\n"
#         "story = []\n"
#         "story.append(\"Fetching prior work from context.\")\n"
#         "ws = ctx_tools.fetch_working_set(select=\"latest\")\n"
#         "story.append(f\"Loaded `{slot}` from previous run.\")\n"
#         "\n"
#         "story.append(\"Searching for Q4 market data.\")\n"
#         "sources = await tool_call(...)\n"
#         "story.append(f\"Found {len(sources)} sources.\")\n"
#         "\n"
#         "story.append(\"Generating risk assessment.\")\n"
#         "analysis = await tool_call(...)\n"
#         "story.append(\"Slot `risk_analysis` ready.\")\n"
#         "\n"
#         "try:\n"
#         "    pdf = await tool_call(...)\n"
#         "    story.append(\"Slot `report_pdf` ready - file: q4_report.pdf\")\n"
#         "    status = \"Completed\"\n"
#         "except Exception as e:\n"
#         "    story.append(f\"PDF failed: {str(e)[:50]}\")\n"
#         "    status = \"Partial: PDF failed\"\n"
#         "\n"
#         "# Build slots section (metadata only)\n"
#         "slots_md = \"\"\n"
#         "for name, data in out_dyn.items():\n"
#         "    if name == \"project_log\": continue\n"
#         "    slots_md += f\"\\n### {name} ({data['type']})\\n{data['description']}\\n\"\n"
#         "    slots_md += f\"**Format:** {data['format']}\\n\" if data['type']=='inline' else f\"**Mime:** {data['mime']}\\n**Filename:** {data['path']}\\n\"\n"
#         "\n"
#         "log = f\"\"\"# Project Log\\n\\n## Objective\\n{objective}\\n\\n## Status\\n{status}\\n\\n## Story\\n{' '.join(story)}\\n\\n## Produced Slots\\n{slots_md}\"\"\"\n"
#         "\n"
#         "out_dyn[\"project_log\"] = {\"type\":\"inline\", \"format\":\"markdown\", \"description\":\"Semantic story\", \"value\":log.strip()}\n"
#         "```\n"
#         "\n"
#         "### Rules\n"
#         "1. Write as you go - past tense, dense prose\n"
#         "2. Say WHY: 'need current data', 'building on prior work'\n"
#         "3. Say WHAT: 'Slot X ready' or 'Slot X failed: reason'\n"
#         "4. Keep ≤500 words total\n"
#         "\n"
#         "## Reference pipeline\n"
#         "\n"
#         "1) **Editing or fresh start:**\n"
#         "   - If editing, `ws = ctx_tools.fetch_working_set(select=\"latest\")` then start from `ws['existing_deliverables'][<slot_name>].text` and treat `ws['existing_sources']` as prior_sources.\n"
#         "   - Alternatively, the previous work can be found in `(ws['previous_assistant'] or {}).get('text')`.\n"
#         "   - If not editing, start fresh.\n"
#         "\n"
#         "2) **Source management rules:**\n"
#         "   - When multiple source tools are used, ALWAYS call `ctx_tools.merge_sources`.\n"
#         "   - Pattern: `unified_sources = ctx_tools.merge_sources(source_collections=json.dumps([sources1, sources2, sources3]))`.\n"
#         "\n"
#         "3) **LLM tool usage:**\n"
#         "   - Prepare GUIDANCE for LLM tools defining what to do with current inputs.\n"
#         "   - GUIDANCE can come from another llm tool or you write it based on current purpose.\n"
#         "   - Example call:\n"
#         "     ```python\n"
#         "     edited = generic_tools.edit_text_llm(\n"
#         "         text=existing_slot_text + GUIDANCE,\n"
#         "         instruction='Apply GUIDANCE; keep structure; no invented facts; add [[S:n]] only on NEW/CHANGED claims; REMOVE GUIDANCE block.',\n"
#         "         keep_formatting=True,\n"
#         "         sources_json=json.dumps(unified_sources),\n"
#         "         cite_sources=True,\n"
#         "         forbid_new_facts_without_sources=True\n"
#         "     )\n"
#         "     ```\n"
#         "\n"
#         "4) **Rendering files:**\n"
#         "- Pass the SAME `unified_sources` to renderers (e.g., write_pdf) with `resolve_citations=True` when applicable.\n"
#         "- After rendering, set the file slot’s `text` to the exact input text used for rendering (or to the file’s full textual content if it is a textual format). ★\n"
#         "# Example (assign text to file slot; no extra inline slot)\n"
#         "pdf_path = f\"{OUTPUT_DIR}/briefing.pdf\"\n"
#         "await agent_io_tools.tool_call(\n"
#         "  fn=generic_tools.write_pdf,\n"
#         "  params_json=json.dumps({\"path\": pdf_path, \"content_md\": briefing_md, \"title\": \"Briefing\"}),\n"
#         "  call_reason=\"Render briefing as PDF\",\n"
#         "  tool_id=\"generic_tools.write_pdf\"\n"
#         ")\n"
#         "out_dyn[\"pdf_file\"] = {\n"
#         "  \"type\": \"file\", \"path\": \"briefing.pdf\", \"mime\": \"application/pdf\",\n"
#         "  \"description\": \"Stakeholder briefing PDF\",\n"
#         "  \"text\": briefing_md  # the exact source text used to render ★\n"
#         "}\n"
#         "\n"
#         "5) **Fill all slots:**\n"
#         "   - Fill all slots exactly per output_contract_dyn contract.\n"
#         "   - Example: `out_dyn[\"project_log\"] = {\"type\":\"inline\", \"format\":\"markdown\", \"description\":\"Semantic story for this run\", \"value\": project_log_md}`\n"
#         "\n"
#         "## File/path rules\n"
#         "- All files must physically live in `OUTPUT_DIR`.\n"
#         "- Store `OUTPUT_DIR`-relative paths in `out_dyn` (e.g., `\"rust_advances.pdf\"`).\n"
#         "\n"
#         "## Error handling\n"
#         "- A runtime helper `fail(...)` is injected into your program and available globally.\n"
#         "- Managed errors: call `await fail(\"<short description>\", where=\"<stage>\", details=\"<why>\")` and return immediately.\n"
#         "- Unhandled exceptions: wrap `main()` in try/except; in except, call `await fail(\"Unhandled exception\", where=\"main\", error=str(e), details=type(e).__name__, managed=False)`.\n"
#         "- The helper writes a normalized failure envelope to `result.json` (including `contract`, `objective`, and optional `out_dyn`).\n"
#         "\n"
#         "## Async\n"
#         "- If any adapter is async, implement `async def main()` and run with `asyncio.run(main())`.\n"
#         "\n"
#         "## Style & behavior\n"
#         "- Linear and concise. No prints.\n"
#         "- USER-FACING STATUS: two short lines (objective; plan). Do **not** name tools/providers/models.\n"
#         "\n"
#     )
#     sys += (
#         f"• Keep main.py ≤ {line_budget} lines.\n"
#         f"Assume today={today} (UTC).\n"
#     )
#
#     # ---------- Strict 3-section protocol ----------
#     sys = _add_3section_protocol(
#         sys,
#         "{"
#         "  \"entrypoint\": \"python main.py\","
#         "  \"files\": [ {\"path\": \"main.py\", \"content\": \"...\"} ],"
#         "  \"outputs\": [ {\"filename\": \"result.json\", \"kind\": \"json\", \"key\": \"worker_output\"} ],"
#         "  \"notes\": \"<=40 words\","
#         "  \"result_interpretation_instruction\": \"<=120 words, tool-agnostic, concise\""
#         "}"
#     )
#
#     # ---------- Message: task + adapters with DOCS ----------
#     adapters_for_llm = _adapters_public_view(adapters)
#
#     contract_dyn = (decision or {}).get("output_contract_dyn") or {}
#
#     msg = (
#         "TASK (objective + constraints for this program):\n"
#         f"{json.dumps(task or {}, ensure_ascii=False, indent=2)}\n\n"
#         "SOLVABILITY / DECISION (read-only hints):\n"
#         f"{json.dumps(decision or {}, ensure_ascii=False, indent=2)}\n\n"
#         "DYNAMIC OUTPUT CONTRACT YOU MUST FULFILL - output_contract_dyn (slot → description):\n"
#         f"{json.dumps(contract_dyn, ensure_ascii=False, indent=2)}\n\n"
#         "ADAPTERS — imports, call templates, is_async:\n"
#         f"{json.dumps([{k: v for k,v in a.items() if k in ('id','import','call_template','is_async')} for a in adapters_for_llm], ensure_ascii=False, indent=2)}\n\n"
#         "TOOL DOCS (purpose/args/returns/constraints/examples):\n"
#         f"{json.dumps([{ 'id': a['id'], 'doc': a.get('doc', {}) } for a in adapters_for_llm], ensure_ascii=False, indent=2)}\n\n"
#         "Produce the three sections as instructed."
#     )
#
#     # ---------- Stream ----------
#     return await _stream_agent_sections_to_json(
#         svc,
#         client_name="solver_codegen",
#         client_role="solver_codegen",
#         sys_prompt=sys,
#         user_msg=msg,
#         schema_model=SolverCodegenOut,
#         on_thinking_delta=on_thinking_delta,
#         ctx=ctx,
#         max_tokens=6000,
#     )

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
    "INTERNAL THINKING (STATUS): tiny.\n"
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
    tools_to_use: List[str] = Field(default_factory=list)
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
        "  \"output_contract_dyn\": {"
        "      \"<slot>\": {\"type\":\"inline\",\"description\":\"...\",\"format\":\"markdown\"},"
        "      \"<slot2>\": {\"type\":\"file\",\"description\":\"...\",\"mime\":\"application/pdf\",\"filename_hint\":\"report.pdf\"}"
        "  }"
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

