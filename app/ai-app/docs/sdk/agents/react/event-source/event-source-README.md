---
id: ks:docs/sdk/agents/react/event-source/event-source-README.md
title: "React Event Sources"
summary: "Event-source declarations, ReAct policy bindings, discovery, identity, and feature-flagged rollout."
tags: ["sdk", "agents", "react", "event-source", "policies"]
keywords: ["event_source", "event_source_id", "event_id", "react_phase", "event_policy_id", "tool policy", "timeline projection"]
see_also:
  - ks:docs/sdk/events/external-event-envelope-README.md
  - ks:docs/sdk/events/external-events-README.md
  - ks:docs/sdk/events/external-events-journey-and-handling-README.md
  - ks:docs/arch/proc/events-orchestration-README.md
  - ks:docs/sdk/events/event-subsystem-README.md
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

A tool call is the first implemented special case of an event source. The tool
is still called through the normal tool subsystem; the event-source layer only
describes how that tool occurrence is validated, converted into timeline blocks,
projected into rendered context, announced, or prepared for compaction.

## Three Layers

| Layer | Field | Meaning |
|---|---|---|
| Source declaration | `event_source_id` | Semantic/policy key, such as `web_tools.web_search` or `react.followup`. |
| Occurrence | `event_id` | One concrete occurrence of that source. For tools this is the `tool_call_id`. |
| Accepted event type | `event.type` | Semantic event shape, such as `event.user.prompt`, `event.user.attachment.*`, `event.user.followup`, `event.user.steer`, `event.external`, `event.snapshot`, or `event.canvas`. |
| Physical block shape | `block.type` | Current renderer/storage projection, such as `react.tool.call`, `react.tool.result`, `user.prompt`, `user.attachment.*`, `user.followup`, `user.steer`, `event.external`, or `event.snapshot`. |

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

An authored external event also has a `logical_path` in the `ev:` namespace,
for example `ev:turn_<id>.events/<event_path>`. That path identifies the event
object on the timeline and is readable with `react.read`, like `tc:` for tool
call/result objects. It is not a file/artifact namespace and is not passed to
`react.pull` or `react.checkout`. If the event body is hosted or points to
files, the pullable refs live in `hosted_uri`, `payload.event_ref`, or inside
`payload.event`.

From the event-source perspective, a tool call and an authored external event
are both event occurrences:

| Occurrence | Accepted event type | Default block group / projection |
|---|---|
| User prompt | `event.user.prompt` | Compatibility projection currently emits `user.prompt`. |
| User attachment | `event.user.attachment.*` | Compatibility projection currently emits `user.attachment.*`. |
| User followup | `event.user.followup` | Compatibility projection currently emits `user.followup`. |
| User steer | `event.user.steer` | Compatibility projection currently emits `user.steer` / control path. |
| Tool call | Tool occurrence uses `tool_id == event_source_id` and `tool_call_id == event_id`. | `react.tool.call` plus one or more `react.tool.result` / artifact blocks. |
| Authored domain event | `event.external` | One `event.external` block at the event `ev:` path, no blocks, or policy-produced blocks. |
| Snapshot event | `event.snapshot` | One `event.snapshot` block at the event `ev:` path, or policy-produced blocks. |
| Canvas state event | `event.canvas` | One `event.canvas` block at the event `ev:` path, or policy-produced blocks. |

Custom `block_production` policies may expand an external event into a richer
group, such as additional payload/artifact blocks. The default event block body
uses the tool-result-like shape: `ok`, `status`, optional `error`, optional
`ret`, and optional `surfaces`. `payload.event` becomes `ret`;
`payload.event_ref` becomes `ret.event_ref`. The default producer also extracts
standard tool-result surfaces from that `ret` and stores them in `surfaces`, including
exploration rows, hosted/artifact rows, declared file rows, snapshot refs,
ANNOUNCE candidates, and notices. Those blocks remain grouped by `event_id` and
addressed by `event_source_id`.

