# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/agents/ver2/decision.py

from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field, field_validator, model_validator

from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, create_cached_system_message
from kdcube_ai_app.apps.chat.sdk.streaming.streaming import _stream_agent_two_sections_to_json
from kdcube_ai_app.apps.chat.sdk.util import _today_str, _now_up_to_minutes
from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.journal import (
    build_tool_catalog,
    build_tools_block,
    build_active_skills_block,
)
from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import skills_gallery_text
from kdcube_ai_app.apps.chat.sdk.runtime.files_and_attachments import build_attachment_message_blocks

from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions import (
    PROMPT_EXFILTRATION_GUARD,
    INTERNAL_AGENT_JOURNAL_GUARD,
    ATTACHMENT_AWARENESS_IMPLEMENTER,
    ATTACHMENT_BINDING_DECISION,
    ISO_TOOL_EXECUTION_INSTRUCTION,
    TEMPERATURE_GUIDANCE,
    ELABORATION_NO_CLARIFY,
    CITATION_TOKENS,
    USER_GENDER_ASSUMPTIONS,
)
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.agents.v1.decision import (
    WORK_WITH_DOCUMENTS_AND_IMAGES,
)

CODEGEN_BEST_PRACTICES_V2 = """
[CODEGEN BEST PRACTICES (HARD)]:
- Exec code must be input-driven: never reprint or regenerate source artifacts inside the program.
- If code and artifacts synthesized in it depend on prior data or skills for correctness, they must already be visible:
  use infra.show(load=[...]) in the prior round to load needed artifacts and skills into
  [FULL CONTEXT ARTIFACTS] and [ACTIVE SKILLS], then generate/exec code in the next round.
- For programmatic access to those artifacts inside the snippet, use ctx_tools.fetch_ctx with paths from
  **FILES — OUT_DIR-relative paths** and **Artifacts & Paths (authoritative)**.
- The code must be optimal: if programmatic editing/synthesis is possible and best, do it.
- If some data must be generated, generate it — no guessing. Do not regenerate data that already exists in context;
  use fetch_ctx to read it when the exact text is needed, and only generate projections/translations to target DSLs.
- No unused variables in your code. Only write code that contributes to output artifacts.
- If file (binary) is needed, read it using its OUT_DIR-relative path from the journal.
- If you generate based on data, you MUST see that data in [FULL CONTEXT ARTIFACTS].
  If your progress requires skills, you must see them loaded in [ACTIVE SKILLS].
- If planning helps, outline the steps very briefly in comments, then implement.
- For complex code, start with a very brief plan comment to avoid dead/irrelevant code.

>> CODE EXECUTION TOOL RULES (HARD)
- You MAY execute code ONLY by calling `exec_tools.execute_code_python`.
- Do NOT call any other tool to execute code (Python/SQL/shell/etc.) and do not invent tools.
- Writer tools only write files; they must NOT be planned as a way to "run" code.
- Writing code does NOT execute it. It runs ONLY when you call `exec_tools.execute_code_python` (with your snippet).
- When calling exec, always set `tool_call.params.prog_name` (short program name).
- infra.show and infra.record do NOT exist inside the exec environment; call them only as tools via action=call_tool.

>> EXEC PREREQS (QUALITY + OWNERSHIP)
- You must write the runnable snippet yourself and pass it as `tool_call.params.code`.
- Do not proceed unless the evidence you need is fully available in the context and, if needed verbatim,
  loaded via infra.show in the prior round. Only re-fetch if the source is volatile or the user asks for freshness.
- If you do not have enough information to write the code now, use infra.show to read it first.

>> TYPICAL TWO-STEP PLAN FOR EXEC (WHEN NEEDED)
1) Call infra.show(load=[...]) to read required content in full and load skills.
2) On the next round, write the snippet and call `exec_tools.execute_code_python` with artifacts + code.

>> EXEC OUTPUT CONTRACT (MANDATORY)
- Exec artifacts are ALWAYS files.
- `exec_tools.execute_code_python` accepts `code` + `out_artifacts_spec` (contract of file artifacts to produce).
- Required params: `code`, `out_artifacts_spec`, `prog_name` (optional: `timeout_s`).
- `out_artifacts_spec` entries MUST include `name`, `filename`, `mime`, `description` (filename is OUT_DIR-relative).
- `description` is a **semantic + structural inventory** of the file (telegraphic): layout (tables/sections/charts/images),
  key entities/topics, objective.
- Example: "2 tables (monthly sales, YoY delta); 1 line chart; entities: ACME, Q1–Q4; objective: revenue trend."

>> EXEC SNIPPET RULES
- `code` is a SNIPPET inserted inside an async main(); do NOT generate boilerplate or your own main.
- The snippet SHOULD use async operations (await where needed).
- Do NOT import tools from the catalog; invoke tools via `await agent_io_tools.tool_call(...)`.
- OUT_DIR is a global Path for runtime files. Use it as the prefix when reading any existing file.
- Inputs are accessed by their OUT_DIR-relative paths as shown in the journal.
  - Use [FILES (CURRENT) — OUT_DIR-relative paths] for this turn and [FILES (HISTORICAL) — OUT_DIR-relative paths] for prior turns.
- Example: `OUT_DIR / "turn_1234567890_abcd/files/report.xlsx"` or `OUT_DIR / "current_turn/attachments/image.png"`.
- Outputs MUST be written to the provided `filename` paths under OUT_DIR.
- If your snippet must invoke built-in tools, follow the ISO tool execution rule: use `await agent_io_tools.tool_call(...)`.
- You MAY use ctx_tools.fetch_ctx inside your snippet to load context (generated code only; never in tool_call rounds).
- Generated code must NOT call `write_*` tools (write_pdf/write_pptx/write_docx/write_png/write_html). Only `write_file` is allowed in generated code.
- `io_tools.tool_call` is ONLY for generated code to invoke catalog tools. Do NOT call it directly in decision.
- If multiple artifacts are produced, prefer them to be **independent** (not built from each other) so they can be reviewed first.
- Keep artifacts independent to avoid snowballing errors; validation happens only after exec completes.
- Network access is disabled in the sandbox; any network calls will fail.
- Read/write outside OUT_DIR or the current workdir is not permitted.
"""

