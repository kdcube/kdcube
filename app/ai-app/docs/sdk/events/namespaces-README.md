---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/events/namespaces-README.md
title: "Logical Reference Namespaces"
summary: "Foundational model for conversation-owned ReAct refs such as conv:fi, conv:ar, conv:ev, external owner refs such as task/mem/cnv, and how they relate to events, react.read, react.pull, and react.checkout."
status: active
tags: ["sdk", "events", "react", "logical-references", "namespaces", "artifacts"]
updated_at: 2026-07-04
keywords:
  [
    "logical reference namespace",
    "conv:fi",
    "conv:ar",
    "conv:ev",
    "conv:tc",
    "task:",
    "mem:",
    "cnv:",
    "react.read",
    "react.pull",
    "react.checkout",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/references-and-paths-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/workspace-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/react-object-materialization-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-ingress-to-react-turn-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/external-event-envelope-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-subsystem-README.md
---
# Logical Reference Namespaces

A logical reference namespace is the resolver prefix of a model-facing ref.

ReAct conversation-owned refs have a two-part prefix:

```text
conv:<family>:<body>
```

Examples:

```text
conv:fi:turn_123.files/report.pdf
conv:ar:turn_123.user.prompt.evt_1
conv:tc:turn_123.tc_abcd.result
conv:so:sources_pool[1-3]
conv:ev:turn_123.events/chat/user-prompt/evt_1
```

External owner refs use their owner namespace directly:

```text
mem:mem_123
task:issue:ticket_123
cnv:canvas/users/.../objects/...
```

The namespace tells ReAct, event-source policies, UI reducers, and resolvers
which subsystem owns the ref. Data Bus object refs can carry these strings, but
Data Bus itself does not resolve namespaces.

## `conv:` And `conv_<id>` Are Different

```text
conv:      outer owner namespace for ReAct conversation-owned refs
conv_<id>  optional body segment selecting another conversation
```

`conv_<id>` must stay inside the body after the family:

```text
conv:fi:conv_<conversation_id>.turn_<turn_id>.files/report.pdf
conv:ar:conv_<conversation_id>.turn_<turn_id>.assistant.completion
```

It is not a replacement for `conv:` and it is not a standalone namespace.

## Owner Rule

The namespace owner defines the semantics. Do not infer that two refs are
interchangeable because they contain similar path text.

| Ref | Owner | Meaning |
| --- | --- | --- |
| `conv:fi:` | ReAct conversation realm | File/artifact bytes and materialized workspace paths. |
| `conv:ar:` | ReAct conversation realm | User/assistant/plan/conversation replica records. |
| `conv:tc:` | ReAct conversation realm | Tool call/result/notice records. |
| `conv:so:` | ReAct conversation realm | Source-pool rows and source metadata. |
| `conv:ws:` | ReAct conversation realm | Working summaries. |
| `conv:su:` | ReAct conversation realm | Summary/search-summary records. |
| `conv:ev:` | ReAct conversation realm | Accepted event occurrence/object on a turn timeline. |
| `task:` | Task provider | Task issues, task attachments, and task actions. |
| `mem:` | Memory provider | Durable user memory records. |
| `cnv:` | Canvas provider | Canvas boards, pins, and canvas-owned objects. |

When one subsystem pins or mentions another subsystem's object, it preserves the
original owner ref. A canvas pin of a task remains `task:...`; a canvas pin of a
ReAct file remains `conv:fi:...`.

## ReAct Tool Semantics

| Namespace | `react.read` | `react.pull` | `react.checkout` |
| --- | --- | --- | --- |
| `conv:ar:` | yes | no | no |
| `conv:tc:` | yes | no | no |
| `conv:so:` | yes | no | no |
| `conv:ws:` | yes | no | no |
| `conv:su:` | yes | no | no |
| `conv:ev:` | yes, event object/metadata | no; pull refs carried by the event payload instead | no |
| `conv:fi:` | yes | yes, materializes bytes locally | only `conv:fi:<turn>.git/projects/...` project refs |
| `task:` | no direct default read; use visible blocks or owner tools | yes through the task provider materializer | no |
| `mem:` | no direct default read; use visible blocks or owner tools | yes through the memory provider materializer | no |
| `cnv:` | no direct default read; use visible canvas context or owner tools | yes through the canvas provider materializer | no |

External owner refs are not workspace files. If exact owner content is needed,
call `react.pull(paths=["<namespace>:..."])`. The connected owner rehoster
mirrors that content into the current ReAct turn and returns `conv:fi:`
logical paths plus physical paths. The agent then reads, searches, executes, or
renders against those returned paths.

## Events And Namespaces

Every accepted external event can have a conversation-owned event path:

```text
conv:ev:turn_<turn_id>.events/<event-object-path>
conv:ev:conv_<conversation_id>.turn_<turn_id>.events/<event-object-path>
```

The `conv:ev:` ref identifies the event occurrence/object on the timeline. It
is readable as event metadata. It is not the event's hosted bytes.

Event payloads may carry refs owned by other namespaces:

```json
{
  "logical_path": "conv:ev:turn_123.events/task-tracker/snapshot/latest",
  "object_ref": "cnv:main@7",
  "payload": {
    "mime": "application/json",
    "context_refs": [
      "task:issue:ticket_2026-06-07-10-20-30",
      "conv:fi:turn_122.files/report.pdf"
    ]
  }
}
```

In that example:

- `conv:ev:` names the event object.
- `cnv:` names the canvas-owned board/snapshot payload.
- `task:` names a task subsystem object.
- `conv:fi:` names a ReAct file artifact.

Block-production and rendering policies decide which refs become visible in
timeline or ANNOUNCE.

Two lane-protocol facts hold for every authored event: the TRANSPORT kind is
uniformly `external_event` (the semantic type rides `payload.event.type` —
publishing a semantic type as the transport kind makes the event invisible to
the fold), and folding is TOTAL — an event the fold cannot render still
advances the handler cursor and is acked, so it can never wedge a turn's
completion. Details in
[App Conversation Events](../bundle/bundle-conversation-events-and-react-output-README.md).

## Built-In User Events

Built-in conversation events are external events at ingress. ReAct projects
them into established conversation-owned namespaces:

| Event type | Typical projection |
| --- | --- |
| `event.user.prompt` | `conv:ar:<turn>.user.prompt...` |
| `event.user.followup` | `conv:ar:<turn>.external.followup...` |
| `event.user.steer` | `conv:ar:<turn>.external.steer...` |
| `event.user.attachment.*` | `conv:fi:<turn>.user.attachments/...` |
| Generic/domain event | `conv:ev:<turn>.events/...` plus any owner refs in the payload |

## Pull Versus Read

Use `react.read` when the target ref already names readable
conversation-owned content such as `conv:ar:`, `conv:tc:`, `conv:ev:`,
`conv:fi:`, `conv:so:`, `conv:ws:`, or `conv:su:`.

Use `react.pull` when exact bytes/content from historical artifacts or external
owner refs must be materialized into the current ReAct workspace. Pull returns
one or more `conv:fi:` rows. Use those returned refs and paths for later
`react.read`, generated code, local search, rendering, or checkout decisions.

Use `react.checkout` only when an historical project tree should become an
editable current-turn copy. Checkout accepts only supported
`conv:fi:...git/projects/...` refs.

## Design Checklist

When introducing a new namespace:

- define the owner;
- define whether refs are event objects, files/artifacts, domain objects, or aliases;
- define whether refs are read directly, pulled, both, or neither;
- if pull is supported, define the returned `conv:fi:` location and metadata;
- if read is supported, define the event/source block-production policy;
- define permissions and tenant/project/user visibility;
- document how refs appear in events, timeline blocks, ANNOUNCE, tool results,
  UI cards, and named-service schemas;
- keep transport docs free of namespace semantics unless the transport itself
  owns the namespace.
