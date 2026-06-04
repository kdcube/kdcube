---
id: ks:docs/sdk/agents/react/event-source/block-production-README.md
title: "Block Production Phase"
summary: "Implemented ReAct event-source phase that maps tool/event results into timeline blocks, source rows, artifact rows, and notices."
tags: ["sdk", "agents", "react", "event-source", "block-production"]
keywords: ["block_production", "result_items", "artifact_rows", "source_rows", "declared_file_items", "snapshot_refs", "announce_candidates"]
see_also:
  - ks:docs/sdk/agents/react/event-source/event-source-README.md
  - ks:docs/sdk/agents/react/event-source/events-blocks-and-rendering-README.md
  - ks:docs/sdk/agents/react/tool-call-blocks-README.md
  - ks:docs/sdk/agents/react/source-pool-README.md
---
# Block Production Phase

`block_production` runs after a tool/event source returns and before result-side
blocks are appended to the timeline. For tool-backed sources, the harness has
already emitted the generic `react.tool.call` block. Production policies own the
result side of that occurrence.

```text
tool call selected
    -> optional tool_call_validation
    -> harness emits react.tool.call
    -> tool returns {ok, error, ret}
    -> block_production policies mutate one accumulator
    -> shared builders create result/artifact/source blocks
    -> result-side blocks are appended to timeline
```

Authored external events use the same result accumulator shape, but the default
structural output is one event occurrence block:

```text
external_event retained in lane
    -> event reader builds accepted event target
    -> block_production policies mutate one accumulator
    -> policy either emits blocks or marks the occurrence as no-timeline
    -> default producer emits one event.<type> block at the ev: path when
       no registered policy handled the occurrence
    -> event block body stores {ok, status, error?, ret?, surfaces?}
```

For events, `payload.event` is the `ret` analogue. If the body is hosted,
`payload.event_ref` is represented as `ret.event_ref`. The default event
producer also runs the common surface extractors and stores non-empty extracted
rows under `surfaces`, including `source_rows`, `artifact_rows`,
`declared_file_items`, `snapshot_refs`, `announce_candidates`, and
`notice_rows`. Later timeline projection, ANNOUNCE, and compaction policies can
use those durable surfaces without needing the original Redis lane record.

## Production Target

The target is a mutable accumulator:

```python
{
    "tool_id": "...",
    "event_source_id": "...",
    "tool_call_id": "...",
    "event_id": "...",
    "turn_id": "...",
    "final_params": {...},
    "ok": True,
    "error": None,
    "ret": ...,
    "raw": ...,
    "summary": "...",
    "blocks": [],
    "result_items": [],
    "source_rows": [],
    "artifact_rows": [],
    "declared_file_items": [],
    "snapshot_refs": [],
    "announce_candidates": [],
    "notice_rows": [],
    "source_rows_merge": False,
    "result_items_produced": False,
    "declared_file_items_produced": False,
    "notice_rows_produced": False,
}
```

Multiple production policies may run on the same target. Each policy reads the
surface it owns and appends to the matching field.

A policy can also intentionally consume an authored event without producing
ReAct timeline blocks. Bind `react.block_production.no_timeline` for events that
should travel through the ordered event lane and bundle callbacks, but should
not become durable ReAct history.

## Accumulator Fields

| Field | Meaning |
|---|---|
| `blocks` | Direct result-side timeline block candidates. Used for exec report text and similar explicit blocks. |
| `result_items` | Ordinary tool result rows consumed by the shared ReAct artifact/result builder. This is the primary path for JSON/text/file result surfaces. |
| `source_rows` | Exploration rows for `sources_pool`, such as web search/fetch results. |
| `artifact_rows` | File/artifact rows, usually from exec `raw.items` or composite hosted artifact results. |
| `declared_file_items` | Explicit file rows derived from `{artifact_type:"files", files:[...]}`. |
| `snapshot_refs` | Read-only snapshot payload refs, such as `fi:<turn_id>.snapshots/current.yaml` or `ext:<bundle>/snapshots/current`, for later projection/ANNOUNCE/compaction. They are not editable canvas state. |
| `announce_candidates` | Data for a later ANNOUNCE phase. ANNOUNCE itself is not persisted on the timeline. |
| `notice_rows` | Notices/errors/warnings to emit through the existing ReAct notice transport. |

