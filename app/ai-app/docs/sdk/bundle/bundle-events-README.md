---
id: ks:docs/sdk/bundle/bundle-events-README.md
title: "Bundle Events"
summary: "Bundle-facing guide for authored UI events, tool-backed event sources, ReAct policies, story-aware widgets, snapshots, and custom artifact namespace rehosters."
status: draft
tags: ["sdk", "bundle", "events", "react", "tools", "ui", "snapshots"]
keywords:
  [
    "bundle events",
    "event_source",
    "event_source_id",
    "event_id",
    "react policies",
    "block production",
    "external_event",
    "story_id",
    "wizard",
    "canvas",
    "snapshot",
    "artifact_namespace_rehoster",
    "event_source_reader",
    "custom namespace",
  ]
see_also:
  - ks:docs/service/comm/bus-routing-and-partitioning-README.md
  - ks:docs/sdk/events/event-subsystem-README.md
  - ks:docs/sdk/events/external-events-README.md
  - ks:docs/sdk/events/external-events-journey-and-handling-README.md
  - ks:docs/sdk/agents/react/event-source/event-source-README.md
  - ks:docs/sdk/agents/react/event-source/block-production-README.md
  - ks:docs/sdk/agents/react/agent-workspace-collboration-README.md
  - ks:docs/sdk/agents/react/react-turn-workspace-README.md
  - ks:docs/sdk/bundle/bundle-agent-integration-README.md
  - ks:docs/sdk/bundle/bundle-client-communication-README.md
  - ks:docs/sdk/bundle/bundle-widget-integration-README.md
---
# Bundle Events

This page is the bundle-developer guide for event-aware applications.

Use it when a bundle has a UI that produces meaningful user actions, tools that
produce structured results, story/wizard snapshots, or custom artifact refs that
must later be materialized for ReAct.

The short model:

```text
tool call                  -> event source occurrence
UI authored external event -> event source occurrence
snapshot ref in event data -> artifact ref, materialized by react.pull when needed
```

Tools are now a special case of event sources. A tool can declare policies that
control how its result becomes timeline blocks and, over time, how those blocks
are projected into visible timeline context, ANNOUNCE, and compaction. A bundle
can also declare its own non-tool event sources for UI events such as wizard
assistance requests, canvas review requests, or saved story snapshots.

## Terms

| Term | Meaning |
|---|---|
| `event_source_id` | Stable semantic source key. For a tool-backed event this is the `tool_id`. For a bundle UI event it is a bundle-owned id such as `demo_workspace.wizard.assistance.requested`. |
| `event_id` | One occurrence of that source. For a tool-backed event this is the `tool_call_id`. For an authored UI event it is assigned by ingress/transport. |
| `react_phase` | ReAct lifecycle spot where a policy runs, such as `block_production`, `timeline_projection`, `announce_production`, or `compaction_projection`. |
| `event_policy_id` | Registered handler for a phase. Built-in ReAct policies use `react.*`; bundle policies should use a bundle namespace. |
| `kind` | Event-source occurrence family. `react.tool` means a ReAct tool-call/result source; `react.event_source_reader` means an owner-domain reader for runtime/policy code; `react.external` means an authored external event. |
| `story_id` | Bundle-owned product-flow instance id, for example one open wizard/canvas/case. It helps policies and tools interpret the event. |
| `event source reader` | Namespace-owner callable registered with `@event_source_reader`; resolves owner refs for runtime/policy code. |
| `artifact namespace rehoster` | A bundle/SDK handler that turns an external owner ref such as `cnv:...` or `mem:...` into a ReAct `fi:` artifact path for `react.pull`. |

For tool-backed events:

```text
tool_id      == event_source_id
tool_call_id == event_id
```

Do not treat every event source as a tool. Agent-visible tools require normal
tool exposure, such as a Semantic Kernel `@kernel_function` loaded through the
tool subsystem. Event-source declarations only attach policy identity. Exact
external owner content is imported with `react.pull` through a namespace
rehoster; source ids such as `canvas.read` are not callable model tools.

## Where Events Fit In A Bundle

```text
main bundle UI
  embeds side chat iframe
  embeds wizard iframe
  embeds canvas/editor iframe
        |
        v
bundle APIs
  save domain state
  host or store domain artifacts
  submit authored external events
        |
        v
conversation external-event lane
  tenant + project + user_id + conversation_id + agent_id
        |
        v
ReAct workflow
  folds events into timeline blocks
  calls tools
  applies event-source policies
        |
        v
timeline / ANNOUNCE / compaction / artifacts
```

The UI should send explicit, meaningful events. Good event boundaries are:

- user asks the assistant to review a wizard section;
- user uploads or deletes an evidence file;
- user saves a wizard draft;
- user presses a canvas "review" or "complete" action;
- user sends a chat message.

Routine keystrokes and high-frequency pointer changes usually belong in bundle
UI state or bundle storage. A later explicit event can include a snapshot ref
that points at the current state.

## Example Product Shape: Case Workspace

Use this support-case workspace shape to understand how wizard, canvas, chat,
snapshots, and events work together. Treat it as an illustration of the SDK
surfaces and event model.

```text
Case Workspace main UI
  |
  +-- Case list / last N cases
  |
  +-- Side chat iframe
  |     target.agent_id = "default.react.agent"
  |     target.story_id = optional current domain story
  |
  +-- Case wizard iframe
  |     story_id = "nmsp:<object_id or draft_id>"
  |     emits saved/assistance/snapshot events
  |
  +-- Canvas iframe
        story_id = "nmsp:<object_id or draft_id>"
        emits review-requested and snapshot events
```

One story is the bundle's unit of product work. A story can be backed by one
conversation, or a bundle can keep an index such as:

```text
user_id + case_id -> last conversation_id + agent_id
```

That index lets the UI reopen the latest relevant conversation when the user
returns to an existing case, while still allowing the user to switch to an older
conversation if the product exposes that control.

## Reactive And Non-Reactive Events

```text
non-reactive event
  "save draft happened"
  "file attached"
  "snapshot available"
  -> retained as ordered context

reactive event
  "review this section"
  "review canvas now"
  "chat message"
  -> can wake or extend the ReAct agent
```

For transported authored external events, reactivity is an occurrence fact in
the payload:

```json
{
  "payload": {
    "target": {
      "agent_id": "default.react.agent",
      "story_kind": "case_wizard",
      "story_id": "nmsp:draft-123"
    },
    "external_event": {
      "event_source_id": "demo_workspace.wizard.assistance.requested",
      "story_id": "nmsp:draft-123",
      "routing": {
        "reactive": true,
        "iteration_credit": 1
      },
      "data": {
        "section_id": "observed_behavior",
        "snapshot_ref": "nmsp:workspace/draft-123/snapshots/current.yaml"
      }
    }
  }
}
```

`agent_id` selects the event lane and the agent target. `story_id` is product
correlation for the bundle and policies. `snapshot_ref` can point at bundle
storage, a domain system, or another artifact namespace; ReAct materializes it
only through a registered namespace rehoster.

## Agent Lane Routing

A bundle can run several internal agents behind one reactive entrypoint. The
conversation event bus routes to the selected lane with `agent_id`.

```text
target.agent_id = "example.reviewer"
  -> tenant/project/user/conversation/example.reviewer lane
  -> @on_reactive_event run(...)
  -> bundle dispatches to reviewer agent
```

In `run(...)`, read the target from the accepted event list or from the bound
request context:

```python
@on_reactive_event
async def run(self, **params):
    events = params.get("external_events") or []
    agent_id = next(
        (
            event.get("agent_id")
            for event in events
            if isinstance(event, dict) and event.get("agent_id")
        ),
        "default.react.agent",
    )
    return await self.route_to_agent(agent_id, **params)
```

Use one target `agent_id` for one submitted package. If a UI needs to address
two agents, submit two explicit events/packages so each agent lane keeps its own
ordering.

## Defining Bundle Event Sources

A bundle can define event sources in an event module loaded into ReAct:

```text
my.bundle@1-0/
  events_descriptor.py
  events/
    case_events.py
  orchestrator/
    workflow.py
```

`events_descriptor.py`:

```python
EVENT_SOURCE_SPECS = [
    {"ref": "events/case_events.py", "alias": "case_events"},
]
```

`orchestrator/workflow.py`:

```python
from .. import events_descriptor, tools_descriptor

react = self.build_react(
    scratchpad,
    mod_tools_spec=tools_descriptor.TOOLS_SPECS,
    event_source_specs=events_descriptor.EVENT_SOURCE_SPECS,
)
```

`events/case_events.py`:

```python
from kdcube_ai_app.apps.chat.sdk.events import event_source_declaration

def list_event_sources():
    return [
        event_source_declaration(
            event_source_id="demo_workspace.wizard.assistance.requested",
            kind="react.external",
            reactive=True,
            iteration_credit=1,
            policies=[
                {
                    "react_phase": "timeline_projection",
                    "event_policy_id": "demo_workspace.timeline_projection.wizard_event",
                },
                {
                    "react_phase": "announce_production",
                    "event_policy_id": "demo_workspace.announce.current_snapshot",
                },
            ],
            description="User asked the assistant to review a wizard section.",
        ),
        event_source_declaration(
            event_source_id="demo_workspace.canvas.review.requested",
            kind="react.external",
            reactive=True,
            iteration_credit=2,
            policies=[
                {
                    "react_phase": "timeline_projection",
                    "event_policy_id": "demo_workspace.timeline_projection.canvas_event",
                },
            ],
            description="User asked the assistant to review the canvas.",
        ),
        event_source_declaration(
            event_source_id="demo_workspace.snapshot.available",
            kind="react.external",
            reactive=False,
            policies=[
                {
                    "react_phase": "timeline_projection",
                    "event_policy_id": "demo_workspace.timeline_projection.snapshot_ref",
                },
                {
                    "react_phase": "announce_production",
                    "event_policy_id": "demo_workspace.announce.current_snapshot",
                },
            ],
            description="A current story snapshot is available for the agent.",
        ),
    ]
```

The declaration gives the source a name and policy bindings. The transported
event occurrence still carries the effective `routing.reactive` value.

## Tools As Event Sources

Bundle tools can define event-source policies too. This lets the tool own how
its result becomes timeline blocks.

```python
from kdcube_ai_app.apps.chat.sdk.events import event_source

@event_source(
    event_source_id="{alias}.find_related_cases",
    kind="react.tool",
    reactive=False,
    policies=[
        {
            "react_phase": "block_production",
            "event_policy_id": "demo_workspace.block_production.related_cases",
        },
        {
            "react_phase": "timeline_projection",
            "event_policy_id": "demo_workspace.timeline_projection.related_cases",
        },
    ],
    description="Find related cases and produce bounded source rows.",
)
async def find_related_cases(...):
    ...
```

Current first implemented policy family is `block_production`: it receives the
tool result envelope and appends the blocks/rows/artifact refs that should be
stored on the timeline. The same source can also bind later phases:

| Phase | Bundle use |
|---|---|
| `tool_call_validation` | Normalize/validate tool params before execution. |
| `block_production` | Convert `{ok, error, ret}` and files into durable timeline blocks/artifact rows. |
| `timeline_projection` | Mutate already-stored blocks before visible timeline rendering. |
| `announce_production` | Add non-durable current-state context to ANNOUNCE, for example a current snapshot summary. |
| `compaction_projection` | Prepare blocks for compaction/summarization. |

See [React Event Sources](../agents/react/event-source/event-source-README.md)
for exact phase status and handler signatures.

## Snapshots And External Artifact Refs

A snapshot is a story/wizard/canvas state artifact. The bundle usually owns the
domain state and may store the authoritative copy in bundle storage, a database,
object storage, or an external system. Events can carry a snapshot ref:

```json
{
  "event_source_id": "demo_workspace.snapshot.available",
  "story_id": "nmsp:draft-123",
  "routing": { "reactive": false },
  "data": {
    "snapshot_ref": "nmsp:workspace/draft-123/snapshots/current.yaml",
    "summary": "Draft has title, observed behavior, two evidence files, and missing expected result."
  }
}
```

The event can be stored as timeline context with only metadata and refs. When
the agent needs the actual bytes, it calls:

```json
{
  "paths": ["nmsp:workspace/draft-123/snapshots/current.yaml"]
}
```

`react.pull` invokes the registered namespace rehoster and returns ordinary
ReAct paths, for example:

```json
{
  "source_ref": "nmsp:workspace/draft-123/snapshots/current.yaml",
  "logical_path": "fi:turn_123.snapshots/nmsp/workspace/draft-123/current.yaml",
  "physical_path": "turn_123/snapshots/nmsp/workspace/draft-123/current.yaml"
}
```

The returned rows are the continuation contract. Agents should continue from
the returned `logical_path` or `physical_path`.

## Registering A Custom Artifact Namespace

Register a namespace rehoster in a loaded tool module or loaded event module.
The rehoster must know the ReAct workspace layout and choose the destination by
artifact meaning. Read:

- [Agent Workspace Collaboration](../agents/react/agent-workspace-collboration-README.md)
- [ReAct Turn Workspace](../agents/react/react-turn-workspace-README.md)
- [Files vs Outputs](../agents/react/files-vs-outputs-README.md)

Destination map:

| Source artifact meaning | ReAct destination |
|---|---|
| Story/wizard/canvas snapshot | `fi:turn_<id>.snapshots/<path>` / `turn_<id>/snapshots/<path>` |
| Evidence or domain attachment | `fi:turn_<id>.external.<event_kind>.attachments/<event_id>/<name>` / `turn_<id>/external/<event_kind>/attachments/<event_id>/<name>` |
| Editable project/workspace file | `fi:turn_<id>.files/<workspace_scope>/<path>` / `turn_<id>/files/<workspace_scope>/<path>` |
| Produced report/export/rendered artifact | `fi:turn_<id>.outputs/<artifact_scope>/<path>` / `turn_<id>/outputs/<artifact_scope>/<path>` |