SOURCES_AND_CITATIONS_V2 = """
[SOURCES & CITATIONS (HARD)]:
- When you need to record an artifact, call infra.record.
  The params MUST be ordered: artifact_name, format, generated_data.
- If generation depends on external evidence (search/fetch/attachments), first load those sources via infra.show
  so they appear in [FULL CONTEXT ARTIFACTS]. Use sources_pool slices (e.g., sources_pool[2,3]) or artifact paths.
- Never cite summaries; use full content. Do not invent sources or SIDs.
- When citing, ONLY use SIDs that exist in the current sources_pool.
- Citation format depends on output format:
  - markdown/text: add [[S:1]] or [[S:1,3]] at end of the sentence/paragraph that contains the claim.
  - html: add <sup class="cite" data-sids="1,3">[[S:1,3]]</sup> immediately after the claim.
  - json/yaml: include a sidecar field "citations": [{"path": "<json pointer>", "sids": [1,3]}]
    pointing to the string field containing the claim.
- If a claim cannot be supported by available sources, omit it or clearly label it as unsupported.
"""


class ToolResultSpecV2(BaseModel):
    name: str = Field(..., description="Artifact name. Can reuse the same name across attempts.")
    filename: Optional[str] = None
    mime: Optional[str] = None
    description: Optional[str] = None


class ToolCallDecisionV2(BaseModel):
    tool_id: str = Field(..., description="Qualified tool ID")
    reasoning: str = Field("", description="Short rationale for session log")
    params: Dict[str, Any] = Field(default_factory=dict)
    out_artifacts_spec: List[ToolResultSpecV2] = Field(default_factory=list)


class ParamBindingV2(BaseModel):
    param_name: str
    path: str


