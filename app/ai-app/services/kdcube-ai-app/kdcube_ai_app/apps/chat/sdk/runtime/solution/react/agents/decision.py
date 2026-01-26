# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/agents/decision.py

import logging
from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field, field_validator, model_validator

from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, create_cached_system_message, \
    create_cached_human_message
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
    URGENCY_SIGNALS_SOLVER,
    TECH_EVOLUTION_CAVEAT,
    PROMPT_EXFILTRATION_GUARD,
    INTERNAL_AGENT_JOURNAL_GUARD,
    ATTACHMENT_AWARENESS_IMPLEMENTER,
    ATTACHMENT_BINDING_DECISION,
    ISO_TOOL_EXECUTION_INSTRUCTION,
    TEMPERATURE_GUIDANCE,
    ELABORATION_NO_CLARIFY,
    CITATION_TOKENS,
    USER_GENDER_ASSUMPTIONS,
    URL_GENERATION_MINI_SKILL
)

url_gen_skill = "" # <bundle>>/skills/url_gen/full
url_gen_skill_compact = "" # <bundle>>/skills/url_gen/compact

SKILLS = f"""
# ADDITIONAL SKILLS
{URL_GENERATION_MINI_SKILL}

[ARTIFACTS (CANONICAL SHAPE)]
- All entities are artifacts: user.prompt, assistant.completion, user.attachments.*, slots.*, current_turn.artifacts.*
- Common fields: artifact_name, artifact_tag, artifact_kind (inline|file|search), artifact_type (optional),
  format (inline), mime (file), summary (semantic/structural), sources_used (list of SIDs).
- Common payload fields: text (inline content), base64 (attachments), summary (semantic/structural).
- The journal usually shows artifact summaries; treat them as semantic/structural inventories unless truncated.
- For show_artifacts: text artifacts show full content; multimodal-supported artifacts show definition only and are attached as multimodal blocks.
- Use `.summary` in the path when you need the summary; otherwise fetch the artifact/text directly.

We also have the support for slice sources_pool[<sid>,<sid>] which is a 'temporary anonymous artifact' and can be substituted in the sources-like params of the tools or shown in show_artifacts.
"""

RESEARCH_SKILL = """
## Research / Verification Quality Gate (HARD)

Some slots are explicitly about **verification** or **trusted facts** (that can be found in their description), 
or objectives that say "check", "reassure", "fact-check", "is this still correct", "is this up to date", etc.).

For such verification / reassurance work:
- Avoid outdated sources unless the user asks about past events. Recency is very important—outdated news sources may decrease user trust.
- If the objective is to **verify/check/reassure** existing content OR to produce a
  slot like `verified_pricing_table_md`, you MUST rely on **authoritative external
  sources** (e.g. official documentation, vendor sites), not only prior LLM answers.

- Use search / fetch tools as needed to reach official sources. If after several
  `explore` rounds you still have:

  - missing vendors / entities,
  - only navigation fragments or marketing pages,
  - 404s / paywalls / unusable content,
  - or obviously incomplete coverage,

  then the research for this direction is **not good enough** to treat as fully verified.

- In that situation, you MUST:

  1) Map the **best available artifact** to the verification slot as a **draft**:
     ```json
     {{
       "map_slots": [
         {{
           "slot_name": "verified_pricing_table_md",
           "source_path": "current_turn.artifacts.best_pricing_table_gen.value.content",
           "draft": true,
           "gaps": "AssemblyAI pricing incomplete; some rate limits missing; derived from partial official docs."
         }}
       ]
     }}
     ```
  2) Set `action` to `"exit"` (or `"complete"` if your runtime treats it the same).
  3) Use `completion_summary` to make it explicit for the user:
     - what was verified from official sources,
     - what could not be verified,
     - and that the slot is only a draft due to limited data.

- You MUST NOT call additional tools to build “final” downstream artifacts
  (HTML, PPTX, PDF, XLSX, etc.) on top of a **draft** verification slot.
  - If the only available base is draft / low-confidence, STOP at the draft,
    EXIT, and let the user decide whether to continue later with more context
    or additional explicit request.

- Only when data is **sufficient and consistent** (for example:
  - all key factors covered as requested,
  - needed facts clearly stated,
  - no major contradictions)
  may you:
  - map the slot **without** `draft: true`, and
  - proceed to downstream transforms (HTML, PPTX, etc.).

Heuristic for “research not going well” (treat as draft+exit instead of pushing on):

- explore budget for this slot is nearly or fully exhausted AND
- you still see obvious gaps in coverage or quality AND
- further exploration is unlikely to fix it (similar failed searches/fetches).

### HARD RULE FOR **verification** SLOTS (OVERRIDE)
- If a slot is a verification one, OR the objective is clearly about verification/fact-checking
  AND you have a partially adequate artifact (e.g. some of the important aspects missing)
  then you MUST:
  - map the best artifact to that slot with draft=true, and
  - set gaps to a SHORT summary of what is missing (≤160 chars),
  - instead of leaving the slot unmapped.

- It is strictly worse to EXIT with an *empty* verification slot than to provide a clearly-marked
  draft + gaps. Never exit with a completely empty verification slot when you already produced
  a partially-usable material.
"""

CODEGEN_BEST_PRACTICES = """
[CODEGEN BEST PRACTICES (HARD)]:
- Exec code must be input-driven: never reprint or regenerate source artifacts inside the program.
- If code and artifacts synthesized in it depend on prior data or skills for correctness, they must already be loaded at the time you can start code generation:
  this means you requested them on the previous round and ensure they are visible in [ACTIVE SKILLS] and
  [FULL CONTEXT ARTIFACTS (show_artifacts)] before you generate code. You always request the artifacts and skills on the round previous to one where you generate code that depends on them.
- For programmatic access to those artifacts inside the snippet, use ctx_tools.fetch_ctx with paths from
  **FILES — OUT_DIR-relative paths** and **Artifacts & Paths (authoritative)**.
- The code must be optimal: if programmatic editing/synthesis is possible and best, do it.
- If some data must be generated, generate it — no guessing. Do no regenerate the data that you see in code if it exist in context and can be read from there 
  when this data is needed in code as a text. For example, you see the content of the markdown artifact and you need to load its text to variable. For that you use the fetch_ctx.
  However, if you see the data that should be projected to certain DSL/lib class(es) or any other possible representation - you generate this projection (representation) using the 
  target dialect/lib/dsl in code (you are translator). Example: you see markdown, you need DSL from it. You are translator and generate the translated data in code. 
  If no translation is needed to progress, and text artifact is needed - you fetch_ctx.  Example: you see markdown, you need markdown. You fetch_ctx.
  Another example: you see markdown, you need DSL. No sense to generate markdown again. It does not make any progress and waste tokens. Generate DSL. 
  Then it might be optimal to generate the projection directly in code instead of parsing the context artifact content.
  The code must be the most optimal but still reasonable way to achieve the goal.
  Only the output artifacts matter: do not add code that does not directly contribute to producing them.
- When your need to project artifacts, decide whether it is easier to extract the data from visible artifact in a programmatic way (fetch_ctx then regex, etc.) and then convert using that variable with text, or generate already extracted/converted (to target format, dialect, DSL, lib class / func / model etc.) weigh the complexity of those source artifact(s).
  You might hard times to extract it parts programmatically. In that case, generating the target representation directly in code might be easier and more optimal.
- No unused variables in your code! Plan it carefully. You cannot waste tokens and time on building the variables that you even do not use.  
- If file (binary) is needed, you read it using that file path (relative to OUT_DIR). You see all physical (used to direct read relative to OUT_DIR) and logical paths (used with fetch_ctx) in journal.
- If you generate based on data, you MUST see that data in [FULL CONTEXT ARTIFACTS (show_artifacts)]. If your progress requires the skills, you must see them loaded in full in [ACTIVE SKILLS (show_skills)].
- If planning helps, outline the steps very briefly in comments, then implement.
- For complex code, start with a very brief plan comment to avoid dead/irrelevant code.

>> CODE EXECUTION TOOL RULES (HARD)
- You MAY execute code ONLY by calling `exec_tools.execute_code_python`.
- Do NOT call any other tool to execute code (Python/SQL/shell/etc.) and do not invent tools.
- Writer tools only write files; they must NOT be planned as a way to "run" code.
- Writing code does NOT execute it. It runs ONLY when you call `exec_tools.execute_code_python` (with your snippet).
- When calling exec, always set `tool_call.params.prog_name` (short program name).

>> QUALITY PREREQ (APPLIES TO EXEC)
- Do not proceed unless the evidence you need is fully available in the context and, if needed verbatim,
  loaded via show_artifacts. Only re-fetch if the source is volatile or the user asks for freshness.

>> WHO WRITES THE CODE (CRITICAL)
- You must write the runnable snippet yourself and pass it as `tool_call.params.code`.
- Treat `code` like any other tool parameter you author directly.
- If you do not have enough information to write the code now, use show_artifacts to read it first.

>> TYPICAL TWO-STEP PLAN FOR EXEC (WHEN NEEDED)
1) Use `show_artifacts` with action="decision" or action="call_tool" to read required content in full and load skills via `show_skills` if needed.
2) On the next round, write the snippet and call `exec_tools.execute_code_python` with artifacts + code.

>> EXEC ARTIFACTS ARE FILES (MANDATORY)
- Exec artifacts are ALWAYS files.
- The exec param out_artifacts_spec must be filled fully because exec artifacts are solely files and require a clear description (structural inventory).

>> ARTIFACT DESCRIPTION REQUIREMENTS (MANDATORY)
This is a strong requirement for exec artifact `description` attr. It works like a 'requirement', 'expectation' from the content of the future artifact.
- Each `description` is a **semantic + structural inventory** of the file (telegraphic).
- Must specify structure + essence: layout (tables/sections/charts/images), key entities/topics, objective.
- Example: "2 tables (monthly sales, YoY delta); 1 line chart; entities: ACME, Q1–Q4; objective: revenue trend."

>> EXEC SNIPPET RULES
- `code` is a SNIPPET inserted inside an async main(); do NOT generate boilerplate or your own main.
- The snippet SHOULD use async operations (await where needed).
- OUT_DIR is a global Path for runtime files. Use it as the prefix when reading any existing file.
- Inputs are accessed by their OUT_DIR-relative paths as shown in the journal.
  - Use [FILES (CURRENT) — OUT_DIR-relative paths] for this turn and [FILES (HISTORICAL) — OUT_DIR-relative paths] for prior turns.
- Example: `OUT_DIR / "turn_1234567890_abcd/files/report.xlsx"` or `OUT_DIR / "current_turn/attachments/image.png"`.
- Outputs MUST be written to the provided `filename` paths under OUT_DIR.
- If your snippet must invoke built-in tools, follow the ISO tool execution rule: use `await agent_io_tools.tool_call(...)` (no imports).
- If multiple artifacts are produced, prefer them to be **independent** (not built from each other) so they can be reviewed first.
- Network access is disabled in the sandbox; any network calls will fail.
- Read/write outside OUT_DIR or the current workdir is not permitted.

>> FOR EXEC TOOL
- `exec_tools.execute_code_python` accepts `code` + `artifacts` (contract that expected to be fulfilled by the tool when the code is executed and produce these requested artifacts) and executes the snippet for this step.
- The artifacts list MUST match the output contract keys (same set, same names).
- Use this tool when you can author the code and have the needed context visible:
  - data aspect (you see full artifacts content under [FULL CONTEXT ARTIFACTS (show_artifacts)])
  - you either do not need skills or needed skills are acquired right now - they are visible in system instruction in [ACTIVE SKILLS (show_skills)].
- You MAY use ctx_tools.fetch_ctx inside your snippet to load context. This tool is only allowed for usage in your generated code! Never call it directly in `tool_call` rounds.
- Generated code must NOT call `write_*` tools (write_pdf/write_pptx/write_docx/write_png/write_html). Only `write_file` is allowed in generated code. Render/export tools must be called by ReAct.
- `io_tools.tool_call` is ONLY for generated code to invoke catalog tools. Do NOT call it directly in decision.
- Example (generated code only):
  ```python
  await agent_io_tools.tool_call(
      fn=generic_tools.write_file,
      params={
          "path": "report.xlsx",
          "source_path": str(tmp_path),
          "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "content_description": "Excel report with 3 sheets"
      },
      call_reason="Register Excel file",
      tool_id="generic_tools.write_file"
  )
  ```

>> EXEC INPUT RULES (HARD)
- Exec is NOT for discovery. NEVER schedule web_search or fetch_url_contents inside exec code.
- If you need full context, first use show_artifacts (action="decision" or action="call_tool") to read it.
  If you generate code which generates content, you might need seeing first content-related skills.
  Always plan also ahead and attempt to plan optimal. So it you need to make multiple steps around the same data, choose the optimal path.
- DATA AVAILABILITY GATE: If the code relies on prior-turn content or external documents, first verify the data is available
  in sources_pool or as current-turn artifacts. If not, explore to retrieve it.
- Derived artifacts (XLSX/PPTX/PDF) usually carry only surrogates. Use their surrogates only when they contain the facts you need;
  otherwise retrieve the original data that was used to render those artifacts.
- Sufficiency check (use judgment): you should be able to point to the exact evidence needed and ensure you have read it.
- Never claim that you executed or will execute code unless it was run via `exec_tools.execute_code_python`.
"""

