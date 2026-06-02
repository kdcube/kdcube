---
id: ks:docs/sdk/agents/react/event-source/event-source-README.md
title: "React Event Sources"
summary: "Event-source declarations, ReAct policy bindings, discovery, identity, and feature-flagged rollout."
tags: ["sdk", "agents", "react", "event-source", "policies"]
keywords: ["event_source", "event_source_id", "event_id", "react_phase", "event_policy_id", "tool policy", "timeline projection"]
see_also:
  - ks:docs/sdk/agents/react/event-source/events-blocks-and-rendering-README.md
  - ks:docs/sdk/agents/react/event-source/block-production-README.md
  - ks:docs/sdk/agents/react/tool-call-blocks-README.md
  - ks:docs/sdk/agents/react/event-blocks-README.md
---
# React Event Sources

Event sources make ReAct timeline behavior policy-addressable. A source can be
a tool, a folded external event, or a future user-input source. The event-source
layer does not replace timeline block types. It adds semantic identity and
phase-specific policies around the blocks that already represent what happened.

## Three Layers

| Layer | Field | Meaning |
|---|---|---|
| Source declaration | `event_source_id` | Semantic/policy key, such as `web_tools.web_search` or `react.followup`. |
| Occurrence | `event_id` | One concrete occurrence of that source. For tools this is the `tool_call_id`. |
| Physical block shape | `block.type` | Renderer/storage shape, such as `react.tool.call`, `react.tool.result`, `user.followup`, or `user.attachment`. |

`event_source_id` is the type of event. `event_id` is one occurrence. `block.type`
is how the timeline renderer stores and renders the block.

For tool-backed events, the existing tool fields remain authoritative:

```text
tool_id      == event_source_id
tool_call_id == event_id
```

When a block already has `tool_id`/`tool_call_id` via its tool-call map, code can
derive event identity without duplicating durable fields on every block. When a
non-tool external event is folded into the timeline, it should carry explicit
`event_source_id` and `event_id`.

## Feature Flag

The new policy path is gated by `RuntimeCtx.event_source_pipeline_enabled`.
Bundle configuration can set it through either:

```yaml
react:
  event_source_pipeline:
    enabled: true
```

or:

```yaml
config:
  react:
    event_source_pipeline:
      enabled: true
```

When a ReAct workflow is constructed, the effective runtime context is logged
with this prefix:

```text
[react.runtime_ctx.effective]
```

The log includes `event_source_pipeline_enabled` and
`event_source_pipeline_config.source/raw/effective`, which is the quickest way
to verify whether bundle props or process settings won.

## Declaring Sources

Tool functions can declare their source identity directly:

```python
from kdcube_ai_app.apps.chat.sdk.events import event_source
from kdcube_ai_app.apps.chat.sdk.solutions.react.events import exploration_source_policies

@event_source(
    event_source_id="{alias}.web_search",
    policies=exploration_source_policies(),
    description="Web search results are source rows for ReAct sources_pool.",
    kind="react.tool",
)
async def web_search(...):
    ...
```

`{alias}` is resolved by `EventSourceSubsystem` from the module entry. For a
module registered as alias `web_tools`, `{alias}.web_search` becomes
`web_tools.web_search`.

Modules can also expose declarations explicitly:

```python
from kdcube_ai_app.apps.chat.sdk.events import event_source_declaration

def list_event_sources():
    return [
        event_source_declaration(
            event_source_id="react.followup",
            policies=[],
            description="User followup folded into the active ReAct timeline.",
            kind="react.external",
        )
    ]
```

If `list_event_sources()` returns any declarations, it is the authoritative
source list for that module. Otherwise the subsystem scans decorated functions,
objects, and tool owners.

## Binding Policies

An event source binds policy implementations by phase:

```python
policies=[
    {
        "react_phase": "block_production",
        "event_policy_id": "react.block_production.exploration_results",
    },
    {
        "react_phase": "timeline_projection",
        "event_policy_id": "react.timeline_projection.identity",
    },
]
```

`react_phase` names the ReAct lifecycle spot. `event_policy_id` names a
registered handler for that phase. The handler mutates the supplied target and
returns either the same target or `None`.

Policy functions are registered with phase-specific decorators:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.react.events import block_production_policy

@block_production_policy(event_policy_id="my_bundle.block_production.issue_snapshot")
def issue_snapshot_policy(target, **context):
    target.setdefault("snapshot_refs", []).append("fi:turn_1.snapshots/current.yaml")
    return target
```

Policy IDs should be namespaced. Built-in ReAct policies use `react.*`;
tool-specific policies use their tool namespace, for example
`exec_tools.block_production.exec_result`.

## Supported ReAct Phases

| `react_phase` | Mutable target | Current status |
|---|---|---|
| `tool_call_validation` | One tool-call pre-execution target with `base_params`, mutable `final_params`, state updates, notices, and stop/retry markers. | Implemented for exec and rendering input preparation. |
| `block_production` | One result-production accumulator for `{ok,error,ret}`, result items, artifact rows, source rows, snapshot refs, announce candidates, and notices. | Implemented for external SDK tools and structured custom tools. |
| `timeline_projection` | Mutable timeline blocks before visible render and cache marker assignment. | Phase seam exists; default identity/hide-by-segment policies exist. |
| `announce_production` | Mutable non-durable ANNOUNCE tail material, with access to the full timeline. | Policy type exists; full materialization path is pending. |
| `compaction_projection` | Mutable blocks selected for compaction/summarization. | Phase seam exists; preservation logic is still partly hardcoded. |

## Built-In Source Families

| Source family | Declarations | Policy pack |
|---|---|---|
| ReAct external events | `react.followup`, `react.steer` | Currently identity/stamping only; external-event preservation is still partly hardcoded. |
| Native ReAct tools | `react.write`, `react.memsearch` | Native handlers produce blocks directly; timeline/compaction policies make them addressable. |
| Web tools | `web_tools.web_search`, `web_tools.web_fetch` | `exploration_source_policies()` merges source rows into `sources_pool` and creates the ordinary result item. |
| Browser tools | `browser_tools.open_page`, `click`, `fill`, `scroll`, `status`, `close` | `structured_result_source_policies()` creates JSON/text result items and declared-file rows. |
| Rendering tools | `rendering_tools.write_pptx`, `write_png`, `write_pdf`, `write_docx` | `write_tool_source_policies()` prepares inputs and maps `params.path` to the produced artifact row. |
| Exec tools | `exec_tools.execute_code_python` | Exec validation plus `exec_tools.block_production.exec_result` for report text and artifact rows. |
| Memory tools | `memory.search_memory`, `recent_memories`, `record_memory`, `confirm_memory`, `retire_memory` | Structured-result policies; no file-backed artifact is fabricated for ordinary JSON results. |

## Runtime Rule

Policies do not own transport, queueing, cache marker placement, or final
message rendering. They own the source-specific transformation at the phase they
are bound to. The caller remains responsible for invoking policies at the
correct ReAct spot and for preserving cache-point ordering.

