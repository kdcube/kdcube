---
id: ks:docs/sdk/events/namespaces-README.md
title: "Logical Reference Namespaces"
summary: "Foundational model for model-facing logical refs such as ar:, ev:, tc:, fi:, task:, mem:, cnv:, and so:, and how they relate to events, react.read, react.pull, and react.checkout."
status: draft
tags: ["sdk", "events", "react", "logical-references", "namespaces", "artifacts"]
updated_at: 2026-06-09
keywords:
  [
    "logical reference namespace",
    "react.read",
    "react.pull",
    "react.checkout",
    "ev:",
    "ar:",
    "tc:",
    "fi:",
    "task:",
    "mem:",
    "so:",
    "cnv:",
  ]
see_also:
  - ks:docs/sdk/namespace-services/providers-README.md
  - ks:docs/sdk/events/external-events-README.md
  - ks:docs/sdk/events/external-event-envelope-README.md
  - ks:docs/sdk/events/event-subsystem-README.md
  - ks:docs/sdk/agents/react/artifact-discovery-README.md
  - ks:docs/sdk/agents/react/workspace/workspace-checkout-model-README.md
  - ks:docs/sdk/agents/react/files-vs-outputs-README.md
---
# Logical Reference Namespaces

A logical reference namespace is the prefix before the first colon in a
model-facing reference:

```text
fi:turn_123.outputs/report.pdf
ev:turn_123.events/task-tracker/state/rev-7
ar:turn_123.user.prompt.evt_1
task:issues/ticket_2026-06-07-10-20-30
```

The namespace tells ReAct and event-source policies which subsystem owns the
reference and which resolver/tool path can interpret it. When that owner exposes
a cross-bundle/client contract, use the
[Namespace Services: Providers](../namespace-services/providers-README.md)
model. In that model, a namespace-owning provider is a namespaced service.

This is not a transport concept. For example, Data Bus `object_ref` is an
opaque partition key unless a bundle explicitly chooses to put a logical ref
there. Data Bus does not resolve namespaces.

## Core Rule

The namespace owner defines the semantics.

Do not infer that two refs are interchangeable because they have similar text.
For example:

- `ar:` is a conversation replica or control record.
- `ev:` is an event occurrence/object on a turn timeline.
- `fi:` is a ReAct artifact/file ref.
- `task:` is a task subsystem object ref.
- `mem:` is a memory subsystem object ref.
- `cnv:` is a canvas subsystem object or board ref.

When one subsystem pins or mentions another subsystem's object, it should
preserve the original ref. A canvas pin of a task remains `task:...`; a canvas
pin of a ReAct artifact remains `fi:...`. The resolver remains the original
owner.

Only the subsystem creating a new hosted object should mint a new id in its own
namespace.

## ReAct Tool Semantics

| Namespace | Primary owner | `react.read` | `react.pull` | `react.checkout` |
| --- | --- | --- | --- | --- |
| `ar:` | ReAct conversation replicas and controls | yes, for visible conversation records and stable aliases | no | no |
| `ev:` | External event objects on a turn timeline | yes, for the event object/metadata | no; pull refs carried by the event payload instead | no |
| `tc:` | ReAct tool call/result records | yes, for call/result records | no; pull artifact refs carried by the tool result instead | no |
| `fi:` | ReAct artifact/file storage | yes | yes, to materialize bytes locally | only for supported `fi:...files/...` workspace refs |
| `task:` | Task subsystem object refs | not directly by default; use visible context or owner tools | yes only when a task namespace rehoster is registered; returns `fi:` rows | no; pull first, then checkout only if the returned `fi:` is a supported files ref |
| `mem:` | Memory subsystem refs | not directly by default; use visible context | yes when the memory namespace rehoster is registered; returns a `fi:` mirror | no |
| `cnv:` | Canvas subsystem refs, including boards such as `cnv:main@7` and canvas-owned objects | not directly by default; use visible canvas ANNOUNCE/context | yes when the canvas namespace rehoster is registered; returns a `fi:` mirror | no |
| `so:` / `su:` | Source/search subsystems | through source/search tooling or visible source pools | subsystem-defined | no |
| `ks:` | Knowledge/docs namespace | subsystem-defined; often read/browse tooling | subsystem-defined | no |

