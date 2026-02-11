# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/agents/ver2/decision.py

from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field, field_validator
from langchain_core.messages import HumanMessage

from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, create_cached_system_message
from kdcube_ai_app.apps.chat.sdk.streaming.streaming import _stream_agent_two_sections_to_json
from kdcube_ai_app.apps.chat.sdk.util import _today_str, _now_up_to_minutes
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.layout import (
    build_tool_catalog,
    build_instruction_catalog_block,
)
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.call import get_react_tools_catalog
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
    PATHS_EXTENDED_GUIDE,
    INTERNAL_NOTES_PRODUCER,
    INTERNAL_NOTES_CONSUMER,
)

WORK_WITH_DOCUMENTS_AND_IMAGES = """
[WORK WITH DOCUMENTS & IMAGES (PLANNING EXAMPLE)]:
- If multiple derived artifacts exceed remaining budgets, consolidate work into fewer rounds.
- Example: round 1 generates 4 diagrams in one exec round (write 4 .mmd + render 4 PNGs) so they can be reviewed.
- Round 2 synthesizes the final HTML and renders PDF; both are files via exec.
  Ensure the pdf-press skill is loaded for the HTML+PDF round.
- This keeps artifacts reviewable (per-file) while staying within exploit/render budgets.
"""

CODEGEN_BEST_PRACTICES_V2 = """
[CODEGEN BEST PRACTICES (HARD)]:
- Exec code must be input-driven: never reprint or regenerate source artifacts inside the program.
- If code and artifacts synthesized in it depend on prior data or skills for correctness, they must already be visible:
  use react.read(artifacts paths) in the prior round to load needed artifacts and skills into visible context.
  Active skills are marked with ACTIVE 💡 banner.
- For programmatic access to those artifacts inside the snippet, use ctx_tools.fetch_ctx with the SAME paths
  you would pass to react.read (fi:<turn_id>.files/<path>, ar:<turn_id>..., tc:<turn_id>..., so:sources_pool[...]).
  fetch_ctx returns a canonical artifact dict: {path, kind, mime, sources_used, filepath?, text|base64}.
- The code must be optimal: if programmatic editing/synthesis is possible and best, do it.
- If some data must be generated, generate it — no guessing. Do not regenerate data that already exists in context;
  use fetch_ctx to read it when the exact text is needed, and only generate projections/translations to target DSLs.
- No unused variables in your code. Only write code that contributes to output artifacts.
- If file (binary) is needed, read it using its OUT_DIR-relative path from the visible context.
- If you generate based on data, you MUST see that data in your visible context in full, 
  otherwise you must react.read it if you see its path in context.
  If your progress requires skills, you must see them loaded and visible as ACTIVE 💡.
- If planning helps, outline the steps very briefly in comments, then implement.
- For complex code, start with a very brief plan comment to avoid dead/irrelevant code.

>> CODE EXECUTION TOOL RULES (HARD)
- You MAY execute code ONLY by calling `exec_tools.execute_code_python`.
- Do NOT call any other tool to execute code (Python/SQL/shell/etc.) and do not invent tools.
- Writer tools only write files; they must NOT be planned as a way to "run" code.
- Writing code does NOT execute it. It runs ONLY when you call `exec_tools.execute_code_python` (with your snippet).
- When calling exec, always set `tool_call.params.prog_name` (short program name).
- react.read and react.write do NOT exist inside the exec environment; call them only as tools via action=call_tool.

>> EXEC PREREQS (QUALITY + OWNERSHIP)
- You must write the runnable snippet yourself and pass it as `tool_call.params.code`.
- Do not proceed unless the evidence you need is fully available in the context and, if needed verbatim,
  loaded via react.read in the prior round. Only re-fetch if the source is volatile or the user asks for freshness.
- If you do not have enough information to write the code now, use react.read to read it first.

>> TYPICAL TWO-STEP PLAN FOR EXEC (WHEN NEEDED)
1) Call react.read([...]) to read required content in full and load skills.
2) On the next round, write the snippet and call `exec_tools.execute_code_python` with artifacts + code.

>> EXEC OUTPUT CONTRACT (MANDATORY)
- Exec artifacts are ALWAYS files.
- `exec_tools.execute_code_python` accepts `code` + `contract` (file artifacts to produce).
- Required params: `code`, `contract`, `prog_name` (optional: `timeout_s`).
- `contract` entries MUST include `name`, `filename`, `description`.
- `filename` MUST be **relative to OUT_DIR** and MUST be nested under the current turn folder:
  `"<turn_id>/files/<path>"` (you choose `<path>`).
- `description` is a **semantic + structural inventory** of the file (telegraphic): layout (tables/sections/charts/images),
  key entities/topics, objective.
- Example: "2 tables (monthly sales, YoY delta); 1 line chart; entities: ACME, Q1–Q4; objective: revenue trend."

>> EXEC SNIPPET RULES
- `code` is a SNIPPET inserted inside an async main(); do NOT generate boilerplate or your own main.
- The snippet SHOULD use async operations (await where needed).
- Do NOT import tools from the catalog; invoke tools via `await agent_io_tools.tool_call(...)`.
- OUT_DIR is a global Path for runtime files. Use it as the prefix when reading any existing file.
- Inputs are accessed by their OUT_DIR-relative paths as shown in the visible context.
  - Look for artifact_path and its physical_path in the context.
- Non‑PDF/image attachments (e.g., .docx, .xlsx, .txt) are **meta‑only** in the timeline. Read them via
  their physical path under OUT_DIR, e.g. `OUT_DIR / "<turn_id>/attachments/<filename>"`.
- Example: `OUT_DIR / "<turn_id>/files/report.xlsx"`.
- Outputs MUST be written to the provided `filename` paths under OUT_DIR.
- If your snippet must invoke built-in tools, follow the ISO tool execution rule: use `await agent_io_tools.tool_call(...)`.
- You MAY use ctx_tools.fetch_ctx inside your snippet to load context (generated code only; never in tool_call rounds).
- `io_tools.tool_call` is ONLY for generated code to invoke catalog tools. Do NOT call it directly in decision.
- fetch_ctx only supports ar:, tc:, so: paths. It does NOT support fi:. For files/attachments use
  physical OUT_DIR paths.
- If multiple artifacts are produced in the same code, prefer them to be **independent** (not built from each other) so they can be reviewed first.
- Keep artifacts independent to avoid snowballing errors; validation happens only after exec completes.
- Network access is disabled in the sandbox; any network calls will fail.
- Read/write outside OUT_DIR or the current workdir is not permitted.
"""