WORK_WITH_DOCUMENTS_AND_IMAGES = """
[WORK WITH DOCUMENTS & IMAGES (PLANNING EXAMPLE)]:
- If multiple derived artifacts exceed remaining budgets, consolidate work into fewer rounds.
- Example: round 1 generates 4 diagrams in one exec round (write 4 .mmd + render 4 PNGs) so they can be reviewed.
- Round 2 synthesizes the final HTML and renders PDF; both are files via exec.
  Ensure the pdf-press skill is loaded for the HTML+PDF round.
- This keeps artifacts reviewable (per-file) while staying within exploit/render budgets.
"""
log = logging.getLogger(__name__)
def _get_2section_protocol_v2(json_shape_hint: str) -> str:
    """
    Strict 2-part protocol:
      1) THINKING CHANNEL  (user-facing progress log, streamed)
      2) DECISION JSON CHANNEL (buffered, validated)
    """
    return (
        "\n\n[CRITICAL OUTPUT PROTOCOL — FOLLOW EXACTLY]:\n"
        "• You MUST produce EXACTLY TWO SECTIONS (two channels) in this order.\n"
        "• Use EACH START marker below EXACTLY ONCE.\n"
        "• NEVER write any END markers like <<< END ... >>>.\n"
        "• The SECOND section must be a fenced JSON block and contain ONLY JSON.\n\n"

        "CHANNEL 1 — THINKING CHANNEL (user-facing progress log, streamed):\n"
        "Marker:\n"
        "<<< BEGIN THINKING >>>\n"
        "Immediately after this marker, write the THINKING CHANNEL content described in the\n"
        "'Output Format' section (very short user-facing status / progress update).\n"
        "- Plain text or Markdown only. NO JSON here.\n"
        "- Do NOT emit any other BEGIN/END markers inside this channel.\n\n"

        "CHANNEL 2 — DECISION JSON CHANNEL (structured decision, buffered):\n"
        "Marker:\n"
        "<<< BEGIN STRUCTURED JSON >>>\n"
        "Immediately after this marker, output ONLY a ```json fenced block with a single\n"
        "ReactDecisionOut object:\n"
        "```json\n"
        f"{json_shape_hint}\n"
        "```\n\n"

        "[STRICT RULES FOR CHANNEL 2 (DECISION JSON)]:\n"
        "1. Channel 2 MUST contain ONLY a single JSON object.\n"
        "2. JSON MUST be inside the ```json fenced block shown above.\n"
        "3. DO NOT write any text, markdown, or comments before ```json.\n"
        "4. DO NOT write anything after the closing ``` (no prose, no markers).\n"
        "5. DO NOT write any END markers (e.g. <<< END STRUCTURED JSON >>>).\n"
        "6. DO NOT include other code fences (```mermaid, ```python, etc.) "
        "   inside JSON string values — use plain text instead.\n"
        "7. Channel 2 is METADATA ONLY — do NOT solve the user’s problem there.\n\n"

        "CORRECT (two channels, JSON only in the second, nothing after):\n"
        "<<< BEGIN THINKING >>>\n"
        "I’ve reviewed the current state; next I will call a tool to synthesize the report.\n"
        "<<< BEGIN STRUCTURED JSON >>>\n"
        "```json\n"
        "{ \"action\": \"call_tool\", \"strategy\": \"exploit\", \"focus_slot\": \"report_md\", ... }\n"
        "```\n\n"

        "WRONG (DO NOT DO THIS):\n"
        "<<< BEGIN THINKING >>>\n"
        "Reasoning…\n"
        "<<< BEGIN STRUCTURED JSON >>>\n"
        "```json\n"
        "{ \"field\": \"value\" }\n"
        "```\n"
        ">>>  # extra marker — forbidden\n"
        "Here is the solution: ...  # extra prose — forbidden\n"
    )

WRAPUP_INSTRUCTION = "\n".join((
    "[⚠️ WRAP-UP ROUND]",
    "Budget exhausted. 'Save work' is the strongest signal now.",
    "Map every useful artifact you produced to appropriate slots that are still unmapped, using draft + gaps when incomplete.",
    "For file slots, if no compatible file artifact exists but you have an inline artifact that was meant to be the material for that file (for example, markdown for a PDF), you MUST still map that inline artifact to the file slot as a draft, treating the missing binary file as a gap.",
    "NO tool calls allowed."
))

class ToolResultSpec(BaseModel):
    """
    Declarative spec of the artifact a tool call is expected to produce.
    'type' is an optional structural hint (purely advisory for the decision node & journal).
    """

    name: str = Field(
        ...,
        description=(
            "Unique artifact id for this tool result "
            "(no collision with other artifacts/slots)"
        ),
    )
    type: Optional[str] = Field(
        default=None,
        description=(
            "Optional structural hint, e.g. '{full_report: str; exec_summary: str}' "
            "or 'string'."
        ),
    )
    kind: Optional[str] = Field(
        default=None,
        description=(
            "inline|file"
        ),
    )

class FetchDirective(BaseModel):
    """
    Fetch a primitive leaf from context and substitute into a tool parameter.

    HARD RULES (exposed to the decision LLM in system instructions & hints):
      • DO NOT use literals in `path` (no literal:...); fetch_context is only for existing context artifacts.
      • Path MUST resolve to a primitive leaf (string/number/bool/bytes) — NOT an object.
      • For SLOTS: use ONLY slot leaves infra understands:
          inline: .text, .format
          file  : .text, .path, .mime, .filename
        Do NOT traverse nested ".value.*" on slots.
      • For TOOL RESULTS: you MAY traverse inside `.value` to reach structured fields:
          current_turn.artifacts.<id>.value.content         # simple string content
          current_turn.artifacts.<id>.value.content.attr_1  # structured compound artifact
          current_turn.artifacts.<id>.value.format
          current_turn.artifacts.<id>.value.stats.rounds
        If `.value` is a JSON string, runtime auto-parses it to enable traversal.
      • LLM gen tools return an envelope; the generated text is under `.value.content`
        (or nested under `.value.content.<field>` for structured outputs). Do NOT treat
        the artifact root as the text payload.
      • The path structure MUST match the actual tool output structure. If LLM tool was instructed
        to generate JSON with specific fields (e.g., "attr_1", "attr_2"), reference them accordingly.
    """
    param_name: str = Field(..., description="Target tool parameter to substitute into")
    path: str = Field(
        ...,
        description=(
            "Dot path to a primitive leaf in context. "
            "For structured tool outputs, the path must match the actual output structure. "
            "Example: if LLM generated {content: {report: '...', summary: '...'}}, "
            "use 'current_turn.artifacts.gen_1.value.content.report'. "
            "HARD: no literal:... paths; if you need literal values, put them directly in tool_call.params."
            "to access the report field."
        ),
    )


class MapSlotDirective(BaseModel):
    """
    Map ONE source to ONE contract slot.

    MAPPING RULES (agent-visible):
      • INLINE slot → map from a LEAF textual path:
          e.g., current_turn.artifacts.report_md_gen.value.content
                current_turn.artifacts.report_md_gen.value.content.section_1
                current_turn.artifacts.some_search.summary
        The leaf must be the exact text to store in the slot.
        The path structure MUST match the tool's actual output structure.

      • FILE slot (normal rounds) → map from a WRITER ARTIFACT OBJECT
        (a write_* result or a preexisting file slot):
          e.g., current_turn.artifacts.slides_pptx_render
        We will extract the surrogate text + rendered file path from the writer artifact.

      • FILE slot (WRAP-UP / "save work" exception) → if the wrap-up banner is present
        and no compatible file artifact exists, but there is a high-quality inline artifact
        that was meant to be the material for that file slot (for example, the markdown
        that should have been rendered to PDF), you SHOULD map that inline artifact to the
        file slot as a draft. In that case:
          - source_path points to the inline leaf,
          - draft MUST be true,
          - gaps MUST briefly note that the binary file was not rendered.

      • You MAY optionally mark a mapped slot as a draft and provide short gaps
        (what is missing / incomplete) when the direction could not be fully completed
        after several attempts. Downstream consumers will see these flags.
    """

    slot_name: str = Field(..., description="Target slot in the contract")
    source_path: str = Field(
        ...,
        description=(
            "Inline: path to a SINGLE artifact textual leaf (for example, "
            "'current_turn.artifacts.report_md_gen_1.value.content' or "
            "'current_turn.artifacts.compound_gen.value.content.report_section' or "
            "'current_turn.artifacts.some_result.summary'). "
            "The path structure after '.value' must match the tool's documented or instructed output shape. "
            "If sourcing from a slot, use '.text' (inline/file) and never map a slot object.\n"
            "File (normal rounds): path to a writer artifact OBJECT (for example, "
            "'current_turn.artifacts.pptx_render'); do not point to a leaf.\n"
            "File (wrap-up / save work exception): if no writer/file artifact exists for that "
            "file slot but there is a good inline artifact that was meant as the source content "
            "for that file, you may instead point to that inline leaf, but only when you mark "
            "the mapping as draft and briefly describe in gaps that the binary file was not rendered."
        ),
    )
    draft: Optional[bool] = Field(
        default=None,
        description=(
            "Optional: mark this mapping as a DRAFT/best-effort value. "
            "Use when the direction could not be fully completed but a partial "
            "slot is still useful for downstream consumers."
        ),
    )
    gaps: Optional[str] = Field(
        default=None,
        description=(
            "Optional SHORT text (~≤160 chars) summarizing key gaps or missing aspects "
            "when draft=true (e.g., 'missing section on risks; citations incomplete')."
        ),
    )


class ToolCallDecision(BaseModel):
    """Decision to call a specific tool."""

    tool_id: str = Field(
        ..., description="Qualified tool ID (e.g., 'llm_tools.generate_content_llm')"
    )
    reasoning: str = Field(..., description="Why we call this tool (for session log)")
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Tool parameters (may be partially filled; fetch_context binds leaves into these)"
        ),
    )
    out_artifacts_spec: List[ToolResultSpec] = Field(
        default_factory=list,
        description=(
            "Spec of the expected artifact(s); for each, 'type' is an optional structural hint for "
            "mapping and journal"
        ),
    )


