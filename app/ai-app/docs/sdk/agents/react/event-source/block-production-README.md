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

## Accumulator Fields

| Field | Meaning |
|---|---|
| `blocks` | Direct result-side timeline block candidates. Used for exec report text and similar explicit blocks. |
| `result_items` | Ordinary tool result rows consumed by the shared ReAct artifact/result builder. This is the primary path for JSON/text/file result surfaces. |
| `source_rows` | Exploration rows for `sources_pool`, such as web search/fetch results. |
| `artifact_rows` | File/artifact rows, usually from exec `raw.items` or composite hosted artifact results. |
| `declared_file_items` | Explicit file rows derived from `{artifact_type:"files", files:[...]}`. |
| `snapshot_refs` | Durable snapshot refs, such as `fi:<turn_id>.snapshots/current.yaml`. |
| `announce_candidates` | Data for a later ANNOUNCE phase. ANNOUNCE itself is not persisted on the timeline. |
| `notice_rows` | Notices/errors/warnings to emit through the existing ReAct notice transport. |

## Implemented Policy Packs

| Policy pack | Main policy IDs | Use |
|---|---|---|
| `default_tool_event_policies()` | `react.block_production.tool_default` | Initializes the accumulator and declares identity timeline/compaction policies. |
| `exploration_source_policies()` | `react.block_production.exploration_results`, `react.block_production.generic_result_item` | Web search/fetch rows merge into `sources_pool` and still produce an ordinary result item. |
| `structured_result_source_policies()` | `react.block_production.generic_result_item`, `react.block_production.declared_file_items` | Browser, memory, and custom structured-result tools. |
| `write_tool_source_policies()` | `react.block_production.write_tool_result`, `react.block_production.declared_file_items` | Rendering/write tools where `params.path` is the produced artifact. |
| `exec_tool_event_policies()` | `exec_tools.block_production.exec_result` | Exec `raw.report_text` and `raw.items`. |
| `composite_artifact_source_policies()` | `hosted_artifacts`, `snapshot_refs`, `announce_candidates` | Composite custom results with several result surfaces. |

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

## Current Completion Status

The block-production phase is implemented and tested under
`event_source_pipeline.enabled=true` for the external SDK tool families listed
above. The legacy path remains available when the flag is disabled. The enabled
path is expected to preserve the visible timeline shape of the old path while
making result production configurable by event-source policy.

