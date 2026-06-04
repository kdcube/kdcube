---
id: ks:docs/sdk/events/event-subsystem-README.md
title: "SDK Events Subsystem"
summary: "Shared event-source declarations and discovery used by tools today and by broader SDK event flows over time."
status: draft
tags: ["sdk", "events", "event-source", "tools", "react"]
keywords:
  [
    "event_source",
    "event_source_id",
    "event_id",
    "EventSourceSubsystem",
    "tool-backed event source",
    "event policies",
    "artifact_namespace_rehoster",
    "namespace rehoster",
    "react.pull",
  ]
see_also:
  - ks:docs/sdk/bundle/bundle-events-README.md
  - ks:docs/sdk/events/external-event-envelope-README.md
  - ks:docs/sdk/events/external-events-README.md
  - ks:docs/arch/proc/events-orchestration-README.md
  - ks:docs/sdk/agents/react/agent-workspace-collboration-README.md
  - ks:docs/sdk/agents/react/react-turn-workspace-README.md
  - ks:docs/sdk/agents/react/files-vs-outputs-README.md
  - ks:docs/sdk/agents/react/event-source/event-source-README.md
  - ks:docs/sdk/agents/react/event-source/block-production-README.md
  - ks:docs/sdk/tools/tool-subsystem-README.md
  - ks:docs/sdk/tools/custom-tools-README.md
  - ks:docs/sdk/agents/react/design/timeline-events-transport-lifecycle-README.md
---
# SDK Events Subsystem

The SDK events subsystem provides shared event-source identity and discovery.
ReAct is the first consumer, but the model is wider than ReAct: the same source
identity can describe tool calls, external UI events, authored external events,
and future event-producing SDK surfaces.

## Core Model

An event source has two identities:

| Term | Meaning |
|---|---|
| `event_source_id` | Stable semantic source key, such as `web_tools.web_search`, `react.followup`, or `bundle.wizard.field_changed`. |
| `event_id` | One occurrence of that source. For tool-backed events this is the tool call id. |

A tool call is the first implemented special case of an event source:

```text
tool_id      == event_source_id
tool_call_id == event_id
```

The tool still executes through the normal tool subsystem. Event-source metadata
only tells downstream consumers how to validate, produce, project, announce, or
compact the occurrence.

Event-source policy bindings are the integration contract. For ReAct, a source
can declare policies that find result surfaces, convert them to timeline
blocks, project those blocks for model-visible rendering, append ANNOUNCE tail
material, or prepare compaction views. The shared SDK events subsystem only
discovers and names those policies; it does not itself render timeline text or
generate file previews.

Authored UI/domain events use the same source/occurrence model. Their accepted
transport envelope is documented in
[External Event Envelope](external-event-envelope-README.md). In that envelope,
`event_source_id` selects policies, `event_id` identifies the occurrence, and
`logical_path` is the `ev:` path of the event object on the turn timeline.
User prompts, user attachments, followups, and steers are built-in external
event types (`event.user.prompt`, `event.user.attachment.*`,
`event.user.followup`, `event.user.steer`) even when they are authored through
the existing chat message or continuation wire fields.

## Declaration

Event sources are declared with `@event_source(...)` or returned from
`list_event_sources()` / explicit event-spec modules.

```python
from kdcube_ai_app.apps.chat.sdk.events import event_source

@event_source(
    event_source_id="{alias}.search",
    policies=[
        {
            "react_phase": "block_production",
            "event_policy_id": "react.block_production.generic_result_item",
        },
    ],
    kind="react.tool",
    reactive=False,
)
async def search(...):
    ...
```

Policy bindings are consumer-specific. Today the supported consumer is ReAct,
so bindings use `react_phase` and `event_policy_id`. The shared SDK events
subsystem does not define ReAct timeline behavior by itself.

For non-tool external events, the declaration can also define ReAct admission
defaults:

```python
from kdcube_ai_app.apps.chat.sdk.events import event_source_declaration

def list_event_sources():
    return [
        event_source_declaration(
            event_source_id="my_app.wizard.assistance.requested",
            kind="react.external",
            reactive=True,
            iteration_credit=2,
            policies=[
                {
                    "react_phase": "timeline_projection",
                    "event_policy_id": "my_app.timeline_projection.wizard_event",
                },
            ],
        )
    ]
```

`reactive` is declaration metadata/default for code that authors occurrences of
this source. Transported external-event occurrences must still carry their
effective `external_events[].reactive` value; the runtime does not
silently wake ReAct from a declaration alone. `iteration_credit` is the default
live-turn credit for one occurrence that is explicitly reactive. An accepted
occurrence may override the declaration default with an occurrence-level credit
field; runtime caps always apply last.

## Discovery

`EventSourceSubsystem` discovers event declarations from:

- loaded tool modules;
- explicit event source modules;
- declarations returned by `list_event_sources()`;
- first-party built-in ReAct event modules.

The subsystem validates duplicate `event_source_id` values and lets consumers
look up declarations by source id or, when a durable block carries
`event_source_id`, by block.

## Artifact Namespace Rehosters

The same module discovery path can register artifact namespace rehosters. A
rehoster is different from an event source: it turns a custom namespace
artifact URI such as `ext:...` into a normal ReAct `fi:` artifact ref by
copying bytes into the current turn artifact surface. The rehoster owns the
mapping from the custom URI to the ReAct artifact namespace.