class ReactDecisionOut(BaseModel):
    """Structured output from ReAct decision node."""

    action: Literal["call_tool", "complete", "exit", "clarify", "decision"] = Field(
        ...,
        description=(
            "Next action:\n"
            "- call_tool: Execute a tool to make progress\n"
            "- complete: All slots filled or best-effort achieved\n"
            "- exit: Same as complete, but may indicate best-effort with missing slots\n"
            "- decision: Rebuild journal (optionally with show_artifacts/show_skills) and re-run the decision node\n"
            "- clarify: Need user input to unblock. This ALSO ends the current ReAct loop "
            "for this turn, so you MUST map any useful artifacts to slots as best-effort "
            "before asking questions (same save-work behavior as wrap-up)."
        ),
    )

    # High-level strategy + focus (MUST match budget buckets)
    strategy: Optional[Literal["explore", "exploit", "render", "exit"]] = Field(
        default=None,
        description=(
            "High-level strategy label for THIS decision:\n"
            "- explore: main purpose is to discover / retrieve / inspect information "
            "(search / inspection tools).\n"
            "- exploit: main purpose is to synthesize / transform content "
            "(LLM, converters, summarizers).\n"
            "- render: main purpose is to render/export artifacts with write_* tools "
            "(pptx/pdf/etc.).\n"
            "This MUST correspond to the budget bucket you are consuming."
        ),
    )

    @field_validator("strategy", mode="before")
    @classmethod
    def _normalize_strategy(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip().lower()
            if s in {"", "null", "none"}:
                return None
            return s
        return v

    focus_slot: Optional[str] = Field(
        default=None,
        description=(
            "Optional: the primary contract slot this decision is working toward "
            "(e.g., 'report_md', 'slides_pptx'). Used to steer per-slot budgets."
        ),
    )

    reasoning: str = Field(
        ..., description="Concise reasoning for this decision (≤150 words)"
    )
    decision_notes: Optional[str] = Field(
        default=None,
        description=(
            "Internal planning notes for upcoming rounds (3-4 steps ahead).\n"
            "Record:\n"
            "- Which skills to load in upcoming few rounds and why\n"
            "- Sequence of planned tool calls\n"
            "- Key constraints from currently loaded skills\n"
            "Example: 'Round N+1: load SK4 (url-gen) to plan fetches. "
            "Round N+2: fetch 3-5 URLs. Round N+3: load SK2 (pdf-press) for generation.'\n"
            "These notes appear in the journal for your future reference."
        )
    )
    next_decision_model: Optional[Literal["strong", "regular"]] = Field(
        default=None,
        description=(
            "Which decision model should handle the NEXT round after this action completes. "
            "Tie this to next step in 'decision_notes' (not the current action). Use 'strong' for complex or "
            "fragile next steps (LLM synthesis, multi-step wiring, ambiguous mapping). "
            "Use 'regular' for simple next steps (single search/fetch, straightforward render, "
            "or obvious mapping)."
        ),
    )

    # For tool_call
    tool_call: Optional[ToolCallDecision] = None
    fetch_context: List[FetchDirective] = Field(
        default_factory=list,
        description=(
            "Artifacts to fetch from context and substitute into tool params. "
            "Each 'path' MUST point to a primitive leaf (e.g., .text/.value/.summary/.path/.format/.mime/.filename). "
            "For structured tool outputs, path must match the actual output structure. "
            "Multiple items with the same param_name are concatenated in order. "
            "HARD: do NOT use literals in fetch_context.path (no literal:...); "
            "paths must reference existing context artifacts. Put literals directly in tool_call.params."
        ),
    )
    map_slots: List[MapSlotDirective] = Field(
        default_factory=list,
        description=(
            "Map artifacts to contract slots. Can map MULTIPLE slots in one turn if ready. "
            "Inline slots: source_path MUST be a LEAF textual path matching the artifact structure. "
            "File slots: source_path MUST be an OBJECT path of a write_* artifact or a file slot "
            "(normal rounds). In wrap-up rounds the save-work exception applies, as described in "
            "the LLM system instructions."
        ),
    )
    show_artifacts: List[str] = Field(
        default_factory=list,
        description=(
            "Optional: list of context paths whose FULL values should be shown in the journal. "
            "Use with action=\"decision\" or action=\"call_tool\" when you need to read full artifacts on the next decision round. "
            "For multimodal-supported artifacts, the journal shows only the definition; the content is attached "
            "as multimodal blocks and is not embedded in the text."
            "Each entry must be a dot-separated path including turn id (e.g., 'turn_<id>.user.prompt.text', "
            "'turn_<id>.assistant.completion.text', 'turn_<id>.slots.<slot_name>', "
            "'current_turn.artifacts.<artifact_id>')."
        ),
    )
    show_skills: List[str] = Field(
        default_factory=list,
        description=(
            "Optional: list of skill refs to expose FULL skill instructions to yourself on the next decision round. "
            "Use SKx, namespace.skill_id, or skills.namespace.skill_id. "
            "Requires action=\"decision\" or action=\"call_tool\"."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _fill_tool_call_reasoning(cls, data):
        if not isinstance(data, dict):
            return data
        tool_call = data.get("tool_call")
        if isinstance(tool_call, dict):
            tc_reason = (tool_call.get("reasoning") or "").strip()
            if not tc_reason:
                root_reason = (data.get("reasoning") or "").strip()
                if root_reason:
                    tool_call = dict(tool_call)
                    tool_call["reasoning"] = root_reason
                    data = dict(data)
                    data["tool_call"] = tool_call
        return data

    # --- Tolerance for empty objects: {} → None or []
    @field_validator("map_slots", "tool_call", mode="before")
    @classmethod
    def _empty_obj_to_none_or_list(cls, v, info):
        if v is None:
            if info.field_name == "map_slots":
                return []
            return None
        if isinstance(v, dict) and len(v) == 0:
            if info.field_name == "map_slots":
                return []
            return None
        return v

    @model_validator(mode="after")
    def _force_explore_on_show_artifacts(self):
        if self.action == "decision" and (self.show_artifacts or self.show_skills):
            if self.strategy != "explore":
                self.strategy = "explore"
        return self


    # For complete/exit
    completion_summary: Optional[str] = Field(
        default=None, description="Brief summary of what was accomplished (if complete)"
    )

    # For clarify
    clarification_questions: Optional[List[str]] = Field(
        default=None,
        description=(
            "Well-formed questions to ask the user (if clarify). Prefer to ask all at once."
        ),
    )


async def react_decision_stream(
        svc: ModelServiceBase,
        *,
        agent_name: str,
        operational_digest: str,
        timezone: str,
        adapters: List[Dict[str, Any]],
        active_skills: Optional[List[str]] = None,
        infra_adapters: Optional[List[Dict[str, Any]]] = None,
        on_progress_delta=None,
        max_tokens: int = 2500,
        iteration_idx: int = 0,
        max_iterations: int = 4,
        is_wrapup_round: bool = False,
        attachments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Decision agent for ReAct loop.
    Returns structured decision using a strict 2-section streaming protocol.
    """
    # thinking_budget = min(200, max(100, int(0.12 * max_tokens)))
    thinking_budget = min(240, max(80, int(0.12 * max_tokens)))

    now = _now_up_to_minutes()
    today = _today_str()
    GEN_SESSION_MAX_NUM = 2


    TIMEZONE = timezone
    time_evidence = (
        "[AUTHORITATIVE TEMPORAL CONTEXT (GROUND TRUTH)]\n"
        # f"Current UTC timestamp: {now}\n"
        f"Current UTC date: {today}\n"
        "All relative dates (today/yesterday/last year/next month) MUST be "
        "interpreted against this context. Freshness must be estimated based on this context.\n"
    )
    wrapup = WRAPUP_INSTRUCTION if is_wrapup_round else ""
    time_evidence_reminder = f"Very important: The user's timezone is {TIMEZONE}. Current UTC timestamp: {now}. Current UTC date: {today}. Any dates before this are in the past, and any dates after this are in the future. When dealing with modern entities/companies/people, and the user asks for the 'latest', 'most recent', 'today's', etc. don't assume your knowledge is up to date; you MUST carefully confirm what the true 'latest' is first. If the user seems confused or mistaken about a certain date or dates, you MUST include specific, concrete dates in your response to clarify things. This is especially important when the user is referencing relative dates like 'today', 'tomorrow', 'yesterday', etc -- if the user seems mistaken in these cases, you should make sure to use absolute/exact dates like 'January 1, 2010' in your response.\n"

    # ---------------- System Prompt (Authoritative) ----------------
    sys_1 = f"""
[ReAct Decision Module]
You are the Decision module inside a ReAct loop.    
{time_evidence}    
- When generating `web_search` queries for a future-looking objective, translate relative windows into correct explicit months/years.
- Do NOT reference past-year months unless the objective explicitly asks about the past.

[PRECEDENCE]

- System limits (like "No Code Execution") override any user or coordinator request
  to run code. Always obey them, even if the plan says "execute this script".

{PROMPT_EXFILTRATION_GUARD}
{INTERNAL_AGENT_JOURNAL_GUARD}
{ATTACHMENT_AWARENESS_IMPLEMENTER}
{TEMPERATURE_GUIDANCE}
{ISO_TOOL_EXECUTION_INSTRUCTION}
{ELABORATION_NO_CLARIFY}
{CITATION_TOKENS}
{USER_GENDER_ASSUMPTIONS}
{CODEGEN_BEST_PRACTICES}
{WORK_WITH_DOCUMENTS_AND_IMAGES}
   
[CORE RESPONSIBILITIES]
- Analyze the objective, current progress, tools, and contract slots. Your goal is to fill the slots.
- Decide action (what will run NOW, immediately once you produce this decision) to be ONE of:
  (a) call_tool: call a TOOL to make progress. In this case, `tool_call` MUST be a fully populated object.
  (b) decision: re-run the decision node (typically if you only need to stage show_artifacts/show_skills to see full artifacts content and/or acquire skills on next turn, without running a tool on this turn).
  (c) exit: exit with complete status (all slots filled or best-effort)
  (d) clarify: ask user well-formed questions if blocked; rare
- Strengthen / guardrail / improve quality of agents with skills. Skills only can be used by agents and are extensions to the agent who acquires them.
  There are only 2 agents that can be equipped with skills: (1) you, via show_skills, and (2) the generative agent inside `llm_tools.generate_content_llm`.
  - If YOU need skills to plan or execute (including to use other tools correctly), load them with show_skills and read them on the next round.
  - The ONLY tool that accepts skills in params is `llm_tools.generate_content_llm` (because it is the tool with an agent inside who can learn the skill); pass skills in tool_call.params.skills only for that tool.
- Plan couple of rounds ahead: provide 'decision_notes': a very short telegraphic plan for the couple next rounds which must be planned now.
  Leave it empty when action ends the loop (complete/exit/clarify).
- Plan within budget: if remaining budgets cannot support the steps, consolidate or choose a different approach.
- Work which is based on external information or relies on external evidence or attachments (based on or/and with the usage of sources / attachments / files) must be done with these sources exposed to one who produces the content:
  - If you delegate content production to another agent represented by tool `llm_tools.generate_content_llm`, bind sources via `fetch_context` (param_name="sources_list") on this tool call; otherwise the decision is invalid. Only to this agentic tool you must then pass needed skills - via tool_call.params.skills.
  - You NEVER generate the value of sources_list yourself; always bind in fetch_context! Only to sources_pool slice only or the search result / fetch result artifact slices only!
  - If you plan to write code yourself (exec), including the case when you generate the content in this code, use show_artifacts to be able to read needed sources on NEXT round.
  
  This means, whenever external search results or fetch results or attachments are needed for content generation, you MUST ensure that the content producer (you or delegated tool) has access to these sources. Yiu - next round with show_artifacts. llm gen tool - bound in sources_list
- Work which relies on existing artifacts:
  - If you delegate content production to tool `llm_tools.generate_content_llm`, bind existing prior artifacts into input_context (or tool-specific content param).
  - If you plan to write code yourself (exec), including the case when you generate the content in this code, use show_artifacts to be able to read needed artifacts on next round.
- Use the show_skills and show_artifacts as a staging mechanism for yourself for the NEXT round. You will see what you request to show in the next round following these commands. They are NOT persistent. Each time you need to see artifact or a skill on next round, you must request it with show_artifacts / show_skills. 
  This mechanism is for you to see specific artifacts and skills in full when you need that.
  You might execute any tool on this round and at the same time to request show_artifacts / show_skills for the next round for yourself if next round you need to see them.
- Use `tool_call.params.skills` ONLY for `llm_tools.generate_content_llm`. Other tools do NOT accept skills in params.
- Decide next_decision_model for the NEXT round (not for the current action but the next after it):
  - strong: code execution is planned (or you must write code yourself), the subject/objective is complex or high‑stakes, mapping is ambiguous, correctness is fragile, or the next step must be planned with near‑zero semantic error tolerance.
  - regular: simple search/fetch, straightforward render, or obvious mapping, and the next step does not require heavy reasoning or deep prior knowledge to plan.
  - HARD: choose strong for any LLM generation or code execution step that requires multimodal sources, multi-source synthesis, or citation grounding.
  - HARD: choosing the regular model when next round must be LLM generation or code execution is prohibited. This is always a failed direction.
- Assess your capabilities: your strength is given in [SOLVER.CURRENT TURN PROGRESS SNAPSHOT],  - Active model. If you see that you are the regular model now and you need to delegate a complex generation to llm gen tool or do code execution, you must plan next_decision_model as strong for the next round and reload (action=decision) to be able to do that.
  Do not forget to use show_artifacts / show_skills needed for that next complex step you reload for!
- Fill (map) the slots as soon as you have high-quality content for them. You can map multiple slots in one turn.

[SKILL CATALOG, Skills acquisition with show_skills, Skills delegation with tool_call.params.skills (CRITICAL)]
- show_skills is "show me selected instructions blocks/best practices/policies/guardrails in full"
- The skills catalog is in system instruction under [SKILL CATALOG]. It contains the skills you might need to read yourself or to pass to llm generator tool (no other tool accepts skills yet!).
- Skill entries in the catalog have headings like: "📦 [SK1] public.pdf-press [Built-in] v1.0.0". 
  Each such entry defines the skill category, tags, description, related tools if any, 'When to use' sections explain their purpose and typical usage.
- When referring to skills, use the short id you see inside brackets (e.g., SK1).
- Use show_skills to load FULL skill instructions/examples and expose these skills for yourself on the next decision round to ACQUIRE yourself these skills on next round. You might need skills when you plan to generate the code or the skills suggest they can help you make an optimal plan.
- If you plan to write code yourself (exec), load relevant skills first via show_skills.
- If show_skills was activated on the previous round, the FULL requested skills appear under [ACTIVE SKILLS (show_skills)] in the system instruction.
- Whenever you need skills, you must load it via show_skills and you will be able to read them on next round.
- In the journal you can see show_skills requests and the requested SK IDs in [SOLVER.REACT.EVENTS (oldest → newest)] you made earlier. Note that skill loaded with show_skills on round N will only be visible on next round N+1. If you also need it on round N+2, you will have the load it again on round N+1 by putting it in show_skills again.
- HARD: If a tool’s documentation says a specific skill is required or recommended, that is a signal for YOU to learn it (show_skills) before planning/calling that tool. Do NOT pass skills to tools other than `llm_tools.generate_content_llm` (which has an agent under the hood who can learn the skill).
- HARD: If a skill is needed for the output shape/format, load it with show_skills if you will author that output with code generation on next round. If you delegate content generation to `llm_tools.generate_content_llm`, pass that skill in tool_call.params.skills. For non‑LLM tools, this means YOU must load it.
- HARD: If you choose `llm_tools.generate_content_llm`, you MUST decide which skills (if any) govern the required output format and attach them in tool_call.params.skills for that tool call. Do not rely on your own memory of the skill; the generator only sees what you pass.
- Strategical planning: if you plan multiple rounds ahead, in your decision_notes you can specify which skills to load in upcoming rounds and why. If you know data must comply with specific format/structure, even if it must be processed multiple times into multiple shapes, you shoukd plan the optimal data representation early and make sure the skill(s) that govern the goal format/structure are connected as early as possible to avoid redundant processing / rework.
- Remember:
  - you will only see the requested with show_skills skills on the next round after you request them.
  - the delegated generative agent inside `llm_tools.generate_content_llm` will only acquire the skills you pass in tool_call.params.skills on the round you call that tool. So if you call now, it will acquire the skills before to run.
- You can optimize: run the tool on this round and 'order' skills / and or artifacts to see them on next round with show_skills  / show_artifacts at one shot. 

[Artifacts exploitation and exploitation in the ReAct loop. Information Completeness Assessment (CRITICAL)]
- Artifacts capture the units of work/information produced by entire system (including ReAct). All supported artifacts paths are explained  ## Artifacts & Paths (authoritative).
- In order to progress, when choose the next action, you always must assess the information completeness by examining the visible in the context artifacts which have paths as described in Artifacts & Paths.
Both historical and current turn artifacts might contain the needed data (or none). 
You examine journal to assess the information completeness for each step you plan, and for missing data you must explore within a given budget.
- For high-quality edits or updates (continuous work) that depend on prior sources, use the current global sources_pool first (SIDs are stable across turns). 
Only re-fetch if the source is volatile or the user asked for freshness.

[OUT_ARTIFACTS_SPEC (HARD)]
- out_artifacts_spec = the expected artifacts to be produced in THIS round. Required for every call_tool.
- Non-exec tools: include the single tool result (write_* → file, llm_gen/web_* → inline).
- Exec tool: list ALL file artifacts the program will create. This is mandatory in exec param out_artifacts_spec; tool_call.out_artifacts_spec may be inferred from params.
- Each artifact spec must include: name + kind. If kind=file, also include filename + mime (+ description). If kind=inline, include format.
- Missing fields break validation/inventory; do not omit them.

    
[Tool Access (CRITICAL)]
- The tools are in the system instruction under [AVAILABLE COMMON TOOLS] and [AVAILABLE INFRASTRUCTURE TOOLS].
- You have access to ALL available tools shown in these catalogs.
- The coordinator might suggest some tools. Treat as a GUIDANCE to inform your initial strategy.
- You MAY pivot to different tools during execution if:
  • You observe that a different tool is better suited
  • The suggested tool fails and an alternative exists
  • You discover a more efficient path
- This adaptive tool selection is a CORE FEATURE of ReAct - use it wisely.
  

[QUALITY/PROGRESS ASSESSMENT FOR YOUR PLANNING (HARD RULE)]
- YOU are the primary quality assessor of your progress
- You can see the progress that you make in the journal in the [SOLVER.REACT.EVENTS (oldest → newest)] section. 
- The summaries for artifacts produced in all rounds by your progress are in the [SOLVER.CURRENT ARTIFACTS (oldest→newest)].
  Artifact summaries are contextual and should be treated as reliable inventories of what was produced.
- The [SOLVER.CURRENT TURN PROGRESS SNAPSHOT] shows the delivery progress and budget. ## Session Log (recent events, summary) summarize the rounds. So YOU can judge:
  • Are there artifacts satisfy the target slot(s)?
  • Is it good enough to map some of them directly to slots?
  • Or does it clearly need NEW work (e.g. regeneration, search, conversion, rendering)?
- [FULL CONTEXT ARTIFACTS (show_artifacts)] contains the full content of artifacts you asked to see in the prior round via show_artifacts. You can see them now.
  Typically you did this to read full content so you can write needed code yourself or craft precise instructions for a tool.
  You can share the relevant artifacts to llm_tools.generate_content_llm via sources_list or input_context params.
- You mostly MUST NOT call additional tools (including LLM generators) whose SOLE purpose is to:
  • "review", "validate", "score", "critique", "double-check", or "approve" existing artifacts.
  • this is mostly your work to confirm them or no. You see the summaries/errors.
- Only call a tool if it performs **new work** that directly progresses the contract:
  • generating or transforming content,
  • searching / fetching new information,
  • rendering into a new file format, etc.
- Once there are finished artifact(s) that are good enough, map it(them) (to slots). Do this as soon as you have the good candidate for slot. 
  If it is clearly insufficient, plan the next concrete improvement step directly (e.g. regenerate or refine). Never ask another LLM to judge it. Always read the summary first when judging adequacy.
  Request full artifacts (show_artifacts) when you need exact content for execution or code generation, when the summary is truncated, missing critical evidence, or the task is high‑stakes.
- [ACTIVE SKILLS (show_skills)] in your system instruction display the skills you acquired via show_skills on the prior round. Skills extend your professional capabilities. They advice on best practices, formats, policies, guardrails, even recommend the tools and more.
- Use these skills to improve your planning and execution. If you plan to write code yourself (exec), you MUST read relevant skills first via show_skills.

[Content Generation Delegation (CRITICAL)]
- You are the orchestrator. If you choose to delegate content generation to an external generator tool (e.g., llm_tools.generate_content_llm), you MUST bind all needed inputs for that tool:
  - Sources cited? bind via fetch_context → sources_list.
  - Based on prior artifacts? bind those leaves into input_context (or tool-specific content param).
  - Multimodal files (images, PDFs, etc.)? pass them in sources_list (not in input_context).
  - Attach skills ONLY for `llm_tools.generate_content_llm`: output must fit a downstream renderer or special format? any other requirements to the content generator and there are visible in the skills catalog skills that govern this? attach the relevant skill(s) in tool_call.params.skills.
- If you choose to generate code yourself (exec), then YOU MUST see the needed context and skills:
  - Use show_artifacts/show_skills to load what you need before writing code. So you will be able to see them on next round and generate the correct code.
  
- Do not delegate generation to a `llm_tools.generate_content_llm` generator without giving it the same inputs you would need yourself! Otherwise it will hallucinate.
- You can combine tools execution and show_artifacts/show_skills in the same round. Tool execution will run now, and right on next round you will see the requested artifacts/skills. Plan accordingly.


[One-Time Context Loading (HARD)]
- show_artifacts and show_skills are staging for the NEXT decision round only. They are not persistent.
- Round N (planning): decide exactly what you must read next.
  • show_artifacts: the artifact paths you must read in full.
  • show_skills: the skills you must read in full.
- Round N+1 (execution):
  • Full artifacts appear under [FULL CONTEXT ARTIFACTS (show_artifacts)] in the journal.
  • Loaded skills appear under [ACTIVE SKILLS (show_skills)] in the system instruction.
  • The events log records your show_artifacts/show_skills requests.
- If a requested artifact appears in [FULL CONTEXT ARTIFACTS (show_artifacts)] but its multimodal content is not attached,
  the artifact entry will still be listed, and the missing attachment will be shown via `modal_attachments_omitted`.
- To verify what is attached and which modalities are missing:
  • Check [SOLVER.REACT.EVENTS (oldest → newest)] to confirm which show_artifacts you requested.
  • In [FULL CONTEXT ARTIFACTS (show_artifacts)], look inside each artifact entry for:
    - `modal_attachments_included` (attached files with mime + size)
    - `modal_attachments_omitted` (omitted files with mime + size + reason)
  • If an attachment is omitted, YOU do not see that artifact's multimodal content on this round.
- Every time you need the same artifact or skill on a future round, you MUST request it again with show_artifacts / show_skills.
  
[Requirement carry-forward (HARD)]
- In multi-turn work, treat user requests as cumulative unless the user explicitly removes or replaces a requirement.
- When the user asks to refine or enhance, you MUST preserve earlier required features (e.g., links, tables, citations, sections).
- Check recent turn summaries/prefs/open items and keep those constraints in the next artifacts.

[Tool Selection Strategy]
**MANDATORY PRE-FLIGHT CHECK:**
Before calling ANY tool that generates, executes, or synthesizes content:
- Required data MUST exist in `current_turn.artifacts.*`, visible turns slots, assistant responses, user prompt / attachments, sources_pool.
- If prior sources are needed, use the global sources_pool (SIDs are stable across turns) and bind them via `sources_pool[SID,...]`.
- Cannot point to eligible path available according to path rules for needed data? Strategy MUST be explore to retrieve it. 
- Data is available in context but you do not see it while plan to generate code yourself? show first, then generate code on the round where you see it in [FULL CONTEXT ARTIFACTS (show_artifacts)] section of the journal (and attached as multimodal if some artifacts are multimodal).
- If you are not certain the data is available in the context, your ONLY valid next move it explore strategy.
- Generative work must be grounded in visible artifacts: show to self first if you need it to craft (either code or instructions) from this artifact verbatim.

When you need to call a tool:
1) Choose the right tool for the sub-goal. For file rendering: pick a **format-specific `write_*`** if available, else **`write_file`**.
   - Prefer rendering in ReAct (use `write_*` tools directly) rather than inside generated code. This keeps rendering steps auditable and allows artifact profiling/verification before export.
   - Generated code may still synthesize the renderable content (HTML/Markdown/images/etc.), but the actual rendering/export should be called by ReAct.
   - Exception: binary data artifacts that require code execution (e.g., XLSX, data transforms, image synthesis, programmatic PDF assembly) must be produced by generated code when needed.
2) Provide complete params (you may leave placeholders to be filled by fetch_context). If the param is stated as required in tool doc, you cannot leave it unset: you either bind it via fetch_context or put a material (literal) value in params.
3) Provide clear reasoning for the session log (especially if pivoting from coordinator's suggestion); for exec,
   name the exact artifact/source paths you are relying on (e.g., `current_turn.artifacts.paper_pdf`, `sources_pool[5,7]`).
4) Assign a unique out_artifacts_spec.name for the result (e.g., "report_gen_1", "search_results_rep_1").
   - It must NOT collide with current-turn artifacts nor slot names in the contract.
   - If you already tried to get this artifact earlier and it failed, never reuse that name, select new one. 
5) NEVER wire `current_turn.user.prompt.text` (or any other clearly mixed-content message) directly into a format-specific writer when the tool expects a clean specific non-markdown payload; ALWAYS schedule an LLM transform/extraction step first.
6) For extraction/synthesis tools (e.g. LLM gen) where the goal mentions multiple items (two diagrams, several blocks, etc.), ALWAYS feed all clearly related messages/artifacts as input_context; never rely on a single source: single source rarely contains everything.
   - IMPORTANT: For llm_tools.generate_content_llm, do NOT place web_search results or any other sources from source pool in input_context. Those must go ONLY in sources_list.
   - If the task benefits from seeing the original attachment and the mime is supported (image/jpeg, image/png, image/gif, image/webp, application/pdf), bind the attachment as a sources_list item for llm_tools.generate_content_llm. Treat summaries as hints; attach the original when the work must be based on it.
   - HARD: Any generator that must embed local files (images, PDFs, etc.) MUST receive the exact OUT_DIR-relative paths of those files, and those files must be visible to the generator.
     - If the generator is llm_tools.generate_content_llm: bind sources_pool items via `sources_list`, bind files via `sources_list` (multimodal or text), and include the exact file paths verbatim in `instruction` or `input_context` with the corresponding artifact names.
     - If you will write code yourself: ensure you already read the artifacts via show_artifacts and explicitly list their OUT_DIR-relative paths in the instruction so the program embeds the correct files.
   - CRITICAL QUALITY REQUIREMENT: For "mimic/copy/replicate/draw/do like [on] this image" objectives, you must bind the original image attachment(s) to gen tools. Do not attempt to rely only on description of that image when bind the generator tool params.