SOURCES_AND_CITATIONS_V2 = """
[SOURCES & CITATIONS (HARD)]:
- When you need to record an artifact, call react.write.
  The params MUST be ordered: path, channel, content, kind.
- If generation depends on external evidence (search/fetch/attachments), first load those sources via react.read
  so they appear in your visible context. Use sources_pool slices (e.g., so:sources_pool[2,3]) or artifact paths.
- If the content must be generated by following strict rules (e.g. to be rendered by rendering_tools.write_*),
  ensure you first read any required guidance that is already visible in the timeline.
- Never cite summaries; use full content. Do not invent sources or SIDs.
- When citing, ONLY use SIDs that exist in the current sources_pool.
- Citation format depends on output format:
  - markdown/text: add [[S:1]] or [[S:1,3]] at end of the sentence/paragraph that contains the claim.
  - html: add <sup class="cite" data-sids="1,3">[[S:1,3]]</sup> immediately after the claim.
  - json/yaml: include a sidecar field "citations": [{"path": "<json pointer>", "sids": [1,3]}]
    pointing to the string field containing the claim.
- If a claim cannot be supported by available sources, omit it or clearly label it as unsupported.
"""

class ToolCallDecisionV2(BaseModel):
    tool_id: str = Field(..., description="Qualified tool ID")
    notes: str = Field("", description="Short rationale for this tool call")
    params: Dict[str, Any] = Field(default_factory=dict)