This table describes the default architectural contract. A bundle can add a
registered rehoster or tool for a namespace, but that registration belongs to
the namespace owner and must be documented by that subsystem.

External owner refs are not workspace files. If exact owner content is needed,
use `react.pull(paths=["<namespace>:..."])`. The runtime-connected namespace
rehoster mirrors the owner content into the ReAct workspace and returns ordinary
`fi:` logical paths plus physical paths. ReAct then reads, searches, or executes
against those returned paths. ReAct does not hard-code memory, canvas, task, or
knowledge object rendering.

## Events And Namespaces

Every accepted external event can have an event logical path:

```text
ev:turn_<turn_id>.events/<event-object-path>
ev:conv_<conversation_id>.turn_<turn_id>.events/<event-object-path>
```

The `ev:` ref identifies the event occurrence/object on the timeline. It is
readable as event metadata. It is not the event's hosted bytes.

Event payloads may also carry refs owned by other namespaces:

```json
{
  "logical_path": "ev:turn_123.events/task-tracker/snapshot/latest",
  "hosted_uri": "cnv:main@7",
  "payload": {
    "mime": "application/json",
    "event_ref": "cnv:main@7",
    "event": {
      "context_refs": [
        "task:issues/ticket_2026-06-07-10-20-30",
        "fi:turn_122.outputs/report.pdf"
      ]
    }
  }
}
```

In that example:

- `ev:` names the event object.
- `cnv:` names the canvas-owned board/snapshot payload.
- `task:` names a task subsystem object.
- `fi:` names a ReAct artifact.

Block-production and rendering policies decide which of these refs become
visible in timeline or ANNOUNCE. ReAct then uses the appropriate tool path for
the namespace.

## Built-In User Events

Built-in conversation events are still external events at ingress, but ReAct
projects them into established namespaces:

| Event type | Typical projection |
| --- | --- |
| `event.user.prompt` | `ar:<turn>.user.prompt...` |
| `event.user.followup` | `ar:<turn>.external.followup...` |
| `event.user.steer` | `ar:<turn>.external.steer...` |
| `event.user.attachment.*` | `fi:<turn>.user.attachments/...` |

Generic/domain events keep their event occurrence identity as `ev:` and may
carry refs in other namespaces.

## Pull Versus Read

Use `react.read` when the target ref already names readable model/context
content in the ReAct-visible logical space, such as `ar:`, `tc:`, `ev:`, `fi:`,
`so:`, `su:`, `ks:`, or `sk:` refs supported by the runtime.

Use `react.pull` when exact bytes/content from historical artifacts or external
owner refs must be materialized into the current ReAct artifact space. Pull
returns one or more `fi:` rows. The agent should use those returned refs for
later `react.read`, generated code, local search, or checkout decisions.

Use `react.checkout` only when the agent needs an editable current-turn
workspace copy. Checkout is not a generic resolver. It accepts only supported
workspace-like `fi:...files/...` refs.

## Cross-Conversation Refs

Some `fi:` and `ev:` refs include a conversation prefix:

```text
fi:conv_<conversation_id>.turn_<turn_id>.outputs/report.pdf
ev:conv_<conversation_id>.turn_<turn_id>.events/<event-path>
```

The `conv_...` segment is part of the ref identity. Validators and tools must
preserve it. Dropping that segment changes the owner conversation and breaks
cross-conversation artifact/event resolution.

## Design Checklist

When introducing a new namespace:

- define the namespace owner;
- define the namespaced service when other bundles, widgets, agents, or scene
  hosts need a stable provider/client contract for the namespace;
- define whether refs are event objects, artifacts, domain objects, or aliases;
- define whether `react.read` can read them directly;
- if `react.read` is supported, register an owner-domain event source reader
  and block-production policy;
- define whether `react.pull` can rehost them to `fi:`;
- define whether any returned `fi:` refs can be checked out;
- define permissions and user/project/tenant visibility;
- document how refs appear in events, timeline blocks, ANNOUNCE, and tool
  results;
- keep transport docs free of namespace semantics unless the transport itself
  owns the namespace.