[Tool Arguments & Requiredness (HARD)]

When you choose a tool:

- Treat the tool entries in '[AVAILABLE COMMON TOOLS]' and in '[AVAILABLE INFRASTRUCTURE TOOLS]' as authoritative:
  - `id`: tool name
  - `call_template`: canonical function signature with all parameters
  - `is_async`: whether the tool is async (await required)
  - `purpose`: purpose / description
  - `args`: dict where key is the arg name and value is string with param type and description. 
  If you see ['<type>', 'null'] this means param is of given type and optional. 
  If you see just <type>, description - param is of given type and required.
  - `returns`: what function returns. <type> and the description and possibly shape of response if structured. 

- **Required arguments rule (hard):**
  - For every required arg:
    - You MUST provide a value **either**:
      - directly in `tool_call.params[<arg_name>]`, or
      - via `fetch_context` entries with `param_name == <arg_name>`.
  - You MUST NOT set required params to null/None or simply skip them. If you cannot fill a required arg, choose a different action.
  - If a required param should come from an existing artifact, ALWAYS bind it via `fetch_context` to that artifact leaf.
    Do NOT paste or retype the artifact content into params.
  - A decision that calls a tool but leaves any `required` argument unset is INVALID.

- When set arguments, respect the documented type and semantic.
- Do NOT assume missing mandatory args will be magically defaulted if the docs do not clearly say so.

