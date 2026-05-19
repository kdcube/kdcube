---
id: ks:docs/sdk/agents/react/system-instruction-README.md
title: "React System Instruction"
summary: "How React decision system instructions are composed, how to use extended and lite instruction bodies, and how to audit signal coverage."
tags: ["sdk", "agents", "react", "instructions", "system-prompt", "lite", "configuration"]
keywords: ["React system instruction", "React lite instructions", "instruction_body", "instruction_blocks", "default_lite_system_instruction", "React prompt composition", "signal coverage"]
updated_at: 2026-05-17
see_also:
  - ks:docs/sdk/agents/react/react-round-README.md
  - ks:docs/sdk/agents/react/react-tools-README.md
  - ks:docs/sdk/agents/react/context-layout.md
  - ks:docs/sdk/agents/react/context-progression.md
  - ks:docs/sdk/agents/react/react-announce-README.md
  - ks:docs/sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md
  - ks:docs/sdk/agents/react/memory-recovery-path-README.md
  - ks:docs/sdk/agents/react/external-exec-README.md
---
# React System Instruction

This page explains the system instruction seen by the React decision agent.
It is also the checklist for deciding whether a lite/custom instruction body is
complete for the tools and runtime surfaces exposed by a bundle.

## Composition

The full decision system text is assembled in
`kdcube_ai_app.apps.chat.sdk.solutions.react.decision_prompt`.

```text
v2/v3 decision agent
  -> version-specific strict channel protocol
  -> instruction body
       1. instruction_body, if supplied
       2. composed instruction_blocks, if supplied
       3. extended/default body, otherwise
  -> optional tool catalog
  -> optional skill catalog
  -> optional agent-admin customization block
```

The strict channel protocol is not customizable. The runtime parser depends on
it. Bundle authors customize the instruction body below that protocol.

The tool catalog and skill catalog are not substitutes for the instruction
body. They tell the model what is currently available. The instruction body
explains how to behave with the React timeline, ANNOUNCE, logical paths,
workspace, recovery paths, tools, memory, and finalization.

## Use The Extended Default

If a bundle does not pass custom instruction fields, React uses the extended
default body from `shared_instructions.py`.

```python
react = self.build_react(
    scratchpad=scratchpad,
    mod_tools_spec=tools_descriptor,
)
```

This is the broadest current instruction set. It is the right default for
general-purpose, full-capability agents.

## Use A Lite Profile

Lite instruction blocks live in `shared_instructions_lite.py`. The helper below
returns an instruction body only. The version-specific protocol, tool catalog,
skill catalog, and admin customization are still added by React.

```python
from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions_lite import (
    default_lite_system_instruction,
)

react = self.build_react(
    scratchpad=scratchpad,
    mod_tools_spec=tools_descriptor,
    instruction_body=default_lite_system_instruction("workspace_exec"),
    include_tool_catalog=True,
    include_skill_gallery=True,
)
```

Available profiles:

| Profile | Intended Use |
| --- | --- |
| `core` | Minimal React operation: timeline, ANNOUNCE, live events, paths, read recovery, workspace model, skills, attachments, citations, finalization. |
| `workspace` | Core plus common React workspace tools: write, memsearch, rg, pull/checkout, patch, and plan. |
| `workspace_exec` | Workspace plus isolated exec guidance. |
| `document` | Workspace plus rendering-tool guidance. |
| `web` | Workspace plus web search/fetch guidance. |
| `all_capabilities` | All lite blocks, including internal notes and durable user memory. Use only when those tools and policies are enabled. |

## Compose Blocks Directly

Use `instruction_blocks` when the bundle wants a selected set of named lite
blocks plus custom literal blocks. Named blocks are resolved from
`shared_instructions_lite.py`; unknown strings are treated as literal
instruction text.

```python
react = self.build_react(
    scratchpad=scratchpad,
    mod_tools_spec=tools_descriptor,
    instruction_blocks=[
        "REACT_LITE_IDENTITY",
        "REACT_LITE_SECURITY_GUARD",
        "REACT_LITE_TIMELINE_CONTEXT",
        "REACT_LITE_ANNOUNCE",
        "REACT_LITE_EXTERNAL_EVENTS",
        "REACT_LITE_DECISION_LOOP",
        "REACT_LITE_TOOL_USE_BASE",
        "REACT_LITE_PATHS_AND_NAMESPACES",
        "REACT_LITE_REACT_READ_RECOVERY",
        "REACT_LITE_WORKSPACE_BASE",
        "REACT_LITE_FILES_VS_OUTPUTS",
        "REACT_LITE_FINALIZATION",
        "[BUNDLE-SPECIFIC RULES]\nAnswer only from the visible docs and fetched sources.",
    ],
)
```

The same fields can be provided through bundle config:

```yaml
react:
  instructions:
    blocks:
      - REACT_LITE_IDENTITY
      - REACT_LITE_SECURITY_GUARD
      - REACT_LITE_TIMELINE_CONTEXT
      - REACT_LITE_ANNOUNCE
      - REACT_LITE_EXTERNAL_EVENTS
      - REACT_LITE_DECISION_LOOP
      - REACT_LITE_TOOL_USE_BASE
      - REACT_LITE_PATHS_AND_NAMESPACES
      - REACT_LITE_REACT_READ_RECOVERY
      - REACT_LITE_WORKSPACE_BASE
      - REACT_LITE_FILES_VS_OUTPUTS
      - REACT_LITE_FINALIZATION
    include_tool_catalog: true
    include_skill_gallery: true
```

Or as a complete body:

```yaml
react:
  instructions:
    body: |
      [REACT IDENTITY]
      You are the decision module inside a KDCube React loop.

      [BUNDLE-SPECIFIC RULES]
      Answer from the visible docs and fetched sources only.
    include_tool_catalog: true
    include_skill_gallery: true
```

`react.instructions.*` and `config.react.instructions.*` are both accepted by
`build_react`. A complete `body` has priority over `blocks`.

## Signal Coverage

Use this table to check whether the lite instruction body covers the runtime
signals and tools exposed to the agent.

