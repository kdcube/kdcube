---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/system-instruction-README.md
title: "React System Instruction"
summary: "How React decision system instructions are composed, how to use extended and lite instruction bodies, and how to audit signal coverage."
tags: ["sdk", "agents", "react", "instructions", "system-prompt", "lite", "configuration"]
keywords: ["React system instruction", "React lite instructions", "instruction_body", "instruction_blocks", "default_lite_system_instruction", "React prompt composition", "signal coverage"]
updated_at: 2026-07-22
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-caching-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-round-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-layout.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-progression.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/micro-agents-and-cache-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-announce-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/memory-recovery-path-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/external-exec-README.md
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

### Cache Consequence

The composed system instruction is part of the exact model-input prefix. It is
not a timeline block, but it still affects the prompt prefix that React sends
before the rendered timeline.

```
[strict protocol]
[instruction body]
[tool/skill catalog]
[admin or runtime customization]
[rendered timeline prefix]
[ANNOUNCE / current tail]
```

Changing any bytes before the rendered timeline creates a different downstream
prompt prefix. In the current ReAct decision path, the tool catalog and skill
catalog are text rendered in this system instruction, usually near the bottom.
Changing them changes the system prefix before the rendered timeline. A
per-user customization suffix therefore prevents cross-user cache sharing for
that agent after that suffix. A subagent with a different instruction has its
own cache story. Put volatile current state in ANNOUNCE or another tail block
instead of appending it to the instruction body.

React maps this prefix layout to explicit cache controls for Anthropic/Claude.
For other providers, the same prompt layout still controls token shape and
semantic stability, but React does not currently assume equivalent
provider-side cache-control behavior.

The important boundary is the first changed segment:

```
[strict protocol]                         stable for all compatible agents
[default React instruction]               stable for all compatible agents
[shared bundle/domain instruction]        stable for this bundle/config

--- first variable segment below this line limits cache sharing ---

[per-user instruction suffix]             differs by user
[selected tool catalog]                   differs if user/runtime changes tools
[selected skill catalog]                  differs if user/runtime changes skills
[rendered timeline prefix]                downstream of the variable segments
[ANNOUNCE / current tail]                 intentionally uncached
```

If the tool catalog or skill catalog is rendered inside the instruction
envelope, it is part of the cache prefix. Letting a user select tools or skills
therefore partitions the cache by that exact selection. Changing the selection
between turns invalidates the downstream cache for that user. Changing it
between rounds is worse: the same turn can no longer reuse cache points after
the changed catalog segment.

Keep the instruction envelope stable when cache sharing matters. Put changing
current state in ANNOUNCE. Only put tool/skill catalogs in the instruction
envelope when the model really needs that catalog for the current call, and
expect the cache to be keyed by the exact catalog text.

Be explicit about the reuse scope:

- Same user reuse: later turns, later rounds, or repeated calls to the same
  configured subagent can reuse cache only while that user's instruction/catalog
  prefix stays identical.
- Cross-user reuse: multiple users can share the common prefix only before any
  per-user instruction, per-user data, or user-selected catalog segment. This is
  valuable on Anthropic because traffic from multiple users is more likely to
  keep a short-lived cache entry hot.
- Subagent reuse: a subagent configured dynamically by the main agent is usually
  a cache story inside that user's work. A ready-made/static subagent can share
  its common prefix across users until the first user-specific segment.

## Use The Extended Default

If a bundle does not pass custom instruction fields, React uses the extended
default body from `shared_instructions.py`.