## File Artifact Previews

File-producing sources do not automatically get text previews just because they
produce or host files. Block production distinguishes two responsibilities:

1. Find and register file rows (`artifact_rows`, `declared_file_items`, or
   `hosted_artifacts`).
2. Optionally provide bounded model-visible file text (`text_preview`).

The shared artifact builders always preserve artifact metadata such as
`logical_path`, `physical_path`, `hosted_uri`, `rn`, `key`, `mime`,
`size_bytes`, and `text_symbols` when present. For image/PDF artifacts they may
also add a bounded multimodal block according to runtime caps. For text file
content, the builder only emits a visible preview when the producer already
provided `text_preview` or explicit inline `text`.

Exec is the current first-party source that provides `text_preview`: the exec
runtime has the produced bytes locally, reads a bounded window, and formats it
as `[TEXT FILE PREVIEW]` during block production. Rendering tools, hosted-file
tools, and composite event sources may be metadata-only unless they explicitly
provide a preview. That is valid. ReAct can inspect the file later with
`react.read(paths=["fi:..."])` using the visible logical artifact path.

If a source does provide a pre-rendered preview, it should mark the produced
artifact text block:

```json
{
  "meta": {
    "projection": {
      "phase": "block_production",
      "producer": "my_tool.produce_file",
      "format": "text_file_preview.v1",
      "already_rendered": true
    }
  }
}
```

The timeline renderer then treats the preview as already projected and does not
wrap or number it a second time. Without that marker, visible artifact text is
treated as raw text and may be rendered with the standard line window,
`line_numbers`, and `content:` wrapper.

## Implemented Policy Packs

| Policy pack | Main policy IDs | Use |
|---|---|---|
| `default_tool_event_policies()` | `react.block_production.tool_default` | Initializes the accumulator and declares identity timeline/compaction policies. |
| `exploration_source_policies()` | `react.block_production.exploration_results`, `react.block_production.generic_result_item` | Web search/fetch rows merge into `sources_pool` and still produce an ordinary result item. |
| `structured_result_source_policies()` | `react.block_production.generic_result_item`, `react.block_production.declared_file_items` | Browser, memory, and custom structured-result tools. |
| `write_tool_source_policies()` | `react.block_production.write_tool_result`, `react.block_production.declared_file_items` | Rendering/write tools where `params.path` is the produced artifact. |
| `exec_tool_event_policies()` | `exec_tools.block_production.exec_result` | Exec `raw.report_text` and `raw.items`. |
| `composite_artifact_source_policies()` | `hosted_artifacts`, `snapshot_refs`, `announce_candidates` | Composite custom results with several result surfaces. |
| custom event bus-only policy | `react.block_production.no_timeline` | Suppresses default event-block fallback after the bundle/runtime callback has observed the event. |

## Default Authored Event Producers