`event.snapshot` and `event.canvas` are deliberately different. A snapshot is a
read-only projection/ref produced from external or bundle state. ReAct may
pull/read the referenced payload, but it should not patch the snapshot as
authoritative state. A canvas is the mutually writable JSON state surface; user
and agent edits append new `event.canvas` occurrences with later revisions.

Authored external events arrive through chat ingress as
top-level `external_events[]` and are retained first in the per-conversation
event lane. The accepted event envelope includes `type`, `event_source_id`,
`event_id`, `logical_path`, optional `hosted_uri`, `reactive`, `agent_id`,
`story_id`, and `payload`. The transport, reactivity, and story-correlation
contract is documented in [External Events](../../../events/external-events-README.md);
the concrete accepted event shape is documented in
[External Event Envelope](../../../events/external-event-envelope-README.md).

When a retained event starts processor work, the ready queue carries
`ExternalEventLaneWakeup`; proc resolves that wakeup to the lane event's stored
`ExternalEventPayload` before the bundle is invoked. ReAct policies see the
event only after transport and processor resolution have already happened.

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
    reactive=False,
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
            reactive=True,
        )
    ]
```

If `list_event_sources()` returns any declarations, it is the authoritative
source list for that module. Otherwise the subsystem scans decorated functions,
objects, and tool owners.

For authored external events, `reactive` and `iteration_credit` are declaration
defaults:

```python
event_source_declaration(
    event_source_id="my_app.wizard.assistance.requested",
    kind="react.external",
    reactive=True,
    iteration_credit=2,
    policies=[...],
)
```

`reactive=True` is declaration metadata/default for code that authors
occurrences of this source. A transported external event must still carry its
effective `external_events[].reactive` value; the runtime does not wake
ReAct from a declaration alone. `iteration_credit=2` means one live occurrence
that is explicitly reactive grants two extra iterations before runtime caps are
applied. An occurrence-level credit field can override the declaration default.

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

@block_production_policy(event_policy_id="my_bundle.block_production.document_snapshot")
def document_snapshot_policy(target, **context):
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
| `timeline_projection` | Phase-local mutable timeline view before visible render and cache marker assignment. | Implemented in render; default identity, hide-by-segment, event, snapshot, and canvas render policies exist. |
| `announce_production` | Mutable non-durable ANNOUNCE tail material, with access to a cloned full timeline context. | Implemented in render tail after cache markers. |
| `compaction_projection` | Mutable block view selected for compaction/summarization. | Phase seam exists; default event/snapshot/canvas render compaction exists, while some preservation logic is still partly hardcoded. |

## File-Producing Sources

File production is policy-addressable in the block-production phase, but file
preview generation is not implicit. A source can register file rows through
`artifact_rows`, `declared_file_items`, or `hosted_artifacts`; the shared
artifact builder will preserve the artifact metadata and make the logical
artifact path visible. That is enough for ReAct to recover exact content later
with `react.read(paths=["fi:..."])`.

Only producers that already have a bounded text preview should provide
`text_preview`. Exec does this during its own result production because it has
the generated files locally and can format a safe preview at that moment.
Rendering tools and hosted-file/event sources may remain metadata-only unless
they explicitly provide `text_preview` or inline text. If a source provides a
pre-rendered preview, mark the resulting artifact text block with
`meta.projection.already_rendered=true` and
`meta.projection.format="text_file_preview.v1"` so timeline rendering does not
wrap/line-number the preview again.

## Built-In Source Families

| Source family | Declarations | Policy pack |
|---|---|---|
| ReAct external events | `react.message`, `react.user_attachment`, `react.followup`, `react.steer`, `react.external_event` | Built-in default block producers for prompt, attachment, followup, steer, and generic authored external events; snapshot/canvas defaults are selected by accepted event `type`; some legacy compaction preservation remains partly hardcoded. |
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