Example:

```python
from kdcube_ai_app.apps.chat.sdk.events import artifact_namespace_rehoster
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import resolve_artifact_path
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    build_external_attachment_logical_path,
    build_external_attachment_physical_path,
    build_logical_artifact_path,
    build_physical_artifact_path,
)

@artifact_namespace_rehoster(
    namespace="nmsp",
    description="Materialize case workspace refs for ReAct tools.",
)
async def rehost_nmsp_workspace_ref(*, ref, key, ctx_browser, outdir, **_):
    turn_id = ctx_browser.runtime_ctx.turn_id

    if key.endswith("/snapshots/current.yaml"):
        relpath = f"nmsp/{key}"
        logical_path = build_logical_artifact_path(
            turn_id=turn_id,
            namespace="snapshots",
            relpath=relpath,
        )
        physical_path = build_physical_artifact_path(
            turn_id=turn_id,
            namespace="snapshots",
            relpath=relpath,
        )
    else:
        event_id = "nmsp_workspace"
        event_kind = "external_ref"
        relpath = f"nmsp/{key}"
        logical_path = build_external_attachment_logical_path(
            turn_id=turn_id,
            kind=event_kind,
            message_id=event_id,
            relpath=relpath,
        )
        physical_path = build_external_attachment_physical_path(
            turn_id=turn_id,
            kind=event_kind,
            message_id=event_id,
            relpath=relpath,
        )

    target = resolve_artifact_path(outdir, physical_path, prefer_existing=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(load_bytes_from_bundle_storage_or_domain_system(key))

    return {
        "materialized": [{
            "source_ref": ref,
            "logical_path": logical_path,
            "physical_path": physical_path,
            "file_count": 1,
        }]
    }
```

`nmsp` is an example owner-domain namespace. A bundle can register another
compact URI-style namespace if that namespace has a loaded rehoster in the
ReAct `EventSourceSubsystem`.

## End-To-End Wizard / Canvas Flow

```text
1. User opens a case wizard
   UI loads case state from bundle API
   bundle maps user_id + case_id -> conversation_id + agent_id

2. User edits fields and attaches evidence
   bundle API saves domain state and files
   UI may submit non-reactive events such as file attached or snapshot available

3. User presses "review this section"
   UI submits reactive external_event with story_id and snapshot_ref
   ingress appends event to the agent lane and wakes/continues ReAct

4. ReAct folds event into timeline
   event_source_id selects bundle policies
   policy renders concise context and preserves refs

5. Agent needs snapshot bytes
   agent calls react.pull(paths=["<namespace>:..."])
   namespace rehoster materializes snapshot as fi:turn_<id>.snapshots/...

6. Agent responds in side chat or produces artifacts
   bundle UI can render the response next to the wizard/canvas
```

Canvas follows the same model. The canvas stores current state through bundle
APIs. A reactive "review canvas" event should carry a snapshot ref and focused
selection metadata.

## UI / UX Guidance

- Use `story_id` for the user-story instance: one wizard draft, one existing
  case, one canvas session, or one focused product flow.
- Use `story_kind` for the product flow type, such as `case_create`,
  `case_edit`, or `canvas_review`.
- Use `agent_id` from the start. It scopes the event lane and lets one bundle
  run a default agent plus specialized story agents.
- Keep high-frequency UI state in bundle storage or the domain system.
- Emit authored events for meaningful transitions: saved, uploaded, deleted,
  assistance requested, review requested, snapshot available.
- Carry refs to snapshots/artifacts in event data. Materialize through
  `react.pull` when the agent needs bytes.
- Keep snapshot data in the source of truth the bundle controls; rehost into
  ReAct artifact space when the agent needs to read or search it.

## Minimal Bundle Checklist

1. Add `events_descriptor.py` for bundle event modules.
2. Pass `event_source_specs` into `BaseWorkflow.build_react(...)`.
3. Declare bundle UI event sources with `event_source_declaration(...)`.
4. Add tool `@event_source(...)` declarations when tools need custom policy
   behavior.
5. Register `@artifact_namespace_rehoster(...)` for custom owner refs such as
   `nmsp:...`, `mem:...`, or `cnv:...`.
6. Make UI events carry `payload.target.agent_id`, `story_kind`, `story_id`,
   and `external_events[].event_source_id`.
7. Use `external_events[].reactive=true` only for events intended
   to wake or continue the agent.
8. Return and preserve `logical_path` / `physical_path` rows from rehosters.