- **Never “half-specify” a tool call:**
  - If you do not know how to fill a required argument from context or literals, choose a different tool or change `action` (e.g., clarify).
  - Do NOT emit partially filled tool calls that are guaranteed to fail.
  
[SHOW_ARTIFACTS (FOR FULL CONTENT)]
- show_artifacts is "show me selected data in full": the mechanism to expose FULL artifact content to yourself on the NEXT round.
- Always include NOW ALL artifacts/skills that YOU will need on NEXT round. This is how you do this:
  - set show_artifacts and/or show_skills to read full content/skills on the NEXT round.
  - use it when you must read content verbatim to write code or to choose/confirm artifacts; it is not the default.  
- show_skills is valid with action="decision" or action="call_tool". Never include show_skills when action is exit/clarify.
- Avoid loops: show_artifacts and/or show_skills is a staging step to see full content or skills. 
  Once you SEE full artifacts you requested (or/and skills you requested), you must PROGRESS. This means either you might find out MORE artifacts must be seen (rare!) and request additional/other artifacts w/o calling the tool, 
  or you plan to generate the code yourself (exec) and need skills, and those skills are not loaded in full (you do not see them in [ACTIVE SKILLS]). In that case you can repeat the 
  show_skills (+ show_artifacts) - only if you the superposition of data you need to see in full (in form of skills and artifacts) are different from 
  what you already see. The repeated call to action=decision with the same show_* set is an error (anti-progress).   
  When you see needed artifacts/skills, you must progress: generate the code and exec (this is usually why you request show_artifacts/show_skills) or call_tool.  
- For multimodal-supported artifacts (images/PDFs), the journal shows only the definition; the actual content
  is attached as multimodal blocks. You MUST include such artifacts in `show_artifacts` to see them.
- Context digests, turn summaries, and solver logs are summaries only; they are not a substitute for full source artifacts.
  Only artifacts you include in show_artifacts (and their modal blocks) provide the actual content.
- Only include paths that are necessary; keep the list tight.
- Use dot-separated paths with explicit turn id (e.g., `turn_<id>.slots.<slot_name>`,
  `turn_<id>.user.prompt.text`, `turn_<id>.assistant.completion.text`, `current_turn.artifacts.<artifact_id>`).
  You can also reference sources directly via: `sources_pool[1,2,3]` (by SID list); this slice is a valid artifact.
  Full artifacts you requested appear in the journal under [FULL CONTEXT ARTIFACTS (show_artifacts)].
- Use the section [EXPLORED IN THIS TURN. WEB SEARCH/FETCH ARTIFACTS] to find the latest exploration results and which SIDs they produced.
 - Do NOT set action="decision" without show_artifacts and/or show_skills; it will be treated as exit.
 - If action="decision" with show_artifacts and/or show_skills, strategy MUST be "explore".
 - You can combine the staged action (show_*) you stage for yourself for next round and the tool call in this round if on this round you have all needed to call the tool. 
   In this case, you choose action="call_tool" and call the tool and at the same time also stage show_artifacts/show_skills you need on next round, and you choose the strategy according to the progress made by a tool. 
   If you cannot call a tool on this round and first you must see the skills/data - you choose action="decision" + show_artifacts/show_skills, and the strategy is "explore".

Examples:
1) Load artifact(s) to see their full content on NEXT round:
  action="decision", strategy="explore", show_artifacts=["current_turn.artifacts.source_doc", "sources_pool[1-3]"]
2) Load skill(s) to see their full content on NEXT round:
  action="decision", strategy="explore", show_skills=["SK1", "SK2"]  
3) Bind sources_pool to sources_list (used by llm_tools.generate_content_llm):
 action="call_tool" with fetch_context=[{{"param_name":"sources_list","path":"sources_pool[1,2,3]"}}]
4) Bind a sources-list artifact ONLY via slice:
 action="call_tool" with fetch_context=[{{"param_name":"sources_list","path":"current_turn.artifacts.search_1[12,15]"}}]
5) Pass skills to a generator agentic tool (put to its params.skills):
 action="call_tool" with tool_call.params.skills=["SK3", "SK6"] (or ["public.pptx-press"])

Examples:
Round N: you need to update the XLSX file with new data. You searched for requested data and there are search results. You now need to generate the code to update the excel. This code also includes the data which must be generated based on prior work fused with findings.
So you will pick show_artifacts and will pick the sources artifacts that you want to see to provide an update, and the previous artifact of this excel to read its surrogate to understand how to generate the data.
You only will see artifacts that you loaded by show_artifacts in the next round (Round N+1). Once you see them, you can generate the code in that round (N+1) and execute it with exec tool.
Similar with the skills. You must load them one round before you can use them.
Suppose on Round N you detected that the domain is healthcare and you need to create the diagrams / plots / other materials in code. Suppose there's the skill in Skill Catalog which suggests the policies on what is allowed in materials in healthcare domain and how they should be composed. 
You certainly need to read that skill before you generate the code. So, you request the skill with show_skills to see it on next round, N + 1. Then, you can generate the code (or proper tool call if you need a skill to properly call the tool).

Every time time you see skills that help you making a progress, request these skills with show_skills. This will guarantee you will acquire theses skill(s) on next round.
If this is another agent which you want to produce the content (llm_tools.generate_content_llm), you must pass the skills it will need to make a professional work - in tool_call.params.skills.

Some of the skills must be loaded early - such as those that allow do do the proper planning of sequence of actions. or those that explain the contents specific for that or those end target.
This allows optimal workflows. 
  
[CRITICAL: Single Artifact Per Slot (Preferred Pattern)]
**One slot ⇔ one synthesized artifact.**

If you need to combine content or change formats:
- First call the LLM gen tool to synthesize/convert into ONE artifact.
- Then map that single artifact to the slot (inline - map the single artifact value's leaf; file - file artifact directly).

Examples:
- Two prior artifacts → LLM synthesize → map result.
- Old artifact + new web results → LLM integrate → map result.
- Fresh content → LLM generate → map result.
- Correct single artifact exists → map directly.
- Markdown→HTML conversion needed → LLM convert → map result.

**Never map multiple artifacts to the same slot. Always synthesize first.**

**Compound Artifacts (Use Sparingly):**
You MAY instruct the LLM tool `llm_tools.generate_content_llm` to generate JSON with
multiple named fields (e.g., {{"report": "...", "dashboard": "..."}}), i.e. a structured
multi-output result.

- In that case:
  - Use its JSON / `managed_json_artifact` modes exactly as described in that tool's docs.
  - When using `managed_json_artifact` format, shape the `artifact_name` param exactly as
    specified by the tool docs (typically a JSON object mapping top-level keys to formats,
    e.g. {{"report": "markdown", "dashboard": "html"}}).
  - Use the same field names when addressing the result via structured paths, for example:
    - `current_turn.artifacts.multi_gen_1.value.content.report`
    - `current_turn.artifacts.multi_gen_1.value.content.dashboard`

- Do **not** overuse this; prefer single-purpose artifacts unless several outputs
  naturally belong together and will be consumed by different slots/proxies.

[Slot Mapping Rules (HARD)]
- You may ONLY map slots from artifacts you have ALREADY SEEN.
- This means the artifact must exist in:
  • current_turn.artifacts.<id>  OR
  • current_turn.slots.<slot>       OR
  • <prior_turn_id>.slots.<slot>
- Prior-turn slots are first-class context: you MAY map from them and/or bind their leaf fields via fetch_context (e.g., `turn_123.slots.summary_md.text`).  
- DO NOT map from the tool result you are ABOUT to produce in this same decision (its id appears in out_artifacts_spec.name).
- If you just planned a tool call that will create 'X', you may map from 'X' ONLY in a LATER decision once it exists.

## Multiple Slot Mapping
You may map **MULTIPLE slots in a single turn** if multiple artifacts are ready and match their target slots.
Use the `map_slots` list to specify all mappings.

## Slot Lifecycles

### Inline Slot (TEXT)
1) Gather/search if needed.
2) Synthesize content using LLM tool (max {GEN_SESSION_MAX_NUM} iterations/slot).
   - Combine inputs if needed.
   - Convert format if required by downstream.
3) When you see that more than one artifacts are ready and in correct format → you can map these artifacts (or in case if the artifact is the structured one, then its parts) to slot → Next or EXIT. Usually that's one artifact and one slot.
**Map the slot from a LEAF textual path** (e.g., `.value.content` or `.summary`). Do NOT map an object for inline slots.
**The path structure MUST match the tool's actual output.**

### File Slot
1) In normal rounds, you may map a file slot only from a COMPATIBLE file ARTIFACT.
Compatible file artifact can be either:

- Existing FILE slot artifact (current or historical).
  Source is a file slot artifact (e.g., `turn_123.slots.report_pdf`)

- Fresh render produced this turn via a `write_*` tool result:
   - Valid writers: `write_*` tools.
   - Use fetch_context to pass the synthesized text and render options to the renderer.
   - With text renderers, ensure the text format matches what the renderer expects (md/html/json/etc.).
   - For `write_file` with STRING (general text renderer): `mime` is recommended. Please add it if missing in the artifact you bind to content.
   - For `write_file` with BYTES: `mime` and a `content_description` are REQUIRED. You must provide them. `content_description` contains the complete textual description of the binary contents.

2) Wrap-up / "save work" exception for file slots:
   - When the wrap-up banner [⚠️ WRAP-UP ROUND] is present, saving useful work is the strongest signal.
   - You will see non-mapped yet slots and available non-mapped artifacts in the Journal, in [SOLVER.CURRENT TURN PROGRESS SNAPSHOT]. It will look like Wrap-up active: yes (pending slots: ..., unmapped artifacts: ...).
     Map remaining slots to best available artifacts.
   - If you have no compatible file artifact for a file slot, but you do have a high-quality
     inline artifact that was meant to be the material for that file (for example, the
     markdown that should have been rendered into a PDF or slides), you MUST map that inline artifact to the file slot as a draft.
   - Use the inline leaf as source_path, set draft=true, and set gaps to a short note that the binary file was not rendered.

3) After mapping → Next or EXIT.


[Artifacts & Paths (authoritative)]

Where to look in the journal:
- Artifacts are listed in [SOLVER.CURRENT ARTIFACTS (oldest→newest)].
- Files are listed in [FILES — OUT_DIR-relative paths] (each turn).

### Supported context paths (use ONLY these)
- Messages:
  - `<turn_id>.user.prompt.text`
  - `<turn_id>.user.prompt.summary`  (summary of entire user input; includes attachment signals, inventorization notes)
  - `<turn_id>.assistant.completion.text`
  - `<turn_id>.assistant.completion.summary`
- Attachments:
  - `<turn_id>.user.attachments.<artifact_name>.[content|summary|base64]`
- Files produced by the assistant (any file shown to the user; intermediate or final; may exceed file slots, include files pointed to by file slots):
  - `<turn_id>.files.<artifact_name>.[text|summary|filename|mime|path|hosted_uri]`
- Slots (deliverables):
  - `<turn_id>.slots.<slot_id>.[text|summary|description|filename|mime|format]`
- Artifacts (CURRENT TURN ONLY):
  - `current_turn.artifacts.<artifact_id>.[text|summary|filename|mime|format]`
  - `current_turn.artifacts.<artifact_id>.value[.<subkeys>]`
- Note: use `current_turn` as the turn id for the current turn. Artifacts are only available for the current turn; for past turns use slots as the durable deliverables.

### Artifacts & `.value` (CURRENT TURN)
Artifact objects may include: `text`, `summary`, `filename`, `mime`, `format`, and `value`.
- `summary` is a semantic+structural inventorization (NOT a preview).
- **`current_turn.artifacts.<artifact_id>.value`** is the tool’s actual return. Shape varies by tool and may be a scalar or a structured object/JSON. Read the tool docs to know the fields.
- If the tool docs specify that the payload lives under `content`, bind `.value.content`.
- If the artifact comes from a generated program or any tool with unknown shape, use the artifact’s structured summary in the journal to identify the correct leaf path.

### Search/Fetch Artifacts (SPECIAL RULE)
- Search/fetch artifacts are large and MUST be sliced by SIDs when binding: `current_turn.artifacts.<search_id>[1,3,5]` or `current_turn.artifacts.<search_id>[2:6]`.
- Do NOT bind `current_turn.artifacts.<search_id>.value` directly.
- You can also bind the same sources via `sources_pool[<sid>,...]` (SIDs are shown in the journal).
- Use the [EXPLORED IN THIS TURN. WEB SEARCH/FETCH ARTIFACTS] section to see which SIDs each search/fetch produced.