```python
tool_config = agent_tool_config_from_bundle_props(
    self.bundle_props,
    "main",
    bundle_root=BUNDLE_ROOT,
)
react = self.build_react(
    scratchpad=scratchpad,
    mod_tools_spec=tool_config.tool_specs,
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

tool_config = agent_tool_config_from_bundle_props(
    self.bundle_props,
    "main",
    bundle_root=BUNDLE_ROOT,
)
react = self.build_react(
    scratchpad=scratchpad,
    mod_tools_spec=tool_config.tool_specs,
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

### Exec Artifact Output Paths

Every built-in instruction tier that exposes `exec_tools.execute_code_python`
teaches the same two-part path contract:

1. The action's `contract[].filepath` is an `OUTPUT_DIR`-relative string such
   as `turn_<current>/files/report/report.xlsx`. Keep this `artifact_rel`
   value relative.
2. Generated code resolves that value under the runtime artifact root, creates
   its parent, and passes the resolved path to the writer:

```python
from pathlib import Path

artifact_rel = "turn_<current>/files/report/report.xlsx"
artifact_path = Path(OUTPUT_DIR) / artifact_rel
artifact_path.parent.mkdir(parents=True, exist_ok=True)
wb.save(artifact_path)
```

The contract `filepath` must equal `artifact_rel` byte-for-byte. It is not the
path passed directly to `open()`, `wb.save()`, or another writer. Writing
`artifact_rel` directly would create a bare `turn_<current>/...` tree relative
to the process working directory, where the artifact collector will not find
the file.

This rule lives in the full body's `CODEGEN_BEST_PRACTICES_V2` and
`EXEC_SNIPPET_RULES`, the moderate `REACT_LITE_EXEC_TOOL`, and the extra-lite
`REACT_XLITE_EXEC` block. The exec tool's parameter description repeats it, so
the rule also survives the compact tool catalog. Named Lite and Extra-lite
profiles without an effective exec tool intentionally omit their exec blocks.
The legacy Full body is monolithic and retains its exec guidance. A fully
custom body that hides the tool catalog must carry its own complete exec
contract.

Exec code itself is preserved as a Python module body and evaluated with
top-level `await` enabled. Do not generate an event-loop runner or a separate
`main()` entrypoint.

## The `blocks` Vocabulary

A `blocks` list (in `instruction_blocks` or a profile's `blocks`) is composed
into the instruction body by
`kdcube_ai_app.apps.chat.sdk.solutions.agentic_instructions.compose_instruction_body`
— an agent-neutral composer, reached from ReAct through
`decision_prompt.normalize_instruction_blocks`. **Each item is resolved
independently and all are joined with a blank line in the exact order listed.**
One vocabulary spans the three tiers:

| Token | Resolves to | Notes |
| --- | --- | --- |
| `full` | the agent's complete default body | Monolithic (not capability-scoped). Any suffix — `full:workspace_exec` — is ignored. A profile with no `blocks` also uses the full default. |
| `lite:<profile>` | a whole **moderate** profile body | Profiles below. `blocks: [lite:workspace_exec]` == listing that profile's `REACT_LITE_*` blocks. |
| `xlite:<profile>` | a whole **extra-lite** profile body | Same profile names, for serving-constrained models. |
| `REACT_LITE_*` | a single moderate block | From `shared_instructions_lite.py`. |
| `REACT_XLITE_*` | a single extra-lite block | From `instructions_extra_lite.py`. |
| anything else | literal instruction text | Used verbatim — e.g. a `[BUNDLE-SPECIFIC RULES]` fragment. |

Profiles (for `lite:` and `xlite:`): `core`, `workspace`, `workspace_exec`,
`document`, `web`, `all_capabilities`.

The runtime protocol is always prepended and the tool/skill catalogs appended by
the decision agent; `blocks` composes only the body between them. Full is not
name-addressable block-by-block; it is the default, the `full` token, or a
literal `body`.

Named Lite and Extra Lite capability blocks are intersected with the effective
per-turn tool selection. For example, exec, web, and rendering guidance is
removed when those tools are disabled or absent. App-specific additions must
follow the same rule; Workspace omits its canvas and memory guidance when `cnv`
or `mem` is disabled.

A whole profile in one token — the common case:

```yaml
react:
  instructions:
    blocks:
      - lite:all_capabilities        # the whole moderate set
    include_tool_catalog: true
    include_skill_gallery: true