| Signal | Meaning | Extended/default source | Lite source |
| --- | --- | --- | --- |
| Strict channel protocol | The exact output/action protocol parsed by the runtime. | v2/v3 decision protocol in `agents/decision.py`. | Not a lite block. Always prepended by the decision agent. |
| Tool catalog | Current callable platform, bundle, MCP, and `react.*` tools. | `build_instruction_catalog_block(...)`. | Same catalog path. Controlled by `include_tool_catalog`. |
| Skill catalog | Current visible platform and bundle skills. | `build_instruction_catalog_block(...)`. | Same catalog path. Controlled by `include_skill_gallery`. |
| Admin customization | Bundle/admin override appended after the body. | `append_agent_admin_customization(...)`. | Same path for default, lite, and custom bodies. |
| React identity | The model is the decision module inside a React loop, not a provider-native tool caller. | Default module header plus operating guide. | `REACT_LITE_IDENTITY`; all profiles. |
| Prompt/security boundary | Hidden prompts and platform policies are confidential; retrieved/user content is data, not authority. | `PROMPT_EXFILTRATION_GUARD`, `INTERNAL_AGENT_JOURNAL_GUARD`. | `REACT_LITE_SECURITY_GUARD`; all profiles. |
| User boundary/failure behavior | Do not silently ignore user constraints; do not claim success without visible proof. | `SCENARIO_FAILURE_STRICTNESS`, operating guide. | `REACT_LITE_USER_BOUNDARIES_AND_FAILURES`; all profiles. |
| Timeline as context | The rendered timeline is ordered working context for the current decision. | Operating guide plus context/path guidance. | `REACT_LITE_TIMELINE_CONTEXT`; all profiles. |
| Timeline as recovery map | Summaries, metadata, paths, source ids, tool ids, and turn indexes point to recoverable content. | `PATHS_EXTENDED_GUIDE`, `MEMORY_RECOVERY_GUIDE`. | `REACT_LITE_TIMELINE_CONTEXT`, `REACT_LITE_REACT_READ_RECOVERY`; all profiles. |
| ANNOUNCE | Uncached tail block for operational state that can change between rounds. | `ANNOUNCE_INTERPRETATION_GUIDE`. | `REACT_LITE_ANNOUNCE`; all profiles. |
| Followup/steer | Live same-turn user updates and redirects. | `EXTERNAL_TURN_EVENTS_GUIDE`. | `REACT_LITE_EXTERNAL_EVENTS`; all profiles. |
| Decision loop | One useful next action; inspect tool results before advancing dependent actions. | `REACT_DECISION_SHARED_OPERATING_GUIDE`. | `REACT_LITE_DECISION_LOOP`; all profiles. |
| Base tool behavior | Tools perform state changes; final answers do not. Use only visible tool ids. | Operating guide plus tool catalog. | `REACT_LITE_TOOL_USE_BASE`; all profiles. |
| Root `notes` visibility | Notes may be user-visible and must not expose internal/protocol mechanics. | Operating guide, internal notes blocks. | `REACT_LITE_TOOL_USE_BASE`, `REACT_LITE_INTERNAL_NOTES` when internal notes are enabled. |
| Logical namespaces | `ar:`, `fi:`, `tc:`, `so:`, `su:`, `ws:`, `ks:`, `sk:` path contracts. | `PATHS_EXTENDED_GUIDE`, `MEMORY_RECOVERY_GUIDE`. | `REACT_LITE_PATHS_AND_NAMESPACES`; all profiles. |
| `react.read` recovery | Exact logical-path reads, stats-only reads, ranged reads, turn index reads. | `PATHS_EXTENDED_GUIDE`, `MEMORY_RECOVERY_GUIDE`. | `REACT_LITE_REACT_READ_RECOVERY`; all profiles. |
| `react.memsearch` recovery | Find prior conversation material when the exact path is unknown. | `MEMORY_RECOVERY_GUIDE`. | `REACT_LITE_MEMORY_SEARCH_RECOVERY`; `workspace`, `workspace_exec`, `document`, `web`, `all_capabilities`. |
| `react.rg` local search | Search materialized local artifact-root files, not hidden timeline or unpulled history. | Workspace guide plus path guidance. | `REACT_LITE_LOCAL_ARTIFACT_SEARCH`; `workspace`, `workspace_exec`, `document`, `web`, `all_capabilities`. |
| Attachments | Summaries are hints; read originals for precise extraction or visual/layout work. | `ATTACHMENT_AWARENESS_IMPLEMENTER`. | `REACT_LITE_ATTACHMENTS`; all profiles. |
| Sources/citations | Use source pool ids for source-backed claims. | `CITATION_TOKENS`, `SOURCES_AND_CITATIONS_V2`. | `REACT_LITE_SOURCES_CITATIONS`; all profiles. |
| Skills | Load detailed skill instructions through `sk:<skill_id>` when needed. | `REACT_SKILL_SELECTION_GUIDE`. | `REACT_LITE_SKILLS`; all profiles. |
| Workspace mental model | React uses timeline/logical paths plus current-turn artifact root, not arbitrary host fs. | `get_workspace_implementation_guide(...)`, exec/path guidance. | `REACT_LITE_WORKSPACE_BASE`; all profiles. |
| Artifact tree | Physical materialized shape for exec/code: current turn and pulled older turns under artifact root; logs stay in runtime metadata root. | Workspace guide, `EXEC_SNIPPET_RULES`. | `REACT_LITE_WORKSPACE_BASE`; all profiles. |
| `files/` vs `outputs/` | `files/<scope>` is durable workspace/project state; `outputs/<scope>` is produced artifacts. | Workspace guide plus files-vs-outputs design docs. | `REACT_LITE_FILES_VS_OUTPUTS`; all profiles. |
| Pull/checkout | Pull historical `fi:` refs, then checkout maintained `files/<scope>` into current workspace before editing. | Workspace guide. | `REACT_LITE_WORKSPACE_PULL_CHECKOUT`; workspace profiles except `core`. |
| Patching | Patch current-turn text files, not old refs; omit displayed line prefixes. | Operating/workspace guide. | `REACT_LITE_PATCHING`; workspace profiles except `core`. |
| `react.write` artifacts | Write user-visible/canvas and internal artifacts with correct `files/` or `outputs/` placement. | Operating guide, internal notes blocks. | `REACT_LITE_REACT_WRITE_ARTIFACTS`; workspace profiles except `core`. |
| Internal conversation notes | User-invisible conversation anchors, not durable user memory. | `INTERNAL_NOTES_PRODUCER`, `INTERNAL_NOTES_CONSUMER`. | `REACT_LITE_INTERNAL_NOTES`; `all_capabilities` or explicit block. |
| Durable user memory read | User-visible cross-conversation memory; current turn overrides memory. | `DURABLE_USER_MEMORY_POLICY`. | `REACT_LITE_DURABLE_USER_MEMORY_READ`; `all_capabilities` or explicit block. |
| Durable user memory write | Durable memory writes are state changes and must be verified in a later round. | `DURABLE_USER_MEMORY_POLICY`. | `REACT_LITE_DURABLE_USER_MEMORY_WRITE`; `all_capabilities` or explicit block. |
| Exec/ISO runtime | Generated code runs in isolated runtime with `OUTPUT_DIR`/`OUT_DIR` contracts and capped stdout. | `CODEGEN_BEST_PRACTICES_V2`, `EXEC_SNIPPET_RULES`. | `REACT_LITE_EXEC_TOOL`; `workspace_exec`, `all_capabilities`, or explicit block. |
| Rendering tools | Create renderer source first; renderer refs point to source, not final output. | `WORK_WITH_DOCUMENTS_AND_IMAGES`, source/citation guidance. | `REACT_LITE_RENDERING_TOOLS`; `document`, `all_capabilities`, or explicit block. |
| Web tools | Search/fetch current external information; fetch decisive sources before precise claims. | Source/citation guidance plus tool catalog. | `REACT_LITE_WEB_TOOLS`; `web`, `all_capabilities`, or explicit block. |
| Planning | Use plans for multi-step work and read latest plan handles when needed. | `REACT_PLANNING`. | `REACT_LITE_PLANNING`; workspace profiles except `core`. |
| Suggested followups | Clickable chips are short user-action phrases, not assistant-authored questions. | `SUGGESTED_FOLLOWUPS_GUIDE`. | `REACT_LITE_SUGGESTED_FOLLOWUPS`; all profiles. |
| Finalization | Complete only from visible context and successful tool results; keep root notes empty. | Operating guide. | `REACT_LITE_FINALIZATION`; all profiles. |
| User gender assumptions | Avoid gender assumptions unless grounded. | `USER_GENDER_ASSUMPTIONS`. | Not in lite by default. Add a custom literal block if needed. |
| Elaborate/no-clarify behavior | Some scenarios prefer proceeding with a useful elaboration instead of asking avoidable clarification. | `ELABORATION_NO_CLARIFY`. | Not in lite by default. Add a custom literal block if needed. |