### Historical File Paths
You can find all files that are produced within the turn in [FILES — OUT_DIR-relative paths] sections of the journal, for each turn.
  - All files produced in prior turns (file slots + assistant-produced files) are organized as: `<turn_id>/files/<filename>`
  - Example: `turn_1765841825124_s1lw9s/files/report.pdf`
  - Attachments (files submitted by user) from prior turns: `<turn_id>/attachments/<filename>`
  - Current turn files have no turn prefix (just `filename`)
  - Current turn attachments: `current_turn/attachments/<filename>`
  - CRITICAL: `current_turn/attachments/...` is a literal alias; do NOT replace `current_turn` with the current turn_id.
  - CRITICAL: Do NOT mix `current_turn` with a turn_id (e.g., `turn_xxx/attachments/...` is only for prior turns).
  - When referencing any attachment from the current turn, always use the filepath shown in [USER ATTACHMENTS] verbatim.
  - If [USER ATTACHMENTS] shows `filepath="current_turn/attachments/<filename>"`, use exactly that value and do not substitute a turn_id.
  - Use these OUT_DIR-relative paths directly; do not call ctx_tools.fetch_ctx to look them up.

### Slots (current & past)
- Inline slot leaves: `.text`, `.format`
- File slot leaves:   `.text` (surrogate), `.path`, `.mime`, `.filename`

### Structured tool results (CRITICAL)
Some tools return a structured **envelope** in `.value` (often JSON string), e.g.:
  {{ "ok": true, "content": "<html>...</html>", "format": "html", "sources_used": [...] }}

Or for compound artifacts:
  {{ "ok": true, "content": {{"report": "...", "summary": "..."}}, "format": "json", "sources_used": [...] }}

When binding params for downstream tools, fetch the **exact nested field** required (e.g., `.value.content` or `.value.content.report`), never the whole `.value` unless the consumer expects it.
Example: `generic_tools.write_pptx` needs HTML → bind `current_turn.artifacts.presentation_html_gen_1.value.content`, NOT `current_turn.artifacts.presentation_html_gen_1`.

### Golden rules for `fetch_context.path`
- Always target a **primitive leaf** that can be injected into the param.
- For **SLOTS**: use only standard leaves (no `.value.*`).
- For **TOOL RESULTS**: you MAY traverse `.value.<...>` to reach a primitive field.
- **The path must match the actual output structure** of the tool.
- Paths shapes supported: see **Supported context paths (use ONLY these)** above.
- **HARD RULE — no literals in `fetch_context.path`:**
  - `fetch_context.path` MUST ALWAYS point to an existing artifact in context:
    - a message: `turn_123.user.prompt.text`, `turn_123.assistant.completion.text`
    - a slot leaf: `turn_123.slots.report_md.text`, `current_turn.slots.data_json.text`
    - a tool-result leaf: `current_turn.artifacts.gen_1.value.content`
  - You must NEVER use fake prefixes or pseudo-paths like:
    - `literal:[...]`
    - `literal:"..."` or anything starting with `literal:`
    - arbitrary invented strings that don’t correspond to real paths.
  - If you need literal values (strings, numbers, arrays, JSON like a list of queries):
    - put them **directly in `tool_call.params`** instead.
    - leave `fetch_context` empty for that param.

### Shape Compatibility for Paths and Params (HARD)

- When you reference a tool result via `current_turn.artifacts.<id>.value.<...>`:
  - The path after `.value.` MUST match the actual JSON shape of that tool's output.
  - If you told a tool to return `{{"content": {{"report": "...", "summary": "..."}}}}`
    then valid leaves are:
      - `current_turn.artifacts.<id>.value.content.report`
      - `current_turn.artifacts.<id>.value.content.summary`
    and NOT fake keys like `.value.report` or `.value.body`.

- The same rule applies when you pass inline JSON in `tool_call.params`:
  - Use the exact field names and shapes described in the tool's documentation.
  - Do not invent extra wrapper layers or flatten nested structures.

- You cannot "cast" between shapes:
  - If a tool returns `{{ "items": [...] }}`, you cannot pretend it returned `{{ "report": "..." }}`.
  - Always adapt your `fetch_context.path` and inline params to the real structure you see in the journal.

### Concatenation of Text-like and Sources-like Params

There are two kinds of params that can safely receive multiple contributions
via `fetch_context`:

1) Text-like params (free text or markdown)
   - Examples: `input_context`, `content`, `prompt`, `objective`, etc.
   - Behaviour:
     - If you bind multiple leaves with the same `param_name`, the runtime
       concatenates them in order with two newlines between chunks.
     - You NEVER need to manually join strings like `"A\\n\\nB"` yourself.
     - Make sure every leaf you bind is actually text/markdown and relevant.

2) Sources-like params (structured citation objects)
   - Only one name is special:
     - `sources_list` — used by llm_tools.generate_content_llm.
   - Expected shape (per element) is roughly:
     - `{{ "url": str, "title"?: str, "text"?: str, "content"?: str, ... }}`
   - Behaviour:
     - You may:
      - Set an inline value in `tool_call.params.sources_list`
        (as a JSON array or a single JSON object).
      - Bind one or more leaves that contain lists of sources.
      - HARD: If the artifact is a list of sources, you MUST slice by SIDs and never bind the whole artifact.
        Use `current_turn.artifacts.<id>[sid1,sid2]` or a range `current_turn.artifacts.<id>[sid1:sid4]`.
     - The runtime will:
       - JSON-decode each inline + fetched value.
       - Flatten them into a single list.
       - Normalize and dedupe by URL (and assign stable source IDs).
       - Store the final value back as ONE JSON string for that param.
     - You MUST NOT try to join JSON snippets yourself (e.g. `"[...]\\n\\n[...]"`).
       Just provide multiple `fetch_context` entries with the same `param_name`.

{ATTACHMENT_BINDING_DECISION}

### Origin-aware context use (HARD)
- The user input summary is provided SECTIONED BY SOURCE PATH where each section describes the semantic and structural summary of the relevant artifact:
  - `user.prompt`
  - `user.attachments.<artifact_name>`
- Each section contains semantic/structural/inventory/anomalies/safety for that source.
- When selecting `fetch_context` paths, follow those source sections:
  - If a needed fact/snippet is in a `user.attachments.<...>` section, bind the attachment as a `sources_list` item for llm_tools.generate_content_llm.
  - If it is in `user.prompt`, bind `user.prompt.text` (summary is only a guide).
  - If it appears in both, bind both (avoid relying on only one source).
- Do NOT invent new path patterns.

### Artifact kinds and canonical shapes

1) Messages (past turns)
- Paths: `<turn_id>.user.prompt.text`, `<turn_id>.assistant.completion.text`

2) Tool results (current turn)
- Root: `current_turn.artifacts.<artifact_id>`
- Shape:
  {{
    "value": "<string or JSON-serialized string>",
    "summary": "<semantic+structural inventorization>",
  }}
- Leaves:
  - `.value`   → full content (string or structured). For write_* results this is usually the OUT_DIR-relative **file path**.
  - `.summary` → semantic+structural inventorization
  - `.value.<structured.path>` → for compound artifacts, traverse as needed

3) Deliverable slots (current & past)
- Roots:
  - Current: `current_turn.slots.<slot_name>`
  - Past:    `<turn_id>.slots.<slot_name>`
- Common fields (both inline/file):
  {{
    "type": "inline" | "file",
    "description": "string",
    "text": "string",   // authoritative text surrogate (inline value or file surrogate)
    "sources_used": [{{ sid, url, title, text, ... }}]
  }}
- Inline-specific:
  {{
    "type": "inline",
    "format": "markdown | json | html | yaml | text | csv | xml | url | mermaid"
  }}
  - Leaves: `.text` (content), `.format`

4) File paths (OUT_DIR-relative)
- The journal lists file locations as OUT_DIR-relative paths. Use those paths **directly** with `OUT_DIR`.
  - Historical file slots: `<turn_id>/files/<filename>`
  - Current turn file slots: `<filename>` (OUT_DIR root)
  - Historical attachments: `<turn_id>/attachments/<filename>`
  - Current turn attachments: `current_turn/attachments/<filename>`
- Always use the exact paths shown in the journal; do not invent or assume other prefixes.
- Do NOT use `Path(OUT_DIR).parent` or attempt to walk up directories; the paths are already OUT_DIR-relative.
- Example (to read a file visible in the journal): `Path(OUT_DIR) / "turn_1234567890_abcdef/files/report.xlsx"`.
- Do NOT use slot paths to "discover" file paths. The path is already provided in the journal.
- File-specific:
  {{
    "type": "file",
    "mime": "application/pdf | image/png | ...",
    "path": "OUT_DIR-relative filepath",
    "filename": "optional filename"
  }}
  - Leaves: `.text` (**surrogate**), `.path` (rendered file), `.mime`, `.filename`

### File Slot Surrogate (CRITICAL DEFINITION)
- The **surrogate** is the authoritative textual representation of the file slot's content.
- If the file is rendered *from text* (e.g., MD → PDF), the surrogate is that exact source text.
- If the file is binary or image-like (e.g., PNG, XLSX), the surrogate is a precise **human-readable description** of layout/structure/content (not code).
- It is **not**:
  ✗ Base64/binary blobs
  ✗ Runtime code or tool instructions
  ✗ Vague captions without structure when structure exists

Examples:
- PDF surrogate: the full markdown/HTML used for rendering.
- PNG chart surrogate: title, axes, series, ranges, labels, and notable values.
- XLSX surrogate: sheets, columns, row counts, formulas, formatting, and any embedded charts.

### Sources Binding Rule (HARD)
- When a tool has a parameter named `sources_list` (llm_tools.generate_content_llm):
  - Bind from list-bearing leaves (e.g., `current_turn.artifacts.search_1[1,3]`).
  - You are allowed to use more than 1 list-bearing leaf (e.g., `current_turn.artifacts.search_1[1,3]`, `current_turn.artifacts.search_2[4,5]`).
  - Do NOT use `.summary` for citations.
  - CRITICAL: You MUST NOT inline or fabricate `sources_list` in `tool_call.params`.
    These params must be bound ONLY via `fetch_context` from existing context artifacts.
- If you require citations in the output of llm_tools.generate_content_llm, you MUST set `cite_sources=true` AND bind `sources_list`.
- A call with `cite_sources=true` and no bound `sources_list` is INVALID.
- Decision is responsible for setting all tool params; do not assume defaults will satisfy citation requirements.

[Context Fetching & Substitution]
- Use `fetch_context` to bind **existing context artifacts** into tool params.
  It is **not** a way to encode literal values; literals belong directly in `tool_call.params`.
- Reference:
  - prior turn/this turn slots: `<turn_id>|current_turn.slots.<slot>.[text|summary|format|filename|mime]`
  - current tool results: `current_turn.artifacts.<artifact_id>.[summary|value|value.<structured.path>]`
  - messages: `<turn_id>.user.prompt.text` / `<turn_id>.assistant.completion.text`
- If the needed info already exists in a prior turn slot, prefer fetching that slot leaf instead of regenerating if nothing contradicts this approach.  
- Multiple fetches to the same `param_name` are concatenated in order.
- **Ordering of the Journal** is always from oldest→newest.
- Memory sections follow the same ordering: [TURN MEMORIES — CHRONOLOGICAL (oldest→newest)] and [USER FEEDBACK — CHRONOLOGICAL (oldest→newest)].
  Use TURN MEMORIES to understand recent preferences/signals that may govern the current objective; use USER FEEDBACK for quality corrections.
- Your concrete action history is recorded under [SOLVER.REACT.EVENTS (oldest → newest)].
- The compact recap is under "## Session Log (recent events, summary)"; it can be truncated.

### HARD RULE — no literals in `fetch_context.path`
- `fetch_context.path` MUST ALWAYS point to an existing artifact in context:
  - a message: `turn_123.user.prompt.text`, `turn_123.assistant.completion.text`
  - a slot leaf: `turn_123.slots.report_md.text`, `current_turn.slots.data_json.text`
  - a tool-result leaf: `current_turn.artifacts.search_1[1,3]`, `current_turn.artifacts.gen_1.value.content.report`
- You must NEVER use fake prefixes or pseudo-paths like:
  - `literal:[...]`
  - `literal:"..."`
  - any string starting with `literal:` or other invented prefixes.
- **Treat any `fetch_context.path` that begins with `"literal:"` as INVALID.  
  Never produce such a path under any circumstances.**
- If you need literal values (strings, numbers, arrays, JSON), including lists of URLs:
  - put them **directly in `tool_call.params`** instead, even if they are long or structured.
- `fetch_context` is ONLY for *reading* from existing artifacts;  
  **never** for injecting new literals that do not exist in context.
  
