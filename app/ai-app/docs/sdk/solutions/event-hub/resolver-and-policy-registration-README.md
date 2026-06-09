---
id: ks:docs/sdk/solutions/event-hub/resolver-and-policy-registration-README.md
title: "Event Domain Resolvers And Policies"
summary: "Concrete SDK contract for namespace-owning event domains, their object resolvers, ReAct rendering policies, and the composition bundle that mounts multiple integrations."
status: draft
tags: ["sdk", "solutions", "event-hub", "resolvers", "react", "events", "namespaces", "composition-bundle"]
updated_at: 2026-06-09
keywords:
  [
    "event domain resolver",
    "event source policy",
    "composition bundle",
    "namespace owner",
    "canvas resolver",
    "memory resolver",
    "task resolver",
    "fi resolver",
    "react event policy",
  ]
see_also:
  - ks:docs/sdk/solutions/event-hub/design/resolver-directory-and-operation-routing-README.md
  - ks:docs/sdk/events/namespaces-README.md
  - ks:docs/sdk/events/event-subsystem-README.md
  - ks:docs/sdk/events/external-events-README.md
  - ks:docs/sdk/events/external-event-envelope-README.md
  - ks:docs/sdk/agents/react/event-source/events-blocks-and-rendering-README.md
  - ks:docs/service/comm/conversation-event-bus-and-data-bus-README.md
  - ks:docs/service/comm/data-bus-README.md
---
# Event Domain Resolvers And Policies

This article defines how event-producing domains expose object semantics and
model-facing representations, and how a bundle composes several domains into
one working assistant scene.

## Terms

| Term | Meaning |
| --- | --- |
| Event domain | A subsystem that owns a logical object family, event source family, or namespace. Examples: ReAct artifacts, memory, task issues, canvas. |
| Namespace | The prefix before `:` in a logical ref. Examples: `fi:`, `mem:`, `task:`, `cnv:`. |
| Namespace owner | The domain that mints refs in a namespace and defines their semantics. |
| Resolver | Backend callable owned by the namespace owner. It maps `object_ref + action` to a bounded result. |
| Event source reader | Backend callable owned by the namespace owner. Runtime/policy code can use it to resolve a canonical ref into an event-source payload. It is not automatically a direct model tool. |
| Namespace rehoster | Backend callable owned by the namespace owner. It materializes an external owner ref such as `mem:` or `cnv:` into the ReAct workspace for `react.pull`. |
| Policy | ReAct rendering callable owned by the event domain. It maps events/tool results into blocks, timeline text, compaction text, or ANNOUNCE text. |
| Composition bundle | A bundle that imports available domains, registers their resolvers/policies/tools/widgets, and mounts the UI scene. |

## Core Rules

1. The namespace owner defines object semantics.
2. A composition bundle registers resolvers; it does not redefine them.
3. Canvas stores canonical refs and board metadata; it does not rehost or
   reinterpret non-canvas objects.
4. ReAct rendering uses event policies; it must not depend on UI card code.
5. Exact owner-domain content is imported with `react.pull`, which invokes the
   namespace owner's rehoster and returns a workspace path.
6. A ref keeps its original namespace when it moves between surfaces.
7. Current resolver registration is local Python import plus explicit
   registration. Remote resolver discovery is not implemented.
8. Tool visibility and event visibility are separate even when implemented in
   the same subsystem package. A bundle exposes callable tools through
   `TOOLS_SPECS`; it exposes owner-domain event policies, readers, and
   namespace rehosters through `EVENT_SOURCE_SPECS`.

## Domain Ownership Table

| Domain | Canonical refs or event source | Owner module | Resolver location | Policy location | Default representation owner |
| --- | --- | --- | --- | --- | --- |
| ReAct artifacts | `fi:<artifact-ref>` | ReAct event/artifact layer | `kdcube_ai_app.apps.chat.sdk.solutions.react.events.resolver` | ReAct event policies | ReAct SDK |
| Memory | `mem:<memory-id>` | SDK memory module | `kdcube_ai_app.apps.chat.sdk.context.memory.events.resolver` | memory event policies when present | Memory SDK |
| Task issue story | `task:issues/<issue-id>` | Task/issue subsystem | bundle task module, for example `issues/events/resolver.py` | task issue policies | Task subsystem |
| Canvas board | canvas events and canvas-owned refs | Canvas subsystem | canvas module, for example `canvas/events/resolver.py` | canvas event policies | Canvas subsystem |
| Knowledge source | `ks:<doc-or-source-ref>` | Knowledge subsystem | knowledge resolver module | knowledge/source policies | Knowledge subsystem |

