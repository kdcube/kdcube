---
id: ks:docs/sdk/events/external-event-envelope-README.md
title: "External Event Envelope"
summary: "Canonical plural external-event payload shape, accepted event fields, event logical paths (`ev:`), hosted event payload URIs (`ext:`), inline payloads, snapshot events, file upload events, and text-selection context events."
status: draft
tags: ["sdk", "events", "external-events", "event-envelope", "snapshots", "react"]
keywords:
  [
    "external_events[]",
    "event.snapshot",
    "event.canvas",
    "event.external",
    "logical_path",
    "hosted_uri",
    "event_source_id",
    "event_id",
    "ev:",
    "ext:",
    "snapshot event",
    "canvas event",
  ]
see_also:
  - ks:docs/sdk/events/external-events-README.md
  - ks:docs/sdk/events/external-events-journey-and-handling-README.md
  - ks:docs/sdk/events/event-subsystem-README.md
  - ks:docs/sdk/agents/react/event-source/event-source-README.md
  - ks:docs/sdk/agents/react/event-source/timeline-projection-README.md
  - ks:docs/sdk/agents/react/react-announce-README.md
---
# External Event Envelope

Clients submit authored UI/domain events as a plural list:

```text
external_events[]
```

There is no singular event field in the target protocol. A user prompt with
attachments, a canvas snapshot plus review request, or a wizard field update
can all be represented as multiple ordered event occurrences in one client
submission.

## Accepted Event Shape

After ingress accepts an event, the event occurrence has this shape:

```json
{
  "event_id": "evt_canvas_snapshot_001",
  "type": "event.snapshot",
  "event_source_id": "task_tracker.canvas.snapshot",
  "logical_path": "ev:turn_123.events/task-tracker/snapshots/draft-123/canvas/latest",
  "hosted_uri": "ext:task-tracker/snapshots/draft-123/canvas/latest",
  "reactive": false,
  "agent_id": "default.react.agent",
  "story_id": "task:draft-123",
  "payload": {
    "mime": "application/json",
    "event_ref": "ext:task-tracker/snapshots/draft-123/canvas/latest"
  }
}
```

Field roles:

| Field | Meaning |
|---|---|
| `event_id` | One accepted occurrence id in the target turn timeline. |
| `type` | Structural event block type, such as `event.snapshot` or `event.external`. |
| `event_source_id` | Semantic source and policy key, such as `task_tracker.canvas.snapshot`. |
| `logical_path` | `ev:` path of this event object on the turn timeline. |
| `hosted_uri` | Optional external URI for the hosted event payload/body. |
| `reactive` | Whether this occurrence may wake or extend ReAct. |
| `agent_id` | Target agent lane. Defaults to `default.react.agent` when omitted by the producer. |
| `story_id` | Optional product/story correlation id. |
| `payload.mime` | MIME type of `payload.event` or of the target of `payload.event_ref`. |
| `payload.event` | Inline event object/string/bytes metadata. |
| `payload.event_ref` | Pullable URI for the event payload/body, for example `fi:` or `ext:`. |

`payload` must describe the event body. It should contain exactly one of
`event` or `event_ref`, plus `mime`.

`hosted_uri` is top-level because it describes where the accepted event body is
hosted outside the timeline, when the body is hosted. For snapshot events it
usually matches `payload.event_ref`.

## Logical Paths

Events are stored on turn timelines, so `ev:` paths include the turn:

```text
ev:turn_<turn_id>.events/<event-object-path>
ev:conv_<conversation_id>.turn_<turn_id>.events/<event-object-path>
```

The event object path is semantic and should be stable enough to read in
rendered timeline/ANNOUNCE context:

```text
ev:turn_123.events/task-tracker/snapshots/draft-123/canvas/latest
ev:turn_123.events/task-tracker/canvas/files/file-7/uploaded
ev:turn_123.events/task-tracker/canvas/selection/review-requested/evt_9
```

`ev:` identifies the event occurrence or event object on the timeline. It is
readable with `react.read` like `tc:`. It is not an artifact URI and is not
pullable with `react.pull`. Artifact bytes or snapshot bodies are accessed
through `payload.event_ref`, `hosted_uri`, or refs carried inside
`payload.event`.

## Default Timeline Blocks

Every accepted event occurrence has:

- `event_source_id`: semantic/policy key;
- `event_id`: occurrence id;
- `logical_path`: the `ev:` path of the event object on the timeline;
- `type`: structural block shape requested by the producer, such as
  `event.external` or `event.snapshot`.

The conceptual event group is the same layer as a tool occurrence:

| Event-source occurrence | Default timeline representation |
|---|---|
| User prompt event | Accepted type `event.user.prompt`; current compatibility projection is `user.prompt` |
| User attachment event | Accepted type family `event.user.attachment.*`; current compatibility projection is `user.attachment.*` |
| User followup event | Accepted type `event.user.followup`; current compatibility projection is `user.followup` |
| User steer event | Accepted type `event.user.steer`; current compatibility projection is `user.steer` / control path |
| Tool call | `react.tool.call` plus one or more `react.tool.result` / artifact blocks |
| Authored external event | One `event.external` block at the event `ev:` path |
| Snapshot event | One `event.snapshot` block at the event `ev:` path |
| Canvas state event | One `event.canvas` block at the event `ev:` path |
| Event with attachments | Event block plus `user.attachment.*` / external attachment blocks |