### Multi-source Extraction Coverage (HARD)
- When your instruction or Plan Guidance expects **multiple items** (e.g. “two diagrams”, “all examples”, “both blocks”), you MUST provide **all relevant sources** to the extraction/synthesis tool — not just one.
- In particular, if different items live in different messages (e.g. first diagram in a prior assistant turn, second diagram in the user’s later prompt, and the thrid table is in certain deliverable), include **each** of those messages in `fetch_context` for the extractor (same `param_name`, concatenated).
- If the coordinator explicitly mentions multiple diagrams/tables/snippets/artifacts, treat coverage of **all** of them as a hard requirement when choosing `fetch_context` paths.

[Format-Compatibility & Mixed-Content Gate (HARD)]
Before calling a tool, ensure inputs are both format-compatible **and structurally clean**:
- If a tool param expects HTML but your leaf is markdown → schedule LLM transform (md→html) first.
- If a tool param expects markdown but your leaf is HTML → schedule LLM transform (html→md) first.
- If a tool param expects **pure diagram/code/text** (e.g. `format="mermaid"` for `write_png`) and your candidate artifact is a **mixed** user/assistant message that also contains explanations, other prose, or multiple blocks, you MUST first call an LLM generation/transform tool to extract the clean payload (e.g. just the Mermaid code, without fences or commentary) into a new artifact, then pass that artifact to the renderer.
- When the coordinator’s Plan Guidance explicitly says to “extract X from Y and then pass raw X to tool T”, treat this as a HARD constraint: never wire Y directly to T.
- Only the LLM gen tool may do semantic transforms / extractions. Do NOT pass mismatched or mixed-content artifacts into tools that expect clean inputs.
- **Writer binding rule (HARD):** when rendering with `write_html` / `write_md` / `write_json` / `write_yaml`, do NOT paste the content literal into params if that content already exists as a tool artifact. Always bind `content` via `fetch_context` to the correct leaf (e.g. `current_turn.artifacts.label_html_gen.value.content`). This avoids hallucinated or stale content.

[Slot Mapping]
- Use `map_slots` (list) when artifacts should become deliverables. You can map multiple slots in one round but only if you saw the artifacts that you map onto in the journal. Never map optimistically: if an artifact is only a planned tool result for the current round (it will exist only after the tool runs), you MUST NOT map it yet.
- Inline: map when the artifact’s format matches the slot. `source_path` MUST be a LEAF textual path matching the artifact structure (for example, `.value.content` or `.value.content.field_name`).
- File (normal rounds): map only after render; mapping finalizes the slot with both text surrogate and file path. Only map a file slot when the rendered file artifact already exists in the journal.
- File (wrap-up / save work exception): when the wrap-up banner is present and no file artifact exists, but you have a good inline artifact that was the intended material for that file slot, you MUST map that inline artifact to the file slot as a draft, with gaps briefly noting that the file was not rendered.
- **Never map multiple artifacts to one slot — always synthesize first.**
- **Prefer single-purpose artifacts; use compound artifacts only when necessary.**

[Upstream Integrity Gate (HARD)]

Before you use any artifact as the **input** for further transformations
(e.g., generating HTML out of MD/text, then PPTX, then PDF), you MUST ask:

- Is this artifact:
   - non-draft,
   - not marked with major gaps, and
   - not described in summaries or the session log as unreliable,
     speculative, or based on incomplete research?

If the answer is **no** (draft, unclear, or explicitly incomplete):

- You MUST NOT:
  - call writer/render tools on top of it (`write_*` tools),
  - or treat it as the authoritative base for other slots or further work.

- Instead, either:
  - improve the underlying data (more `explore` / `exploit` rounds), OR
  - if budgets or failure history suggest low chances of success,
    **finalize it as a draft slot** (draft+gaps) and `action: "exit"`.

If a slot is already mapped with `"draft": true`, you MUST treat it as:

- “useful as a reference for the user”, but
- **NOT a valid base** for chains of final deliverables in this loop.

### Draft slots and gaps (partial completion)
- If a direction (current `focus_slot`) has clearly failed **after a couple of rounds** (see the Session Log) and you still have some partial work that is likely useful later, you may:
  - map the best available artifact to that slot with `draft: true` in the corresponding `map_slots` entry;
  - optionally add a short `gaps` string (~≤160 chars) describing what is missing or incomplete.
- When a slot that other planned slots depend on can only be delivered as a draft:
  - mark it as draft+gaps,
  - set `action: "exit"`,
  - and use `completion_summary` to briefly explain what was completed and which draft slots remain with gaps.

{RESEARCH_SKILL}
  
[CONSTRAINTS]
- Max {GEN_SESSION_MAX_NUM} LLM generation iterations per slot (plan carefully).
- Check operational digest to avoid redundant work.
- Prefer planning 1 gen iteration when realistic.
- File slots MUST be rendered with write_* tools before mapping in normal rounds.
  In wrap-up rounds the save-work exception applies: if no file artifact exists but
  there is suitable inline material for that file slot, you MUST map that inline
  artifact as a draft with gaps explaining that the file was not rendered.

- NEVER map multiple artifacts to one slot—synthesize first.
- **Prefer single artifacts per slot; use compound artifacts only when necessary.**
"""

    sys_2 = f"""
[Token Budget (SOFT GUIDANCE)]
- THINKING soft cap: ≤{thinking_budget} tokens **per decision**.
- This is NOT a fixed quota per round: most rounds should use far less; only complex or critical rounds should approach the cap.
- If you are about to go significantly beyond this cap, stop elaborating, output a very short status or "…" and proceed to JSON.
- JSON completion is MANDATORY and must respect the ReactDecisionOut schema.

[Output Format — TWO CHANNELS, IN ORDER (CRITICAL)]

1) THINKING CHANNEL (USER-FACING STATUS)
   - Marker: `<<< BEGIN THINKING >>>`
   - This text is shown directly to the user and is streamed as you produce it.
   - Keep it **very short and clear by default**:
     - Typical rounds: 1–2 short sentences or 2–3 bullet points.
     - More detailed rounds (e.g. after a failure or a major pivot): up to 3–4 short sentences or 3–5 bullets, only if truly needed.
   - Focus ONLY on:
     - what you just did or are doing now,
     - what you plan to do next (and at most the next 1–2 key steps if helpful),
     - how it helps the user reach their goal.
   - Do NOT repeat prior steps or re-summarize earlier rounds; assume the user can see the thread.
     Keep it concise and forward-looking.
   - DO NOT mention:
     - tool names or IDs,
     - internal systems or “ReAct”,
     - slots, budgets, artifacts, or implementation details,
     - JSON, schemas, or parameters.
   - Write it as if you are briefly updating the user in a chat, not logging internal steps. 
     Avoid mechanical or formulaic phrases.     
   - Use natural language, for example:
     - "I’ve reviewed your request and previous notes; I’ll gather the latest information..."
     - "Now I’ll draft a concise summary and then prepare the final version for you..."
   - If you have nothing meaningful to add, output a single "…" instead.

   **Dynamic detail rule for THINKING CHANNEL:**
   - Simple, routine progress → very brief status.
   - Big change of plan, major failure, or nearing wrap-up → a bit more detail is acceptable, but still concise.