The task-tracker bundle is currently a composition bundle. It imports the
domains above and registers them. It is not the owner of `fi:` or `mem:`.

## Resolver Contract

A resolver answers actions for one namespace.

```text
input:
  object_ref: canonical logical ref
  action: capabilities | describe | preview | open | download | rehost
  request context: tenant, project, user, story/conversation ids, storage handles

output:
  ok: boolean
  object_ref: same canonical ref
  namespace: namespace prefix
  resolver: stable resolver id
  capabilities: action availability map
  payload fields: resolver-specific bounded result
  ui_event: optional event request for mounted UI surfaces
```

Action meanings:

| Action | Meaning | Result shape |
| --- | --- | --- |
| `capabilities` | Report supported actions for this ref. | `{preview, open, download, rehost}` booleans. |
| `describe` | Return resolver identity and compact metadata. | No large object body. |
| `preview` | Return bounded human/UI preview. | Title, summary, mime, compact object payload. |
| `open` | Request a UI surface to focus the object. | Usually returns `ui_event`. |
| `download` | Return bytes, signed URL, or stream metadata. | Transport owned by resolver. |
| `rehost` | Copy bytes/object into another owning subsystem. | New canonical ref in target namespace. |

The resolver must not return implementation-only UI handles as object
identity. For a ReAct artifact card, the identity remains `fi:...`; download
transport is returned only by the `fi:` resolver when `download` is called.

## Policy Contract

Resolvers handle object actions. Policies handle ReAct rendering.

ReAct policy phases:

| Phase | Purpose | Typical output |
| --- | --- | --- |
| `tool_call_validation` | Validate tool call batches before execution. | Accept/reject/annotate tool call. |
| `block_production` | Convert raw events or tool results into durable blocks. | Structured block rows. |
| `timeline_projection` | Render compact turn-visible timeline text. | Short facts, refs, status, revision. |
| `compaction_projection` | Render compact memory of old blocks. | Stable summaries. |
| `announce_production` | Render current high-priority context. | Board maps, focused refs, current story forms. |

Policy functions are registered with ReAct event policy decorators, for
example:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies import (
    announce_event_policy,
    timeline_projection_policy,
)

@timeline_projection_policy("task_tracker.timeline_projection.canvas_state")
def render_canvas_state_timeline(target, **context):
    ...

@announce_event_policy("task_tracker.announce.canvas_map")
def render_canvas_announce(target, **context):
    ...
```

The event source declaration chooses which policy ids apply to each event
source. A domain can ship default policy ids for its own events. A composition
bundle can override or add policy ids only when it intentionally changes the
agent-facing representation for that scene.

## Model Workspace Import Contract

Object resolvers are for UI or service actions: preview, open, download, and
rehost. Model-facing exact content uses the sparse workspace import path.

```text
react.pull(paths=["mem:mem_123"])
  -> namespace owner rehoster for mem:
  -> materialized workspace artifact
  -> returned fi:/physical path
  -> react.read or react.rg against the returned path
```

Owner module example:

```python
from kdcube_ai_app.apps.chat.sdk.events import artifact_namespace_rehoster

@artifact_namespace_rehoster(
    namespace="mem",
    resolver_name="{alias}.memory_rehoster",
)
async def rehost_memory_ref(*, uri, ctx_browser=None, **context):
    ...
```

The rehoster returns materialized artifact rows. It should not pre-render
timeline text unless that text is part of the artifact itself.

Event source readers still exist for runtime/policy reads and custom projection
paths. They follow the same ownership rule as external event ingestion:

```text
external event occurrence
  -> event source declaration
  -> block-production policy
  -> timeline block

runtime/policy owner read
  -> event source reader
  -> event source declaration
  -> block-production policy
  -> read block
