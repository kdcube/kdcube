---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-README.md
title: "External Events"
summary: "Semantic and transport model for conversation-scoped external events, including reactive/non-reactive behavior, story targeting, Redis transport, and ReAct folding."
status: draft
tags: ["sdk", "events", "external-events", "ingress", "react", "conversation"]
keywords:
  [
    "external_event",
    "event_source_id",
    "reactive",
    "non reactive",
    "story_id",
    "payload.target",
    "conversation external event",
    "redis external event source",
    "react event source",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/namespaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-event-envelope-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-journey-and-handling-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/ingress/events-inception-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proc/events-orchestration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-source/event-source-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/shared-timeline-event-bus-steer-followup-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/runtime-configuration-README.md
---

# External Events

External events are conversation-scoped facts or intents that arrive from a UI,
transport, webhook, or system component and may be folded into a ReAct
conversation. User prompts, attachments, followups, and steer controls are
built-in external event types; `external_events[]` is the authored
event transport for both built-in user events and bundle/domain events. Tools
are not transported through ingress, but they use the same event-source identity
model once ReAct invokes them:

```text
event_source_id = stable semantic source
event_id        = one occurrence
```

The ingress-level mechanics are documented in
[Ingress Event Inception](../../arch/ingress/events-inception-README.md). This
document explains the SDK semantics and how they connect to ReAct event sources.
The end-to-end transport and handling journey is documented in
[External Events Journey And Handling](external-events-journey-and-handling-README.md).

## Wire Shape

Clients send client-authored events through the normal chat ingress request:

```json
{
  "message": {
    "conversation_id": "conv_...",
    "turn_id": "turn_...",
    "payload": {
      "target": {
        "agent_id": "default.react.agent"
      },
      "external_events": [
        {
          "event_id": "evt_canvas_snapshot_001",
          "type": "event.snapshot",
          "event_source_id": "task_tracker.canvas.snapshot",
          "logical_path": "ev:turn_123.events/task-tracker/snapshots/draft-123/canvas/latest",
          "hosted_uri": "cnv:snapshots/draft-123/canvas/latest",
          "reactive": false,
          "agent_id": "default.react.agent",
          "story_id": "task:draft-123",
          "payload": {
            "mime": "application/json",
            "event_ref": "cnv:snapshots/draft-123/canvas/latest"
          }
        },
        {
          "event_id": "evt_prompt_001",
          "type": "event.user.prompt",
          "event_source_id": "react.message",
          "logical_path": "ev:turn_123.events/chat/user-prompt/evt_prompt_001",
          "hosted_uri": null,
          "reactive": true,
          "agent_id": "default.react.agent",
          "story_id": "task:draft-123",
          "payload": {
            "mime": "text/plain",
            "event": {
              "text": "Review this selected area and suggest the next step.",
              "context_refs": [
                "ev:turn_123.events/task-tracker/snapshots/draft-123/canvas/latest"
              ]
            }
          }
        }
      ]
    }
  }
}
```

Field roles:

| Field | Meaning |
|---|---|
| `external_events[]` | Plural list of client-authored event occurrences. The target protocol does not use a singular event field. |
| `external_events[].type` | Structural event block shape, for example `event.user.prompt`, `event.snapshot`, or `event.external`. |
| `external_events[].event_source_id` | Semantic event source, used for event-source discovery and policy lookup. |
| `external_events[].logical_path` | `ev:` path of this event object on the turn timeline. |
| `external_events[].hosted_uri` | Optional external URI for a hosted copy of the event payload/body. |
| `external_events[].reactive` | Effective occurrence flag when this event may wake or continue ReAct. Absence/false means no wake. |
| `external_events[].story_id` | Optional product/story correlation carried with the event. |
| `external_events[].payload` | Event body descriptor: `payload.mime` plus either inline `payload.event` or pullable `payload.event_ref`. |
| `payload.target.agent_id` | Target agent lane. |
| `turn_id` / `active_turn_id` | Platform turn routing fields. Do not hide these inside `payload.target`. |

The canonical envelope and examples for snapshot, file upload, and text
selection events are in
[External Event Envelope](external-event-envelope-README.md).
The logical reference namespace model for `ev:`, `ar:`, `fi:`, `cnv:`,
`task:`, `mem:`, and related refs is in [Logical Reference Namespaces](namespaces-README.md).

`ev:` is event identity, not artifact storage. ReAct can read the event object
with `react.read(paths=["ev:..."])`, similar to `tc:` tool-call/result refs.
ReAct should not pass `ev:` to `react.pull` or `react.checkout`. When the event
points to material that must become local, use the event's `hosted_uri`,
`payload.event_ref`, or artifact refs carried inside `payload.event`.

When ReAct folds an accepted event, block-production policies decide whether
the event becomes timeline material. Built-in user event policies project
`event.user.prompt`, `event.user.followup`, `event.user.steer`, and
`event.user.attachment.*` into the current ReAct user block shapes (`ar:` for
chat text/control blocks and `fi:` for user attachment refs). Generic/domain,
snapshot, and canvas event policies emit event blocks at the accepted event's
`ev:` path. Those event block bodies mirror a tool-result envelope:
`payload.event` becomes `ret`, errors become `error`, and recognized composite
result surfaces are preserved under `surfaces`. Current built-in surface
extractors understand exploration/source rows, hosted artifact rows, declared
file rows, snapshot refs, ANNOUNCE candidates, and notice rows.

Registered sources can override the default, including with
`react.block_production.no_timeline`. That policy consumes the event for lane
ordering and bundle callbacks but produces no durable ReAct blocks. This lets
bundles reuse the same event bus for hosting, product API calls, and bundle
storage updates without forcing every received event into the ReAct timeline.

Snapshot events are read-only from ReAct's perspective: they project external
or bundle state into a readable snapshot ref for timeline/ANNOUNCE/compaction.
Canvas state is represented separately by `event.canvas`, an append-only
sequence of JSON canvas revisions. ReAct updates canvas through a bundle
tool/API that validates and writes the canvas, then emits a new `event.canvas`
occurrence; it does not patch `event.snapshot`.

File refs carried by events are also just refs unless a source policy chooses
to project them. A block-production policy may preserve hosted artifact rows or
declared file rows as timeline metadata without embedding file text. That is a
valid ReAct integration: the rendered timeline gives the model the logical
artifact path, and the model can call `react.read(paths=["fi:..."])` when it
needs the content. Automatic `[TEXT FILE PREVIEW]` blocks are not implied by
`hosted_artifacts`; producers such as exec must explicitly provide
`text_preview` when they want source-owned bounded preview text.

## Event Source Defaults

The server-side event source declaration can describe authoring defaults for a
source:

```python
from kdcube_ai_app.apps.chat.sdk.events import event_source_declaration

def list_event_sources():
    return [
        event_source_declaration(
            event_source_id="invoice_intake.wizard.assistance.requested",
            kind="react.external",
            reactive=True,
            iteration_credit=2,
            policies=[],
            description="Wizard assistance request is authored as a reactive event by default.",
        )
    ]
```

Use `kind="react.external"` for authored UI/integration events. Other source
kinds exist for different occurrence families:

| `kind` | Use |
| --- | --- |
| `react.external` | Authored external event in `external_events[]`. |
| `react.tool` | ReAct tool-call/result source. |
| `react.event_source_reader` | Namespace-owner reader for runtime/policy resolution. Exact model-facing content should enter through `react.pull` when the namespace has a registered rehoster. |

The transported `external_events[]` payload does not carry this declaration
`kind`; it carries `event_source_id`, `event_id`, `type`, and occurrence facts
such as `reactive`.

For transported events, reactivity is an occurrence fact. Each accepted event
must carry the effective value in `external_events[].reactive`.
This avoids silently waking ReAct for an event just because a declaration exists.

Live credit is resolved only after the occurrence is explicitly reactive:

1. An occurrence-level iteration credit field when the occurrence sets it.
2. `event_source_declaration(..., iteration_credit=...)` when the source defines
   a default.
3. Runtime default `reactive_event_iteration_credit_per_event`.

The runtime cap still clamps granted credit.

Ingress is earlier than ReAct and does not load bundle declarations just to
classify an idle event. If an idle event must start ReAct, the producer must
send `external_events[].reactive=true`.

## Payload Refs

`payload.event_ref` or fields inside `payload.event` may contain custom
namespace artifact URIs, for example `nmsp:...` refs produced by bundle-owned
storage. ReAct does not treat those refs as local files. A bundle or SDK module
must register an artifact namespace rehoster, such as
`@artifact_namespace_rehoster(namespace="nmsp")`, and the agent materializes the
ref explicitly with `react.pull(paths=["nmsp:..."])`. The rehoster resolves the
custom URI and copies the bytes into the current ReAct artifact surface. The
pull result then contains the materialized `fi:` logical path and current-turn
physical path that `react.read` or generated code can use. Agents should follow
the returned rows instead of deriving a target path from the owner-domain ref.

## Semantic Axes

External events are arranged along four independent axes.

| Axis | Values | Effect |
|---|---|---|
| Source | `event_source_id` | Selects semantic source and representation policies. |
| Occurrence | `event_id` | Identifies one accepted event. Ingress/Redis assigns this for transport-level events. |
| Reactivity | `reactive` | Decides whether the event may start or extend ReAct. Absence/false means no wake. |
| Story correlation | `story_id`, `story_kind`, `payload.target` | Lets a bundle or agent tie the event to a product flow; it does not replace conversation or turn routing. |

`story_id` is intentionally semantic metadata. It helps policies, tools, and
bundle code interpret the event, but the platform still orders events by
conversation and routes active-turn delivery by turn ownership.

## Transport Path

```text
Client/UI
  sends /sse/chat or Socket.IO chat_message
  with a top-level external_events[] event submission
        |
        v
Ingress
  validates session, bundle visibility, conversation ownership
  accepts ordered external_events[] items
  normalizes built-in user events and bundle/domain events into event metadata
  builds ExternalEventPayload.event
        |
        +-- idle reactive event
        |     append to event lane
        |     enqueue ExternalEventLaneWakeup; wakeup carries lane pointer only
        |
        +-- busy turn events
        |     includes followup, steer, prompt-like continuation, and domain events
        |     append to event lane for live owner or later promotion
        |
        +-- idle non-reactive accepted events
              append to event lane and return without model wake
        |
        v
Processor / ReAct owner
  processor resolves wakeups back to lane event task_payload
  live owner drains Redis source when active
  otherwise proc may promote eligible retained events as wakeups
        |
        v
ReAct ContextBrowser
  calls bundle/runtime event callbacks
  applies block-production policies
  projects built-in user events and policy-produced bundle/domain events into blocks
  stamps event_source_id/event_id when event-source pipeline is enabled
  contributes blocks to the active turn timeline
```

For Socket.IO, the first `chat_message` argument is this event submission
object directly. Nested `{ "message": ... }` wrappers are not part of the
protocol.

## Persistence Boundary

The shared external-event source is Redis-backed retained operational state,
scoped by an event lane:

```text
tenant + project + user_id + conversation_id + agent_id
```

The Redis keys include `user_id` and `agent_id` when the payload carries event
metadata. New producers should use the scoped lane. The implementation can still
read retained operational records from the older tenant/project/conversation
lane during rollout.

```text
kdcube:chat:conversation:external-events:{tenant}:{project}:{conversation_id}:user:{user_id}:agent:{agent_id}
kdcube:chat:conversation:external-events:seq:{tenant}:{project}:{conversation_id}:user:{user_id}:agent:{agent_id}
kdcube:chat:conversation:external-events:{tenant}:{project}:{conversation_id}:user:{user_id}:agent:{agent_id}:event:{event_id}
```

`agent_id` defaults to `default.react.agent` when the producer does not target a
named agent. The implementation journey is tracked in
[External Events Journey And Handling](external-events-journey-and-handling-README.md).

Redis provides ordering, replay while retained, and crash recovery for live
owner handoff. It is not permanent conversation or artifact storage.

When ReAct folds an event, the resulting blocks and external-event cursor become
part of the normal persisted ReAct timeline. If an idle non-reactive event must
be permanent even when no ReAct turn later folds it, the bundle/platform must
also materialize it into durable conversation storage or a bundle artifact. That
durable event-history slice is separate from the current Redis transport.

## Reactive And Non-Reactive Events

Reactive events can wake or extend ReAct. Reactivity must be visible on the
occurrence:

```json
{
  "type": "event.external",
  "event_source_id": "task_tracker.canvas.assistance.requested",
  "reactive": true,
  "iteration_credit": 2
}
```

The runtime grants bounded credit only when the active owner consumes the event
live. The effective ceiling is:

```text
effective_max_iterations = base_max_iterations + reactive_iteration_credit
```

Non-reactive events are still ordered and may be folded if an active owner
drains them, but they do not request another model decision by themselves.

`steer` remains a built-in control event. A future product can also define an
event source whose semantics mean "stop", "pause", or "reorient"; that should
be represented by its own `event_source_id` and policy rather than by a generic
interrupt flag.

## Event Source Integration

External events connect to the SDK event subsystem by source id:

```python
from kdcube_ai_app.apps.chat.sdk.events import event_source_declaration

def list_event_sources():
    return [
        event_source_declaration(
            event_source_id="invoice_intake.wizard.assistance.requested",
            kind="react.external",
            reactive=True,
            iteration_credit=2,
            policies=[
                {
                    "react_phase": "timeline_projection",
                    "event_policy_id": "invoice_intake.timeline_projection.wizard_event",
                },
            ],
            description="Wizard user requested assistance for the active invoice story.",
        )
    ]
```

The transport does not apply these policies. It only carries the event and its
source id. ReAct applies policies later when it produces or projects blocks for
the relevant phase.

## Current Gaps

| Gap | Current behavior | Needed when required |
|---|---|---|
| Durable idle event history | Idle non-reactive events are retained only in Redis unless later folded. | Persist event facts into conversation storage or bundle-owned artifacts. |
| Custom event block builders | Built-in folding exists; richer custom source builders are still emerging. | Event-source policies for external-event block production/projection. |
| Story-specific event policies | `story_id` is carried as metadata. | Source/story policy resolution when a product flow needs different projection. |