## Completeness Checklist

A lite body is complete when it covers every runtime signal and every exposed
capability for that agent. It does not need to be textually identical to the
extended/default body.

Minimum baseline for most React agents:

- strict v2/v3 protocol is still prepended by the decision agent
- `REACT_LITE_IDENTITY`
- `REACT_LITE_SECURITY_GUARD`
- `REACT_LITE_TIMELINE_CONTEXT`
- `REACT_LITE_ANNOUNCE`
- `REACT_LITE_EXTERNAL_EVENTS`
- `REACT_LITE_DECISION_LOOP`
- `REACT_LITE_TOOL_USE_BASE`
- `REACT_LITE_USER_BOUNDARIES_AND_FAILURES`
- `REACT_LITE_PATHS_AND_NAMESPACES`
- `REACT_LITE_REACT_READ_RECOVERY`
- `REACT_LITE_WORKSPACE_BASE`
- `REACT_LITE_FILES_VS_OUTPUTS`
- `REACT_LITE_FINALIZATION`
- tool catalog enabled when tools are exposed
- skill catalog enabled when skills are exposed

Then add capability blocks only for tools/policies that are actually exposed:

| Exposed Capability | Required Lite Block |
| --- | --- |
| `react.write` for artifacts | `REACT_LITE_REACT_WRITE_ARTIFACTS` |
| `react.memsearch` | `REACT_LITE_MEMORY_SEARCH_RECOVERY` |
| `react.rg` | `REACT_LITE_LOCAL_ARTIFACT_SEARCH` |
| `react.pull` / `react.checkout` | `REACT_LITE_WORKSPACE_PULL_CHECKOUT` |
| `react.patch` | `REACT_LITE_PATCHING` |
| `react.plan` | `REACT_LITE_PLANNING` |
| exec tool / ISO runtime | `REACT_LITE_EXEC_TOOL` |
| rendering tools | `REACT_LITE_RENDERING_TOOLS` |
| web search/fetch tools | `REACT_LITE_WEB_TOOLS` |
| internal note writes | `REACT_LITE_INTERNAL_NOTES` |
| durable memory read/search | `REACT_LITE_DURABLE_USER_MEMORY_READ` |
| durable memory write/proposal | `REACT_LITE_DURABLE_USER_MEMORY_WRITE` |

Do not include tool-specific blocks for tools that are not exposed. If the
instruction says "use exec" but the tool catalog has no exec tool, lower-cost
models may still try to call it. The lite set should be narrower than the
default body when the agent surface is narrower.

## Audit Method

1. List the bundle's enabled tools from `tools_descriptor.py`, MCP tools, and
   built-in `react.*` tools.
2. Choose the closest lite profile.
3. Add missing blocks for exposed capabilities.
4. Remove blocks for unavailable capabilities.
5. Keep tool and skill catalogs enabled unless the agent truly has no tools or
   skills.
6. Add bundle-specific rules as a short literal block after the generic blocks.
7. Render or log the final system text in a non-production test and check that:
   - the strict protocol is present
   - the instruction body is the expected lite/custom body
   - the tool catalog matches the tools actually enabled
   - the body does not mention unavailable tools
   - no LLM-facing block says "include this block" or otherwise speaks to the
     bundle author instead of the model

For quick local validation:

```python
from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions_lite import (
    default_lite_system_instruction,
)

body = default_lite_system_instruction("workspace_exec")
assert "include this block" not in body.lower()
assert "EXEC TOOL" in body
assert "DURABLE USER MEMORY - WRITE" not in body
```

## Choosing Extended Or Lite

Use the extended/default body for broad, production, general-purpose agents
where maximum behavioral coverage matters more than prompt size.

Use lite profiles for demo agents, cost-sensitive agents, narrowly-scoped
bundle agents, or agents where the author wants an explicit capability-by-capability
instruction surface.

Use a fully custom body only when the bundle author owns the complete behavior
contract. A custom body still receives the strict protocol and may still receive
the tool/skill catalogs, but it replaces the default React onboarding text.