```

The event source declaration's `kind` must match the occurrence family:

| `kind` | Use |
| --- | --- |
| `react.tool` | Actual model-callable tool source, for example `canvas.patch`. |
| `react.event_source_reader` | Runtime/policy read source for owner refs, for example memory or canvas internal projections. |
| `react.external` | Authored UI/integration event transported through `external_events[]`. |

`kind` is not tool visibility. Tool visibility still comes from the tool
subsystem. Reader sources are intentionally not called directly by the model.

Do not add namespace-specific branches to ReAct for every subsystem. Add owner
policies for rendering and a namespace rehoster for `react.pull`, then load
that module into the composition bundle's event source subsystem.

Loading that module does not make any callable visible to the model. Tool
visibility is a separate decision made through the tool descriptor.

## Resolver And Policy Are Different

| Question | Resolver answers | Policy answers |
| --- | --- | --- |
| User clicked "open" on a `task:` card. | Which task object to open, and which UI event to emit. | Not involved. |
| Agent sees `[CANVAS BOARD]` in ANNOUNCE. | Not involved. | Canvas announce policy rendered the board. |
| Agent calls `react.pull(paths=["mem:..."])`. | Not involved unless the rehoster delegates to an object resolver. | Not involved; the result is a workspace artifact that can then be read/searched. |
| User drags a `fi:` file to canvas. | `fi:` resolver can later preview/download it. | ReAct artifact policies can render it in timeline/ANNOUNCE if included as event context. |
| Agent calls `task.patch`. | Task tool and task resolver own the task mutation result. | Task tool result policy renders compact timeline and refreshed ANNOUNCE. |
| Old turn is compacted. | Not involved. | Compaction policy renders durable summary. |

## Composition Bundle Topology

Current implementation uses local Python imports. The composition bundle is
the only place where these imports are assembled into one scene.

```text
composition bundle
  entrypoint.py
    imports resolver modules from mounted domains
    imports policy modules from mounted domains
    registers resolver instances/adapters
    exposes bundle operations and widgets

  tools_descriptor.py
    exposes tools from mounted domains

  agents/instructions.py
    concatenates stable additional instructions from mounted domains

  ui
    mounts chat, canvas, memory, task, and other widgets
```

Runtime resolver graph:

```text
canvas/card action or other object action request
  -> composition bundle operation
  -> resolver registry
  -> namespace_for_ref(object_ref)
  -> owner resolver
  -> bounded result or ui_event
```

Runtime ReAct context graph:

```text
browser/widget selected context
  -> external_events[]
  -> event source declaration
  -> block_production policies
  -> timeline_projection policies
  -> announce_production policies
  -> ReAct decision input