```

A selected set of blocks plus a custom fragment:

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
      - REACT_LITE_PROJECTS_AND_FILES
      - REACT_LITE_FINALIZATION
      - "[BUNDLE-SPECIFIC RULES]\nAnswer only from the visible docs and fetched sources."
    include_tool_catalog: true
    include_skill_gallery: true
```

A user-pickable set of sizes via instruction profiles (the picker shows one per
conversation, next to the model picker):

```yaml
react:
  default_agent:
    instruction_profiles:
      default: full
      options:
        - id: full
          label: Full
          description: The complete battle-proven instruction set.
        - id: lite
          label: Lite (moderate)
          blocks: [lite:all_capabilities]
        - id: extra-lite
          label: Extra Lite (local models)
          multi_action_mode: "off"
          include_skill_gallery: false
          tool_catalog_detail: compact
          blocks: [xlite:workspace_exec]
```

Profile runtime options apply together with the selected body: `multi_action_mode`
can force the one-action protocol, `include_skill_gallery` and
`include_tool_catalog` control the appended catalogs, and
`tool_catalog_detail: compact` keeps exact effective tool IDs and parameter
names while omitting long examples and repeated prose. The reference Extra Lite
profile uses single-action mode, no skill gallery, and the compact tool catalog;
the compact catalog also omits scheduling traits used only by multi-action mode.

Or a complete hand-written body (highest priority, over `blocks`):

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
`build_react`; an instruction profile the user picked overrides the agent-level
`instructions`. A complete `body` has priority over `blocks`.

The equivalent Python call:

```python
react = self.build_react(
    scratchpad=scratchpad,
    mod_tools_spec=tool_config.tool_specs,
    instruction_blocks=["lite:workspace_exec"],   # or ["full"], or a block list
    include_tool_catalog=True,
    include_skill_gallery=True,
)
```