| Policy ID | Input event type | Default timeline output |
|---|---|---|
| `react.block_production.event_default` | `event.external` and other generic domain events | One `event.<type>` JSON block at the accepted event's `ev:` path. The body stores `ok`, `status`, `ret`, optional `error`, optional `event_ref`, and extracted `surfaces`. |
| `react.block_production.snapshot_default` | `event.snapshot` | One `event.snapshot` JSON block at the `ev:` path. The body preserves snapshot refs and ANNOUNCE candidates as read-only projection data. |
| `react.block_production.canvas_default` | `event.canvas` | One `event.canvas` JSON block at the `ev:` path. The body preserves the canvas revision/state as collaborative domain state; edits must still go through a bundle API/tool that emits a later canvas event. |
| `react.block_production.user_prompt_default` | `event.user.prompt` | Compatibility `user.prompt` block with event identity in metadata. |
| `react.block_production.user_followup_default` | `event.user.followup` | Compatibility `user.followup` block with event identity and `is_continuation` metadata. |
| `react.block_production.user_steer_default` | `event.user.steer` | Compatibility `user.steer` block with event identity and `is_continuation` metadata. |
| `react.block_production.user_attachment_default` | `event.user.attachment.*` or attachments carried alongside a prompt/followup/domain event | Compatibility `user.attachment.meta`, `user.attachment`, and `user.attachment.text` blocks through the shared attachment builder. Hosted metadata such as `hosted_uri`, `rn`, `key`, and `physical_path` is preserved in block metadata. |

## Generic JSON Is Not A File

Structured JSON results are represented as `tc:<turn>.<call>.result`. They must
not be normalized into `fi:<turn>.files/<tool_id>`. Only explicit file-backed
rows, write-tool rows, exec artifact rows, declared files, or already-hosted
records go through file path resolution and hosting.

This distinction matters for tools such as `memory.search_memory` and
`web_tools.web_search`: their ordinary JSON/search result rows should not
produce `protocol_violation.path_rewritten` notices simply because the
`artifact_id` is the tool id.

## Composite Result Example

A tool can return several result surfaces in one `ret`:

```json
{
  "ok": true,
  "error": null,
  "ret": {
    "exploration_results": [],
    "hosted_artifacts": [],
    "snapshot_refs": ["fi:turn_1.snapshots/current.yaml"],
    "announce_candidates": [{"section": "wizard", "text": "Issue draft changed"}]
  }
}
```

Block-production policies do not need to agree on one "trait". They mutate the
same accumulator, so one policy can append source rows, another can append
artifact rows, and another can record snapshot refs.

`snapshot_refs` means "there is a readable snapshot payload over there." It is
not a write channel. Snapshot payloads are normally produced from external
state and are read by ReAct through `react.pull`/`react.read` when needed. For
shared state that both the user and agent can edit as JSON, use a dedicated
event type such as `event.canvas`.

The same rule applies to authored event payloads. A snapshot event may carry a
compact inline payload, a hosted snapshot ref, hosted artifact refs, and an
ANNOUNCE candidate together:

```json
{
  "event_id": "evt_canvas_snapshot_001",
  "type": "event.snapshot",
  "event_source_id": "task_tracker.canvas.snapshot",
  "logical_path": "ev:turn_1.events/task-tracker/snapshots/draft-123/canvas/latest",
  "payload": {
    "mime": "application/json",
    "event": {
      "summary": "Canvas has one selected note and one attachment.",
      "hosted_artifacts": [
        {"hosted_uri": "ext:task-tracker/files/draft-123/diagram.png", "mime": "image/png"}
      ],
      "snapshot_ref": "ext:task-tracker/snapshots/draft-123/canvas/latest",
      "announce_entry": {"title": "Canvas snapshot", "text": "One selected note."}
    }
  }
}
```

The default block producer emits one event block at the event's `ev:` path. The
block body keeps `ret.summary` plus `surfaces.artifact_rows`,
`surfaces.snapshot_refs`, and `surfaces.announce_candidates`.

## Current Completion Status

The block-production phase is implemented and tested under
`event_source_pipeline.enabled=true` for the external SDK tool families listed
above. The legacy path remains available when the flag is disabled. The enabled
path is expected to preserve the visible timeline shape of the old path while
making result production configurable by event-source policy.

For authored external events, the default single event-block production path is
implemented for unregistered sources, snapshots, canvas state, prompt, followup,
steer, and attachment events. Event result surfaces are preserved durably first;
later timeline, ANNOUNCE, and compaction policies project or announce them.
Artifact rows from events may stay metadata/ref-only unless the source also
provides `text_preview` or inline text.