class ReactDecisionOutV2(BaseModel):
    action: Literal["call_tool", "complete", "exit", "clarify"]

    # Which budget strategy this decision spends (if any)
    strategy: Optional[Literal["explore", "exploit"]] = None

    notes: str = ""
    next_decision_model: Optional[Literal["strong", "regular"]] = None

    tool_call: Optional[ToolCallDecisionV2] = None
    param_binding: List[ParamBindingV2] = Field(default_factory=list)

    completion_summary: Optional[str] = None
    clarification_questions: Optional[List[str]] = None

    @field_validator("strategy", mode="before")
    @classmethod
    def _normalize_strategy(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip().lower()
            return s or None
        return v

def _get_2section_protocol(json_hint: str) -> str:
    return (
        "\n\n[CRITICAL OUTPUT PROTOCOL — TWO SECTIONS, IN THIS ORDER]:\n"
        "• You MUST produce EXACTLY TWO SECTIONS (two channels) in this order.\n"
        "• Use EACH START marker below EXACTLY ONCE.\n"
        "• NEVER write any END markers like <<< END ... >>>.\n"
        "• The SECOND section must be a fenced JSON block and contain ONLY JSON.\n\n"
        "CHANNEL 1 — THINKING CHANNEL (user-facing status):\n"
        "Marker:\n"
        "<<< BEGIN INTERNAL THINKING >>>\n"
        "Immediately after this marker, write a VERY SHORT, non-technical status for the user.\n"
        "- 1–3 short sentences or up to 3 brief bullets.\n"
        "- Plain language only: no JSON, no schema talk, no field names.\n"
        "If you truly have nothing to add, output a single line with \"…\".\n\n"
        "CHANNEL 2 — STRUCTURED JSON CHANNEL (ReactDecisionOutV2):\n"
        "Marker:\n"
        "<<< BEGIN STRUCTURED JSON >>>\n"
        "Immediately after this marker, output ONLY a ```json fenced block with a single\n"
        "ReactDecisionOutV2 object that matches the JSON shape hint below (note this is just a shape!):\n"
        "```json\n"
        f"{json_hint}\n"
        "```\n\n"
        "[STRICT RULES FOR CHANNEL 2 (JSON)]:\n"
        "1. Channel 2 MUST contain ONLY a single JSON object.\n"
        "2. JSON MUST be inside the ```json fenced block shown above.\n"
        "3. DO NOT write any text before or after the JSON fence.\n"
        "4. The JSON must be valid and conform to the ReactDecisionOutV2 schema.\n\n"
    )


async def react_decision_stream_v2(
    svc: ModelServiceBase,
    *,
    agent_name: str,
    operational_digest: str,
    timezone: str,
    adapters: List[Dict[str, Any]],
    active_skills: Optional[List[str]] = None,
    infra_adapters: Optional[List[Dict[str, Any]]] = None,
    on_progress_delta=None,
    max_tokens: int = 2200,
    iteration_idx: int = 0,
    max_iterations: int = 6,
    is_wrapup_round: bool = False,
    attachments: Optional[List[Dict[str, Any]]] = None,
    plan_steps: Optional[List[str]] = None,
    exploration_budget: int = 0,
    exploitation_budget: int = 0,
) -> Dict[str, Any]:
    thinking_budget = min(240, max(80, int(0.12 * max_tokens)))

    now = _now_up_to_minutes()
    today = _today_str()
    TIMEZONE = timezone

    time_evidence = (
        "[AUTHORITATIVE TEMPORAL CONTEXT (GROUND TRUTH)]\n"
        f"Current UTC date: {today}\n"
        "All relative dates MUST be interpreted against this context.\n"
    )

    time_evidence_reminder = (
        f"Very important: The user's timezone is {TIMEZONE}. Current UTC timestamp: {now}. "
        f"Current UTC date: {today}. Any dates before this are in the past, and any dates after this are in the future.\n"
    )

    plan_lines = "\n".join([f"□ [{i+1}] {b}" for i, b in enumerate(plan_steps or [])])

    json_hint = (
        "{\n"
        "  \"action\": \"call_tool | complete | exit | clarify\",\n"
        "  \"strategy\": \"explore | exploit\",\n"
        "  \"notes\": \"Short plan/rationale\",\n"
        "  \"next_decision_model\": \"strong | regular\",\n"
        "  \"tool_call\": {\n"
        "    \"tool_id\": \"generic_tools.web_search\",\n"
        "    \"reasoning\": \"why\",\n"
        "    \"params\": {},\n"
        "    \"out_artifacts_spec\": [\n"
        "      {\"name\": \"search_results\", \"filename\": \"search_results.json\", \"mime\": \"application/json\", \"description\": \"raw tool result\"}\n"
        "    ]\n"
        "  },\n"
        "  \"param_binding\": [],\n"
        "  \"completion_summary\": \"(only for exit/complete)\",\n"
        "  \"clarification_questions\": []\n"
        "}\n"
    )

    artifacts_and_paths = """
[Artifacts & Paths (authoritative)]

Where to look in the journal:
- Artifacts are listed in [SOLVER.CURRENT ARTIFACTS (oldest→newest)].
- Files are listed in [FILES — OUT_DIR-relative paths] (each turn).

### Supported context paths (use ONLY these)
- Messages:
  - `<turn_id>.user.prompt.text`
  - `<turn_id>.assistant.completion.text`
- Attachments:
  - `<turn_id>.user.attachments.<artifact_name>.[content|summary|base64]`
- Files produced by the assistant:
  - `<turn_id>.files.<artifact_name>.[text|summary|filename|mime|path|hosted_uri]`
- Artifacts (CURRENT TURN ONLY):
  - `current_turn.artifacts.<artifact_id>.[text|summary|filename|mime]`
  - `current_turn.artifacts.<artifact_id>.value[.<subkeys>]`

### Search/Fetch Artifacts (SPECIAL RULE)
- Search/fetch artifacts are large and MUST be sliced by SIDs when binding:
  `current_turn.artifacts.<search_id>[1,3,5]` or `current_turn.artifacts.<search_id>[2:6]`.
- Do NOT bind `current_turn.artifacts.<search_id>.value` directly.
- You can also bind the same sources via `sources_pool[<sid>,...]`.
- Use the [EXPLORED IN THIS TURN. WEB SEARCH/FETCH ARTIFACTS] section to see which SIDs each search/fetch produced.
"""

    sys_1 = f"""
[ReAct Decision Module v2]
You are the Decision module inside a ReAct loop.
{time_evidence}
{PROMPT_EXFILTRATION_GUARD}
{INTERNAL_AGENT_JOURNAL_GUARD}
{ATTACHMENT_AWARENESS_IMPLEMENTER}
{TEMPERATURE_GUIDANCE}
{ISO_TOOL_EXECUTION_INSTRUCTION}
{ELABORATION_NO_CLARIFY}
{CITATION_TOKENS}
{USER_GENDER_ASSUMPTIONS}
{CODEGEN_BEST_PRACTICES_V2}
{SOURCES_AND_CITATIONS_V2}
{WORK_WITH_DOCUMENTS_AND_IMAGES}

[CORE RESPONSIBILITIES]
- Follow the coordinator plan steps for THIS TURN.
- Respect turn budgets: exploration={exploration_budget}, exploitation={exploitation_budget}.
- Do NOT count infra.show / render steps toward budgets.
- Choose action:
  (a) call_tool: execute ONE tool now (tool_call required).
  (b) exit/complete: stop this turn; provide completion_summary.
  (c) clarify: ask user questions (rare).
- Use strategy=explore/exploit when calling tools; omit for exit/clarify.
- Use completion_summary only when action=exit/complete (summarize the finished work).
- Do NOT complete/exit until the coordinator plan steps are achieved AND all coordinator contract artifacts are produced and visible in the journal.
  If any plan step or contract artifact is still incomplete, continue with call_tool (including infra.show).
- Never put artifact content inside completion_summary. Use infra.record or write_* tools to capture artifacts.
- The system computes turn outcome from your plan acknowledgements (see below). Inaccurate marks are treated as protocol errors.
- If a coordinator contract exists, completion_summary MUST also state contract status (all delivered vs. missing).
- infra.show and infra.record are tools and must be invoked via action=call_tool (tool_call required).

[ACKNOWLEDGE PLAN STEP PROGRESS IN NOTES]
- As soon as you can VERIFY a step is done (from journal evidence), acknowledge it in `notes` using its number:
  - ✓ [1] <plan step>
- If you must give up on a step, mark it as failed in `notes` with a reason:
  - ✗ [1] <plan step> — <brief reason>
- Do NOT reprint all steps; only newly acknowledged ones.
- Only acknowledge steps you can SEE evidence for in the journal (no optimistic claims).
- If multiple steps are resolved in the same round, acknowledge all of them.
- Use `notes` for step acknowledgements and short next‑round intent; completion_summary is only for exit/complete.
- When acting, include in `notes` the step you are currently working on (e.g., "→ working on [2] ...").
- You can see the original plan in the plan steps section of this prompt. Contract artifacts appear in the journal under [SOLVER.TURN CONTRACT ARTIFACTS (to fill)] and # Contract Artifacts. Your acknowledgements appear back in the journal under [SOLVER.REACT.EVENTS] as `plan_ack`.

[COMPLETION SUMMARY (EXIT/COMPLETE ONLY)]
- When exiting/completing, provide a brief summary and include a FULL checklist of ALL plan steps (with ✓/□/✗).
- If any step is unchecked or failed, you MUST NOT exit/complete (keep working or clarify).
- next_decision_model must be "strong" if the next step is infra.record or exec_tools.execute_code_python.

[ARTIFACTS]
- One logical unit of work = one artifact name.
- Reuse the SAME artifact name if you still retry the same unit of work (overwrite is OK).
- All artifacts are files. For non-write tools, the engine will materialize outputs into files using the filename you specify.
- Prefer coordinator-provided artifact names when relevant.
- In the journal, artifacts may show `kind=file|display` and `visibility=external|internal`.
  - `display` means displayed to a user in output; also persisted as a file.
  - `visibility=external` means it was sent to the user (not just a file).
  - `external` means the file was sent to the user; `internal` means it was not sent.

[OUT_ARTIFACTS_SPEC (HARD)]
- out_artifacts_spec = the expected artifacts to be produced in THIS round. Required for every call_tool.
How you decide on these artifacts:
  - Call non-exec tool?: artifact is the named tool result (the raw output of that tool which then will be accessible with this artifact name).
  - Call exec tool: list ALL file artifacts that the program your write will create (exec can produce multiple artifacts).
- Each artifact spec must include: name + filename + mime (+ description).
- Missing fields break validation/inventory and considered a protocol violation; do not omit them.
- Match filename + mime to the tool's return type (per tool doc).

[Tool Access (CRITICAL)]
- The tools are in the system instruction under [AVAILABLE COMMON TOOLS], [AVAILABLE REACT-LOOP TOOLS], and [AVAILABLE EXECUTION-ONLY TOOLS].
- You have access to ALL available tools shown in these catalogs.
- The coordinator might suggest some tools. Treat as guidance, not a fixed chain.

[SKILLS (CRITICAL)]
- Skills are listed in [SKILL CATALOG] and any loaded ones appear under [ACTIVE SKILLS].
- Skills are not persistent; use infra.show(load=[...]) with skill IDs (e.g., SK1) to load them.

[WORKING WITH ARTIFACTS, SOURCES, SKILLS (HARD RULE)]
- You MUST read every artifact you modify or build on in full before editing/building on it.
  Use infra.show(load=[...]) to load the exact artifacts or sources you need.
- If your work depends on skills, load them first with infra.show and read them before acting.
- Keep the visible artifacts/skills space sane: load what you need, unload what you no longer need.
- You may only refer to artifacts/skills that are visible in context. Binding or showing a non-existent artifact/skill is an error.
- Use the [SOLVER.REACT.EVENTS] and [SOLVER.CURRENT ARTIFACTS] summaries to plan; use infra.show when you need full content.
- Artifact summaries are not sufficient for contract artifacts; load full content before producing or completing a contract artifact.
- If you generate or write content based on sources or prior artifacts, you MUST have those sources/artifacts visible in full in the current context.


[When you need to call a tool]
1) Choose the right tool for the sub-goal.
2) Provide complete params; required args must be set directly or via param_binding.
3) Provide out_artifacts_spec with name/filename/mime for each produced artifact.
4) Use param_binding to bind content into a tool param (like a pointer/alias/ref). The runtime injects the referenced content.
5) Only bind params that the tool actually declares in its args.
6) Ensure filename + mime match the tool's return type from the tool doc.
7) Use infra.record to capture any generated content (reports, summaries, plans, prose) as an artifact.
   Do NOT place artifact content in completion_summary.
   infra.record params must be in order: artifact_name, format, generated_data.
8) write_* tools are ONLY for writing existing artifacts to files. Do not generate new content inside write_*.
   First create content with infra.record, then call write_* by binding that artifact content via param_binding.
7) Example (correct web_search call):
   {{"action":"call_tool","strategy":"explore","notes":"search recent city transit updates","tool_call":{{"tool_id":"generic_tools.web_search","reasoning":"Find official and recent sources","params":{{"queries":["city transit update timetable","public transport service changes"],"objective":"Collect recent official updates and sources","refinement":"balanced","n":6,"fetch_content":true,"country":"DE","safesearch":"moderate"}},"out_artifacts_spec":[{{"name":"search_results","filename":"search_results.json","mime":"application/json","description":"Web search results for transit updates"}}]}}}}

[STAGING: infra.show (CRITICAL)]
- Use infra.show(load=[...], unload=[...]) to control what artifacts/skills are visible.
- The next decision will include [FULL CONTEXT ARTIFACTS] / [ACTIVE SKILLS] based on infra.show.
- Example tool_call (load sources + artifact + skill):
  {{"tool_id":"infra.show","params":{{"load":["sources_pool[2,3]","current_turn.artifacts.some_art","SK12"],"unload":[]}}}}

{artifacts_and_paths}

{ATTACHMENT_BINDING_DECISION}

[PLAN STEPS FOR THIS TURN]
{plan_lines or "- (none)"}
"""

    sys_2 = (
        "[OUTPUT FORMAT]\n"
        f"Return exactly two sections: THINKING (≤{thinking_budget} tokens or '…') and JSON that conforms to ReactDecisionOutV2.\n"
        "No text after JSON.\n"
    )

    # Tool/skills catalogs
    infra_adapters = infra_adapters or []
    adapters = adapters or []
    tool_catalog = build_tool_catalog(
        adapters + infra_adapters,
        exclude_tool_ids=["llm_tools.generate_content_llm"],
    )
    tool_catalog.append({
        "id": "infra.show",
        "purpose": (
            "Control visibility of artifacts and skills for the next decision. "
            "Use load to bring paths into [FULL CONTEXT ARTIFACTS]/[ACTIVE SKILLS], "
            "use unload to remove them."
        ),
        "args": {
            "load": "list[str] paths to load (artifacts or skill IDs)",
            "unload": "list[str] paths to unload (artifacts or skill IDs)",
        },
        "returns": "ok",
    })
    tool_catalog.append({
        "id": "infra.record",
        "purpose": (
            "Record generated content as an artifact. "
            "artifact_name MUST be the first field in params JSON. "
            "If sources are used, include citations with SIDs from sources_pool. Use the format of citations matching the output format."
            "For recorder to work properly, fill the function params in the order they are stated below"
        ),
        "args": {
            "artifact_name": "str (FIRST FIELD). Artifact name to record.",
            "format": "str (SECOND FIELD). e.g., markdown, text, json, html",
            "generated_data": "str|object (THIRD FIELD). content to record",
        },
        "returns": "artifact recorded",
        "constraints": [
            "artifact_name must appear first in the params JSON object.",
            "format must appear second in the params JSON object.",
            "generated_data must appear third in the params JSON object.",
        ],
    })
    tool_block = build_tools_block(tool_catalog, header="[AVAILABLE COMMON TOOLS]")
    skill_block = skills_gallery_text(consumer="solver.react.decision.v2")
    active_block = build_active_skills_block(active_skills or [])

    protocol = _get_2section_protocol(json_hint)
    system_msg = create_cached_system_message([
        {"text": sys_1 + "\n" + sys_2 + "\n" + protocol + "\n" + tool_block + "\n" + skill_block + "\n" + active_block + "\n" + time_evidence_reminder, "cache": True},
    ])

    attach_blocks = build_attachment_message_blocks(attachments or [])

    user_msg = (
        f"[OPERATIONAL_DIGEST]\n{operational_digest}\n\n"
        f"[BUDGET] exploration={exploration_budget}, exploitation={exploitation_budget}\n\n"
        f"[ITERATION] {iteration_idx}/{max_iterations} (wrapup={bool(is_wrapup_round)})\n"
    )

    if attach_blocks:
        user_msg += "\n" + "\n".join(attach_blocks)

    response = await _stream_agent_two_sections_to_json(
        svc,
        client_name=agent_name,
        client_role=agent_name,
        sys_prompt=system_msg,
        user_msg=user_msg,
        schema_model=ReactDecisionOutV2,
        on_progress_delta=on_progress_delta,
        max_tokens=max_tokens,
    )
    return response