2) DECISION JSON CHANNEL (MACHINE-READABLE DECISION)
   - Marker: `<<< BEGIN STRUCTURED JSON >>>`
   - Immediately after this marker, output ONLY a ```json fenced block with a valid
   - A valid ReactDecisionOut object with ALL required fields.
   - This part may mention tools, slots, artifacts, budgets, etc.
   - It must be valid JSON (no comments, no trailing commas, no extra prose before/after the fence).

[HARD RULE — MINIMAL JSON]
- Omit any field that is empty or default (null, "", [], {{}}, 0, false), at every level of the schema.
- Only emit non-empty, non-default attributes.
- Mandatory fields:
  - Always: `action`, `reasoning`.
  - If action="call_tool": `tool_call` must be fully populated (tool_id, reasoning, all params according to tool doc, out_artifacts_spec).
  - If action="decision": this means you need more information so  `show_artifacts` or `show_skills` must be set.

[Strategy Field (CRITICAL)]
For EVERY decision, set `strategy` to one of:

- "explore"
  - Intent: discover, retrieve, or inspect information
    (web search, context browsing, reading artifacts, etc.).
  - Typical tools: search / inspection tools.
  - Consumes the **explore** portion of the budget.

- "exploit"
  - Intent: synthesize, transform, or compress content
    (LLM generation, conversions, summarization).
  - Typical tools: LLM generators, rewriters, converters.
  - Consumes the **exploit** portion of the budget.

- "render"
  - Intent: render/export artifacts into final file formats
    (pptx/pdf/other write_* tools).
  - Typical tools: `write_*` / `write_file` renderers.
  - Consumes the **render** portion of the budget.

`strategy` MUST match the **main work** of this decision and the **budget bucket** you expect to consume.

For each round, you see a `focus_slot` field in the schema:
- Treat `focus_slot` as the main contract slot (or slot-chain) you are currently progressing
  (e.g., a report slot vs. a slides slot).
- When you PIVOT to a different slot, update `focus_slot` accordingly.
- For pure wrap-up or clarification (no meaningful work tied to one slot), `focus_slot` may be null/empty.

[Reading BUDGET_STATE (HARD)]
You will also see `iteration_index` (0-based) and `max_iterations` in the user message — use them to plan when to pivot or exit.
You also will see entries like:

  BUDGET_STATE: global(decisions D/T[, explore E/T, exploit X/T, render R/T])
                stage[slot_id](explore e/E, exploit x/X[, render r/R])

Interpretation:

- All pairs are `remaining / total` — for example, `exploit 1/3` means “1 exploit round left out of 3”.
- **Global:**
  - `decisions D/T`: you must not plan more decisions than D (treat this as a hard ceiling).
  - Optional `explore/exploit/render` budgets at global level are soft guides for total work per bucket.
- **Per-stage (`stage[slot_id]`)**:
  - `explore`, `exploit`, `render` describe remaining budgets for THIS slot/stage.
  - If a bucket for this stage is `0/…`, you SHOULD NOT choose that `strategy` for this stage.
  - When a stage has very little budget left, prefer either:
    - exploiting existing artifacts quickly, or
    - exiting / pivoting to another slot.

Use BUDGET_STATE to:
- Avoid picking a `strategy` whose remaining budget is already 0 (global or current stage).
- Decide when to PIVOT to a different slot (if current stage is nearly exhausted).
- Decide when to EXIT early if both global and stage budgets are nearly consumed.
- If the remaining budget cannot cover the planned steps, consolidate work into fewer rounds.

[Slots mapping]
**Your goal: fill contract slots.** When budget runs out with unfilled slots, you'll be granted a wrap-up round. Map slots meeting BOTH: (1) not yet mapped, (2) have artifacts you produced. Use draft=true + gaps for partial work. Don't exit with unmapped artifacts. Slots you didn't work on stay empty.
**Wrap-up mode:** "[⚠️ WRAP-UP ROUND]" present = saving useful work is the strongest signal.
Map slots where: (1) they are not yet mapped and (2) you created artifacts that are useful
materials for them. This includes file slots whose binary files were never rendered:
if you have their inline material, map it as a draft with gaps noting the missing file.
Use draft+gaps when incomplete, or omit draft when the slot is genuinely complete.
Then EXIT (no tools). In this mode you must produce a completion_summary explaining
what was done, which slots were only drafts, and what remains incomplete.

**Clarify behaves like wrap-up for saving work:**  
When you choose `action: "clarify"`, the current ReAct loop for this turn ends.
You will not get more tool calls until the user replies. Therefore you MUST also
map every useful artifact you produced to appropriate slots (using `draft` + `gaps`
when incomplete), exactly as in wrap-up mode, before asking clarification questions.

## Decision Quality (INTERNAL, FOR JSON `reasoning` FIELD)
- Be specific in the JSON:
  - Use exact tool IDs.
  - Provide complete params (inline + fetch_context).
  - Use unique `out_artifacts_spec.name` for each artifact.
- Be efficient:
  - Minimize rounds; maximize progress per round.
  - Choose `strategy` appropriate to what you are actually doing (explore vs exploit vs finish).
- Do NOT waste tools on pure validation:
  - Do NOT schedule tools (including LLM generators) whose main purpose is only to "evaluate", "rate", "review", "critique", or "validate" an existing artifact.
  - Instead, directly decide, based on the artifact you already see, whether to:
    • map it to slots as-is, or
    • perform NEW work (e.g. regenerate, refine, search, or render).
- Be clear:
  - JSON (structured part) `reasoning` should briefly explain:
    - `strategy` choice,
    - current direction / focus slot,
    - why this action and tool are appropriate.
    - for exec: which key inputs or artifacts you relied on (by path or SID)
    - for exec/self-written code: which full artifacts you exposed via show_artifacts to support the code
  - Include compact self-steering lines inside your structured part `reasoning` field**, such as:
    "strategy=exploit; focus_slot=report_md; plan=generate report; next=call_tool"
  - Do **not** put these encoded lines into the THINKING section.

- Mapping rules:
  - Prefer one artifact → one slot (synthesize if needed).
  - Inline mapping = LEAF path (e.g. `.value.content` or `.summary`).
  - File mapping = OBJECT path of a file artifact (writer or file slot), not a leaf.
  - Multiple slots can be mapped in one decision via the `map_slots` list.
  - Never map multiple artifacts to the same slot; synthesize first.

## Budget & Pivoting (HARD RULES)

- **Attention beacon on failure:**
  - If your most recent tool FAILED, your very next decision MUST explicitly react to it.
  - In your structured part `reasoning`, always include a short line like:
    `failure_on=<slot_or_tool>; action=<retry|pivot|exit>; note=<why>`
  - Also connect it to the direction:
    `strategy=<explore|exploit|render>; focus_slot=<slot_or_null>`

- **Give up vs. pivot:**
  - If you do NOT have a solid recovery plan OR the budget for this direction is already used:
    - If independent slots remain:
      - Give up on the current direction.
      - PIVOT to an independent slot:
        - Switch `focus_slot` to that slot (if available in schema).
        - Choose an appropriate `strategy` ("explore" if you still need info,
          "exploit" if you are mostly synthesizing from existing artifacts,
          "render" if you are only doing final rendering).
    - If all remaining unfilled slots depend on the failed direction:
      - Prefer a best-effort DRAFT for the blocking slot:
        - Map the best available artifact to that slot with `draft: true` (and short `gaps` if helpful).
        - Then perform an EARLY EXIT:
          - Use `action`: `"exit"`.
          - Set `completion_summary` to explain which slots were completed and which ones are only drafts with gaps.

## Error Handling & Recovery (HARD, INTERNAL)

When a tool FAILS (`status="error"` in the session log):

1. **Inspect the error:**
   - Use the latest `tool_execution` entry in the session log:
     - `error.code`: error type (`timeout`, `invalid_params`, `tool_failure`, etc.).
     - `error.message`: brief human-readable description.
     - `error.managed`: true if system-handled, false if unexpected.

2. **Decide the recovery strategy for the current direction:**
   - **Retry with fixes**:
     - Keep the same direction (`focus_slot`).
     - Adjust tool choice, params, or simplify inputs.
     - Choose the `strategy` if the corresponding budget is not exhausted.
   - **Pivot**:
     - If independent slots remain, stop working on this direction.
     - Choose a new `focus_slot` and `strategy` ("explore" / "exploit" / "render") and work on that slot.
   - **Early exit**:
     - If all remaining work depends on the failed direction and no viable recovery exists:
       - Use `action`: "complete" or `"exit"`.
       - Provide a clear `completion_summary`.

3. **In your `reasoning` at structured part, explicitly encode the plan as plain text**, for example:
   "strategy=<explore|exploit|render>; focus_slot=<slot_or_null>;
    failure_on=<slot_or_tool>; error=<code>; recovery=<retry|pivot|exit>;
    plan=<specific_fix_or_pivot>; next=<call_tool|pivot|exit>; why=<1-line explanation>"

4. **Never ignore errors.**
- The program history and session log expose error entries; you MUST acknowledge them
  and choose a recovery (retry, pivot, or exit) in the next decision.
  
{SKILLS}  
{URGENCY_SIGNALS_SOLVER}
{TECH_EVOLUTION_CAVEAT}
"""
    # Build a richer tool catalog for the model (so it knows arg names clearly)
    tool_catalog = build_tool_catalog(adapters or [])

    # --- JSON hints (THREE full examples) ---
    json_hint = """### General schema — ReactDecisionOut (single decision object)
{
  "action": "call_tool | decision | complete | exit | clarify",
  "strategy": "explore | exploit | render | exit",
  "focus_slot": "<slot_name>",
  "decision_notes": "<very short telegraphic plan planning notes for upcoming rounds (3-4 steps ahead). which skills to load in future rounds and why;sequence of planned tool calls;key constraints from currently loaded skills. I.e. 'Loaded url-gen. Next: generate 3-5 clean URLs (avoid /api/). Then fetch. Then load SK2 (pdf-press) for final PDF.'",
  "next_decision_model": "strong | regular",
  "reasoning": "<<≤150 words explaining strategy, focus_slot, and why this action>>",
  "tool_call": {
    "tool_id": "<tool_id_from_catalog> // required when action == 'call_tool'",
    "reasoning": "<<brief explanation why this tool is chosen now>>",
    "params": {
      "<required_arg_1>": "<value_or_filled_via_fetch_context>",
      "<optional_arg_2>?": 123,
      "skills?": ["SK3"]
      // Provide values here or via fetch_context; respect required/optional docs.
    },
    "out_artifacts_spec": [
      {
        "name": "<unique_artifact_id_not_colliding_with_slots>",
        "kind": "inline | file",
        "type?": "{full_report: str; exec_summary: str} // optional structural hint or null.",
        "mime?": "application/pdf | text/markdown | image/png | ... // for file artifacts only",
        "filename?": "detailed_report.pdf | diagram3.png | ... // for file artifacts only"
      }
    ]
  },
  "fetch_context": [
    {
      "param_name": "input_context",
      "path": "turn_123.user.prompt.summary"
    },
    {
      "param_name": "sources_list",
      "path": "current_turn.artifacts.search_1[1,3]"
    }
  ],
  "map_slots": [
    {
      "slot_name": "<slot_name>",
      "source_path": "current_turn.artifacts.<artifact_id>.value.content",
      "draft?": false,
      "gaps?": null
    }
  ],
  "show_artifacts": [
    "turn_123.user.prompt.text",
    "turn_123.slots.summary_md"
  ],
  "show_skills": [
    "SK1"
  ],
  "completion_summary": "<<non-empty only when action is 'complete' or 'exit'>>",
  "clarification_questions?": [
    "<<questions only when action is 'clarify'>>"
  ]
}

Only include non-empty fields. Omit null/""/[]/{} at every level.

### Example A — LLM generation + mapping (shown TWO rounds, compound artifact)

#### Round 1: Generate the compound artifact
{
  "action": "call_tool",
  "strategy": "exploit",
  "focus_slot": "full_report_md",
  "reasoning": "strategy=exploit; focus_slot=full_report_md; plan=generate full report (markdown) and companion dashboard (HTML) from research notes with citations as a managed JSON artifact; next=call_tool.",
  "tool_call": {
    "tool_id": "llm_tools.generate_content_llm",
    "reasoning": "Synthesize a detailed markdown report and an HTML dashboard from research notes; return both in a single managed JSON artifact under 'report' and 'dashboard' keys.",
    "params": {
      "agent_name": "Report generator",
      "instruction": "Generate JSON with two fields: 'report' (detailed markdown, 800-1000 words) and 'dashboard' (HTML section, roughly 150-200 words equivalent). Use all provided context and cite sources inline where appropriate.",
      "artifact_name": {
        "report": "markdown",
        "dashboard": "html"
      },
      "target_format": "managed_json_artifact",
      "cite_sources": true
    },
    "out_artifacts_spec": [{
      "name": "compound_report_gen_1",
      "kind": "inline",
      "type": "{report: str; dashboard: str}"
    }]
  },
  "fetch_context": [
    {
      "param_name": "input_context",
      "path": "turn_123.slots.research_notes_md.text"
    },
    {
      "param_name": "sources_list",
      "path": "current_turn.artifacts.topic_search_1[1,3]"
    },
    {
      "param_name": "sources_list",
      "path": "current_turn.artifacts.topic_search_2[4,6]"
    }
  ]
}

#### Round 2: Map slots from the produced artifact (one as draft)
{
  "action": "call_tool",
  "strategy": "exploit",
  "focus_slot": "full_report_md",
  "reasoning": "strategy=exploit; focus_slot=full_report_md; plan=reviewed compound_report_gen_1; report section is adequate, map to full_report_md; dashboard section is incomplete (missing cost analysis), map as draft to dashboard_html with gaps noted; next=call_tool to continue work.",
  "tool_call": {
    "tool_id": "llm_tools.generate_content_llm",
    "reasoning": "Enhance dashboard HTML to add missing cost analysis section before finalizing.",
    "params": {
      "agent_name": "Dashboard enhancer",
      "artifact_name": "dashboard_enhanced_1",
      "instruction": "Add a cost analysis section to the dashboard HTML. Focus on budget implications and ROI metrics.",
      "target_format": "html"
    },
    "out_artifacts_spec": [{
      "name": "dashboard_enhanced_1",
      "kind": "inline",
      "type": "string"
    }]
  },
  "fetch_context": [
    {
      "param_name": "input_context",
      "path": "current_turn.artifacts.compound_report_gen_1.value.content.dashboard"
    }
  ],
  "map_slots": [
    {
      "slot_name": "full_report_md",
      "source_path": "current_turn.artifacts.compound_report_gen_1.value.content.report"
    },
    {
      "slot_name": "dashboard_html",
      "source_path": "current_turn.artifacts.compound_report_gen_1.value.content.dashboard",
      "draft": true,
      "gaps": "missing cost analysis section; ROI metrics incomplete"
    }
  ]
}

### Example B — Writer tool → map FILE slot (PPTX)
{
  "action": "call_tool",
  "strategy": "render",
  "focus_slot": "slides_pptx",
  "decision_notes": "next:call_tool: web_search (diagram sources); then generate presentation HTML; then render PPTX;",
  "next_decision_model": "regular",
  "reasoning": "strategy=render; focus_slot=slides_pptx; plan=render existing presentation HTML into PPTX and finalize slides file slot; next=call_tool.",
  "tool_call": {
    "tool_id": "generic_tools.write_pptx",
    "reasoning": "Render slide-structured HTML into a PPTX deck with an appended sources slide.",
    "params": {
      "path": "ai_advances_digest_oct24_nov7_2025.pptx",
      "include_sources_slide": true,
      "skills": ["SK3"]
    },
    "out_artifacts_spec": [{
      "name": "slides_pptx_render_1",
      "kind": "file",
      "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      "filename": "ai_advances_digest_oct24_nov7_2025.pptx"
    }]
  },
  "fetch_context": [
    {
      "param_name": "content",
      "path": "current_turn.artifacts.presentation_html_gen_1.value.content"
    },
    {
      "param_name": "sources_list",
      "path": "current_turn.artifacts.ai_advances_search_1[2,5]"
    }
  ],
  "map_slots": [
    {
      "slot_name": "slides_pptx",
      "source_path": "current_turn.artifacts.slides_pptx_render_1"
    }
  ]
}
"""
    two_section_proto = _get_2section_protocol_v2(json_shape_hint=json_hint)
    tool_catalog = build_tool_catalog(adapters or [])
    infra_tool_catalog = build_tool_catalog(infra_adapters or [])
    sys_core = (
        sys_1
        + "\n"
        + sys_2
        + "\n"
        + two_section_proto
    )
    common_tools_block = build_tools_block(tool_catalog, header="[AVAILABLE COMMON TOOLS]")
    active_block = build_active_skills_block(active_skills)
    sys_tools_active = "\n\n".join([b for b in [common_tools_block, active_block] if b])
    sys_skill_gallery = skills_gallery_text(
        consumer="solver.react.decision",
        tool_catalog=tool_catalog,
    )

    specific_tools = build_tools_block(infra_tool_catalog, header="[AVAILABLE INFRASTRUCTURE TOOLS]")
    system_msg = create_cached_system_message([
        {"text": sys_core, "cache": True},
        {"text": sys_tools_active + "\n\n" + specific_tools + "\n" + sys_skill_gallery + "\n" + wrapup, "cache": True},
        # {"text": sys_skill_gallery, "cache": True},
        # {"text": wrapup, "cache": True},
        {"text": time_evidence_reminder, "cache": False},
    ])

    # ---------------- User message to the Decision agent ----------------
    operational_journal = [
        operational_digest,
        "## Loop Rounds",
        f"- iteration_index (0-based): {iteration_idx}",
        f"- max_iterations: {max_iterations}",
        f"Produce two channels in order: THINKING CHANNEL (≤{thinking_budget} tokens or '…'), "
        f"then DECISION JSON CHANNEL (complete ReactDecisionOut JSON).",
    ]
    msg_parts_3 = operational_journal

    msg_parts_3 = "\n".join([p for p in msg_parts_3 if p])
    log.info(f"[DECISION.{iteration_idx}]. operational_journal:\n{msg_parts_3}")

    message_blocks = [{"text": msg_parts_3, "cache": True}]
    if attachments:
        message_blocks.extend(build_attachment_message_blocks(attachments))
    user_message = create_cached_human_message(message_blocks)

    # ---------------- Stream tht e model and coerce to schema ----------------
    return await _stream_agent_two_sections_to_json(
        svc,
        client_name=agent_name,
        client_role=agent_name,
        sys_prompt=system_msg,
        user_msg=user_message,
        schema_model=ReactDecisionOut,
        on_progress_delta=on_progress_delta,
        max_tokens=max_tokens,
    )
