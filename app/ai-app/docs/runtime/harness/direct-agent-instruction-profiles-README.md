---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/direct-agent-instruction-profiles-README.md
title: "Direct Agent Instruction Profiles"
summary: "Teach the KDCube Native ReAct agent, LangGraph, and Claude Code how to use the active workspace, conversation recall, tools, and skills correctly while runtime enforcement remains authoritative."
tags: ["runtime", "harness", "agents", "instructions", "profiles", "native-react", "langgraph", "claude-code"]
keywords: ["direct agent instructions", "workspace-files", "conversation search", "lite:core", "additional_instructions", "Claude CLAUDE.md", "LangGraph system prompt"]
updated_at: 2026-09-08
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/quickstart/run-agent-harness-from-python-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/system-instruction-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/skills/instruction-blocks-and-signals-README.md
  - repo:kdcube-ai-app/agents/README.md
---
# Direct Agent Instruction Profiles

A direct Agent Harness host must teach its model how the harness workspace and
configured capabilities work. Tool schemas describe individual calls; the
instruction profile supplies the cross-tool operating model: a response is not
a file, each invocation has a current-turn artifact workspace, isolated code
must contract its outputs, and a tool result is the authority for success.

The profile exists because exposing a tool is not enough to make an agent use
it coherently. Shared teaching lets different agent loops find the right
workspace, preserve deliverables, recover earlier work, and report actual tool
outcomes instead of guessing from a schema.

The direct-hosting SDK owns that base teaching. Application text extends it;
it never replaces it.

## Configuration Contract

Each sample selects a named base profile and keeps product-specific behavior in
a separate field:

```yaml
agent:
  instructions:
    profile: workspace-files
  additional_instructions: |
    You are a research agent. Preserve public source URLs.
```

The **KDCube Native ReAct agent** (**Native agent** below) selects `lite:core`;
LangGraph and Claude Code select `workspace-files`. A legacy scalar
`agent.instructions` remains readable as
additive customization. It is not treated as a complete system instruction.
An unknown profile ID or a config that mixes the legacy scalar with
`additional_instructions` fails during construction.

```text
configured profile
       |
       v
SDK-owned base workspace/conduct contract
       |
       +--> blocks for the enabled tool roster only
       |
       +--> selected skill delivery
       |
       v
administrator additional_instructions (last)
       |
       v
agent framework
```

`additional_instructions` is wrapped as an administrator customization. It can
specialize model behavior; it does not change runtime safety enforcement, the
ReAct protocol parser, or any tool API contract.

## Native agent

The Native agent sample uses:

```yaml
agent:
  instructions:
    profile: lite:core
```

`ReactSolverV2` still owns the strict channel protocol, always-present harness
context, tool catalog, skill gallery, and administrator envelope. The direct
host passes `lite:core` through `instruction_blocks`, then adds the existing
standard blocks corresponding to its effective roster:

| Enabled tool family | Added standard block |
| --- | --- |
| `exec_tools.execute_code_python` | `REACT_LITE_EXEC_TOOL` |
| `rendering_tools.*` | `REACT_LITE_RENDERING_TOOLS` |
| `web_tools.*` | `REACT_LITE_WEB_TOOLS` |

Changing the tool roster changes this teaching. Disabling a capability removes
its block through the same effective-roster check used by hosted Native
agents. The sample's Web Search adapter therefore uses the canonical
`web_tools.web_search` ID.

Other supported Native agent profile IDs are `full`, `lite:<profile>`, and
`xlite:<profile>`, where profile is `core`, `workspace`, `workspace_exec`,
`document`, `web`, or `all_capabilities`. The configured aliases
`instr:profile:full`, `instr:profile:lite`, and
`instr:profile:extra-lite` are also accepted. Capability blocks already present
in a broader profile are not duplicated.

The Native agent's ReAct composition and profile meanings are owned by
[React System Instruction](../../sdk/agents/react/system-instruction-README.md).

## LangGraph And Claude Code

`workspace-files` is the direct-hosting provider-native profile. It contains no
ReAct channel syntax. It teaches:

- KDCube accounting, communicator, conversation, attachment, file, and
  execution evidence boundaries;
- exact current-turn artifact mapping, where a contract path such as
  `files/research/report.xlsx` maps to
  `Path(OUTPUT_DIR) / "files/research/report.xlsx"` without dropping the
  leading namespace;
- the distinction between framework-native process files and hosted KDCube
  artifacts;
- isolated execution without ambient network or secrets;
- standard conduct and untrusted-content guards;
- conversation-recall, web, execution, and renderer guidance only when the
  corresponding tools are enabled.

LangGraph receives the composed text as its `system_prompt`. Its selected
KDCube skill instructions are expanded into that prompt before administrator
customization.

Claude Code receives the composed text as generated `CLAUDE.md`. Its selected
KDCube skills are materialized as native project skills under
`.claude/skills/`; `CLAUDE.md` names that skill surface without duplicating the
full skill body. Claude's native `Read`, `Write`, and `Edit` tools operate on
its process workspace. User-facing deliverables are produced through the
configured KDCube execution and rendering MCP tools.

Conversation persistence does not require an embedding provider. The direct
host always writes transcript rows to Postgres. When the selected embedding
provider has a configured credential or endpoint, conversation search combines
semantic, lexical, and trigram retrieval; otherwise it uses lexical and trigram
retrieval without probing an unconfigured provider. When
`conversation_tools.search` is selected, the profile teaches LangGraph or
Claude to search earlier conversations with the harness-bound caller identity;
the model cannot choose a different user in tool arguments.

## Verify The Selection

Run the construction check from any sample directory:

```bash
.venv/bin/python agent.py --check
```

The startup summary includes:

```text
instruction profile: workspace-files
custom instructions: configured
```

The Native agent prints `instruction profile: lite:core`. The check validates the profile
and exact tool and skill IDs without displaying hidden administrator text.

The runnable configuration and evidence procedure is
[Run the Agent Harness from Python](../../recipes/quickstart/run-agent-harness-from-python-README.md).
