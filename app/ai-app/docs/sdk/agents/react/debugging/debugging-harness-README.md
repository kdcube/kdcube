---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/debugging/debugging-harness-README.md
title: "React Harness Debugging"
summary: "How to trace a React turn through the SDK harness: runtime context, tool calls, workspace writes, artifact hosting, communicator events, persisted turn logs, and failure markers."
tags: ["sdk", "agents", "react", "debugging", "harness", "tools", "artifacts", "streaming"]
keywords: ["React harness debugging", "turn_id", "conversation_id", "tool_call_id", "chat.files", "host_files", "artifact hosting", "turn log", "timeline", "communicator", "external events", "tool subsystem"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/tool-call-blocks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/turn-log-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/conversation-artifacts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/artifact-storage-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-turn-workspace-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/tool-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/custom-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/hosting/files-storage-system-README.md
---
# React Harness Debugging

This page is the operator/agent guide for proving that React SDK machinery
actually ran and for finding where an integration failed.

The React harness is not one function. A successful turn may touch:

- request/runtime context
- decision loop
- context browser and timeline
- tool subsystem
- isolated/runtime tool bridge
- current-turn workspace
- file/artifact hosting
- communicator events
- conversation store
- accounting
- transport-specific renderers such as Telegram or web clients

Debugging starts with concrete ids, then follows observable side effects.

## Minimum Trace Keys

Always collect these first:

| Key | Why it matters |
| --- | --- |
| `tenant` | storage and event routing scope |
| `project` | storage and event routing scope |
| `bundle_id` | bundle loader, storage, and tool descriptor scope |
| `user_id` | bundle user/account scope |
| `conversation_id` | timeline, communicator room, and hosted files |
| `turn_id` | current turn workspace, turn log, and artifact paths |
| `tool_call_id` | exact tool call/result blocks and hosting lineage |
| `execution_id` / `task_id` | saved-task/job runs when applicable |

Do not ask the model to invent these. They must come from runtime context,
logs, job payload, or a prior tool result.

## One-Turn Debug Recipe

1. Find the turn start/completion lines.

```text
conversation_id=<id>
turn_id=<id>
```

2. Confirm the React loop advanced.

Search for:

```text
[react.v2]
[react.v3]
phase=decision
ANNOUNCE
Iteration
```

3. Confirm runtime context was bound.

Search for:

```text
tenant=
project=
bundle_id=
user_id=
conversation_id=
turn_id=
```

4. Confirm tool calls happened.

Search for:

```text
[bundle.tool.start]
[bundle.tool.success]
[bundle.tool.error]
react.tool.call
react.tool.result
tool_call_id=
```

5. Confirm workspace and artifacts.

Search for:

```text
OUT_DIR
WORKDIR
exec-workspace
artifact_analysis
hosted turn_log
chat.files
```

6. Confirm the transport saw final material.

For Telegram, search for:

```text
telegram.stream
telegram.render
telegram.send
telegram.edit
file_items=
```

For web/SSE clients, search for:

```text
chat.delta
chat.files
ChatRelayCommunicator
ServiceCommunicator
```

## Expected Side Effects

Use this table to decide whether a subsystem ran.

| Mechanism | Expected side effect | Useful markers |
| --- | --- | --- |
| React decision loop | timeline blocks, iteration logs, final state | `ANNOUNCE`, `phase=decision`, `turn_log_blocks` |
| React tool call | `react.tool.call` and `react.tool.result` blocks | `tool_call_id`, `tc:<turn>.<call>.call`, `tc:<turn>.<call>.result` |
| Bundle/custom tool | start/success/error logs with scope | `[bundle.tool.start]`, `[bundle.tool.success]`, `[bundle.tool.error]` |
| Isolated tool bridge | supervisor/runtime bootstrap and tool-call result | `bootstrap_bind_all`, `ToolStub`, `agent_io_tools.tool_call` |
| Current-turn workspace | files under current `OUT_DIR` / turn workspace | `turn_<id>/outputs/...`, `exec-workspace` |
| Declarative file result | React hosts `ret.artifact_type == "files"` rows | `[react.artifact.host.*]`, `chat.files` |
| Tool-side file hosting | trusted tool hosts through `host_files(...)` | `[bundle.tool.host_files.*]`, `chat.files` |
| Communicator streaming | delta/file events emitted to room or peer | `ChatRelayCommunicator`, `chat.delta`, `chat.files` |
| Turn-log persistence | final or recovered turn log in conversation store | `hosted turn_log`, `turn.log.json` |
| Accounting | usage records under tenant/project/bundle/user context | `[apply_accounting]`, `Token breakdown`, `Estimated spend` |

## Tool Result Contracts

Normal tool data is not a file.

Files are recognized only through the strict tool envelope:

```json
{
  "ok": true,
  "error": null,
  "ret": {
    "artifact_type": "files",
    "files": [
      {
        "type": "file",
        "visibility": "external",
        "physical_path": "turn_123/outputs/report.pdf",
        "filename": "report.pdf",
        "mime_type": "application/pdf"
      }
    ]
  }
}
```

React v2 and v3 unwrap `{ok, error, ret}` first. Then they host rows only when
`ret.artifact_type == "files"`.

Trusted tools may instead call:

```python
from kdcube_ai_app.apps.chat.sdk.tools.bundle_tool_context import host_files

hosted = await host_files(files, emit=True)
```

The helper requires prepared tool runtime context:

- active `ToolSubsystem`
- `ToolSubsystem.hosting_service`
- tenant
- project
- user id
- conversation id
- turn id
- conversation storage
- readable output directory

Normal React workflows prepare this in `BaseWorkflow.build_react(...)`.
Isolated execution prepares this in `bootstrap_bind_all(...)`.

## File Hosting Trace Markers

There are two hosting paths.

### Tool-Side Hosting

This is used when a trusted bundle/catalog tool calls `host_files(...)`.

Search for:

```text
[bundle.tool.host_files.start]
[bundle.tool.host_files.success]
[bundle.tool.host_files.error]
[bundle.tool.host_files.emit.start]
[bundle.tool.host_files.emit.success]
[bundle.tool.host_files.emit.error]
```

What the markers mean:

| Marker | Meaning |
| --- | --- |
| `start` | tool asked the platform to host files |
| `success` | conversation store returned hosted rows |
| `error` | hosting failed and the tool call should expose/fallback/report it |
| `emit.start` | hosted rows are being emitted as file events |
| `emit.success` | `chat.files` emission completed |
| `emit.error` | file rows may be stored, but event delivery failed |

The log includes scope, request id, outdir, count, filename/path/mime/visibility
summary, and hosted metadata such as `rn`, `key`, and `hosted_uri`.

### React Declarative Hosting

This is used when a tool returns `ret.artifact_type == "files"` and React hosts
the rows after the tool returns.

Search for:

```text
[react.artifact.host.start]
[react.artifact.host.success]
[react.artifact.host.empty]
[react.artifact.host.error]
[react.artifact.host.skip]
[react.artifact.emit.start]
[react.artifact.emit.success]
[react.artifact.emit.error]
[react.artifact.emit.skip]
```

What the markers mean:

| Marker | Meaning |
| --- | --- |
| `host.start` | React is attempting to host one declared file |
| `host.success` | the file was hosted and artifact metadata was updated |
| `host.empty` | hosting service returned no hosted row |
| `host.error` | hosting raised; React keeps best-effort behavior and continues |
| `host.skip` | React had no hosting service or communicator |
| `emit.start` | React is emitting hosted files to clients |
| `emit.success` | event emission completed |
| `emit.error` | emit failed |
| `emit.skip` | emit was requested but could not run |

If `host.error` appears but the tool result still has local file rows, inspect
the file path and current `OUT_DIR`; the most common issue is that the declared
file does not exist where the harness expects it.

## Exec Tool Debug Markers

React decisions are logged with the same payload that is also rendered as the
console decision table:

```text
[agent.packet] agent=<solver-id> phase=react.decision.v2
[agent.packet] agent=<solver-id> phase=react.decision.v3
Internal thinking:
User-facing:
Structured response:
```

The Rich table in the terminal is for operator readability. The `[agent.packet]`
record is the file-log version to grep in `chat-proc.log`.

Tool timeline blocks are also mirrored into the file log:

```text
[react.tool.call] turn_id=<turn> call_id=<tc> tool_id=<tool>
[react.tool.result] turn_id=<turn> call_id=<tc>
```

The call/result log body is the same JSON/text stored in the timeline block.
Binary blocks log metadata only and omit base64, so PDFs/images remain
traceable without flooding the log.

Before executing generated code, the React exec path logs the exact payload that
will be run:

```text
[react.exec.prepare]
[react.exec.contract]
[/react.exec.contract]
[react.exec.code]
[/react.exec.code]
```

Use these markers when a generated script produced the wrong output, wrote to
the wrong path, or created a corrupt artifact.

| Marker | Meaning |
| --- | --- |
| `react.tool.call` | exact model-selected tool id and parameters contributed to the timeline |
| `react.tool.result` | exact tool result or file metadata contributed to the timeline |
| `react.exec.prepare` | tool id, tool call id, exec id, turn id, conversation id, timeout, workdir, and outdir |
| `react.exec.contract` | normalized output contract passed to the exec runtime |
| `react.exec.code` | exact generated code passed to the exec runtime after React path rewriting |

The contract and code logs are emitted after React normalizes the contract and
rewrites known current-turn file references. They are the authoritative input to
the exec runtime for that attempt.

Cache-point diagnostics are compact single-line records:

```text
[cache_points:attempt]
[cache_points:retry]
[anthropic.payload.cache]
```

They preserve cache indexes and previews without dumping the full Anthropic
payload.

## Where To Look On Disk

Local development paths vary, but these are the usual roots.

Bundle shared local storage:

```text
~/.kdcube/dev-workspace/data/bundle-storage/<tenant>/<project>/<bundle-safe-id>/
```

Conversation storage:

```text
~/.kdcube/dev-workspace/data/kdcube-storage/cb/tenants/<tenant>/projects/<project>/conversation/<user>/<conversation_id>/<turn_id>/
```

Execution workspace:

```text
~/.kdcube/dev-workspace/data/exec-workspace/<ctx>/work/
~/.kdcube/dev-workspace/data/exec-workspace/<ctx>/out/
```

Turn outputs usually use:

```text
<OUT_DIR>/<turn_id>/outputs/...
```

Hosted turn logs may appear as:

```text
artifact-<timestamp>-turn.log.json
```

## Common Failure Patterns

### Tool Ran But Files Did Not Arrive

Check:

```text
[bundle.tool.success]
[bundle.tool.host_files.*]
[react.artifact.host.*]
chat.files
telegram.render
telegram.send
```

Interpretation:

- `bundle.tool.host_files.success` but no `chat.files`: emit failed or the
  transport listener was detached.
- `bundle.tool.host_files.error`: the tool-side hosting utility failed. The tool
  may fall back to declarative rows if it catches the exception.
- `react.artifact.host.error`: declarative hosting failed after the tool
  returned. Check file path, `OUT_DIR`, and scope.
- no hosting markers at all: the tool did not return `ret.artifact_type ==
  "files"` and did not call `host_files(...)`.

### Tool Call Used Wrong User Or Account Scope

Search the tool start log:

```text
[bundle.tool.start] tool=<tool_id> scope={...}
```

Verify:

- `tenant`
- `project`
- `bundle_id`
- `user_id`
- `conversation_id`
- `turn_id`

If these are wrong, fix runtime mapping or job context. Do not add
model-facing parameters for these ids.

### Isolated Runtime Tool Cannot Host

Search for:

```text
bootstrap_bind_all
tool hosting service is unavailable
tool communicator is unavailable
tools are not bound to the current tool subsystem
```

Expected behavior:

- normal isolated tool supervisor calls `bootstrap_bind_all(...)`
- bootstrap rebuilds communicator, conversation store, and hosting-capable
  `ToolSubsystem`
- trusted catalog tools can then call `host_files(...)`

If bootstrap was bypassed, `host_files(...)` should fail fast.

### Final Answer Replaced Progress

This is transport behavior, not necessarily a React failure.

Telegram progress streaming may edit one progress message during the turn, then
replace/finalize it with the final answer. Check:

```text
telegram.stream
telegram.edit
telegram.response rendered
```

If files were emitted during the turn, they may have been sent before final
answer rendering.

### Turn Completed But Persisted Log Is Missing

Search for:

```text
hosted turn_log
turn_log_blocks=
timeline_blocks=
```

If the turn log was recovered from hosting, the harness likely persisted it but
the in-memory state handoff lost it. If neither appears, inspect workflow
completion and exception logs.

## Minimal Log Grep Set

Use this set when debugging a user report:

```text
turn_id=
conversation_id=
[react.v
phase=decision
[agent.packet]
[react.tool.call]
[react.tool.result]
[bundle.tool.
tool_call_id=
[bundle.tool.host_files
[react.artifact.host
[react.artifact.emit
[react.exec.prepare]
[react.exec.contract]
[react.exec.code]
[cache_points:
[anthropic.payload.cache]
chat.files
hosted turn_log
telegram.stream
telegram.render
telegram.send
telegram.edit
[apply_accounting]
```

## What To Record In A Bug Report

Include:

- user-visible request
- tenant/project/bundle id
- conversation id and turn id
- task id/execution id if scheduled
- exact tool id and tool call id
- relevant `[bundle.tool.*]` lines
- relevant hosting markers
- final transport markers
- whether files exist under `OUT_DIR`
- whether hosted metadata exists (`rn`, `key`, `hosted_uri`)

This gives enough information to decide whether the failure is in planning,
tool selection, runtime binding, file materialization, hosting, event emission,
or final transport delivery.