> Direction: `solutions/agentic_instructions` is the seam for externally-managed,
> versioned instruction sets. A block/profile named here will resolve, in time,
> from a managed store (id + version) rather than only from the in-tree
> registries — with no change to how `blocks` is written in config.

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
| Catalog completeness | Configuration can add reactive tools beyond the core set the body describes; the catalog is the authority, and each catalog section opens with a derived roster (tool count + ids) the model verifies completeness against. Observed failure without this: a weak model stops scanning a long catalog early and treats an opt-in tool it never reached as nonexistent. | `[ON BUILT-IN TOOLS]` note plus the roster line in `build_tools_block`. | `REACT_LITE_TOOL_USE_BASE` note; same roster (catalog path shared). |
| Root `notes` visibility | Notes may be user-visible and must not expose internal/protocol mechanics. | Operating guide, internal notes blocks. | `REACT_LITE_TOOL_USE_BASE`, `REACT_LITE_INTERNAL_NOTES` when internal notes are enabled. |
| Logical namespaces | `conv:ar:`, `conv:fi:`, `conv:tc:`, `conv:so:`, `conv:su:`, `conv:ws:`, `sk:` path contracts plus owner namespace handoff rules. | `PATHS_EXTENDED_GUIDE`, `MEMORY_RECOVERY_GUIDE`. | `REACT_LITE_PATHS_AND_NAMESPACES`; all profiles. |
| `react.read` recovery | Exact logical-path reads, stats-only reads, ranged reads, turn index reads. | `PATHS_EXTENDED_GUIDE`, `MEMORY_RECOVERY_GUIDE`. | `REACT_LITE_REACT_READ_RECOVERY`; all profiles. |
| `react.memsearch` recovery | Find prior conversation material when the exact path is unknown. | `MEMORY_RECOVERY_GUIDE`. | `REACT_LITE_MEMORY_SEARCH_RECOVERY`; `workspace`, `workspace_exec`, `document`, `web`, `all_capabilities`. |
| `react.rg` local search | Search materialized local artifact-root files, not hidden timeline or unpulled history. | Workspace guide plus path guidance. | `REACT_LITE_LOCAL_ARTIFACT_SEARCH`; `workspace`, `workspace_exec`, `document`, `web`, `all_capabilities`. |
| Attachments | Summaries are hints; read originals for precise extraction or visual/layout work. | `ATTACHMENT_AWARENESS_IMPLEMENTER`. | `REACT_LITE_ATTACHMENTS`; all profiles. |
| Sources/citations | Use source pool ids for source-backed claims. | `CITATION_TOKENS`, `SOURCES_AND_CITATIONS_V2`. | `REACT_LITE_SOURCES_CITATIONS`; all profiles. |
| Skills | Load detailed skill instructions through `sk:<skill_id>` when needed. | `REACT_SKILL_SELECTION_GUIDE`. | `REACT_LITE_SKILLS`; all profiles. |
| Workspace mental model | React uses timeline/logical paths plus current-turn artifact root, not arbitrary host fs. | `get_workspace_implementation_guide(...)`, exec/path guidance. | `REACT_LITE_WORKSPACE_BASE`; all profiles. |
| Artifact tree | Physical materialized shape for exec/code: current turn and pulled older turns under artifact root; logs stay in runtime metadata root. | Workspace guide, `EXEC_SNIPPET_RULES`. | `REACT_LITE_WORKSPACE_BASE`; all profiles. |
| `git/projects/` vs `files/` | `git/projects/<scope>` is durable workspace/project state; `files/<scope>` is produced artifacts. | Workspace guide plus realm refs docs. | `REACT_LITE_PROJECTS_AND_FILES`; all profiles. |
| Pull/checkout | Pull historical `conv:fi:` refs, then checkout maintained `git/projects/<scope>` into current workspace before editing. | Workspace guide. | `REACT_LITE_WORKSPACE_PULL_CHECKOUT`; workspace profiles except `core`. |
| Patching | Patch current-turn text files, not old refs; omit displayed line prefixes. | Operating/workspace guide. | `REACT_LITE_PATCHING`; workspace profiles except `core`. |
| `react.write` artifacts | Write user-visible/canvas and internal artifacts with correct `git/projects/`, `files/`, or `git/snapshots/` placement. | Operating guide, internal notes blocks. | `REACT_LITE_REACT_WRITE_ARTIFACTS`; workspace profiles except `core`. |
| Internal conversation notes | User-invisible conversation anchors, not durable user memory. | `INTERNAL_NOTES_PRODUCER`, `INTERNAL_NOTES_CONSUMER`. | `REACT_LITE_INTERNAL_NOTES`; `all_capabilities` or explicit block. |
| Durable user memory read | User-visible cross-conversation memory; current turn overrides memory. | `DURABLE_USER_MEMORY_POLICY`. | `REACT_LITE_DURABLE_USER_MEMORY_READ`; `all_capabilities` or explicit block. |
| Durable user memory write | Durable memory write/proposal tools are neutral runtime bookkeeping; result-dependent success claims wait for the visible tool result. | `DURABLE_USER_MEMORY_POLICY`. | `REACT_LITE_DURABLE_USER_MEMORY_WRITE`; `all_capabilities` or explicit block. |
| Exec/ISO runtime | Generated code is an async-capable module body. Contract `filepath` stays relative as `artifact_rel`; code writes `Path(OUTPUT_DIR) / artifact_rel` after creating its parent. Stdout is capped. | `CODEGEN_BEST_PRACTICES_V2`, `EXEC_SNIPPET_RULES`. | `REACT_LITE_EXEC_TOOL`; `workspace_exec`, `all_capabilities`, or explicit block. |
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
- `REACT_LITE_PROJECTS_AND_FILES`
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

1. List the bundle's enabled tools from
   `surfaces.as_consumer.agents.<agent>.tools`, MCP tools, and built-in
   `react.*` tools.
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
assert "artifact_path = Path(OUTPUT_DIR) / artifact_rel" in body
assert "artifact_path.parent.mkdir(parents=True, exist_ok=True)" in body
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