A rehoster must be aware of the ReAct workspace and artifact surfaces. It does
not merely download bytes; it chooses where the artifact belongs in the ReAct
model and returns the resulting `fi:` logical path plus `OUTPUT_DIR`-relative
physical path. Read
[Agent Workspace Collaboration](../agents/react/agent-workspace-collboration-README.md)
and [Files vs Outputs](../agents/react/files-vs-outputs-README.md) before
writing a bundle rehoster.

### Where rehosters are discovered

`@artifact_namespace_rehoster` is discovered only from modules that are loaded
into the ReAct `EventSourceSubsystem`. There are two normal bundle paths:

1. A tool module listed in `tools_descriptor.py`.
2. An event module listed in `events_descriptor.py` and passed to
   `BaseWorkflow.build_react(..., event_source_specs=...)`.

Tool modules are scanned automatically because `ToolSubsystem` builds
`EventSourceSubsystem` from its loaded tool modules. Event-only modules are
loaded from `event_source_specs`; a descriptor file by itself is not scanned
unless the workflow passes those specs into `build_react`.

Bundle shape:

```text
my.bundle@1-0/
  tools_descriptor.py
  events_descriptor.py
  events/
    my_artifacts.py
  orchestrator/
    workflow.py
```

`events_descriptor.py`:

```python
EVENT_SOURCE_SPECS = [
    {"ref": "events/my_artifacts.py", "alias": "my_artifacts"},
]
```

Workflow handoff:

```python
from .. import events_descriptor, tools_descriptor

react = self.build_react(
    scratchpad,
    mod_tools_spec=tools_descriptor.TOOLS_SPECS,
    event_source_specs=events_descriptor.EVENT_SOURCE_SPECS,
)
```

Inside `events/my_artifacts.py`:

- snapshot-like state should materialize as `fi:turn_<id>.snapshots/...`
- external evidence/files should materialize as
  `fi:turn_<id>.external.<event_kind>.attachments/<event_id>/<name>`
- editable workspace/project state should materialize as
  `fi:turn_<id>.files/...`
- produced deliverables/reports should materialize as `fi:turn_<id>.outputs/...`

The destination is semantic:

| Source artifact meaning | ReAct destination |
|---|---|
| Story/wizard state snapshot | `fi:turn_<id>.snapshots/<path>` / `turn_<id>/snapshots/<path>` |
| Evidence or domain attachment | `fi:turn_<id>.external.<event_kind>.attachments/<event_id>/<name>` / `turn_<id>/external/<event_kind>/attachments/<event_id>/<name>` |
| Editable project/workspace file | `fi:turn_<id>.files/<workspace_scope>/<path>` / `turn_<id>/files/<workspace_scope>/<path>` |
| Produced report/export/rendered artifact | `fi:turn_<id>.outputs/<artifact_scope>/<path>` / `turn_<id>/outputs/<artifact_scope>/<path>` |

The returned paths are the agent contract. After `react.pull(paths=["ext:..."])`,
the agent should use the returned `logical_path` or `physical_path`; it should
not derive a replacement path from the external URI.

```python
from kdcube_ai_app.apps.chat.sdk.events import artifact_namespace_rehoster

@artifact_namespace_rehoster(
    namespace="ext",
    description="Materialize external artifact refs for ReAct tools.",
)
async def rehost_external_ref(*, ref, key, ctx_browser, outdir, **context):
    ...
    return {
        "materialized": [{
            "source_ref": ref,
            "logical_path": "fi:turn_<id>.snapshots/ext/path.yaml",
            "physical_path": "turn_<id>/snapshots/ext/path.yaml",
        }]
    }
```

The same module can also expose an explicit list when it does not want the
subsystem to scan all top-level objects:

```python
def list_artifact_namespace_rehosters():
    return [rehost_external_ref]
```

`react.pull` calls registered rehosters before the normal `fi:` hydration path.
A registered rehoster is required for each external namespace. This keeps custom
namespace artifact URIs such as `ext:...` explicit on the timeline while giving
agents a standard way to materialize them when they need the actual file. The
pull result is the contract consumed by the agent: it includes the source ref
and the resolved/rehosted `logical_path` / `physical_path` rows to use next.

## File Rows And Preview Text

For ReAct, file-producing event sources usually integrate by declaring
block-production policies that produce file rows:

- `artifact_rows`
- `declared_file_items`
- `hosted_artifacts`

Those rows let the shared ReAct artifact builders preserve logical paths,
hosted refs, physical paths, MIME, size, and other artifact metadata. They do
not automatically imply that the file body is copied into model-visible text.

A source should provide `text_preview` only when it already has the bytes and
can create a bounded, source-owned preview during block production. Exec does
this for text files produced in the isolated runtime. Other sources can stay
metadata-only; ReAct can later call `react.read(paths=["fi:..."])` on the
visible logical artifact path when exact content is needed.

If a source emits a pre-rendered file preview block, the block should carry:

```json
{
  "meta": {
    "projection": {
      "phase": "block_production",
      "format": "text_file_preview.v1",
      "already_rendered": true
    }
  }
}
```

That marker tells ReAct timeline rendering not to wrap or line-number the same
preview a second time.

## Boundary

The shared events subsystem owns:

- event-source declarations;
- identity naming;
- source discovery;
- policy binding lookup.
- artifact namespace rehoster discovery.

It does not own:

- transport delivery;
- queueing or turn ownership;
- processor wakeup resolution;
- final renderer block shapes;
- ReAct cache marker placement;
- ANNOUNCE text formatting.

Those remain responsibilities of the consuming runtime. For ReAct, see the
event-source phase documents under `docs/sdk/agents/react/event-source/`.
For conversation-scoped authored events that arrive through chat ingress, see
[External Events](external-events-README.md).