The built-in prompt/followup/steer compatibility blocks are produced by
block-production policies:

- `react.block_production.user_prompt_default`
- `react.block_production.user_followup_default`
- `react.block_production.user_steer_default`
- `react.block_production.user_attachment_default`

Generic authored events use `react.block_production.event_default`. Snapshot and
canvas events have explicit defaults:
`react.block_production.snapshot_default` and
`react.block_production.canvas_default`. The attachment default preserves hosted
artifact metadata (`hosted_uri`, `rn`, `key`, `physical_path`) on the produced
attachment blocks.

The default event block body has the tool-result-like shape: `ok`, `status`,
optional `error`, optional `ret`, and optional `surfaces`. `payload.event` is
the `ret` analogue. If the body is hosted, `payload.event_ref` is represented
as `ret.event_ref`. The SDK default producer also extracts standard result
surfaces from `ret` and stores them under `surfaces`, for example
`source_rows`, `artifact_rows`, `declared_file_items`, `snapshot_refs`,
`announce_candidates`, and `notice_rows`.

If an event source registers a `block_production` policy, that policy may
replace or expand this group with source-specific payload/result/artifact
blocks. The grouping key is still `event_id`; the policy key is still
`event_source_id`. A registered source may also bind
`react.block_production.no_timeline` when the occurrence should stay available
to the ordered event lane and bundle callbacks, but should not become durable
ReAct timeline material.

## Inline Payload

Small or already-bounded event bodies can be inline:

```json
{
  "event_id": "evt_canvas_review_001",
  "type": "event.external",
  "event_source_id": "task_tracker.canvas.review.requested",
  "logical_path": "ev:turn_123.events/task-tracker/canvas/review-requested/evt_canvas_review_001",
  "hosted_uri": null,
  "reactive": true,
  "agent_id": "default.react.agent",
  "story_id": "task:draft-123",
  "payload": {
    "mime": "application/json",
    "event": {
      "prompt": "Review this selected area and suggest the next step.",
      "selection": {
        "item_ids": ["note-7"]
      },
      "snapshot": "ev:turn_123.events/task-tracker/snapshots/draft-123/canvas/latest"
    }
  }
}
```

References inside `payload.event` are ordinary event data. If a field contains
a pullable URI such as `fi:` or `ext:`, ReAct can decide to call
`react.pull(paths=[...])` and then use the returned `fi:` path.

## Snapshot Events

Snapshot events are special because they usually drive ANNOUNCE. A snapshot is
one-way from ReAct's perspective: external or bundle state is projected into a
readable snapshot, and ReAct may pull/read it, but it should not patch or
checkout that snapshot as the authoritative editable object. The timeline must
store the snapshot event occurrence even when the full snapshot body lives
behind `ext:`.

```json
{
  "event_id": "evt_canvas_snapshot_001",
  "type": "event.snapshot",
  "event_source_id": "task_tracker.canvas.snapshot",
  "logical_path": "ev:turn_123.events/task-tracker/snapshots/draft-123/canvas/latest",
  "hosted_uri": "ext:task-tracker/snapshots/draft-123/canvas/latest",
  "reactive": false,
  "agent_id": "default.react.agent",
  "story_id": "task:draft-123",
  "payload": {
    "mime": "application/json",
    "event_ref": "ext:task-tracker/snapshots/draft-123/canvas/latest"
  }
}
```

The produced timeline block should carry enough bounded metadata for policies
to build ANNOUNCE without loading the full body. The default shape is one event
block:

```json
{
  "type": "event.snapshot",
  "path": "ev:turn_123.events/task-tracker/snapshots/draft-123/canvas/latest",
  "meta": {
    "event_id": "evt_canvas_snapshot_001",
    "event_source_id": "task_tracker.canvas.snapshot",
    "event_type": "event.snapshot",
    "event_occurrence": true
  },
  "text": {
    "ok": true,
    "status": "success",
    "ret": {
      "event_ref": "ext:task-tracker/snapshots/draft-123/canvas/latest"
    },
    "surfaces": {
      "snapshot_refs": [
        "ext:task-tracker/snapshots/draft-123/canvas/latest"
      ],
      "announce_candidates": [
        {
          "title": "Canvas snapshot",
          "text": "Draft issue notes, 2 attachments, selected area: reproduction steps."
        }
      ]
    }
  }
}
```

The occurrence of an `event.snapshot` block in a turn timeline means the client
sent a snapshot update into that turn's event lane. During an active turn, this
is how the client reports changed widget/canvas context while ReAct is still
working.

`surfaces.snapshot_refs` are read-only snapshot payload refs for later
timeline projection, ANNOUNCE, and compaction. They are not a writable state
channel.