```

The composition bundle wires both graphs. The resolver graph is for object
actions. The ReAct context graph is for model-facing rendering.

## Current Local Registration Pattern

Example: task-tracker scene composition.

```python
from kdcube_ai_app.apps.chat.sdk.context.memory.events.resolver import (
    memory_ref_capabilities,
    resolve_memory_ref_action,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.resolver import (
    resolve_event_ref_action,
)

from .canvas.events.resolver import (
    CallableCanvasObjectResolver,
    build_default_canvas_resolver_registry,
)
from .issues.events.resolver import TaskIssueObjectResolver


def build_scene_resolver_registry(store, issue_service, memory_store, memory_scope):
    registry = build_default_canvas_resolver_registry(store)

    registry.register(TaskIssueObjectResolver(service_factory=issue_service))

    registry.register(CallableCanvasObjectResolver(
        namespace="fi",
        resolver="react.event_ref",
        resolver_status="implemented",
        capabilities={"preview": True, "open": False, "download": True, "rehost": False},
        handler=lambda payload, user_id, story_id, action: resolve_event_ref_action(...),
    ))

    registry.register(CallableCanvasObjectResolver(
        namespace="mem",
        resolver="sdk.memory",
        resolver_status="implemented",
        capabilities=memory_ref_capabilities(),
        handler=lambda payload, user_id, story_id, action: resolve_memory_ref_action(...),
    ))

    return registry
```

The adapter is allowed in the composition bundle. The semantic code stays in
the namespace owner module.

## Registration Checklist

For each domain mounted into a scene:

| Required item | Owner implements | Composition bundle does |
| --- | --- | --- |
| Canonical namespace | Yes | Preserves refs unchanged. |
| Resolver | Yes | Imports and registers it. |
| Event source reader | Yes, if `react.read` should resolve refs in this namespace | Imports the owner module through tool/event source specs. |
| ReAct event policies | Yes | Imports policy module and binds policy ids through event source descriptors. |
| Tools | Yes, if the domain is agent-editable | Exposes tool descriptors to the agent. |
| Additional instructions | Yes, if the agent needs stable operational rules | Concatenates instruction blocks. |
| UI widget | Yes, if domain has a surface | Mounts widget or keeps it available for open events. |
| Data Bus handlers | Yes, if domain owns durable state mutations | Registers handler during bundle load. |

## Mounted Widget Rule

An `open` resolver action can return a UI request:

```json
{
  "type": "kdcube.ui.object.open.requested",
  "subject": "ui.object.open.requested",
  "object_ref": "task:issues/ticket_2026-06-08-120000",
  "target_surface": "task_tracker.issue_editor",
  "mode": "focus"
}
```

The target widget owns dirty-state handling. The resolver requests the open; it
does not force the UI to discard edits.

For task editor navigation:

```text
open requested
  -> editor checks current draft
  -> clean: open requested issue
  -> dirty: show Save and open / Discard and open / Cancel
```

## Context Rendering Rule

If the user sends a chat message with context attached, the timeline for that
turn must include the context facts before the user prompt.

Correct order:

```text
[CANVAS STATE]
[CANVAS FOCUS]
[TASK STORY REF]
[MEMORY REF]
[USER MESSAGE]
```

Incorrect order:

```text
[USER MESSAGE]
```

The second form loses the execution context. It makes "this issue" or "this
card" ambiguous for ReAct.

## Anti-Patterns

| Anti-pattern | Correct behavior |
| --- | --- |
| Canvas stores `rn` or `ef` download handles as card identity. | Canvas stores only canonical refs such as `fi:...`, `task:...`, `mem:...`, `cnv:...`. |
| Composition bundle reimplements `mem:` preview. | Import memory resolver and register it. |
| Task resolver lives in `canvas/`. | Task resolver lives in task domain, for example `issues/events/resolver.py`. |
| ReAct `fi:` canonicalization lives in canvas. | ReAct owns `fi:` canonicalization in `react/events/resolver.py`. |
| Timeline policy dumps full JSON bodies. | Timeline policy renders compact facts and refs; ANNOUNCE carries current high-priority view. |
| Agent instructions contain mutable per-turn state. | Mutable state is rendered by event policies into timeline/ANNOUNCE. Instructions describe stable rules only. |
| Widget open silently replaces dirty editor state. | Target widget gates navigation and asks the user. |

## Related Resolver Directory Design

The current runtime uses local Python import plus explicit registration in the
composition bundle. The proposed cross-bundle design for a Redis TTL resolver
directory, direct resolver operation calls, temporary blob exchange, and Data
Bus handoff for async/UI cases is tracked in
[Resolver Directory And Operation Routing Design](design/resolver-directory-and-operation-routing-README.md).

## Implementation Status

Current implemented registration mode:

```text
local Python import -> explicit registration in composition bundle
```

Current non-implemented modes:

```text
remote resolver discovery
resolver marketplace
ad-hoc resolver request/reply topic
automatic resolver loading from arbitrary bundle metadata
```

Do not document a bundle as using remote resolver discovery unless that path is
implemented in platform code and covered by tests.

## Test Requirements

A composition bundle that mounts multiple event domains needs focused tests:

| Test | Assertion |
| --- | --- |
| Resolver path imports | All registered resolver modules import successfully. |
| Namespace dispatch | `task:`, `mem:`, `fi:`, `cnv:` route to the expected resolver id. |
| Canonical ref preservation | Dragging/pinning the same object from different surfaces produces the same `object_ref`. |
| Context event preservation | Live chat send and dry-run render the same context event batch before `[USER MESSAGE]`. |
| Policy rendering | Timeline is compact; ANNOUNCE contains current high-priority view. |
| UI open dirty state | Open request does not discard unsaved edits. |

These tests belong to the composition bundle because they verify that the
scene is wired correctly. Unit tests for resolver semantics belong to the
namespace owner.
