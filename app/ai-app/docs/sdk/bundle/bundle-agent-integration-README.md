---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md
title: "Bundle Agent Integration"
summary: "Canonical bundle guide for wiring React agents, bundle-local tools and skills, MCP connectors, bundle-served MCP endpoints, and Claude Code subagents with deployable auth and network requirements."
tags: ["sdk", "bundle", "agents", "react", "claude-code", "tools", "skills", "mcp", "deployment"]
keywords: ["bundle agent integration", "React tools descriptor", "skills descriptor", "MCP_TOOL_SPECS", "event_source_reader", "bundle served MCP", "Claude Code MCP", "ClaudeCodeAgent", "mcp_base_url", "agent runtime context"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-event-recording-and-sinks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/runtime-configuration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/bus-routing-and-partitioning-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-reserved-platform-properties-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/mcp-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/claude/claude-code-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/claude/claude-code-workspace-bootstrap-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-tools-README.md
---
# Bundle Agent Integration

This page is the canonical bundle-level map for agent integration points.

Use it when a bundle needs any of these:

- a KDCube React agent with bundle-local tools and skills
- bundle-local tools or UI events that need event-source policies, timeline
  block production, snapshots, or custom artifact refs
- MCP tools available to the React tool subsystem
- a bundle-served MCP endpoint exposed through `@mcp(...)`
- a Claude Code subprocess agent that uses custom MCP tools
- separate agent surfaces for chat turns, scheduled jobs, or background jobs

Before adding bundle-local tools or subagents, check
[How To Assemble A Bundle With SDK Building Blocks](build/how-to-assemble-bundle-with-sdk-building-blocks-README.md).
The SDK already provides reusable task, email, Telegram, delivery, web,
rendering, execution, storage, MCP, and Claude Code building blocks.

This document intentionally describes public SDK patterns. Reference bundles may
implement these patterns with product-specific names, but bundle docs should not
depend on private bundle paths.

## 1. Agent Surfaces

There are two different agent runtimes in this SDK surface.

| Runtime | Who runs it | Tool source | Skill source | Typical use |
| --- | --- | --- | --- | --- |
| React | KDCube chat runtime | `tools_descriptor.py`, `MCP_TOOL_SPECS`, SDK tools | `skills_descriptor.py` and bundle `skills/` | normal chat turns, task execution turns, transport-backed assistant work |
| Claude Code | `claude` CLI subprocess | Claude built-ins plus Claude MCP config written into workspace | `CLAUDE.md`, Claude settings, future custom Claude skill support | scoped code/file/research subagent work, private sub-processing, specialized tool loops |

Important:

- Claude Code does not inherit React tools or React skills automatically.
- React does not read Claude `.mcp.json` or `CLAUDE.md`.
- If both runtimes need the same capability, wire it explicitly for each one.

## 2. What A Bundle Can Provide To Agents

Think of a bundle agent surface as:

```text
agent runtime + descriptors + bundle props/secrets + runtime context + transport context
```

The SDK lets the bundle provide different inputs depending on the agent runtime.

### Shared Inputs

These concepts apply to both React and Claude Code, although the concrete API is
different:

| Input | What it is | Who provides it | Where it comes from |
| --- | --- | --- | --- |
| user/turn context | user id, conversation id, turn id, timezone, request text, attachments | platform runtime | `scratchpad`, `ExternalEventPayload`, request context |
| bundle context | tenant, project, bundle id, user scope, storage roots, job ids | platform runtime plus bundle entrypoint | `BaseWorkflow`, `bundle_call_context`, job payload |
| config | non-secret behavior switches, model choices, URLs, feature flags | descriptor/admin/bundle code | `self.bundle_prop(...)`, bundle props |
| secrets | API keys, auth signing keys, OAuth client secrets | deployment/admin/user secret store | `await get_secret(...)`, including `await get_secret("u:...")` for user-scoped secrets |
| custom instructions | product-specific operating rules | bundle code/config | React `additional_instructions`, Claude `CLAUDE.md` |
| tools | callable capabilities | bundle descriptors or Claude config | React tool subsystem, Claude allowed tools/MCP |
| MCP connectivity | how to reach MCP servers and authenticate | bundle config/code | `mcp.services`, `MCP_TOOL_SPECS`, `ClaudeCodeWorkspaceConfig` |
| MCP server exposure | MCP server implemented by the bundle | bundle entrypoint | `@mcp(...)` plus bundle-owned auth |
| streaming | progress, deltas, steps, subsystem events | agent runtime | communicator |
| persistence | durable state, artifacts, workspace/session files | bundle/runtime | bundle storage, conversation store, Claude workspace/session store |

The model should not be asked to invent runtime ids or paths. Those must come
from runtime context, job payload, bundle props, secret lookups, or prior tool
results.

For recording selected events emitted by React, tools, skills, MCP endpoints,
or Claude Code wrapper flows, use
[Bundle Event Recording And Sinks](bundle-event-recording-and-sinks-README.md).

For bundle-authored UI events, tools as event sources, ReAct policy bindings,
story/wizard/canvas snapshots, and custom artifact namespace rehosters, use
[Bundle Events](bundle-events-README.md).

### ReAct Preview Line Numbering

The platform default comes from `assembly.yaml` at
`ai.react.line_numbers_mode` / `AI_REACT_LINE_NUMBERS_MODE`. A bundle can
override it with:

```yaml
items:
  - id: my.bundle@1-0
    config:
      react:
        default_agent:
          line_numbers_mode: sparsed  # disabled | lines | sparsed
```

`lines` preserves the historical behavior and numbers every rendered preview
line. `sparsed` numbers only the first, middle, and last preview windows to
reduce model-input cost. `disabled` omits preview line prefixes. This changes
only model-visible previews and `react.read` rendering; it does not change file
contents or stored artifacts. `react.read` also accepts an explicit
`line_numbers` value for a single read when the tool is available.

## 2A. Model Selection For Agent Roles

Every SDK model call should use a logical role such as
`report.writer`, `memory.reconciler`, or
`solver.react.v2.decision.v2.regular`. The platform model router maps that role
to `{provider, model}`.

There are three supported places to set the mapping.

```text
bundle code default
entrypoint.configuration / configuration_defaults()
        |
        v
deployment or admin override
bundles.yaml -> items[].config.role_models
or live bundle props
        |
        v
current invocation overlay
bundle_call_context.role_models
        |
        v
ModelRouter(role) -> provider/model for SDK agent calls,
React decisions, SDK tools, and isolated tool runtimes
```

Router precedence:

1. `bundle_call_context.role_models` for the currently bound invocation
2. effective bundle props `config.role_models`
3. platform defaults

The request overlay is inherited by nested SDK agent calls, React decision
calls, in-process tools, and isolated Docker/Fargate tool runtimes because the
processor snapshots `bundle_call_context` into `RUNTIME_GLOBALS_JSON` and child
runtimes restore it. It is not persisted. If the same choice must affect a later
background job, store the selected strength/model in the job payload and bind it
again inside `@on_job`.

### Bundle-Level Defaults In Code

Use code defaults when the bundle owns the normal model policy. Merge with
`super()` so platform roles are not dropped.

```python
from typing import Any, Dict

class MyEntrypoint(BaseEntrypoint):
    @property
    def configuration(self) -> Dict[str, Any]:
        config = dict(super().configuration)
        role_models = dict(config.get("role_models") or {})
        for role, spec in {
            "report.writer": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
            "report.writer.lite": {
                "provider": "anthropic",
                "model": "claude-haiku-4-5",
            },
            "report.writer.strong": {
                "provider": "anthropic",
                "model": "claude-opus-4-6",
            },
            "solver.react.v2.decision.v2.regular": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
            "solver.react.v2.decision.v2.strong": {
                "provider": "anthropic",
                "model": "claude-opus-4-6",
            },
        }.items():
            role_models.setdefault(role, spec)
        config["role_models"] = role_models
        return config
```

### External Bundle Props Override

Use descriptor/admin props when the deployment operator should choose the model
without editing bundle source.

```yaml
items:
  - id: my.bundle@1-0
    path: /bundles/my.bundle@1-0
    config:
      role_models:
        report.writer:
          provider: anthropic
          model: claude-sonnet-4-6
        report.writer.lite:
          provider: anthropic
          model: claude-haiku-4-5
        report.writer.strong:
          provider: anthropic
          model: claude-opus-4-6
        solver.react.v2.decision.v2.regular:
          provider: anthropic
          model: claude-sonnet-4-6
        solver.react.v2.decision.v2.strong:
          provider: anthropic
          model: claude-opus-4-6
```

This is durable deployment state. It survives reloads and is exported with
bundle props when the active descriptor provider supports export.

### Ad Hoc Override For One Call

Use `bundle_call_context.role_models` when a widget, API body, MCP call, cron
decision, chat request, or background job chooses a temporary model strength.

`bind_current_bundle_call_context_patch(...)` is a shallow patch. If several
components may set role overrides, merge the existing `role_models` first:

```python
from contextlib import contextmanager
from typing import Iterator

from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    bind_current_bundle_call_context_patch,
    get_current_bundle_call_context,
)

STRENGTH_MODELS = {
    "lite": "claude-haiku-4-5",
    "regular": "claude-sonnet-4-6",
    "strong": "claude-opus-4-6",
}


@contextmanager
def use_agent_model(
    role: str,
    *,
    strength: str = "regular",
    provider: str = "anthropic",
) -> Iterator[None]:
    model = STRENGTH_MODELS.get(strength, STRENGTH_MODELS["regular"])
    current = get_current_bundle_call_context()
    role_models = dict(current.get("role_models") or {})
    role_models[role] = {"provider": provider, "model": model}
    with bind_current_bundle_call_context_patch({
        "role_models": role_models,
        "my_bundle": {"agent_strength": strength},
    }):
        yield
```

The helper above can be used from any bundle surface.

`@api(...)`:

```python
@api(method="POST", alias="report_run", route="operations")
async def report_run(self, strength: str = "regular", **kwargs):
    del kwargs
    with use_agent_model("report.writer", strength=strength):
        return await self._run_report_agent()
```

`@mcp(...)`:

```python
@mcp(alias="report_tools", route="operations")
async def report_tools(self, strength: str = "regular", **kwargs):
    del kwargs
    with use_agent_model("report.writer", strength=strength):
        return await self._build_report_mcp_app()
```

For MCP servers, bind the context around the code that actually performs the
model call. If the decorated method only constructs a long-lived MCP app, bind
again inside the MCP operation handler that runs later.

`@cron(...)`:

```python
@cron(alias="nightly-report", cron_expression="0 2 * * *", span="system")
async def nightly_report(self):
    with use_agent_model("report.writer", strength="lite"):
        await self._enqueue_or_run_nightly_report()
```

`@on_reactive_event`:

```python
@on_reactive_event
async def run(self, **params):
    strength = str(params.get("agent_strength") or "regular")
    with use_agent_model("solver.react.v2.decision.v2.regular", strength=strength):
        return await super().run(**params)
```

`@on_job`:

```python
@on_job
async def on_job(self, **kwargs):
    payload = kwargs.get("payload") or {}
    strength = str(payload.get("agent_strength") or "regular")
    with use_agent_model("report.writer", strength=strength):
        return await self._run_report_job(**kwargs)
```

If the selection arrives through Socket.IO/SSE ingress instead of inside bundle
code, place the same JSON object in `ExternalEventPayload.bundle_call_context` before
the task is queued:

```json
{
  "role_models": {
    "solver.react.v2.decision.v2.regular": {
      "provider": "anthropic",
      "model": "claude-haiku-4-5"
    }
  },
  "my_bundle": {
    "agent_strength": "lite"
  }
}
```

### React Role Ids

For React, override the actual role ids used by the runtime:

```python
with bind_current_bundle_call_context_patch({
    "role_models": {
        "solver.react.v2.decision.v2.regular": {
            "provider": "anthropic",
            "model": "claude-haiku-4-5",
        },
        "solver.react.v2.decision.v2.strong": {
            "provider": "anthropic",
            "model": "claude-opus-4-6",
        },
    },
}):
    result = await react.run(allowed_plugins=allowed_plugins)
```

This affects SDK model calls routed through `ModelServiceBase` /
`ModelRouter`. Direct provider clients that bypass the SDK router will not see
the override.

### React Agent Inputs

A React bundle agent is configured through `BaseWorkflow.build_react(...)`.

```text
bundle workflow
  |
  | build_react(...)
  v
+-------------------------+      +------------------------+
| ToolSubsystem           |      | EventSourceSubsystem   |
|                         |      |                        |
| TOOLS_SPECS             |      | EVENT_SOURCE_SPECS     |
| MCP_TOOL_SPECS          |      | + tool module events   |
| TOOL_RUNTIME            |      |                        |
|                         |      | event declarations     |
| model-callable catalog  |      | block policies         |
| allowed_plugins filter  |      | projection policies    |
+-----------+-------------+      | announce policies      |
            |                    | namespace rehosters    |
            |                    | event-source readers   |
            |                    +-----------+------------+
            |                                |
            v                                v
        React runtime / ContextBrowser / Timeline
            |
            +-- system + additional instructions
            +-- skills catalog filtered by active tools
            +-- runtime hooks from RuntimeCtx
            +-- scratchpad user prompt, attachments, context events
            |
            v
        model decision loop
            |
            +-- call tool -> ToolSubsystem -> react.tool.* blocks
            +-- fold event -> EventSourceSubsystem policies -> timeline/ANNOUNCE
            +-- pull ref  -> namespace rehoster -> fi: workspace mirror
            +-- compact   -> compaction hooks + compaction projections
```

The split is intentional:

| Surface | Makes visible to the model? | Used for |
| --- | --- | --- |
| `TOOLS_SPECS` | yes | callable Python tool aliases |
| `MCP_TOOL_SPECS` | yes | callable MCP tool aliases |
| `EVENT_SOURCE_SPECS` | no | event declarations, policies, readers, namespace rehosters |
| `additional_instructions` | text only | how the model should interpret the connected surfaces |
| runtime hooks | no | workflow callbacks such as compaction lifecycle |

Do not put a module into `TOOLS_SPECS` only to register event policies or
namespace rehosters. If the model should not call it, put it in
`EVENT_SOURCE_SPECS`.

| Input | SDK surface | Notes |
| --- | --- | --- |
| local Python tools | `tools_descriptor.py` / `TOOLS_SPECS` | exposes bundle tool modules by alias |
| MCP tools | `tools_descriptor.py` / `MCP_TOOL_SPECS` | selects which configured MCP server tools enter the catalog |
| namespace rehosters | loaded tool/event modules with `@artifact_namespace_rehoster` | lets `react.pull` import owner-domain refs such as `mem:` or `cnv:` into the ReAct workspace |
| event policies | loaded tool/event modules with policy decorators plus event source declarations | renders external events, tool results, and reader results into timeline/ANNOUNCE/compaction |
| MCP server connection config | bundle props `config.mcp.services` | controls server URLs, transports, and auth |
| skills | `skills_descriptor.py`, `CUSTOM_SKILLS_ROOT`, `AGENTS_CONFIG` | exposes bundle skill prompts and visibility rules |
| skill-tool mapping | skill `tools.yaml` | tells the agent which tool ids belong to a skill; `required: true` gates the skill on active tool availability |
| custom instructions | `additional_instructions` argument | should combine product defaults with bundle-configured instructions |
| model/runtime version | platform/bundle config | React version is selected by platform config; bundle code should call `build_react(...)` |
| allowed tool groups | `react.run(allowed_plugins=...)` | keeps the active turn limited to the aliases intended for that surface |
| turn state | `scratchpad` | carries user text, attachments, conversation metadata, and current turn paths |
| tool runtime context | bound runtime globals/helpers | tools receive service, integrations, communicator, cache, context client, and bundle scope helpers |

Example:

```python
react = self.build_react(
    tools_runtime=getattr(tools_mod, "TOOL_RUNTIME", None),
    mod_tools_spec=tools_mod.TOOLS_SPECS,
    mcp_tools_spec=getattr(tools_mod, "MCP_TOOL_SPECS", None) or [],
    custom_skills_root=skills_mod.CUSTOM_SKILLS_ROOT,
    skills_visibility_agents_config=skills_mod.AGENTS_CONFIG or {},
    scratchpad=scratchpad,
    additional_instructions=additional_instructions,
)

result = await react.run(allowed_plugins=allowed_plugins)
```

Skill discovery is intentionally wider than bundle-local files:

```text
core SDK skills
  + SDK solution skills, for example task.* from the Tasks solution
  + bundle CUSTOM_SKILLS_ROOT
  -> AGENTS_CONFIG filter for the exact consumer id
  -> tools.yaml required-tool filter against active React tool catalog
  -> visible skill catalog / SK short ids
```

Bundle authors can narrow this explicitly, but subsystem skills that declare
required tools are also filtered by the active tool catalog. For example, Tasks
solution skills are omitted automatically when `tasks.*` / `task_job.*` tools
are not exposed. Use `AGENTS_CONFIG` when the bundle needs an explicit
allow-list or hard deny:

```python
AGENTS_CONFIG = {
    "solver.react.v2.decision.v2.strong": {"disabled": ["task.*"]},
    "solver.react.v2.decision.v2.regular": {"disabled": ["task.*"]},
}
```

Use `agent_disclosure: hidden` in `SKILL.md` only for operational guidance that
may still be loaded by exact id or import but must not be advertised in the
skill catalog or in user-facing self-descriptions. This is prompt-disclosure
control, not authorization. Use `AGENTS_CONFIG` when a consumer must not be able
to load a skill at all.

If a skill is coupled to a subsystem that may be disabled per bundle or per
runtime surface, mark its hard tool dependencies in `tools.yaml`:

```yaml
tools:
  - id: memory.search_memory
    role: durable memory read
    required: true
```

When the active React tool catalog does not contain a required tool id, that
skill is removed from the visible catalog, short-id mapping, imported skill set,
and `react.read(sk:...)` path for the current runtime context.

#### React Tool Results That Produce Files

Normal tool JSON is treated as data, not as deliverable files. A bundle tool
must still return the standard envelope:

```json
{"ok": true, "error": null, "ret": {}}
```

The file declaration goes inside `ret`:

```json
{
  "ok": true,
  "error": null,
  "ret": {
    "artifact_type": "files",
    "files": [
      {
        "type": "file",
        "path": "turn_123/outputs/invoices/invoice.pdf",
        "filename": "invoice.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 12345,
        "visibility": "external"
      }
    ]
  }
}
```

The React runtime unwraps `{ok, error, ret}` first. It then recognizes the
explicit `ret.artifact_type == "files"` marker in `ret`, copies each declared
file to the conversation store, and emits normal hosted artifact metadata
(`hosted_uri`, `key`, `rn`, `physical_path`). This works for one file or many
files.

Use this for tools that intentionally materialize files, such as fetching a
specific email attachment. If the user asks for an archive or conversion,
produce the archive/conversion explicitly and declare that file.

Trusted bundle tools can also host files themselves with
`kdcube_ai_app.apps.chat.sdk.tools.bundle_tool_context.host_files(...)` after
writing or materializing the file under the current turn output directory. The
helper uses the active conversation hosting service, emits file events, and
returns the same `ret.artifact_type: "files"` payload with hosted rows already
populated.

This tool-side hosting surface is available to catalog tools in normal React
tool calls, in-memory tool execution, and isolated supervisor/runtime execution.
Generated executor code should reach it by calling a visible catalog tool
through `agent_io_tools.tool_call(...)`; that catalog tool is the trusted
boundary that fetches bytes, writes files, and hosts them.

`host_files(...)` requires prepared runtime scope. The SDK-prepared tool context
must include an active `ToolSubsystem` with hosting service, tenant, project,
user id, conversation id, turn id, user type, conversation storage, and a current
output directory. Normal React bundle flows get this when
`BaseWorkflow.build_react(...)` creates the tool subsystem, and cached workflows
refresh the request-bound communicator through
`BaseWorkflow.rebind_request_context(...)`. Isolated execution gets the same
context from `bootstrap_bind_all(...)` in
`kdcube_ai_app.apps.chat.sdk.runtime.bootstrap`.

If the context is missing, `host_files(...)` raises a runtime error such as
`tools are not bound to the current tool subsystem`,
`tool hosting service is unavailable`, `tool communicator is unavailable`, or
`bundle storage root is unavailable`. Tool schemas should not expose
tenant/project/user/conversation/turn ids for the model to fill in; those ids
come from runtime prep, job payloads, or opaque references returned by earlier
tools.

### Claude Code Agent Inputs

A Claude Code subagent is configured through `ClaudeCodeAgentConfig`,
`ClaudeCodeBinding`, and optional `ClaudeCodeWorkspaceConfig`.

| Input | SDK surface | Notes |
| --- | --- | --- |
| agent identity | `agent_name` | participates in Claude session identity and accounting metadata |
| user/conversation binding | `ClaudeCodeBinding` | controls stable Claude session id boundary |
| model/command | `model`, `command` | `command` defaults to `claude`; model may be alias or full name |
| workspace | `workspace_path` | subprocess working directory, not a sandbox |
| additional directories | `additional_directories` | passed to Claude as `--add-dir` |
| built-in allowed tools | `allowed_tools` | forwarded as Claude `--allowedTools` |
| permission mode | `permission_mode` | forwarded to Claude Code |
| environment | `env` | caller passes resolved env values deliberately |
| timeout | `timeout_seconds` | SDK terminates/marks failed when exceeded |
| streaming markers | `step_name`, `delta_marker` | controls communicator event labels |
| structured progress | `structured_output_prefixes`, callbacks | parses caller-defined line-framed JSON from streamed text |
| MCP servers | `ClaudeCodeWorkspaceConfig.mcp_servers` | SDK writes `.mcp.json` |
| bundle-served MCP tools | `@mcp(...)` endpoint plus `ClaudeCodeWorkspaceConfig.mcp_servers` | `@mcp(...)` exposes the server; workspace config tells Claude how to reach it |
| Claude MCP enablement | `enabled_mcp_servers` | SDK writes `.claude/settings.local.json` |
| Claude allow/deny tools | `allowed_tools`, `denied_tools` in workspace config | SDK writes local Claude settings |
| Claude instructions | `instructions_markdown` | SDK writes `CLAUDE.md` |
| Claude Code project skills | `.claude/skills/<skill-name>/SKILL.md` under `workspace_path` | Claude Code discovers native project Skills from this location |
| KDCube skills | `ClaudeCodeWorkspaceConfig.skill_ids` | SDK resolves active KDCube skills by id/imports and writes native Claude Code project Skills |

The bundle still owns policy:

- which MCP URL is correct for the deployment
- which short-lived token/header should be written
- which built-in Claude tools are allowed or denied
- which instructions are safe for the scenario
- which env secrets are passed into the subprocess

The SDK can write the standard workspace files from `ClaudeCodeWorkspaceConfig`,
but it does not decide those values automatically.

Skill rule:

- React skills are first-class SDK inputs through `skills_descriptor.py`.
- Claude Code does not consume KDCube `skills_descriptor.py`, `SKILL.md`, or
  skill `tools.yaml` directly.
- To use KDCube skills with Claude Code, pass fully-qualified skill ids to
  `ClaudeCodeWorkspaceConfig.skill_ids`. The SDK resolves them through the
  active skills subsystem, expands imports, and writes native Claude Code
  project Skills under `.claude/skills/<skill-name>/SKILL.md`.
- For short global guidance, use
  `ClaudeCodeWorkspaceConfig.instructions_markdown`, which the SDK writes as
  `CLAUDE.md`.
- Tool access still has to be wired separately for Claude Code, usually through
  MCP and `allowed_tools`. If a Claude Code Skill should declare skill-local
  tool hints, pass Claude MCP/built-in tool names through
  `ClaudeCodeWorkspaceConfig.skill_allowed_tools`.

`CLAUDE.md` and Skills are not the same:

- `CLAUDE.md` is broad project/workspace instruction loaded as part of the
  Claude project context.
- a Claude Code Skill is a discoverable capability folder. Claude reads the
  Skill metadata, then loads the full `SKILL.md` only when the task matches its
  description.
- Skill support files live next to `SKILL.md` and are loaded only when needed.

Native Claude Code project Skill layout:

```text
workspace/
  .claude/
    skills/
      email-processing/
        SKILL.md
        reference.md
        scripts/
          helper.py
```

Minimal native Claude Code `SKILL.md`:

```markdown
---
name: Email Processing
description: Process scoped email candidates and record a structured result. Use when the task asks Claude to classify, summarize, or match email messages through the scoped email MCP tools.
allowed-tools: mcp__task_memo_email__task_context, mcp__task_memo_email__list_new_messages, mcp__task_memo_email__get_message, mcp__task_memo_email__record_processing_result
---

# Email Processing

Use only the scoped email MCP tools.
Call task_context first.
Inspect only candidate messages returned by the MCP server.
Call record_processing_result before the final answer.
```

SDK materialization:

- `ClaudeCodeWorkspaceConfig.skill_ids` writes native Claude Code Skill folders
  from KDCube skills known to the active skills subsystem.
- `ClaudeCodeWorkspaceConfig.instructions_markdown` writes `CLAUDE.md`.
- Skill imports are expanded before materialization.
- Support files next to the KDCube `SKILL.md` are copied next to the generated
  Claude Code `SKILL.md`.

Example:

```python
workspace_config = ClaudeCodeWorkspaceConfig(
    mcp_servers={
        "bundle_email": {
            "type": "http",
            "url": mcp_url,
            "headers": {"Authorization": f"Bearer {short_lived_token}"},
        }
    },
    skill_ids=("product.email-analysis",),
    skill_allowed_tools={
        "product.email-analysis": (
            "mcp__bundle_email__task_context",
            "mcp__bundle_email__list_new_messages",
            "mcp__bundle_email__get_message",
            "mcp__bundle_email__record_processing_result",
        )
    },
    allowed_tools=(
        "mcp__bundle_email__task_context",
        "mcp__bundle_email__list_new_messages",
        "mcp__bundle_email__get_message",
        "mcp__bundle_email__record_processing_result",
    ),
)
```

## 3. React Bundle Agent Integration

Recommended layout:

```text
my.bundle@1-0/
  entrypoint.py
  orchestrator/
    workflow.py
  tools_descriptor.py
  skills_descriptor.py
  tools/
    domain_tools.py
  skills/
    product/
      domain/
        SKILL.md
        tools.yaml
```

`tools_descriptor.py` exposes local Python tools and optional MCP tool specs:

```python
TOOLS_SPECS = [
    {"ref": "tools/domain_tools.py", "alias": "domain", "use_sk": True},
]

MCP_TOOL_SPECS = [
    {"server_id": "docs", "alias": "docs", "tools": ["search", "fetch"]},
]
```

`skills_descriptor.py` exposes bundle skill roots and visibility rules:

```python
CUSTOM_SKILLS_ROOT = "skills"

REACT_DECISION_SKILLS = [
    "public.*",
    "product.domain",
]

AGENTS_CONFIG = {
    "solver.react.v2.decision.v2.strong": {"enabled": REACT_DECISION_SKILLS},
    "solver.react.v2.decision.v2.regular": {"enabled": REACT_DECISION_SKILLS},
}
```

The skill registry loads core SDK skills, SDK solution skills, and the bundle
`CUSTOM_SKILLS_ROOT`. Solution skills such as `task.tasks` and `task.job` are
present in discovery even if the bundle does not use the Tasks solution, but
their required tool gates remove them from the active catalog when task tools
are absent. Use explicit `enabled` lists or `disabled: ["task.*"]` only when
the bundle wants policy stricter than tool availability.

The keys are the React decision agent ids used by the runtime. Use both
`solver.react.v2.decision.v2.strong` and
`solver.react.v2.decision.v2.regular` when both model tiers should see the same
bundle skills. Do not key visibility by the skill id itself. If a bundle has a
different decision agent name, use the name emitted in runtime/accounting logs.

Skill front matter may include `agent_disclosure: hidden`. Hidden-disclosure
skills are excluded from the visible catalog and `SK1` short-id map. If loaded
by exact id or import, their active instruction block is rendered with a
redacted heading and a non-disclosure rule instead of the skill id/name. This
does not disable the skill; combine it with `AGENTS_CONFIG` if the skill must be
unavailable to a consumer.

Each skill's `tools.yaml` should reference real tool ids from the descriptor.
For example, with alias `domain`, a Python function `search_assets` becomes:

```yaml
tools:
  - domain.search_assets
  - domain.update_asset
```

The workflow builds React from those descriptors:

```python
base_instructions = (
    "You are the product assistant. Use product tools for durable product state."
)
configured_instructions = self.bundle_prop("react.default_agent.additional_instructions", "")
additional_instructions = "\n".join(
    item for item in [base_instructions, configured_instructions] if item
)

react = self.build_react(
    tools_runtime=getattr(tools_mod, "TOOL_RUNTIME", None),
    mod_tools_spec=tools_mod.TOOLS_SPECS,
    mcp_tools_spec=getattr(tools_mod, "MCP_TOOL_SPECS", None) or [],
    custom_skills_root=skills_mod.CUSTOM_SKILLS_ROOT,
    skills_visibility_agents_config=skills_mod.AGENTS_CONFIG or {},
    scratchpad=scratchpad,
    additional_instructions=additional_instructions,
)

allowed_plugins = [
    spec["alias"]
    for spec in tools_mod.TOOLS_SPECS
    if spec.get("alias")
]

for spec in getattr(tools_mod, "MCP_TOOL_SPECS", None) or []:
    allowed_plugins.append(spec.get("alias") or f"mcp_{spec.get('server_id')}")

result = await react.run(allowed_plugins=list(dict.fromkeys(allowed_plugins)))
```

React configuration sources:

- `tools_descriptor.py` controls local Python tool modules and aliases
- loaded modules can also contribute event-source declarations, event-source
  readers, rehosters, and policies; these are not automatically model-visible
  tools
- event-only modules enter through `event_source_specs`; this is cumulative
  with tool-module event declarations and does not expose additional callable
  tools
- `MCP_TOOL_SPECS` controls which MCP server tools enter the React catalog
- `skills_descriptor.py` controls skill roots and visibility
- bundle props such as `mcp.services` control MCP connection details
- bundle props may add product-specific instructions, for example
  `react.default_agent.additional_instructions`
- platform config selects the React runtime version; bundle code should call
  `BaseWorkflow.build_react(...)` instead of hardcoding a React version

Event-source `kind` is part of this contract:

- `kind="react.tool"` means the occurrence is an actual tool call/result, such
  as `canvas.patch`;
- `kind="react.event_source_reader"` means the source backs
  runtime/policy resolution for canonical refs, such as `canvas.read` for
  `cnv:` or memory read for `mem:`. Exact model-facing content should be
  imported through `react.pull(...)` when the namespace has a registered
  rehoster;
- `kind="react.external"` means a UI/integration-authored event transported as
  `external_events[]`.

Do not expose read-only owner readers as direct tools just because they have an
event source id. The model should call `react.pull` on the canonical ref when
it needs exact content in the workspace.

Runtime context rule:

- user id, conversation id, turn id, task id, execution id, storage roots, and
  provider context must come from runtime context, job payload, or previous tool
  results
- do not ask the model to invent runtime ids or filesystem paths
- use bundle tool helpers and `bundle_call_context` for ids that should not be
  model-provided

Model-facing tool contract:

- every React-visible tool should return the standard envelope
  `{ok, error, ret}`
- every tool description or return annotation should also state the concrete
  `ret` shape that will appear on the timeline, for example
  `ret={items:[{id,title,status}],count,next_cursor?}`
- include exact ids and opaque references in `ret` when later tool calls should
  reuse them
- do not rely on hidden service fields or vague phrases such as "returns
  metadata"; the decision model sees the tool catalog and timeline result, so
  this contract determines whether it can plan the next step correctly

Multiple agent surfaces are allowed. A bundle can keep separate descriptors for
normal chat and scheduled job execution:

```text
tools_descriptor.py
skills_descriptor.py
job_tools_descriptor.py
job_skills_descriptor.py
```

This keeps job tools narrower and prevents the scheduled-job agent from editing
task definitions when it should only execute one task.

## 4. MCP Has Three Different Meanings

Do not collapse these concepts:

| Concept | Config/code | Consumer |
| --- | --- | --- |
| MCP client config for React/KDCube tools | `config.mcp.services` plus `MCP_TOOL_SPECS` | KDCube `ToolSubsystem` |
| Bundle-served MCP endpoint | `@mcp(...)` on the bundle entrypoint | external MCP clients, Claude Code, other services |
| Claude Code MCP config | `.mcp.json` in the Claude workspace | the `claude` CLI subprocess |

`config.mcp.services` does not configure Claude Code.

Claude Code sees an MCP server only if the bundle writes Claude-compatible MCP
configuration into the workspace that Claude runs from.

## 5. External MCP Server

If the MCP server is external to the bundle, do not declare `@mcp(...)`.

For React/KDCube tools, configure the external server in bundle props:

```yaml
config:
  mcp:
    services:
      mcpServers:
        docs:
          transport: http
          url: https://mcp.internal.example.com
          auth:
            type: bearer
            secret: b:docs.token
```

Then expose only the needed tools in `MCP_TOOL_SPECS`:

```python
MCP_TOOL_SPECS = [
    {"server_id": "docs", "alias": "docs", "tools": ["search", "fetch"]},
]
```

For Claude Code, the same external server must still be written into Claude's
workspace `.mcp.json`. Claude Code does not read KDCube bundle props directly.

## 6. Bundle-Served MCP Endpoint

Use `@mcp(...)` when the bundle itself exposes an MCP server.

Entrypoint shape:

```python
from typing import Any

from kdcube_ai_app.infra.plugin.bundle_loader import mcp


@mcp(alias="scoped_data", route="public", transport="streamable-http")
def scoped_data_mcp(self, request: Any, **kwargs):
    return build_scoped_data_mcp_app(
        entrypoint=self,
        request=request,
        storage_root=self.storage_root,
    )
```

Route shape:

```text
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{alias}
/api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/{alias}
```

For MCP, `public` and `operations` are URL families. They are not proc-side
auth modes. Proc forwards the request into the bundle MCP app, and the bundle
owns authentication.

MCP service shape:

```python
from typing import Any

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP


TOKEN_HEADER = "X-Example-MCP-Token"
SERVER_NAME = "example_scoped_data"


def build_scoped_data_mcp_app(*, entrypoint: Any, request: Any, storage_root: str):
    expected = load_or_verify_run_token(entrypoint=entrypoint, request=request)
    if not expected.ok:
        raise HTTPException(status_code=401, detail=expected.error)

    mcp = FastMCP(SERVER_NAME, stateless_http=True)

    @mcp.tool(name="task_context")
    async def task_context() -> dict:
        return {"task_id": expected.task_id, "instruction": expected.instruction}

    @mcp.tool(name="list_items")
    async def list_items(limit: int = 20) -> dict:
        return {"items": expected.items[: max(1, min(int(limit or 20), 50))]}

    @mcp.tool(name="get_item")
    async def get_item(item_id: str) -> dict:
        if item_id not in expected.allowed_item_ids:
            return {"ok": False, "error": {"code": "item_not_in_scope"}}
        return {"ok": True, "item": expected.items_by_id[item_id]}

    @mcp.tool(name="record_result")
    async def record_result(processed_item_ids: list[str], matched_item_ids: list[str]) -> dict:
        persist_result(storage_root, expected.run_id, processed_item_ids, matched_item_ids)
        return {"ok": True}

    return mcp
```

For scoped data, prefer short-lived run-scoped auth:

- token is signed by a bundle secret
- token includes `user_id`, `run_id`, `scope`, and `exp`
- token is sent in a custom header named by bundle props
- server validates the token before returning MCP tools
- tools enforce candidate item ids from the saved run document

This pattern lets the MCP endpoint be reachable without making the data public.

## 7. Claude Code Agent With Bundle MCP

Claude Code is configured by files in the workspace and by
`ClaudeCodeAgentConfig`.

Responsibility split:

- the bundle decides which MCP server URL, token, allowed tools, denied tools,
  and instructions are appropriate for the current run
- the SDK can write the standard Claude Code workspace files from that config
  by using `ClaudeCodeWorkspaceConfig`
- the SDK does not invent auth policy, resolve secrets, or decide which MCP
  tools are safe for a product scenario

Workspace files usually include:

```text
workspace/
  .mcp.json
  .claude/
    settings.local.json
  CLAUDE.md
```

Example `.mcp.json`:

```json
{
  "mcpServers": {
    "example_scoped_data": {
      "type": "http",
      "url": "https://internal.example/api/integrations/bundles/demo/project/example.bundle@1-0/public/mcp/scoped_data",
      "headers": {
        "X-Example-MCP-Token": "<short-lived-run-token>"
      }
    }
  }
}
```

Example `.claude/settings.local.json`:

```json
{
  "enableAllProjectMcpServers": false,
  "enabledMcpjsonServers": ["example_scoped_data"],
  "permissions": {
    "allow": [
      "mcp__example_scoped_data__task_context",
      "mcp__example_scoped_data__list_items",
      "mcp__example_scoped_data__get_item",
      "mcp__example_scoped_data__record_result"
    ],
    "deny": ["Bash", "Read", "Edit", "Write", "WebFetch", "WebSearch"]
  }
}
```

Example `CLAUDE.md`:

```markdown
# Scoped Data Processor

Use only the configured example_scoped_data MCP tools.
Always call task_context first.
Only inspect candidate items returned by the MCP server.
Call record_result before the final answer.
```

The bundle creates and runs the Claude agent:

```python
from pathlib import Path

from kdcube_ai_app.apps.chat.sdk.solutions.claude_code import (
    ClaudeCodeAgent,
    ClaudeCodeAgentConfig,
    ClaudeCodeBinding,
    ClaudeCodeWorkspaceConfig,
    run_claude_code_turn,
)

workspace_config = ClaudeCodeWorkspaceConfig(
    mcp_servers={
        "example_scoped_data": {
            "type": "http",
            "url": mcp_url,
            "headers": {"X-Example-MCP-Token": short_lived_token},
        }
    },
    allowed_tools=[
        "mcp__example_scoped_data__task_context",
        "mcp__example_scoped_data__list_items",
        "mcp__example_scoped_data__get_item",
        "mcp__example_scoped_data__record_result",
    ],
    denied_tools=["Bash", "Read", "Edit", "Write", "WebFetch", "WebSearch"],
    instructions_markdown=(
        "# Scoped Data Processor\n\n"
        "Use only the configured example_scoped_data MCP tools.\n"
        "Always call task_context first, inspect only scoped items, and call "
        "record_result before the final answer.\n"
    ),
)

agent = ClaudeCodeAgent(
    config=ClaudeCodeAgentConfig(
        agent_name="scoped-data-processor",
        workspace_path=Path(workspace),
        model=bundle_prop("integrations.scoped_data.claude_code.model", "sonnet"),
        allowed_tools=list(workspace_config.allowed_tools),
        workspace_config=workspace_config,
        env={
            "ANTHROPIC_API_KEY": anthropic_key,
            "CLAUDE_CODE_KEY": claude_code_key,
            "MCP_TIMEOUT": "10000",
            "MCP_TOOL_TIMEOUT": "60000",
            "MAX_MCP_OUTPUT_TOKENS": "50000",
            "DISABLE_AUTOUPDATER": "1",
        },
        command=bundle_prop("integrations.scoped_data.claude_code.command", "claude"),
        permission_mode="acceptEdits",
        timeout_seconds=300,
        step_name="scoped_data.claude_code",
        delta_marker="scoped_data_processing",
    ),
    binding=ClaudeCodeBinding(
        user_id=user_id,
        conversation_id=conversation_id,
        session_id=session_id,
        claude_session_id=stable_claude_session_id,
    ),
    comm=comm,
)

result = await run_claude_code_turn(agent=agent, prompt=prompt)
```

When `workspace_config` is present, `ClaudeCodeAgent.run_turn(...)` writes the
standard files before starting Claude. A bundle can still write those files
itself for specialized cases, but the normal path should use the SDK helper.

Current Claude Code customization knobs exposed by the SDK runner:

- `agent_name` for Claude session identity and accounting metadata
- `workspace_path` for the caller-owned Claude workspace
- `model` and `command`
- `allowed_tools`
- `additional_directories`
- `extra_args`
- `env`
- `permission_mode`
- `timeout_seconds`
- `step_name` and `delta_marker` for communicator events
- `structured_output_prefixes`, `on_structured_output`, and `on_text_chunk`
- `executive_journal_prefixes` and `executive_journal_max_entries` for the
  standard `EXECUTIVE_JOURNAL {...}` checkpoint channel captured in
  `ClaudeCodeRunResult.executive_journal`
- `workspace_config` for SDK-managed `.mcp.json`,
  `.claude/settings.local.json`, and `CLAUDE.md`

Runtime behavior:

- Claude stdout/stderr is consumed with chunk-based line assembly so a single
  large `stream-json` line does not crash the reader.
- The runner touches the processor task watchdog on subprocess start,
  stdout/stderr activity, and while the subprocess remains alive. These touches
  are internal activity signals; they do not emit synthetic chat events.
- Contracted isolated execution through `exec_tools.execute_code_python` also
  touches the same watchdog while the isolated runtime is still running, so a
  long computation is not treated as idle solely because it has no visible chat
  deltas yet.
- The processor hard wall-time cap still applies even when internal activity is
  present.

Workspace and secret boundary:

- `workspace_path` means "run Claude from this directory."
- `additional_directories` means "also pass these paths via `--add-dir`."
- That is workspace scoping, but not security isolation. Claude is still a
  subprocess in the same OS/container security boundary. It is not a sandbox,
  chroot, container, or per-user filesystem jail.
- Repo bootstrap/publish means hydrating/persisting Claude's own
  session/workspace files, for example via git-backed session store. That
  remains handled by the higher-level runtime, not the low-level subprocess
  runner.
- Secret injection policy means the runner should not decide which secrets are
  safe to resolve/write. The caller must pass resolved short-lived tokens or env
  values deliberately.

Do not treat a completed Claude process as proof that the MCP workflow
succeeded. If the MCP workflow requires `record_result`, read the run document
after Claude exits and report failure when no result was recorded.

## 8. MCP URL Reachability

`@mcp(...)` creates the route path. It does not solve which host name Claude
should use.

The URL must be reachable from the process or container running `claude`.

Recommended config:

```yaml
config:
  integrations:
    scoped_data:
      claude_code:
        mcp_base_url: "http://chat-processor.internal:8020"
```

The bundle then builds:

```python
base = entrypoint.bundle_prop("integrations.scoped_data.claude_code.mcp_base_url")
url = f"{base}/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/scoped_data"
```

Deployment matrix:

| Deployment | Can use `127.0.0.1`? | Correct base URL |
| --- | --- | --- |
| local dev, Claude and proc on same host | yes | `http://127.0.0.1:<CHAT_PROCESSOR_PORT>` |
| same container as proc | yes, if proc listens there | `http://127.0.0.1:<container-port>` |
| different Docker container | no | Docker service DNS or internal host name |
| ECS same task, shared task network | usually yes, if target listens on the port | `http://127.0.0.1:<container-port>` or the task-local container endpoint |
| ECS separate task/service | no | Cloud Map name, internal ALB, or other internal service endpoint |

If the URL is wrong, Claude will see MCP connection or invalid-session errors
even though the bundle's `@mcp(...)` endpoint is implemented correctly.

## 9. Claude Code Deployment Requirements

For Claude Code plus bundle MCP to work in any deployment, all of these must be
true:

- the `claude` CLI exists in the runtime image/container
- Anthropic or Claude Code credentials are available to the subprocess
- the Claude workspace path is writable
- any required workspace files are written before the turn starts
- the bundle MCP endpoint is enabled by bundle props
- Claude can reach the MCP URL over HTTP from its network namespace
- the MCP endpoint authenticates itself because proc does not authenticate MCP
- the MCP app is stateless HTTP or correctly handles lifespan/session startup
- the MCP tools use bounded schemas and bounded outputs
- write tools are idempotent or run-scoped enough to survive retries
- logs identify run id, user scope, MCP URL, allowed tools, and final status

Managed container recommendations:

- set `DISABLE_AUTOUPDATER=1` for the Claude subprocess
- avoid relying on globally cached Claude or MCP state
- construct the workspace under bundle storage or another deterministic,
  writable runtime path
- configure `mcp_base_url` per environment instead of hardcoding localhost

## 10. Testing Checklist

Before debugging the model, prove the runtime boundary:

1. Verify the bundle endpoint is discoverable through `@mcp(...)`.
2. Call `tools/list` against the final MCP URL from the same host/container that
   will run Claude.
3. Confirm missing or invalid MCP auth returns `401`.
4. Confirm valid MCP auth returns only the scoped tools.
5. Confirm each tool enforces the run scope.
6. Generate the Claude workspace and inspect `.mcp.json` and
   `.claude/settings.local.json`.
7. Run the Claude turn with only the MCP allowed tools.
8. Assert the run-scoped result was recorded.
9. Assert a Claude timeout, MCP connection failure, or missing recorded result
   is reported as MCP sub-processing failure, not as success.

Useful route probes:

```bash
curl -X POST \
  "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "X-Example-MCP-Token: $TOKEN" \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/list"}'
```

For Docker or ECS, run that probe from the same container or task context that
will spawn `claude`.