## Canvas State Event

Canvas state is different from a snapshot. A canvas is a shared JSON object that
the user and the agent can both update. It should be represented as
`event.canvas`:

```json
{
  "event_id": "evt_canvas_rev_7",
  "type": "event.canvas",
  "event_source_id": "task_tracker.canvas.state",
  "logical_path": "ev:turn_123.events/task-tracker/canvas/draft-123/state/rev-7",
  "hosted_uri": null,
  "reactive": false,
  "agent_id": "default.react.agent",
  "story_id": "task:draft-123",
  "payload": {
    "mime": "application/json",
    "event": {
      "canvas_id": "draft-123",
      "revision": 7,
      "items": [
        {
          "id": "note-1",
          "kind": "note",
          "text": "Browser crashes after uploading a large CSV.",
          "x": 120,
          "y": 96
        }
      ]
    }
  }
}
```

The timeline is still append-only. Editing the canvas means accepting a new
`event.canvas` occurrence with a later revision, not mutating an older timeline
block. The bundle remains responsible for validating permissions and applying
the edit to its authoritative canvas store. ReAct should update canvas state
through a bundle tool/API that writes the canvas and emits a new `event.canvas`
occurrence; it should not edit `event.snapshot`.

## File Upload Event

A file upload is also an event. The file itself can remain in bundle/domain
storage and be referenced by a pullable URI inside the event body:

```json
{
  "event_id": "evt_canvas_file_001",
  "type": "event.external",
  "event_source_id": "task_tracker.canvas.file.uploaded",
  "logical_path": "ev:turn_123.events/task-tracker/canvas/files/file-7/uploaded",
  "hosted_uri": null,
  "reactive": false,
  "agent_id": "default.react.agent",
  "story_id": "task:draft-123",
  "payload": {
    "mime": "application/json",
    "event": {
      "file_id": "file-7",
      "name": "browser-crash-log.txt",
      "file_uri": "ext:task-tracker/files/draft-123/file-7/browser-crash-log.txt",
      "file_mime": "text/plain",
      "size_bytes": 18412
    }
  }
}
```

If ReAct needs the bytes, it pulls `file_uri`. The event payload should not
invent a second artifact-ref channel.

An upload event does not automatically expose file text in the timeline. The
event can produce a metadata-only block or an artifact row that points to the
file. That is enough for ReAct to discover the logical artifact/ref and decide
whether to call `react.pull` or `react.read`. If the event source wants a
bounded text preview to appear immediately, its block-production policy must
provide explicit `text_preview` or inline text and mark the produced artifact
text block as an already-rendered `text_file_preview.v1` projection.

## Text Selection Context Event

When the user selects text or a canvas area and asks for assistance, send an
explicit event for that action. The current selection can be inline, while the
current editable canvas state is referenced by the latest `event.canvas`
occurrence. A snapshot ref can also be present when the bundle wants ANNOUNCE
or read-oriented projection:

```json
{
  "event_id": "evt_canvas_selection_review_001",
  "type": "event.external",
  "event_source_id": "task_tracker.canvas.selection.review.requested",
  "logical_path": "ev:turn_123.events/task-tracker/canvas/selection/review-requested/evt_canvas_selection_review_001",
  "hosted_uri": null,
  "reactive": true,
  "agent_id": "default.react.agent",
  "story_id": "task:draft-123",
  "payload": {
    "mime": "application/json",
    "event": {
      "prompt": "Review this selected text and suggest how to improve it.",
      "selection": {
        "surface": "canvas",
        "text": "The browser crashes after uploading a large CSV.",
        "range": {
          "anchor": "note-7:12",
          "focus": "note-7:68"
        }
      },
      "canvas": "ev:turn_123.events/task-tracker/canvas/draft-123/state/rev-7",
      "snapshot": "ev:turn_123.events/task-tracker/snapshots/draft-123/canvas/latest"
    }
  }
}
```

The snapshot should already include the applied user effects that matter for
context, such as current field values, current selection, attached files, and
canvas item positions. The selection event tells ReAct why the user is asking
now; `event.canvas` tells ReAct what the editable canvas state is; the snapshot
is the read-oriented projection for ANNOUNCE or compact context.

## Policy Implication

Timeline and ANNOUNCE are derived from stored event blocks and event-source
policies:

- `block_production` stores `event.snapshot` / `event.external` blocks or
  deliberately suppresses ReAct blocks for bus-only events;
- `timeline_projection` decides whether those blocks are visible, hidden, or
  replaced in normal timeline render;
- `announce_production` can inspect the full timeline and emit an ephemeral
  ANNOUNCE entry for the latest relevant `event.snapshot`;
- `compaction_projection` preserves the event path, hosted URI, and summary
  instead of carrying large payload bodies into compaction.

The SDK provides structural defaults for snapshot/canvas/event block
production and compact render projection. Bundle-specific policies should add
the richer behavior: latest snapshot or canvas state appears in ANNOUNCE with a
domain-specific projection, while durable timeline blocks retain refs and
causality instead of large mutable bodies.