class ReactDecisionOutV2(BaseModel):
    action: Literal["call_tool", "complete", "exit", "clarify"]

    # Which budget strategy this decision spends (if any)
    strategy: Optional[Literal["explore", "exploit"]] = None

    notes: str = ""
    tool_call: Optional[ToolCallDecisionV2] = None

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
    timezone: str,
    adapters: List[Dict[str, Any]],
    active_skills: Optional[List[str]] = None,
    infra_adapters: Optional[List[Dict[str, Any]]] = None,
    on_progress_delta=None,
    max_tokens: int = 2200,
    attachments: Optional[List[Dict[str, Any]]] = None,
    plan_steps: Optional[List[str]] = None,
    exploration_budget: int = 0,
    exploitation_budget: int = 0,
    user_blocks: Optional[List[Dict[str, Any]]] = None,
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
        "  \"tool_call\": {\n"
        "    \"tool_id\": \"web_tools.web_search\",\n"
        "    \"notes\": \"why\",\n"
        "    \"params\": {<tool params according to tool documentation. to bind param, in param value put 'ref:<bound artifact path>'>},\n"
        "  },\n"
        "  \"completion_summary\": \"(only for exit/complete)\",\n"
        "  \"clarification_questions\": []\n"
        "}\n"
    )

    artifacts_and_paths = """
[Artifacts & Paths (authoritative)]

Where to look in the visible context:
- The timeline is ordered **oldest → newest** (newest at bottom). Each turn begins with `[TURN <turn_id>]`.
- Within a turn, user prompt/attachments appear first, followed by agent contributions and tool call/result blocks.

### Context artifacts discovery and access (CRITICAL)
You use these paths to: 
1) bind content into tool params with "ref:<artifact path>"; 
2) to load content with react.read in react loop tool;
3) to read content in your code (exec snippets) with ctx_tools.fetch_ctx.

CRITICAL: You never use the filesystem paths shown in context directly.
CRITICAL: Filesystem paths only can be used in exec snippets.

#### Path usage (Decision-only)
- react.read / fetch_ctx **require logical paths** (ar:/fi:/tc:/so:/su:).  
  Example: `react.read(path="fi:<turn_id>.files/reports/summary.md")`
- Tools that **write or patch files** expect **physical paths**:  
  - `react.patch(path="turn_<id>/files/draft.md", patch="...")`  
  - `rendering_tools.write_pdf(path="turn_<id>/files/report.pdf", content=...)`  
  - exec code uses the same physical paths.
- If you pass a logical path to a physical‑path tool (or vice versa), the runtime will rewrite it and log a protocol notice.

### Using Search/Fetch results (SPECIAL RULE)
- Search/fetch tool results are large. They are only available in full right after the tool call in its tool result block.
  The result of such tools is added in the sources_pool and is only available from there afterward (sources_pool[sid1, sid2, ..]).
  Each search / fetch tool result is summarized in its tool result block.
- You never access the sources via that artifact, only via sources_pool. 
- You never keep all sources at a time visible in the [FULL CONTEXT ARTIFACTS]. This might eat all context.
  You request the sources with slice operator and use the either range of SIDs or the enumeration of SIDs when binding/reading them:
  `sources_pool[1,3,5]` or `sources_pool[2:6]`.
- You never bind/reference tool outputs via legacy `current_turn.*` paths.
- You can also bind the same sources via `sources_pool[<sid>,...]`.
- Use the [EXPLORED IN THIS TURN. WEB SEARCH/FETCH ARTIFACTS] section to see which SIDs each search/fetch produced.
"""

    sys_1 = f"""
[ReAct Decision Module v2]
You are the Decision module inside a ReAct loop.
{time_evidence}
{PROMPT_EXFILTRATION_GUARD}
{INTERNAL_AGENT_JOURNAL_GUARD}
{INTERNAL_NOTES_PRODUCER}
{INTERNAL_NOTES_CONSUMER}
{ATTACHMENT_AWARENESS_IMPLEMENTER}
{TEMPERATURE_GUIDANCE}
{ISO_TOOL_EXECUTION_INSTRUCTION}
{ELABORATION_NO_CLARIFY}
{CITATION_TOKENS}
{PATHS_EXTENDED_GUIDE}
{USER_GENDER_ASSUMPTIONS}
{CODEGEN_BEST_PRACTICES_V2}
{SOURCES_AND_CITATIONS_V2}
{WORK_WITH_DOCUMENTS_AND_IMAGES}

[CORE RESPONSIBILITIES]
- Follow the coordinator plan steps for THIS TURN.
- Respect turn budgets: exploration={exploration_budget}, exploitation={exploitation_budget}.
- Do NOT count react.read / render steps toward budgets.
- Choose action:
  (a) call_tool: execute ONE tool now (tool_call required).
  (b) exit/complete: stop this turn; provide completion_summary.
  (c) clarify: ask user questions (rare).
- Use strategy=explore/exploit when calling tools; omit for exit/clarify.
- Use completion_summary only when action=exit/complete (summarize the finished work).
- You may continue working even if plan acknowledgements are incomplete; missing acknowledgements do NOT block completion.
- Never put artifact content inside completion_summary. Use the content capturing tools to capture artifact content. See content capturing tools. 
- The system computes turn outcome from your plan acknowledgements (see below). Inaccurate marks are treated as protocol errors.
- react.read, react.write, and react.patch are tools and must be invoked via action=call_tool (tool_call required).

[PLAN ACKNOWLEDGEMENT]
- You are NOT required to acknowledge a step every round.
- Whenever a plan step becomes DONE or FAILED, you MUST include a line in `notes`.
- If a step is still in progress, do NOT mark it as done/failed; use a "working on" note instead.
- Use the working marker format: "… [1] <step> — in progress".
- Format: "✓ [1] <step>" or "✗ [1] <step> — <reason>" or "… [1] <step> — in progress".
- Example notes:
  ✓ [1] Locate sources
  … [2] Draft report — in progress

[ACKNOWLEDGE PLAN STEP PROGRESS IN NOTES]
- As soon as you can VERIFY a step is done (from visible context evidence), acknowledge it in `notes` using its number:
  - ✓ [1] <plan step>
- If you must give up on a step, mark it as failed in `notes` with a reason:
  - ✗ [1] <plan step> — <brief reason>
- Do NOT reprint all steps; only newly acknowledged ones.
- Only acknowledge steps you can SEE evidence for in the visible context (no optimistic claims).
- If multiple steps are resolved in the same round, acknowledge all of them.
- Use `notes` for step acknowledgements and short next‑round intent; completion_summary is only for exit/complete.
- When acting, include in `notes` the step you are currently working on (e.g., "… [2] Draft report — in progress").
- You can see the current plan in the react.plan block and in the ACTIVE STATE announce (plan checklist).
  Your acknowledgements appear back in the tool result/event blocks as `plan_ack`.

[COMPLETION SUMMARY (EXIT/COMPLETE ONLY)]
- Completion_summary is INTERNAL ONLY (not shown to the user). Keep it brief.
- If you need to show results to the user, you MUST call react.write (channel=timeline_text or canvas) before exiting.
- When exiting/completing, provide a brief summary and include a FULL checklist of ALL plan steps (with ✓/□/✗/…).

[REACT EVENTS, TOOL CALLS AND TOOL RESULTS, ARTIFACTS]
Each time you call a tool we save its input in tc:<turn_id>.tool_calls.<tool_call_id>in.json and its output in tc:<turn_id>.tool_calls.<tool_call_id>out.json.
You can see the tool call id for each tool call in its tool call block.
For each tool call, we show the tool id, tool call id, notes, params (including bindings), and tool result blocks.
Protocol violations and errors are also shown after the tool call so you can verify correctness.

Artifacts produced in your react loop are shown in the tool result blocks.
Sometimes artifact content is large; we only show summary/truncated content in the tool result block and mark it. 
If you do not see the full content of an artifact in the visible context, you MUST read it in full with react.read before building on it or editing it.
The artifact description includes the path you use with react.read and the tool id + tool call id they resulted from.

Results/artifacts resulting from the following tools will be automatically summarized IF exceed 1000 tokens (which web_search.* tools usually do)
- react.write        (content your produce will be summarized)
- react.patch        (patched file content may be summarized; patch text is streamed)
- web_search.fetch_url_contents (result of this tool will be summarized)
- web_search.web_search (result of this tool will be summarized)

Artifacts produced by following rendering tools will be in analyzed, assessed and summarized with multimodal summarizer:
- rendering_tools.write_pdf (the file artifact which results from this tool will be summarized)
- rendering_tools.write_png (the file artifact which results from this tool will be summarized)

Provide telegraphic notes when you call tools. These notes help later summarization and troubleshooting.

[CONTENT STREAMING AND CAPTURING TOOLS (HARD)]
You have following tools to capture content which you produce in the named and distributable artifacts:
- react.write: use to generate artifact. 
  If you want the user to see it as you produce it (which is great UX for any presentable long content).
  You can pick 3 channels: canvas, timeline_text, internal. 
  - Chat timeline will show the content in the main chat stream (allows markdown); 
  - Canvas is for more visual/tabular content and will be shown in a separate canvas block in the UI.
  - Internal means this artifact will only be stored as a file artifact and won't be shared to a user in any channel.
  You use internal channel in order to write the notes to remember things.
  You might want to write the internal notes when:
  - you need to remember the name of the user or their preferences. Mark such line with [P] (personal/preferences).
  - you want to document the decisions and their rationale for future reference. Mark such line with [D] (decisions, rationale)
  - you want to collect the technical details of the project you work on. Mark such lines with [S] (spec, structure) 
  Mostly these notes must be telegraphic. This is will go to long conversation memory.
  Do not pick timeline_text for large content. Default channel is canvas so user sees what you generate.
  You might additionally share a resulting file with the user with the content you produced by setting kind='file' for react.write. 

- react.patch: use to update an existing file in-place. The patch should be a unified diff; if it is plain text it replaces the file.
  The patch itself is streamed to the user in your chosen channel. If kind='file', the updated file is also shared.
  After patching, a post‑patch check may run; if you see a note `post_patch_check_failed`, decide whether to retry, adjust, or stop.

- react.memory_read: use to search prior turns for missing context. This surfaces compact snippets with turn_id and scores.
- react.memory_hide: use to hide a large snippet in the visible timeline (replace with a short placeholder). Use only when the snippet is near the tail and clearly no longer needed. The original content remains retrievable via react.read(path).
- react.search_files: safe file search under the current workdir (no shell). Use to locate files by name_regex/content_regex when needed.
  Use when you suspect the needed info exists but is not visible. This does NOT load full artifacts; follow up with react.read.

- Use rendering_tools.write_* to render and write the special formats (pdf, pptx, docx, png).
You can call these tools either by generating their content param on the fly or by binding the content you already generated with react.write.
You cannot use both at a time. Setting `content` param value to "ref:<artifact_path>" is considered binding.
If no ref: prefix is used, we consider you generating content on the fly.
Note, when you call these tools with inline content which you generate on the fly, we automatically stream it to a user in canvas channel.
It is preferable to use react.write for streaming large content and use rendering_tools.write_* for rendering the final artifact.

[CAPTURING PROGRESS WITH ARTIFACTS]
- One logical unit of work = one artifact path name.
- Artifact path delimiter is . (e.g., "report.md", "analysis.findings.txt", "plan.v1.md").
  Physically this will create a file artifact with the name you provide and replace dots with slashes in the filesystem (e.g., "report.md" → report.md, "analysis.findings.txt" → analysis/findings.txt).
- Physical paths are only used in exec snippets and rendering_tools.write_*.
You never use them with react.* tools.
- All artifacts are files. You always can look at their content if they are text or pdf/image if you don't see them in full by calling react.read([paths to see]).
 - Reuse the SAME artifact path name if you still retry the same unit of work (overwrite is OK).
- In the visible context, artifacts may show `kind=file|display` and `visibility=external|internal`.
  - `kind=display` means displayed to a user in rendering canvas; `kind=file` means it was [also] shared as a file to the user. For internal files this is 'file' automatically.
  - `visibility=external` means it was shared with the user. `visibility=internal` means it was never shared.
  - `channel` means the channel in which the artifact shared to a user (timeline_text|canvas|file). If no channel set, it was not shared.

[Tool Access (CRITICAL)]
- The tools are in the system instruction under [AVAILABLE COMMON TOOLS], [AVAILABLE REACT-LOOP TOOLS], and [AVAILABLE EXECUTION-ONLY TOOLS].
- You have access to ALL available tools shown in these catalogs.
- The coordinator might suggest some tools. Treat as guidance, not a fixed chain.

[SKILLS (CRITICAL)]
- Skills are listed in [SKILL CATALOG] and any loaded ones appear in your visible context with ACTIVE💡 banner.
- Skills are shown originally only briefly (catalog); use react.read([...]) with skill IDs (e.g., sk:SK1 or sk:1) to load them into visible context with ACTIVE banner.

[WORKING WITH ARTIFACTS, SOURCES, SKILLS (HARD RULE)]
- You MUST read every artifact you modify or build on in full before editing/building on it.
  Use react.read([...]) to load the exact artifacts or sources or skills you need into your visible context.
- If your work depends on skills, load them first with react.read and read them before acting.
- Keep the visible artifacts/skills space sane: load what you need, unload what you no longer need.
- You may only refer to artifacts/skills that are visible in context. Binding or showing a non-existent artifact/skill is an error.
- Use the tool call/result blocks and historical [TURN PROGRESS LOG] sections to plan; use react.read when you need full content.
- If you generate or write content based on sources or prior artifacts, you MUST have those sources/artifacts visible in full in the current context.


[When you need to call a tool]
1) Choose the right tool for the sub-goal.
2) Provide complete params; required args must be set directly or via param binding with ref:<artifact path>.
3) Use ref:<artifact path> in param value to bind content into a tool param (like a pointer/alias/ref). The runtime injects the referenced content.
4) Only bind/fill params that the tool actually declares in its args.
5) Use react.write to write your generated content (reports, summaries, plans, prose). For non-internal channels, it will be streamed to a user. 
   Regardless of whether you pick the kind='display' (no file shared) or kind='file' (stream and also share the file), we always capture it as a file artifact. 
   It is available for further reference in fi:<turn_id>.files/<path> with the <path> you provide (and for exec, with simply <path> as OUT_DIR-relative path).
   react.write params must be in order: path (use nice name), channel, content, kind.
   
6) Use react.patch to update an existing file. react.patch params must be in order: path, channel, patch, kind.
   
7) Do NOT place artifact contents in completion_summary. This makes it invisible to a user.
   
8) rendering_tools.write_* tools: prefer rendering existing artifacts to files. Prefer avoid generate new content directly in the call to rendering_tools.write_*.
   First create content with react.write, then call write_* by binding that artifact content via setting value of the `content` param to 'ref:<artifact path>'.
   Motivation: you won't have a chance to review the content you generate and semi-working file will be shared to a user. It's better if you first generate the content, review it in the visible context, then call rendering_tools.write_* to render it to a user.
7) Example of tool call:
   {{"action":"call_tool","strategy":"explore","notes":"search recent city transit updates","tool_call":{{"tool_id":"web_tools.web_search","notes":"Find official and recent sources","params":{{"queries":["city transit update timetable","public transport service changes"],"objective":"Collect recent official updates and sources","refinement":"balanced","n":6,"fetch_content":true,"country":"DE","safesearch":"moderate"}}}}}}

[react.read (CRITICAL)]
- Use react.read([...]) to control what artifacts/skills are visible in your context so you can refer to them.
- Example tool_call (load sources + artifact + skill):
  {{"tool_id":"react.read","params":["so:sources_pool[2,3]","fi:<turn_id>.files/some_art.md","sk:<skill id or num>"]}}

{artifacts_and_paths}

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
        exclude_tool_ids=[],
    )
    tool_block = build_instruction_catalog_block(
        consumer="solver.react.decision.v2",
        tool_catalog=tool_catalog,
        react_tools=get_react_tools_catalog(),
        active_skills=active_skills or None,
        include_skill_gallery=True,
    )

    protocol = _get_2section_protocol(json_hint)
    system_msg = create_cached_system_message([
        {"text": sys_1 + "\n" + sys_2 + "\n" + protocol + "\n" + tool_block + "\n" + time_evidence_reminder, "cache": True},
    ])

    attach_blocks = build_attachment_message_blocks(attachments or [])

    user_msg = HumanMessage(content=user_blocks)

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
